"""alerting phase 2

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"; down_revision = "0001"; branch_labels = None; depends_on = None


def upgrade():
    op.execute("""
    CREATE INDEX IF NOT EXISTS incidents_affected_jobs_gin ON incidents USING GIN (affected_job_ids);
    ALTER TABLE incidents ADD COLUMN IF NOT EXISTS rule_id UUID REFERENCES alert_rules(id) ON DELETE SET NULL;
    ALTER TABLE incidents ADD COLUMN IF NOT EXISTS last_notified_at TIMESTAMPTZ;
    ALTER TABLE incidents ADD COLUMN IF NOT EXISTS notes TEXT;
    ALTER TABLE notification_channels ADD COLUMN IF NOT EXISTS enabled BOOLEAN NOT NULL DEFAULT true;
    ALTER TABLE alert_rules ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();
    CREATE INDEX IF NOT EXISTS alert_rules_org_enabled_idx ON alert_rules (org_id) WHERE enabled;
    CREATE INDEX IF NOT EXISTS maintenance_org_time_idx ON maintenance_windows (org_id, starts_at, ends_at);
    """)


def downgrade():
    op.execute("""
    DROP INDEX IF EXISTS incidents_affected_jobs_gin;
    ALTER TABLE incidents DROP COLUMN IF EXISTS rule_id, DROP COLUMN IF EXISTS last_notified_at, DROP COLUMN IF EXISTS notes;
    ALTER TABLE notification_channels DROP COLUMN IF EXISTS enabled;
    """)
