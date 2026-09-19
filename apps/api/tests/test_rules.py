from datetime import UTC, datetime, timedelta

from cronsentinel.alerting import rules as R

J = R.JobCtx(id="j1", name="backup", tags=["prod", "db"], environment_id="e1", workspace_id="w1", team_id=None)


def test_scope():
    assert R.scope_matches(R.Rule("r", "failed"), J)
    assert R.scope_matches(R.Rule("r", "failed", scope={"tags": ["db"]}), J)
    assert not R.scope_matches(R.Rule("r", "failed", scope={"tags": ["staging"]}), J)
    assert R.scope_matches(R.Rule("r", "failed", scope={"job_ids": ["j1"]}), J)


def test_conditions():
    assert R.condition_fires(R.Rule("r", "failed"), "running", "failed", J)
    assert not R.condition_fires(R.Rule("r", "failed"), "running", "healthy", J)
    assert R.condition_fires(R.Rule("r", "runtime_exceeded"), "running", "timeout", J)
    j = R.JobCtx(**{**J.__dict__, "consecutive_failures": 3})
    assert R.condition_fires(R.Rule("r", "consecutive_failures", params={"count": 3}), "running", "failed", j)
    assert not R.condition_fires(R.Rule("r", "consecutive_failures", params={"count": 5}), "running", "failed", j)
    j2 = R.JobCtx(**{**J.__dict__, "success_rate": 95.0, "sla_target": 99.0})
    assert R.condition_fires(R.Rule("r", "sla_breach"), "x", "y", j2)


def test_business_hours():
    bh = {"tz": "Asia/Kolkata", "days": [1, 2, 3, 4, 5], "start": "09:00", "end": "18:00"}
    assert R.in_business_hours(bh, datetime(2026, 9, 14, 5, 0, tzinfo=UTC))   # Mon 10:30 IST
    assert not R.in_business_hours(bh, datetime(2026, 9, 13, 5, 0, tzinfo=UTC))  # Sun
    assert not R.in_business_hours(bh, datetime(2026, 9, 14, 15, 0, tzinfo=UTC))  # 20:30 IST
    assert R.in_business_hours(None, datetime.now(UTC))


def test_flapping_and_repeat():
    now = datetime.now(UTC)
    ts = [now - timedelta(minutes=m) for m in (1, 3, 5)]
    assert R.is_flapping(ts, now)
    assert not R.is_flapping(ts[:2], now)
    assert R.should_repeat(None, None, now)
    assert not R.should_repeat(now - timedelta(minutes=5), None, now)
    assert R.should_repeat(now - timedelta(minutes=31), 1800, now)
    assert not R.should_repeat(now - timedelta(minutes=10), 1800, now)
