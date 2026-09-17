"""Schedule reconciler (D4): marks LATE / MISSED / TIMEOUT. Runs every RECONCILER_INTERVAL_S."""
import asyncio
import json
import time

import structlog
from sqlalchemy import text

from .. import events
from ..config import settings
from ..db import system_session
from ..state_machine import Event, JobStatus, transition

log = structlog.get_logger()


def tick(s) -> list[dict]:
    changed = []
    # LATE: expected start passed + grace, no execution started since expected
    rows = s.execute(text("""
        SELECT id, org_id, status, next_expected_at, grace_s, late_marked_at FROM jobs
        WHERE paused=false AND schedule_expr IS NOT NULL AND next_expected_at IS NOT NULL
          AND status NOT IN ('running','paused')
          AND now() > next_expected_at + (grace_s || ' seconds')::interval
        FOR UPDATE SKIP LOCKED""")).all()
    for j in rows:
        prev = JobStatus(j.status)
        over_2x = s.execute(text("SELECT now() > :n + (:g * 2 || ' seconds')::interval"), {"n": j.next_expected_at, "g": j.grace_s}).scalar()
        new = transition(prev, Event.GRACE_EXPIRED)
        if over_2x:
            new = transition(new, Event.GRACE_EXPIRED_2X)
        if new != prev:
            s.execute(text("UPDATE jobs SET status=:st, late_marked_at=COALESCE(late_marked_at, now()), updated_at=now() WHERE id=:id"),
                      {"st": new.value, "id": j.id})
            if new == JobStatus.MISSED:
                # record a synthetic missed execution and roll expected time forward
                s.execute(text(
                    "INSERT INTO executions (id, org_id, job_id, status, scheduled_ts, server_received_ts) VALUES (:id, :org, :job, 'missed', :ts, now()) ON CONFLICT DO NOTHING"),
                    {"id": f"missed-{j.id}-{int(j.next_expected_at.timestamp())}", "org": j.org_id, "job": j.id, "ts": j.next_expected_at})
                from ..schedule import next_run
                expr, tz = s.execute(text("SELECT schedule_expr, tz FROM jobs WHERE id=:id"), {"id": j.id}).first()
                s.execute(text("UPDATE jobs SET next_expected_at=:n, late_marked_at=NULL WHERE id=:id"), {"n": next_run(expr, tz), "id": j.id})
            changed.append({"job_id": str(j.id), "org_id": str(j.org_id), "prev_status": prev.value, "new_status": new.value})

    # TIMEOUT: running longer than expected_runtime + grace
    rows = s.execute(text("""
        SELECT j.id, j.org_id, e.id AS exec_id, e.scheduled_ts FROM jobs j
        JOIN executions e ON e.job_id=j.id AND e.status='running'
        WHERE j.status='running' AND j.expected_runtime_s IS NOT NULL
          AND now() > e.agent_ts_start + ((j.expected_runtime_s + j.grace_s) || ' seconds')::interval
        FOR UPDATE OF j SKIP LOCKED""")).all()
    for j in rows:
        s.execute(text("UPDATE executions SET status='timeout', agent_ts_end=now(), duration_ms=EXTRACT(EPOCH FROM (now()-agent_ts_start))*1000 WHERE id=:e AND scheduled_ts=:ts"),
                  {"e": j.exec_id, "ts": j.scheduled_ts})
        s.execute(text("UPDATE jobs SET status='timeout', last_status='timeout', updated_at=now() WHERE id=:id"), {"id": j.id})
        changed.append({"job_id": str(j.id), "org_id": str(j.org_id), "prev_status": "running", "new_status": "timeout"})
    return changed


async def main():
    nc, js = await events.connect()
    log.info("reconciler started", interval=settings.reconciler_interval_s)
    while True:
        t0 = time.monotonic()
        try:
            with system_session() as s:
                changed = tick(s)
            for c in changed:
                await js.publish(f"jobstatus.{c['org_id']}", json.dumps(c).encode())
            if changed:
                log.info("reconciled", changes=len(changed))
        except Exception as e:
            log.error("reconciler tick failed", error=str(e))
        await asyncio.sleep(max(0, settings.reconciler_interval_s - (time.monotonic() - t0)))


if __name__ == "__main__":
    asyncio.run(main())
