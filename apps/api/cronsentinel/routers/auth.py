"""Keycloak OIDC authorization-code flow → httpOnly session cookie (D1).
Session = signed JWT {sub, email, name, org_id?} using SECRET_ENCRYPTION_KEY. CSRF: SameSite=Lax + state param.
PKCE (S256) is required — Keycloak public clients reject authorize requests without code_challenge_method."""
import base64
import hashlib
import json
import secrets
import time
from urllib.parse import urlencode
from uuid import UUID

import httpx
import redis
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from jose import jwt
from pydantic import BaseModel
from sqlalchemy import text

from ..config import settings
from ..csrf import enforce as enforce_csrf
from ..csrf import issue as issue_csrf
from ..db import system_session
from ..keys import signing_key
from ..oidc import IdTokenError, fetch_jwks, verify_id_token

router = APIRouter(prefix="/auth", tags=["auth"])
_r = redis.Redis.from_url(settings.redis_url, decode_responses=True)
COOKIE = "cs_session"
_oidc: dict = {}


def _discovery():
    if not _oidc:
        try:
            r = httpx.get(f"{settings.keycloak_issuer}/.well-known/openid-configuration", timeout=5)
            r.raise_for_status()
            _oidc.update(r.json())
        except Exception as e:
            raise HTTPException(503, f"identity provider unavailable: {e}") from e
    return _oidc


def _pkce_pair() -> tuple[str, str]:
    """RFC 7636 S256: verifier is sent on token exchange; challenge goes on the authorize URL."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def sign_session(claims: dict) -> str:
    return jwt.encode({**claims, "iat": int(time.time()), "exp": int(time.time()) + settings.session_ttl_s}, signing_key("session-cookie"), algorithm="HS256")


def read_session(request: Request) -> dict | None:
    tok = request.cookies.get(COOKIE)
    if not tok: return None
    try: return jwt.decode(tok, signing_key("session-cookie"), algorithms=["HS256"])
    except Exception: return None


def _set_cookie(resp: Response, tok: str):
    resp.set_cookie(COOKIE, tok, httponly=True, secure=settings.cookie_secure, samesite="lax", max_age=settings.session_ttl_s, path="/")


@router.get("/login")
def login(next: str = "/", login_hint: str | None = None):
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    verifier, challenge = _pkce_pair()
    # state binds the redirect; nonce binds the id_token; verifier completes PKCE on token exchange.
    _r.setex(f"oauth:{state}", 600, json.dumps({"next": next, "nonce": nonce, "code_verifier": verifier}))
    params: dict[str, str] = {
        "client_id": settings.keycloak_client_id,
        "response_type": "code",
        "scope": "openid email profile",
        "redirect_uri": f"{settings.api_public_url}/auth/callback",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    # OIDC login_hint pre-fills the IdP email field when the user typed it on our form.
    if login_hint and "@" in login_hint:
        params["login_hint"] = login_hint.strip()[:254]
    q = urlencode(params)
    return RedirectResponse(f"{_discovery()['authorization_endpoint']}?{q}")


def _login_error_redirect(reason: str, state: str | None = None) -> RedirectResponse:
    """Send the browser back to the SPA login page instead of a FastAPI JSON 422."""
    if state:
        _r.delete(f"oauth:{state}")
    # Keep the message short and safe — never echo raw IdP HTML/descriptions.
    q = urlencode({"error": reason})
    return RedirectResponse(f"{settings.web_public_url}/login?{q}")


@router.get("/callback")
def callback(code: str | None = None, state: str | None = None, error: str | None = None, error_description: str | None = None):
    # Keycloak cancel / misconfig / bare refresh land here without `code`.
    if error:
        msg = {"access_denied": "sign_in_cancelled", "login_required": "sign_in_required"}.get(error, "sign_in_failed")
        return _login_error_redirect(msg, state)
    if not code or not state:
        return _login_error_redirect("sign_in_incomplete", state)
    saved = _r.get(f"oauth:{state}")
    if not saved: raise HTTPException(400, "invalid state")
    _r.delete(f"oauth:{state}")   # single use: a replayed callback fails the state lookup
    try:
        parsed = json.loads(saved)
        nxt, nonce = parsed["next"], parsed["nonce"]
        code_verifier = parsed.get("code_verifier")
    except (ValueError, KeyError, TypeError):
        nxt, nonce, code_verifier = saved, None, None   # tolerate sessions started before the nonce change
    token_data = {"grant_type": "authorization_code", "code": code, "redirect_uri": f"{settings.api_public_url}/auth/callback",
                  "client_id": settings.keycloak_client_id}
    if code_verifier:
        token_data["code_verifier"] = code_verifier
    if settings.keycloak_client_secret:
        token_data["client_secret"] = settings.keycloak_client_secret
    tok = httpx.post(_discovery()["token_endpoint"], data=token_data, timeout=10)
    if tok.status_code != 200: raise HTTPException(401, "token exchange failed")
    disc = _discovery()
    try:
        claims = verify_id_token(tok.json()["id_token"], jwks=fetch_jwks(disc["jwks_uri"]),
                                 issuer=disc["issuer"], audience=settings.keycloak_client_id, nonce=nonce)
    except IdTokenError:
        # a rotated signing key looks exactly like a bad signature: refetch once before rejecting
        try:
            claims = verify_id_token(tok.json()["id_token"], jwks=fetch_jwks(disc["jwks_uri"], force=True),
                                     issuer=disc["issuer"], audience=settings.keycloak_client_id, nonce=nonce)
        except IdTokenError as e:
            raise HTTPException(401, f"invalid id_token: {e}") from e
    email = claims.get("email")
    if not email:
        raise HTTPException(401, "id_token has no email claim (is the 'email' scope granted?)")
    with system_session() as s:
        uid = s.execute(text("INSERT INTO users (keycloak_sub, email, name) VALUES (:sub, :e, :n) ON CONFLICT (email) DO UPDATE SET keycloak_sub=EXCLUDED.keycloak_sub, name=COALESCE(EXCLUDED.name, users.name) RETURNING id"),
                        {"sub": claims["sub"], "e": email, "n": claims.get("name")}).scalar()
        org = s.execute(text("SELECT org_id FROM memberships WHERE user_id=:u ORDER BY created_at LIMIT 1"), {"u": uid}).scalar()
    sess = sign_session({"sub": claims["sub"], "uid": str(uid), "email": email, "name": claims.get("name"), "org_id": str(org) if org else None})
    resp = RedirectResponse(f"{settings.web_public_url}{nxt if org else '/onboarding'}")
    _set_cookie(resp, sess)
    return resp


@router.post("/logout")
def logout():
    resp = Response(status_code=204); resp.delete_cookie(COOKIE, path="/"); return resp


@router.get("/session")
def session(request: Request):
    s = read_session(request)
    if not s: raise HTTPException(401, "not signed in")
    with system_session() as db:
        orgs = [dict(r._mapping) for r in db.execute(text("SELECT o.id, o.name, o.slug, o.plan, m.role FROM memberships m JOIN organizations o ON o.id=m.org_id WHERE m.user_id=:u"), {"u": s["uid"]}).all()]
    # csrf_token is the SPA's double-submit value; it must be echoed in X-CSRF-Token on writes.
    return {"user": {"id": s["uid"], "email": s["email"], "name": s.get("name")}, "org_id": s.get("org_id"),
            "orgs": orgs, "csrf_token": issue_csrf(s["sub"])}


class CreateOrg(BaseModel):
    name: str


@router.post("/orgs", status_code=201)
def create_org(body: CreateOrg, request: Request):
    """Onboarding step 1: create workspace/org for the signed-in user; becomes owner."""
    s = read_session(request)
    if not s: raise HTTPException(401)
    enforce_csrf(request, s["sub"])   # cookie POST that bypasses current_principal
    slug = "".join(c if c.isalnum() else "-" for c in body.name.lower()).strip("-")[:40] + "-" + secrets.token_hex(2)
    with system_session() as db:
        oid = db.execute(text("INSERT INTO organizations (name, slug) VALUES (:n, :s) RETURNING id"), {"n": body.name, "s": slug}).scalar()
        db.execute(text("INSERT INTO memberships (user_id, org_id, role) VALUES (:u, :o, 'owner')"), {"u": s["uid"], "o": oid})
        db.execute(text("INSERT INTO subscriptions (org_id, plan, trial_ends_at) VALUES (:o, 'free', now() + interval '14 days')"), {"o": oid})
        ws = db.execute(text("INSERT INTO workspaces (org_id, name) VALUES (:o, 'default') RETURNING id"), {"o": oid}).scalar()
        db.execute(text("INSERT INTO environments (org_id, workspace_id, name) VALUES (:o, :w, 'production')"), {"o": oid, "w": ws})
    resp = Response(content='{"org_id":"%s"}' % oid, media_type="application/json", status_code=201)
    _set_cookie(resp, sign_session({**{k: s[k] for k in ("sub", "uid", "email", "name")}, "org_id": str(oid)}))
    return resp


@router.post("/switch/{org_id}")
def switch_org(org_id: UUID, request: Request):
    s = read_session(request)
    if not s: raise HTTPException(401)
    enforce_csrf(request, s["sub"])   # cookie POST that bypasses current_principal
    with system_session() as db:
        if not db.execute(text("SELECT 1 FROM memberships WHERE user_id=:u AND org_id=:o"), {"u": s["uid"], "o": str(org_id)}).first(): raise HTTPException(403)
    resp = Response(status_code=204); _set_cookie(resp, sign_session({**{k: s[k] for k in ("sub", "uid", "email", "name")}, "org_id": str(org_id)})); return resp
