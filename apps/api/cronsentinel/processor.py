"""Execution event → DB state. Shared by ingest (sync fallback) and NATS worker.
Idempotent on (execution_id, sequence) — D5. Monotonic status."""
from datetime import UTC, datetime

from sqlalchemy import text
from ulid import ULID

from .config import settings
from .redaction import redact
from .state_machine import Event, JobStatus, transition

_ORDER = {"scheduled": 0, "running": 1, "success": 2, "failed": 2, "timeout": 2, "missed": 2}


def attach_slot(s, org, job_id, started_at, exec_id: str, status: str) -> int | None:
    """R3: bind an execution to the expected_run slot it belongs to, so late/missed accounting is
    per-slot rather than per-job. Executions with no matching slot (manual run, ad-hoc invocation,
    heartbeat-only job) stay unattached — they still record, they just do not settle a slot."""
    row = s.execute(text("""
        SELECT id FROM expected_runs
        WHERE job_id = :j AND org_id = :o AND state IN ('pending','late','running')
          AND :ts BETWEEN scheduled_for - interval '90 seconds' AND deadline
        ORDER BY abs(EXTRACT(EPOCH FROM (:ts - scheduled_for))), scheduled_for LIMIT 1"""),
        {"j": job_id, "o": org, "ts": started_at}).first()
    if not row:
        return None
    new = {"running": "running", "success": "succeeded", "failed": "failed", "timeout": "failed"}.get(status, "running")
    # :st is used once as an enum and once as text → psycopg cannot infer one type; cast explicitly
    s.execute(text("""UPDATE expected_runs SET state=CAST(:st AS expected_run_state), execution_id=:e, matched_at=now(),
        settled_at = CASE WHEN CAST(:st AS text) IN ('succeeded','failed') THEN now() ELSE settled_at END WHERE id=:id"""),
        {"st": new, "e": exec_id, "id": row.id})
    s.execute(text("UPDATE executions SET expected_run_id=:er WHERE id=:e AND org_id=:o"), {"er": row.id, "e": exec_id, "o": org})
    return row.id


def process(s, ev: dict) -> dict | None:
    """ev: {org_id, job_id, agent_id?, kind: start|success|fail, execution_id?, sequence, agent_ts?, server_ts,
    duration_ms?, exit_code?, host?, stdout_tail?, stderr_tail?, meta}
    Returns {job_id, prev_status, new_status} or None if duplicate."""
    org, job_id = ev["org_id"], ev["job_id"]
    exec_id = ev.get("execution_id") or str(ULID())
    server_ts = datetime.fromisoformat(ev["server_ts"]) if isinstance(ev.get("server_ts"), str) else ev.get("server_ts") or datetime.now(UTC)
    agent_ts = ev.get("agent_ts")
    if isinstance(agent_ts, str):
        agent_ts = datetime.fromisoformat(agent_ts)
    skew_ms = int((server_ts - agent_ts).total_seconds() * 1000) if agent_ts else 0

    dup = s.execute(text(
        "INSERT INTO execution_events (org_id, execution_id, agent_id, sequence, kind, agent_ts, server_ts, payload) "
        "VALUES (:org, :eid, :agent, :seq, :kind, :ats, :sts, CAST(:payload AS jsonb)) ON CONFLICT DO NOTHING RETURNING 1"),
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
            "VALUES (:id, :org, :job, :agent, 'running', :sched, :ats, :sts, :skew, :host, :seq, CAST(:meta AS jsonb)) "
            "ON CONFLICT (id, scheduled_ts) DO UPDATE SET status='running', agent_ts_start=EXCLUDED.agent_ts_start, sequence_max=GREATEST(executions.sequence_max, EXCLUDED.sequence_max)"),
            {"id": exec_id, "org": org, "job": job_id, "agent": ev.get("agent_id"), "sched": agent_ts or server_ts,
             "ats": agent_ts or server_ts, "sts": server_ts, "skew": skew_ms, "host": ev.get("host"), "seq": ev.get("sequence", 0),
             "meta": __import__("json").dumps(ev.get("meta", {}))})
        attach_slot(s, org, job_id, agent_ts or server_ts, exec_id, "running")
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
            start = end_ts if duration is None else datetime.fromtimestamp(end_ts.timestamp() - duration / 1000, tz=UTC)
            s.execute(text(
                "INSERT INTO executions (id, org_id, job_id, agent_id, status, scheduled_ts, agent_ts_start, agent_ts_end, server_received_ts, skew_ms, duration_ms, exit_code, host, sequence_max, meta) "
                "VALUES (:id, :org, :job, :agent, :st, :sched, :start, :end, :sts, :skew, :dur, :ec, :host, :seq, CAST(:meta AS jsonb)) ON CONFLICT DO NOTHING"),
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
        attach_slot(s, org, job_id, start or end_ts, exec_id, "success" if final == "success" else "failed")
        new = transition(prev, Event.COMPLETED_OK if final == "success" else Event.COMPLETED_FAIL)
        s.execute(text("UPDATE jobs SET last_run_at=:ts, last_status=:st WHERE id=:id"), {"ts": end_ts, "st": final, "id": job_id})

    # R3: expected_runs is the source of truth for late/missed. next_expected_at is now a display
    # cache only — read from the next unsettled slot so a late start cannot push the expectation
    # forward (the old behaviour silently hid a job that drifted later every run).
    if job.schedule_expr:
        s.execute(text("""UPDATE jobs SET next_expected_at = (
            SELECT min(scheduled_for) FROM expected_runs WHERE job_id=:id AND state='pending' AND scheduled_for > now()
        ), late_marked_at=NULL WHERE id=:id"""), {"id": job_id})

    s.execute(text("UPDATE jobs SET status=:st, updated_at=now() WHERE id=:id"), {"st": new.value, "id": job_id})
    return {"job_id": job_id, "execution_id": exec_id, "prev_status": prev.value, "new_status": new.value}
