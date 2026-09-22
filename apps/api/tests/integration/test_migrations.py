"""Schema sanity against the live DB."""
from sqlalchemy import text


def test_at_head():
    from cronsentinel.db import system_session
    with system_session() as s:
        assert s.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0012"


def test_all_tenant_tables_force_rls():
    from cronsentinel.db import system_session
    with system_session() as s:
        rows = s.execute(text("""SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_attribute a ON a.attrelid=c.oid AND a.attname='org_id' AND NOT a.attisdropped
            WHERE n.nspname='public' AND c.relkind IN ('r','p') AND NOT c.relispartition AND NOT c.relforcerowsecurity""")).scalars().all()
        assert rows == [], f"tables with org_id but RLS not forced: {rows}"


def test_app_role_is_not_superuser_and_cannot_bypass_rls():
    from cronsentinel.db import system_session
    with system_session() as s:
        r = s.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname='jobwatch_app'")).first()
        assert r is not None and not r.rolsuper and not r.rolbypassrls


def test_connected_as_non_superuser():
    """The suite is only meaningful if it runs as the app role — as a superuser every RLS test passes vacuously."""
    from cronsentinel.db import system_session
    with system_session() as s:
        assert not s.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")).scalar()
