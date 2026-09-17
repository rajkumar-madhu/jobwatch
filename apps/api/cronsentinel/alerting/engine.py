"""Rule engine: jobstatus event → incidents + notifications. I/O layer around rules.py."""
import json
from datetime import datetime, timezone

import redis
import structlog
from sqlalchemy import text

from ..config import settings
from ..db import system_session
from . import rules as R
from . import correlation as C
from .tasks import send_notification

log = structlog.get_logger()
_r = redis.Redis.from_url(settings.redis_url, decode_responses=True)


def _job_ctx(s, org, job_id) -> R.JobCtx | None:
    j = s.execute(text("SELECT id, name, tags, environment_id, workspace_id, team_id, sla_target, sla_window FROM jobs WHERE id=:id AND org_id=:o"),
                  {"id": job_id, "o": org}).first()
    if not j:
        return None
    cf = s.execute(text("""
        SELECT count(*) FROM executions WHERE job_id=:id AND status IN ('failed','timeout','missed')
          AND scheduled_ts > COALESCE((SELECT max(scheduled_ts) FROM executions WHERE job_id=:id AND status='success'), '1970-01-01')
    """), {"id": job_id}).scalar()
    days = {"rolling_7d": 7, "rolling_30d": 30}.get(j.sla_window, 30)
    sr = s.execute(text("SELECT 100.0*count(*) FILTER (WHERE status='success')/NULLIF(count(*),0) FROM executions WHERE job_id=:id AND scheduled_ts >= now() - (:d || ' days')::interval AND status IN ('success','failed','timeout','missed')"),
                   {"id": job_id, "d": days}).scalar()
    return R.JobCtx(id=str(j.id), name=j.name, tags=list(j.tags or []), environment_id=str(j.environment_id) if j.environment_id else None,
                    workspace_id=str(j.workspace_id), team_id=str(j.team_id) if j.team_id else None,
                    consecutive_failures=int(cf or 0), success_rate=float(sr) if sr is not None else None,
                    sla_target=float(j.sla_target) if j.sla_target is not None else None)


def _in_maintenance(s, org, job: R.JobCtx, now) -> bool:
    rows = s.execute(text("SELECT scope FROM maintenance_windows WHERE org_id=:o AND starts_at <= :n AND ends_at >= :n"), {"o": org, "n": now}).all()
    # TODO: rrule expansion for recurring windows
    for r in rows:
        sc = r.scope or {}
        if not sc or job.id in sc.get("job_ids", []) or set(sc.get("tags", [])) & set(job.tags) or job.environment_id in sc.get("environment_ids", []):
            return True
    return False


def _record_change(job_id: str, now: datetime) -> list[datetime]:
    k = f"flap:{job_id}"
    _r.zadd(k, {now.isoformat(): now.timestamp()}); _r.zremrangebyscore(k, 0, now.timestamp() - 3600); _r.expire(k, 3600)
    return [datetime.fromtimestamp(sc, tz=timezone.utc) for _, sc in _r.zrange(k, 0, -1, withscores=True)]


def handle_status_change(ev: dict):
    org, job_id, prev, new = ev["org_id"], ev["job_id"], ev["prev_status"], ev["new_status"]
    # R2 — UNKNOWN-visibility suppression, checked before any rule work so an agent outage cannot
    # fan out into one page per job it owned. Logged so the reason is visible in the ledger view.
    supp = R.suppressed_unknown(ev.get("new_state"), ev.get("unknown_reason"))
    if supp:
        log.info("suppressed: unknown visibility", job=job_id, reason=supp)
        return
    now = datetime.now(timezone.utc)
    with system_session() as s:
        job = _job_ctx(s, org, job_id)
        if not job:
            return
        changes = _record_change(job_id, now)
        flapping = R.is_flapping(changes, now)

        # auto-resolve open incidents for this job when it recovers/healthy
        if new in ("recovered", "healthy"):
            s.execute(text("UPDATE incidents SET status='resolved', resolved_at=now(), resolution='auto: job recovered' "
                           "WHERE org_id=:o AND status<>'resolved' AND :j = ANY(affected_job_ids)"), {"o": org, "j": job_id})
            s.execute(text("INSERT INTO incident_events (org_id, incident_id, kind, payload) SELECT org_id, id, 'resolved', '{\"auto\":true}'::jsonb FROM incidents WHERE org_id=:o AND resolved_at >= now() - interval '1 second' AND :j = ANY(affected_job_ids)"),
                      {"o": org, "j": job_id})

        if _in_maintenance(s, org, job, now):
            log.info("suppressed: maintenance", job=job.name, new=new); return

        rule_rows = s.execute(text("SELECT id, condition, scope, params, severity, channel_ids, business_hours, repeat_interval_s FROM alert_rules WHERE org_id=:o AND enabled"), {"o": org}).all()
        for rr in rule_rows:
            rule = R.Rule(id=str(rr.id), condition=rr.condition, scope=rr.scope or {}, params=rr.params or {}, severity=str(rr.severity),
                          channel_ids=[str(c) for c in rr.channel_ids or []], business_hours=rr.business_hours, repeat_interval_s=rr.repeat_interval_s)
            if not R.scope_matches(rule, job) or not R.condition_fires(rule, prev, new, job, new_state=ev.get("new_state"), unknown_reason=ev.get("unknown_reason")):
                continue
            if not R.in_business_hours(rule.business_hours, now):
                log.info("suppressed: outside business hours", rule=rule.id); continue
            cond = "flapping" if flapping and rule.condition != "recovered" else rule.condition
            dk = R.dedup_key(rule.id, job.id, cond)
            last = s.execute(text("SELECT max(sent_at) FROM notification_ledger WHERE org_id=:o AND dedup_key=:k AND status='sent'"), {"o": org, "k": dk}).scalar()
            if not R.should_repeat(last, rule.repeat_interval_s, now):
                log.info("suppressed: dedup", key=dk); continue

            incident_id = None
            if new in R.BAD or new == "late" or cond == "flapping":
                inc = s.execute(text("SELECT id FROM incidents WHERE org_id=:o AND status<>'resolved' AND :j = ANY(affected_job_ids) ORDER BY started_at DESC LIMIT 1"),
                                {"o": org, "j": job_id}).first()
                if not inc:
                    sig = C.signals_for_job(s, org, job_id)
                    rel_id, why = C.find_related_incident(s, org, job_id, sig)
                    if rel_id:
                        C.attach(s, org, rel_id, job_id, sig, why, prev, new); incident_id = str(rel_id)
                        for ch in rule.channel_ids:
                            send_notification.delay(org, ch, dk, incident_id, {"job_id": job.id, "job_name": job.name, "prev_status": prev, "new_status": new, "condition": cond,
                                                                              "severity": rule.severity, "incident_id": incident_id, "execution_id": ev.get("execution_id"), "ts": now.isoformat(), "correlated": why})
                        continue
                if inc:
                    incident_id = str(inc.id)
                    s.execute(text("INSERT INTO incident_events (org_id, incident_id, kind, payload) VALUES (:o, :i, 'status_change', :p::jsonb)"),
                              {"o": org, "i": incident_id, "p": json.dumps({"prev": prev, "new": new})})
                else:
                    title = f"{job.name} is {new}" if cond != "flapping" else f"{job.name} is flapping"
                    incident_id = str(s.execute(text(
                        "INSERT INTO incidents (org_id, severity, title, correlation_key, affected_job_ids, rule_id, correlation_signals) VALUES (:o, :sev, :t, :ck, ARRAY[:j]::uuid[], :r, :sig::jsonb) RETURNING id"),
                        {"o": org, "sev": rule.severity, "t": title, "ck": f"job:{job_id}", "j": job_id, "r": rule.id, "sig": json.dumps([{**sig, "job_id": job_id}])}).scalar())
                    s.execute(text("INSERT INTO incident_events (org_id, incident_id, kind, payload) VALUES (:o, :i, 'opened', :p::jsonb)"),
                              {"o": org, "i": incident_id, "p": json.dumps({"prev": prev, "new": new, "rule": rule.id})})

            payload = {"job_id": job.id, "job_name": job.name, "prev_status": prev, "new_status": new, "condition": cond,
                       "severity": rule.severity, "incident_id": incident_id, "execution_id": ev.get("execution_id"), "ts": now.isoformat()}
            for ch in rule.channel_ids:
                send_notification.delay(org, ch, dk, incident_id, payload)
