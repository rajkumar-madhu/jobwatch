"""R5 — RLS policies must tolerate an empty app.org_id

Revision ID: 0008
Revises: 0007

Found by the integration harness running as a non-superuser: once a pooled connection has had
`app.org_id` set transaction-locally, later transactions on that connection see
current_setting('app.org_id', true) = '' (not NULL), and ''::uuid raises. With a superuser this
never surfaced because RLS was bypassed. Every policy now goes through app_org_id(), which maps
'' to NULL, and app_bypass(), so the rule lives in one place.
"""
from alembic import op

revision = "0008"; down_revision = "0007"; branch_labels = None; depends_on = None


def _tenant_tables(conn):
    return [r[0] for r in conn.execute(__import__("sqlalchemy").text("""
        SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_attribute a ON a.attrelid=c.oid AND a.attname='org_id' AND NOT a.attisdropped
        WHERE n.nspname='public' AND c.relkind IN ('r','p') AND NOT c.relispartition AND c.relrowsecurity""")).all()]


def upgrade():
    op.execute("""
    CREATE OR REPLACE FUNCTION app_org_id() RETURNS uuid LANGUAGE sql STABLE AS
      $$ SELECT NULLIF(current_setting('app.org_id', true), '')::uuid $$;
    CREATE OR REPLACE FUNCTION app_bypass() RETURNS boolean LANGUAGE sql STABLE AS
      $$ SELECT current_setting('app.bypass_rls', true) = 'on' $$;
    GRANT EXECUTE ON FUNCTION app_org_id(), app_bypass() TO jobwatch_app;
    """)
    conn = op.get_bind()
    for t in _tenant_tables(conn):
        pols = [r[0] for r in conn.execute(__import__("sqlalchemy").text("SELECT policyname FROM pg_policies WHERE tablename=:t"), {"t": t}).all()]
        for p in pols:
            op.execute(f'DROP POLICY IF EXISTS "{p}" ON {t}')
        op.execute(f"CREATE POLICY {t}_tenant ON {t} USING (org_id = app_org_id()) WITH CHECK (org_id = app_org_id())")
        op.execute(f"CREATE POLICY {t}_bypass ON {t} USING (app_bypass()) WITH CHECK (app_bypass())")
    # app role may read the migration head (tests / health checks), never write it
    op.execute("GRANT SELECT ON alembic_version TO jobwatch_app")


def downgrade():
    conn = op.get_bind()
    for t in _tenant_tables(conn):
        op.execute(f"DROP POLICY IF EXISTS {t}_tenant ON {t}; DROP POLICY IF EXISTS {t}_bypass ON {t}")
        op.execute(f"CREATE POLICY {t}_tenant ON {t} USING (org_id = current_setting('app.org_id', true)::uuid) WITH CHECK (org_id = current_setting('app.org_id', true)::uuid)")
        op.execute(f"CREATE POLICY {t}_bypass ON {t} USING (current_setting('app.bypass_rls', true) = 'on') WITH CHECK (current_setting('app.bypass_rls', true) = 'on')")
    op.execute("REVOKE SELECT ON alembic_version FROM jobwatch_app; DROP FUNCTION IF EXISTS app_org_id(); DROP FUNCTION IF EXISTS app_bypass()")
