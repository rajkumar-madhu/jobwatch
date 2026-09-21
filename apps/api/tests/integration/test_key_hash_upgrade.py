"""R21 — pre-R21 argon2 API keys keep working and are rehashed to SHA-256 on first use."""
import os

import pytest
from argon2 import PasswordHasher
from sqlalchemy import text

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs postgres (DATABASE_URL)")


def test_legacy_api_key_authenticates_and_is_upgraded_in_place(org):
    from fastapi.testclient import TestClient
    from cronsentinel.auth import generate_api_key
    from cronsentinel.db import system_session
    from cronsentinel.main import app
    prefix, raw = generate_api_key()
    with system_session() as s:
        kid = s.execute(text("INSERT INTO api_keys (org_id, name, prefix, key_hash, role) VALUES (:o,'legacy',:p,:h,'viewer') RETURNING id"),
                        {"o": org["id"], "p": prefix, "h": PasswordHasher().hash(raw)}).scalar()
    c = TestClient(app)
    assert c.get("/api/v1/jobs", headers={"X-API-Key": raw}).status_code == 200
    with system_session() as s:
        stored = s.execute(text("SELECT key_hash FROM api_keys WHERE id=:i"), {"i": kid}).scalar()
    assert stored.startswith("sha256$"), "legacy hash was not upgraded"
    assert c.get("/api/v1/jobs", headers={"X-API-Key": raw}).status_code == 200      # still works after upgrade
    assert c.get("/api/v1/jobs", headers={"X-API-Key": raw[:-1] + "x"}).status_code == 401


def test_wrong_secret_does_not_upgrade(org):
    from fastapi.testclient import TestClient
    from cronsentinel.auth import generate_api_key
    from cronsentinel.db import system_session
    from cronsentinel.main import app
    prefix, raw = generate_api_key()
    legacy = PasswordHasher().hash(raw)
    with system_session() as s:
        kid = s.execute(text("INSERT INTO api_keys (org_id, name, prefix, key_hash, role) VALUES (:o,'legacy',:p,:h,'viewer') RETURNING id"),
                        {"o": org["id"], "p": prefix, "h": legacy}).scalar()
    assert TestClient(app).get("/api/v1/jobs", headers={"X-API-Key": prefix + ".wrong"}).status_code == 401
    with system_session() as s:
        assert s.execute(text("SELECT key_hash FROM api_keys WHERE id=:i"), {"i": kid}).scalar() == legacy
