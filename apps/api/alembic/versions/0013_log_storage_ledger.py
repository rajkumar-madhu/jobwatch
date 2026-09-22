"""R24 — incremental log-storage metering, and an org_id index on execution_logs.

Found in R24: usage metering ran, every hour, one correlated
`(SELECT sum(length(content)) FROM execution_logs WHERE org_id = o.id)` per organisation, and
execution_logs had no index on org_id — so each organisation's subquery was a full table scan.
Cost = organisations x total log rows: 6.0 s already at 98k rows / 50 orgs on the test box, heading
for tens of minutes per hour at realistic volume. The missing index also made tenant log reads
(RLS `org_id = app_org_id()`) full cross-tenant scans.

Metering is now incremental. Statement-level AFTER triggers with transition tables append one row
per (statement, org) to storage_ledger with the byte delta; the scorer folds the ledger into
org_storage hourly. An append-only ledger rather than updating one counter row per org, so a busy
tenant's concurrent log writes do not serialise on a single hot row.

Bytes are octet_length(content), i.e. bytes. The old metering used length(), i.e. characters —
storage_bytes was mislabelled for any non-ASCII log output.

The trigger is created before the backfill, in the same transaction; CREATE TRIGGER blocks writers
to execution_logs until commit, so the backfilled total and the live ledger neither overlap nor
miss rows. The backfill is one full scan of execution_logs at migration time.

Also fixes a latent bug in SECURITY DEFINER functions, found while adding FORCE RLS here. A definer
function runs as its owner. Tenant tables use FORCE ROW LEVEL SECURITY, which applies RLS to the
owner too — unless the owner is a superuser. In the sandbox, CI and default compose the owner IS a
superuser, so nothing showed. On managed Postgres (RDS, Cloud SQL) it usually is not, and then no
policy matches the owner (no app.org_id, not a member of jobwatch_system): purge_org() and
purge_job() would DELETE nothing and report success, and these storage triggers would reject log
inserts from system sessions. Demonstrated with a non-superuser owner on a scratch table: 0 of 5
rows deleted; 5 of 5 once the function was owned by jobwatch_system. So the DML-only definer
functions are now owned by jobwatch_system, whose policy covers every tenant table. DDL functions
(ensure_month_partition, drop_partition_if_empty) keep the table owner: DROP needs ownership, and
they touch partitions directly, where the parent's RLS does not apply.
"""
from alembic import op

revision = "0013"; down_revision = "0012"; branch_labels = None; depends_on = None


def upgrade():
    op.execute("CREATE INDEX IF NOT EXISTS execution_logs_org_idx ON execution_logs (org_id)")
    op.execute("""
    CREATE TABLE org_storage (
        org_id uuid PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
        bytes bigint NOT NULL DEFAULT 0,
        updated_at timestamptz NOT NULL DEFAULT now());
    CREATE TABLE storage_ledger (
        id bigserial PRIMARY KEY,
        org_id uuid NOT NULL,          -- no FK: purge_org writes negative deltas just before the org row goes
        delta bigint NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now());

    CREATE OR REPLACE FUNCTION execution_logs_storage_ins() RETURNS trigger AS $$
    BEGIN
      INSERT INTO storage_ledger (org_id, delta)
        SELECT org_id, sum(octet_length(content)) FROM new_rows WHERE content IS NOT NULL GROUP BY org_id;
      RETURN NULL;
    END $$ LANGUAGE plpgsql SECURITY DEFINER;

    CREATE OR REPLACE FUNCTION execution_logs_storage_del() RETURNS trigger AS $$
    BEGIN
      INSERT INTO storage_ledger (org_id, delta)
        SELECT org_id, -sum(octet_length(content)) FROM old_rows WHERE content IS NOT NULL GROUP BY org_id;
      RETURN NULL;
    END $$ LANGUAGE plpgsql SECURITY DEFINER;

    CREATE OR REPLACE FUNCTION execution_logs_storage_upd() RETURNS trigger AS $$
    BEGIN
      INSERT INTO storage_ledger (org_id, delta)
        SELECT org_id, sum(d) FROM (
          SELECT org_id, octet_length(content) AS d FROM new_rows WHERE content IS NOT NULL
          UNION ALL
          SELECT org_id, -octet_length(content) FROM old_rows WHERE content IS NOT NULL) x
        GROUP BY org_id HAVING sum(d) <> 0;
      RETURN NULL;
    END $$ LANGUAGE plpgsql SECURITY DEFINER;

    CREATE TRIGGER execution_logs_storage_ins AFTER INSERT ON execution_logs
      REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION execution_logs_storage_ins();
    CREATE TRIGGER execution_logs_storage_del AFTER DELETE ON execution_logs
      REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION execution_logs_storage_del();
    CREATE TRIGGER execution_logs_storage_upd AFTER UPDATE ON execution_logs
      REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION execution_logs_storage_upd();

    -- Backfill after the triggers exist, same transaction: see module docstring.
    INSERT INTO org_storage (org_id, bytes)
      SELECT l.org_id, sum(octet_length(l.content)) FROM execution_logs l
      JOIN organizations o ON o.id = l.org_id WHERE l.content IS NOT NULL GROUP BY l.org_id;

    -- Same RLS shape as every tenant table since 0011: tenant sees its own row, system sees all.
    ALTER TABLE org_storage ENABLE ROW LEVEL SECURITY;
    ALTER TABLE storage_ledger ENABLE ROW LEVEL SECURITY;
    ALTER TABLE org_storage FORCE ROW LEVEL SECURITY;
    ALTER TABLE storage_ledger FORCE ROW LEVEL SECURITY;
    CREATE POLICY org_storage_tenant ON org_storage USING (org_id = app_org_id());
    CREATE POLICY org_storage_system ON org_storage TO jobwatch_system USING (true) WITH CHECK (true);
    CREATE POLICY storage_ledger_tenant ON storage_ledger USING (org_id = app_org_id());
    CREATE POLICY storage_ledger_system ON storage_ledger TO jobwatch_system USING (true) WITH CHECK (true);
    GRANT SELECT, INSERT, UPDATE, DELETE ON org_storage, storage_ledger TO jobwatch_app, jobwatch_system;
    GRANT USAGE, SELECT ON SEQUENCE storage_ledger_id_seq TO jobwatch_app, jobwatch_system;
    """)
    # Re-own DML-only definer functions (see docstring). A non-superuser migration role must be able
    # to SET ROLE jobwatch_system to hand ownership to it; INHERIT FALSE keeps it from acting as it.
    op.execute("""
    DO $$ BEGIN
      IF NOT (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) THEN
        EXECUTE format('GRANT jobwatch_system TO %I WITH INHERIT FALSE, SET TRUE', current_user);
      END IF;
    END $$;
    ALTER FUNCTION purge_job(uuid) OWNER TO jobwatch_system;
    ALTER FUNCTION purge_org(uuid) OWNER TO jobwatch_system;
    ALTER FUNCTION execution_logs_storage_ins() OWNER TO jobwatch_system;
    ALTER FUNCTION execution_logs_storage_del() OWNER TO jobwatch_system;
    ALTER FUNCTION execution_logs_storage_upd() OWNER TO jobwatch_system;
    ALTER FUNCTION prune_status_page_job() OWNER TO jobwatch_system;   -- R11 status-page trigger, same bug
    """)


def downgrade():
    op.execute("ALTER FUNCTION purge_job(uuid) OWNER TO CURRENT_USER; ALTER FUNCTION purge_org(uuid) OWNER TO CURRENT_USER; "
               "ALTER FUNCTION prune_status_page_job() OWNER TO CURRENT_USER;")
    op.execute("""
    DROP TRIGGER IF EXISTS execution_logs_storage_ins ON execution_logs;
    DROP TRIGGER IF EXISTS execution_logs_storage_del ON execution_logs;
    DROP TRIGGER IF EXISTS execution_logs_storage_upd ON execution_logs;
    DROP FUNCTION IF EXISTS execution_logs_storage_ins(), execution_logs_storage_del(), execution_logs_storage_upd();
    DROP TABLE IF EXISTS storage_ledger;
    DROP TABLE IF EXISTS org_storage;
    DROP INDEX IF EXISTS execution_logs_org_idx;
    """)
