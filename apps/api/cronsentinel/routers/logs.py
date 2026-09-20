"""Central log explorer over execution_logs (Phase 4b). Full-text via ILIKE; TODO: pg_trgm / ClickHouse adapter."""
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from ..auth import Principal, current_principal
from ..db import tenant_session
from ..schemas import LogSearchOut

router = APIRouter(prefix="/api/v1/logs", tags=["logs"])


@router.get("/search", response_model=LogSearchOut)
def search(p: Principal = Depends(current_principal), q: str | None = None, job_id: str | None = None, stream: str | None = None,
           host: str | None = None, status: str | None = None, since: datetime | None = None, until: datetime | None = None, limit: int = Query(100, le=500)):
    where, params = ["l.org_id=:org"], {"org": str(p.org_id), "lim": limit}
    if q: where.append("l.content ILIKE :q"); params["q"] = f"%{q}%"
    if job_id: where.append("e.job_id=:jid"); params["jid"] = job_id
    if stream in ("stdout", "stderr"): where.append("l.stream=:st"); params["st"] = stream
    if host: where.append("e.host=:host"); params["host"] = host
    if status: where.append("e.status=:es"); params["es"] = status
    if since: where.append("e.scheduled_ts >= :since"); params["since"] = since
    if until: where.append("e.scheduled_ts <= :until"); params["until"] = until
    with tenant_session(p.org_id) as s:
        rows = s.execute(text(f"""
            SELECT l.execution_id, l.stream, l.content, e.job_id, j.name AS job_name, e.status::text, e.host, e.scheduled_ts, e.exit_code
            FROM execution_logs l JOIN executions e ON e.id=l.execution_id JOIN jobs j ON j.id=e.job_id
            WHERE {' AND '.join(where)} ORDER BY e.scheduled_ts DESC LIMIT :lim"""), params).all()
        hosts = [r[0] for r in s.execute(text("SELECT DISTINCT host FROM executions WHERE host IS NOT NULL AND scheduled_ts >= now() - interval '30 days' ORDER BY 1")).all()]
    return {"items": [dict(r._mapping) for r in rows], "hosts": hosts}
