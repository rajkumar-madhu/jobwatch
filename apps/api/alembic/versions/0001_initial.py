"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-13
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Tables carrying org_id get Row-Level Security. API sets `SET LOCAL app.org_id`.
TENANT_TABLES = [
    "workspaces", "teams", "memberships", "environments", "agents", "servers", "clusters",
    "jobs", "job_dependencies", "executions", "execution_events", "execution_logs",
    "host_metrics", "k8s_cronjobs", "incidents", "incident_events", "alert_rules",
    "notification_channels", "notification_ledger", "maintenance_windows", "integrations",
    "api_keys", "audit_logs", "subscriptions", "usage_records", "status_pages",
]

UPGRADE_SQL = r"""
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TYPE job_status AS ENUM ('unknown','healthy','running','late','missed','failed','timeout','recovered','paused');
CREATE TYPE exec_status AS ENUM ('scheduled','running','success','failed','timeout','missed');
CREATE TYPE member_role AS ENUM ('owner','admin','devops','sre','developer','viewer');
CREATE TYPE incident_severity AS ENUM ('critical','high','medium','low');
CREATE TYPE incident_status AS ENUM ('open','acknowledged','resolved');

CREATE TABLE organizations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  slug TEXT NOT NULL UNIQUE,
  plan TEXT NOT NULL DEFAULT 'free',
  settings JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  keycloak_sub TEXT UNIQUE,
  email TEXT NOT NULL UNIQUE,
  name TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE workspaces (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, name)
);

CREATE TABLE teams (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  escalation_policy JSONB NOT NULL DEFAULT '{}',
  UNIQUE (org_id, name)
);

CREATE TABLE memberships (
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  role member_role NOT NULL DEFAULT 'viewer',
  team_ids UUID[] NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, org_id)
);

CREATE TABLE environments (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  tz TEXT NOT NULL DEFAULT 'UTC',
  UNIQUE (workspace_id, name)
);

CREATE TABLE agents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('linux','k8s','ci')),
  host_id TEXT,
  name TEXT NOT NULL,
  version TEXT,
  key_hash TEXT NOT NULL,
  key_prefix TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  last_seen_at TIMESTAMPTZ,
  skew_ms INTEGER NOT NULL DEFAULT 0,
  revoked_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX agents_org_idx ON agents (org_id);

CREATE TABLE servers (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  agent_id UUID REFERENCES agents(id) ON DELETE SET NULL,
  hostname TEXT NOT NULL,
  labels JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, hostname)
);

CREATE TABLE clusters (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  agent_id UUID REFERENCES agents(id) ON DELETE SET NULL,
  name TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'cluster',
  namespaces TEXT[] NOT NULL DEFAULT '{}',
  UNIQUE (org_id, name)
);

CREATE TABLE jobs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  environment_id UUID REFERENCES environments(id) ON DELETE SET NULL,
  name TEXT NOT NULL,
  description TEXT,
  kind TEXT NOT NULL DEFAULT 'heartbeat',      -- heartbeat|cron|systemd|k8s_cronjob|docker|ci
  source TEXT NOT NULL DEFAULT 'manual',       -- manual|agent|k8s
  fingerprint TEXT,                            -- D7
  heartbeat_token TEXT NOT NULL UNIQUE,
  schedule_expr TEXT,
  tz TEXT NOT NULL DEFAULT 'UTC',
  expected_runtime_s INTEGER,
  grace_s INTEGER NOT NULL DEFAULT 300,
  owner_id UUID REFERENCES users(id) ON DELETE SET NULL,
  team_id UUID REFERENCES teams(id) ON DELETE SET NULL,
  server_id UUID REFERENCES servers(id) ON DELETE SET NULL,
  cluster_id UUID REFERENCES clusters(id) ON DELETE SET NULL,
  tags TEXT[] NOT NULL DEFAULT '{}',
  command TEXT,
  run_as_user TEXT,
  working_dir TEXT,
  sla_target NUMERIC(5,2),                     -- e.g. 99.50
  sla_window TEXT NOT NULL DEFAULT 'rolling_30d',
  alert_policy JSONB NOT NULL DEFAULT '{}',
  public_visibility TEXT NOT NULL DEFAULT 'none',
  status job_status NOT NULL DEFAULT 'unknown',
  paused BOOLEAN NOT NULL DEFAULT false,
  reliability_score SMALLINT,
  last_run_at TIMESTAMPTZ,
  last_status exec_status,
  next_expected_at TIMESTAMPTZ,
  late_marked_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, name)
);
CREATE INDEX jobs_org_status_idx ON jobs (org_id, status);
CREATE INDEX jobs_next_expected_idx ON jobs (next_expected_at) WHERE paused = false AND schedule_expr IS NOT NULL;
CREATE INDEX jobs_fingerprint_idx ON jobs (org_id, fingerprint);

CREATE TABLE job_dependencies (
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  job_id UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  depends_on_job_id UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  PRIMARY KEY (job_id, depends_on_job_id),
  CHECK (job_id <> depends_on_job_id)
);

-- Executions: partitioned monthly on scheduled_ts (D3, D9)
CREATE TABLE executions (
  id TEXT NOT NULL,                             -- ULID
  org_id UUID NOT NULL,
  job_id UUID NOT NULL,
  agent_id UUID,
  status exec_status NOT NULL DEFAULT 'scheduled',
  scheduled_ts TIMESTAMPTZ NOT NULL,
  agent_ts_start TIMESTAMPTZ,
  agent_ts_end TIMESTAMPTZ,
  server_received_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  skew_ms INTEGER NOT NULL DEFAULT 0,           -- D6
  duration_ms BIGINT,
  exit_code INTEGER,
  host TEXT, container TEXT, pod TEXT, node TEXT,
  commit_sha TEXT, deploy_version TEXT,
  env_var_names TEXT[] NOT NULL DEFAULT '{}',   -- names only, never values
  sequence_max INTEGER NOT NULL DEFAULT 0,      -- D5
  meta JSONB NOT NULL DEFAULT '{}',
  PRIMARY KEY (id, scheduled_ts)
) PARTITION BY RANGE (scheduled_ts);
CREATE INDEX executions_job_ts_idx ON executions (job_id, scheduled_ts DESC);
CREATE INDEX executions_org_status_ts_idx ON executions (org_id, status, scheduled_ts DESC);
CREATE TABLE executions_default PARTITION OF executions DEFAULT;

CREATE TABLE execution_events (
  org_id UUID NOT NULL,
  execution_id TEXT NOT NULL,
  agent_id UUID,
  sequence INTEGER NOT NULL,
  kind TEXT NOT NULL,                           -- scheduled|started|progress|completed|failed
  agent_ts TIMESTAMPTZ,
  server_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  payload JSONB NOT NULL DEFAULT '{}',
  PRIMARY KEY (execution_id, sequence)          -- dedup key (D5)
);
CREATE INDEX execution_events_org_ts_idx ON execution_events (org_id, server_ts DESC);

CREATE TABLE execution_logs (
  org_id UUID NOT NULL,
  execution_id TEXT NOT NULL,
  stream TEXT NOT NULL CHECK (stream IN ('stdout','stderr')),
  chunk_idx INTEGER NOT NULL,
  content TEXT NOT NULL,                        -- capped 256KB/execution total (D9), redacted at ingest
  s3_key TEXT,
  PRIMARY KEY (execution_id, stream, chunk_idx)
);

CREATE TABLE host_metrics (
  org_id UUID NOT NULL,
  server_id UUID NOT NULL,
  ts TIMESTAMPTZ NOT NULL,
  cpu_pct REAL, mem_pct REAL, load1 REAL, disk_pct REAL, inode_pct REAL,
  io_wait_ms REAL, net_rx_bps BIGINT, net_tx_bps BIGINT,
  PRIMARY KEY (server_id, ts)
) PARTITION BY RANGE (ts);
CREATE TABLE host_metrics_default PARTITION OF host_metrics DEFAULT;

CREATE TABLE k8s_cronjobs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  cluster_id UUID NOT NULL REFERENCES clusters(id) ON DELETE CASCADE,
  job_id UUID REFERENCES jobs(id) ON DELETE SET NULL,
  namespace TEXT NOT NULL, name TEXT NOT NULL,
  schedule TEXT, suspend BOOLEAN, concurrency_policy TEXT,
  successful_history_limit INTEGER, failed_history_limit INTEGER,
  active_deadline_s INTEGER, starting_deadline_s INTEGER,
  last_schedule_at TIMESTAMPTZ, last_success_at TIMESTAMPTZ,
  spec JSONB NOT NULL DEFAULT '{}',
  UNIQUE (cluster_id, namespace, name)
);

CREATE TABLE incidents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  severity incident_severity NOT NULL DEFAULT 'medium',
  status incident_status NOT NULL DEFAULT 'open',
  title TEXT NOT NULL,
  correlation_key TEXT,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  acknowledged_at TIMESTAMPTZ, resolved_at TIMESTAMPTZ,
  affected_job_ids UUID[] NOT NULL DEFAULT '{}',
  root_cause TEXT, resolution TEXT,
  responder_ids UUID[] NOT NULL DEFAULT '{}'
);
CREATE INDEX incidents_org_status_idx ON incidents (org_id, status, started_at DESC);
CREATE INDEX incidents_corr_idx ON incidents (org_id, correlation_key) WHERE status <> 'resolved';

CREATE TABLE incident_events (
  id BIGSERIAL PRIMARY KEY,
  org_id UUID NOT NULL,
  incident_id UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
  ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  kind TEXT NOT NULL, actor_id UUID, payload JSONB NOT NULL DEFAULT '{}'
);

CREATE TABLE notification_channels (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,                           -- email|slack|teams|discord|telegram|pagerduty|opsgenie|sms|webhook
  name TEXT NOT NULL,
  config_enc BYTEA NOT NULL,                    -- envelope-encrypted, never returned
  rate_per_min INTEGER NOT NULL DEFAULT 30,     -- D10
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE alert_rules (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  scope JSONB NOT NULL DEFAULT '{}',            -- {job_ids|tags|environment_ids}
  condition TEXT NOT NULL,                      -- failed|missed|late|runtime_exceeded|recovered|consecutive_failures|sla_breach
  params JSONB NOT NULL DEFAULT '{}',
  severity incident_severity NOT NULL DEFAULT 'medium',
  channel_ids UUID[] NOT NULL DEFAULT '{}',
  business_hours JSONB, repeat_interval_s INTEGER,
  enabled BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE notification_ledger (
  id BIGSERIAL PRIMARY KEY,
  org_id UUID NOT NULL,
  incident_id UUID REFERENCES incidents(id) ON DELETE SET NULL,
  channel_id UUID REFERENCES notification_channels(id) ON DELETE SET NULL,
  dedup_key TEXT NOT NULL,
  sent_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  status TEXT NOT NULL, error TEXT
);
CREATE INDEX notification_ledger_dedup_idx ON notification_ledger (org_id, dedup_key, sent_at DESC);

CREATE TABLE maintenance_windows (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  scope JSONB NOT NULL DEFAULT '{}',
  starts_at TIMESTAMPTZ NOT NULL, ends_at TIMESTAMPTZ NOT NULL,
  rrule TEXT
);

CREATE TABLE integrations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  kind TEXT NOT NULL, name TEXT NOT NULL,
  config_enc BYTEA NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE api_keys (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  prefix TEXT NOT NULL UNIQUE,
  key_hash TEXT NOT NULL,
  role member_role NOT NULL DEFAULT 'developer',
  scopes TEXT[] NOT NULL DEFAULT '{}',
  last_used_at TIMESTAMPTZ, revoked_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE audit_logs (
  id BIGSERIAL PRIMARY KEY,
  org_id UUID NOT NULL,
  actor_id UUID, actor_type TEXT NOT NULL DEFAULT 'user',
  action TEXT NOT NULL, target_type TEXT, target_id TEXT,
  ip INET, ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  payload JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX audit_logs_org_ts_idx ON audit_logs (org_id, ts DESC);

CREATE TABLE subscriptions (
  org_id UUID PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
  plan TEXT NOT NULL DEFAULT 'free',
  stripe_customer_id TEXT, stripe_subscription_id TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  trial_ends_at TIMESTAMPTZ,
  current_period_end TIMESTAMPTZ
);

CREATE TABLE usage_records (
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  period DATE NOT NULL,
  jobs_count INTEGER NOT NULL DEFAULT 0,
  executions_count BIGINT NOT NULL DEFAULT 0,
  storage_bytes BIGINT NOT NULL DEFAULT 0,
  PRIMARY KEY (org_id, period)
);

CREATE TABLE status_pages (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  slug TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  visibility TEXT NOT NULL DEFAULT 'private',
  job_ids UUID[] NOT NULL DEFAULT '{}'
);

-- Plan limits (D15)
CREATE TABLE plan_limits (
  plan TEXT PRIMARY KEY,
  max_jobs INTEGER, retention_days INTEGER, features TEXT[] NOT NULL DEFAULT '{}'
);
INSERT INTO plan_limits VALUES
 ('free', 5, 7, '{email}'),
 ('developer', 50, 30, '{email,slack,webhook}'),
 ('team', 500, 90, '{email,slack,webhook,teams,discord,telegram,ai,kubernetes}'),
 ('business', 5000, 365, '{email,slack,webhook,teams,discord,telegram,ai,kubernetes,pagerduty,opsgenie,sms,sso,analytics}'),
 ('enterprise', NULL, NULL, '{all}');

-- Monthly partition helper
CREATE OR REPLACE FUNCTION ensure_month_partition(parent TEXT, month DATE) RETURNS void AS $$
DECLARE
  part TEXT := parent || '_' || to_char(month, 'YYYYMM');
  start_d DATE := date_trunc('month', month)::date;
  end_d DATE := (date_trunc('month', month) + interval '1 month')::date;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = part) THEN
    EXECUTE format('CREATE TABLE %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L)', part, parent, start_d, end_d);
  END IF;
END $$ LANGUAGE plpgsql;

SELECT ensure_month_partition('executions', now()::date);
SELECT ensure_month_partition('executions', (now() + interval '1 month')::date);
SELECT ensure_month_partition('host_metrics', now()::date);
SELECT ensure_month_partition('host_metrics', (now() + interval '1 month')::date);
"""


def upgrade():
    op.execute(UPGRADE_SQL)
    for t in TENANT_TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {t}_tenant ON {t} USING (org_id = current_setting('app.org_id', true)::uuid) "
            f"WITH CHECK (org_id = current_setting('app.org_id', true)::uuid)"
        )
        # Workers/reconciler run with app.bypass_rls = 'on'
        op.execute(
            f"CREATE POLICY {t}_bypass ON {t} USING (current_setting('app.bypass_rls', true) = 'on') "
            f"WITH CHECK (current_setting('app.bypass_rls', true) = 'on')"
        )


def downgrade():
    raise RuntimeError("Downgrade of initial schema not supported")
