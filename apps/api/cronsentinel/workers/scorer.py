"""Reliability score (design §4.3) + usage metering + partition/retention housekeeping. Runs hourly.

R23 rewrite. Before, every step ran in ONE transaction:
  * the score UPDATE touches every job row and holds those row locks until commit — and
    recompute_state (i.e. every heartbeat) takes FOR UPDATE on job rows. Measured: with a tenant
    downgraded from business to free (1.7M executions to delete), heartbeats for its jobs waited
    3.6-4.9 s, for the rest of the tick. That grows with the size of the delete;
  * a failure in any step rolled back all of them — and ensure_month_partition failed permanently
    once a row sat in the default partition (fixed in migration 0012), so one stranded row stopped
    scoring, usage and retention for good.

Now each phase is its own transaction and failure-isolated; scoring goes in batches of jobs;
retention deletes in batches and takes each batch's logs with it; partitions are ensured three
months ahead; and empty partitions for months nobody's retention reaches any more are dropped.
Retention is per plan (7-365 days, enterprise unlimited), so partitions cannot simply be dropped by
age — a month only empties, and becomes droppable, once no tenant's retention covers it.
"""
import asyncio
import time

import structlog
from sqlalchemy import text

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
  FROM jobs j LEFT JOIN executions e ON e.job_id=j.id AND e.scheduled_ts >= now() - interval '30 days'
  WHERE j.id = ANY(CAST(:ids AS uuid[])) GROUP BY j.id, j.org_id),
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
  COALESCE((SELECT bytes FROM org_storage st WHERE st.org_id=o.id), 0)   -- R24: incremental (migration 0013); COALESCE outside: orgs with no logs have no row
FROM organizations o
ON CONFLICT (org_id, period) DO UPDATE SET jobs_count=EXCLUDED.jobs_count, executions_count=EXCLUDED.executions_count, storage_bytes=EXCLUDED.storage_bytes
"""

PARTITIONED = ("executions", "expected_runs", "host_metrics")
MONTHS_AHEAD = 3
SCORE_BATCH = 1000
DELETE_BATCH = 20_000
RETENTION_BUDGET_S = 600   # stop deleting after this long; the next hour continues

# Batched retention deletes. Each statement removes at most DELETE_BATCH rows, in its own
# transaction, so no delete holds locks or blocks vacuum for long.
EXEC_RETENTION = text("""
    WITH doomed AS (
        SELECT e.id, e.scheduled_ts FROM executions e
        JOIN organizations o ON o.id = e.org_id JOIN plan_limits pl ON pl.plan = o.plan
        WHERE pl.retention_days IS NOT NULL AND e.scheduled_ts < now() - make_interval(days => pl.retention_days)
        LIMIT :n),
    del AS (DELETE FROM executions e USING doomed d WHERE e.id = d.id AND e.scheduled_ts = d.scheduled_ts RETURNING e.id),
    logs AS (DELETE FROM execution_logs l USING del WHERE l.execution_id = del.id)
    SELECT count(*) FROM del""")

SLOT_RETENTION = text("""
    WITH doomed AS (
        SELECT er.id, er.scheduled_for FROM expected_runs er
        JOIN organizations o ON o.id = er.org_id JOIN plan_limits pl ON pl.plan = o.plan
        WHERE er.scheduled_for < now() - make_interval(days => pl.expected_runs_retention_days)
        LIMIT :n),
    del AS (DELETE FROM expected_runs er USING doomed d WHERE er.id = d.id AND er.scheduled_for = d.scheduled_for RETURNING 1)
    SELECT count(*) FROM del""")

SIMPLE_RETENTION = {
    "signal_deliveries": text("""WITH d AS (DELETE FROM signal_deliveries WHERE ctid IN
        (SELECT ctid FROM signal_deliveries WHERE created_at < now() - interval '30 days' LIMIT :n) RETURNING 1) SELECT count(*) FROM d"""),
    "host_metrics": text("""WITH doomed AS (SELECT server_id, ts FROM host_metrics WHERE ts < now() - interval '90 days' LIMIT :n),
        d AS (DELETE FROM host_metrics h USING doomed x WHERE h.server_id = x.server_id AND h.ts = x.ts RETURNING 1) SELECT count(*) FROM d"""),
}


def _phase(name, fn):
    """Run one phase; a failure is logged and does not stop the others."""
    t0 = time.monotonic()
    try:
        out = fn()
        log.info("scorer phase", phase=name, result=out, took_s=round(time.monotonic() - t0, 2))
        return out
    except Exception as e:
        log.error("scorer phase failed", phase=name, error=str(e)[:300])
        return None


def ensure_partitions() -> int:
    n = 0
    for parent in PARTITIONED:
        for m in range(0, MONTHS_AHEAD + 1):
            with system_session() as s:   # one transaction per partition: one bad month cannot block the rest
                s.execute(text("SELECT ensure_month_partition(:p, (date_trunc('month', now()) + make_interval(months => :m))::date)"),
                          {"p": parent, "m": m})
            n += 1
    return n


def score() -> int:
    with system_session() as s:
        ids = [str(i) for i in s.execute(text("SELECT id FROM jobs ORDER BY id")).scalars().all()]
    done = 0
    for i in range(0, len(ids), SCORE_BATCH):
        with system_session() as s:     # row locks on at most SCORE_BATCH jobs, released per batch
            done += s.execute(text(SCORE_SQL), {"ids": ids[i:i + SCORE_BATCH]}).rowcount or 0
    return done


# R24: fold the append-only storage ledger into per-org totals. Ledger rows for organisations that
# no longer exist (purge_org writes negative deltas just before deleting the org) are consumed and
# dropped by the join, so they cannot trip org_storage's foreign key.
COMPACT_STORAGE = text("""
    WITH d AS (DELETE FROM storage_ledger RETURNING org_id, delta),
    agg AS (SELECT org_id, sum(delta) AS delta FROM d GROUP BY org_id)
    INSERT INTO org_storage (org_id, bytes)
      SELECT a.org_id, a.delta FROM agg a JOIN organizations o ON o.id = a.org_id
    ON CONFLICT (org_id) DO UPDATE SET bytes = org_storage.bytes + EXCLUDED.bytes, updated_at = now()""")


def usage() -> None:
    with system_session() as s:
        s.execute(COMPACT_STORAGE)
        s.execute(text(USAGE_SQL))


def _drain(stmt, deadline) -> int:
    total = 0
    while time.monotonic() < deadline:
        with system_session() as s:
            n = s.execute(stmt, {"n": DELETE_BATCH}).scalar() or 0
        total += n
        if n < DELETE_BATCH:
            break
    return total


def retention() -> dict:
    deadline = time.monotonic() + RETENTION_BUDGET_S
    out = {"executions": _drain(EXEC_RETENTION, deadline), "expected_runs": _drain(SLOT_RETENTION, deadline)}
    for name, stmt in SIMPLE_RETENTION.items():
        out[name] = _drain(stmt, deadline)
    if time.monotonic() >= deadline:
        log.warning("retention budget exhausted; continuing next tick", budget_s=RETENTION_BUDGET_S)
    return out


def drop_empty_partitions() -> list[str]:
    """Drop month partitions that have emptied and are safely in the past. Instant space reclaim,
    where row deletes only free space for reuse. Never touches the current or previous month."""
    dropped = []
    with system_session() as s:
        parts = s.execute(text("""
            SELECT c.relname, p.relname AS parent FROM pg_inherits i
            JOIN pg_class c ON c.oid = i.inhrelid JOIN pg_class p ON p.oid = i.inhparent
            WHERE p.relname = ANY(:parents) AND c.relname ~ '_[0-9]{6}$'"""), {"parents": list(PARTITIONED)}).all()
    # The age/name/emptiness rules live in drop_partition_if_empty() (migration 0012), which runs as
    # the table owner: neither app role may DROP a table it does not own.
    for relname, _parent in parts:
        with system_session() as s:
            if s.execute(text("SELECT drop_partition_if_empty(:p)"), {"p": relname}).scalar():
                dropped.append(relname)
    return dropped


def tick(s=None):
    """All phases once. The session argument is accepted for compatibility and ignored: each phase
    manages its own transactions, which is the point of R23."""
    _phase("partitions", ensure_partitions)
    n = _phase("score", score) or 0
    _phase("usage", usage)
    r = _phase("retention", retention) or {}
    _phase("drop_empty_partitions", drop_empty_partitions)
    return n, r.get("executions", 0)


async def main():
    log.info("scorer started")
    while True:
        t0 = time.monotonic()
        n, d = tick()
        log.info("scored", jobs=n, retention_deleted=d, took_s=round(time.monotonic() - t0, 1))
        await asyncio.sleep(max(0, 3600 - (time.monotonic() - t0)))


if __name__ == "__main__":
    asyncio.run(main())
