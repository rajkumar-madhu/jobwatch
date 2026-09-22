# Load test (R19)

`apps/api/scripts/loadtest.py` seeds a tenant population straight into Postgres and times each path
that grows with job count, on its own. Run it as the `jobwatch_app` role so RLS is on:

```bash
cd apps/api
python scripts/loadtest.py --jobs 10000 --orgs 50 --history 30
python scripts/loadtest.py --only generator            # one path
python scripts/loadtest.py --keep                      # leave the load-* orgs for inspection
```

Seeded orgs are named `load-*` and removed with `purge_org()` unless `--keep`.

**These numbers are a floor, not a capacity plan.** They come from a 1 vCPU / 3 GB sandbox with
Postgres, NATS and Redis on the same box. Use them to compare before/after a change and to find
paths that grow badly. Re-run on production-shaped hardware before sizing anything.

## Population

- 10,000 jobs (9,889 after rounding) across 50 orgs, skewed: the first org holds 2,000 — the
  "big customer" whose dashboard reads are timed.
- Schedule mix: 5% every minute, 25% every 5 min, 40% hourly, 30% daily.
- 30 executions per job (296,670 rows), 3% failed.
- Jobs are created three days ago with no slots, so the generator run is the **worst case**:
  every job catching up the full 2-day backfill plus the 6-hour horizon.

## Results

| Path | Result |
|---|---|
| Generator, worst-case catch-up | 2,799,174 slots in 194 s (51 batches, max batch 16.7 s before the slot budget) |
| Reconciler, 30-minute outage backlog | 17,712 overdue slots settled in one 13.6 s tick; 2,952 state changes |
| `recompute_state` (30 executions/job) | p50 3.9 ms, p95 4.5 ms |
| `processor.process`, single worker | p50 6.6 ms → ~148 events/s |
| `GET /jobs?limit=50` (2k-job tenant) | p50 179 ms, p95 217 ms |
| `GET /topology` | p50 199 ms, p95 263 ms |
| `GET /analytics/overview` | p50 665 ms, p95 715 ms |
| `GET /analytics/jobs` | p50 635 ms, p95 890 ms |

Demand for this mix: roughly 1,070 runs/min, two events each (start + terminal) ≈ 36 events/s.
One processor worker on this box has ~4× headroom.

## What the first run found (fixed in R19)

The generator did not finish 5,000 jobs within 150 s. Three compounding causes:

1. **`slots_between` computed 2,000 occurrences eagerly** and then cut at the window end. An hourly
   job computed ~83 days of runs to keep 54; a daily job ~5.5 years to keep 2. About 95 ms per job
   regardless of how many slots it needed. Now lazy (`schedule.iter_runs`): hourly 96 → 2.5 ms,
   daily 96 → 0.24 ms.
2. **One INSERT round-trip per slot.** Now one `unnest()` INSERT per 20k-row chunk.
3. **The whole pass in one transaction holding `FOR UPDATE` on up to 5,000 job rows.**
   `recompute_state` locks the job row too, so heartbeats for those jobs waited behind the
   generator for as long as it ran. Now one short transaction per batch of ≤200 jobs, capped at
   50,000 slots per batch so no batch holds locks for long; jobs are revisited only when less
   than 30 minutes of materialised horizon remain; a pass never overruns into the next tick.

## R20 follow-ups (fixed)

| Change | Before | After |
|---|---|---|
| Reconciler: settle slots in one transaction, then recompute state in batches of 100 jobs, publishing per batch | one 13.6 s transaction holding `FOR UPDATE` on ~3,000 job rows | longest transaction 1.1 s (31 transactions); wall time ~15 s, unchanged |
| Synthetic `missed` executions | one INSERT per slot (17,712 round-trips) | one `WITH … UPDATE … INSERT … SELECT` statement |
| `recompute_state` at a week of every-minute history (10,080 executions/job) | p50 18.8 ms, p95 25.5 ms (3.9 ms at 30/job) | p50 9.0 ms, p95 11.2 ms with migration 0010's expression index |

Events are published only after each batch commits, so a state that could still roll back is
never announced. The index cannot be built `CONCURRENTLY` on a partitioned table; migration 0010's
docstring has the per-partition procedure for large production tables.

## R21 — the two platform-wide costs behind "slow analytics"

Investigating the analytics numbers before caching them found that most of the latency was not in
the analytics queries at all.

**1. RLS policies prevented index use for every tenant query.** Each tenant table had two
permissive policies, ORed by Postgres: `org_id = app_org_id() OR app_bypass()`. The column-less
second arm stops the planner using the `org_id` index, so tenant queries scanned *all tenants'*
rows and filtered — a 7-day executions count for one tenant read 386k rows and discarded 326k
(247 ms). Cost grew with the whole platform's data, not the tenant's. Migration 0011 moves system
sessions to a `jobwatch_system` role with its own policy; the app role now sees only
`org_id = app_org_id()`, which is an index condition (same query: 5.6 ms).

**2. Argon2 on every machine-authenticated request.** API keys and agent keys were verified with
argon2 (~181 ms on this box) on every request — including every agent heartbeat, where the Redis
cache held the hash but not the verification. That put a ~180 ms floor under every API-key request,
capped a core at ~5 agent requests/s (the 10k mix needs ~36/s), and let anyone who knew a key's
public prefix burn 180 ms of CPU per request ahead of the rate limiter. These are 256-bit
server-generated tokens; they now use SHA-256, with legacy argon2 hashes accepted and upgraded in
place on first use.

| Endpoint (2k-job tenant) | R19 p50 | R21 p50 |
|---|---|---|
| `/jobs?limit=50` | 179 ms | 19.6 ms |
| `/topology` | 199 ms | 18.2 ms |
| `/incidents` | ~188 ms | 5.2 ms |
| `/analytics/jobs` | 635 ms | 125 ms |
| `/analytics/overview` | 665 ms | 193 ms |
| `recompute_state` | 3.9 ms | 2.4 ms |
| processor, single worker | 148 ev/s | 194 ev/s |

No caching was added: at these numbers it is not yet needed, and caching the slow version would
have hidden both problems. The integration suite went from ~190 s to 28 s — argon2 was most of it.

## R22 — ingest under concurrent HTTP load

`apps/api/scripts/ingest_loadtest.py` drives the running ingest service the way clients do: cron
one-liner pings (`GET /ping/{token}`), agent event batches (`POST /agent/v1/events`), and a
`/healthz` probe every 100 ms as a head-of-line-blocking detector. Load generator, ingest,
Postgres, NATS and six workers all share the one vCPU, so these understate a real deployment.

| Scenario | Before | After |
|---|---|---|
| 20 concurrent, 20% batches of 50 | 620 events/s, batch p50 1,470 ms | **1,447 events/s**, batch p50 470 ms |
| 20 concurrent, pings only | 219 req/s | 186 req/s (noise, or the threadpool hop on 1 vCPU — not a win) |
| 60 concurrent, 30% batches | not run on the old code | 1,124 events/s, 0 errors; p95 ~2.2 s (CPU-saturated) |

**What was wrong in `/agent/v1/events`:** one un-cached lookup query per event (pings use Redis,
batches did not), and a DB session — with its pooled connection — held open across every
`await js.publish` for the whole batch (~1.5 s for 50 events). Now tokens and fingerprints resolve
in two `= ANY(...)` queries in a threadpool, the connection is released, then events publish in
order (a batch can hold start + success for one execution). Heartbeat token resolution and the
rate-limit `INCR` also moved off the event loop.

**Corrections to my own predictions, recorded so they are not repeated:**
- I expected head-of-line stalls of ~1.4 s. The probe peaked ~200 ms: each `await publish` yields
  the loop between events, so the blocking was fine-grained. Real, but smaller than predicted.
- Connection-pool exhaustion at ~30 concurrent batches is inferred from pool size × hold time;
  the old code was not run at that concurrency, so it is not a demonstrated before/after.

**No test covered `/agent/v1/events` before R22** — the R9 route smoke suite walks the main API,
not ingest. `tests/integration/test_agent_events.py` now does (token and fingerprint resolution,
unknown jobs dropped, progress stored, start+success ordering, cross-org isolation, bad key). It
passes on both the old and new handler, so the rewrite is behaviour-preserving.

**Deployment:** ingest runs one uvicorn process by default. `INGEST_WORKERS` in compose adds
processes; multi-worker throughput was not measured here.

## R23 — retention and hourly housekeeping

The hourly scorer (reliability score, usage metering, partitions, retention) ran as **one
transaction**. The score `UPDATE` locks every job row until commit, and `recompute_state` — every
heartbeat — takes `FOR UPDATE` on job rows.

At 10k jobs with nothing to delete the tick took 1.1 s and a heartbeat waited 0.1 s: real shape,
no real harm. With one tenant downgraded business → free (365 → 7 days, 1,728,000 executions to
delete), heartbeats for its jobs **waited 3.6 s and 4.9 s** (probes fired 2.5 s and 4 s into the
tick). A first probe at 1 s showed only 0.1 s — it raced the score `UPDATE` and got the row first;
recorded because the first number was misleading.

| | Before | After |
|---|---|---|
| Heartbeat wait during a 1.7M-row retention delete | 3.6–4.9 s | max 0.06 s over 205 probes |
| Scorer wall time for that tick | 5.9–8.9 s | 53 s |

The wall-time increase is the trade: batched deletes (20k rows, each its own transaction) re-find
their rows every batch, ~32k rows/s here. Retention has a 10-minute budget per hour and resumes on
the next tick, so a very large downgrade drains over hours without blocking anything.

**Partition trap (fixed, migration 0012).** Once any row sits in `<parent>_default` for a month
without a partition, creating that partition fails with `CheckViolation`, every time. Reached by
scorer downtime across a month boundary, or by one agent with a skewed clock reporting a run
months ahead. Inside the old single transaction it stopped scoring, usage and retention
permanently. `ensure_month_partition` now moves stranded rows into the new partition and attaches
it; partitions are ensured three months ahead, each in its own transaction.

**Partition drop.** Retention is per plan (7–365 days, enterprise unlimited), so partitions cannot
be dropped by age — a month only becomes droppable once it is empty. `drop_partition_if_empty()`
(SECURITY DEFINER; the app roles cannot drop tables they do not own — found by the tests) drops
empty month partitions older than ~2 months. Enterprise data keeps old partitions alive, as it
should.

Also: retention deletes now take each batch's `execution_logs` with them, replacing an hourly
anti-join over the entire logs table; every phase is failure-isolated, so one broken phase logs an
error and the rest still run.

## R24 — usage metering

Hourly metering ran one `sum(length(content))` subquery per organisation over `execution_logs`,
which had **no index on `org_id`** — a full table scan per org, so cost = orgs × total log rows.
6.0 s at 98k rows / 50 orgs. The same missing index made tenant log reads full cross-tenant scans
(a common search term hid it: `LIMIT 200` stops early; the plan showed the seq scan).

Now an `org_id` index plus an append-only `storage_ledger` fed by statement-level triggers, folded
into `org_storage` hourly: metering **6.0 s → 0.03 s**. A ledger rather than a per-org counter row,
so a busy tenant's concurrent log writes don't serialise on one hot row. The meter now counts
bytes (`octet_length`); the old one counted characters. Tests assert the meter equals a full
recount after inserts, conflicts, updates, deletes, retention and purged orgs. One real bug caught
on the way: a log-less org made the metering statement insert NULL and fail for every org.

## Open findings (not fixed)

- **`/analytics/overview` shows one ~4 s request out of 20** in two separate runs (p95 = max), apparently
  the first after seeding. Not diagnosed — cold buffers or first-plan cost are guesses, not findings.
- **Backfill semantics after a generator outage.** A job with no slots gets up to 2 days of past
  slots, which the reconciler then marks missed — a missed-alert storm after a long outage, even
  for runs that did happen but were never attached to a slot. R3 behaviour; needs a decision
  (e.g. backfill only as far as the last recorded execution).
- **Not load-tested:** multi-worker / multi-replica ingest, NATS consumer lag, the notifier
  under an alert storm, and partition growth / retention at months of data.
