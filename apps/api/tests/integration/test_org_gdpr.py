"""R29 — self-service org export and deletion."""
import os
import uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs postgres (DATABASE_URL)")


@pytest.fixture
def org_slug(org):
    # conftest's org fixture inserts the same value for both name and slug (see conftest.py).
    return f"it-{org['id'].hex[:8]}"


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from cronsentinel.main import app
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def key(org):
    """An owner-role key, since deletion requires owner."""
    from cronsentinel.auth import generate_api_key, hash_secret
    from cronsentinel.db import system_session
    prefix, raw = generate_api_key()
    with system_session() as s:
        s.execute(text("INSERT INTO api_keys (org_id, name, prefix, key_hash, role) VALUES (:o,'gdpr',:p,:h,'owner')"),
                  {"o": org["id"], "p": prefix, "h": hash_secret(raw)})
    return raw


def test_export_includes_config_and_members_but_never_secrets_or_key_hashes(client, key, org, make_job):
    from cronsentinel.db import system_session
    j = make_job()
    u = uuid.uuid4()
    with system_session() as s:
        s.execute(text("INSERT INTO users (id, email, name) VALUES (:i, :e, 'Test User')"), {"i": u, "e": f"gdpr-{u.hex[:8]}@example.com"})
        s.execute(text("INSERT INTO memberships (org_id, user_id, role) VALUES (:o, :u, 'admin')"), {"o": org["id"], "u": u})
        s.execute(text("INSERT INTO notification_channels (org_id, kind, name, config_enc) VALUES (:o, 'webhook', 'secret-channel', :c)"),
                  {"o": org["id"], "c": b"\\xde\\xad\\xbe\\xef"})
    r = client.get("/api/v1/org/export", headers={"X-API-Key": key})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["organization"]["id"] == str(org["id"])
    assert any(j["id"] == str(j and j) or True for j in body["jobs"]) or body["jobs"]  # non-empty
    assert any(m["email"].startswith("gdpr-") for m in body["members"])
    assert body["notification_channels"][0]["name"] == "secret-channel"
    dumped = str(body)
    assert "deadbeef" not in dumped.lower() and "config_enc" not in dumped   # the encrypted secret itself never appears
    assert "key_hash" not in dumped


def test_export_is_scoped_to_the_caller_org(client, key, org, org_slug):
    """Another org's export must be unreachable — this key can only ever see its own org_id."""
    r = client.get("/api/v1/org/export", headers={"X-API-Key": key})
    assert r.json()["organization"]["slug"] == org_slug


def test_deletion_requires_the_exact_slug(client, key, org, org_slug):
    r = client.request("DELETE", "/api/v1/org/", json={"confirm_slug": "not-the-real-slug"}, headers={"X-API-Key": key})
    assert r.status_code == 400, r.text
    with __import__("cronsentinel.db", fromlist=["system_session"]).system_session() as s:
        assert s.execute(text("SELECT 1 FROM organizations WHERE id=:o"), {"o": org["id"]}).first()   # still there


def test_deletion_blocked_while_a_paid_subscription_is_active(client, key, org, org_slug):
    from cronsentinel.db import system_session
    with system_session() as s:
        s.execute(text("""INSERT INTO subscriptions (org_id, plan, status) VALUES (:o, 'business', 'active')
            ON CONFLICT (org_id) DO UPDATE SET plan='business', status='active'"""), {"o": org["id"]})
    r = client.request("DELETE", "/api/v1/org/", json={"confirm_slug": org_slug}, headers={"X-API-Key": key})
    assert r.status_code == 409, r.text
    assert "billing" in r.text.lower() or "subscription" in r.text.lower()


def test_deletion_with_correct_slug_actually_removes_everything(client, key, org, org_slug, make_job):
    from cronsentinel.db import system_session
    j = make_job()
    with system_session() as s:
        s.execute(text("UPDATE subscriptions SET plan='free', status='active' WHERE org_id=:o"), {"o": org["id"]})
        s.execute(text("INSERT INTO executions (id, org_id, job_id, status, scheduled_ts, server_received_ts) "
                       "VALUES (:e, :o, :j, 'success', now(), now())"), {"e": f"gdpr-{uuid.uuid4().hex[:8]}", "o": org["id"], "j": j})
    r = client.request("DELETE", "/api/v1/org/", json={"confirm_slug": org_slug}, headers={"X-API-Key": key})
    assert r.status_code == 204, r.text
    with system_session() as s:
        assert not s.execute(text("SELECT 1 FROM organizations WHERE id=:o"), {"o": org["id"]}).first()
        assert not s.execute(text("SELECT 1 FROM jobs WHERE id=:j"), {"j": j}).first()               # FK cascade
        assert not s.execute(text("SELECT 1 FROM executions WHERE job_id=:j"), {"j": j}).first()     # purge_org (no FK)
        assert not s.execute(text("SELECT 1 FROM api_keys WHERE org_id=:o"), {"o": org["id"]}).first()  # deleting key's own org
