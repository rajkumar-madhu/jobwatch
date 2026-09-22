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
from .platform_health import GAP_MULTIPLIER, IN_GAP_SQL, heartbeat

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


def retro_match(s) -> list[tuple]:
    """R25: bind unattached terminal executions to still-open slots. Executions that arrived while
    no slot existed (generator outage, job just created with a past first slot) were recorded with
    expected_run_id NULL; without this they look like misses once the slot is backfilled. Same
    window as the live path (attach_slot): start within [scheduled_for - 90 s, deadline]. One
    execution per slot, nearest start wins."""
    return s.execute(text("""
        WITH cand AS (
            SELECT DISTINCT ON (er.id) er.id AS er_id, e.id AS exec_id, e.status, er.job_id, er.org_id
            FROM expected_runs er
            JOIN executions e ON e.job_id = er.job_id AND e.org_id = er.org_id AND e.expected_run_id IS NULL
                 AND e.status IN ('success','failed','timeout')
                 AND COALESCE(e.agent_ts_start, e.scheduled_ts, e.server_received_ts)
                     BETWEEN er.scheduled_for - interval '90 seconds' AND er.deadline
            WHERE er.state IN ('pending','late') AND er.execution_id IS NULL
            ORDER BY er.id, abs(EXTRACT(EPOCH FROM (COALESCE(e.agent_ts_start, e.scheduled_ts, e.server_received_ts) - er.scheduled_for)))),
        one AS (SELECT DISTINCT ON (exec_id) * FROM cand ORDER BY exec_id, er_id),   -- an execution settles at most one slot
        upd AS (
            UPDATE expected_runs er SET
                state = CASE WHEN one.status = 'success' THEN 'succeeded' ELSE 'failed' END::expected_run_state,
                execution_id = one.exec_id, matched_at = now(), settled_at = now()
            FROM one WHERE er.id = one.er_id RETURNING er.id, one.exec_id, er.job_id, er.org_id),
        ex AS (UPDATE executions e SET expected_run_id = upd.id FROM upd WHERE e.id = upd.exec_id)
        SELECT job_id, org_id FROM upd""")).all()


def _mark_unobserved(s) -> list[tuple]:
    """R25: overdue empty slots inside a monitoring gap are unobserved, not missed. No synthetic
    execution and no alert: nobody was watching, so nothing was observed either way."""
    rows = s.execute(text(f"""
        WITH u AS (
            UPDATE expected_runs er SET state='unobserved', settled_at=now()
            WHERE er.state IN ('pending','late') AND er.execution_id IS NULL AND er.deadline < now()
              AND ({IN_GAP_SQL})
            RETURNING er.id, er.job_id, er.org_id, er.deadline),
        cnt AS (
            UPDATE monitoring_gaps g SET slots_unobserved = g.slots_unobserved + c.n
            FROM (SELECT g2.id, count(*) AS n FROM u JOIN monitoring_gaps g2
                    ON u.deadline >= g2.started_at AND u.deadline < g2.ended_at GROUP BY g2.id) c
            WHERE g.id = c.id)
        SELECT job_id, org_id FROM u"""),
        {"self_service": "reconciler", "thr_s": settings.reconciler_interval_s * GAP_MULTIPLIER}).all()
    if rows:
        log.warning("slots marked unobserved (monitoring gap)", slots=len(rows))
    return rows


def _mark_missed(s) -> list[tuple]:
    # R19/R20: one statement. The synthetic execution rows (history, SLA and the run strip show the
    # gap) used to be inserted one round-trip per slot — 17,712 of them for a 30-minute outage at
    # 10k jobs, inside the same transaction as everything else.
    # R25: runs after retro_match and _mark_unobserved, so only genuinely watched, genuinely empty
    # slots get here.
    return s.execute(text("""
        WITH m AS (
            UPDATE expected_runs SET state='missed', settled_at=now()
            WHERE state IN ('pending','late') AND execution_id IS NULL AND deadline < now()
            RETURNING id, job_id, org_id, scheduled_for),
        ins AS (
            INSERT INTO executions (id, org_id, job_id, status, scheduled_ts, server_received_ts, expected_run_id)
            SELECT 'missed-' || job_id || '-' || CAST(EXTRACT(EPOCH FROM scheduled_for) AS bigint), org_id, job_id,
                   'missed', scheduled_for, now(), id
            FROM m ON CONFLICT DO NOTHING)
        SELECT id, job_id, org_id, scheduled_for FROM m""")).all()


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


RECOMPUTE_BATCH = 100  # job rows locked per transaction during the state pass


def settle(s) -> set[tuple]:
    """Phase 1: settle slots (set-based UPDATEs on expected_runs; no job-row locks). Returns the
    (job_id, org_id) pairs that need a state pass."""
    touched: set[tuple] = set()
    heartbeat(s, "reconciler", settings.reconciler_interval_s)   # R25: records our own gaps
    skipped = _settle_maintenance(s)
    for r in skipped: touched.add((r.job_id, r.org_id))
    for r in retro_match(s): touched.add((r.job_id, r.org_id))   # R25: before any late/missed call
    for r in _mark_late(s): touched.add((r.job_id, r.org_id))
    for r in _mark_unobserved(s): touched.add((r.job_id, r.org_id))
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
    return touched


def recompute(s, jobs) -> list[dict]:
    """Phase 2: four-state pass for a batch of jobs. recompute_state takes FOR UPDATE on each job
    row, so the caller should keep batches small and commit between them."""
    out = []
    for job_id, org_id in jobs:
        ev = recompute_state(s, job_id, org_id)
        if ev:
            out.append(ev)
    return out


def tick(s) -> list[dict]:
    """Both phases in the caller's transaction — kept for tests and one-off use. The worker loop
    runs them as separate short transactions instead; see main()."""
    return recompute(s, sorted(settle(s), key=str))


async def main():
    """R20: settlement and the state pass are separate transactions, and the state pass commits
    every RECOMPUTE_BATCH jobs. Before, all of it was one transaction: settling a 30-minute outage
    at 10k jobs held FOR UPDATE on ~3,000 job rows for 13.6 s, and the exec-processor's
    recompute_state for any of those jobs — i.e. their heartbeats — waited the whole time.
    Events are published per batch, so the first alerts go out after one batch, not after all."""
    nc, js = await events.connect()
    log.info("reconciler started", interval=settings.reconciler_interval_s, batch=RECOMPUTE_BATCH)
    while True:
        t0 = time.monotonic()
        try:
            with system_session() as s:
                touched = sorted(settle(s), key=str)   # stable order → predictable lock order
            changes = 0
            for i in range(0, len(touched), RECOMPUTE_BATCH):
                with system_session() as s:
                    changed = recompute(s, touched[i:i + RECOMPUTE_BATCH])
                for c in changed:   # after commit: never announce a state that could still roll back
                    await js.publish(f"jobstatus.{c['org_id']}", json.dumps(c).encode())
                changes += len(changed)
            if changes:
                log.info("reconciled", changes=changes, jobs=len(touched), took_s=round(time.monotonic() - t0, 2))
        except Exception as e:
            log.error("reconciler tick failed", error=str(e))
        await asyncio.sleep(max(0, settings.reconciler_interval_s - (time.monotonic() - t0)))


if __name__ == "__main__":
    asyncio.run(main())
