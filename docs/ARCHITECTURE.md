# Architecture

## Services
| Service | Tech | Role |
|---|---|---|
| web | Next.js 15 | Dashboard, landing, onboarding, public status pages |
| api | FastAPI | Authenticated REST (`/api/v1`), OIDC session (`/auth`), public status (`/public`) |
| ingest | FastAPI | Heartbeats (`/ping`, `/heartbeat`, `/api/v1/heartbeat`) and agent endpoints (`/agent/v1`); scaled independently |
| exec-processor | Python, NATS pull consumer | `exec.*` → executions/events/logs, job state machine, emits `jobstatus.*` |
| reconciler | Python loop, 30s | LATE at grace, MISSED at 2×grace, TIMEOUT for runs over expected+grace |
| rule-engine | Python, NATS pull consumer | `jobstatus.*` → maintenance/business-hours/dedup/flapping → correlation → incidents → Celery tasks |
| notifier | Celery, queue `notify` | Email/Slack/Teams/Discord/Telegram/webhook with per-channel rate limit + ledger |
| scorer | Python loop, hourly | Reliability score, usage metering, partition creation, retention |
| NATS JetStream | work-queue stream `CS_EXEC` (`exec.>`, `jobstatus.>`) | Durable event bus |
| PostgreSQL 16 | RLS, monthly partitions on `executions`/`host_metrics` | System of record |
| Redis | rate limits, heartbeat token cache, agent key cache, bootstrap tokens, OAuth state, flapping windows, Celery broker |
| Keycloak | realm `cronsentinel` | Identity (email/password, Google/GitHub/Microsoft, SAML, MFA) |
| Ollama / vLLM | OpenAI-compatible | Copilot model, self-hosted by default |

## Data flow: a heartbeat
```
curl /ping/{token} → ingest: token→job (Redis) → rate limit → NATS exec.{org}
→ exec-processor: dedup (execution_id, sequence) → executions upsert → state machine → jobs.status → NATS jobstatus.{org}
→ rule-engine: rules match → correlation → incident → send_notification.delay()
→ notifier: decrypt channel config → send → notification_ledger + incident_events
→ web (polls every 15s) shows status/incident/ledger
```
## Data flow: a missed run
```
reconciler (30s): next_expected_at + grace < now and no start → LATE; at 2×grace → MISSED + synthetic execution + next_expected roll → jobstatus → rule-engine …
```
## Tenancy
`organizations` is the tenant root. Every tenant table has `org_id` + RLS policy; API sessions `SET LOCAL app.org_id`; workers use `app.bypass_rls='on'` and filter explicitly.

## Auth paths
Cookie `cs_session` (HS256, from Keycloak code flow) · `Authorization: Bearer` (Keycloak JWT + `X-Org-Id`) · `X-API-Key` (argon2-hashed, role-scoped) · `X-Agent-Key`+`X-Agent-Id` (agents, ingest tier only).

## R2/R3 — expected-run engine and four-state model

### Why the old model was wrong
`jobs.next_expected_at` was a single mutable column, rolled forward whenever a run arrived. Three
failures fell out of that:

1. **Lost misses.** If the reconciler was down when a run was due, the column had already moved on
   by the time it came back — the missed run was never recorded.
2. **Silent drift.** A job that consistently started 10 minutes late pushed its own expectation
   forward each run, so it never looked late. The schedule was effectively redefined by the job.
3. **No slot identity.** Overlapping runs could not be attributed to the occurrence they belonged
   to, so "did the 02:00 run succeed?" was unanswerable.

### What replaces it
`expected_runs` materialises every slot a schedule should produce, ahead of time
(`schedule-generator`, every 60s, 6h horizon, 2-day backfill cap, unique on `(job_id, scheduled_for)`).
Slots are generated **independently of executions**, so:

- an outage delays detection but never loses it — overdue slots are still `pending` on restart
- lateness is measured against the schedule, not against the last run
- executions bind to a slot via a match window (`scheduled_for - 90s` .. `deadline`)

Slot lifecycle: `pending → running → succeeded | failed`, or `pending → late → missed`, or
`→ skipped` when the slot fell inside a maintenance window or the job was paused.

### Four states
`OK | LATE | FAILING | UNKNOWN`, derived in `cronsentinel/states.py` (pure). The legacy nine-value
`jobs.status` is still written, derived from the four-state, so existing UI, filters and status
pages keep working unchanged.

**UNKNOWN-visibility suppression** is the operational point: UNKNOWN means *we cannot make a
statement*, and never alerts. Previously a dead agent marched every job it owned through
HEALTHY → LATE → MISSED and fired one page per job. Those jobs are now `unknown/agent_offline` —
the alert belongs to the agent, once, not to N jobs.

### Open items
- `expected_runs` retention is not wired into the scorer's partition-drop path yet — slots
  accumulate. Drop partitions on the same per-plan schedule as executions.
- Slot generation is single-worker (`FOR UPDATE SKIP LOCKED` makes it safe to run more, but the
  `generated_through` cursor has not been load-tested with concurrent generators).
- Maintenance-window matching in the reconciler expands one-off windows only; rrule recurrence is
  still a stub (unchanged from Phase 2).
- `agent_offline` uses a fixed 10-minute threshold; should be per-agent, derived from its reported
  heartbeat interval.

## R6 — messaging topology

Two JetStream streams, not one:

| stream | subjects | retention | consumers |
|---|---|---|---|
| `CS_EXEC` | `exec.>` | WORK_QUEUE | `exec-processor` only (scale by adding workers on the same durable) |
| `CS_STATUS` | `jobstatus.>` | INTEREST | `rule-engine`, `outbound-exporter`, and any future subscriber |

A WORK_QUEUE stream allows **one** filtered consumer per subject and deletes a message as soon as
any consumer acks it. With both subjects on one work-queue stream, R4's outbound-exporter failed to
start (`filtered consumer not unique on workqueue stream`) and, had it started, would have stolen
status messages from the rule engine. Found by the R6 pipeline harness; `events.ensure_streams`
creates both and attempts `update_stream` so a pre-split deployment repairs itself on restart.

### One place derives job state
`cronsentinel/job_state.py::recompute_state` is called by **both** producers on `jobstatus.*` —
the reconciler (slot timeouts) and the processor (an execution arriving). Previously the processor
wrote only the legacy `status` column, so a genuinely failing job kept `job_state = 'unknown'` and
UNKNOWN-suppression dropped its alert. Jobs with no slots (heartbeat-only, ad-hoc) fall back to
their last execution so their state is still derivable; they simply can never be LATE or MISSED.

### jobstatus.* payload contract
Both producers emit: `org_id, job_id, execution_id?, prev_status, new_status, prev_state,
new_state, unknown_reason, consecutive_failures, occurred_at`. The rule engine reads `org_id` and
the state fields; the outbound exporter maps it to `job.state_changed`. Before R6 the processor
omitted `org_id` entirely, so every status change from an execution crashed the rule engine.
