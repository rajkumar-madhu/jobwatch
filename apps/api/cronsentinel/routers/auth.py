"""Keycloak OIDC authorization-code flow → httpOnly session cookie (D1).
Session = signed JWT {sub, email, name, org_id?} using SECRET_ENCRYPTION_KEY. CSRF: SameSite=Lax + state param."""
import base64
import hashlib
import json
import re
import secrets
import time
from urllib.parse import urlencode
from uuid import UUID

import httpx
import redis
import structlog
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from jose import jwt
from pydantic import BaseModel
from sqlalchemy import text

from .. import ratelimit
from ..config import settings
from ..csrf import enforce as enforce_csrf
from ..csrf import issue as issue_csrf
from ..db import system_session
from ..keys import signing_key
from ..oidc import IdTokenError, fetch_jwks, verify_id_token

log = structlog.get_logger()
router = APIRouter(prefix="/auth", tags=["auth"])
_r = redis.Redis.from_url(settings.redis_url, decode_responses=True)
COOKIE = "cs_session"
_oidc: dict = {}


def _discovery():
    if not _oidc:
        _oidc.update(httpx.get(f"{settings.keycloak_issuer}/.well-known/openid-configuration", timeout=5).json())
    return _oidc


def sign_session(claims: dict) -> str:
    return jwt.encode({**claims, "iat": int(time.time()), "exp": int(time.time()) + settings.session_ttl_s}, signing_key("session-cookie"), algorithm="HS256")


def read_session(request: Request) -> dict | None:
    tok = request.cookies.get(COOKIE)
    if not tok: return None
    try: return jwt.decode(tok, signing_key("session-cookie"), algorithms=["HS256"])
    except Exception: return None


def _set_cookie(resp: Response, tok: str):
    resp.set_cookie(COOKIE, tok, httponly=True, secure=settings.cookie_secure, samesite="lax", max_age=settings.session_ttl_s, path="/")


def safe_next(nxt: str | None) -> str:
    """Only same-site absolute paths survive. The callback redirects to f"{web_public_url}{next}", so
    `next=@evil.com` became `https://app.example@evil.com` (userinfo + attacker host) and
    `next=.evil.com` / `next=//evil.com` / backslash tricks did the same — a post-login open redirect."""
    if not nxt or not isinstance(nxt, str) or len(nxt) > 2048:
        return "/"
    if not nxt.startswith("/") or nxt.startswith("//") or "\\" in nxt or any(ord(c) < 0x20 for c in nxt):
        return "/"
    return nxt


def _sso_for(email: str):
    """(idp_alias, display_name) when the email's domain is verified by an org with SSO enabled."""
    domain = email.rsplit("@", 1)[-1].strip().lower() if "@" in email else ""
    if not domain:
        return None
    with system_session() as s:
        return s.execute(text("""SELECT os.idp_alias, os.display_name FROM org_domains d JOIN org_sso os ON os.org_id = d.org_id
            WHERE d.domain = :d AND d.verified_at IS NOT NULL AND os.enabled"""), {"d": domain}).first()


@router.get("/sso")
def sso_start(email: str, next: str = "/"):
    """R43: "Sign in with SSO" — find the org that verified this email's domain and send the user
    straight to its identity provider."""
    row = _sso_for(email)
    if not row:
        raise HTTPException(404, "single sign-on is not set up for this email domain; sign in with your password instead")
    return login(next=next, idp=row.idp_alias, login_hint=email)


@router.get("/discover")
def discover(email: str, request: Request):
    """R51: what the sign-in page should offer for this email. Reveals only whether a domain has SSO
    (which its own users see anyway), never whether an account exists. Rate-limited per IP."""
    ratelimit.check(f"discover:{request.client.host if request.client else 'unknown'}", 30, request)
    row = _sso_for(email[:254])
    return {"sso": bool(row), "provider": row.display_name if row else None}


@router.get("/signup")
def signup(email: str = "", next: str = "/onboarding", idp: str | None = None):
    """R51: "Start free". A work email whose company has SSO goes to that IdP (the person joins the
    company's org on first sign-in); anyone else gets Keycloak's registration form, email pre-filled."""
    email = email.strip()[:254]
    if idp:   # "Continue with Google/GitHub/…": the provider creates the account on first sign-in
        return login(next=next, idp=idp, register=True)
    row = _sso_for(email) if email else None
    if row:
        return login(next=next, idp=row.idp_alias, login_hint=email)
    return login(next=next, login_hint=email or None, register=True)


def social_providers() -> list[str]:
    return [a.strip() for a in settings.auth_social_providers.split(",") if re.fullmatch(r"[a-z0-9-]{2,40}", a.strip())]


_SOCIAL_LABELS = {"google": "Google", "github": "GitHub", "microsoft": "Microsoft", "gitlab": "GitLab", "bitbucket": "Bitbucket"}


@router.get("/providers")
def providers():
    """R51: what the sign-in and sign-up pages may offer. Public; reveals only configured button names."""
    return {"social": [{"alias": a, "label": _SOCIAL_LABELS.get(a, a.title())} for a in social_providers()],
            "sso": True, "signup": True}


@router.get("/login")
def login(next: str = "/", idp: str | None = None, login_hint: str | None = None, register: bool = False):
    next = safe_next(next)
    # Only an organization's own IdP (org-<uuid>) or a configured social provider may be hinted.
    if idp is not None and not (re.fullmatch(r"org-[0-9a-f-]{36}", idp) or idp in social_providers()):
        raise HTTPException(400, "invalid identity provider")
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    # state and nonce are stored together: state binds the redirect, nonce binds the id_token to
    # this login attempt so a replayed code cannot mint a session.
    _r.setex(f"oauth:{state}", 600, json.dumps({"next": next, "nonce": nonce, "code_verifier": verifier}))
    params = {"client_id": settings.keycloak_client_id, "response_type": "code", "scope": "openid email profile",
              "redirect_uri": f"{settings.api_public_url}/auth/callback", "state": state, "nonce": nonce,
              "code_challenge": challenge, "code_challenge_method": "S256"}
    if idp:
        params["kc_idp_hint"] = idp          # skip Keycloak's own login page, go to the org's IdP
    if login_hint:
        params["login_hint"] = login_hint[:254]
    q = urlencode(params)
    endpoint = _discovery()["authorization_endpoint"]
    if register and not idp:
        # Keycloak's registration entry point; same OIDC parameters, lands on the sign-up form.
        endpoint = endpoint.rsplit("/auth", 1)[0] + "/registrations"
    return RedirectResponse(f"{endpoint}?{q}")


@router.get("/callback")
def callback(code: str, state: str):
    saved = _r.get(f"oauth:{state}")
    if not saved: raise HTTPException(400, "invalid state")
    _r.delete(f"oauth:{state}")   # single use: a replayed callback fails the state lookup
    try:
        parsed = json.loads(saved)
        nxt, nonce = safe_next(parsed["next"]), parsed["nonce"]
        verifier = parsed.get("code_verifier")
    except (ValueError, KeyError, TypeError) as e:
        # Pre-nonce state values expired (10 min TTL) long ago; a state without a nonce is not
        # accepted any more — it would skip the id_token replay check entirely.
        raise HTTPException(400, "invalid state") from e
    if not nonce:
        raise HTTPException(400, "invalid state")
    tok_data = {"grant_type": "authorization_code", "code": code, "redirect_uri": f"{settings.api_public_url}/auth/callback",
                "client_id": settings.keycloak_client_id}
    if settings.keycloak_client_secret:
        tok_data["client_secret"] = settings.keycloak_client_secret
    if verifier:
        tok_data["code_verifier"] = verifier
    tok = httpx.post(_discovery()["token_endpoint"], data=tok_data, timeout=10)
    if tok.status_code != 200: raise HTTPException(401, "token exchange failed")
    disc = _discovery()
    # R35: the same response's access_token, so verify_id_token can check at_hash — Keycloak (and
    # any spec-following IdP) puts at_hash on every id_token issued alongside an access_token,
    # which every authorization_code exchange is. Found by testing against a real Keycloak: without
    # this, every real login rejected with "No access_token provided to compare against at_hash".
    at = tok.json().get("access_token")
    try:
        claims = verify_id_token(tok.json()["id_token"], jwks=fetch_jwks(disc["jwks_uri"]),
                                 issuer=disc["issuer"], audience=settings.keycloak_client_id, nonce=nonce, access_token=at)
    except IdTokenError:
        # a rotated signing key looks exactly like a bad signature: refetch once before rejecting
        try:
            claims = verify_id_token(tok.json()["id_token"], jwks=fetch_jwks(disc["jwks_uri"], force=True),
                                     issuer=disc["issuer"], audience=settings.keycloak_client_id, nonce=nonce, access_token=at)
        except IdTokenError as e:
            raise HTTPException(401, f"invalid id_token: {e}") from e
    email = claims.get("email")
    if not email:
        raise HTTPException(401, "id_token has no email claim (is the 'email' scope granted?)")
    # Users are matched by email, so an unverified email must never sign anyone in: with any IdP or
    # self-registration path that issues unverified addresses, typing a victim's email would have
    # taken over the victim's JobWatch user and every org membership attached to it.
    if claims.get("email_verified") is not True:
        raise HTTPException(403, "email address is not verified with the identity provider")
    idp = claims.get("idp") or None   # set by the realm's identity_provider session-note mapper (R43)
    sso_org = None
    with system_session() as s:
        if idp and idp.startswith("org-"):
            # R43: an org's IdP may only vouch for addresses in domains that org has verified.
            # Otherwise an admin could configure an IdP that asserts a victim's email.
            domain = email.rsplit("@", 1)[-1].lower()
            sso = s.execute(text("""SELECT os.org_id, os.default_role FROM org_sso os
                JOIN org_domains d ON d.org_id = os.org_id AND d.domain = :d AND d.verified_at IS NOT NULL
                WHERE os.idp_alias = :a AND os.enabled"""), {"a": idp, "d": domain}).first()
            if not sso:
                log.warning("sso login refused: email domain not verified for this IdP", idp=idp, domain=domain)
                raise HTTPException(403, "your organization's single sign-on does not cover this email address")
            sso_org = sso
        existing = s.execute(text("SELECT id, keycloak_sub FROM users WHERE lower(email)=lower(:e)"), {"e": email}).first()
        if existing and existing.keycloak_sub and existing.keycloak_sub != claims["sub"]:
            # Same email, different IdP identity (re-created Keycloak user, second IdP, recycled
            # address). Silently re-pointing the row hands the old identity's orgs to the new one.
            log.warning("login refused: email bound to a different identity", email=email)
            raise HTTPException(409, "this email is linked to a different sign-in identity; contact support")
        if existing:
            uid = existing.id
            s.execute(text("UPDATE users SET keycloak_sub=:sub, name=COALESCE(:n, name) WHERE id=:id"),
                      {"sub": claims["sub"], "n": claims.get("name"), "id": uid})
        else:
            uid = s.execute(text("INSERT INTO users (keycloak_sub, email, name) VALUES (:sub, :e, :n) RETURNING id"),
                            {"sub": claims["sub"], "e": email, "n": claims.get("name")}).scalar()
        if sso_org:
            # R44: someone the directory deactivated over SCIM must not walk back in through JIT.
            if s.execute(text("SELECT 1 FROM scim_users WHERE org_id=:o AND user_id=:u AND NOT active"),
                         {"o": sso_org.org_id, "u": uid}).first():
                raise HTTPException(403, "your access to this organization was removed by your administrator")
            # just-in-time provisioning: first SSO login joins the org with its default role
            s.execute(text("INSERT INTO memberships (user_id, org_id, role) VALUES (:u, :o, :r) ON CONFLICT DO NOTHING"),
                      {"u": uid, "o": sso_org.org_id, "r": sso_org.default_role})
            org = sso_org.org_id
        else:
            org = s.execute(text("""SELECT m.org_id FROM memberships m LEFT JOIN org_sso os ON os.org_id = m.org_id
                WHERE m.user_id=:u AND NOT (COALESCE(os.enforce AND os.enabled, false) AND m.role <> 'owner' AND os.idp_alias IS DISTINCT FROM :idp)
                ORDER BY m.created_at LIMIT 1"""), {"u": uid, "idp": idp}).scalar()
    sess = sign_session({"sub": claims["sub"], "uid": str(uid), "email": email, "name": claims.get("name"),
                         "org_id": str(org) if org else None, "idp": idp})
    resp = RedirectResponse(f"{settings.web_public_url}{nxt if org else '/onboarding'}")
    _set_cookie(resp, sess)
    return resp


@router.post("/logout")
def logout(request: Request):
    s = read_session(request)
    if s:
        enforce_csrf(request, s["sub"])   # otherwise any site can log a user out (login-CSRF's sibling)
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
    _set_cookie(resp, sign_session({**{k: s[k] for k in ("sub", "uid", "email", "name")}, "org_id": str(oid), "idp": s.get("idp")}))
    return resp


@router.post("/switch/{org_id}")
def switch_org(org_id: UUID, request: Request):
    s = read_session(request)
    if not s: raise HTTPException(401)
    enforce_csrf(request, s["sub"])   # cookie POST that bypasses current_principal
    with system_session() as db:
        m = db.execute(text("""SELECT m.role, os.enforce AND os.enabled AS enforce, os.idp_alias FROM memberships m
            LEFT JOIN org_sso os ON os.org_id = m.org_id WHERE m.user_id=:u AND m.org_id=:o"""), {"u": s["uid"], "o": str(org_id)}).first()
        if not m: raise HTTPException(403)
        if sso_required(m.role, m.enforce, m.idp_alias, s.get("idp")):
            raise HTTPException(403, "this organization requires single sign-on: sign in with SSO")
    resp = Response(status_code=204); _set_cookie(resp, sign_session({**{k: s[k] for k in ("sub", "uid", "email", "name")}, "org_id": str(org_id), "idp": s.get("idp")})); return resp


def sso_required(role: str | None, enforce: bool | None, alias: str | None, session_idp: str | None) -> bool:
    """True when this member may not use the org from this session. Owners are exempt (break-glass)."""
    return bool(enforce) and role != "owner" and session_idp != alias
