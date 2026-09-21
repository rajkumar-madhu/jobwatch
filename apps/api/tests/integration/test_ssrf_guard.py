"""R18 — the SSRF guard through the real API and delivery paths, in SaaS mode."""
import os
import uuid

import pytest
from sqlalchemy import text

from cronsentinel.config import settings

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs postgres (DATABASE_URL)")


@pytest.fixture(autouse=True)
def saas(monkeypatch):
    monkeypatch.setattr(settings, "outbound_allow_private", False)


@pytest.fixture
def api(org):
    from fastapi.testclient import TestClient
    from cronsentinel.auth import generate_api_key, hash_secret
    from cronsentinel.db import system_session
    from cronsentinel.main import app
    with system_session() as s:
        s.execute(text("UPDATE organizations SET plan='team' WHERE id=:o"), {"o": org["id"]})
        prefix, raw = generate_api_key()
        s.execute(text("INSERT INTO api_keys (org_id, name, prefix, key_hash, role) VALUES (:o,'k',:p,:h,'admin')"),
                  {"o": org["id"], "p": prefix, "h": hash_secret(raw)})
    c = TestClient(app, raise_server_exceptions=False)
    c.headers["X-API-Key"] = raw
    return c


@pytest.mark.parametrize("kind,cfg", [
    ("webhook", {"url": "http://169.254.169.254/latest/meta-data/iam/security-credentials/"}),
    ("webhook", {"url": "http://10.0.0.12:8080/admin"}),
    ("slack", {"webhook_url": "http://127.0.0.1:6379/"}),
    ("discord", {"webhook_url": "http://[::ffff:169.254.169.254]/"}),
])
def test_channel_with_internal_url_is_refused(api, kind, cfg):
    r = api.post("/api/v1/alerts/channels", json={"kind": kind, "name": f"x-{uuid.uuid4().hex[:6]}", "config": cfg})
    assert r.status_code == 400, r.text


@pytest.mark.parametrize("body", [
    {"url": "http://169.254.169.254/computeMetadata/v1/"},
    {"url": "https://hooks.example.com/x", "headers": {"Metadata-Flavor": "Google"}},
    {"url": "https://hooks.example.com/x", "headers": {"Host": "169.254.169.254"}},
])
def test_destination_with_internal_url_or_metadata_header_is_refused(api, body):
    r = api.post("/api/v1/integrations/destinations", json={"name": "d", "event_types": ["job.failed"], **body})
    assert r.status_code == 400, r.text


def _raw_destination(org, url):
    """A destination as it exists in a pre-R18 database: saved without any validation."""
    from cronsentinel.crypto import encrypt_json
    from cronsentinel.db import system_session
    with system_session() as s:
        return s.execute(text("""INSERT INTO signal_destinations (org_id, name, kind, config_enc, event_types, enabled)
            VALUES (:o, 'legacy', 'webhook', :c, ARRAY['job.failed'], true) RETURNING id"""),
            {"o": org["id"], "c": encrypt_json({"url": url})}).scalar()


def test_send_test_on_a_legacy_internal_destination_reveals_nothing(api, org):
    d = _raw_destination(org, "http://127.0.0.1:6379/")
    r = api.post(f"/api/v1/integrations/destinations/{d}/test")
    assert r.status_code == 200
    body = r.json()
    assert body == {"ok": False, "error": "destination not allowed"}, body
    assert "127.0.0.1" not in r.text and "6379" not in r.text and "refused" not in r.text.lower()


def test_delivery_to_a_legacy_internal_destination_is_blocked_and_logged_safely(org):
    from cronsentinel.db import system_session
    from cronsentinel.outbound import schema
    from cronsentinel.outbound.deliver import deliver_signal
    d = _raw_destination(org, "http://169.254.169.254/latest/meta-data/")
    env = schema.from_jobstatus({"org_id": str(org["id"]), "job_id": str(uuid.uuid4()), "new_state": "failing", "prev_state": "ok",
                                 "new_status": "failed", "prev_status": "healthy"}, {"name": "ssrf"}).envelope()
    deliver_signal.apply(args=(str(org["id"]), str(d), env))   # eager, no broker; retries recorded as failures
    with system_session() as s:
        row = s.execute(text("SELECT status::text, last_error FROM signal_deliveries WHERE destination_id=:d"), {"d": d}).first()
    assert row is not None and row.status in ("failed", "dead")
    assert row.last_error == "destination not allowed"
    assert "169.254" not in row.last_error
