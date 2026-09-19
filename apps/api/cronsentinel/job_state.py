"""Single place that derives and persists a job's four-state.

Both producers on `jobstatus.*` call this — the reconciler (slot timeouts) and the processor
(an execution arriving). Before R6 the processor only wrote the legacy `status` column, so a
genuinely failing job kept `job_state = 'unknown'` and UNKNOWN-suppression silently ate its
alert. Deriving state in one function makes that class of drift impossible.
"""
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from .states import JobState, SlotState, derive, legacy_status  # noqa: F401

OFFLINE_MULTIPLIER = 3  # agent silent for 3x its own reported heartbeat interval => UNKNOWN, not failing


def recompute_state(s, job_id, org_id) -> dict | None:
    """Recompute the four-state for one job from its slots. Returns a change event, or None."""
    j = s.execute(text("""SELECT j.id, j.paused, j.schedule_expr, j.last_run_at, j.job_state::text, j.status::text, j.state_since, j.unknown_reason,
            a.last_seen_at AS agent_seen, a.heartbeat_interval_s AS agent_hb
        FROM jobs j LEFT JOIN servers sv ON sv.id=j.server_id LEFT JOIN agents a ON a.id=sv.agent_id
        WHERE j.id=:id FOR UPDATE OF j"""), {"id": job_id}).first()
    if not j:
        return None
    open_late = s.execute(text("SELECT EXISTS (SELECT 1 FROM expected_runs WHERE job_id=:id AND state='late')"), {"id": job_id}).scalar()
    running = s.execute(text("SELECT EXISTS (SELECT 1 FROM executions WHERE job_id=:id AND status='running')"), {"id": job_id}).scalar()
    last = s.execute(text("""SELECT state::text FROM expected_runs WHERE job_id=:id AND state IN ('succeeded','failed','missed')
        ORDER BY scheduled_for DESC LIMIT 1"""), {"id": job_id}).scalar()
    prev_ok = s.execute(text("""SELECT state::text FROM expected_runs WHERE job_id=:id AND state IN ('succeeded','failed','missed')
        ORDER BY scheduled_for DESC OFFSET 1 LIMIT 1"""), {"id": job_id}).scalar()
    agent_offline = bool(j.agent_seen and datetime.now(UTC) - j.agent_seen > timedelta(seconds=(j.agent_hb or 60) * OFFLINE_MULTIPLIER))
    if last is None:
        # Heartbeat-only jobs and ad-hoc runs never produce slots; fall back to the last execution
        # so the four-state is still derivable for them (they simply cannot be LATE or MISSED).
        lastx = s.execute(text("""SELECT status::text FROM executions WHERE job_id=:id AND status IN ('success','failed','timeout','missed')
            ORDER BY COALESCE(agent_ts_end, server_received_ts) DESC LIMIT 1"""), {"id": job_id}).scalar()
        last = {"success": "succeeded", "failed": "failed", "timeout": "failed", "missed": "missed"}.get(lastx)
        if prev_ok is None:
            prevx = s.execute(text("""SELECT status::text FROM executions WHERE job_id=:id AND status IN ('success','failed','timeout','missed')
                ORDER BY COALESCE(agent_ts_end, server_received_ts) DESC OFFSET 1 LIMIT 1"""), {"id": job_id}).scalar()
            prev_ok = {"success": "succeeded", "failed": "failed", "timeout": "failed", "missed": "missed"}.get(prevx)
    last_slot = SlotState(last) if last else None
    state, reason = derive(paused=j.paused, has_schedule=bool(j.schedule_expr), ever_ran=bool(j.last_run_at),
                           agent_offline=agent_offline, open_late=bool(open_late), running=bool(running), last_settled=last_slot)
    legacy = legacy_status(state, paused=j.paused, running=bool(running), last_settled=last_slot,
                           recovered=last == "succeeded" and prev_ok in ("failed", "missed"))
    fails = s.execute(text("""SELECT count(*) FROM expected_runs WHERE job_id=:id AND state IN ('failed','missed')
        AND scheduled_for > COALESCE((SELECT max(scheduled_for) FROM expected_runs WHERE job_id=:id AND state='succeeded'), '-infinity'::timestamptz)"""), {"id": job_id}).scalar()
    if state.value == j.job_state and legacy == j.status and (reason or None) == j.unknown_reason:
        s.execute(text("UPDATE jobs SET consecutive_failures=:c WHERE id=:id"), {"c": fails, "id": job_id})
        return None
    s.execute(text("""UPDATE jobs SET job_state=CAST(:st AS job_state), unknown_reason=:r, status=CAST(:legacy AS job_status), consecutive_failures=:c,
        state_since=CASE WHEN job_state=CAST(:st AS job_state) THEN state_since ELSE now() END, updated_at=now() WHERE id=:id"""),
        {"st": state.value, "r": reason, "legacy": legacy, "c": fails, "id": job_id})
    return {"job_id": str(job_id), "org_id": str(org_id), "prev_status": j.status, "new_status": legacy,
            "prev_state": j.job_state, "new_state": state.value, "unknown_reason": reason, "consecutive_failures": fails}
