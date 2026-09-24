"""R35 — every channel kind the API accepts is one the notifier can send, and vice versa.

Guards against the drift that put pagerduty/opsgenie/sms in plan_limits.features (0001) with no
sender behind them: the router's _KINDS and the notifier's _SENDERS must stay the same set, and no
plan may advertise a channel kind outside it (except the enterprise wildcard).
"""
from cronsentinel.alerting.tasks import _SENDERS
from cronsentinel.routers.alerting import _KINDS

CHANNEL_KINDS = set(_KINDS)
NON_CHANNEL_FEATURES = {"ai", "kubernetes", "all"}


def test_router_and_notifier_agree_on_channel_kinds():
    assert set(_SENDERS) == CHANNEL_KINDS


def test_seeded_plans_only_advertise_deliverable_kinds():
    from sqlalchemy import text

    from cronsentinel.db import system_session
    with system_session() as s:
        rows = s.execute(text("SELECT plan, features FROM plan_limits")).all()
    for plan, feats in rows:
        unknown = set(feats) - CHANNEL_KINDS - NON_CHANNEL_FEATURES
        assert not unknown, f"plan {plan} advertises undeliverable features {sorted(unknown)}"
