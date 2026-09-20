"""R11 — job and org teardown for the tables no FK can reach

Revision ID: 0009
Revises: 0008

`executions`, `expected_runs`, `execution_events`, `execution_logs`, `host_metrics`,
`audit_logs`, `copilot_sessions`, `incident_events`, `k8s_events`, `notification_ledger` and
`signal_deliveries` carry `org_id` but no FK to `organizations` — several are partitioned, and
the rest were simply never wired up. References in `uuid[]` columns (`status_pages.job_ids`,
`alert_rules.channel_ids`, `signal_destinations.workspace_ids`) cannot have one at all.

Rather than cascade-deleting from huge partitioned tables on every `DELETE FROM jobs`, this adds:
  - `purge_job(uuid)`   — called by the delete-job handler, inside the same transaction
  - `purge_org(uuid)`   — called before removing an organization (tenant offboarding)
  - a trigger that prunes `status_pages.job_ids` when a job disappears, so the public page cannot
    render a phantom entry even if a job is deleted by some other path

`incidents.affected_job_ids` is deliberately NOT pruned: an incident is a historical record and
must keep naming the job it was about. The read paths already tolerate an id that no longer
resolves (see tests/integration/test_lifecycle_cascade.py).
"""
from alembic import op

revision = "0009"; down_revision = "0008"; branch_labels = None; depends_on = None


def upgrade():
    op.execute("""
    CREATE OR REPLACE FUNCTION purge_job(p_job uuid) RETURNS void AS $$
    BEGIN
      DELETE FROM execution_events WHERE execution_id IN (SELECT id FROM executions WHERE job_id = p_job);
      DELETE FROM execution_logs   WHERE execution_id IN (SELECT id FROM executions WHERE job_id = p_job);
      DELETE FROM executions       WHERE job_id = p_job;
      DELETE FROM expected_runs    WHERE job_id = p_job;
      DELETE FROM k8s_cronjobs     WHERE job_id = p_job;
      UPDATE status_pages SET job_ids = array_remove(job_ids, p_job) WHERE p_job = ANY(job_ids);
    END $$ LANGUAGE plpgsql SECURITY DEFINER;

    CREATE OR REPLACE FUNCTION purge_org(p_org uuid) RETURNS void AS $$
    BEGIN
      DELETE FROM execution_events   WHERE org_id = p_org;
      DELETE FROM execution_logs     WHERE org_id = p_org;
      DELETE FROM executions         WHERE org_id = p_org;
      DELETE FROM expected_runs      WHERE org_id = p_org;
      DELETE FROM host_metrics       WHERE org_id = p_org;
      DELETE FROM audit_logs         WHERE org_id = p_org;
      DELETE FROM copilot_sessions   WHERE org_id = p_org;
      DELETE FROM incident_events    WHERE org_id = p_org;
      DELETE FROM k8s_events         WHERE org_id = p_org;
      DELETE FROM notification_ledger WHERE org_id = p_org;
      DELETE FROM signal_deliveries  WHERE org_id = p_org;
    END $$ LANGUAGE plpgsql SECURITY DEFINER;

    REVOKE ALL ON FUNCTION purge_job(uuid), purge_org(uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION purge_job(uuid), purge_org(uuid) TO jobwatch_app;

    -- Belt and braces: whatever path deletes a job, no status page may keep pointing at it.
    CREATE OR REPLACE FUNCTION prune_status_page_job() RETURNS trigger AS $$
    BEGIN
      UPDATE status_pages SET job_ids = array_remove(job_ids, OLD.id) WHERE OLD.id = ANY(job_ids);
      RETURN OLD;
    END $$ LANGUAGE plpgsql SECURITY DEFINER;
    DROP TRIGGER IF EXISTS jobs_prune_status_pages ON jobs;
    CREATE TRIGGER jobs_prune_status_pages AFTER DELETE ON jobs
      FOR EACH ROW EXECUTE FUNCTION prune_status_page_job();
    """)


def downgrade():
    op.execute("""
    DROP TRIGGER IF EXISTS jobs_prune_status_pages ON jobs;
    DROP FUNCTION IF EXISTS prune_status_page_job();
    DROP FUNCTION IF EXISTS purge_job(uuid);
    DROP FUNCTION IF EXISTS purge_org(uuid);
    """)
