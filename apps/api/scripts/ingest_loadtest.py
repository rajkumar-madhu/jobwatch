"""R22 — ingest under concurrent HTTP load, as agents and heartbeat clients actually call it.

R19 timed processor.process() directly, which skipped HTTP, auth and the event loop — and missed
that every agent request ran argon2 (fixed in R21). This drives the running ingest service:

  pings    GET /ping/{token}                     — cron one-liners (curl on success)
  batches  POST /agent/v1/events, N events each  — the Linux agent's wrapper flush
  probe    GET /healthz at a fixed rate          — head-of-line blocking detector: if the
                                                    event loop is blocked, this latency shows it

Needs ingest running (scripts/fullstack.sh up) and DATABASE_URL for seeding. The load generator
shares the box with the server, so absolute numbers understate a real deployment; compare runs.

  python scripts/ingest_loadtest.py --seconds 20 --concurrency 20 --batch 50
"""
from __future__ import annotations

import argparse
import asyncio
import os
import random
import statistics
import time
import uuid

import httpx
from sqlalchemy import text

INGEST = f"http://127.0.0.1:{os.getenv('INGEST_PORT', '18010')}"


def seed(n_jobs: int, n_agents: int):
    from cronsentinel.auth import hash_secret
    from cronsentinel.db import system_session
    org = uuid.uuid4()
    with system_session() as s:
        s.execute(text("INSERT INTO organizations (id, name, slug, plan) VALUES (:i, :n, :n, 'business')"), {"i": org, "n": f"load-{org.hex[:10]}"})
        ws = s.execute(text("INSERT INTO workspaces (org_id, name) VALUES (:o, 'd') RETURNING id"), {"o": org}).scalar()
        toks = s.execute(text("""INSERT INTO jobs (org_id, workspace_id, name, kind, heartbeat_token, status, job_state)
            SELECT :o, :w, 'ij-' || g, 'heartbeat', md5(random()::text) || g, 'healthy', 'ok' FROM generate_series(1, :n) g
            RETURNING heartbeat_token"""), {"o": org, "w": ws, "n": n_jobs}).scalars().all()
        agents = []
        for i in range(n_agents):
            key = "csa_" + uuid.uuid4().hex + uuid.uuid4().hex
            aid = s.execute(text("""INSERT INTO agents (org_id, kind, host_id, name, version, status, key_hash, key_prefix, last_seen_at)
                VALUES (:o, 'linux', :h, :n, 'load', 'active', :kh, :kp, now()) RETURNING id"""),
                {"o": org, "h": f"load-host-{i}", "n": f"load-{i}", "kh": hash_secret(key), "kp": key[:12]}).scalar()
            agents.append((str(aid), key))
    return org, toks, agents


def cleanup(org, attempts: int = 10):
    """The exec-processor is still draining this org's backlog when the run ends and keeps writing
    rows for it, so a single purge can race it. Retry until the org is gone."""
    from cronsentinel.db import system_session
    for i in range(attempts):
        try:
            with system_session() as s:
                s.execute(text("SELECT purge_org(:o)"), {"o": org})
                s.execute(text("DELETE FROM agents WHERE org_id=:o"), {"o": org})
                s.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": org})
            return
        except Exception:
            time.sleep(3)
    print(f"WARNING: could not remove load org {org}; purge it manually")


async def run(seconds, concurrency, batch, toks, agents, batch_share):
    lat = {"ping": [], "batch": [], "probe": []}
    errs = {"ping": 0, "batch": 0, "probe": 0}
    events_sent = 0
    stop = time.monotonic() + seconds
    limits = httpx.Limits(max_connections=concurrency + 5, max_keepalive_connections=concurrency + 5)
    async with httpx.AsyncClient(base_url=INGEST, timeout=30, limits=limits) as c:
        async def worker():
            nonlocal events_sent
            while time.monotonic() < stop:
                a = time.perf_counter()
                if random.random() < batch_share:
                    aid, key = random.choice(agents)
                    evs = [{"job_token": random.choice(toks), "kind": "success", "execution_id": f"il-{uuid.uuid4().hex}",
                            "sequence": 1, "agent_ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "duration_ms": 900, "exit_code": 0}
                           for _ in range(batch)]
                    r = await c.post("/agent/v1/events", json={"events": evs}, headers={"X-Agent-Id": aid, "X-Agent-Key": key})
                    kind = "batch"
                    if r.status_code == 200: events_sent += batch
                else:
                    r = await c.get(f"/ping/{random.choice(toks)}"); kind = "ping"
                    if r.status_code in (200, 202): events_sent += 1
                lat[kind].append((time.perf_counter() - a) * 1000)
                if r.status_code >= 400: errs[kind] += 1

        async def probe():
            while time.monotonic() < stop:
                a = time.perf_counter()
                try:
                    r = await c.get("/healthz"); ok = r.status_code == 200
                except Exception:
                    ok = False
                lat["probe"].append((time.perf_counter() - a) * 1000)
                if not ok: errs["probe"] += 1
                await asyncio.sleep(0.1)

        t0 = time.monotonic()
        await asyncio.gather(probe(), *[worker() for _ in range(concurrency)])
        wall = time.monotonic() - t0
    return lat, errs, events_sent, wall


def pct(xs, p):
    xs = sorted(xs); return round(xs[min(len(xs) - 1, int(p * len(xs)))], 1) if xs else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=20)
    ap.add_argument("--concurrency", type=int, default=20)
    ap.add_argument("--batch", type=int, default=50)
    ap.add_argument("--batch-share", type=float, default=0.2, help="fraction of requests that are agent batches")
    ap.add_argument("--jobs", type=int, default=2000)
    ap.add_argument("--agents", type=int, default=20)
    a = ap.parse_args()
    org, toks, agents = seed(a.jobs, a.agents)
    try:
        lat, errs, n, wall = asyncio.run(run(a.seconds, a.concurrency, a.batch, toks, agents, a.batch_share))
        report(a, lat, errs, n, wall)
    finally:
        cleanup(org)


def report(a, lat, errs, n, wall):
    reqs = sum(len(v) for k, v in lat.items() if k != "probe")
    print(f"{reqs} requests / {n} events in {wall:.1f}s -> {reqs / wall:.0f} req/s, {n / wall:.0f} events/s  (concurrency {a.concurrency}, batch {a.batch})")
    for k in ("ping", "batch", "probe"):
        if lat[k]:
            print(f"  {k:6s} n={len(lat[k]):5d} p50={pct(lat[k], .5):7.1f} p95={pct(lat[k], .95):7.1f} p99={pct(lat[k], .99):7.1f} max={max(lat[k]):7.1f} ms  errors={errs[k]}")


if __name__ == "__main__":
    main()
