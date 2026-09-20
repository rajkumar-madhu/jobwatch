"""Status pages: org-managed config + public read endpoint (no auth) honouring per-job public_visibility (D14)."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text

from ..auth import Principal, audit, current_principal, require_role
from ..db import system_session, tenant_session
from ..schemas import StatusPageOut

router = APIRouter(prefix="/api/v1/status-pages", tags=["status-pages"])
public = APIRouter(prefix="/public", tags=["public"])


class PageIn(BaseModel):
    slug: str
    title: str
    visibility: str = "public"
    job_ids: list[UUID] = []


@router.get("", response_model=list[StatusPageOut])
def list_pages(p: Principal = Depends(current_principal)):
    with tenant_session(p.org_id) as s:
        return [dict(r._mapping) for r in s.execute(text("SELECT id, slug, title, visibility, job_ids FROM status_pages")).all()]


@router.post("", status_code=201)
def create_page(body: PageIn, request: Request, p: Principal = Depends(require_role("admin"))):
    if not body.slug.replace("-", "").isalnum(): raise HTTPException(400, "slug: letters, digits, dashes")
    with tenant_session(p.org_id) as s:
        try:
            pid = s.execute(text("INSERT INTO status_pages (org_id, slug, title, visibility, job_ids) VALUES (:o, :s, :t, :v, CAST(:j AS uuid[])) RETURNING id"),
                            {"o": str(p.org_id), "s": body.slug, "t": body.title, "v": body.visibility, "j": [str(j) for j in body.job_ids]}).scalar()
        except Exception as e:
            if "unique" in str(e).lower(): raise HTTPException(409, "slug taken")
            raise
        s.execute(text("UPDATE jobs SET public_visibility='status_only' WHERE id = ANY(CAST(:j AS uuid[])) AND public_visibility='none'"), {"j": [str(j) for j in body.job_ids]})
        audit(s, p, "status_page.create", "status_page", str(pid), ip=request.client.host)
    return {"id": pid, "url": f"/status/{body.slug}"}


@router.delete("/{page_id}", status_code=204)
def delete_page(page_id: UUID, p: Principal = Depends(require_role("admin"))):
    with tenant_session(p.org_id) as s:
        if not s.execute(text("DELETE FROM status_pages WHERE id=:id"), {"id": str(page_id)}).rowcount: raise HTTPException(404)


@public.get("/status/{slug}")
def public_status(slug: str):
    with system_session() as s:
        pg = s.execute(text("SELECT id, org_id, title, visibility, job_ids FROM status_pages WHERE slug=:s"), {"s": slug}).first()
        if not pg or pg.visibility != "public": raise HTTPException(404)  # TODO: private pages behind signed link
        jobs = s.execute(text("""
            SELECT j.id, j.name, j.status::text, j.public_visibility, j.last_run_at, j.schedule_expr,
              (SELECT round(100.0*count(*) FILTER (WHERE status='success')/NULLIF(count(*) FILTER (WHERE status IN ('success','failed','timeout','missed')),0),2)
                 FROM executions e WHERE e.job_id=j.id AND e.scheduled_ts >= now() - interval '90 days') AS uptime_90d,
              (SELECT array_agg(status::text ORDER BY scheduled_ts DESC) FROM (SELECT status, scheduled_ts FROM executions WHERE job_id=j.id ORDER BY scheduled_ts DESC LIMIT 60) x) AS recent
            FROM jobs j WHERE j.org_id=:o AND j.id = ANY(CAST(:ids AS uuid[])) AND j.public_visibility<>'none' ORDER BY j.name"""),
            {"o": pg.org_id, "ids": [str(i) for i in pg.job_ids]}).all()
        inc = s.execute(text("SELECT title, severity::text, status::text, started_at, resolved_at FROM incidents WHERE org_id=:o AND affected_job_ids && CAST(:ids AS uuid[]) AND started_at >= now() - interval '30 days' ORDER BY started_at DESC LIMIT 20"),
                        {"o": pg.org_id, "ids": [str(i) for i in pg.job_ids]}).all()
        mw = s.execute(text("SELECT starts_at, ends_at FROM maintenance_windows WHERE org_id=:o AND ends_at >= now() ORDER BY starts_at LIMIT 5"), {"o": pg.org_id}).all()
    overall = "operational"
    for j in jobs:
        if j.status in ("failed", "timeout", "missed"): overall = "degraded"
        elif j.status == "late" and overall == "operational": overall = "delayed"
    return {"title": pg.title, "overall": overall, "jobs": [{"name": j.name, "status": j.status, "last_run_at": j.last_run_at, "uptime_90d": float(j.uptime_90d) if j.uptime_90d is not None else None,
                                                            "recent": j.recent or [], "show_duration": j.public_visibility == "status_and_duration"} for j in jobs],
            "incidents": [dict(r._mapping) for r in inc], "maintenance": [dict(r._mapping) for r in mw]}
