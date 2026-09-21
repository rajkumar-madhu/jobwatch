"""Auth: Keycloak OIDC bearer (users) or X-API-Key (machines). RBAC via role ordering."""
import ipaddress
import secrets
from dataclasses import dataclass
from uuid import UUID

import httpx
import hashlib
import hmac

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, Header, HTTPException, Request
from jose import jwt
from sqlalchemy import text

from . import ratelimit
from .config import settings
from .db import system_session

ROLE_ORDER = ["viewer", "developer", "sre", "devops", "admin", "owner"]
_ph = PasswordHasher()
_jwks_cache: dict = {}


@dataclass
class Principal:
    org_id: UUID
    role: str
    user_id: UUID | None = None
    api_key_id: UUID | None = None

    def at_least(self, role: str) -> bool:
        return ROLE_ORDER.index(self.role) >= ROLE_ORDER.index(role)


# R21: machine secrets (API keys, agent keys) are hashed with SHA-256, not argon2.
#
# Argon2 exists to make low-entropy *human* passwords expensive to guess. These secrets are
# server-generated with secrets.token_urlsafe(32) — 256 bits — so brute force is infeasible
# whatever the hash, and the ~180 ms argon2 verify bought nothing while running on every request:
# every API-key call, and every agent heartbeat (agent_principal cached the hash, not the result).
# That capped a core at ~5 agent requests/s — the 10k-job load mix needs ~36/s — and let anyone
# who knew a key's (non-secret) prefix burn 180 ms of CPU per request, before the rate limiter ran.
#
# No pepper on purpose: a peppered hash would tie every key to SECRET_ENCRYPTION_KEY, so rotating
# the root secret would silently revoke every API and agent key.
#
# NEVER use these for passwords. There are none today (users authenticate through Keycloak); if a
# password ever appears, it needs argon2, which is why _ph stays for the legacy path below.
_FAST = "sha256$"


def hash_secret(raw: str) -> str:
    return _FAST + hashlib.sha256(raw.encode()).hexdigest()


def verify_secret(raw: str, hashed: str) -> bool:
    if hashed.startswith(_FAST):
        return hmac.compare_digest(hashed, hash_secret(raw))
    try:  # pre-R21 argon2 hash; callers upgrade it via needs_rehash()
        return _ph.verify(hashed, raw)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def needs_rehash(hashed: str) -> bool:
    return not hashed.startswith(_FAST)


def generate_api_key() -> tuple[str, str]:
    prefix = "cs_" + secrets.token_hex(4)
    return prefix, f"{prefix}.{secrets.token_urlsafe(32)}"


def _jwks():
    if "keys" not in _jwks_cache:
        oidc = httpx.get(f"{settings.keycloak_issuer}/.well-known/openid-configuration", timeout=5).json()
        _jwks_cache["keys"] = httpx.get(oidc["jwks_uri"], timeout=5).json()
    return _jwks_cache["keys"]


def _principal_from_jwt(token: str, org_id: UUID) -> Principal:
    try:
        claims = jwt.decode(token, _jwks(), audience=settings.keycloak_audience, issuer=settings.keycloak_issuer)
    except Exception as e:
        raise HTTPException(401, f"invalid token: {e}")
    with system_session() as s:
        row = s.execute(text(
            "SELECT u.id, m.role FROM users u JOIN memberships m ON m.user_id=u.id "
            "WHERE u.keycloak_sub=:sub AND m.org_id=:org"), {"sub": claims["sub"], "org": str(org_id)}).first()
    if not row:
        raise HTTPException(403, "not a member of this organization")
    return Principal(org_id=org_id, role=row.role, user_id=row.id)


def _principal_from_api_key(raw: str) -> Principal:
    prefix = raw.split(".", 1)[0]
    with system_session() as s:
        row = s.execute(text(
            "SELECT id, org_id, key_hash, role FROM api_keys WHERE prefix=:p AND revoked_at IS NULL"), {"p": prefix}).first()
        if not row or not verify_secret(raw, row.key_hash):
            raise HTTPException(401, "invalid api key")
        # Upgrade a pre-R21 argon2 hash in place on first successful use.
        new_hash = hash_secret(raw) if needs_rehash(row.key_hash) else None
        s.execute(text("UPDATE api_keys SET last_used_at=now(), key_hash=COALESCE(:h, key_hash) WHERE id=:id"), {"id": row.id, "h": new_hash})
    return Principal(org_id=row.org_id, role=row.role, api_key_id=row.id)


def current_principal(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    x_org_id: str | None = Header(default=None),
) -> Principal:
    if x_api_key:
        p = _principal_from_api_key(x_api_key)
        ratelimit.check(f"key:{p.api_key_id}", settings.api_rate_per_min, request)
        return p
    sess = request.cookies.get("cs_session")
    if sess:
        from .routers.auth import read_session
        sd = read_session(request)
        if not sd: raise HTTPException(401, "session expired")
        if not sd.get("org_id"): raise HTTPException(403, "no organization yet — complete onboarding")
        with system_session() as s:
            role = s.execute(text("SELECT role FROM memberships WHERE user_id=:u AND org_id=:o"), {"u": sd["uid"], "o": sd["org_id"]}).scalar()
        if not role: raise HTTPException(403, "not a member")
        # R8: cookie auth is the only browser-drivable path, so it is the only one that needs CSRF.
        from . import csrf
        csrf.enforce(request, sd["sub"])
        p = Principal(org_id=UUID(sd["org_id"]), role=role, user_id=UUID(sd["uid"]))
        ratelimit.check(f"user:{p.user_id}", settings.api_rate_per_min, request)
        return p
    if authorization and authorization.lower().startswith("bearer "):
        if not x_org_id:
            raise HTTPException(400, "X-Org-Id header required")
        p = _principal_from_jwt(authorization[7:], UUID(x_org_id))
        ratelimit.check(f"user:{p.user_id}", settings.api_rate_per_min, request)
        return p
    raise HTTPException(401, "authentication required")


def require_role(role: str):
    def dep(p: Principal = Depends(current_principal)) -> Principal:
        if not p.at_least(role):
            raise HTTPException(403, f"requires role >= {role}")
        return p
    return dep


def _inet(ip: str | None) -> str | None:
    """audit_logs.ip is `inet`. Starlette's request.client.host is not always an address — it is
    "testclient" under TestClient, and can be a hostname or a unix-socket path depending on the
    server and proxy in front. Feeding that straight in raised InvalidTextRepresentation and
    failed the *whole write* the audit entry was attached to, so a logging concern could 500 a
    job creation. Store NULL rather than lose the operation."""
    if not ip:
        return None
    try:
        return str(ipaddress.ip_address(ip.strip().strip("[]").split("%")[0]))
    except ValueError:
        return None


def audit(s, p: Principal, action: str, target_type: str, target_id: str, payload: dict | None = None, ip: str | None = None):
    s.execute(text(
        "INSERT INTO audit_logs (org_id, actor_id, actor_type, action, target_type, target_id, ip, payload) "
        "VALUES (:org, :actor, :atype, :action, :tt, :tid, :ip, CAST(:payload AS jsonb))"),
        {"org": str(p.org_id), "actor": str(p.user_id or p.api_key_id), "atype": "user" if p.user_id else "api_key",
         "action": action, "tt": target_type, "tid": target_id, "ip": _inet(ip), "payload": __import__("json").dumps(payload or {})})
