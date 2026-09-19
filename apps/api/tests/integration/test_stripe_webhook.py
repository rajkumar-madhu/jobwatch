"""R7 — Stripe webhook: signature verification and the plan state machine, against the real DB.

No Stripe account needed — signatures are HMAC over a payload we control, which is exactly what
Stripe sends. This is the part worth testing: a wrong verifier either lets anyone set an org's
plan, or silently drops legitimate events.
"""
import hashlib
import hmac
import json
import os
import time
import uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs postgres")
SECRET = "whsec_test_secret"


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    from cronsentinel.config import settings
    monkeypatch.setattr(settings, "stripe_webhook_secret", SECRET, raising=False)
    from cronsentinel.main import app
    return TestClient(app)


def sign(payload: bytes, secret=SECRET, ts=None, extra_v1=False):
    ts = ts or int(time.time())
    v1 = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    header = f"t={ts},v1={v1}"
    if extra_v1 == "first":   # real signature first, stale rotation signature after it
        header = f"t={ts},v1={v1},v1={'0' * 64}"
    elif extra_v1:            # stale first, real one after
        header = f"t={ts},v1={'0' * 64},v1={v1}"
    return header


def post(client, body: dict, **kw):
    raw = json.dumps(body).encode()
    return client.post("/api/v1/billing/webhook", content=raw,
                       headers={"Stripe-Signature": sign(raw, **kw), "Content-Type": "application/json"})


@pytest.fixture
def billing_org(org):
    from cronsentinel.db import system_session
    cust = f"cus_{uuid.uuid4().hex[:12]}"
    with system_session() as s:
        s.execute(text("""INSERT INTO subscriptions (org_id, plan, status, stripe_customer_id)
            VALUES (:o, 'free', 'active', :c)"""), {"o": org["id"], "c": cust})
        s.execute(text("UPDATE plan_limits SET stripe_price_id='price_pro' WHERE plan='pro'"))
    return {**org, "customer": cust}


def _plan(org_id):
    from cronsentinel.db import system_session
    with system_session() as s:
        return s.execute(text("SELECT o.plan::text, s.status FROM organizations o JOIN subscriptions s ON s.org_id=o.id WHERE o.id=:o"),
                         {"o": org_id}).first()


def test_bad_signature_is_rejected(client, billing_org):
    raw = json.dumps({"id": "evt_x", "type": "checkout.session.completed", "data": {"object": {}}}).encode()
    r = client.post("/api/v1/billing/webhook", content=raw, headers={"Stripe-Signature": sign(raw, secret="wrong")})
    assert r.status_code == 400


def test_missing_signature_is_rejected(client):
    r = client.post("/api/v1/billing/webhook", content=b"{}")
    assert r.status_code == 400


def test_replayed_old_timestamp_is_rejected(client, billing_org):
    body = {"id": f"evt_{uuid.uuid4().hex}", "type": "checkout.session.completed", "data": {"object": {}}}
    r = post(client, body, ts=int(time.time()) - 3600)
    assert r.status_code == 400, "a captured webhook must not be replayable an hour later"


def test_rotated_secret_second_v1_is_accepted(client, billing_org):
    """During a secret rotation Stripe signs with both; accepting only the last v1 breaks one half."""
    body = {"id": f"evt_{uuid.uuid4().hex}", "type": "checkout.session.completed",
            "data": {"object": {"subscription": "sub_1", "metadata": {"plan": "pro", "org_id": str(billing_org["id"])}}}}
    assert post(client, body, extra_v1=True).status_code == 200
    assert _plan(billing_org["id"]).plan == "pro"
    body2 = {**body, "id": f"evt_{uuid.uuid4().hex}"}
    assert post(client, body2, extra_v1="first").status_code == 200, "order of v1 values must not matter"


def test_checkout_completed_upgrades_the_org(client, billing_org):
    body = {"id": f"evt_{uuid.uuid4().hex}", "type": "checkout.session.completed",
            "data": {"object": {"subscription": "sub_1", "metadata": {"plan": "pro", "org_id": str(billing_org["id"])}}}}
    assert post(client, body).status_code == 200
    row = _plan(billing_org["id"])
    assert row.plan == "pro" and row.status == "active"


def test_duplicate_event_id_is_ignored(client, billing_org):
    eid = f"evt_{uuid.uuid4().hex}"
    body = {"id": eid, "type": "checkout.session.completed",
            "data": {"object": {"subscription": "sub_1", "metadata": {"plan": "pro", "org_id": str(billing_org["id"])}}}}
    assert post(client, body).status_code == 200
    r2 = post(client, body)
    assert r2.status_code == 200 and r2.json().get("duplicate") is True


def test_subscription_deleted_downgrades_to_free(client, billing_org):
    up = {"id": f"evt_{uuid.uuid4().hex}", "type": "checkout.session.completed",
          "data": {"object": {"subscription": "sub_1", "metadata": {"plan": "pro", "org_id": str(billing_org["id"])}}}}
    post(client, up)
    body = {"id": f"evt_{uuid.uuid4().hex}", "type": "customer.subscription.deleted",
            "data": {"object": {"customer": billing_org["customer"], "status": "canceled",
                                "items": {"data": [{"price": {"id": "price_pro"}}]},
                                "current_period_end": int(time.time())}}}
    assert post(client, body).status_code == 200
    assert _plan(billing_org["id"]).plan == "free"


def test_payment_failed_marks_past_due(client, billing_org):
    body = {"id": f"evt_{uuid.uuid4().hex}", "type": "invoice.payment_failed",
            "data": {"object": {"customer": billing_org["customer"]}}}
    assert post(client, body).status_code == 200
    assert _plan(billing_org["id"]).status == "past_due"


def test_malformed_event_does_not_record_the_id(client, billing_org):
    """A crash must leave the event unrecorded so Stripe's retry can succeed, not be deduped away."""
    from cronsentinel.db import system_session
    eid = f"evt_{uuid.uuid4().hex}"
    body = {"id": eid, "type": "checkout.session.completed", "data": {"object": {}}}  # no metadata
    assert post(client, body).status_code == 400
    with system_session() as s:
        assert s.execute(text("SELECT count(*) FROM stripe_events WHERE id=:i"), {"i": eid}).scalar() == 0


def test_unknown_org_in_metadata_does_not_upgrade_anyone(client, billing_org):
    body = {"id": f"evt_{uuid.uuid4().hex}", "type": "checkout.session.completed",
            "data": {"object": {"subscription": "s", "metadata": {"plan": "pro", "org_id": str(uuid.uuid4())}}}}
    post(client, body)
    assert _plan(billing_org["id"]).plan == "free"
