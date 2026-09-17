"""R2 pure tests — four-state derivation and UNKNOWN suppression."""
from cronsentinel.alerting.rules import suppressed_unknown
from cronsentinel.states import (
    JobState,
    SlotState,
    UnknownReason,
    derive,
    legacy_status,
)


def d(**kw):
    base = dict(paused=False, has_schedule=True, ever_ran=True, agent_offline=False,
                open_late=False, running=False, last_settled=SlotState.SUCCEEDED)
    return derive(**{**base, **kw})


def test_healthy_job_is_ok():
    assert d() == (JobState.OK, None)


def test_paused_is_unknown_not_ok():
    assert d(paused=True) == (JobState.UNKNOWN, UnknownReason.PAUSED)


def test_agent_offline_beats_a_late_slot():
    """The key R2 fix: a dead agent must not turn into N failing jobs."""
    assert d(agent_offline=True, open_late=True, last_settled=SlotState.MISSED) == (JobState.UNKNOWN, UnknownReason.AGENT_OFFLINE)


def test_never_ran_is_unknown_not_failing():
    assert d(ever_ran=False, last_settled=None) == (JobState.UNKNOWN, UnknownReason.NO_DATA_YET)


def test_heartbeat_job_with_no_schedule_and_no_data():
    assert d(has_schedule=False, ever_ran=False, last_settled=None) == (JobState.UNKNOWN, UnknownReason.NO_SCHEDULE)


def test_missed_slot_is_failing():
    assert d(last_settled=SlotState.MISSED)[0] == JobState.FAILING


def test_open_late_slot_beats_a_previously_successful_run():
    assert d(open_late=True)[0] == JobState.LATE


def test_running_counts_as_ok_when_nothing_is_overdue():
    assert d(running=True, last_settled=None, ever_ran=True)[0] == JobState.OK


def test_legacy_status_preserves_recovered():
    assert legacy_status(JobState.OK, paused=False, running=False, last_settled=SlotState.SUCCEEDED, recovered=True) == "recovered"
    assert legacy_status(JobState.FAILING, paused=False, running=False, last_settled=SlotState.MISSED, recovered=False) == "missed"


def test_unknown_never_alerts():
    assert suppressed_unknown("unknown", "agent_offline") == "unknown:agent_offline"
    assert suppressed_unknown("failing", None) is None
    assert suppressed_unknown("late", None) is None
