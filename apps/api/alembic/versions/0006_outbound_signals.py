"""R4 — outbound signal destinations (AEGIS-downstream boundary) + R3 open items

Revision ID: 0006
Revises: 0005
"""
from alembic import op

revision = "0006"; down_revision = "0005"; branch_labels = None; depends_on = None


def upgrade():
    op.execute("""
    CREATE TYPE signal_destination_kind AS ENUM ('webhook','nats');
    CREATE TABLE signal_destinations (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
      name TEXT NOT NULL,
      kind signal_destination_kind NOT NULL,
      config_enc BYTEA NOT NULL,             -- Fernet: {url, secret} or {url, subject_prefix, creds}
      event_types TEXT[] NOT NULL DEFAULT '{}',  -- empty = all
      workspace_ids UUID[] NOT NULL DEFAULT '{}', -- empty = all
      enabled BOOLEAN NOT NULL DEFAULT true,
      consecutive_failures INTEGER NOT NULL DEFAULT 0,
      disabled_reason TEXT,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    ALTER TABLE signal_destinations ENABLE ROW LEVEL SECURITY; ALTER TABLE signal_destinations FORCE ROW LEVEL SECURITY;
    CREATE POLICY sd_tenant ON signal_destinations USING (org_id = current_setting('app.org_id', true)::uuid) WITH CHECK (org_id = current_setting('app.org_id', true)::uuid);
    CREATE POLICY sd_bypass ON signal_destinations USING (current_setting('app.bypass_rls', true) = 'on') WITH CHECK (current_setting('app.bypass_rls', true) = 'on');

    CREATE TABLE signal_deliveries (
      id BIGSERIAL PRIMARY KEY,
      org_id UUID NOT NULL,
      destination_id UUID NOT NULL REFERENCES signal_destinations(id) ON DELETE CASCADE,
      signal_id TEXT NOT NULL,               -- ULID; the idempotency key consumers dedupe on
      event_type TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'pending', -- pending|sent|failed|dead
      attempts INTEGER NOT NULL DEFAULT 0,
      last_error TEXT,
      response_code INTEGER,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      sent_at TIMESTAMPTZ
    );
    CREATE UNIQUE INDEX signal_deliveries_dedup ON signal_deliveries (destination_id, signal_id);
    CREATE INDEX signal_deliveries_org_idx ON signal_deliveries (org_id, created_at DESC);
    ALTER TABLE signal_deliveries ENABLE ROW LEVEL SECURITY; ALTER TABLE signal_deliveries FORCE ROW LEVEL SECURITY;
    CREATE POLICY sdl_tenant ON signal_deliveries USING (org_id = current_setting('app.org_id', true)::uuid) WITH CHECK (org_id = current_setting('app.org_id', true)::uuid);
    CREATE POLICY sdl_bypass ON signal_deliveries USING (current_setting('app.bypass_rls', true) = 'on') WITH CHECK (current_setting('app.bypass_rls', true) = 'on');

    -- R3 open item: per-agent offline threshold derived from the interval the agent reports
    ALTER TABLE agents ADD COLUMN IF NOT EXISTS heartbeat_interval_s INTEGER NOT NULL DEFAULT 60;
    -- R3 open item: expected_runs retention (partition-aware, see scorer)
    ALTER TABLE plan_limits ADD COLUMN IF NOT EXISTS expected_runs_retention_days INTEGER NOT NULL DEFAULT 90;
    """)


def downgrade():
    op.execute("""
    DROP TABLE IF EXISTS signal_deliveries; DROP TABLE IF EXISTS signal_destinations;
    DROP TYPE IF EXISTS signal_destination_kind;
    ALTER TABLE agents DROP COLUMN IF EXISTS heartbeat_interval_s;
    ALTER TABLE plan_limits DROP COLUMN IF EXISTS expected_runs_retention_days;
    """)
