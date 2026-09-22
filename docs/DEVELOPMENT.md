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

## R10 — RBAC matrix

`tests/integration/test_rbac_matrix.py`: 32 gated write endpoints x 6 roles, derived from the live
dependency graph so a missing `require_role()` fails the suite. Plus header-spoofing, cross-tenant
writes (404 via RLS) and revoked keys. **319 tests green.**

### Found
- `POST /api/v1/copilot/ask` had no role gate — a viewer could spend LLM budget. Now `developer`
  (deliberate behaviour change, see SECURITY.md).
- `DELETE /api/v1/api-keys/{key_id}` and `POST /auth/switch/{org_id}` took `str` path params, so a
  malformed id 500'd out of Postgres instead of 422ing at the edge.
- Test-side: `maintenance_windows` has no `name` column and `PATCH /jobs` does not accept `name`;
  both had been guessed rather than checked against the schema.

### Suite runtime
Full run is ~2m40s, dominated by the RBAC matrix (6 roles x 32 endpoints, each a real HTTP call
against Postgres). Fine in CI; use `-k` locally.

### Still not verified
- Go agents (R1, on a real host); K8s agent heartbeat_interval_s.
- A live Keycloak server and a live Stripe account.
- `nats` outbound destination kind still raises not-implemented.
- Handler-level business rules (the matrix is authorisation breadth, not logic depth).

## R11 — lifecycle and cascade

`tests/integration/test_lifecycle_cascade.py` (8) covers job delete, agent revoke and org purge.
Migration 0009 adds `purge_job()`, `purge_org()` and a status-page prune trigger;
`DELETE /api/v1/jobs/{id}` now calls `purge_job` in the same transaction, and
`python -m cronsentinel.cli purge-org --org-id … --yes` offboards a tenant. **327 tests green.**

### Found
- Deleting a job left its `expected_runs` behind (78 rows in the fixture) plus its executions and
  execution_events — none of those tables has a FK to `jobs`. The reconciler would keep settling
  slots for a job that no longer exists.
- A deleted job stayed in `status_pages.job_ids`, so a public page kept listing it.
- There was no way to fully remove a tenant's data: eleven tables carry `org_id` with no FK to
  `organizations`, so `DELETE FROM organizations` silently retained all of it.
- Verified as already correct: the public status page and the incident detail view both survive a
  deleted job; revoking an agent leaves its jobs alone and marks them UNKNOWN/agent_offline.

### Still not verified
- Go agents (R1, on a real host); K8s agent heartbeat_interval_s.
- A live Keycloak server and a live Stripe account.
- `nats` outbound destination kind still raises not-implemented.
- No self-service org deletion, and no export-before-delete for GDPR-style requests.

## R12 — web tests

The frontend had **zero** tests against 327 on the backend. Two layers added, both DB-free:

- **vitest** (`apps/web/tests/unit`, 20 tests) — `lib/format` and `lib/api`. The CSRF flow written
  in R8 had never executed: it is now pinned that the token is fetched before the first cookie
  write, reused afterwards, absent on reads and on API-key requests, refreshed exactly once on a
  403, and never persisted to localStorage.
- **Playwright** (`apps/web/tests/e2e`, 22 tests x desktop + Pixel 7) — all 14 app pages plus the
  landing and public status pages render against `tools/ui-review/mock_api.py` with no ErrorBox, no
  page error and no failing request; sidebar/drawer navigation; and the failure states the mock
  cannot produce (500 → error, empty list → empty state, 401 → sign in, slow → skeleton), driven by
  `page.route` interception.

CI now runs `npm test`, the build, then the e2e suite against the mock API, uploading the
Playwright report on failure.

### Notes from the run
- Playwright must match the browser build available to the runner; pinned to 1.56.0 here.
- The app loads IBM Plex from `fonts.googleapis.com`, which is blocked in sandboxes. Pages render
  correctly on the fallback stack, so the e2e suite excludes that host by name rather than
  ignoring all failed requests — a real failing API call still fails the test. **Open:** consider
  self-hosting the font so the app has no hard remote dependency at render time.

### Still not verified
- Go agents (R1, on a real host); K8s agent heartbeat_interval_s.
- A live Keycloak server and a live Stripe account.
- `nats` outbound destination kind still raises not-implemented.
- e2e runs against the mock API, not the real backend: contract drift between
  `tools/ui-review/mock_api.py` and the actual routers is still possible and unchecked.

## R13 — mock/real contract

R12's e2e suite runs the frontend against `tools/ui-review/mock_api.py`. Green there means the
frontend works *against the mock*; if the mock drifts from the real routers the suite stays green
while production breaks. R13 pins the two together (22 tests, 349 backend total).

- `tests/test_mock_contract.py` (no DB) — every mock route exists in the real route table, with
  path-param names normalised so `{jid}` vs `{job_id}` is not treated as drift, and the endpoints
  the dashboard calls all have mock coverage.
- `tests/integration/test_mock_shape_contract.py` — calls both APIs for the same endpoint against
  seeded data and diffs the JSON key structure. Fields the mock invents fail; container kind
  (bare list vs `{"items": [...]}`) is checked separately because both render as "no data" in the
  UI rather than as an error.

### Findings
- **OpenAPI is not usable as a contract here.** Only 3 of 77 endpoints declare a `response_model`;
  the rest return bare dicts and serialise as `{"type": "object", "additionalProperties": true}`.
  Schema validation against the real spec would assert nothing. **Open:** adding response models
  to the read endpoints would make this test far stronger and is worth doing incrementally.
- The mock 500s on any job id it does not know, so path params must come from the mock itself.

### Traps this test had to avoid (both produced false drift on the first run)
- An empty real collection has no item paths, so every mock item field looks invented. Each
  compared endpoint now seeds a populated row, and a still-empty real container **skips with a
  reason** rather than passing silently.
- Data-keyed maps (`analytics.by_status`) are values, not fields, and are excluded by name.

The suite was mutation-checked: adding a field to the mock makes it fail, so it is not vacuous.

### Not covered
`/clusters`, `/topology`, `/agents`, `/copilot/*`, `/billing` — seeding a real row needs a
cluster, an agent or an LLM that this harness does not have. The mock's shape for those is
**unchecked**, and listed in the test.

## R14 — response models, and a real schema contract

R13 found that OpenAPI could not serve as a contract: 3 of 77 endpoints declared a
`response_model`. R14 adds models to the dashboard read endpoints and upgrades the contract test
from a key diff to real schema validation (358 backend tests).

Modelled endpoints went 3 → 11: `JobPage`, `IncidentOut`, `ChannelOut`, `AlertRuleOut`,
`StatusPageOut`, `WorkspaceOut`, `OverviewOut`, `DependencyGraph` (+ node/edge/metric models) in
`cronsentinel/schemas.py`. The models describe what the routers already returned — the full suite
passed unchanged after wiring them, which is the evidence that they are accurate rather than
aspirational.

`tests/test_mock_schema_contract.py` (no DB) validates the mock's responses against those schemas
with jsonschema, so field **types** are now checked, not just key names.

### Drift this found
The mock omitted `org_id` on incidents and alert rules, and `created_at` on channels, rules and
workspaces — fields the real API always returns. The frontend was therefore developed against a
shape production never sends. Fixed in `tools/ui-review/mock_api.py`.

### Behaviour change worth knowing
With a `response_model`, a router returning a row that is missing a required field now raises a
500 at serialisation instead of passing the partial object through. That is the intent, but it is
a real change in failure mode for the eight endpoints above.

### Remaining gaps
`UNMODELLED` in the test lists the read endpoints still returning bare dicts — `/agents`,
`/clusters`, `/topology`, `/logs/search`, `/analytics/series`, `/analytics/jobs`. Their mock shape
is **unvalidated**. The test asserts the list in both directions: a new bare-dict read endpoint
must be acknowledged, and adding a model must remove its entry, so coverage cannot quietly stay
flat. Note `/clusters`, `/topology` and `/agents` are also the endpoints R13 could not seed — they
are the least-tested surface in the product.

## R15 — full read coverage, and generated frontend types

Two things: close the R14 gap list, then make the frontend bind to the spec at compile time.
362 backend tests, 20 vitest, 44 Playwright (desktop + mobile).

### Response models: 11 → 17
`AgentOut`, `ClusterOut`, `TopologyOut` (+ server/user/namespace/job nodes), `LogSearchOut`,
`SeriesOut`, `JobAnalyticsOut`. `UNMODELLED` in `tests/test_mock_schema_contract.py` is now empty;
the assertion still guards the other direction, so a new bare-dict read endpoint must be added
there consciously. Contract coverage went from 8 endpoints to 12.

`LogHit` and `SeriesIncident` are deliberately `extra="allow"` — a log row's columns depend on the
backing store, so pinning them would be a false contract.

### Drift this found
The mock omitted `created_at` on agents. Same class of bug as R14: the frontend was developed
against rows production never sends.

### Generated TypeScript types
`apps/web/lib/api-types.ts` is generated from the spec with `openapi-typescript`; `lib/api.ts`
re-exports friendly aliases (`Job`, `Incident`, `Agent`, …) over it. The hand-written interfaces
it replaced are gone. `JobStatus` stays hand-written on purpose: it is a DB enum that the
generated types widen to `string`, and narrowing it keeps exhaustive switches working.

Regenerate:
```bash
cd apps/api && python -c "import json;from cronsentinel.main import app;json.dump(app.openapi(),open('../web/openapi.json','w'),indent=2)"
cd apps/web && npm run types:gen
```
CI regenerates and runs `git diff --exit-code`, so a checked-in file that lags the API fails the
build. `npm run typecheck` now runs in CI too.

**A real bug the types caught immediately:** the overview page rendered `r.p95_ms` as
`number | null`, but the API declares it optional, so an omitted field would have reached `dur()`
as `undefined`. Hand-written interfaces had hidden this.

### Limits
Only response shapes are typed. Request bodies, query parameters and status codes are not, so a
wrong POST body is still a runtime 422. Typing those is the next increment if it is wanted.

## R16 — own-the-stack audit: fonts, Copilot data boundary

Triggered by a "what did we miss" review. Two principle issues were fixed; fixing them surfaced a
production bug and showed that part of R12/R15's coverage was not what it claimed.
397 backend tests, 20 vitest, 44 Playwright.

### Fonts
IBM Plex was loaded from `fonts.googleapis.com` at render time — a third-party request on every
page load, and a hard failure mode for air-gapped deploys. Now bundled via `@fontsource`
(SIL OFL 1.1, redistribution permitted), imported in `app/layout.tsx`. The e2e allow-list for
Google Fonts is gone; instead **any request to a host other than the app and the API fails the
page test**.

### Copilot data boundary (`cronsentinel/copilot/egress.py`)
The default backend was already self-hosted (OpenAI-compatible vLLM/Ollama), but nothing enforced
it. Three gaps:

1. **External endpoints are now refused** unless `COPILOT_ALLOW_EXTERNAL=true`. Internal means
   loopback, RFC 1918 / ULA literals, single-label names (Kubernetes Services), or a configured
   suffix (`COPILOT_INTERNAL_SUFFIXES`, default `.svc,.cluster.local,.internal,.local,.lan`).
   Link-local is excluded so `169.254.169.254` never counts as internal. Decided on the configured
   name, not by DNS resolution — slower and repointable; ambiguous means external.
2. **When external is allowed, topology is pseudonymised**: hosts, pods, nodes, k8s object names
   and IPv4s become `host-1`, `pod-2`, `ip-1`, including inside free text (stderr, failure
   reasons, commands). Tokens are mapped back in the answer, so the user sees real names and the
   model can still correlate "the same host" across jobs.
3. **Redaction gaps closed**: `failure_reason`, k8s event `message`, incident title and
   `correlation_signals`, and the user's question now pass through `redact()` — for internal
   endpoints too, since a self-hosted model still logs prompts. The 502 no longer echoes the raw
   httpx error, which contained the LLM endpoint URL, to every tenant.

`tests/integration/test_copilot_egress_e2e.py` asserts on the bytes a fake LLM server actually
receives. Mutation-checked: removing failure_reason redaction, pseudonymisation, or question
redaction each fails the suite.

**Deploy note:** anyone running the Copilot against a public API will get a 503 after upgrading
until they set `COPILOT_ALLOW_EXTERNAL=true` deliberately.

### Production bug found: Logs page refetch loop
`app/(app)/logs/page.tsx` computed `since` from `Date.now()` during render and put it in the query
key. Every render produced a new key, which fetched, which re-rendered: **~113 requests/second per
open tab** against the partitioned `execution_logs` table. The key is now the applied filters; the
rolling window is computed inside `queryFn`, so the 15s poll still gets a fresh window. Other
render-time `Date.now()` uses were checked and are display-only.

The page e2e test now counts API calls per path and fails above 3 in the settle window.

### Coverage that was not what it claimed
- **R12**: `/logs` and `/integrations` had rendered only their error state against the mock since
  R12 — the mock's `logs(**kw)` made FastAPI require a query param named `kw` (every call 422'd),
  and R4's integrations routes were never mocked (404). Both passed because assertions ran before
  the queries failed. Page tests now wait for the initial queries to settle (bounded, since the 15s
  poll means some pages never fully idle).
- **R15**: the docs said a new bare-dict read endpoint would fail the contract guard. It would not —
  the guard only checked paths already on its hand-written list, which is how R4's integrations
  reads were missed. It now scans the route table in both directions. R4's reads gained models
  (`SignalDestinationOut`, `SignalDeliveryOut`, `SignalSchemaOut`) and contract coverage; **16
  detail reads remain unmodelled**, listed in the test as generated from the route table.

## R17 — the whole product, end to end

Every earlier suite tested a piece. Nothing proved that a heartbeat arriving at ingest ends up as a
webhook on the customer's endpoint. R17 does, on real processes. 399 backend tests + 6 full-chain.

- `apps/api/scripts/fullstack.sh up|down|status` starts API, ingest, exec-processor,
  schedule-generator, reconciler, rule-engine, outbound-exporter and the Celery notifier —
  the same entrypoints as `infra/docker/docker-compose.yml`, service for service — over real
  Postgres, NATS JetStream and Redis. Logs in `/tmp/jobwatch-fullstack/`.
- `tests/fullstack/test_alert_chain.py` (`JOBWATCH_FULLSTACK=1`, ~3 min) drives everything through
  public HTTP: slots materialise without being asked; a success heartbeat makes the job healthy; a
  failure reaches a webhook receiver; an incident opens; and with **no heartbeat at all** the
  reconciler flags the missed slot and the alert still arrives. Timeouts dump job status, recent
  executions and slots, which is what diagnosed the bug below.
- `.github/workflows/fullstack.yml` runs it nightly, on demand, and on PRs touching the alert path.

### Production bug: a failure after a success was silently ignored
The first full-chain run sent success then fail 360 ms apart; the job stayed green with no alert.
`attach_slot` binds only to an *open* slot, so once a success settled the slot, a later run in the
same period — a manual rerun, a wrapper retry — bound to nothing or to an older slot. State was
derived from the latest settled slot by `scheduled_for`, so that failure was recorded and then
ignored. The mirror case was broken too: a failure then a successful retry stayed failing.

`job_state.recompute_state` now takes the last and previous outcome from the **executions
timeline**, ordered by `COALESCE(agent_ts_end, server_received_ts)` so a delayed delivery cannot
override a newer outcome. That is complete because the reconciler writes a `missed` execution per
missed slot and flips runs to `timeout`. Slots still drive LATE and missed detection.
`consecutive_failures` uses the same timeline. Two regression tests in `test_slot_lifecycle.py`
fail on the old derivation with the exact symptom and pass on the new one at several positions in
the minute.

### Test isolation fix
The NATS pipeline tests assumed empty streams; the full-chain run left backlog and made `drain()`
counts wrong. The fixture now purges CS_EXEC and CS_STATUS. **Do not run the integration suite
while `fullstack.sh` is up** — the purge would drop the stack's messages.

### Honest notes
- `test_overdue_slots_become_missed_and_job_failing` failed once in a full-suite run and did not
  reproduce in 22 targeted runs across the */5 cycle or a full rerun. It is time-position sensitive
  (R9 already deflaked it once). Recorded, not fixed.
- This is processes, not containers. It proves the chain; it does not prove the images, the compose
  networking or the Helm chart. That still needs a real host.
- Not in the chain: the Go agents (R1), Keycloak login, Stripe, the web UI against the real API.

### Security finding, not yet fixed: webhook SSRF
There is no destination check on webhook channels or signal destinations. A tenant can point one at
`http://169.254.169.254/…` (cloud metadata) or any internal HTTP service, and the notifier will POST
to it from inside the cluster. For a multi-tenant SaaS this must be blocked; for a self-hosted
single-tenant deploy, internal webhooks are legitimate. Needs a mode switch (block private/link-local
by default in SaaS, allow in self-hosted) plus DNS-rebinding-safe resolution at send time.

## R18 — SSRF guard for tenant webhooks

Closes the R17 finding. Details and the threat model are in `docs/SECURITY.md`; 436 backend tests
+ 6 full-chain.

The audit found the problem was wider than "no destination check":
- **"Send test" was a port scanner.** It ran synchronously in the API pod and returned the raw
  exception, distinguishing refused, timeout and reset for any host:port a tenant named.
- **Arbitrary tenant headers** meant `Metadata-Flavor: Google` would make a GCP metadata fetch
  succeed rather than 403.
- **The same leak in stored errors**: delivery `last_error`, destination `disabled_reason` and the
  notification ledger kept `str(e)`, all tenant-readable.

Tests: `tests/test_netguard.py` (28, no DB — ranges, rebinding blocked at connect, redirects not
followed, TLS verified by hostname against a local CA, safe error text) and
`tests/integration/test_ssrf_guard.py` (API refuses internal URLs and metadata headers; a legacy
pre-R18 internal destination is blocked at send and logged as `destination not allowed`).
Mutation-checked: removing the transport hook fails both connect-time tests.

Test-setup changes, stated so they are not mistaken for weakening: the Celery worker test and
`fullstack.sh` run in self-hosted mode because their receivers are on 127.0.0.1; the outbound tests
patch `netguard.post` instead of `httpx.post`.

**Deploy note:** Helm installs now refuse webhooks to private addresses. A self-hosted Helm install
that posts to internal services must set `OUTBOUND_ALLOW_PRIVATE: "true"`. Existing destinations
pointing inside the cluster will start failing with `destination not allowed` and auto-disable
after the usual number of failures.

## R19 — load test

See `docs/LOADTEST.md` for the harness, the numbers, and the hardware caveat. Summary: the
schedule generator could not keep up at a few thousand jobs and, worse, held row locks that
blocked heartbeat processing while it ran. Fixed (lazy occurrence iteration, bulk inserts, short
per-batch transactions with a slot budget, horizon-aware revisits). 10k jobs now catch up 2.8M
slots in 194 s on 1 vCPU. The reconciler has the same locking shape and is the next fix.

## R20 — reconciler locking, timeline index

Closes two R19 open findings; numbers in `docs/LOADTEST.md`.
- The reconciler now settles slots in one transaction and runs the state pass in batches of 100
  jobs, each its own transaction, publishing events after each commit. Longest transaction on a
  30-minute outage backlog at 10k jobs: 13.6 s → 1.1 s. Synthetic missed executions are one
  statement instead of one INSERT per slot. `tick(s)` keeps its single-transaction behaviour for
  tests; the worker uses `settle()` + `recompute()`.
- Migration 0010 adds an expression index for R17's timeline sort, after measuring it: 18.8 ms →
  9.0 ms per recompute at a week of every-minute history. Building it locks writes on
  `executions` briefly; see the migration docstring for the per-partition route on large tables.

## R21 — RLS index use, machine-secret hashing

Started as "cache the slow analytics endpoints"; measuring first found two platform-wide costs
instead, both fixed, no cache added. Details and numbers in `docs/LOADTEST.md`, role and hashing
design in `docs/SECURITY.md`. 449 backend tests (28 s, was ~190 s) + 6 full-chain.

- Migration 0011: `jobwatch_system` role for system sessions; tenant policies lose their
  `OR app_bypass()` arm, so `org_id` indexes are used. `db.system_session()` now does
  `SET LOCAL ROLE jobwatch_system` instead of setting a GUC.
- API and agent keys hashed with SHA-256; argon2 hashes upgraded on first use.

**Deploy notes:** 0011 needs PostgreSQL 16 and a migration user able to create roles (0007 already
required this). Existing keys keep working. Do not grant `jobwatch_system` to anything with
INHERIT TRUE.

## R22 — ingest HTTP load

`scripts/ingest_loadtest.py`; results and caveats in `docs/LOADTEST.md`. `/agent/v1/events` now
resolves a batch in two queries off the event loop and releases its DB connection before
publishing (620 → 1,447 events/s on the test box). Heartbeat auth moved off the loop. First tests
for `/agent/v1/events`, verified against the old handler too. `INGEST_WORKERS` knob in compose.
`fullstack.sh` health wait widened to 60 s after the API outlasted 20 s on a loaded box.

## R23 — hourly housekeeping

Scorer split into failure-isolated phases with short transactions (heartbeat wait during a 1.7M-row
retention delete: 3.6–4.9 s → 0.06 s). Migration 0012: `ensure_month_partition` rescues rows
stranded in the default partition instead of failing forever; `drop_partition_if_empty()` lets the
system role reclaim emptied month partitions. Numbers and the trade-off (slower wall time) in
`docs/LOADTEST.md`. Also re-ran the full chain owed from R22 (6/6).

## R24 — usage metering; definer-function ownership

Migration 0013: `execution_logs(org_id)` index; incremental storage metering via a trigger-fed
ledger (6.0 s → 0.03 s); `FORCE` RLS on the new tables; DML-only `SECURITY DEFINER` functions
re-owned by `jobwatch_system`. That last one fixes R11's `purge_org`/`purge_job`/status-page prune,
which would have silently done nothing on a managed-Postgres (non-superuser) owner. Details in
`docs/LOADTEST.md` and `docs/SECURITY.md`.

Noted, not fixed: 348 orphaned `execution_logs` rows in the dev DB from test fixtures that delete
orgs without `purge_org()` — test hygiene, not a product path (the CLI purges first).

## R25 — monitoring gaps: our outage never pages a customer

Migration 0014. After a schedule-generator outage every affected job backfilled up to two days of
slots and the reconciler marked them all missed — an alert storm for a fault on our side. Two
mechanisms, both fixed in the reconciler before any missed judgement:

1. **Retro-match.** Executions that arrived while no slot existed were recorded unattached and the
   backfilled slot then looked empty. `retro_match()` binds them (same 90 s / deadline window as the
   live path, one execution per slot, nearest start wins).
2. **`unobserved`.** Whatever is still empty inside a period the platform was not watching becomes
   `unobserved` — terminal, no synthetic execution, no alert, job state untouched. Never `missed`:
   nobody was there to see it.

Gap detection: `workers/platform_health.heartbeat()` runs every generator and reconciler pass; a
break > 3× the interval writes `monitoring_gaps`. The reconciler also treats a *currently silent*
generator as an open gap, so recovery order does not matter. `GET /api/v1/platform/monitoring-gaps`
exposes gaps (global) and the tenant's own unobserved count; the overview shows a banner.

Measured on the full stack — 45-minute generator outage, every-minute job, 3 real runs reported
during the outage: before, 46 missed alerts; after, 3 runs recognised, 41 unobserved, 2 genuine
misses (slots that came due after recovery with no run).

**Trade-off, chosen deliberately:** while the generator is silent, the reconciler will not call any
overdue slot missed even if ingest was healthy and the job truly did not run. A real miss during
our outage is deferred to `unobserved` rather than risk a false page. Revisit if ingest gets its own
heartbeat, which would let the predicate distinguish "we could not see" from "we did not schedule".

Also fixed: `test_wrong_agent_key_is_rejected` flipped the key's last hex char to "0"; one run in
16 it already was "0" and the "wrong" key was the right key — a 200 that read like an auth hole.
