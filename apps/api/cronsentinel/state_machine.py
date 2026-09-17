"""Job status state machine (Design doc §4.2). Pure functions, fully unit-testable."""
from enum import StrEnum


class JobStatus(StrEnum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    RUNNING = "running"
    LATE = "late"
    MISSED = "missed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    RECOVERED = "recovered"
    PAUSED = "paused"


class Event(StrEnum):
    SCHEDULE_SET = "schedule_set"
    STARTED = "started"
    COMPLETED_OK = "completed_ok"
    COMPLETED_FAIL = "completed_fail"
    RUNTIME_EXCEEDED = "runtime_exceeded"
    GRACE_EXPIRED = "grace_expired"       # no start within grace
    GRACE_EXPIRED_2X = "grace_expired_2x"  # still nothing at 2x grace
    PAUSE = "pause"
    RESUME = "resume"


_BAD = {JobStatus.FAILED, JobStatus.TIMEOUT, JobStatus.MISSED}


def transition(current: JobStatus, event: Event) -> JobStatus:
    if current == JobStatus.PAUSED:
        return JobStatus.UNKNOWN if event == Event.RESUME else JobStatus.PAUSED
    if event == Event.PAUSE:
        return JobStatus.PAUSED
    if event == Event.SCHEDULE_SET and current == JobStatus.UNKNOWN:
        return JobStatus.HEALTHY
    if event == Event.STARTED:
        return JobStatus.RUNNING
    if event == Event.COMPLETED_OK:
        # Recovery semantics: bad -> recovered -> healthy
        return JobStatus.RECOVERED if current in _BAD or current == JobStatus.LATE else JobStatus.HEALTHY
    if event == Event.COMPLETED_FAIL:
        return JobStatus.FAILED
    if event == Event.RUNTIME_EXCEEDED and current == JobStatus.RUNNING:
        return JobStatus.TIMEOUT
    if event == Event.GRACE_EXPIRED and current in {JobStatus.HEALTHY, JobStatus.RECOVERED, JobStatus.UNKNOWN}:
        return JobStatus.LATE
    if event == Event.GRACE_EXPIRED_2X and current == JobStatus.LATE:
        return JobStatus.MISSED
    return current


def is_recovery(previous: JobStatus, new: JobStatus) -> bool:
    return new == JobStatus.RECOVERED and previous in _BAD | {JobStatus.LATE}
