"""Four-state model (R2 ruling).

    OK       last settled slot succeeded, nothing overdue
    LATE     a slot passed its grace window and no execution has started
    FAILING  the most recent settled slot failed, timed out, or was missed
    UNKNOWN  we cannot say — no schedule, no data yet, agent offline, or paused

UNKNOWN is *not* a failure and never alerts (UNKNOWN-visibility suppression): a job we have never
heard from must not page anyone, and an agent that stopped reporting is an agent problem surfaced
on the agents page, not N job failures.

The legacy nine-value `jobs.status` is kept as a presentation detail and derived here so existing
UI, filters and status pages keep working.
"""
from enum import StrEnum


class JobState(StrEnum):
    OK = "ok"
    LATE = "late"
    FAILING = "failing"
    UNKNOWN = "unknown"


class UnknownReason(StrEnum):
    NO_SCHEDULE = "no_schedule"      # heartbeat-only job that has never reported
    NO_DATA_YET = "no_data_yet"      # schedule known, first slot not due yet
    AGENT_OFFLINE = "agent_offline"  # owning agent stopped reporting — not the job's fault
    PAUSED = "paused"


class SlotState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    LATE = "late"
    MISSED = "missed"
    SKIPPED = "skipped"   # inside a maintenance window, or job paused when the slot came due
    UNOBSERVED = "unobserved"  # R25: came due inside a monitoring gap on our side; never alerts


TERMINAL = {SlotState.SUCCEEDED, SlotState.FAILED, SlotState.MISSED, SlotState.SKIPPED, SlotState.UNOBSERVED}
BAD_SLOTS = {SlotState.FAILED, SlotState.MISSED}

# Alerting only ever fires on these transitions; UNKNOWN is deliberately absent.
ALERTABLE = {JobState.LATE, JobState.FAILING}


def derive(*, paused: bool, has_schedule: bool, ever_ran: bool, agent_offline: bool,
           open_late: bool, running: bool, last_settled: SlotState | None) -> tuple[JobState, str | None]:
    """Pure: current four-state + unknown reason. Order matters — most-specific first."""
    if paused:
        return JobState.UNKNOWN, UnknownReason.PAUSED
    if agent_offline:
        return JobState.UNKNOWN, UnknownReason.AGENT_OFFLINE
    if not has_schedule and not ever_ran:
        return JobState.UNKNOWN, UnknownReason.NO_SCHEDULE
    if not ever_ran and last_settled is None and not open_late:
        return JobState.UNKNOWN, UnknownReason.NO_DATA_YET
    if open_late:
        return JobState.LATE, None
    if last_settled in BAD_SLOTS:
        return JobState.FAILING, None
    if last_settled == SlotState.SUCCEEDED or running:
        return JobState.OK, None
    return JobState.UNKNOWN, UnknownReason.NO_DATA_YET


# Presentation only — keeps the nine-value enum the UI already renders.
def legacy_status(state: JobState, *, paused: bool, running: bool, last_settled: SlotState | None, recovered: bool) -> str:
    if paused:
        return "paused"
    if state == JobState.UNKNOWN:
        return "unknown"
    if running:
        return "running"
    if state == JobState.LATE:
        return "late"
    if state == JobState.FAILING:
        return {SlotState.MISSED: "missed", SlotState.FAILED: "failed"}.get(last_settled, "failed")
    return "recovered" if recovered else "healthy"
