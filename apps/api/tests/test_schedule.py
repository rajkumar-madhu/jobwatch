from datetime import datetime, timezone

from cronsentinel import schedule


def test_human():
    assert "2:00 AM" in schedule.human("0 2 * * *")


def test_validate():
    assert schedule.validate("*/5 * * * *")
    assert not schedule.validate("99 * * * *")


def test_next_run_tz():
    base = datetime(2026, 3, 8, 6, 0, tzinfo=timezone.utc)
    n = schedule.next_run("0 2 * * *", "America/New_York", base)
    assert n.hour in (6, 7)  # DST boundary day: 2am local == 06/07 UTC


def test_next_runs_count():
    assert len(schedule.next_runs("0 * * * *", "UTC", 5)) == 5
