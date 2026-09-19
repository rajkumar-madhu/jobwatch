"""R2/R3 reconciler — operates on materialised slots, not on a mutable next_expected_at column.

Each tick:
  1. slots past grace with no execution      → LATE
  2. slots past deadline with no execution    → MISSED (+ synthetic execution row for history)
  3. running executions past their deadline   → TIMEOUT, slot FAILED
  4. slots inside a maintenance window / paused job → SKIPPED (never alert, never count against SLA)
  5. recompute the four-state for every job whose slots changed, and emit jobstatus events

Because slots persist, an outage of this worker delays detection but never loses it: on restart the
overdue slots are still sitting in `pending` and get settled in the first tick.
"""
import asyncio
import json
import time

import structlog
from sqlalchemy import text

from .. import events
from ..config import settings
from ..db import system_session
from ..job_state import recompute_state  # single source of truth for the four-state

log = structlog.get_logger()


def _settle_maintenance(s) -> list[tuple]:
    """Slots that came due inside a maintenance window, or while the job was paused, are skipped."""
    return s.execute(text("""
        UPDATE expected_runs er SET state='skipped', settled_at=now()
        FROM jobs j WHERE er.job_id=j.id AND er.state IN ('pending','late') AND er.grace_until < now()
          AND er.execution_id IS NULL
          AND (j.paused OR EXISTS (
                SELECT 1 FROM maintenance_windows m WHERE m.org_id=er.org_id
                  AND m.starts_at <= er.scheduled_for AND m.ends_at >= er.scheduled_for
                  AND (m.scope = '{}'::jsonb
                       OR (m.scope->'job_ids') ? CAST(er.job_id AS text)
                       OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(COALESCE(m.scope->'tags','[]')) t WHERE t = ANY(j.tags))
                       OR (m.scope->'environment_ids') ? CAST(j.environment_id AS text))))
        RETURNING er.job_id, er.org_id""")).all()


def _mark_late(s) -> list[tuple]:
    return s.execute(text("""
        UPDATE expected_runs SET state='late'
        WHERE state='pending' AND execution_id IS NULL AND grace_until < now() AND deadline >= now()
        RETURNING job_id, org_id""")).all()


def _mark_missed(s) -> list[tuple]:
    rows = s.execute(text("""
        UPDATE expected_runs SET state='missed', settled_at=now()
        WHERE state IN ('pending','late') AND execution_id IS NULL AND deadline < now()
        RETURNING id, job_id, org_id, scheduled_for""")).all()
    for r in rows:  # synthetic execution so history, SLA and the run strip show the gap
        s.execute(text("""INSERT INTO executions (id, org_id, job_id, status, scheduled_ts, server_received_ts, expected_run_id)
            VALUES (:id, :o, :j, 'missed', :ts, now(), :er) ON CONFLICT DO NOTHING"""),
            {"id": f"missed-{r.job_id}-{int(r.scheduled_for.timestamp())}", "o": r.org_id, "j": r.job_id, "ts": r.scheduled_for, "er": r.id})
    return rows


def _mark_timeouts(s) -> list[tuple]:
    rows = s.execute(text("""
        UPDATE expected_runs er SET state='failed', settled_at=now()
        FROM executions e WHERE e.expected_run_id = er.id AND er.state='running' AND e.status='running' AND er.deadline < now()
        RETURNING er.job_id, er.org_id, e.id AS exec_id, e.scheduled_ts""")).all()
    for r in rows:
        s.execute(text("""UPDATE executions SET status='timeout', agent_ts_end=now(),
            duration_ms=EXTRACT(EPOCH FROM (now()-agent_ts_start))*1000, failure_reason=COALESCE(failure_reason,'exceeded expected runtime')
            WHERE id=:e AND scheduled_ts=:ts"""), {"e": r.exec_id, "ts": r.scheduled_ts})
    return rows


def tick(s) -> list[dict]:
    touched: set[tuple] = set()
    skipped = _settle_maintenance(s)
    for r in skipped: touched.add((r.job_id, r.org_id))
    for r in _mark_late(s): touched.add((r.job_id, r.org_id))
    for r in _mark_missed(s): touched.add((r.job_id, r.org_id))
    for r in _mark_timeouts(s): touched.add((r.job_id, r.org_id))
    # jobs whose agent just went quiet, or came back, also need a state pass
    for r in s.execute(text("""SELECT j.id, j.org_id FROM jobs j JOIN servers sv ON sv.id=j.server_id JOIN agents a ON a.id=sv.agent_id
        WHERE (a.last_seen_at < now() - make_interval(secs => a.heartbeat_interval_s * 3)) <> (j.job_state='unknown' AND j.unknown_reason='agent_offline')""")).all():
        touched.add((r.id, r.org_id))
    # pause/resume flips need a state pass even when no slot changed
    for r in s.execute(text("SELECT id, org_id FROM jobs WHERE paused <> (job_state='unknown' AND unknown_reason='paused')")).all():
        touched.add((r.id, r.org_id))
    if skipped:
        log.info("slots skipped (maintenance/paused)", slots=len(skipped))
    out = []
    for job_id, org_id in touched:
        ev = recompute_state(s, job_id, org_id)
        if ev:
            out.append(ev)
    return out


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
