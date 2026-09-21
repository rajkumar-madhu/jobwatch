"""Builds the telemetry context the Copilot reasons over. Everything passes through redact() (D12).
Returns a compact dict + a human-readable transcript for the prompt."""
import json
from datetime import UTC, datetime

from sqlalchemy import text

from ..redaction import redact


def _rows(s, q, **p): return [dict(r._mapping) for r in s.execute(text(q), p).all()]


def _redact_tree(node):
    """redact() every string in a JSON-ish structure (correlation_signals is free-form jsonb)."""
    if isinstance(node, str): return redact(node)
    if isinstance(node, list): return [_redact_tree(v) for v in node]
    if isinstance(node, dict): return {k: _redact_tree(v) for k, v in node.items()}
    return node


def build(s, org: str, job_id: str | None = None, incident_id: str | None = None, question: str = "") -> dict:
    ctx: dict = {"now": datetime.now(UTC).isoformat()}
    job_ids: list[str] = []
    if incident_id:
        inc = s.execute(text("SELECT id, title, severity::text, status::text, started_at, affected_job_ids, correlation_signals FROM incidents WHERE id=:i AND org_id=:o"), {"i": incident_id, "o": org}).first()
        if inc:
            ctx["incident"] = {"title": redact(inc.title or ""), "severity": inc.severity, "status": inc.status, "started_at": inc.started_at.isoformat(), "signals": _redact_tree(inc.correlation_signals)}
            job_ids = [str(x) for x in inc.affected_job_ids]
    if job_id: job_ids = [job_id] + [j for j in job_ids if j != job_id]
    if not job_ids:  # org-wide question: take recently-failing jobs
        job_ids = [str(r["id"]) for r in _rows(s, "SELECT id FROM jobs WHERE org_id=:o AND status IN ('failed','missed','timeout','late') ORDER BY updated_at DESC LIMIT 8", o=org)]
    ctx["jobs"] = []
    for jid in job_ids[:8]:
        j = s.execute(text("SELECT j.id, j.name, j.kind, j.schedule_expr, j.tz, j.expected_runtime_s, j.grace_s, j.status::text, j.reliability_score, j.command, s.hostname, j.cluster_id FROM jobs j LEFT JOIN servers s ON s.id=j.server_id WHERE j.id=:id AND j.org_id=:o"), {"id": jid, "o": org}).first()
        if not j: continue
        execs = _rows(s, "SELECT id, status::text, scheduled_ts, duration_ms, exit_code, host, pod, node, failure_reason, deploy_version, commit_sha FROM executions WHERE job_id=:id ORDER BY scheduled_ts DESC LIMIT 12", id=jid)
        # R16: failure_reason is free text from the agent (often a URL or a connection string) and
        # was the one execution field that skipped redact().
        for e in execs: e["failure_reason"] = redact(e["failure_reason"]) if e.get("failure_reason") else e.get("failure_reason")
        stderr = _rows(s, "SELECT l.execution_id, l.content FROM execution_logs l JOIN executions e ON e.id=l.execution_id WHERE e.job_id=:id AND l.stream='stderr' ORDER BY e.scheduled_ts DESC LIMIT 3", id=jid)
        stats = s.execute(text("""SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms) AS p50, percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95,
            100.0*count(*) FILTER (WHERE status='success')/NULLIF(count(*),0) AS success_rate FROM executions WHERE job_id=:id AND scheduled_ts >= now() - interval '30 days' AND status IN ('success','failed','timeout','missed')"""), {"id": jid}).first()
        metrics = []
        if j.hostname:
            metrics = _rows(s, """SELECT date_trunc('hour', m.ts) AS hour, round(avg(m.cpu_pct)::numeric,1) AS cpu, round(avg(m.mem_pct)::numeric,1) AS mem, round(avg(m.disk_pct)::numeric,1) AS disk, round(avg(m.io_wait_ms)::numeric,1) AS io_wait_ms, round(max(m.load1)::numeric,2) AS load1
                FROM host_metrics m JOIN servers s ON s.id=m.server_id WHERE s.hostname=:h AND m.org_id=:o AND m.ts >= now() - interval '48 hours' GROUP BY 1 ORDER BY 1 DESC LIMIT 48""", h=j.hostname, o=org)
        k8s = _rows(s, "SELECT reason, message, object_name, ts FROM k8s_events WHERE org_id=:o AND cluster_id=:c AND ts >= now() - interval '24 hours' ORDER BY ts DESC LIMIT 15", o=org, c=str(j.cluster_id)) if j.cluster_id else []
        # R16: k8s event messages carry image refs, pull-secret errors and registry URLs.
        for ev in k8s: ev["message"] = redact(ev["message"] or "")
        deps = _rows(s, "SELECT j2.name, j2.status::text FROM job_dependencies d JOIN jobs j2 ON j2.id=d.depends_on_job_id WHERE d.job_id=:id", id=jid)
        siblings = _rows(s, """SELECT j2.name, j2.status::text, j2.last_run_at FROM jobs j2 WHERE j2.org_id=:o AND j2.id<>:id AND j2.status IN ('failed','missed','timeout')
            AND j2.updated_at >= now() - interval '2 hours' ORDER BY j2.updated_at DESC LIMIT 6""", o=org, id=jid)
        ctx["jobs"].append({"name": j.name, "kind": j.kind, "schedule": j.schedule_expr, "tz": j.tz, "expected_runtime_s": j.expected_runtime_s, "grace_s": j.grace_s, "status": j.status,
                            "reliability": j.reliability_score, "command": redact(j.command or ""), "host": j.hostname, "executions": execs, "stderr_tails": [{"execution_id": r["execution_id"], "text": redact(r["content"])[-2000:]} for r in stderr],
                            "stats_30d": dict(stats._mapping) if stats else {}, "host_metrics_48h": metrics, "k8s_warnings_24h": k8s, "depends_on": deps, "other_failing_jobs_2h": siblings})
    return json.loads(json.dumps(ctx, default=str))


def summarize(ctx: dict) -> dict:
    return {"jobs": [j["name"] for j in ctx.get("jobs", [])], "incident": ctx.get("incident", {}).get("title"), "executions": sum(len(j["executions"]) for j in ctx.get("jobs", [])), "metrics_points": sum(len(j["host_metrics_48h"]) for j in ctx.get("jobs", []))}
