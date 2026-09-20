from fastapi import APIRouter, Depends
from sqlalchemy import text

from ..auth import Principal, current_principal
from ..db import tenant_session
from ..schemas import OverviewOut

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


@router.get("/overview", response_model=OverviewOut)
def overview(p: Principal = Depends(current_principal)):
    with tenant_session(p.org_id) as s:
        counts = dict(s.execute(text("SELECT status::text, count(*) FROM jobs GROUP BY status")).all())
        today = s.execute(text(
            "SELECT count(*) AS executions, count(*) FILTER (WHERE status='success') AS ok, count(*) FILTER (WHERE status IN ('failed','timeout','missed')) AS bad "
            "FROM executions WHERE scheduled_ts >= date_trunc('day', now())")).first()
        slow = s.execute(text(
            "SELECT j.name, percentile_cont(0.95) WITHIN GROUP (ORDER BY e.duration_ms) AS p95_ms FROM executions e JOIN jobs j ON j.id=e.job_id "
            "WHERE e.scheduled_ts >= now() - interval '7 days' AND e.duration_ms IS NOT NULL GROUP BY j.name ORDER BY p95_ms DESC NULLS LAST LIMIT 5")).all()
        failing = s.execute(text(
            "SELECT j.name, count(*) AS failures FROM executions e JOIN jobs j ON j.id=e.job_id WHERE e.status IN ('failed','timeout') "
            "AND e.scheduled_ts >= now() - interval '7 days' GROUP BY j.name ORDER BY failures DESC LIMIT 5")).all()
        mt = s.execute(text("SELECT round(avg(EXTRACT(EPOCH FROM (COALESCE(detected_at, started_at) - started_at)))/60,1) AS mttd_min, round(avg(EXTRACT(EPOCH FROM (resolved_at - started_at)))/60,1) AS mttr_min FROM incidents WHERE started_at >= now() - interval '30 days'")).first()
    total = sum(counts.values())
    return {
        "total_jobs": total, "by_status": counts,
        "executions_today": today.executions, "success_rate_today": round(today.ok / today.executions * 100, 2) if today.executions else None,
        "top_slowest_7d": [dict(r._mapping) for r in slow], "top_failing_7d": [dict(r._mapping) for r in failing],
        **{k: (float(v) if v is not None else None) for k, v in dict(mt._mapping).items()},
    }
