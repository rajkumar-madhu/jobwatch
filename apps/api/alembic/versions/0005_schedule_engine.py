"""R2/R3 — independent expected-run engine + four-state model

Revision ID: 0005
Revises: 0004

Introduces `expected_runs`: every slot a schedule *should* produce becomes a row, generated
ahead of time and independent of whether any execution arrives. This replaces the single mutable
`jobs.next_expected_at` column as the source of truth for late/missed, so:
  - a reconciler outage no longer loses missed runs (slots are still there when it comes back)
  - a late start no longer drifts the next expectation forward
  - overlapping runs can be matched to the slot they belong to
"""
from alembic import op

revision = "0005"; down_revision = "0004"; branch_labels = None; depends_on = None


def upgrade():
    op.execute("""
    CREATE TYPE job_state AS ENUM ('ok','late','failing','unknown');
    CREATE TYPE expected_run_state AS ENUM ('pending','running','succeeded','failed','late','missed','skipped');

    CREATE TABLE expected_runs (
      id BIGSERIAL,
      org_id UUID NOT NULL,
      job_id UUID NOT NULL,
      scheduled_for TIMESTAMPTZ NOT NULL,      -- slot instant, in UTC, derived in the job's tz
      grace_until TIMESTAMPTZ NOT NULL,        -- scheduled_for + grace_s
      deadline TIMESTAMPTZ NOT NULL,           -- scheduled_for + grace_s + max(expected_runtime_s, grace_s)
      state expected_run_state NOT NULL DEFAULT 'pending',
      execution_id TEXT,                       -- set when an execution is matched to this slot
      matched_at TIMESTAMPTZ,
      settled_at TIMESTAMPTZ,                  -- when the slot reached a terminal state
      generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY (id, scheduled_for)
    ) PARTITION BY RANGE (scheduled_for);
    CREATE TABLE expected_runs_default PARTITION OF expected_runs DEFAULT;
    CREATE UNIQUE INDEX expected_runs_slot_idx ON expected_runs (job_id, scheduled_for);
    CREATE INDEX expected_runs_open_idx ON expected_runs (state, grace_until) WHERE state IN ('pending','running','late');
    CREATE INDEX expected_runs_job_idx ON expected_runs (job_id, scheduled_for DESC);
    ALTER TABLE expected_runs ENABLE ROW LEVEL SECURITY; ALTER TABLE expected_runs FORCE ROW LEVEL SECURITY;
    CREATE POLICY expected_runs_tenant ON expected_runs USING (org_id = current_setting('app.org_id', true)::uuid) WITH CHECK (org_id = current_setting('app.org_id', true)::uuid);
    CREATE POLICY expected_runs_bypass ON expected_runs USING (current_setting('app.bypass_rls', true) = 'on') WITH CHECK (current_setting('app.bypass_rls', true) = 'on');
    SELECT ensure_month_partition('expected_runs', now()::date);
    SELECT ensure_month_partition('expected_runs', (now() + interval '1 month')::date);

    -- Four-state model. The legacy 9-value `jobs.status` stays for UI continuity and is derived
    -- from these columns (see cronsentinel/states.py); new logic must read job_state.
    ALTER TABLE jobs
      ADD COLUMN IF NOT EXISTS job_state job_state NOT NULL DEFAULT 'unknown',
      ADD COLUMN IF NOT EXISTS state_since TIMESTAMPTZ NOT NULL DEFAULT now(),
      ADD COLUMN IF NOT EXISTS unknown_reason TEXT,          -- no_schedule | no_data_yet | agent_offline | paused
      ADD COLUMN IF NOT EXISTS generated_through TIMESTAMPTZ, -- slots materialised up to here
      ADD COLUMN IF NOT EXISTS consecutive_failures INTEGER NOT NULL DEFAULT 0;

    ALTER TABLE executions ADD COLUMN IF NOT EXISTS expected_run_id BIGINT;
    CREATE INDEX IF NOT EXISTS executions_expected_idx ON executions (expected_run_id);

    -- Backfill: everything with no run yet is unknown/no_data_yet; the rest maps from legacy status.
    UPDATE jobs SET job_state = CASE
        WHEN paused THEN 'unknown' WHEN schedule_expr IS NULL AND last_run_at IS NULL THEN 'unknown'
        WHEN status IN ('failed','timeout','missed') THEN 'failing'
        WHEN status = 'late' THEN 'late'
        WHEN status IN ('healthy','recovered','running') THEN 'ok' ELSE 'unknown' END::job_state,
      unknown_reason = CASE WHEN paused THEN 'paused' WHEN schedule_expr IS NULL AND last_run_at IS NULL THEN 'no_data_yet'
        WHEN status = 'unknown' THEN 'no_data_yet' ELSE NULL END;
    """)


def downgrade():
    op.execute("""
    DROP TABLE IF EXISTS expected_runs;
    ALTER TABLE jobs DROP COLUMN IF EXISTS job_state, DROP COLUMN IF EXISTS state_since, DROP COLUMN IF EXISTS unknown_reason,
      DROP COLUMN IF EXISTS generated_through, DROP COLUMN IF EXISTS consecutive_failures;
    ALTER TABLE executions DROP COLUMN IF EXISTS expected_run_id;
    DROP TYPE IF EXISTS job_state; DROP TYPE IF EXISTS expected_run_state;
    """)
