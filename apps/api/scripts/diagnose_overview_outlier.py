"""R27 — diagnose the ~4s /analytics/overview outlier first flagged in R19/R24.

Method: sample the endpoint's slowest sub-query in a tight loop against the real fullstack (not
in isolation), with wall-clock timestamps, then grep the worker logs for what was running at the
same second. Run with fullstack.sh up:

    DATABASE_URL=... python scripts/diagnose_overview_outlier.py
    grep -h "<timestamp of a spike>" /tmp/jobwatch-fullstack/*.log

Finding (this run): spikes correlate exactly with the reconciler's state-recompute pass
(`reconciled ... took_s=45.38` logged at the same second) and with celery outbound-delivery retry
bursts. Isolated (no other worker running), the same query never exceeds 28ms across 40 reps;
EXPLAIN (ANALYZE, BUFFERS) shows an all-shared-hit, correctly-indexed plan at ~20-30ms. So this is
CPU contention for the box's one vCPU between the API and the background workers, not a query,
index, or plan problem. See docs/LOADTEST.md.
"""
import datetime
import time
import uuid

from sqlalchemy import text

from cronsentinel.db import tenant_session

SLOW = ("SELECT j.name, percentile_cont(0.95) WITHIN GROUP (ORDER BY e.duration_ms) AS p95_ms "
        "FROM executions e JOIN jobs j ON j.id=e.job_id WHERE e.scheduled_ts >= now() - interval '7 days' "
        "AND e.duration_ms IS NOT NULL GROUP BY j.name ORDER BY p95_ms DESC NULLS LAST LIMIT 5")


def run(org: uuid.UUID, seconds: int = 180, threshold_ms: float = 100):
    end = time.time() + seconds
    n, spikes = 0, []
    while time.time() < end:
        with tenant_session(org) as s:
            a = time.perf_counter()
            s.execute(text(SLOW)).all()
            ms = (time.perf_counter() - a) * 1000
        n += 1
        if ms > threshold_ms:
            spikes.append((datetime.datetime.now().isoformat(timespec="milliseconds"), round(ms, 1)))
        time.sleep(0.3)
    print(f"n={n} requests over {seconds}s; spikes over {threshold_ms}ms:")
    for t, ms in spikes:
        print(t, ms)
    print("\nNow: grep -h '<timestamp>' /tmp/jobwatch-fullstack/*.log for each spike above.")


if __name__ == "__main__":
    import os
    org = uuid.UUID(os.environ["ORG_ID"])   # any org with some execution history
    run(org)
