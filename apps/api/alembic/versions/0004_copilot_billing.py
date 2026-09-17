"""copilot + billing phase 6b

Revision ID: 0004
Revises: 0003
"""
from alembic import op

revision = "0004"; down_revision = "0003"; branch_labels = None; depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE IF NOT EXISTS copilot_sessions (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(), org_id UUID NOT NULL, user_id UUID, incident_id UUID, job_id UUID,
      question TEXT NOT NULL, answer JSONB NOT NULL, context_summary JSONB NOT NULL DEFAULT '{}', model TEXT, latency_ms INTEGER, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS copilot_sessions_org_idx ON copilot_sessions (org_id, created_at DESC);
    ALTER TABLE copilot_sessions ENABLE ROW LEVEL SECURITY; ALTER TABLE copilot_sessions FORCE ROW LEVEL SECURITY;
    CREATE POLICY copilot_tenant ON copilot_sessions USING (org_id = current_setting('app.org_id', true)::uuid) WITH CHECK (org_id = current_setting('app.org_id', true)::uuid);
    CREATE POLICY copilot_bypass ON copilot_sessions USING (current_setting('app.bypass_rls', true) = 'on') WITH CHECK (current_setting('app.bypass_rls', true) = 'on');
    ALTER TABLE incidents ADD COLUMN IF NOT EXISTS detected_at TIMESTAMPTZ DEFAULT now();
    ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS stripe_price_id TEXT, ADD COLUMN IF NOT EXISTS cancel_at_period_end BOOLEAN NOT NULL DEFAULT false;
    CREATE TABLE IF NOT EXISTS stripe_events (id TEXT PRIMARY KEY, type TEXT NOT NULL, received_at TIMESTAMPTZ NOT NULL DEFAULT now());
    ALTER TABLE plan_limits ADD COLUMN IF NOT EXISTS stripe_price_id TEXT, ADD COLUMN IF NOT EXISTS monthly_usd NUMERIC(8,2);
    UPDATE plan_limits SET monthly_usd = CASE plan WHEN 'free' THEN 0 WHEN 'developer' THEN 19 WHEN 'team' THEN 79 WHEN 'business' THEN 299 ELSE NULL END;
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS copilot_sessions; DROP TABLE IF EXISTS stripe_events;")
