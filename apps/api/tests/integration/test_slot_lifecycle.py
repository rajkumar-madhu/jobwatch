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
    assert jb.job_state == "failing" and jb.status == "missed" and jb.consecutive_failures == len(states)
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
