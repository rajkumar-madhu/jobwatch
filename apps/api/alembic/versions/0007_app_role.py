"""R5 — non-superuser application role so RLS actually applies

Revision ID: 0007
Revises: 0006

Found by the integration harness: compose (and any setup where the app connects as the DB owner /
superuser) silently bypassed every RLS policy — superusers are exempt from RLS by definition.
Migrations keep running as the owner; the API and workers must connect as `jobwatch_app`.

The role password is read from JOBWATCH_APP_DB_PASSWORD at migration time so it never lands in the
repo. If unset, the role is created without LOGIN and the operator must set one.
"""
import os

from alembic import op

revision = "0007"; down_revision = "0006"; branch_labels = None; depends_on = None

PARTITION_FN = """
CREATE OR REPLACE FUNCTION ensure_month_partition(parent TEXT, month DATE) RETURNS void AS $$
DECLARE
  part TEXT := parent || '_' || to_char(month, 'YYYYMM');
  start_d DATE := date_trunc('month', month)::date;
  end_d DATE := (date_trunc('month', month) + interval '1 month')::date;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = part) THEN
    EXECUTE format('CREATE TABLE %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L)', part, parent, start_d, end_d);
    {grant}
  END IF;
END $$ LANGUAGE plpgsql {security};
"""


def upgrade():
    db = op.get_bind().engine.url.database
    pw = os.getenv("JOBWATCH_APP_DB_PASSWORD")
    op.execute("""
    DO $$ BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'jobwatch_app') THEN
        CREATE ROLE jobwatch_app NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
      END IF;
    END $$;""")
    if pw:
        op.execute("ALTER ROLE jobwatch_app WITH LOGIN PASSWORD '%s'" % pw.replace("'", "''"))
    op.execute(f"""
    GRANT CONNECT ON DATABASE "{db}" TO jobwatch_app;
    GRANT USAGE ON SCHEMA public TO jobwatch_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO jobwatch_app;
    GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO jobwatch_app;
    GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO jobwatch_app;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO jobwatch_app;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO jobwatch_app;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO jobwatch_app;
    REVOKE ALL ON alembic_version FROM jobwatch_app;""")
    # Partition creation is called from the scorer as the app role: run the function as its owner and
    # grant the new partition to the app role inside it.
    op.execute(PARTITION_FN.format(grant="EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO jobwatch_app', part);",
                                   security="SECURITY DEFINER"))
    op.execute("""REVOKE ALL ON FUNCTION ensure_month_partition(text, date) FROM PUBLIC;
                  GRANT EXECUTE ON FUNCTION ensure_month_partition(text, date) TO jobwatch_app;""")


def downgrade():
    db = op.get_bind().engine.url.database
    op.execute(PARTITION_FN.format(grant="", security="SECURITY INVOKER"))
    op.execute(f"""
    ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM jobwatch_app;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM jobwatch_app;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM jobwatch_app;
    REVOKE ALL ON ALL TABLES IN SCHEMA public FROM jobwatch_app;
    REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM jobwatch_app;
    REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM jobwatch_app;
    REVOKE ALL ON SCHEMA public FROM jobwatch_app;
    REVOKE CONNECT ON DATABASE "{db}" FROM jobwatch_app;
    DROP ROLE IF EXISTS jobwatch_app;""")
