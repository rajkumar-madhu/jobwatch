"""R8 — CSRF is actually enforced on cookie-authenticated writes, and only on those."""
import os
import uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.skipif(not os.getenv("REDIS_URL"), reason="needs redis + postgres")


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from cronsentinel.main import app
    return TestClient(app)


@pytest.fixture
def member(org):
    """A signed-in user with a membership, plus their session cookie and CSRF token."""
    from cronsentinel.csrf import issue
    from cronsentinel.db import system_session
    from cronsentinel.routers.auth import sign_session
    sub = f"sub-{uuid.uuid4().hex[:8]}"
    with system_session() as s:
        uid = s.execute(text("INSERT INTO users (keycloak_sub, email, name) VALUES (:s, :e, 'T') RETURNING id"),
                        {"s": sub, "e": f"{sub}@t.test"}).scalar()
        s.execute(text("INSERT INTO memberships (user_id, org_id, role) VALUES (:u, :o, 'owner')"), {"u": uid, "o": org["id"]})
    yield {"cookie": sign_session({"sub": sub, "uid": str(uid), "email": f"{sub}@t.test", "org_id": str(org["id"])}),
           "csrf": issue(sub), "sub": sub, "org": org}
    with system_session() as s:
        s.execute(text("DELETE FROM users WHERE id=:u"), {"u": uid})


BODY = {"name": "csrf-test-job", "schedule_expr": "*/5 * * * *", "grace_s": 60}


def _post(client, member, csrf=None):
    return client.post("/api/v1/jobs", json={**BODY, "workspace_id": str(_ws(member["org"]))},
                       cookies={"cs_session": member["cookie"]},
                       headers={"X-CSRF-Token": csrf} if csrf else {})


def _ws(org):
    from cronsentinel.db import system_session
    with system_session() as s:
        return s.execute(text("SELECT id FROM workspaces WHERE org_id=:o LIMIT 1"), {"o": org["id"]}).scalar()


def test_cookie_write_without_csrf_is_forbidden(client, member):
    r = _post(client, member)
    assert r.status_code == 403 and "CSRF" in r.text


def test_cookie_write_with_valid_csrf_succeeds(client, member):
    r = _post(client, member, csrf=member["csrf"])
    assert r.status_code in (200, 201), r.text


def test_csrf_from_another_session_is_rejected(client, member):
    from cronsentinel.csrf import issue
    r = _post(client, member, csrf=issue("someone-else"))
    assert r.status_code == 403


def test_cookie_reads_do_not_need_csrf(client, member):
    r = client.get("/api/v1/jobs", cookies={"cs_session": member["cookie"]})
    assert r.status_code == 200


def test_api_key_writes_do_not_need_csrf(client, org):
    """API keys are not sent automatically by browsers, so forcing CSRF on them would only break
    every script without adding safety."""
    from cronsentinel.auth import generate_api_key, hash_secret
    from cronsentinel.db import system_session
    prefix, raw = generate_api_key()
    with system_session() as s:
        s.execute(text("INSERT INTO api_keys (org_id, name, prefix, key_hash, role) VALUES (:o,'t',:p,:h,'owner')"),
                  {"o": org["id"], "p": prefix, "h": hash_secret(raw)})
    r = client.post("/api/v1/jobs", json={**BODY, "workspace_id": str(_ws(org))}, headers={"X-API-Key": raw})
    assert r.status_code in (200, 201), r.text


def test_session_endpoint_hands_out_a_usable_token(client, member):
    r = client.get("/auth/session", cookies={"cs_session": member["cookie"]})
    assert r.status_code == 200
    tok = r.json().get("csrf_token")
    assert tok and _post(client, member, csrf=tok).status_code in (200, 201)
