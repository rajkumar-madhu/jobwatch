"""R21 — remove the OR from every tenant RLS policy.

Found by the R19/R21 load test. Each tenant table had two permissive policies, which Postgres ORs:

    org_id = app_org_id()   OR   app_bypass()

The second arm references no column, so the planner cannot turn the first into an index condition.
Every tenant-scoped query on a large table therefore scanned ALL tenants' rows and filtered:
a 7-day count on executions for one tenant seq-scanned 386k rows and discarded 326k belonging to
other tenants (247 ms); the same query with org_id usable as an index condition is an index scan.
Cost scaled with total platform data, not the tenant's — a small tenant paid for the largest, and
every dashboard got slower as the platform grew. The planner also mis-estimated row counts ~40x.

Fix: system (cross-tenant) sessions no longer use a GUC checked inside the tenant policy. They
SET ROLE jobwatch_system, which has its own policy `TO jobwatch_system USING (true)`. For the app
role the only applicable policy is `org_id = app_org_id()` — a plain, index-usable predicate.

Safety-critical detail: a policy TO a role also applies to *members* of that role. jobwatch_app is
granted jobwatch_system WITH INHERIT FALSE, SET TRUE — it may SET ROLE to it, but does not act as it
otherwise. With INHERIT TRUE the `true` policy would apply to every tenant query and RLS would be
off entirely. tests/integration/test_rls_system_role.py asserts isolation without SET ROLE.

Requires PostgreSQL 16 (GRANT … WITH INHERIT/SET options). Threat model unchanged: code able to run
arbitrary SQL as the app role could already set the old bypass GUC; now it could SET ROLE instead.
"""
from alembic import op
from sqlalchemy import text

revision = "0011"; down_revision = "0010"; branch_labels = None; depends_on = None

PARTITION_FN = """
CREATE OR REPLACE FUNCTION ensure_month_partition(parent TEXT, month DATE) RETURNS void AS $$
DECLARE
  part TEXT := parent || '_' || to_char(month, 'YYYYMM');
  start_d DATE := date_trunc('month', month)::date;
  end_d DATE := (date_trunc('month', month) + interval '1 month')::date;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = part) THEN
    EXECUTE format('CREATE TABLE %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L)', part, parent, start_d, end_d);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO jobwatch_app', part);
    {extra}
  END IF;
END $$ LANGUAGE plpgsql SECURITY DEFINER;
"""


def _tenant_tables(conn):
    return [r[0] for r in conn.execute(text("""
        SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_attribute a ON a.attrelid=c.oid AND a.attname='org_id' AND NOT a.attisdropped
        WHERE n.nspname='public' AND c.relkind IN ('r','p') AND NOT c.relispartition AND c.relrowsecurity""")).all()]


def upgrade():
    op.execute("""
    DO $$ BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'jobwatch_system') THEN
        CREATE ROLE jobwatch_system NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
      END IF;
    END $$;
    GRANT jobwatch_system TO jobwatch_app WITH INHERIT FALSE, SET TRUE;
    GRANT USAGE ON SCHEMA public TO jobwatch_system;
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO jobwatch_system;
    GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO jobwatch_system;
    GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO jobwatch_system;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO jobwatch_system;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO jobwatch_system;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO jobwatch_system;
    """)
    op.execute(PARTITION_FN.format(extra="EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO jobwatch_system', part);"))
    op.execute("REVOKE ALL ON FUNCTION ensure_month_partition(text, date) FROM PUBLIC; "
               "GRANT EXECUTE ON FUNCTION ensure_month_partition(text, date) TO jobwatch_app, jobwatch_system;")
    conn = op.get_bind()
    for t in _tenant_tables(conn):
        op.execute(f'DROP POLICY IF EXISTS "{t}_bypass" ON {t}')
        op.execute(f'DROP POLICY IF EXISTS "{t}_system" ON {t}')
        op.execute(f"CREATE POLICY {t}_system ON {t} TO jobwatch_system USING (true) WITH CHECK (true)")
        # {t}_tenant is unchanged: USING (org_id = app_org_id()) — now the only policy the app role sees.


def downgrade():
    conn = op.get_bind()
    for t in _tenant_tables(conn):
        op.execute(f'DROP POLICY IF EXISTS "{t}_system" ON {t}')
        op.execute(f"CREATE POLICY {t}_bypass ON {t} USING (app_bypass()) WITH CHECK (app_bypass())")
    op.execute(PARTITION_FN.format(extra=""))
    op.execute("""
    ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM jobwatch_system;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM jobwatch_system;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM jobwatch_system;
    REVOKE ALL ON ALL TABLES IN SCHEMA public FROM jobwatch_system;
    REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM jobwatch_system;
    REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM jobwatch_system;
    REVOKE USAGE ON SCHEMA public FROM jobwatch_system;
    REVOKE jobwatch_system FROM jobwatch_app;
    DROP ROLE IF EXISTS jobwatch_system;
    """)
