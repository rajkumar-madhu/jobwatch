"""R4 — signal destinations (the only door out of JobWatch)."""
import json
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import text

from ..auth import Principal, audit, current_principal, require_role
from ..crypto import decrypt_json, encrypt_json
from ..db import tenant_session
from ..schemas import SignalDeliveryOut, SignalDestinationOut, SignalSchemaOut
from ..outbound import schema
from ..outbound.deliver import sign

router = APIRouter(prefix="/api/v1/integrations", tags=["integrations"])


class DestinationIn(BaseModel):
    name: str
    kind: str = "webhook"
    url: HttpUrl
    secret: str | None = Field(None, min_length=16, max_length=256)
    headers: dict[str, str] = {}
    event_types: list[str] = []
    workspace_ids: list[UUID] = []
    enabled: bool = True


def _check(body: DestinationIn):
    if body.kind not in ("webhook", "nats"):
        raise HTTPException(400, "kind must be webhook or nats")
    for t in body.event_types:
        try: schema.validate_event_type(t)
        except ValueError as e: raise HTTPException(400, str(e))
    host = body.url.host or ""
    # Data boundary: refuse to point a tenant's telemetry at link-local / loopback — SSRF and
    # "accidentally exported to the metadata service" both start here.
    if host in ("localhost", "127.0.0.1", "169.254.169.254", "::1") or host.endswith(".internal"):
        raise HTTPException(400, "destination host not allowed")


@router.get("/schema", response_model=SignalSchemaOut, response_model_by_alias=True)
def signal_schema():
    """Published contract for consumers (AEGIS reads this, not the DB)."""
    return {"schema": schema.SCHEMA, "version": schema.VERSION, "event_types": list(schema.EVENT_TYPES),
            "headers": ["X-JobWatch-Signal-Id", "X-JobWatch-Schema", "X-JobWatch-Signature"],
            "signature": "sha256 HMAC of raw body with the destination secret, as 'sha256=<hex>'",
            "idempotency": "dedupe on signal_id; deliveries may repeat on retry",
            "example": schema.from_jobstatus({"org_id": "…", "job_id": "…", "new_state": "failing", "prev_state": "ok",
                                              "new_status": "failed", "prev_status": "healthy", "consecutive_failures": 1},
                                             {"name": "nightly-backup", "kind": "cron", "workspace_id": "…"}).envelope()}


@router.get("/destinations", response_model=list[SignalDestinationOut])
def list_destinations(p: Principal = Depends(current_principal)):
    with tenant_session(p.org_id) as s:
        rows = s.execute(text("""SELECT id, name, kind, event_types, workspace_ids, enabled, consecutive_failures, disabled_reason, created_at,
            (SELECT count(*) FROM signal_deliveries d WHERE d.destination_id=sd.id AND d.status='sent' AND d.sent_at > now() - interval '24 hours') AS sent_24h,
            (SELECT count(*) FROM signal_deliveries d WHERE d.destination_id=sd.id AND d.status IN ('failed','dead') AND d.created_at > now() - interval '24 hours') AS failed_24h
            FROM signal_destinations sd ORDER BY name""")).all()
        out = []
        for r in rows:
            d = dict(r._mapping)
            cfg = decrypt_json(s.execute(text("SELECT config_enc FROM signal_destinations WHERE id=:id"), {"id": r.id}).scalar())
            d["url"] = cfg.get("url"); d["has_secret"] = bool(cfg.get("secret"))
            out.append(d)
        return out


@router.post("/destinations", status_code=201)
def create_destination(body: DestinationIn, request: Request, p: Principal = Depends(require_role("devops"))):
    _check(body)
    cfg = {"url": str(body.url), "secret": body.secret, "headers": body.headers}
    with tenant_session(p.org_id) as s:
        r = s.execute(text("""INSERT INTO signal_destinations (org_id, name, kind, config_enc, event_types, workspace_ids, enabled)
            VALUES (:o, :n, :k, :c, :et, :ws, :en) RETURNING id"""),
            {"o": str(p.org_id), "n": body.name, "k": body.kind, "c": encrypt_json(cfg), "et": body.event_types,
             "ws": [str(w) for w in body.workspace_ids], "en": body.enabled}).first()
        audit(s, p, "integration.destination.create", "signal_destination", str(r.id), {"kind": body.kind}, request.client.host if request.client else None)
        return {"id": str(r.id)}


@router.put("/destinations/{dest_id}")
def update_destination(dest_id: UUID, body: DestinationIn, request: Request, p: Principal = Depends(require_role("devops"))):
    _check(body)
    with tenant_session(p.org_id) as s:
        cfg = {"url": str(body.url), "secret": body.secret, "headers": body.headers}
        if body.secret is None:  # keep existing secret when the form omits it
            cur = s.execute(text("SELECT config_enc FROM signal_destinations WHERE id=:id"), {"id": str(dest_id)}).scalar()
            if cur: cfg["secret"] = decrypt_json(cur).get("secret")
        n = s.execute(text("""UPDATE signal_destinations SET name=:n, kind=:k, config_enc=:c, event_types=:et, workspace_ids=:ws, enabled=:en,
            consecutive_failures=0, disabled_reason=NULL, updated_at=now() WHERE id=:id"""),
            {"n": body.name, "k": body.kind, "c": encrypt_json(cfg), "et": body.event_types, "ws": [str(w) for w in body.workspace_ids],
             "en": body.enabled, "id": str(dest_id)}).rowcount
        if not n: raise HTTPException(404)
        audit(s, p, "integration.destination.update", "signal_destination", str(dest_id), None, request.client.host if request.client else None)
        return {"ok": True}


@router.delete("/destinations/{dest_id}", status_code=204)
def delete_destination(dest_id: UUID, request: Request, p: Principal = Depends(require_role("devops"))):
    with tenant_session(p.org_id) as s:
        if not s.execute(text("DELETE FROM signal_destinations WHERE id=:id"), {"id": str(dest_id)}).rowcount: raise HTTPException(404)
        audit(s, p, "integration.destination.delete", "signal_destination", str(dest_id), None, request.client.host if request.client else None)


@router.post("/destinations/{dest_id}/test")
def test_destination(dest_id: UUID, p: Principal = Depends(require_role("devops"))):
    """Synchronous test: sends a job.state_changed sample and reports the response code."""
    with tenant_session(p.org_id) as s:
        row = s.execute(text("SELECT kind::text, config_enc FROM signal_destinations WHERE id=:id"), {"id": str(dest_id)}).first()
        if not row: raise HTTPException(404)
        cfg = decrypt_json(row.config_enc)
    if row.kind != "webhook":
        raise HTTPException(400, "test only supported for webhook destinations")
    sig = schema.from_jobstatus({"org_id": str(p.org_id), "job_id": "00000000-0000-0000-0000-000000000000", "new_state": "ok", "prev_state": "unknown",
                                 "new_status": "healthy", "prev_status": "unknown"}, {"name": "jobwatch-test", "kind": "heartbeat"})
    raw = json.dumps({**sig.envelope(), "test": True}, separators=(",", ":"), default=str).encode()
    headers = {"Content-Type": "application/json", "X-JobWatch-Signal-Id": sig.signal_id, "X-JobWatch-Schema": "jobwatch.signal/1", **cfg.get("headers", {})}
    if cfg.get("secret"): headers["X-JobWatch-Signature"] = sign(cfg["secret"], raw)
    try:
        r = httpx.post(cfg["url"], content=raw, headers=headers, timeout=10)
        return {"status_code": r.status_code, "ok": r.status_code < 400}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/deliveries", response_model=list[SignalDeliveryOut])
def deliveries(limit: int = 100, p: Principal = Depends(current_principal)):
    with tenant_session(p.org_id) as s:
        return [dict(r._mapping) for r in s.execute(text("""SELECT d.id, d.destination_id, sd.name AS destination, d.signal_id, d.event_type, d.status,
            d.attempts, d.last_error, d.response_code, d.created_at, d.sent_at FROM signal_deliveries d JOIN signal_destinations sd ON sd.id=d.destination_id
            ORDER BY d.created_at DESC LIMIT :l"""), {"l": min(limit, 500)}).all()]
