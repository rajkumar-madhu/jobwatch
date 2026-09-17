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
