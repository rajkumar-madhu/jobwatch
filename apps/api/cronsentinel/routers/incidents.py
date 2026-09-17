from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text

from ..auth import Principal, audit, current_principal, require_role
from ..db import tenant_session

router = APIRouter(prefix="/api/v1/incidents", tags=["incidents"])


class NoteIn(BaseModel):
    text: str


class ResolveIn(BaseModel):
    resolution: str
    root_cause: str | None = None


@router.get("")
def list_incidents(p: Principal = Depends(current_principal), status: str | None = None, limit: int = 50):
    q = "SELECT i.*, (SELECT array_agg(name) FROM jobs WHERE id = ANY(i.affected_job_ids)) AS job_names FROM incidents i"
    params = {"l": min(limit, 200)}
    if status: q += " WHERE status=:st"; params["st"] = status
    with tenant_session(p.org_id) as s:
        rows = s.execute(text(q + " ORDER BY started_at DESC LIMIT :l"), params).all()
    return [{**dict(r._mapping), "severity": str(r.severity), "status": str(r.status)} for r in rows]


@router.get("/{incident_id}")
def get_incident(incident_id: UUID, p: Principal = Depends(current_principal)):
    with tenant_session(p.org_id) as s:
        i = s.execute(text("SELECT * FROM incidents WHERE id=:id"), {"id": str(incident_id)}).first()
        if not i: raise HTTPException(404)
        ev = s.execute(text("SELECT ts, kind, actor_id, payload FROM incident_events WHERE incident_id=:id ORDER BY ts"), {"id": str(incident_id)}).all()
        notif = s.execute(text("SELECT l.sent_at, c.kind, c.name, l.status, l.error FROM notification_ledger l LEFT JOIN notification_channels c ON c.id=l.channel_id WHERE l.incident_id=:id ORDER BY l.sent_at"),
                          {"id": str(incident_id)}).all()
        jobs = s.execute(text("SELECT id, name, status::text, last_run_at FROM jobs WHERE id = ANY(:ids)"), {"ids": list(i.affected_job_ids)}).all()
        execs = s.execute(text("SELECT id, job_id, status::text, scheduled_ts, duration_ms, exit_code, host FROM executions WHERE job_id = ANY(:ids) AND scheduled_ts >= :since ORDER BY scheduled_ts DESC LIMIT 50"),
                          {"ids": list(i.affected_job_ids), "since": i.started_at}).all()
        from ..alerting.correlation import downstream_impact
        impact = {str(jid): downstream_impact(s, str(p.org_id), str(jid)) for jid in i.affected_job_ids}
    d = dict(i._mapping); d["severity"] = str(d["severity"]); d["status"] = str(d["status"])
    return {"incident": d, "downstream_impact": impact, "timeline": [dict(r._mapping) for r in ev], "notifications": [dict(r._mapping) for r in notif],
            "affected_jobs": [dict(r._mapping) for r in jobs], "executions": [dict(r._mapping) for r in execs]}


@router.post("/{incident_id}/ack")
def ack(incident_id: UUID, request: Request, p: Principal = Depends(require_role("developer"))):
    with tenant_session(p.org_id) as s:
        n = s.execute(text("UPDATE incidents SET status='acknowledged', acknowledged_at=COALESCE(acknowledged_at, now()), responder_ids=array_append(responder_ids, :u) WHERE id=:id AND status='open'"),
                      {"id": str(incident_id), "u": str(p.user_id) if p.user_id else None}).rowcount
        if not n: raise HTTPException(409, "not open")
        s.execute(text("INSERT INTO incident_events (org_id, incident_id, kind, actor_id) VALUES (:o, :i, 'acknowledged', :a)"), {"o": str(p.org_id), "i": str(incident_id), "a": str(p.user_id) if p.user_id else None})
        audit(s, p, "incident.ack", "incident", str(incident_id), ip=request.client.host)
    return {"ok": True}


@router.post("/{incident_id}/resolve")
def resolve(incident_id: UUID, body: ResolveIn, request: Request, p: Principal = Depends(require_role("developer"))):
    with tenant_session(p.org_id) as s:
        n = s.execute(text("UPDATE incidents SET status='resolved', resolved_at=now(), resolution=:r, root_cause=COALESCE(:rc, root_cause) WHERE id=:id AND status<>'resolved'"),
                      {"id": str(incident_id), "r": body.resolution, "rc": body.root_cause}).rowcount
        if not n: raise HTTPException(409, "already resolved")
        s.execute(text("INSERT INTO incident_events (org_id, incident_id, kind, actor_id, payload) VALUES (:o, :i, 'resolved', :a, :p::jsonb)"),
                  {"o": str(p.org_id), "i": str(incident_id), "a": str(p.user_id) if p.user_id else None, "p": __import__("json").dumps(body.model_dump())})
        audit(s, p, "incident.resolve", "incident", str(incident_id), ip=request.client.host)
    return {"ok": True}


@router.post("/{incident_id}/notes")
def add_note(incident_id: UUID, body: NoteIn, p: Principal = Depends(require_role("developer"))):
    with tenant_session(p.org_id) as s:
        s.execute(text("INSERT INTO incident_events (org_id, incident_id, kind, actor_id, payload) VALUES (:o, :i, 'note', :a, :p::jsonb)"),
                  {"o": str(p.org_id), "i": str(incident_id), "a": str(p.user_id) if p.user_id else None, "p": __import__("json").dumps({"text": body.text})})
    return {"ok": True}
