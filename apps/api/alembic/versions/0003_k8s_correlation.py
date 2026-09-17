"""k8s + correlation phase 6a

Revision ID: 0003
Revises: 0002
"""
from alembic import op

revision = "0003"; down_revision = "0002"; branch_labels = None; depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE k8s_cronjobs ADD COLUMN IF NOT EXISTS uid TEXT, ADD COLUMN IF NOT EXISTS image TEXT, ADD COLUMN IF NOT EXISTS command TEXT, ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();
    CREATE UNIQUE INDEX IF NOT EXISTS k8s_cronjobs_uid_idx ON k8s_cronjobs (cluster_id, uid);
    CREATE TABLE IF NOT EXISTS k8s_events (
      id BIGSERIAL PRIMARY KEY, org_id UUID NOT NULL, cluster_id UUID NOT NULL REFERENCES clusters(id) ON DELETE CASCADE,
      namespace TEXT, object_kind TEXT, object_name TEXT, reason TEXT, message TEXT, ts TIMESTAMPTZ NOT NULL, execution_id TEXT
    );
    CREATE INDEX IF NOT EXISTS k8s_events_org_ts_idx ON k8s_events (org_id, ts DESC);
    ALTER TABLE k8s_events ENABLE ROW LEVEL SECURITY; ALTER TABLE k8s_events FORCE ROW LEVEL SECURITY;
    CREATE POLICY k8s_events_tenant ON k8s_events USING (org_id = current_setting('app.org_id', true)::uuid) WITH CHECK (org_id = current_setting('app.org_id', true)::uuid);
    CREATE POLICY k8s_events_bypass ON k8s_events USING (current_setting('app.bypass_rls', true) = 'on') WITH CHECK (current_setting('app.bypass_rls', true) = 'on');
    ALTER TABLE executions ADD COLUMN IF NOT EXISTS failure_reason TEXT;
    ALTER TABLE incidents ADD COLUMN IF NOT EXISTS correlation_signals JSONB NOT NULL DEFAULT '[]';
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS k8s_events; ALTER TABLE executions DROP COLUMN IF EXISTS failure_reason; ALTER TABLE incidents DROP COLUMN IF EXISTS correlation_signals;")
