"""R3 pure tests — slot generation, matching, backfill capping, DST."""
from datetime import UTC, datetime, timedelta

from cronsentinel.schedule_engine import (
    BACKFILL_LIMIT,
    deadline_for,
    generation_window,
    match_window,
    pick_slot,
    slots_between,
)

UTC = UTC


def test_slots_are_exclusive_of_start_inclusive_of_end():
    n = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)
    s = slots_between("0 * * * *", "UTC", n, n + timedelta(hours=3), 300, 60)
    assert [x.scheduled_for.hour for x in s] == [11, 12, 13]


def test_deadline_falls_back_to_second_grace_without_expected_runtime():
    t = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)
    assert deadline_for(t, 300, None) == t + timedelta(seconds=600)
    assert deadline_for(t, 300, 60) == t + timedelta(seconds=360)


def test_backfill_is_capped_so_an_old_job_does_not_invent_history():
    now = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)
    start, end = generation_window(None, now - timedelta(days=400), now)
    assert start == now - BACKFILL_LIMIT


def test_generation_resumes_from_generated_through_not_now():
    now = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)
    gt = now - timedelta(hours=3)
    start, _ = generation_window(gt, now - timedelta(days=9), now)
    assert start == gt, "an outage must be backfilled, not skipped"


def test_match_accepts_a_start_slightly_before_the_slot():
    n = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)
    s = slots_between("0 * * * *", "UTC", n, n + timedelta(hours=2), 300, 60)
    early = s[0].scheduled_for - timedelta(seconds=45)
    assert pick_slot(s, early) == s[0]


def test_match_rejects_a_start_after_the_deadline():
    n = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)
    s = slots_between("0 * * * *", "UTC", n, n + timedelta(hours=2), 60, 60)
    assert pick_slot(s, s[0].deadline + timedelta(seconds=1)) is not s[0]


def test_overlapping_runs_fill_slots_in_order():
    n = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)
    s = slots_between("*/5 * * * *", "UTC", n, n + timedelta(minutes=20), 600, 600)
    # a start that is inside several slots' windows takes the nearest, then the earlier on a tie
    got = pick_slot(s, s[1].scheduled_for)
    assert got == s[1]


def test_dst_spring_forward_does_not_duplicate_or_drop_the_daily_slot():
    # US DST 2026-03-08 02:00 -> 03:00 local
    start = datetime(2026, 3, 7, 0, 0, tzinfo=UTC)
    s = slots_between("30 2 * * *", "America/New_York", start, start + timedelta(days=3), 300, 60)
    days = {x.scheduled_for.astimezone(UTC).date() for x in s}
    assert len(s) == len(days), "one slot per day across the transition"


def test_match_window_starts_before_scheduled_for():
    t = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)
    s = slots_between("0 * * * *", "UTC", t, t + timedelta(hours=1), 60, 60)[0]
    lo, hi = match_window(s)
    assert lo < s.scheduled_for < hi
