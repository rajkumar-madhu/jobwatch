"""Execution event → DB state. Shared by ingest (sync fallback) and NATS worker.
Idempotent on (execution_id, sequence) — D5. Monotonic status."""
from datetime import datetime, timezone

from sqlalchemy import text
from ulid import ULID

from .config import settings
from .redaction import redact
from .state_machine import Event, JobStatus, transition

_ORDER = {"scheduled": 0, "running": 1, "success": 2, "failed": 2, "timeout": 2, "missed": 2}


def process(s, ev: dict) -> dict | None:
    """ev: {org_id, job_id, agent_id?, kind: start|success|fail, execution_id?, sequence, agent_ts?, server_ts,
    duration_ms?, exit_code?, host?, stdout_tail?, stderr_tail?, meta}
    Returns {job_id, prev_status, new_status} or None if duplicate."""
    org, job_id = ev["org_id"], ev["job_id"]
    exec_id = ev.get("execution_id") or str(ULID())
    server_ts = datetime.fromisoformat(ev["server_ts"]) if isinstance(ev.get("server_ts"), str) else ev.get("server_ts") or datetime.now(timezone.utc)
    agent_ts = ev.get("agent_ts")
    if isinstance(agent_ts, str):
        agent_ts = datetime.fromisoformat(agent_ts)
    skew_ms = int((server_ts - agent_ts).total_seconds() * 1000) if agent_ts else 0

    dup = s.execute(text(
        "INSERT INTO execution_events (org_id, execution_id, agent_id, sequence, kind, agent_ts, server_ts, payload) "
        "VALUES (:org, :eid, :agent, :seq, :kind, :ats, :sts, :payload::jsonb) ON CONFLICT DO NOTHING RETURNING 1"),
        {"org": org, "eid": exec_id, "agent": ev.get("agent_id"), "seq": ev.get("sequence", 0), "kind": ev["kind"],
         "ats": agent_ts, "sts": server_ts, "payload": __import__("json").dumps({k: v for k, v in ev.get("meta", {}).items()})}).first()
    if not dup:
        return None  # duplicate delivery

    job = s.execute(text("SELECT status, schedule_expr, tz, expected_runtime_s FROM jobs WHERE id=:id AND org_id=:org FOR UPDATE"),
                    {"id": job_id, "org": org}).first()
    if not job:
        return None
    prev = JobStatus(job.status)

    if ev["kind"] == "start":
        s.execute(text(
            "INSERT INTO executions (id, org_id, job_id, agent_id, status, scheduled_ts, agent_ts_start, server_received_ts, skew_ms, host, sequence_max, meta) "
            "VALUES (:id, :org, :job, :agent, 'running', :sched, :ats, :sts, :skew, :host, :seq, :meta::jsonb) "
            "ON CONFLICT (id, scheduled_ts) DO UPDATE SET status='running', agent_ts_start=EXCLUDED.agent_ts_start, sequence_max=GREATEST(executions.sequence_max, EXCLUDED.sequence_max)"),
            {"id": exec_id, "org": org, "job": job_id, "agent": ev.get("agent_id"), "sched": agent_ts or server_ts,
             "ats": agent_ts or server_ts, "sts": server_ts, "skew": skew_ms, "host": ev.get("host"), "seq": ev.get("sequence", 0),
             "meta": __import__("json").dumps(ev.get("meta", {}))})
        new = transition(prev, Event.STARTED)
    else:
        final = "success" if ev["kind"] == "success" else "failed"
        end_ts = agent_ts or server_ts
        row = s.execute(text("SELECT scheduled_ts, agent_ts_start FROM executions WHERE id=:id AND org_id=:org"), {"id": exec_id, "org": org}).first()
        if row:
            start = row.agent_ts_start
            duration = ev.get("duration_ms") or (int((end_ts - start).total_seconds() * 1000) if start else None)
            s.execute(text(
                "UPDATE executions SET status=:st, agent_ts_end=:end, duration_ms=:dur, exit_code=:ec, host=COALESCE(:host, host), "
                "sequence_max=GREATEST(sequence_max, :seq) WHERE id=:id AND org_id=:org AND :rank >= (CASE status WHEN 'running' THEN 1 WHEN 'scheduled' THEN 0 ELSE 2 END)"),
                {"st": final, "end": end_ts, "dur": duration, "ec": ev.get("exit_code"), "host": ev.get("host"), "seq": ev.get("sequence", 0),
                 "id": exec_id, "org": org, "rank": _ORDER[final]})
        else:
            # success/fail without prior start (simple ping mode): synthesize a single-point execution
            duration = ev.get("duration_ms")
            start = end_ts if duration is None else datetime.fromtimestamp(end_ts.timestamp() - duration / 1000, tz=timezone.utc)
            s.execute(text(
                "INSERT INTO executions (id, org_id, job_id, agent_id, status, scheduled_ts, agent_ts_start, agent_ts_end, server_received_ts, skew_ms, duration_ms, exit_code, host, sequence_max, meta) "
                "VALUES (:id, :org, :job, :agent, :st, :sched, :start, :end, :sts, :skew, :dur, :ec, :host, :seq, :meta::jsonb) ON CONFLICT DO NOTHING"),
                {"id": exec_id, "org": org, "job": job_id, "agent": ev.get("agent_id"), "st": final, "sched": start, "start": start, "end": end_ts,
                 "sts": server_ts, "skew": skew_ms, "dur": duration, "ec": ev.get("exit_code"), "host": ev.get("host"), "seq": ev.get("sequence", 0),
                 "meta": __import__("json").dumps(ev.get("meta", {}))})
        for stream in ("stdout", "stderr"):
            tail = ev.get(f"{stream}_tail")
            if tail:
                s.execute(text(
                    "INSERT INTO execution_logs (org_id, execution_id, stream, chunk_idx, content) VALUES (:org, :eid, :stream, 0, :c) "
                    "ON CONFLICT DO NOTHING"),
                    {"org": org, "eid": exec_id, "stream": stream, "c": redact(tail)[-settings.max_log_bytes_per_execution:]})
        new = transition(prev, Event.COMPLETED_OK if final == "success" else Event.COMPLETED_FAIL)
        s.execute(text("UPDATE jobs SET last_run_at=:ts, last_status=:st WHERE id=:id"), {"ts": end_ts, "st": final, "id": job_id})

    # advance next_expected_at only when a run has started/completed
    if job.schedule_expr:
        from .schedule import next_run
        s.execute(text("UPDATE jobs SET next_expected_at=:n, late_marked_at=NULL WHERE id=:id"),
                  {"n": next_run(job.schedule_expr, job.tz, server_ts), "id": job_id})

    s.execute(text("UPDATE jobs SET status=:st, updated_at=now() WHERE id=:id"), {"st": new.value, "id": job_id})
    return {"job_id": job_id, "execution_id": exec_id, "prev_status": prev.value, "new_status": new.value}
