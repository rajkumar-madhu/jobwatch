"""R4 — signal destinations (the only door out of JobWatch)."""
import json
from uuid import UUID

import httpx

from .. import netguard
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
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
    url: str
    secret: str | None = Field(None, min_length=16, max_length=256)
    headers: dict[str, str] = {}
    subject_prefix: str | None = Field(None, max_length=200)   # nats only
    token: str | None = Field(None, max_length=500)            # nats only; no user/password auth yet
    event_types: list[str] = []
    workspace_ids: list[UUID] = []
    enabled: bool = True


NATS_SUBJECT_RE = __import__("re").compile(r"^[^\s.*>]+(\.[^\s.*>]+)*$")


def _check(body: DestinationIn):
    if body.kind not in ("webhook", "nats"):
        raise HTTPException(400, "kind must be webhook or nats")
    for t in body.event_types:
        try: schema.validate_event_type(t)
        except ValueError as e: raise HTTPException(400, str(e))
    if body.kind == "nats":
        if not body.subject_prefix:
            raise HTTPException(400, "subject_prefix is required for a nats destination")
        if not NATS_SUBJECT_RE.match(body.subject_prefix):
            raise HTTPException(400, "subject_prefix must not contain whitespace, '.', '*' or '>' as a token boundary issue — "
                                      "use dot-separated tokens with no wildcards, e.g. 'acme.signals'")


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
            if r.kind == "nats":
                d["subject_prefix"] = cfg.get("subject_prefix"); d["has_token"] = bool(cfg.get("token"))
            out.append(d)
        return out


@router.post("/destinations", status_code=201)
def create_destination(body: DestinationIn, request: Request, p: Principal = Depends(require_role("devops"))):
    try:  # R18/R30: refuse internal targets and dangerous headers when saved, with a clear reason
        if body.kind == "nats":
            netguard.check_nats_url(body.url)
        else:
            netguard.check_url(body.url); netguard.check_headers(body.headers)
    except netguard.BlockedDestination as e:
        raise HTTPException(400, str(e))
    _check(body)
    cfg = ({"url": body.url, "subject_prefix": body.subject_prefix, "token": body.token} if body.kind == "nats"
           else {"url": body.url, "secret": body.secret, "headers": body.headers})
    with tenant_session(p.org_id) as s:
        r = s.execute(text("""INSERT INTO signal_destinations (org_id, name, kind, config_enc, event_types, workspace_ids, enabled)
            VALUES (:o, :n, :k, :c, :et, :ws, :en) RETURNING id"""),
            {"o": str(p.org_id), "n": body.name, "k": body.kind, "c": encrypt_json(cfg), "et": body.event_types,
             "ws": [str(w) for w in body.workspace_ids], "en": body.enabled}).first()
        audit(s, p, "integration.destination.create", "signal_destination", str(r.id), {"kind": body.kind}, request.client.host if request.client else None)
        return {"id": str(r.id)}


@router.put("/destinations/{dest_id}")
def update_destination(dest_id: UUID, body: DestinationIn, request: Request, p: Principal = Depends(require_role("devops"))):
    try:  # R18/R30: refuse internal targets and dangerous headers when saved, with a clear reason
        if body.kind == "nats":
            netguard.check_nats_url(body.url)
        else:
            netguard.check_url(body.url); netguard.check_headers(body.headers)
    except netguard.BlockedDestination as e:
        raise HTTPException(400, str(e))
    _check(body)
    with tenant_session(p.org_id) as s:
        cfg = ({"url": body.url, "subject_prefix": body.subject_prefix, "token": body.token} if body.kind == "nats"
               else {"url": body.url, "secret": body.secret, "headers": body.headers})
        cur = s.execute(text("SELECT config_enc FROM signal_destinations WHERE id=:id"), {"id": str(dest_id)}).scalar()
        if body.kind == "webhook" and body.secret is None and cur:  # keep existing secret when the form omits it
            cfg["secret"] = decrypt_json(cur).get("secret")
        if body.kind == "nats" and body.token is None and cur:      # same courtesy for the nats auth token
            cfg["token"] = decrypt_json(cur).get("token")
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
    """Synchronous test: sends a job.state_changed sample and reports the outcome."""
    with tenant_session(p.org_id) as s:
        row = s.execute(text("SELECT kind::text, config_enc FROM signal_destinations WHERE id=:id"), {"id": str(dest_id)}).first()
        if not row: raise HTTPException(404)
        cfg = decrypt_json(row.config_enc)
    sig = schema.from_jobstatus({"org_id": str(p.org_id), "job_id": "00000000-0000-0000-0000-000000000000", "new_state": "ok", "prev_state": "unknown",
                                 "new_status": "healthy", "prev_status": "unknown"}, {"name": "jobwatch-test", "kind": "heartbeat"})
    raw = json.dumps({**sig.envelope(), "test": True}, separators=(",", ":"), default=str).encode()
    if row.kind == "webhook":
        headers = {"Content-Type": "application/json", "X-JobWatch-Signal-Id": sig.signal_id, "X-JobWatch-Schema": "jobwatch.signal/1", **cfg.get("headers", {})}
        if cfg.get("secret"): headers["X-JobWatch-Signature"] = sign(cfg["secret"], raw)
        try:
            r = netguard.post(cfg["url"], content=raw, headers=headers, timeout=10)
            return {"status_code": r.status_code, "ok": r.status_code < 400}
        except Exception as e:
            # R18: never str(e). It carried internal addresses and distinguished refused/timeout/reset —
            # enough to port-scan the cluster from the API pod one "send test" at a time.
            return {"ok": False, "error": netguard.describe_failure(e)}
    elif row.kind == "nats":
        try:
            from ..outbound.deliver import _publish_nats
            _publish_nats(cfg, raw, sig.signal_id, "job.state_changed")
            # R30: NATS core pub/sub has no delivery ack beyond the server accepting the publish
            # (nc.flush(), inside _publish_nats) — "ok" here means reachable and accepted, not that
            # a subscriber received it. Same honesty limit JetStream-less pub/sub always has.
            return {"ok": True, "note": "published — NATS core has no delivery confirmation beyond the server accepting it"}
        except Exception as e:
            return {"ok": False, "error": netguard.describe_failure(e)}
    else:
        raise HTTPException(400, f"test not supported for kind {row.kind}")


@router.get("/deliveries", response_model=list[SignalDeliveryOut])
def deliveries(limit: int = 100, p: Principal = Depends(current_principal)):
    with tenant_session(p.org_id) as s:
        return [dict(r._mapping) for r in s.execute(text("""SELECT d.id, d.destination_id, sd.name AS destination, d.signal_id, d.event_type, d.status,
            d.attempts, d.last_error, d.response_code, d.created_at, d.sent_at FROM signal_deliveries d JOIN signal_destinations sd ON sd.id=d.destination_id
            ORDER BY d.created_at DESC LIMIT :l"""), {"l": min(limit, 500)}).all()]
