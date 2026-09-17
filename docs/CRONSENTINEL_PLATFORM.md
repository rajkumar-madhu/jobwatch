# WeCrew JobWatch — Product & Platform Design Document

Version: 0.1 (design baseline)
Status: Draft for build kickoff
Owner: Platform/DevOps (LE)
Date: 2026-09-13

---

## 1. Product Definition

**One-line:** Multi-tenant SaaS for monitoring, managing, analysing and troubleshooting scheduled jobs across Linux, Kubernetes, containers, CI/CD, databases and cloud workloads.

**Category position:** Better Uptime + Datadog + PagerDuty + Sentry, specialised for scheduled workloads.

### 1.1 Questions the product must answer

| # | Question | Primary source | Feature |
|---|----------|----------------|---------|
| 1 | Did the job run? | Heartbeat / agent exec event | Job status engine |
| 2 | Did it start on time? | Schedule evaluator vs start event | Late/Missed detection |
| 3 | Did it finish successfully? | Exit code / success heartbeat | Failure detection |
| 4 | How long did it take? | start_ts → end_ts | Execution timeline |
| 5 | Did runtime increase? | Duration percentiles (p50/p95) | Duration anomaly |
| 6 | Was expected output generated? | Agent output checks (file/size/row count) | Output assertions |
| 7 | Did infra affect it? | Host metrics correlated by time window | Infra correlation |
| 8 | Where did it run? | host / container / pod / node metadata | Execution context |
| 9 | Why did it fail? | stderr, logs, exit code, infra, deploy events | AI Copilot |
| 10 | Who owns it? | Job owner / team | Job metadata |
| 11 | Who was notified? | Notification ledger | Incident page |
| 12 | What changed before failure? | Deploy/commit/config change events | Change timeline |
| 13 | What next? | Copilot remediation + runbook links | AI Copilot |

### 1.2 Supported sources (v1 vs later)

| Source | v1 | Method |
|--------|----|--------|
| Linux cron (/etc/crontab, cron.d, user crontabs) | Yes | Linux agent |
| systemd timers | Yes | Linux agent |
| Kubernetes CronJobs / Jobs / Pods | Yes | K8s agent (Helm) |
| Docker workloads | Yes | Linux agent (docker socket, read-only) |
| Shell / Python scripts, backup jobs, ETL | Yes | Heartbeat wrapper (`cs-run`) |
| Jenkins scheduled pipelines | v1 | Heartbeat + Jenkins plugin (later) |
| GitHub Actions / GitLab CI | v1 | Heartbeat step |
| Airflow / Dagster | v2 | Callback integration (interface only in v1) |
| DB scheduled jobs (pg_cron, MySQL events) | v2 | DB adapter |
| Outbound API / webhook jobs (we call you) | v2 | Not in v1 — inbound heartbeat only |

---

## 2. Decisions Log (spec gaps resolved)

| ID | Gap | Decision |
|----|-----|----------|
| D1 | Auth | Keycloak OIDC as identity provider. Email/password, Google, GitHub, Microsoft, SAML, MFA all via Keycloak realm. App stores only `subject_id`. |
| D2 | Backend | FastAPI (Python 3.12) + Celery workers + PostgreSQL 16 + Redis 7 + NATS JetStream. |
| D3 | Analytics store | PostgreSQL partitioned tables (monthly) for v1. `AnalyticsStore` interface with ClickHouse adapter stub. |
| D4 | Schedule evaluation | Server-side evaluator (croniter + tz db). Reconciler runs every 30s: expected_next_run + grace → no start event ⇒ `MISSED`. DST handled in job timezone. |
| D5 | Event idempotency | Every execution event carries `execution_id (ULID)`, `sequence`, `agent_id`. Ingestion dedups on `(agent_id, execution_id, sequence)`. Out-of-order tolerated; state machine is monotonic. |
| D6 | Clock skew | Store both `agent_ts` and `server_received_ts`. Agent sends monotonic offset in heartbeat; server computes `skew_ms`. Late/missed decisions use server time; runtime uses agent monotonic delta. |
| D7 | Job identity | Discovered jobs get `fingerprint = sha256(host_id + user + normalized_command + schedule)`. Command text edit ⇒ new fingerprint ⇒ shown as "possible rename" with merge option; not auto-merged. |
| D8 | Agent enrollment | One-time bootstrap token (24h) → agent receives long-lived agent key (hashed at rest). Rotation endpoint, revocation list cached in Redis. Agent self-reports version; server flags outdated. |
| D9 | Retention | Per-plan retention enforced by partition drop. stdout/stderr capped at 256 KB per execution (tail kept), full logs optional via S3/MinIO per plan. |
| D10 | Alert storm | Per-channel rate limit (default 30/min), flapping suppression (3 state changes in 10 min ⇒ single "flapping" alert), incident grouping window 5 min. |
| D11 | K8s scope | Helm chart supports `scope: cluster` or `scope: namespaces: [...]`. Read-only RBAC. |
| D12 | AI data boundary | Copilot calls a configurable LLM endpoint (self-hosted default). Redaction pipeline (secrets, tokens, PII regex + entropy) runs before prompt assembly. No auto-remediation; suggested commands displayed only. |
| D13 | SLA window | Rolling 30 days default, configurable (7d / 30d / calendar month). |
| D14 | Status page visibility | Per-job `public_visibility: none / status_only / status_and_duration`. |
| D15 | Billing | Stripe-compatible schema + plan limit enforcement in v1. Live Stripe webhooks in Phase 6. |
| D16 | UI theme | Landing page light-first (per spec). App dashboard ships light + dark toggle. |

---

## 3. Architecture

### 3.1 Logical

```
Agents (Linux / K8s / CI / Heartbeat)
        │ TLS + agent key
        ▼
Ingestion Gateway (FastAPI, stateless, rate-limited)
        │ validated events
        ▼
NATS JetStream  ──────────────┐
        │                     │
        ▼                     ▼
Execution Processor      Metrics Processor
(state machine,          (host/pod metrics →
 dedup, timeline)         timeseries table)
        │
        ▼
Schedule Reconciler (30s) ── MISSED / LATE
        │
        ▼
Rule Engine ── Incident Correlator ── Notification Workers (Celery)
        │                                   │
        ▼                                   ▼
PostgreSQL (OLTP + partitioned execs)   Email/Slack/Teams/PagerDuty/Webhook
        │
        ▼
API (FastAPI) ── Next.js Web ── AI Copilot (LLM endpoint, redacted context)
```

### 3.2 Production deployment

```
Internet → CDN/WAF → Load Balancer
   ├─ web (Next.js)
   ├─ api (FastAPI, N replicas)
   └─ ingest (FastAPI, N replicas, separate HPA)
NATS JetStream (3 nodes)
Workers: exec-processor, reconciler, rule-engine, notifier, copilot
PostgreSQL 16 (primary + replica) · Redis 7 · MinIO/S3
Keycloak (external or bundled) · Prometheus/OTel collector
```

### 3.3 Tenancy model

- `organization` is the tenant root. Every table (except `users`) carries `org_id`.
- Postgres RLS enabled on all tenant tables; API sets `SET LOCAL app.org_id`.
- Workspace = logical grouping inside org (e.g. "Payments", "Data").
- Environments (prod/staging/dev) are per workspace.

---

## 4. Domain Model

### 4.1 Entities

| Entity | Key fields |
|--------|-----------|
| organization | id, name, slug, plan_id, settings |
| workspace | id, org_id, name |
| team | id, org_id, name, escalation_policy_id |
| user | id, keycloak_sub, email, name |
| membership | user_id, org_id, role (owner/admin/devops/sre/developer/viewer), team_ids |
| environment | id, workspace_id, name, tz |
| agent | id, org_id, kind (linux/k8s), host_id, version, key_hash, last_seen, status |
| server | id, org_id, hostname, agent_id, labels |
| cluster | id, org_id, name, agent_id, scope |
| job | id, org_id, workspace_id, env_id, name, kind, source, fingerprint, schedule_expr, tz, expected_runtime_s, grace_s, owner_id, team_id, tags, sla_target, sla_window, alert_policy_id, public_visibility, status, reliability_score, paused |
| job_dependency | job_id, depends_on_job_id |
| execution | id (ULID), job_id, org_id, status, scheduled_ts, agent_ts_start, agent_ts_end, server_received_ts, skew_ms, duration_ms, exit_code, host/container/pod/node, commit_sha, deploy_version, sequence_max — **partitioned by month on scheduled_ts** |
| execution_event | execution_id, sequence, ts, kind (scheduled/started/progress/completed/failed), payload |
| execution_log | execution_id, stream (stdout/stderr), chunk_idx, content (≤256KB total), s3_key nullable |
| host_metric | server_id, ts, cpu, mem, load1, disk_pct, inode_pct, io_wait_ms, net_rx/tx — partitioned |
| k8s_cronjob | cluster_id, namespace, name, schedule, suspend, concurrency_policy, limits, deadlines, last_schedule, last_success |
| incident | id, org_id, severity, status, started_ts, resolved_ts, root_cause, correlation_key |
| incident_event | incident_id, ts, kind, actor, payload |
| alert_rule | id, org_id, scope (job/tag/env), condition, severity, channel_ids, business_hours, repeat_interval, dedup_key |
| notification_channel | id, org_id, kind, config_encrypted |
| notification_ledger | id, incident_id, channel_id, sent_ts, status |
| maintenance_window | id, org_id, scope, start, end, recurrence |
| integration | id, org_id, kind, config_encrypted |
| api_key | id, org_id, name, prefix, key_hash, scopes, last_used |
| audit_log | id, org_id, actor_id, action, target, ts, ip |
| subscription | org_id, plan, stripe_customer_id, stripe_sub_id, status, trial_end |
| usage_record | org_id, period, jobs_count, executions_count, storage_bytes |
| status_page | id, org_id, slug, visibility, job_group_ids |

### 4.2 Job status state machine

```
UNKNOWN ─(schedule set)─> HEALTHY
HEALTHY ─(started)─> RUNNING
RUNNING ─(completed ok)─> HEALTHY
RUNNING ─(failed / exit≠0)─> FAILED
RUNNING ─(runtime > expected + grace)─> TIMEOUT
HEALTHY ─(no start within grace)─> LATE ─(still none at 2×grace)─> MISSED
FAILED|TIMEOUT|MISSED ─(next success)─> RECOVERED ─(next success)─> HEALTHY
any ─(user pause)─> PAUSED
```

### 4.3 Reliability score (0–100)

```
score = 100
  - 40 × (1 - success_ratio_30d)
  - 20 × missed_ratio_30d
  - 15 × late_ratio_30d
  - 10 × duration_cv_penalty          # CV of duration > 0.5 ⇒ full penalty
  -  5 × dependency_failure_ratio
  - 10 × min(alerts_30d / 20, 1)
clamped to [0,100]
```

---

## 5. Key Flows

### 5.1 Heartbeat ingestion

```
POST /api/v1/heartbeat/{job_token}/{start|success|fail}
GET  /ping/{job_token}              # simple success
POST /api/v1/heartbeat/{job_token}  # {"status","duration_ms","exit_code","meta"}
```
1. Validate token → job (Redis cache).
2. Build event with `execution_id` (client-supplied or server ULID), `sequence`.
3. Publish to NATS `exec.{org_id}`.
4. Processor applies state machine, writes execution + event, emits `job.status.changed`.

### 5.2 Agent execution capture

1. Agent wraps cron via `/etc/cron.d` shim or `cs-run <cmd>` wrapper (opt-in; discovery-only mode never modifies crontab).
2. Sends `started` → periodic `progress` (every 30s, includes host metrics snapshot) → `completed|failed` with exit code, tail of stdout/stderr.
3. Buffered on disk (SQLite WAL) during outage; replayed in order with sequence.

### 5.3 Schedule reconciler

Every 30s per org shard: `SELECT jobs WHERE next_expected_ts + grace < now() AND no execution with started event` ⇒ LATE; at `2×grace` ⇒ MISSED. Emits rule-engine events.

### 5.4 Incident correlation

Window 5 min. Group key = any of: same host, same cluster+namespace, same dependency chain, same deploy_version, shared infra anomaly (disk/io/db latency). Produces single incident with affected job list; downstream dependency impact computed from DAG.

### 5.5 AI Copilot

Context builder pulls: last N executions, stderr tail, exec events, host metrics ±15 min, K8s events, deploy/change events, related incidents → redaction → prompt with fixed schema → response `{summary, root_cause, evidence[], affected[], confidence, remediation[], logs[], investigate_cmds[]}`. Read-only.

---

## 6. API Surface (v1)

```
/api/v1/auth/*           (session via Keycloak; API keys via X-API-Key)
/api/v1/orgs, /workspaces, /teams, /members
/api/v1/jobs             CRUD, pause, test-schedule, next-runs?n=5
/api/v1/jobs/{id}/executions
/api/v1/executions/{id}  timeline, logs, compare?with=
/api/v1/heartbeat/...    (ingest tier)
/api/v1/agents           enroll, rotate, revoke, discovered-jobs
/api/v1/clusters         cronjobs, pods, events
/api/v1/servers          metrics
/api/v1/incidents        list, get, ack, resolve, notes
/api/v1/alerts/rules, /channels, /maintenance
/api/v1/integrations
/api/v1/analytics        overview, duration-percentiles, sla, reliability, reports
/api/v1/copilot/ask
/api/v1/status-pages
/api/v1/billing          subscription, usage, portal
/api/v1/audit-logs
/api/v1/api-keys
```
Conventions: cursor pagination, `X-RateLimit-*` headers, RBAC per route, OpenAPI at `/docs`, all writes audited.

---

## 7. Security

- Tenant isolation: RLS + org_id on every query + tenant-isolation test suite.
- RBAC matrix (owner > admin > devops/sre > developer > viewer); write actions require ≥ developer, alert/integration config ≥ devops, billing/members ≥ admin.
- Secrets: channel/integration configs encrypted with envelope encryption (KMS/age key); never returned by API after create.
- API keys: shown once, stored argon2 hash, prefix visible.
- Agent: least privilege, read-only discovery, no env var *values* transmitted (names only, allow-listed), TLS 1.2+, key pinned.
- Redaction: regex + entropy scan on all logs at ingest; secrets never persisted.
- Web: secure/HttpOnly/SameSite cookies, CSRF double-submit, CSP, rate limits per IP + per org.

---

## 8. Plans & Limits

| Plan | Jobs | History | Channels | Extras |
|------|------|---------|----------|--------|
| Free | 5 | 7d | Email | — |
| Developer | 50 | 30d | + Slack, webhooks | — |
| Team | 500 | 90d | + Teams, Discord, Telegram | AI diagnostics, Kubernetes |
| Business | 5,000 | 1y | + PagerDuty, Opsgenie, SMS | Advanced analytics, SSO |
| Enterprise | custom | custom | all | SAML, private deploy, priority support |

Limits enforced at job create, retention partition drop, feature flags per plan.

---

## 9. Observability (of WeCrew JobWatch itself)

Prometheus `/metrics`, OTel traces, JSON logs, `/healthz`, `/readyz`.
Key SLIs: ingest p99 latency, NATS consumer lag, reconciler lag, notifier failure rate, DB query p95, worker restarts.

---

## 10. Repository Layout

```
apps/web            Next.js 15, TS, Tailwind, shadcn/ui, TanStack Query, ECharts
apps/api            FastAPI, SQLAlchemy 2, Alembic, Celery
apps/ingest         FastAPI ingest tier (shares api package)
agents/linux-agent  Go 1.23
agents/k8s-agent    Go 1.23 (informers)
packages/types      OpenAPI-generated TS types
packages/sdk        TS + Python SDK
packages/ui         shared components
infra/docker        Dockerfiles, docker-compose.yml
infra/kubernetes    manifests
infra/helm          cronsentinel (platform) + cronsentinel-agent
docs/               README, ARCHITECTURE, DEPLOYMENT, SECURITY, AGENT, API, DEVELOPMENT
tests/              unit, api, integration, rbac, tenant-isolation, agent, e2e
```

---

## 11. Delivery Phases

| Phase | Scope | Exit criteria |
|-------|-------|---------------|
| 1 | Monorepo, docker-compose, migrations (all tables, partitions, RLS), API core (orgs/RBAC/API keys), heartbeat ingest, NATS processor, status state machine, reconciler, rate limiting | `curl ping` → job shows HEALTHY; skip ping → MISSED within grace |
| 2 | Rule engine, dedup/flapping, maintenance windows, Email/Slack/webhook channels, Celery notifier, notification ledger | Failed job → Slack message once |
| 3 | Linux agent (Go): discovery, cs-run wrapper, buffered send, systemd unit, enrollment | Agent enrols, crontab appears in UI |
| 4 | Web app: Overview, Jobs, Executions timeline + compare, Failures, Incidents, Logs explorer, command palette; demo seed | Dashboard live with seed data |
| 5 | Landing page, onboarding wizard, status pages, cron parser UI | Public signup → first job monitored |
| 6 | K8s agent + Helm, incident correlation, dependency DAG, topology, Copilot, reliability score, analytics reports, Stripe live | Team plan feature-complete |

**Explicit stubs in v1 (marked `# TODO` in code):** ClickHouse adapter, Airflow/Dagster/DB adapters, outbound webhook jobs, SMS provider, Stripe webhooks (until Phase 6), e2e suite.

---

## 12. Open Items (need owner decision)

1. LLM endpoint for Copilot — which self-hosted model/serving stack.
2. Bundle Keycloak in Helm chart or require external instance.
3. Object storage default: MinIO bundled vs customer S3.
4. Domain/brand: WeCrew JobWatch standalone vs WeCrew sub-product.
5. Demo data: static seed vs synthetic generator running continuously.
