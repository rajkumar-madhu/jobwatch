from cronsentinel.state_machine import Event as E
from cronsentinel.state_machine import JobStatus as S
from cronsentinel.state_machine import transition as t


def test_happy_path():
    assert t(S.UNKNOWN, E.SCHEDULE_SET) == S.HEALTHY
    assert t(S.HEALTHY, E.STARTED) == S.RUNNING
    assert t(S.RUNNING, E.COMPLETED_OK) == S.HEALTHY


def test_failure_and_recovery():
    assert t(S.RUNNING, E.COMPLETED_FAIL) == S.FAILED
    assert t(S.FAILED, E.STARTED) == S.RUNNING
    # recovery requires prior bad state memory — modeled via RECOVERED on next success from bad
    assert t(S.FAILED, E.COMPLETED_OK) == S.RECOVERED
    assert t(S.RECOVERED, E.COMPLETED_OK) == S.HEALTHY


def test_late_missed():
    assert t(S.HEALTHY, E.GRACE_EXPIRED) == S.LATE
    assert t(S.LATE, E.GRACE_EXPIRED_2X) == S.MISSED
    assert t(S.MISSED, E.COMPLETED_OK) == S.RECOVERED


def test_timeout_only_when_running():
    assert t(S.RUNNING, E.RUNTIME_EXCEEDED) == S.TIMEOUT
    assert t(S.HEALTHY, E.RUNTIME_EXCEEDED) == S.HEALTHY


def test_pause_resume():
    assert t(S.FAILED, E.PAUSE) == S.PAUSED
    assert t(S.PAUSED, E.STARTED) == S.PAUSED
    assert t(S.PAUSED, E.RESUME) == S.UNKNOWN
