"""Materialises expected_runs for every scheduled, unpaused job. Runs every minute.
Idempotent: slots are unique on (job_id, scheduled_for) and inserted ON CONFLICT DO NOTHING,
so overlapping runs of this worker, or a catch-up after downtime, cannot double-book a slot.

R19 rewrite, from the load test. The previous tick:
  * issued one INSERT round-trip per slot — a new every-minute job backfills ~2,000 slots, so a few
    thousand jobs meant well over a million single-row inserts;
  * did all of it in ONE transaction holding FOR UPDATE on up to 5,000 job rows. recompute_state
    also locks the job row (FOR UPDATE OF j), so heartbeats for those jobs blocked behind the
    generator for as long as it ran — minutes, at a few thousand jobs on the test box;
  * re-selected every job every minute, including ones already materialised to the horizon.

Now: jobs are taken in batches of BATCH_JOBS, each batch in its own short transaction (locks held
for one batch, not the whole pass); slots go in with one unnest() INSERT per chunk; and a job is
only revisited when its materialised horizon is within REFRESH_MARGIN of running out.
"""
import asyncio
import time
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import text

from ..db import system_session
from .platform_health import heartbeat
from ..schedule_engine import HORIZON, generation_window, slots_between

log = structlog.get_logger()
INTERVAL_S = 60
BATCH_JOBS = 200                       # rows locked per transaction
SLOT_CHUNK = 20_000                    # rows per INSERT statement
SLOT_BUDGET = 50_000                   # stop filling a batch past this many slots; bounds lock time
REFRESH_MARGIN = timedelta(minutes=30)  # revisit a job once less than this much horizon remains

_INSERT = text("""
    INSERT INTO expected_runs (org_id, job_id, scheduled_for, grace_until, deadline)
    SELECT * FROM unnest(CAST(:o AS uuid[]), CAST(:j AS uuid[]), CAST(:sf AS timestamptz[]),
                         CAST(:gu AS timestamptz[]), CAST(:dl AS timestamptz[]))
    ON CONFLICT (job_id, scheduled_for) DO NOTHING""")

_ADVANCE = text("""
    UPDATE jobs SET generated_through = v.t
    FROM unnest(CAST(:ids AS uuid[]), CAST(:ts AS timestamptz[])) AS v(id, t)
    WHERE jobs.id = v.id""")


def run_batch(s, limit: int = BATCH_JOBS, now: datetime | None = None) -> tuple[int, int]:
    """Materialise one batch. Returns (jobs_processed, slots_inserted); (0, 0) means caught up."""
    now = now or datetime.now(UTC)
    jobs = s.execute(text("""
        SELECT id, org_id, schedule_expr, tz, grace_s, expected_runtime_s, generated_through, created_at
        FROM jobs
        WHERE paused = false AND schedule_expr IS NOT NULL
          AND (generated_through IS NULL OR generated_through < :refresh_before)
        ORDER BY generated_through NULLS FIRST
        LIMIT :lim FOR UPDATE SKIP LOCKED"""), {"refresh_before": now + HORIZON - REFRESH_MARGIN, "lim": limit}).all()
    if not jobs:
        return 0, 0
    cols = {"o": [], "j": [], "sf": [], "gu": [], "dl": []}
    ids, ends = [], []
    for j in jobs:
        # Slot budget: a batch of 200 every-minute jobs catching up is 400k rows and held its row
        # locks ~17 s on the test box. Jobs past the budget are simply not advanced; they stay
        # unmaterialised and are picked first by the next batch (NULLS FIRST / oldest first).
        if len(cols["j"]) >= SLOT_BUDGET:
            break
        start, end = generation_window(j.generated_through, j.created_at, now)
        try:
            slots = slots_between(j.schedule_expr, j.tz, start, end, j.grace_s, j.expected_runtime_s)
        except Exception as e:  # invalid expression that slipped past validation
            log.warning("slot generation failed", job=str(j.id), error=str(e))
            slots = []  # still advance generated_through, or this job is re-selected every tick forever
        for sl in slots:
            cols["o"].append(str(j.org_id)); cols["j"].append(str(j.id))
            cols["sf"].append(sl.scheduled_for); cols["gu"].append(sl.grace_until); cols["dl"].append(sl.deadline)
        ids.append(str(j.id)); ends.append(end)
    inserted = 0
    for i in range(0, len(cols["j"]), SLOT_CHUNK):
        chunk = {k: v[i:i + SLOT_CHUNK] for k, v in cols.items()}
        inserted += s.execute(_INSERT, chunk).rowcount or 0
    s.execute(_ADVANCE, {"ids": ids, "ts": ends})
    return len(jobs), inserted


def tick(s) -> int:
    """Materialise everything that is due, inside the caller's transaction. Kept for tests and
    one-off use; the worker loop below uses run_batch() with a transaction per batch instead."""
    total = 0
    while True:
        n_jobs, n_slots = run_batch(s)
        total += n_slots
        if not n_jobs:
            return total


async def main():
    log.info("schedule-generator started", interval_s=INTERVAL_S, batch=BATCH_JOBS)
    while True:
        t0 = time.monotonic()
        jobs = slots = batches = 0
        try:
            with system_session() as s:
                heartbeat(s, "schedule-generator", INTERVAL_S)   # R25: a break here is a monitoring gap
            while True:
                with system_session() as s:          # one short transaction per batch
                    nj, ns = run_batch(s)
                if not nj:
                    break
                jobs += nj; slots += ns; batches += 1
                if time.monotonic() - t0 > INTERVAL_S:   # never overrun into the next tick
                    log.warning("generator pass exceeded interval; resuming next tick", jobs=jobs, batches=batches)
                    break
            if slots:
                log.info("slots generated", slots=slots, jobs=jobs, batches=batches, took_s=round(time.monotonic() - t0, 2))
        except Exception as e:
            log.error("generator tick failed", error=str(e))
        await asyncio.sleep(max(0, INTERVAL_S - (time.monotonic() - t0)))


if __name__ == "__main__":
    asyncio.run(main())
