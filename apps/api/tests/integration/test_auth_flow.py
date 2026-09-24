"""R7 — the OIDC callback end-to-end against a fake provider (real HTTP, real Redis, real DB).

A real Keycloak is not required to prove the thing that matters: that the callback verifies the
id_token it is handed, binds it to the login attempt, and only then mints a session cookie.
"""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwk, jwt
from sqlalchemy import text

pytestmark = pytest.mark.skipif(not os.getenv("REDIS_URL"), reason="needs redis")

PORT = 18077
ISS = f"http://127.0.0.1:{PORT}/realms/jobwatch"
CLIENT = "jobwatch-web"
_state = {"id_token": None}


def _keypair():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()
    pub_pem = priv.public_key().public_bytes(serialization.Encoding.PEM,
                                             serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    d = jwk.construct(pub_pem, algorithm="RS256").to_dict()
    d = {k: (v.decode() if isinstance(v, bytes) else v) for k, v in d.items()}
    d["kid"] = "k1"
    return pem, {"keys": [d]}


PEM, JWKS = _keypair()


class Handler(BaseHTTPRequestHandler):
    def _json(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        if self.path.endswith("/.well-known/openid-configuration"):
            return self._json({"issuer": ISS, "authorization_endpoint": f"{ISS}/protocol/openid-connect/auth",
                               "token_endpoint": f"{ISS}/protocol/openid-connect/token",
                               "jwks_uri": f"{ISS}/protocol/openid-connect/certs"})
        if self.path.endswith("/certs"):
            return self._json(JWKS)
        self.send_response(404); self.end_headers()

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        return self._json({"access_token": "at", "id_token": _state["id_token"], "token_type": "Bearer"})

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module", autouse=True)
def provider():
    srv = HTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)
    yield
    srv.shutdown()


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    from cronsentinel import oidc
    from cronsentinel.config import settings
    from cronsentinel.routers import auth as authr
    monkeypatch.setattr(settings, "keycloak_issuer", ISS, raising=False)
    monkeypatch.setattr(settings, "keycloak_client_id", CLIENT, raising=False)
    monkeypatch.setattr(settings, "keycloak_client_secret", "shh", raising=False)
    authr._oidc.clear(); oidc._jwks_cache.clear()
    from cronsentinel.main import app
    return TestClient(app, follow_redirects=False)


def _login(client):
    r = client.get("/auth/login", params={"next": "/jobs"})
    assert r.status_code in (302, 307)
    q = parse_qs(urlparse(r.headers["location"]).query)
    return q["state"][0], q["nonce"][0]


def _id_token(nonce, *, access_token="at", **over):
    # R35: access_token defaults to "at" — the fixed value Handler.do_POST always returns — so
    # every token minted here carries a real, matching at_hash, the way a real Keycloak always
    # does. Before this round the fixture minted at_hash-free tokens, so this whole file passed
    # against the exact code path that 401ed on every real login; pass access_token=None to mint
    # the old at_hash-free shape for a specific test.
    now = int(time.time())
    claims = {"iss": ISS, "aud": CLIENT, "sub": "kc-sub-1", "email": "u@example.test", "name": "U",
              "iat": now, "exp": now + 300, "nonce": nonce, **over}
    return jwt.encode(claims, PEM, algorithm="RS256", headers={"kid": "k1"}, access_token=access_token)


def test_login_issues_state_and_nonce(client):
    state, nonce = _login(client)
    assert state and nonce and state != nonce


def test_callback_verifies_token_and_sets_session(client):
    from cronsentinel.db import system_session
    state, nonce = _login(client)
    _state["id_token"] = _id_token(nonce)
    r = client.get("/auth/callback", params={"code": "c", "state": state})
    assert r.status_code in (302, 307), r.text
    assert "cs_session" in r.cookies or any("cs_session" in h for h in r.headers.get_list("set-cookie"))
    with system_session() as s:
        row = s.execute(text("SELECT keycloak_sub FROM users WHERE email='u@example.test'")).first()
        assert row and row.keycloak_sub == "kc-sub-1"
        s.execute(text("DELETE FROM users WHERE email='u@example.test'"))


def test_replayed_state_is_rejected(client):
    """State is single-use: the same callback twice must not mint a second session."""
    state, nonce = _login(client)
    _state["id_token"] = _id_token(nonce)
    assert client.get("/auth/callback", params={"code": "c", "state": state}).status_code in (302, 307)
    r2 = client.get("/auth/callback", params={"code": "c", "state": state})
    assert r2.status_code == 400
    from cronsentinel.db import system_session
    with system_session() as s:
        s.execute(text("DELETE FROM users WHERE email='u@example.test'"))


def test_token_with_wrong_nonce_is_rejected(client):
    state, _ = _login(client)
    _state["id_token"] = _id_token("attacker-chosen")
    r = client.get("/auth/callback", params={"code": "c", "state": state})
    assert r.status_code == 401 and "id_token" in r.text


def test_token_signed_by_another_key_is_rejected(client):
    other_pem, _ = _keypair()
    state, nonce = _login(client)
    now = int(time.time())
    _state["id_token"] = jwt.encode({"iss": ISS, "aud": CLIENT, "sub": "x", "email": "e@e.test",
                                     "iat": now, "exp": now + 300, "nonce": nonce}, other_pem,
                                    algorithm="RS256", headers={"kid": "k1"})
    assert client.get("/auth/callback", params={"code": "c", "state": state}).status_code == 401


def test_token_without_email_is_rejected_not_500(client):
    state, nonce = _login(client)
    _state["id_token"] = _id_token(nonce, email=None)
    r = client.get("/auth/callback", params={"code": "c", "state": state})
    assert r.status_code == 401 and "email" in r.text


def test_unknown_state_is_rejected(client):
    assert client.get("/auth/callback", params={"code": "c", "state": "never-issued"}).status_code == 400


def test_callback_rejects_id_token_whose_at_hash_does_not_match_the_returned_access_token(client):
    """R35: this is the actual production bug, exercised through the real route. Before the fix,
    routers/auth.py never passed access_token to verify_id_token at all, so *every* token here
    (which all carry at_hash by default now — see _id_token) would 401 regardless of whether
    at_hash was even correct. This test specifically proves the wiring catches a genuine mismatch
    — the token's at_hash was computed for "not-the-token-we-got-back", but Handler.do_POST always
    hands back access_token="at" — rather than merely proving the happy path still 302s."""
    state, nonce = _login(client)
    _state["id_token"] = _id_token(nonce, access_token="not-the-token-we-got-back")
    r = client.get("/auth/callback", params={"code": "c", "state": state})
    assert r.status_code == 401 and "at_hash" in r.text


def test_callback_still_accepts_an_id_token_with_no_at_hash_at_all(client):
    """Not every IdP (or grant type) returns access_token alongside id_token; a token that never
    had at_hash must keep working exactly as it did before this round."""
    state, nonce = _login(client)
    _state["id_token"] = _id_token(nonce, access_token=None)
    r = client.get("/auth/callback", params={"code": "c", "state": state})
    assert r.status_code in (302, 307), r.text
    from cronsentinel.db import system_session
    with system_session() as s:
        s.execute(text("DELETE FROM users WHERE email='u@example.test'"))
