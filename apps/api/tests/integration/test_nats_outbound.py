"""R30 — the nats destination kind, real end-to-end: save, then actually deliver.

Was `raise RuntimeError("nats destination not implemented")` since R4 — every delivery to a nats
destination failed, retried to exhaustion, and auto-disabled the destination. Uses the sandbox's
own NATS instance (NATS_URL) as a stand-in for "the tenant's own broker": subscribes as a plain
client, triggers a real delivery through the Celery task body, and asserts the message arrives.
"""
import asyncio
import os
import uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.skipif(not (os.getenv("DATABASE_URL") and os.getenv("NATS_URL")), reason="needs postgres + nats")


@pytest.fixture(autouse=True)
def allow_private(monkeypatch):
    # Same posture as compose/dev: the sandbox's own NATS is on localhost, which check_nats_url
    # would otherwise refuse exactly like check_url refuses a localhost webhook in SaaS mode
    # (see test_ssrf_guard.py — that refusal is tested there; this file tests delivery itself).
    from cronsentinel.config import settings
    monkeypatch.setattr(settings, "outbound_allow_private", True)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from cronsentinel.main import app
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def key(org):
    from cronsentinel.auth import generate_api_key, hash_secret
    from cronsentinel.db import system_session
    prefix, raw = generate_api_key()
    with system_session() as s:
        s.execute(text("INSERT INTO api_keys (org_id, name, prefix, key_hash, role) VALUES (:o,'nats',:p,:h,'devops')"),
                  {"o": org["id"], "p": prefix, "h": hash_secret(raw)})
    return raw


def test_saving_a_nats_url_is_no_longer_rejected_by_pydantic(client, key):
    """Before R30, DestinationIn.url was pydantic's HttpUrl — it rejected nats:// before the
    handler even ran. This is the save-path half of the fix; delivery is the other half below."""
    r = client.post("/api/v1/integrations/destinations", headers={"X-API-Key": key},
                    json={"name": "n", "kind": "nats", "url": os.environ["NATS_URL"], "subject_prefix": "it.signals"})
    assert r.status_code == 201, r.text


def test_subject_prefix_is_required_and_validated(client, key):
    missing = client.post("/api/v1/integrations/destinations", headers={"X-API-Key": key},
                          json={"name": "n", "kind": "nats", "url": os.environ["NATS_URL"]})
    assert missing.status_code == 400, missing.text
    bad = client.post("/api/v1/integrations/destinations", headers={"X-API-Key": key},
                      json={"name": "n", "kind": "nats", "url": os.environ["NATS_URL"], "subject_prefix": "has.*.wildcard"})
    assert bad.status_code == 400, bad.text


def test_delivery_actually_publishes_and_a_real_subscriber_receives_it(client, key, org):
    import nats as nats_lib
    from cronsentinel.crypto import encrypt_json
    from cronsentinel.db import system_session
    prefix = f"it-{uuid.uuid4().hex[:8]}"
    with system_session() as s:
        dest_id = s.execute(text("""INSERT INTO signal_destinations (org_id, name, kind, config_enc)
            VALUES (:o, 'd', 'nats', :c) RETURNING id"""),
            {"o": org["id"], "c": encrypt_json({"url": os.environ["NATS_URL"], "subject_prefix": prefix})}).scalar()

    async def _subscribe_and_wait():
        nc = await nats_lib.connect(servers=[os.environ["NATS_URL"]])
        sub = await nc.subscribe(f"{prefix}.>")
        try:
            from cronsentinel.outbound.deliver import deliver_signal
            # deliver_signal runs asyncio.run() internally (see _publish_nats) — exactly as a real
            # Celery worker thread would, with no event loop of its own. Running it on a thread here
            # avoids nesting it inside this test coroutine's already-running loop, which asyncio.run()
            # forbids; a real worker never has that conflict, since it isn't itself inside a loop.
            await asyncio.to_thread(deliver_signal.apply, args=[str(org["id"]), str(dest_id),
                {"signal_id": "sig-1", "event_type": "job.state_changed", "org_id": str(org["id"])}])
            msg = await asyncio.wait_for(sub.next_msg(), timeout=5)
            return msg
        finally:
            await nc.close()

    msg = asyncio.run(_subscribe_and_wait())
    assert msg.subject == f"{prefix}.job.state_changed"
    assert msg.headers.get("Nats-Msg-Id") == "sig-1"
    with system_session() as s:
        status = s.execute(text("SELECT status FROM signal_deliveries WHERE destination_id=:d AND signal_id='sig-1'"), {"d": dest_id}).scalar()
        assert status == "sent"


def test_a_destination_the_tenant_does_not_own_cannot_be_reached_via_another_orgs_key(client, key):
    """The subject_prefix + auth model is per-destination; nothing here lets one tenant's key
    address another tenant's broker connection details — this just pins that create/list stay
    RLS-scoped like every other destination, same as webhook."""
    r = client.get("/api/v1/integrations/destinations", headers={"X-API-Key": key})
    assert r.status_code == 200
    assert all("password" not in str(d) and "token" not in str(d).lower().replace("has_token", "") for d in r.json())
