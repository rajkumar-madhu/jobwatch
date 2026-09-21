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
    # R17: the last/previous outcome comes from the *executions timeline*, not from slots.
    #
    # Before R17 this read the latest settled slot and only fell back to executions when no slot had
    # ever settled. But attach_slot only binds to an open slot, so once a slot succeeded, any further
    # run in that period — a manual rerun, a wrapper retry, a second invocation — was recorded and
    # then ignored: a failure after a success left the job green and raised no alert. Found by the
    # R17 full-stack test (success then fail 360ms apart).
    #
    # Every outcome has an execution row: agents write real runs, and the reconciler writes a
    # 'missed' execution per missed slot and flips runs to 'timeout'. So ordering terminal executions
    # by when they finished covers slot and ad-hoc runs alike. Slots still drive LATE (open_late
    # above) and missed detection; they no longer decide what the most recent outcome was.
    # COALESCE(agent_ts_end, server_received_ts): a delayed delivery of an old run must not override
    # a newer outcome.
    timeline = s.execute(text("""SELECT status::text FROM executions
        WHERE job_id=:id AND status IN ('success','failed','timeout','missed')
        ORDER BY COALESCE(agent_ts_end, server_received_ts) DESC LIMIT 2"""), {"id": job_id}).scalars().all()
    _outcome = {"success": "succeeded", "failed": "failed", "timeout": "failed", "missed": "missed"}
    last = _outcome.get(timeline[0]) if timeline else None
    prev_ok = _outcome.get(timeline[1]) if len(timeline) > 1 else None
    agent_offline = bool(j.agent_seen and datetime.now(UTC) - j.agent_seen > timedelta(seconds=(j.agent_hb or 60) * OFFLINE_MULTIPLIER))
    last_slot = SlotState(last) if last else None
    state, reason = derive(paused=j.paused, has_schedule=bool(j.schedule_expr), ever_ran=bool(j.last_run_at),
                           agent_offline=agent_offline, open_late=bool(open_late), running=bool(running), last_settled=last_slot)
    legacy = legacy_status(state, paused=j.paused, running=bool(running), last_settled=last_slot,
                           recovered=last == "succeeded" and prev_ok in ("failed", "missed"))
    # Same timeline as above, so the count agrees with the state: failures since the last success.
    # NOTE: unbounded scan of this job's executions when it has never succeeded; fine at current
    # scale, worth a time bound if a job accumulates tens of thousands of consecutive failures.
    fails = s.execute(text("""SELECT count(*) FROM executions WHERE job_id=:id AND status IN ('failed','timeout','missed')
        AND COALESCE(agent_ts_end, server_received_ts) > COALESCE((SELECT max(COALESCE(agent_ts_end, server_received_ts))
            FROM executions WHERE job_id=:id AND status='success'), '-infinity'::timestamptz)"""), {"id": job_id}).scalar()
    if state.value == j.job_state and legacy == j.status and (reason or None) == j.unknown_reason:
        s.execute(text("UPDATE jobs SET consecutive_failures=:c WHERE id=:id"), {"c": fails, "id": job_id})
        return None
    s.execute(text("""UPDATE jobs SET job_state=CAST(:st AS job_state), unknown_reason=:r, status=CAST(:legacy AS job_status), consecutive_failures=:c,
        state_since=CASE WHEN job_state=CAST(:st AS job_state) THEN state_since ELSE now() END, updated_at=now() WHERE id=:id"""),
        {"st": state.value, "r": reason, "legacy": legacy, "c": fails, "id": job_id})
    return {"job_id": str(job_id), "org_id": str(org_id), "prev_status": j.status, "new_status": legacy,
            "prev_state": j.job_state, "new_state": state.value, "unknown_reason": reason, "consecutive_failures": fails}
