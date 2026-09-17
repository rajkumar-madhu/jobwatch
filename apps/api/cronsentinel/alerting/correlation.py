"""Incident correlation (D10, spec §12). Called from the rule engine before opening a new incident.
Within a 5-minute window, a new failure joins an existing open incident if it shares a signal:
  same host · same cluster+namespace · dependency chain · same deploy_version · infra anomaly on the same server."""
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

WINDOW = timedelta(minutes=5)


def signals_for_job(s, org: str, job_id: str) -> dict:
    j = s.execute(text("SELECT server_id, cluster_id, kind FROM jobs WHERE id=:id AND org_id=:o"), {"id": job_id, "o": org}).first()
    ex = s.execute(text("SELECT host, deploy_version, meta FROM executions WHERE job_id=:id ORDER BY scheduled_ts DESC LIMIT 1"), {"id": job_id}).first()
    ns = None
    if j and j.cluster_id:
        ns = s.execute(text("SELECT namespace FROM k8s_cronjobs WHERE job_id=:id"), {"id": job_id}).scalar()
    upstream = [str(r[0]) for r in s.execute(text("SELECT depends_on_job_id FROM job_dependencies WHERE job_id=:id"), {"id": job_id}).all()]
    downstream = [str(r[0]) for r in s.execute(text("SELECT job_id FROM job_dependencies WHERE depends_on_job_id=:id"), {"id": job_id}).all()]
    sig = {"host": (ex.host if ex else None) or None, "server_id": str(j.server_id) if j and j.server_id else None,
           "cluster_ns": f"{j.cluster_id}:{ns}" if j and j.cluster_id and ns else None, "deploy_version": ex.deploy_version if ex else None,
           "upstream": upstream, "downstream": downstream}
    # infra anomaly: io_wait or disk on this server above threshold in last 15 min
    if sig["server_id"]:
        m = s.execute(text("SELECT max(io_wait_ms) AS iow, max(disk_pct) AS disk, max(cpu_pct) AS cpu FROM host_metrics WHERE server_id=:s AND ts >= now() - interval '15 minutes'"), {"s": sig["server_id"]}).first()
        anomalies = []
        if m and m.iow and m.iow > 100: anomalies.append(f"io_wait {m.iow:.0f}ms")
        if m and m.disk and m.disk > 90: anomalies.append(f"disk {m.disk:.0f}%")
        if m and m.cpu and m.cpu > 90: anomalies.append(f"cpu {m.cpu:.0f}%")
        sig["infra"] = anomalies
    return sig


def find_related_incident(s, org: str, job_id: str, sig: dict):
    """Return (incident_id, reason) of an open incident within WINDOW sharing a signal, else (None, None)."""
    since = datetime.now(timezone.utc) - WINDOW
    cands = s.execute(text("SELECT id, affected_job_ids, correlation_signals, started_at FROM incidents WHERE org_id=:o AND status<>'resolved' AND started_at >= :since ORDER BY started_at DESC"),
                      {"o": org, "since": since - timedelta(minutes=25)}).all()  # incidents may be older but still receiving failures
    for c in cands:
        cs = c.correlation_signals if isinstance(c.correlation_signals, list) else json.loads(c.correlation_signals or "[]")
        affected = {str(a) for a in c.affected_job_ids}
        for prev in cs:
            if sig.get("host") and prev.get("host") == sig["host"]: return c.id, f"same host {sig['host']}"
            if sig.get("cluster_ns") and prev.get("cluster_ns") == sig["cluster_ns"]: return c.id, "same cluster namespace"
            if sig.get("deploy_version") and prev.get("deploy_version") == sig["deploy_version"]: return c.id, f"same deploy {sig['deploy_version']}"
        if affected & set(sig.get("upstream", [])): return c.id, "upstream dependency failed"
        if affected & set(sig.get("downstream", [])): return c.id, "downstream of failing job"
    return None, None


def attach(s, org: str, incident_id, job_id: str, sig: dict, reason: str, prev: str, new: str):
    s.execute(text("""UPDATE incidents SET affected_job_ids = (SELECT array_agg(DISTINCT x) FROM unnest(array_append(affected_job_ids, :j::uuid)) x),
                      correlation_signals = correlation_signals || :sig::jsonb, title = CASE WHEN array_length(affected_job_ids,1) >= 1 THEN
                        (SELECT count(*)+1 FROM unnest(affected_job_ids))::text || ' related jobs failing' ELSE title END WHERE id=:i AND org_id=:o"""),
              {"j": job_id, "sig": json.dumps([{**sig, "job_id": job_id}]), "i": incident_id, "o": org})
    s.execute(text("INSERT INTO incident_events (org_id, incident_id, kind, payload) VALUES (:o, :i, 'correlated', :p::jsonb)"),
              {"o": org, "i": incident_id, "p": json.dumps({"job_id": job_id, "reason": reason, "prev": prev, "new": new})})


def downstream_impact(s, org: str, job_id: str) -> list[dict]:
    """Transitive dependents (for 'downstream impact' on incident + job pages)."""
    return [dict(r._mapping) for r in s.execute(text("""
        WITH RECURSIVE d AS (SELECT job_id, 1 AS depth FROM job_dependencies WHERE depends_on_job_id=:id AND org_id=:o
          UNION SELECT jd.job_id, d.depth+1 FROM job_dependencies jd JOIN d ON jd.depends_on_job_id=d.job_id WHERE d.depth < 10)
        SELECT j.id, j.name, j.status::text, d.depth FROM d JOIN jobs j ON j.id=d.job_id ORDER BY d.depth, j.name"""), {"id": job_id, "o": org}).all()]
