import uuid

"""R3 end-to-end: generator → execution → slot match → reconciler → four-state."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import text


def now():
    return datetime.now(UTC)


def _slots(job_id):
    from cronsentinel.db import system_session
    with system_session() as s:
        return s.execute(text("SELECT scheduled_for, state::text, execution_id FROM expected_runs WHERE job_id=:j ORDER BY scheduled_for"), {"j": job_id}).all()


def _job(job_id):
    from cronsentinel.db import system_session
    with system_session() as s:
        return s.execute(text("SELECT job_state::text, status::text, unknown_reason, consecutive_failures, next_expected_at FROM jobs WHERE id=:j"), {"j": job_id}).first()


def test_generator_materialises_past_and_future_slots(make_job):
    from cronsentinel.db import system_session
    from cronsentinel.workers.schedule_generator import tick
    j = make_job(schedule="*/5 * * * *", created_ago=timedelta(minutes=30))
    with system_session() as s:
        tick(s)
    sl = _slots(j)
    past = [x for x in sl if x.scheduled_for < now()]
    future = [x for x in sl if x.scheduled_for > now()]
    assert 5 <= len(past) <= 6, "30 minutes of */5 backfilled from created_at"
    assert len(future) >= 70, "6h horizon ahead"


def test_generator_is_idempotent(make_job):
    from cronsentinel.db import system_session
    from cronsentinel.workers.schedule_generator import tick
    j = make_job()
    with system_session() as s:
        tick(s)
    n1 = len(_slots(j))
    with system_session() as s:
        tick(s); tick(s)
    assert len(_slots(j)) == n1


def test_overdue_slots_become_missed_and_job_failing(make_job, org):
    from cronsentinel.db import system_session
    from cronsentinel.workers import reconciler
    from cronsentinel.workers.schedule_generator import tick
    j = make_job(schedule="*/5 * * * *", grace_s=60, expected_runtime_s=30, created_ago=timedelta(minutes=30))
    with system_session() as s:
        tick(s)
        changes = reconciler.tick(s)
    states = [x.state for x in _slots(j) if x.scheduled_for < now() - timedelta(seconds=90)]
    assert states and all(st == "missed" for st in states)
    jb = _job(j)
    # Whether the newest slot is still inside its grace window depends on where "now" falls in the
    # */5 cycle, so an open LATE slot is a legitimate outcome — derive() puts open_late ahead of the
    # last settled slot on purpose. Assert on what is actually invariant.
    open_late = any(x.state == "late" for x in _slots(j))
    assert jb.job_state == ("late" if open_late else "failing"), f"state={jb.job_state} open_late={open_late}"
    assert jb.consecutive_failures == len(states)
    if not open_late:
        assert jb.status == "missed"
    assert any(c["job_id"] == str(j) and c["new_state"] == "failing" for c in changes)
    with system_session() as s:  # synthetic execution rows exist for the run strip / SLA
        assert s.execute(text("SELECT count(*) FROM executions WHERE job_id=:j AND status='missed'"), {"j": j}).scalar() == len(states)


def test_reconciler_detects_misses_that_happened_while_it_was_down(make_job):
    """The R3 headline: slots exist independently, so downtime delays detection but never loses it."""
    from cronsentinel.db import system_session
    from cronsentinel.workers import reconciler
    from cronsentinel.workers.schedule_generator import tick
    j = make_job(schedule="* * * * *", grace_s=30, expected_runtime_s=10, created_ago=timedelta(minutes=10))
    with system_session() as s:
        tick(s)  # generator ran; reconciler "was down" for these 10 minutes
    with system_session() as s:
        reconciler.tick(s)  # first tick after coming back
    missed = [x for x in _slots(j) if x.state == "missed"]
    assert len(missed) >= 8


def test_execution_binds_to_its_slot_and_job_goes_ok(make_job, org):
    from cronsentinel.db import system_session
    from cronsentinel.processor import process
    from cronsentinel.workers import reconciler
    from cronsentinel.workers.schedule_generator import tick
    j = make_job(schedule="* * * * *", grace_s=120, expected_runtime_s=60, created_ago=timedelta(minutes=2))
    with system_session() as s:
        tick(s)
    eid = f"it-{uuid.uuid4().hex}"  # execution ids are globally unique (agent-minted ULIDs); never reuse across runs
    ev = {"org_id": org["id"], "job_id": j, "kind": "start", "execution_id": eid, "sequence": 0,
          "agent_ts": now().isoformat(), "server_ts": now().isoformat(), "meta": {}}
    with system_session() as s:
        process(s, ev)
    with system_session() as s:  # bound to the nearest slot whose window contains the start (minute-boundary safe)
        row = s.execute(text("SELECT state::text, scheduled_for, deadline FROM expected_runs WHERE job_id=:j AND execution_id=:e"), {"j": j, "e": eid}).first()
    assert row is not None and row.state == "running"
    assert row.scheduled_for - timedelta(seconds=90) <= now() <= row.deadline
    target = row.scheduled_for
    with system_session() as s:
        process(s, {**ev, "kind": "success", "sequence": 1, "duration_ms": 1200, "exit_code": 0})
    with system_session() as s:
        row = s.execute(text("SELECT state::text FROM expected_runs WHERE job_id=:j AND scheduled_for=:t"), {"j": j, "t": target}).first()
        reconciler.recompute_state(s, j, org["id"])
    assert row.state == "succeeded"
    jb = _job(j)
    assert jb.job_state == "ok" and jb.status in ("healthy", "recovered")
    assert jb.next_expected_at is not None and jb.next_expected_at > now(), "display cache reads from the next pending slot"


def test_late_start_does_not_drift_next_expectation(make_job, org):
    """Old bug: a late run rolled next_expected_at forward from now(). Now it comes from the slot table."""
    from cronsentinel.db import system_session
    from cronsentinel.processor import process
    from cronsentinel.workers.schedule_generator import tick
    j = make_job(schedule="0 * * * *", grace_s=3600, expected_runtime_s=60, created_ago=timedelta(hours=2))
    eid = f"it-{uuid.uuid4().hex}"
    with system_session() as s:
        tick(s)
    with system_session() as s:
        process(s, {"org_id": org["id"], "job_id": j, "kind": "start", "execution_id": eid, "sequence": 0, "server_ts": now().isoformat(), "meta": {}})
        process(s, {"org_id": org["id"], "job_id": j, "kind": "success", "execution_id": eid, "sequence": 1, "server_ts": now().isoformat(), "meta": {}})
    jb = _job(j)
    assert jb.next_expected_at.minute == 0 and jb.next_expected_at.second == 0, "next expectation is an on-schedule slot, not now()+interval"


def test_paused_job_slots_are_skipped_not_missed(make_job, org):
    from cronsentinel.db import system_session
    from cronsentinel.workers import reconciler
    from cronsentinel.workers.schedule_generator import tick
    j = make_job(schedule="* * * * *", grace_s=30, expected_runtime_s=10, created_ago=timedelta(minutes=5))
    with system_session() as s:
        tick(s)
        s.execute(text("UPDATE jobs SET paused=true WHERE id=:j"), {"j": j})
    with system_session() as s:
        reconciler.tick(s)
    states = {x.state for x in _slots(j) if x.scheduled_for < now() - timedelta(seconds=60)}
    assert states == {"skipped"}
    assert _job(j).job_state == "unknown" and _job(j).unknown_reason == "paused"


def _run(s, org, job, kind, exit_code=None, offset_s=0.0):
    """One complete run (start + terminal event) through the real processor."""
    from cronsentinel.processor import process
    eid = f"it-{uuid.uuid4().hex}"
    t = (now() + timedelta(seconds=offset_s)).isoformat()
    base = {"org_id": org, "job_id": job, "execution_id": eid, "agent_ts": t, "server_ts": t, "meta": {}}
    process(s, {**base, "kind": "start", "sequence": 0})
    # process() recomputes state itself and returns the change it emitted onto CS_STATUS — that
    # return value is what the rule engine would alert on.
    return process(s, {**base, "kind": kind, "sequence": 1, "duration_ms": 500, "exit_code": exit_code})


def test_failure_after_success_in_the_same_slot_is_not_ignored(make_job, org):
    """R17 regression. Found by the full-stack test: a success settles the slot, a second run in the
    same period (manual rerun, wrapper retry) fails, and attach_slot finds no open slot to bind it
    to. State used to be derived from slots only, so the failure was recorded and then ignored —
    job green, no status change, no alert."""
    from cronsentinel.db import system_session
    from cronsentinel.workers import reconciler
    from cronsentinel.workers.schedule_generator import tick
    j = make_job(schedule="* * * * *", grace_s=120, expected_runtime_s=60, created_ago=timedelta(minutes=2))
    with system_session() as s:
        tick(s)
    with system_session() as s:
        _run(s, org["id"], j, "success", 0)
    with system_session() as s:
        reconciler.recompute_state(s, j, org["id"])
    assert _job(j).job_state == "ok"

    with system_session() as s:
        # Where the failure binds depends on which slot windows are open at this instant: no slot at
        # all, or an *older* slot still inside its grace. Both broke the old derivation — it ranked
        # settled slots by scheduled_for, so the success's newer slot won either way. The invariant
        # is the outcome, so that is what is asserted.
        change = _run(s, org["id"], j, "fail", 2, offset_s=1)
    jb = _job(j)
    assert jb.job_state == "failing", f"a failed run after a success left the job {jb.job_state}"
    assert jb.status == "failed" and jb.consecutive_failures == 1
    assert change and change["new_state"] == "failing", "no status change emitted, so no alert would fire"


def test_success_after_failure_in_the_same_slot_recovers(make_job, org):
    """The mirror case: a failed run then a successful retry within one period must recover,
    not stay failing because the slot was settled 'failed' first."""
    from cronsentinel.db import system_session
    from cronsentinel.workers import reconciler
    from cronsentinel.workers.schedule_generator import tick
    j = make_job(schedule="* * * * *", grace_s=120, expected_runtime_s=60, created_ago=timedelta(minutes=2))
    with system_session() as s:
        tick(s)
    with system_session() as s:
        _run(s, org["id"], j, "fail", 1)
    with system_session() as s:
        reconciler.recompute_state(s, j, org["id"])
    assert _job(j).job_state == "failing"
    with system_session() as s:
        _run(s, org["id"], j, "success", 0, offset_s=1)
    with system_session() as s:
        reconciler.recompute_state(s, j, org["id"])
    jb = _job(j)
    assert jb.job_state == "ok" and jb.status == "recovered" and jb.consecutive_failures == 0
