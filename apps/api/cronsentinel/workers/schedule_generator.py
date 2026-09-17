"""Materialises expected_runs for every scheduled, unpaused job. Runs every minute.
Idempotent: slots are unique on (job_id, scheduled_for) and inserted ON CONFLICT DO NOTHING,
so overlapping runs of this worker, or a catch-up after downtime, cannot double-book a slot."""
import asyncio
import time
from datetime import UTC, datetime

import structlog
from sqlalchemy import text

from ..db import system_session
from ..schedule_engine import generation_window, slots_between

log = structlog.get_logger()
INTERVAL_S = 60


def tick(s) -> int:
    now = datetime.now(UTC)
    jobs = s.execute(text("""
        SELECT id, org_id, schedule_expr, tz, grace_s, expected_runtime_s, generated_through, created_at
        FROM jobs WHERE paused = false AND schedule_expr IS NOT NULL
        ORDER BY generated_through NULLS FIRST LIMIT 5000 FOR UPDATE SKIP LOCKED""")).all()
    made = 0
    for j in jobs:
        start, end = generation_window(j.generated_through, j.created_at, now)
        try:
            slots = slots_between(j.schedule_expr, j.tz, start, end, j.grace_s, j.expected_runtime_s)
        except Exception as e:  # invalid expression that slipped past validation
            log.warning("slot generation failed", job=str(j.id), error=str(e)); continue
        for sl in slots:
            s.execute(text("""INSERT INTO expected_runs (org_id, job_id, scheduled_for, grace_until, deadline)
                VALUES (:o, :j, :sf, :gu, :dl) ON CONFLICT (job_id, scheduled_for) DO NOTHING"""),
                {"o": j.org_id, "j": j.id, "sf": sl.scheduled_for, "gu": sl.grace_until, "dl": sl.deadline})
        made += len(slots)
        s.execute(text("UPDATE jobs SET generated_through = :t WHERE id = :id"), {"t": end, "id": j.id})
    return made


async def main():
    log.info("schedule-generator started", interval_s=INTERVAL_S)
    while True:
        t0 = time.monotonic()
        try:
            with system_session() as s:
                n = tick(s)
            if n:
                log.info("slots generated", slots=n)
        except Exception as e:
            log.error("generator tick failed", error=str(e))
        await asyncio.sleep(max(0, INTERVAL_S - (time.monotonic() - t0)))


if __name__ == "__main__":
    asyncio.run(main())
