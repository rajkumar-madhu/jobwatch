from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from ..alerting.rules import CONDITIONS
from ..auth import Principal, audit, current_principal, require_role
from ..crypto import encrypt_json
from ..db import tenant_session
from ..schemas import AlertRuleOut, ChannelOut

router = APIRouter(prefix="/api/v1/alerts", tags=["alerting"])

_KINDS = {"email": ["host", "from", "to"], "slack": ["webhook_url"], "teams": ["webhook_url"], "discord": ["webhook_url"],
          "telegram": ["bot_token", "chat_id"], "webhook": ["url"]}


class ChannelIn(BaseModel):
    kind: str
    name: str
    config: dict
    rate_per_min: int = Field(30, ge=1, le=600)


class RuleIn(BaseModel):
    name: str
    condition: str
    scope: dict = {}
    params: dict = {}
    severity: str = "medium"
    channel_ids: list[UUID] = []
    business_hours: dict | None = None
    repeat_interval_s: int | None = Field(None, ge=60)
    enabled: bool = True


class MaintenanceIn(BaseModel):
    scope: dict = {}
    starts_at: datetime
    ends_at: datetime
    rrule: str | None = None


@router.get("/channels", response_model=list[ChannelOut])
def list_channels(p: Principal = Depends(current_principal)):
    with tenant_session(p.org_id) as s:
        return [dict(r._mapping) for r in s.execute(text("SELECT id, kind, name, rate_per_min, enabled, created_at FROM notification_channels ORDER BY name")).all()]


@router.post("/channels", status_code=201)
def create_channel(body: ChannelIn, request: Request, p: Principal = Depends(require_role("devops"))):
    if body.kind not in _KINDS: raise HTTPException(400, f"kind must be one of {list(_KINDS)}")
    missing = [k for k in _KINDS[body.kind] if k not in body.config]
    if missing: raise HTTPException(400, f"config missing {missing}")
    with tenant_session(p.org_id) as s:
        feats = s.execute(text("SELECT pl.features FROM organizations o JOIN plan_limits pl ON pl.plan=o.plan WHERE o.id=:o"), {"o": str(p.org_id)}).scalar()
        if feats and "all" not in feats and body.kind not in feats:
            raise HTTPException(402, f"{body.kind} channel not available on current plan")
        r = s.execute(text("INSERT INTO notification_channels (org_id, kind, name, config_enc, rate_per_min) VALUES (:o, :k, :n, :c, :rl) RETURNING id"),
                      {"o": str(p.org_id), "k": body.kind, "n": body.name, "c": encrypt_json(body.config), "rl": body.rate_per_min}).first()
        audit(s, p, "channel.create", "notification_channel", str(r.id), {"kind": body.kind, "name": body.name}, request.client.host)
        return {"id": r.id, "kind": body.kind, "name": body.name}


@router.post("/channels/{channel_id}/test")
def test_channel(channel_id: UUID, p: Principal = Depends(require_role("devops"))):
    from ..alerting.tasks import send_notification
    payload = {"job_id": "test", "job_name": "test-job", "prev_status": "healthy", "new_status": "failed", "condition": "failed",
               "severity": "low", "incident_id": None, "execution_id": None, "ts": datetime.utcnow().isoformat()}
    send_notification.delay(str(p.org_id), str(channel_id), f"test:{channel_id}", None, payload)
    return {"queued": True}


@router.delete("/channels/{channel_id}", status_code=204)
def delete_channel(channel_id: UUID, request: Request, p: Principal = Depends(require_role("devops"))):
    with tenant_session(p.org_id) as s:
        if not s.execute(text("DELETE FROM notification_channels WHERE id=:id"), {"id": str(channel_id)}).rowcount: raise HTTPException(404)
        audit(s, p, "channel.delete", "notification_channel", str(channel_id), ip=request.client.host)


@router.get("/rules", response_model=list[AlertRuleOut])
def list_rules(p: Principal = Depends(current_principal)):
    with tenant_session(p.org_id) as s:
        return [dict(r._mapping) for r in s.execute(text("SELECT * FROM alert_rules ORDER BY created_at")).all()]


@router.post("/rules", status_code=201)
def create_rule(body: RuleIn, request: Request, p: Principal = Depends(require_role("devops"))):
    if body.condition not in CONDITIONS: raise HTTPException(400, f"condition must be one of {sorted(CONDITIONS)}")
    with tenant_session(p.org_id) as s:
        r = s.execute(text(
            "INSERT INTO alert_rules (org_id, name, condition, scope, params, severity, channel_ids, business_hours, repeat_interval_s, enabled) "
            "VALUES (:o, :n, :c, CAST(:sc AS jsonb), CAST(:pa AS jsonb), :sev, CAST(:ch AS uuid[]), CAST(:bh AS jsonb), :ri, :en) RETURNING id"),
            {"o": str(p.org_id), "n": body.name, "c": body.condition, "sc": __import__("json").dumps(body.scope), "pa": __import__("json").dumps(body.params),
             "sev": body.severity, "ch": [str(c) for c in body.channel_ids], "bh": __import__("json").dumps(body.business_hours) if body.business_hours else None,
             "ri": body.repeat_interval_s, "en": body.enabled}).first()
        audit(s, p, "rule.create", "alert_rule", str(r.id), {"name": body.name}, request.client.host)
        return {"id": r.id}


@router.patch("/rules/{rule_id}")
def toggle_rule(rule_id: UUID, enabled: bool, request: Request, p: Principal = Depends(require_role("devops"))):
    with tenant_session(p.org_id) as s:
        if not s.execute(text("UPDATE alert_rules SET enabled=:e WHERE id=:id"), {"e": enabled, "id": str(rule_id)}).rowcount: raise HTTPException(404)
        audit(s, p, "rule.toggle", "alert_rule", str(rule_id), {"enabled": enabled}, request.client.host)
        return {"id": rule_id, "enabled": enabled}


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: UUID, request: Request, p: Principal = Depends(require_role("devops"))):
    with tenant_session(p.org_id) as s:
        if not s.execute(text("DELETE FROM alert_rules WHERE id=:id"), {"id": str(rule_id)}).rowcount: raise HTTPException(404)
        audit(s, p, "rule.delete", "alert_rule", str(rule_id), ip=request.client.host)


@router.get("/maintenance")
def list_maintenance(p: Principal = Depends(current_principal)):
    with tenant_session(p.org_id) as s:
        return [dict(r._mapping) for r in s.execute(text("SELECT * FROM maintenance_windows WHERE ends_at >= now() - interval '7 days' ORDER BY starts_at")).all()]


@router.post("/maintenance", status_code=201)
def create_maintenance(body: MaintenanceIn, request: Request, p: Principal = Depends(require_role("devops"))):
    if body.ends_at <= body.starts_at: raise HTTPException(400, "ends_at must be after starts_at")
    with tenant_session(p.org_id) as s:
        r = s.execute(text("INSERT INTO maintenance_windows (org_id, scope, starts_at, ends_at, rrule) VALUES (:o, CAST(:sc AS jsonb), :st, :en, :rr) RETURNING id"),
                      {"o": str(p.org_id), "sc": __import__("json").dumps(body.scope), "st": body.starts_at, "en": body.ends_at, "rr": body.rrule}).first()
        audit(s, p, "maintenance.create", "maintenance_window", str(r.id), ip=request.client.host)
        return {"id": r.id}


@router.delete("/maintenance/{mw_id}", status_code=204)
def delete_maintenance(mw_id: UUID, p: Principal = Depends(require_role("devops"))):
    with tenant_session(p.org_id) as s:
        if not s.execute(text("DELETE FROM maintenance_windows WHERE id=:id"), {"id": str(mw_id)}).rowcount: raise HTTPException(404)


@router.get("/ledger")
def ledger(p: Principal = Depends(current_principal), limit: int = 100):
    with tenant_session(p.org_id) as s:
        return [dict(r._mapping) for r in s.execute(text(
            "SELECT l.id, l.incident_id, l.channel_id, c.kind, c.name, l.dedup_key, l.sent_at, l.status, l.error FROM notification_ledger l "
            "LEFT JOIN notification_channels c ON c.id=l.channel_id ORDER BY l.sent_at DESC LIMIT :l"), {"l": min(limit, 500)}).all()]
