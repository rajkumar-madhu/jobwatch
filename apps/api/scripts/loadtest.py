"""R19 — scale test for the paths that grow with job count.

Seeds a realistic tenant population straight into Postgres, then times each hot path on its own:

  generator   schedule_generator.tick() materialising slots for every job
  reconciler  reconciler.tick() settling a backlog of overdue slots (e.g. after downtime)
  recompute   job_state.recompute_state() per job, with execution history present
  processor   processor.process() — one heartbeat end to end in the DB
  api         the dashboard's heaviest reads for the largest tenant

Usage (DATABASE_URL = the jobwatch_app role, so RLS is on as in production):
  python scripts/loadtest.py --jobs 10000 --orgs 50 --history 30 --report ../../docs/LOADTEST.md

Numbers depend entirely on the box. They are for comparing before/after a change and for finding
paths that grow badly, not for capacity planning — the report says what hardware produced them.
Seeded orgs are named load-* and removed with purge_org() at the end (--keep to inspect).
"""
from __future__ import annotations

import argparse
import os
import platform
import statistics
import time
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

# schedule mix: (cron, share). Every-minute jobs are the expensive tail — 360 slots each per 6h horizon.
MIX = [("* * * * *", 0.05), ("*/5 * * * *", 0.25), ("0 * * * *", 0.40), ("30 2 * * *", 0.30)]


def seed(n_jobs: int, n_orgs: int, history: int) -> list[uuid.UUID]:
    from cronsentinel.db import system_session
    orgs = [uuid.uuid4() for _ in range(n_orgs)]
    t0 = time.perf_counter()
    with system_session() as s:
        for o in orgs:
            s.execute(text("INSERT INTO organizations (id, name, slug, plan) VALUES (:i, :n, :n, 'business')"), {"i": o, "n": f"load-{o.hex[:10]}"})
            s.execute(text("INSERT INTO workspaces (org_id, name) VALUES (:o, 'default')"), {"o": o})
        # Skewed tenant sizes: the first org holds a fifth of all jobs — the "big customer".
        big = n_jobs // 5
        per_rest = (n_jobs - big) // max(n_orgs - 1, 1)
        for idx, o in enumerate(orgs):
            n = big if idx == 0 else per_rest
            for cron, share in MIX:
                k = max(1, int(n * share))
                s.execute(text("""
                    INSERT INTO jobs (org_id, workspace_id, name, kind, heartbeat_token, schedule_expr, tz, grace_s,
                                      expected_runtime_s, status, job_state, created_at, last_run_at)
                    SELECT :o, w.id, 'job-' || md5(random()::text), 'cron', md5(random()::text) || md5(random()::text),
                           :cron, 'UTC', 60, 30, 'healthy', 'ok', now() - interval '3 days', now() - interval '5 minutes'
                    FROM workspaces w, generate_series(1, :k) WHERE w.org_id = :o"""), {"o": o, "cron": cron, "k": k})
        # Execution history: the table recompute_state and analytics read.
        s.execute(text("""
            INSERT INTO executions (id, org_id, job_id, status, scheduled_ts, server_received_ts, agent_ts_start, agent_ts_end, duration_ms, exit_code)
            SELECT 'load-' || j.id || '-' || g, j.org_id, j.id,
                   CASE WHEN random() < 0.03 THEN 'failed' ELSE 'success' END::exec_status,
                   ts, ts, ts, ts + interval '20 seconds', (random() * 60000)::int, 0
            FROM jobs j JOIN organizations o ON o.id = j.org_id AND o.slug LIKE 'load-%',
                 generate_series(1, :h) g,
                 LATERAL (SELECT now() - (g * interval '37 minutes') AS ts) t"""), {"h": history})
    n = count("SELECT count(*) FROM jobs j JOIN organizations o ON o.id=j.org_id WHERE o.slug LIKE 'load-%'")
    e = count("SELECT count(*) FROM executions WHERE id LIKE 'load-%'")
    print(f"seeded {n} jobs / {e} executions across {n_orgs} orgs in {time.perf_counter() - t0:.1f}s")
    return orgs


def count(sql: str) -> int:
    from cronsentinel.db import system_session
    with system_session() as s:
        return s.execute(text(sql)).scalar()


def bench_generator() -> dict:
    """Times the worker's real path: one short transaction per batch (run_batch), until caught up."""
    from cronsentinel.db import system_session
    from cronsentinel.workers.schedule_generator import run_batch
    ticks, total, t0 = [], 0, time.perf_counter()
    while True:
        a = time.perf_counter()
        with system_session() as s:
            nj, n = run_batch(s)
        ticks.append(time.perf_counter() - a)
        total += n
        if not nj or len(ticks) > 2000:
            break
    slots = count("SELECT count(*) FROM expected_runs er JOIN organizations o ON o.id=er.org_id WHERE o.slug LIKE 'load-%'")
    return {"batches": len(ticks), "wall_s": round(time.perf_counter() - t0, 1), "max_batch_s": round(max(ticks), 2),
            "slots_inserted": total, "slots_in_table": slots}


def bench_reconciler(minutes_down: int) -> dict:
    """Simulate the reconciler having been down: open slots whose grace and deadline have passed."""
    from cronsentinel.db import system_session
    from cronsentinel.workers import reconciler
    with system_session() as s:
        s.execute(text("""
            INSERT INTO expected_runs (org_id, job_id, scheduled_for, grace_until, deadline)
            SELECT j.org_id, j.id, gs, gs + interval '60 seconds', gs + interval '90 seconds'
            FROM jobs j JOIN organizations o ON o.id=j.org_id AND o.slug LIKE 'load-%',
                 generate_series(date_trunc('minute', now()) - make_interval(mins => :m), date_trunc('minute', now()) - interval '3 minutes', interval '5 minutes') gs
            WHERE j.schedule_expr IN ('* * * * *', '*/5 * * * *')
            ON CONFLICT DO NOTHING"""), {"m": minutes_down})
    backlog = count("""SELECT count(*) FROM expected_runs er JOIN organizations o ON o.id=er.org_id
                       WHERE o.slug LIKE 'load-%' AND er.state IN ('pending','late') AND er.deadline < now()""")
    # Times the worker's real path (R20): settle in one transaction, then recompute in batches.
    # max_lock_s is the longest single transaction — how long a heartbeat could wait on a job row.
    ticks, changes, t0 = [], 0, time.perf_counter()
    while True:
        a = time.perf_counter()
        with system_session() as s:
            touched = sorted(reconciler.settle(s), key=str)
        ticks.append(time.perf_counter() - a)
        for i in range(0, len(touched), reconciler.RECOMPUTE_BATCH):
            b = time.perf_counter()
            with system_session() as s:
                changes += len(reconciler.recompute(s, touched[i:i + reconciler.RECOMPUTE_BATCH]))
            ticks.append(time.perf_counter() - b)
        remaining = count("""SELECT count(*) FROM expected_runs er JOIN organizations o ON o.id=er.org_id
                             WHERE o.slug LIKE 'load-%' AND er.state IN ('pending','late') AND er.deadline < now()""")
        if not remaining or len(ticks) > 500:
            break
    return {"backlog_slots": backlog, "transactions": len(ticks), "wall_s": round(time.perf_counter() - t0, 1),
            "max_lock_s": round(max(ticks), 2), "state_changes": changes, "remaining": remaining}


def bench_recompute(samples: int) -> dict:
    from cronsentinel.db import system_session
    from cronsentinel.job_state import recompute_state
    with system_session() as s:
        jobs = s.execute(text("""SELECT j.id, j.org_id FROM jobs j JOIN organizations o ON o.id=j.org_id
                                 WHERE o.slug LIKE 'load-%' ORDER BY random() LIMIT :n"""), {"n": samples}).all()
    lat = []
    for jid, oid in jobs:
        a = time.perf_counter()
        with system_session() as s:
            recompute_state(s, jid, oid)
        lat.append((time.perf_counter() - a) * 1000)
    return _pcts(lat)


def bench_processor(events: int) -> dict:
    from cronsentinel.db import system_session
    from cronsentinel.processor import process
    with system_session() as s:
        jobs = s.execute(text("""SELECT j.id, j.org_id FROM jobs j JOIN organizations o ON o.id=j.org_id
                                 WHERE o.slug LIKE 'load-%' ORDER BY random() LIMIT :n"""), {"n": events}).all()
    lat = []
    for jid, oid in jobs:
        now = datetime.now(UTC).isoformat()
        ev = {"org_id": str(oid), "job_id": str(jid), "kind": "success", "execution_id": f"lt-{uuid.uuid4().hex}",
              "sequence": 1, "agent_ts": now, "server_ts": now, "duration_ms": 900, "exit_code": 0, "meta": {}}
        a = time.perf_counter()
        with system_session() as s:
            process(s, ev)
        lat.append((time.perf_counter() - a) * 1000)
    r = _pcts(lat)
    r["events_per_s_single_worker"] = round(1000 / statistics.mean(lat), 1)
    return r


def bench_api(big_org: uuid.UUID, reps: int) -> dict:
    from fastapi.testclient import TestClient
    from cronsentinel.auth import generate_api_key, hash_secret
    from cronsentinel.db import system_session
    from cronsentinel.main import app
    with system_session() as s:
        prefix, raw = generate_api_key()
        s.execute(text("INSERT INTO api_keys (org_id, name, prefix, key_hash, role) VALUES (:o,'load',:p,:h,'viewer')"),
                  {"o": big_org, "p": prefix, "h": hash_secret(raw)})
    c = TestClient(app)
    out = {}
    for path in ("/api/v1/jobs?limit=50", "/api/v1/analytics/overview", "/api/v1/analytics/jobs", "/api/v1/topology", "/api/v1/incidents"):
        lat = []
        for _ in range(reps):
            a = time.perf_counter()
            r = c.get(path, headers={"X-API-Key": raw})
            lat.append((time.perf_counter() - a) * 1000)
            assert r.status_code == 200, (path, r.status_code, r.text[:200])
        out[path] = _pcts(lat)
    return out


def _pcts(xs: list[float]) -> dict:
    xs = sorted(xs)
    q = lambda p: round(xs[min(len(xs) - 1, int(p * len(xs)))], 1)
    return {"n": len(xs), "p50_ms": q(0.50), "p95_ms": q(0.95), "max_ms": round(xs[-1], 1)}


def cleanup(orgs):
    from cronsentinel.db import system_session
    with system_session() as s:
        for o in orgs:
            s.execute(text("SELECT purge_org(:o)"), {"o": o})
            s.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": o})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=10000)
    ap.add_argument("--orgs", type=int, default=50)
    ap.add_argument("--history", type=int, default=30, help="executions per job")
    ap.add_argument("--down-minutes", type=int, default=30, help="reconciler outage to simulate")
    ap.add_argument("--samples", type=int, default=300)
    ap.add_argument("--only", default="generator,reconciler,recompute,processor,api")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    orgs = seed(a.jobs, a.orgs, a.history)
    results = {}
    try:
        for name in a.only.split(","):
            t = time.perf_counter()
            results[name] = {
                "generator": bench_generator, "reconciler": lambda: bench_reconciler(a.down_minutes),
                "recompute": lambda: bench_recompute(a.samples), "processor": lambda: bench_processor(a.samples),
                "api": lambda: bench_api(orgs[0], 20),
            }[name]()
            print(f"{name:11s} {time.perf_counter() - t:6.1f}s  {results[name]}")
    finally:
        if not a.keep:
            cleanup(orgs)
    print(f"\nbox: {platform.machine()} {os.cpu_count()} cpu | jobs={a.jobs} orgs={a.orgs} history={a.history}")


if __name__ == "__main__":
    main()
