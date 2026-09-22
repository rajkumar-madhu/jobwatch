"""R25 — an outage on our side must never page a customer.

Two mechanisms behind the backfill storm, each with its own test, plus the regression that a
genuinely empty slot under a healthy platform is still missed.
"""
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs postgres (DATABASE_URL)")


def _slot(s, org, job, scheduled_for, grace_s=60, deadline_s=600):
    return s.execute(text("""INSERT INTO expected_runs (org_id, job_id, scheduled_for, grace_until, deadline)
        VALUES (:o, :j, :sf, :sf + make_interval(secs => :g), :sf + make_interval(secs => :d)) RETURNING id"""),
        {"o": org, "j": job, "sf": scheduled_for, "g": grace_s, "d": deadline_s}).scalar()


def _exec(s, org, job, started, status="success"):
    eid = f"it-{uuid.uuid4().hex[:10]}"
    s.execute(text("""INSERT INTO executions (id, org_id, job_id, status, scheduled_ts, agent_ts_start, agent_ts_end, server_received_ts)
        VALUES (:e, :o, :j, :st, :t, :t, :t + interval '5 seconds', :t + interval '6 seconds')"""),
        {"e": eid, "o": org, "j": job, "st": status, "t": started})
    return eid


def _state(s, slot):
    return s.execute(text("SELECT state, execution_id FROM expected_runs WHERE id=:i"), {"i": slot}).first()


@pytest.fixture(autouse=True)
def _healthy_platform():
    """Every test starts with both workers freshly heard from and no recorded gaps."""
    from cronsentinel.db import system_session
    with system_session() as s:
        s.execute(text("DELETE FROM monitoring_gaps"))
        s.execute(text("""INSERT INTO platform_heartbeats (service, last_seen) VALUES ('schedule-generator', now()), ('reconciler', now())
            ON CONFLICT (service) DO UPDATE SET last_seen = now()"""))
    yield


def test_backfilled_slot_binds_to_the_execution_that_already_arrived(make_job, org):
    """Mechanism 1: the run happened, no slot existed, the execution sat unattached. Backfilling
    the slot must find it — not declare a miss and page the customer."""
    from cronsentinel.db import system_session
    from cronsentinel.workers import reconciler
    j = make_job()
    sched = datetime.now(UTC) - timedelta(hours=1)
    with system_session() as s:
        e = _exec(s, org["id"], j, sched + timedelta(seconds=20))
        slot = _slot(s, org["id"], j, sched)
        reconciler.tick(s)
        st = _state(s, slot)
        assert st == ("succeeded", e), st
        assert s.execute(text("SELECT expected_run_id FROM executions WHERE id=:e"), {"e": e}).scalar() == slot
        assert s.execute(text("SELECT count(*) FROM executions WHERE job_id=:j AND status='missed'"), {"j": j}).scalar() == 0
        assert s.execute(text("SELECT job_state FROM jobs WHERE id=:j"), {"j": j}).scalar() == "ok"


def test_retro_match_uses_one_execution_per_slot_and_keeps_the_nearest(make_job, org):
    """Two adjacent backfilled slots and two executions: each slot gets its own run, and one
    execution can never settle two slots."""
    from cronsentinel.db import system_session
    from cronsentinel.workers import reconciler
    j = make_job()
    t0 = datetime.now(UTC) - timedelta(hours=2)
    with system_session() as s:
        e1 = _exec(s, org["id"], j, t0 + timedelta(seconds=10), "failed")
        e2 = _exec(s, org["id"], j, t0 + timedelta(minutes=5, seconds=10))
        s1 = _slot(s, org["id"], j, t0)                          # deadline t0+10m covers both starts
        s2 = _slot(s, org["id"], j, t0 + timedelta(minutes=5))
        reconciler.tick(s)
        assert _state(s, s1) == ("failed", e1)
        assert _state(s, s2) == ("succeeded", e2)


def test_slot_inside_a_recorded_gap_is_unobserved_not_missed(make_job, org):
    """Mechanism 2: the generator was down for 40 minutes; a slot whose deadline fell in that window
    and that nobody reported is unobserved. No synthetic execution, no failure, no alert."""
    from cronsentinel.db import system_session
    from cronsentinel.workers import reconciler
    from cronsentinel.workers.platform_health import heartbeat
    j = make_job()
    with system_session() as s:
        s.execute(text("UPDATE platform_heartbeats SET last_seen = now() - interval '40 minutes' WHERE service='schedule-generator'"))
        assert heartbeat(s, "schedule-generator", 60) is True            # closes the gap on first pass back
        gap = s.execute(text("SELECT started_at, ended_at FROM monitoring_gaps")).first()
        assert gap and (gap.ended_at - gap.started_at) > timedelta(minutes=39)
        inside = _slot(s, org["id"], j, datetime.now(UTC) - timedelta(minutes=30))    # deadline 20 min ago, in the gap
        reconciler.tick(s)
        assert _state(s, inside) == ("unobserved", None)
        assert s.execute(text("SELECT count(*) FROM executions WHERE job_id=:j"), {"j": j}).scalar() == 0
        assert s.execute(text("SELECT job_state FROM jobs WHERE id=:j"), {"j": j}).scalar() != "failed"
        assert s.execute(text("SELECT slots_unobserved FROM monitoring_gaps")).scalar() == 1


def test_silent_generator_is_an_open_gap_for_the_reconciler(make_job, org):
    """Order of recovery must not matter: the reconciler back first, generator still silent."""
    from cronsentinel.db import system_session
    from cronsentinel.workers import reconciler
    j = make_job()
    with system_session() as s:
        s.execute(text("UPDATE platform_heartbeats SET last_seen = now() - interval '20 minutes' WHERE service='schedule-generator'"))
        slot = _slot(s, org["id"], j, datetime.now(UTC) - timedelta(minutes=15))
        reconciler.tick(s)
        assert _state(s, slot) == ("unobserved", None)
        assert s.execute(text("SELECT count(*) FROM monitoring_gaps")).scalar() == 0   # generator records it when it returns


def test_healthy_platform_still_marks_a_real_miss(make_job, org):
    """Regression: the storm fix must not turn every miss into 'unobserved'."""
    from cronsentinel.db import system_session
    from cronsentinel.workers import reconciler
    j = make_job()
    with system_session() as s:
        slot = _slot(s, org["id"], j, datetime.now(UTC) - timedelta(minutes=15))
        reconciler.tick(s)
        assert _state(s, slot) == ("missed", None)
        assert s.execute(text("SELECT count(*) FROM executions WHERE job_id=:j AND status='missed'"), {"j": j}).scalar() == 1


def test_first_heartbeat_and_a_slow_pass_are_not_gaps():
    from cronsentinel.db import system_session
    from cronsentinel.workers.platform_health import heartbeat
    with system_session() as s:
        s.execute(text("DELETE FROM platform_heartbeats WHERE service='it-svc'"))
        assert heartbeat(s, "it-svc", 60) is False                         # first ever: an observation, not a gap
        s.execute(text("UPDATE platform_heartbeats SET last_seen = now() - interval '2 minutes' WHERE service='it-svc'"))
        assert heartbeat(s, "it-svc", 60) is False                         # under 3× interval: a slow tick
        assert s.execute(text("SELECT count(*) FROM monitoring_gaps WHERE service='it-svc'")).scalar() == 0
        s.execute(text("DELETE FROM platform_heartbeats WHERE service='it-svc'"))


def test_slot_before_the_gap_is_still_missed(make_job, org):
    """The gap predicate is by deadline: a slot that came due while we were watching stays a miss."""
    from cronsentinel.db import system_session
    from cronsentinel.workers import reconciler
    from cronsentinel.workers.platform_health import heartbeat
    j = make_job()
    with system_session() as s:
        s.execute(text("UPDATE platform_heartbeats SET last_seen = now() - interval '10 minutes' WHERE service='schedule-generator'"))
        heartbeat(s, "schedule-generator", 60)
        before = _slot(s, org["id"], j, datetime.now(UTC) - timedelta(minutes=40))   # deadline 30 min ago: watched
        reconciler.tick(s)
        assert _state(s, before) == ("missed", None)
