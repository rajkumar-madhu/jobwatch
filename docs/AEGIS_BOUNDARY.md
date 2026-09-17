# AEGIS-downstream boundary (R4)

JobWatch is **upstream** of AEGIS. AEGIS is one consumer among any the tenant configures.

## The rule
- Nothing downstream reads JobWatch's database, NATS streams, Redis, or internal APIs.
- Nothing in JobWatch imports from, calls, or depends on AEGIS. JobWatch runs and sells standalone.
- Telemetry leaves JobWatch through **one door**: tenant-configured *signal destinations*
  (`/api/v1/integrations/destinations`), carrying the `jobwatch.signal/1` envelope.
- A tenant's data goes only where that tenant pointed it. There is no platform-level fan-out to AEGIS.

## Contract: `jobwatch.signal` v1
Published at `GET /api/v1/integrations/schema` (with a live example).

```
{ schema:"jobwatch.signal", version:1, source:"wecrew-jobwatch",
  event_type, org_id, workspace_id, signal_id (ULID), occurred_at, emitted_at,
  subject:{job_id|incident_id|agent_id ...}, data:{...} }
```

| event_type | emitted from | data highlights |
|---|---|---|
| `job.state_changed` | jobstatus.* stream | four-state + prev, unknown_reason, legacy status, consecutive_failures, job projection |
| `slot.settled` | expected_runs poll (15s) | scheduled_for, state, execution_id, duration, exit_code, failure_reason, lateness_s |
| `incident.opened/updated/resolved` | incident_events poll | title, severity, status, affected_job_ids, correlation_signals |
| `agent.status` | agent liveness transitions | online, host, kind, jobs_affected |

Guarantees: envelope fields stable within a major; `data` additive-only; `signal_id` is the
idempotency key (retries repeat it); `occurred_at` (event) and `emitted_at` (send) are separate;
**never** stdout/stderr bodies, env, or secrets — only what was already redacted at ingest.

Delivery: HTTPS POST, `X-JobWatch-Signal-Id`, `X-JobWatch-Schema: jobwatch.signal/1`,
`X-JobWatch-Signature: sha256=<hmac hex of raw body>` when a secret is set. Celery retry ×6 with
backoff; a destination that dead-letters 20 signals in a row is auto-disabled with a visible reason.
Deliveries are ledgered (`signal_deliveries`, 30-day retention) and shown on the Integrations page.

Destination URLs pointing at loopback / link-local / `*.internal` are refused at creation.

## What AEGIS does with it (out of scope here)
OpsGraph ingests `job.state_changed` and `incident.*` as nodes/edges; ChangeGraph can join
`slot.settled` against deploy events. That mapping lives in AEGIS's connector, not in JobWatch.

## Open items
- `nats` destination kind is schema-present but delivery raises `not implemented` — needs an
  async bridge or a stream-to-stream forwarder. Webhook covers AEGIS today.
- slots/incidents/agents are polled every 15s from a process-local high-water mark; a restart
  re-establishes the mark at "now", so signals during the gap are not replayed. Move to NATS
  subjects with a durable consumer when a consumer needs replay.
- Per-destination rate limiting is not implemented; a `*/1` job on a flapping host can produce a
  lot of `slot.settled`. Consider coalescing per (destination, job) per minute.
- K8s agent does not call the heartbeat endpoint, so `agent.status` and per-agent offline
  detection only apply to the Linux agent today.
