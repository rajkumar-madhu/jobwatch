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

## Open findings (not fixed)

- **Analytics reads are 0.6–0.9 s for a 2k-job tenant** and the dashboard polls every 15 s. With
  many concurrent users that is significant DB load. Candidates: short-TTL caching or a rollup
  table maintained by a worker.
- **Backfill semantics after a generator outage.** A job with no slots gets up to 2 days of past
  slots, which the reconciler then marks missed — a missed-alert storm after a long outage, even
  for runs that did happen but were never attached to a slot. R3 behaviour; needs a decision
  (e.g. backfill only as far as the last recorded execution).
- **Not load-tested:** ingest HTTP throughput under concurrency, NATS consumer lag, the notifier
  under an alert storm, and partition growth / retention at months of data.
