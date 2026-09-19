# Development (Phase 1)

## Services
| Service | Port | Entry |
|---|---|---|
| API | 8000 | `cronsentinel.main:app` |
| Ingest | 8010 | `cronsentinel.ingest_main:app` |
| exec-processor | — | `python -m cronsentinel.workers.exec_processor` |
| reconciler | — | `python -m cronsentinel.workers.reconciler` |

## Smoke test
```bash
python -m cronsentinel.cli bootstrap --org acme --email you@example.com   # prints api_key + workspace_id
export KEY=cs_...; export WS=...
curl -s -X POST localhost:8000/api/v1/jobs -H "X-API-Key: $KEY" -H 'content-type: application/json' \
  -d '{"workspace_id":"'$WS'","name":"nightly-backup","schedule_expr":"*/2 * * * *","grace_s":30,"expected_runtime_s":60}'
# → heartbeat_token in response
curl -s localhost:8010/heartbeat/<token>/start
curl -s -X POST localhost:8010/api/v1/heartbeat/<token> -H 'content-type: application/json' -d '{"status":"success","duration_ms":1200}'
curl -s localhost:8000/api/v1/jobs -H "X-API-Key: $KEY"        # status: healthy
# wait > 2min + 2*grace without pinging → status: late → missed
curl -s localhost:8000/api/v1/analytics/overview -H "X-API-Key: $KEY"
```

## Tests
`cd apps/api && pytest` — state machine, schedule, redaction run without DB; tenant-isolation needs `DATABASE_URL`.

## Known stubs (Phase 1)
- `/metrics` returns placeholder — Prometheus registry in Phase 2.
- Keycloak JWT path implemented but realm/clients not provisioned — see Phase 5 onboarding.
- `jobstatus.*` NATS subject is published but has no consumer until Phase 2 rule engine.
- MTTD/MTTR in overview — Phase 6.

## Phase 2 — Alerting

New services: `rule-engine` (NATS `jobstatus.>` consumer) and `notifier` (Celery, queue `notify`).

### Smoke
```bash
# 1. Slack channel (config encrypted at rest, never returned)
curl -s -X POST localhost:8000/api/v1/alerts/channels -H "X-API-Key: $KEY" -H 'content-type: application/json' \
  -d '{"kind":"slack","name":"ops","config":{"webhook_url":"https://hooks.slack.com/services/..."}}'
curl -s -X POST localhost:8000/api/v1/alerts/channels/<channel_id>/test -H "X-API-Key: $KEY"

# 2. Rule: any job failed/missed → slack, repeat every 30 min, weekday 9-18 IST
curl -s -X POST localhost:8000/api/v1/alerts/rules -H "X-API-Key: $KEY" -H 'content-type: application/json' \
  -d '{"name":"prod failures","condition":"failed","scope":{"tags":["prod"]},"severity":"high","channel_ids":["<channel_id>"],
       "repeat_interval_s":1800,"business_hours":{"tz":"Asia/Kolkata","days":[1,2,3,4,5],"start":"09:00","end":"18:00"}}'

# 3. Fail a job → incident opens, Slack message sent once
curl -s localhost:8010/heartbeat/<token>/fail
curl -s localhost:8000/api/v1/incidents -H "X-API-Key: $KEY"
curl -s localhost:8000/api/v1/alerts/ledger -H "X-API-Key: $KEY"

# 4. Recover → incident auto-resolves
curl -s localhost:8010/heartbeat/<token>/success
```

Conditions: `failed | missed | late | runtime_exceeded | recovered | consecutive_failures{count} | sla_breach`.
Scope keys: `job_ids | tags | environment_ids | workspace_ids | team_ids` (empty = all).
Suppression order: maintenance window → business hours → dedup/repeat_interval → flapping collapse (3 changes/10 min → one "flapping" alert).
Channels: email, slack, teams, discord, telegram, webhook (HMAC `X-JobWatch-Signature`). PagerDuty/Opsgenie/SMS = Phase 6.

### Phase 2 stubs
- Maintenance `rrule` stored but not expanded (one-off windows only).
- Worker-side metrics (ingest lag, reconciler lag) not exported — API HTTP metrics only.
- Escalation policies (`teams.escalation_policy`) not consumed yet.

## Phase 4a — Web app (Overview, Jobs, Job detail) + demo data
```bash
cd apps/api && python -m cronsentinel.seed --org <org_id>     # 7 days of runs, 7 jobs, 1 open incident
cd apps/web && npm install && npm run dev                      # http://localhost:3000 → Settings → paste API key
```
Keyboard: `⌘K` palette · `g o/j/f/i/l` jump to Overview/Jobs/Failures/Incidents/Logs · dark mode via palette.
Design: IBM Plex Sans/Mono, cool-neutral light base with dark toggle (D16). Single visual grammar: status dot + proportional run strip.
Phase 4b delivers Failures, Incidents (+detail), Logs explorer, Servers & agents, Alerting settings.

## Phase 4b — remaining app screens
- **Failures** — worst-first (failed → timeout → missed → late) with plain-English "why it's here", one-click stderr; recovered section.
- **Incidents** — open/acknowledged/resolved tabs; detail: timeline, notes, runs since start, affected jobs, who was notified, ack/resolve with root cause.
- **Logs** — `GET /api/v1/logs/search` (ILIKE; TODO pg_trgm/ClickHouse): text, job, stream, outcome, host, time range; match highlighting; stderr auto-expanded.
- **Servers & agents** — mint bootstrap token → install command, last-seen/skew/version, rotate/revoke.
- **Alerting** — channels (6 kinds, encrypted config, send test), rules (condition/tags/severity/repeat/channels), recent deliveries ledger.

## Phase 5 — Landing, onboarding, sign-in, status pages
- Routes: `/welcome` (landing), `/login`, `/onboarding` (6 steps), `/tools/cron` (public parser), `/status/{slug}` (public), dashboard under `(app)` group.
- Sign-in: `GET /auth/login` → Keycloak → `/auth/callback` → httpOnly `cs_session` cookie (HS256, 12h). API accepts cookie, bearer, or X-API-Key.
- Keycloak realm auto-imported from `infra/keycloak-realm.json` (client `cronsentinel-web`, secret `change-me` — rotate). Google/GitHub/Microsoft IdPs are present but disabled until client IDs are filled. Email verification + TOTP required-action enabled.
- Onboarding: creates org + owner + 14-day Team trial marker (`subscriptions.trial_ends_at`; plan enforcement still reads `organizations.plan` — TODO wire trial → plan).
- Status pages: `POST /api/v1/status-pages` (admin) → `GET /public/status/{slug}` (no auth), 60-run strip, 90-day uptime, incidents 30d, maintenance. Private pages TODO (signed link).
- Callback verifies id_token claims via TLS only — TODO JWKS signature check (helper exists in auth.py `_jwks`).

## Phase 6a — Kubernetes, correlation, dependencies, topology
- `agents/k8s-agent` (Go, client-go informers on CronJobs/Jobs/Pods/Events): discovery → `/agent/v1/k8s/cronjobs`; Job start/success/fail + pod reasons (ImagePullBackOff, CrashLoopBackOff, OOMKilled, Evicted, DeadlineExceeded) + warning events → `/agent/v1/k8s/events`. **Not compiled** (no Go toolchain here); client-go pins v0.31.3.
- `infra/helm/cronsentinel-agent`: cluster or namespace scope, read-only RBAC, bootstrap-token Secret or `existingSecret`, hardened pod.
- Migration 0003: `k8s_events`, `k8s_cronjobs.uid/image/command`, `executions.failure_reason`, `incidents.correlation_signals`.
- Correlation (`alerting/correlation.py`): 5-min window; joins an open incident on same host / cluster+namespace / deploy_version / dependency chain; title becomes "N related jobs failing"; infra anomalies (io_wait>100ms, disk>90%, cpu>90%) recorded as signals.
- Dependencies: `POST/DELETE /api/v1/dependencies` (cycle-checked), `GET /jobs/{id}/impact` (transitive downstream). Topology: `GET /api/v1/topology` worst-first rollup.
- Web: Kubernetes page (clusters → namespaces → CronJobs with spec fields + last failure reason + warning events), Topology page (two trees + layered DAG), job page "depends on / downstream impact", incident page "downstream at risk" + grouping reasons.
- K8s agent has no disk buffer yet (re-queues in memory) — TODO port `linux-agent/internal/buffer`.

## Phase 6b — Copilot, reliability, analytics, billing
- Copilot: `copilot/context.py` builds per-job telemetry (12 runs, stderr tails, 30-day stats, 48h host metrics, K8s warnings, deps, sibling failures) → `redact()` → `copilot/llm.py` (OpenAI-compatible default for vLLM/Ollama, Anthropic adapter) → strict JSON answer schema → `copilot_sessions`; sets incident `root_cause` if empty. Gated to plans with `ai`. Compose profile `ai` runs Ollama.
- Scorer worker (hourly): reliability formula from design §4.3 in one UPDATE; `usage_records`; next-month partitions; plan retention.
- Analytics API: `series` (volume/failures/p50/p95/incidents), `jobs` (success, p95, drift, SLA met, est. cost), `mttr` (MTTD/MTTA/MTTR), `report` (daily/weekly/monthly with previous-period delta). Overview now includes MTTD/MTTR.
- Billing: REST-only Stripe (checkout session, customer portal, HMAC-verified webhook with event dedup); plan on `organizations.plan` updated by webhook; `plan_limits.stripe_price_id` must be filled. Trial marker exists but trial→Team enforcement TODO.
- Web: AI Copilot page (scoped from job/incident pages, evidence/actions/commands, history), Analytics page (4 charts, per-job table, printable report), Billing page (plan, usage bar, upgrade/downgrade, portal).
- Platform Helm chart `infra/helm/cronsentinel` + CI workflow (`.github/workflows/ci.yml`: api with PG service, web build, Go vet/test/build).

## Known stubs (whole project)
`/metrics` worker lag gauges · JWKS verification on id_token · rrule maintenance windows · PagerDuty/Opsgenie/SMS senders · passive-mode exit codes · K8s agent disk buffer · pg_trgm/ClickHouse log search · escalation policies · private status pages · partition-drop retention · CSRF token for cookie POSTs · Go code uncompiled in this delivery · e2e/frontend component tests.

## R2/R3 — correctness retrofit + schedule engine

New: `alembic/versions/0005_schedule_engine.py` (expected_runs partitioned + RLS, job_state/unknown_reason/
generated_through/consecutive_failures on jobs, executions.expected_run_id, backfill from legacy status),
`cronsentinel/states.py` (pure four-state), `cronsentinel/schedule_engine.py` (pure slot maths),
`cronsentinel/workers/schedule_generator.py` (new service, 60s), rewritten `workers/reconciler.py`,
`processor.attach_slot`, UNKNOWN suppression in `alerting/rules.py` + `alerting/engine.py`.

Wired into `infra/docker/docker-compose.yml` and the Helm workloads list as `schedule-generator`.

Tests: `tests/test_states.py` (10), `tests/test_schedule_engine.py` (9) — all pure, no DB. 40 passed / 1 skipped.

### Not verified
- Migration 0005 has not been run against a live Postgres in this sandbox (no DB available); the
  partition + RLS statements follow the same shape as 0001 but need `alembic upgrade head` on a real
  instance before trusting the backfill UPDATE.
- The reconciler's maintenance-window SQL uses `jsonb ?` containment against `scope`; verify against
  real maintenance rows, the Phase 2 scope shape was only exercised through the Python path.
- No integration test binds an execution to a slot end-to-end — needs the live-PG harness that
  `tests/test_tenant_isolation.py` already skips on.

## R4 — AEGIS-downstream boundary + R3 open items

New: `alembic/versions/0006_outbound_signals.py` (signal_destinations, signal_deliveries, agents.heartbeat_interval_s,
plan_limits.expected_runs_retention_days), `cronsentinel/outbound/{schema,deliver}.py`, `workers/outbound_exporter.py`
(new service), `routers/integrations.py`, web `/integrations` page, `docs/AEGIS_BOUNDARY.md`.

Closed R3 open items: expected_runs retention + partition creation in scorer; agent-offline threshold is now
3× the agent's own reported `heartbeat_interval_s` (Linux agent sends it; K8s agent still does not).

Also fixed: rename had produced `X-WeCrew JobWatch-Signature` (invalid header, space) → `X-JobWatch-Signature`.

Tests: `tests/test_outbound_schema.py` (9 contract tests). 49 passed / 1 skipped.

### Not verified
- Migration 0006 not run against live Postgres (no DB in sandbox).
- `deliver_signal` Celery task exercised only through import + `sign()`; end-to-end delivery needs Redis + a receiver.
- Go changes (heartbeat interval field) not compiled — R1 still pending on a real host.

## R5 — integration harness (real Postgres)

`tests/integration/` (18 tests, see its README) run against Postgres 16 as `jobwatch_app`. CI now runs
`alembic upgrade head` → `downgrade base` → `upgrade head` as the owner, then the whole suite as the app role.

### Four production-breaking bugs the harness found — none of the DB paths had ever run
1. `SET LOCAL app.org_id = :org` — SET cannot take bind params → every tenant request 500. Now `set_config(..., true)`.
2. App connected as the DB superuser (compose `POSTGRES_USER`) → superusers bypass RLS, tenant isolation was off.
   Migration 0007 creates `jobwatch_app` (NOSUPERUSER NOBYPASSRLS); compose has a `migrate` service, Helm's
   migrate hook uses `migration.databaseUrl` + `migration.appDbPassword`; API/workers use the app URL.
3. `current_setting('app.org_id', true)::uuid` — on a pooled connection the GUC reads `''` after its transaction,
   and `''::uuid` raises. Migration 0008 recreates all policies via `app_org_id()` (NULLIF) / `app_bypass()`.
4. `:param::type` in SQLAlchemy `text()` is not parsed as a bind + cast → syntax error at the very first ingest
   statement (28 sites incl. processor, alerting, incidents, audit, status pages). All now `CAST(:param AS type)`.

Also: `FOR UPDATE` on an outer join, enum/text ambiguous params, reconciler not re-evaluating paused/skipped jobs.

### Still not verified
- Go agents (R1). K8s agent heartbeat_interval_s.
- NATS consumers end-to-end (harness calls `tick()` directly). Celery real broker (task run with `.apply()`).
- Keycloak flow, Stripe webhook against a live account.

## R6 — NATS + Celery end-to-end

`tests/integration/test_pipeline_nats.py` (4) drives the real workers against a real JetStream:
exec event -> exec-processor -> executions row + jobstatus payload; failure -> rule-engine -> incident;
UNKNOWN suppressed across the transport; duplicate delivery idempotent. Worker loops were split into
`subscribe()` / `drain()` so the harness steps them deterministically instead of racing a task.
`tests/integration/test_celery_redis.py` (2) enqueues over real Redis and runs a worker subprocess
against a throwaway HTTP receiver. CI gained redis + nats services. 73 tests green.

### Three more real bugs found
1. `exec-processor` published a jobstatus payload with no `org_id` -> `handle_status_change` KeyError
   on every execution-driven status change. Payload is now the documented contract.
2. Both subjects lived on one WORK_QUEUE stream -> R4's outbound-exporter could not start
   (`filtered consumer not unique on workqueue stream`) and would have stolen the rule engine's
   messages. Split into CS_EXEC (work-queue) and CS_STATUS (interest).
3. The processor never wrote `job_state`, so after a real failure the job stayed `unknown` and
   UNKNOWN-suppression ate the alert. State derivation moved to `cronsentinel/job_state.py`, called
   by both producers.

### Still not verified
- Go agents (R1, on a real host). K8s agent heartbeat_interval_s.
- Keycloak auth flow; Stripe webhook against a live account.
- `nats` outbound destination kind (still raises not-implemented).
- `events.connect()` retries forever by default: a bad NATS_URL hangs a worker silently rather than
  crash-looping. Consider max_reconnect_attempts + a readiness probe.

## R7 — Keycloak OIDC + Stripe webhook

`cronsentinel/oidc.py` (new) replaces the `get_unverified_claims` stub with full id_token
verification; `/auth/login` issues a nonce and `/auth/callback` verifies signature, iss, aud, exp
and nonce, refetching the JWKS once on failure. Stripe `_verify` now accepts any of several `v1=`
signatures (secret rotation) and malformed events 400 without recording the event id.

Tests: `tests/test_oidc.py` (11 pure — wrong key, alg none, HS256 confusion, wrong iss/aud, expired,
nonce replay, unknown kid, multi-aud azp), `tests/integration/test_auth_flow.py` (7, fake OIDC
provider over real HTTP + Redis + DB), `tests/integration/test_stripe_webhook.py` (10, real DB).
**101 tests green.**

### Two more real bugs
1. Stripe `_verify` built a dict from the signature header, keeping only the last `v1=` — during a
   webhook-secret rotation Stripe sends several and half the events would have been rejected.
2. `checkout.session.completed` without metadata raised KeyError → 500, and Stripe would retry into
   the same 500 forever.

### Still not verified
- Go agents (R1, on a real host). K8s agent heartbeat_interval_s.
- A live Keycloak server (flow proven against a fake provider) and a live Stripe account.
- `nats` outbound destination kind still raises not-implemented.
- `events.connect()` retries forever: a bad NATS_URL hangs a worker silently.

## R8 — the three flagged gaps

New: `cronsentinel/keys.py` (HKDF purpose-derived subkeys), `cronsentinel/csrf.py`,
`scripts/reencrypt_configs.py`, bounded reconnect + `is_connected()` in `events.py`, `/readyz`
returning 503 when NATS is down, web `lib/api.ts` sending `X-CSRF-Token` from an in-memory token.

Tests: `tests/test_keys_csrf.py` (11 pure), `tests/integration/test_csrf_enforcement.py` (6),
`tests/integration/test_nats_resilience.py` (3). **121 tests green**; web build passes.

### Two more real bugs found while testing
1. `audit()` wrote `request.client.host` into an `inet` column — a non-address value failed the
   whole write it was attached to, not just the audit row.
2. R4's integrations router called `audit()` with the wrong signature; every signal-destination
   create/update/delete would have 500'd. No test had exercised those endpoints.

### Deploy order for R8
1. Stop the API/workers.
2. `python scripts/reencrypt_configs.py` (old key → new derived key; idempotent).
3. Deploy. Sessions are invalidated by the key change — users sign in again.

### Still not verified
- Go agents (R1, on a real host); K8s agent heartbeat_interval_s.
- A live Keycloak server and a live Stripe account.
- `nats` outbound destination kind still raises not-implemented.
- CSRF token rotation on privilege change (role change keeps a valid token until TTL).

## R9 — endpoint smoke coverage

`tests/integration/test_router_smoke.py` is table-driven off the **live** route table (77
endpoints): every one is called unauthenticated (must be 401/403/422, never 2xx, never 5xx) and
again with a valid API key (must not 5xx). `test_every_endpoint_is_covered` fails if the surface
shrinks unexpectedly, so a new router cannot ship with zero coverage the way R4's integrations
router did. External-dependency endpoints (Stripe, Copilot LLM, OIDC, channel test-sends) are
classified and skipped for the authenticated call, not silently missed.

**257 tests green.**

### Four more real bugs, all in never-called endpoints
1. `/api/v1/clusters/{id}/cronjobs` — `(:ns IS NULL OR k.namespace=:ns)` left Postgres unable to
   infer the parameter type (`AmbiguousParameter`); the namespace filter 500'd every time. Now
   `CAST(:ns AS text)`.
2. `/api/v1/analytics/jobs` — `round(double precision, int)` does not exist in Postgres. The
   `percentile_cont`-derived cost column 500'd the whole endpoint. Casts to `numeric`.
   (Verified against live PG that the other `round(...)` call sites return numeric and are fine.)
3. `/api/v1/analytics/report` — `_rows(s, q, **p)` took the session as `s`, and two callers bound
   a query parameter *also* called `:s`, so `_rows(..., s=since)` raised
   `TypeError: got multiple values for argument 's'`. Parameter renamed to `session`.
4. `POST /api/v1/dependencies` — a nonexistent `depends_on_job_id` hit the FK and 500'd. Now a
   404 naming the unknown id(s); because RLS hides other tenants' jobs, the same check covers
   cross-tenant references.

Also: `/readyz` and the ingest `/healthz` read `app.state.js` directly, so any request arriving
before the lifespan handler finished raised AttributeError → 500 instead of reporting not-ready.

### Still not verified
- Go agents (R1, on a real host); K8s agent heartbeat_interval_s.
- A live Keycloak server and a live Stripe account.
- `nats` outbound destination kind still raises not-implemented.
- Smoke coverage is breadth, not depth: it proves an endpoint responds sanely, not that its
  business logic is right. RBAC is only checked at the unauthenticated boundary — per-role
  permission matrices (viewer cannot delete, etc.) are not yet tested.
