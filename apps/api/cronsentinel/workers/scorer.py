"""Reliability score (design §4.3) + usage metering + partition/retention housekeeping. Runs hourly."""
import asyncio, time

import structlog
from sqlalchemy import text

from ..config import settings
from ..db import system_session

log = structlog.get_logger()

SCORE_SQL = """
WITH w AS (
  SELECT j.id, j.org_id,
    count(e.*) FILTER (WHERE e.status IN ('success','failed','timeout','missed')) AS n,
    count(e.*) FILTER (WHERE e.status='success') AS ok,
    count(e.*) FILTER (WHERE e.status='missed') AS missed,
    count(e.*) FILTER (WHERE e.status='timeout') AS timeouts,
    stddev_samp(e.duration_ms) FILTER (WHERE e.status='success') AS sd, avg(e.duration_ms) FILTER (WHERE e.status='success') AS mean
  FROM jobs j LEFT JOIN executions e ON e.job_id=j.id AND e.scheduled_ts >= now() - interval '30 days' GROUP BY j.id, j.org_id),
alerts AS (SELECT unnest(affected_job_ids) AS job_id, count(*) AS n FROM incidents WHERE started_at >= now() - interval '30 days' GROUP BY 1),
deps AS (SELECT d.job_id, count(*) FILTER (WHERE j2.status IN ('failed','missed','timeout')) AS bad FROM job_dependencies d JOIN jobs j2 ON j2.id=d.depends_on_job_id GROUP BY d.job_id)
UPDATE jobs SET reliability_score = GREATEST(0, LEAST(100, round(
    100
    - 40 * (1 - COALESCE(w.ok::float / NULLIF(w.n,0), 1))
    - 20 * COALESCE(w.missed::float / NULLIF(w.n,0), 0)
    - 15 * COALESCE(w.timeouts::float / NULLIF(w.n,0), 0)
    - 10 * LEAST(1, COALESCE(w.sd / NULLIF(w.mean,0), 0) / 0.5)
    - 5  * LEAST(1, COALESCE(deps.bad, 0))
    - 10 * LEAST(1, COALESCE(alerts.n, 0) / 20.0)
  )))::smallint
FROM w LEFT JOIN alerts ON alerts.job_id = w.id LEFT JOIN deps ON deps.job_id = w.id WHERE jobs.id = w.id AND w.n > 0
"""

USAGE_SQL = """
INSERT INTO usage_records (org_id, period, jobs_count, executions_count, storage_bytes)
SELECT o.id, date_trunc('month', now())::date, (SELECT count(*) FROM jobs j WHERE j.org_id=o.id),
  (SELECT count(*) FROM executions e WHERE e.org_id=o.id AND e.scheduled_ts >= date_trunc('month', now())),
  (SELECT COALESCE(sum(length(content)),0) FROM execution_logs l WHERE l.org_id=o.id)
FROM organizations o
ON CONFLICT (org_id, period) DO UPDATE SET jobs_count=EXCLUDED.jobs_count, executions_count=EXCLUDED.executions_count, storage_bytes=EXCLUDED.storage_bytes
"""

RETENTION_SQL = """
DELETE FROM executions e USING organizations o JOIN plan_limits pl ON pl.plan=o.plan
WHERE e.org_id=o.id AND pl.retention_days IS NOT NULL AND e.scheduled_ts < now() - (pl.retention_days || ' days')::interval
"""


def tick(s):
    n = s.execute(text(SCORE_SQL)).rowcount
    s.execute(text(USAGE_SQL))
    s.execute(text("SELECT ensure_month_partition('executions', (now() + interval '1 month')::date)"))
    s.execute(text("SELECT ensure_month_partition('expected_runs', (now() + interval '1 month')::date)"))
    # R3 open item: expected_runs retention, per plan. Row deletes like executions for now; the same
    # partition-drop TODO applies (drop whole month partitions older than the max retention in use).
    s.execute(text("""DELETE FROM expected_runs er USING organizations o JOIN plan_limits pl ON pl.plan=o.plan
        WHERE er.org_id=o.id AND er.scheduled_for < now() - (pl.expected_runs_retention_days || ' days')::interval"""))
    s.execute(text("DELETE FROM signal_deliveries WHERE created_at < now() - interval '30 days'"))
    s.execute(text("SELECT ensure_month_partition('host_metrics', (now() + interval '1 month')::date)"))
    d = s.execute(text(RETENTION_SQL)).rowcount  # TODO: drop whole partitions instead of row deletes for large tenants
    s.execute(text("DELETE FROM execution_logs l WHERE NOT EXISTS (SELECT 1 FROM executions e WHERE e.id=l.execution_id)"))
    s.execute(text("DELETE FROM host_metrics WHERE ts < now() - interval '90 days'"))
    return n, d


async def main():
    log.info("scorer started")
    while True:
        t0 = time.monotonic()
        try:
            with system_session() as s:
                n, d = tick(s)
            log.info("scored", jobs=n, retention_deleted=d)
        except Exception as e:
            log.error("scorer failed", error=str(e))
        await asyncio.sleep(max(0, 3600 - (time.monotonic() - t0)))


if __name__ == "__main__":
    asyncio.run(main())
