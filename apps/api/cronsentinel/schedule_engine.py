"""R3 — independent expected-run engine.

Materialises the slots a schedule should produce, ahead of time, so late/missed detection
does not depend on a worker being alive at the moment a run was due. Pure functions here;
I/O lives in workers/schedule_generator.py and workers/reconciler.py.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta

from croniter import croniter

from .schedule import next_runs

HORIZON = timedelta(hours=6)     # how far ahead slots are materialised
BACKFILL_LIMIT = timedelta(days=2)  # cold start / long outage: never invent more than this much history
MAX_SLOTS_PER_JOB = 2000         # guard against */1 * * * * over a long backfill


@dataclass(frozen=True)
class Slot:
    scheduled_for: datetime
    grace_until: datetime
    deadline: datetime


def deadline_for(scheduled_for: datetime, grace_s: int, expected_runtime_s: int | None) -> datetime:
    """A slot is only declared missed after grace; it is declared timed out after grace + runtime.
    With no expected runtime we fall back to a second grace period rather than waiting forever."""
    return scheduled_for + timedelta(seconds=grace_s + (expected_runtime_s or grace_s))


def slots_between(expr: str, tz: str, start: datetime, end: datetime, grace_s: int,
                  expected_runtime_s: int | None, limit: int = MAX_SLOTS_PER_JOB) -> list[Slot]:
    """Slots strictly after `start`, up to and including `end`. DST handled by croniter in the job's tz."""
    if start >= end:
        return []
    out: list[Slot] = []
    for ts in next_runs(expr, tz, n=limit, after=start):
        if ts > end:
            break
        out.append(Slot(ts, ts + timedelta(seconds=grace_s), deadline_for(ts, grace_s, expected_runtime_s)))
    return out


def generation_window(generated_through: datetime | None, created_at: datetime, now: datetime) -> tuple[datetime, datetime]:
    """Where to resume materialising. A job that has never generated starts from its creation time,
    capped by BACKFILL_LIMIT so a job created a year ago does not produce a year of missed runs."""
    floor = now - BACKFILL_LIMIT
    start = generated_through or created_at
    return max(start, floor), now + HORIZON


def match_window(slot: Slot) -> tuple[datetime, datetime]:
    """An execution belongs to a slot if it started inside this window. Starts slightly before the
    slot are accepted (clock skew, agent firing a second early)."""
    return slot.scheduled_for - timedelta(seconds=90), slot.deadline


def pick_slot(slots: list[Slot], started_at: datetime) -> Slot | None:
    """Nearest slot whose match window contains the start; ties go to the earlier slot so that a
    backlog of overlapping runs fills slots in order rather than all landing on the newest."""
    cands = [s for s in slots if match_window(s)[0] <= started_at <= match_window(s)[1]]
    if not cands:
        return None
    return min(cands, key=lambda s: (abs((started_at - s.scheduled_for).total_seconds()), s.scheduled_for))


def is_valid(expr: str) -> bool:
    return croniter.is_valid(expr)
