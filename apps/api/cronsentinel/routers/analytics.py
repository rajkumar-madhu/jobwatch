"""Analytics + reports (spec §16). Extends /api/v1/analytics (overview lives in overview.py)."""
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from ..auth import Principal, current_principal
from ..db import tenant_session

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


def _rows(s, q, **p): return [dict(r._mapping) for r in s.execute(text(q), p).all()]


@router.get("/series")
def series(p: Principal = Depends(current_principal), days: int = Query(14, le=90), bucket: str = "day"):
    b = "hour" if bucket == "hour" else "day"
    with tenant_session(p.org_id) as s:
        return {"bucket": b, "points": _rows(s, f"""
            SELECT date_trunc('{b}', scheduled_ts) AS t, count(*) AS executions,
              count(*) FILTER (WHERE status='success') AS ok, count(*) FILTER (WHERE status IN ('failed','timeout')) AS failed, count(*) FILTER (WHERE status='missed') AS missed,
              percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms) AS p50_ms, percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95_ms
            FROM executions WHERE scheduled_ts >= now() - (:d || ' days')::interval AND status<>'running' GROUP BY 1 ORDER BY 1""", d=days),
                "incidents": _rows(s, f"SELECT date_trunc('{b}', started_at) AS t, count(*) AS n FROM incidents WHERE started_at >= now() - (:d || ' days')::interval GROUP BY 1 ORDER BY 1", d=days)}


@router.get("/jobs")
def per_job(p: Principal = Depends(current_principal), days: int = Query(30, le=365), sort: str = "reliability"):
    order = {"reliability": "reliability_score ASC NULLS LAST", "failures": "failures DESC", "p95": "p95_ms DESC NULLS LAST", "drift": "drift_pct DESC NULLS LAST"}.get(sort, "reliability_score ASC NULLS LAST")
    with tenant_session(p.org_id) as s:
        return _rows(s, f"""
            WITH x AS (SELECT job_id, status, duration_ms, scheduled_ts, ntile(2) OVER (PARTITION BY job_id ORDER BY scheduled_ts) AS half FROM executions WHERE scheduled_ts >= now() - (:d || ' days')::interval AND status<>'running')
            SELECT j.id, j.name, j.status::text, j.reliability_score, j.sla_target, j.tags,
              count(x.*) AS runs, count(*) FILTER (WHERE x.status='success') AS ok, count(*) FILTER (WHERE x.status IN ('failed','timeout')) AS failures, count(*) FILTER (WHERE x.status='missed') AS missed,
              round(100.0*count(*) FILTER (WHERE x.status='success')/NULLIF(count(x.*),0), 2) AS success_rate,
              percentile_cont(0.5) WITHIN GROUP (ORDER BY x.duration_ms) AS p50_ms, percentile_cont(0.95) WITHIN GROUP (ORDER BY x.duration_ms) AS p95_ms, max(x.duration_ms) AS max_ms,
              round(100.0 * (avg(x.duration_ms) FILTER (WHERE x.half=2) - avg(x.duration_ms) FILTER (WHERE x.half=1)) / NULLIF(avg(x.duration_ms) FILTER (WHERE x.half=1),0), 1) AS drift_pct,
              CASE WHEN j.sla_target IS NULL THEN NULL ELSE round(100.0*count(*) FILTER (WHERE x.status='success')/NULLIF(count(x.*),0),2) >= j.sla_target END AS sla_met,
              round(count(x.*) * COALESCE(percentile_cont(0.5) WITHIN GROUP (ORDER BY x.duration_ms),0) / 3600000.0 * :rate, 2) AS est_cost_usd
            FROM jobs j LEFT JOIN x ON x.job_id=j.id GROUP BY j.id ORDER BY {order}""", d=days, rate=0.05)  # est_cost: compute-hours × $0.05 placeholder; TODO per-org rate


@router.get("/mttr")
def mttr(p: Principal = Depends(current_principal), days: int = Query(30, le=365)):
    with tenant_session(p.org_id) as s:
        r = s.execute(text("""SELECT count(*) AS incidents,
              round(avg(EXTRACT(EPOCH FROM (COALESCE(detected_at, started_at) - started_at)))/60, 1) AS mttd_min,
              round(avg(EXTRACT(EPOCH FROM (acknowledged_at - started_at)))/60, 1) AS mtta_min,
              round(avg(EXTRACT(EPOCH FROM (resolved_at - started_at)))/60, 1) AS mttr_min,
              count(*) FILTER (WHERE status='resolved') AS resolved
            FROM incidents WHERE started_at >= now() - (:d || ' days')::interval"""), {"d": days}).first()
    return dict(r._mapping)


@router.get("/report")
def report(p: Principal = Depends(current_principal), period: str = "weekly"):
    days = {"daily": 1, "weekly": 7, "monthly": 30}.get(period, 7)
    since = datetime.now(UTC) - timedelta(days=days)
    with tenant_session(p.org_id) as s:
        tot = s.execute(text("""SELECT count(*) AS runs, count(*) FILTER (WHERE status='success') AS ok, count(*) FILTER (WHERE status IN ('failed','timeout')) AS failed, count(*) FILTER (WHERE status='missed') AS missed,
            percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95_ms FROM executions WHERE scheduled_ts >= :s AND status<>'running'"""), {"s": since}).first()
        prev = s.execute(text("SELECT count(*) AS runs, count(*) FILTER (WHERE status='success') AS ok FROM executions WHERE scheduled_ts >= :s2 AND scheduled_ts < :s AND status<>'running'"), {"s2": since - timedelta(days=days), "s": since}).first()
        worst = _rows(s, "SELECT j.name, count(*) AS failures FROM executions e JOIN jobs j ON j.id=e.job_id WHERE e.scheduled_ts >= :s AND e.status IN ('failed','timeout','missed') GROUP BY j.name ORDER BY 2 DESC LIMIT 5", s=since)
        slow = _rows(s, "SELECT j.name, percentile_cont(0.95) WITHIN GROUP (ORDER BY e.duration_ms) AS p95_ms FROM executions e JOIN jobs j ON j.id=e.job_id WHERE e.scheduled_ts >= :s AND e.duration_ms IS NOT NULL GROUP BY j.name ORDER BY 2 DESC LIMIT 5", s=since)
        sla = s.execute(text("SELECT count(*) FILTER (WHERE sla_target IS NOT NULL) AS with_sla FROM jobs")).first()
        inc = s.execute(text("SELECT count(*) AS n, count(*) FILTER (WHERE status<>'resolved') AS open FROM incidents WHERE started_at >= :s"), {"s": since}).first()
        low = _rows(s, "SELECT name, reliability_score FROM jobs WHERE reliability_score IS NOT NULL ORDER BY reliability_score ASC LIMIT 5")
    rate = round(100 * tot.ok / tot.runs, 2) if tot.runs else None
    prate = round(100 * prev.ok / prev.runs, 2) if prev.runs else None
    return {"period": period, "since": since.isoformat(), "runs": tot.runs, "success_rate": rate, "success_rate_prev": prate, "delta_pts": round(rate - prate, 2) if rate is not None and prate is not None else None,
            "failed": tot.failed, "missed": tot.missed, "p95_ms": tot.p95_ms, "incidents": inc.n, "open_incidents": inc.open, "jobs_with_sla": sla.with_sla,
            "worst_jobs": worst, "slowest_jobs": slow, "lowest_reliability": low}
