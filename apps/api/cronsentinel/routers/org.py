from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text

from ..auth import Principal, audit, current_principal, generate_api_key, hash_secret, require_role
from ..db import tenant_session

router = APIRouter(prefix="/api/v1", tags=["org"])


class WorkspaceIn(BaseModel):
    name: str


class ApiKeyIn(BaseModel):
    name: str
    role: str = "developer"


@router.get("/me")
def me(p: Principal = Depends(current_principal)):
    return {"org_id": p.org_id, "role": p.role, "user_id": p.user_id, "api_key_id": p.api_key_id}


@router.get("/workspaces")
def list_workspaces(p: Principal = Depends(current_principal)):
    with tenant_session(p.org_id) as s:
        return [dict(r._mapping) for r in s.execute(text("SELECT id, name, created_at FROM workspaces ORDER BY name")).all()]


@router.post("/workspaces", status_code=201)
def create_workspace(body: WorkspaceIn, request: Request, p: Principal = Depends(require_role("admin"))):
    with tenant_session(p.org_id) as s:
        r = s.execute(text("INSERT INTO workspaces (org_id, name) VALUES (:org, :n) RETURNING id, name"), {"org": str(p.org_id), "n": body.name}).first()
        audit(s, p, "workspace.create", "workspace", str(r.id), ip=request.client.host)
        return dict(r._mapping)


@router.get("/api-keys")
def list_keys(p: Principal = Depends(require_role("admin"))):
    with tenant_session(p.org_id) as s:
        return [dict(r._mapping) for r in s.execute(text(
            "SELECT id, name, prefix, role, last_used_at, revoked_at, created_at FROM api_keys ORDER BY created_at DESC")).all()]


@router.post("/api-keys", status_code=201)
def create_key(body: ApiKeyIn, request: Request, p: Principal = Depends(require_role("admin"))):
    if body.role in ("owner",): raise HTTPException(400, "cannot mint owner keys")
    prefix, raw = generate_api_key()
    with tenant_session(p.org_id) as s:
        r = s.execute(text("INSERT INTO api_keys (org_id, name, prefix, key_hash, role) VALUES (:org, :n, :p, :h, :r) RETURNING id"),
                      {"org": str(p.org_id), "n": body.name, "p": prefix, "h": hash_secret(raw), "r": body.role}).first()
        audit(s, p, "api_key.create", "api_key", str(r.id), {"name": body.name}, request.client.host)
    return {"id": r.id, "prefix": prefix, "key": raw, "note": "shown once; store securely"}


@router.delete("/api-keys/{key_id}", status_code=204)
def revoke_key(key_id: str, request: Request, p: Principal = Depends(require_role("admin"))):
    with tenant_session(p.org_id) as s:
        n = s.execute(text("UPDATE api_keys SET revoked_at=now() WHERE id=:id AND revoked_at IS NULL"), {"id": key_id}).rowcount
        if not n: raise HTTPException(404)
        audit(s, p, "api_key.revoke", "api_key", key_id, ip=request.client.host)


@router.get("/audit-logs")
def audit_logs(p: Principal = Depends(require_role("admin")), limit: int = 100):
    with tenant_session(p.org_id) as s:
        return [dict(r._mapping) for r in s.execute(text(
            "SELECT id, actor_id, actor_type, action, target_type, target_id, ip, ts, payload FROM audit_logs ORDER BY ts DESC LIMIT :l"), {"l": min(limit, 500)}).all()]
