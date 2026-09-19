"""Auth: Keycloak OIDC bearer (users) or X-API-Key (machines). RBAC via role ordering."""
import ipaddress
import secrets
from dataclasses import dataclass
from uuid import UUID

import httpx
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


def hash_secret(raw: str) -> str:
    return _ph.hash(raw)


def verify_secret(raw: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, raw)
    except VerifyMismatchError:
        return False


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
        s.execute(text("UPDATE api_keys SET last_used_at=now() WHERE id=:id"), {"id": row.id})
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
