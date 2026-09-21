"""R20 — index for the executions timeline that job state is derived from.

R17 moved job_state.recompute_state to "latest terminal execution by completion time"
(ORDER BY COALESCE(agent_ts_end, server_received_ts) DESC), which no index covered. Measured in
R20 (docs/LOADTEST.md): 3.9 ms/recompute at 30 executions per job, 18.8 ms at a week of
every-minute history (10,080 rows/job), 9.0 ms with this index. recompute_state runs on every
heartbeat, so this is on the ingest hot path.

Operational note: on a partitioned table CREATE INDEX cannot be CONCURRENTLY and locks writes on
executions while it builds (0.7 s on 500k rows in the test box). For a large production table,
build it per partition first — CREATE INDEX CONCURRENTLY on each partition, then CREATE INDEX ON
ONLY the parent and ALTER INDEX … ATTACH PARTITION — and this migration becomes a no-op because
of IF NOT EXISTS.
"""
from alembic import op

revision = "0010"; down_revision = "0009"; branch_labels = None; depends_on = None


def upgrade():
    op.execute("""CREATE INDEX IF NOT EXISTS executions_job_done_idx
                  ON executions (job_id, (COALESCE(agent_ts_end, server_received_ts)) DESC)""")


def downgrade():
    op.execute("DROP INDEX IF EXISTS executions_job_done_idx")
