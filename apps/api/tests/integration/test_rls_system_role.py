"""R21 — RLS without the OR: tenant queries are isolated AND index-usable; system sessions still
see everything. Runs as jobwatch_app (the CI/harness DATABASE_URL), never as a superuser."""
import os
import uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs postgres (DATABASE_URL)")


@pytest.fixture
def two_tenants(make_job, org):
    from cronsentinel.db import system_session
    other = uuid.uuid4()
    with system_session() as s:
        s.execute(text("INSERT INTO organizations (id, name, slug) VALUES (:i, :n, :n)"), {"i": other, "n": f"rls-{other.hex[:8]}"})
        ws = s.execute(text("INSERT INTO workspaces (org_id, name) VALUES (:o, 'w') RETURNING id"), {"o": other}).scalar()
        s.execute(text("""INSERT INTO jobs (org_id, workspace_id, name, kind, heartbeat_token, status)
            VALUES (:o, :w, 'theirs', 'heartbeat', :t, 'healthy')"""), {"o": other, "w": ws, "t": uuid.uuid4().hex})
    make_job()
    yield org["id"], other
    with system_session() as s:
        s.execute(text("SELECT purge_org(:o)"), {"o": other}); s.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": other})


def test_app_role_holds_system_membership_without_inheriting_it():
    """INHERIT TRUE would make the `TO jobwatch_system USING (true)` policy apply to every tenant
    query — RLS silently off. This is the single most important invariant in migration 0011."""
    from cronsentinel.db import engine
    with engine.connect() as c:
        row = c.execute(text("""SELECT m.inherit_option, m.set_option FROM pg_auth_members m
            JOIN pg_roles r ON r.oid=m.roleid JOIN pg_roles u ON u.oid=m.member
            WHERE r.rolname='jobwatch_system' AND u.rolname=current_user""")).first()
    assert row is not None, "app role cannot SET ROLE jobwatch_system — system sessions would fail"
    assert row.inherit_option is False, "app role INHERITS jobwatch_system: RLS is bypassed for every tenant query"
    assert row.set_option is True


def test_no_tenant_policy_is_ored_with_a_bypass():
    from cronsentinel.db import engine
    with engine.connect() as c:
        bad = c.execute(text("""SELECT tablename, policyname FROM pg_policies
            WHERE schemaname='public' AND ('public' = ANY(roles) OR 'jobwatch_app' = ANY(roles))
              AND (qual ILIKE '%bypass%' OR policyname LIKE '%_bypass')""")).all()
    assert not bad, f"policies still OR a bypass into tenant queries (defeats org_id indexes): {bad}"


def test_tenant_session_sees_only_its_own_rows(two_tenants):
    from cronsentinel.db import tenant_session
    mine, theirs = two_tenants
    with tenant_session(mine) as s:
        assert s.execute(text("SELECT count(*) FROM jobs WHERE org_id=:o"), {"o": theirs}).scalar() == 0
        assert s.execute(text("SELECT count(*) FROM jobs")).scalar() == s.execute(
            text("SELECT count(*) FROM jobs WHERE org_id=:o"), {"o": mine}).scalar() > 0


def test_tenant_session_cannot_write_into_another_tenant(two_tenants):
    from cronsentinel.db import tenant_session
    mine, theirs = two_tenants
    with pytest.raises(Exception, match="(?i)row-level security"):
        with tenant_session(mine) as s:
            s.execute(text("INSERT INTO workspaces (org_id, name) VALUES (:o, 'smuggled')"), {"o": theirs})


def test_no_context_sees_nothing(two_tenants):
    from cronsentinel.db import engine
    with engine.begin() as c:   # plain connection, no tenant GUC, no role switch
        assert c.execute(text("SELECT count(*) FROM jobs")).scalar() == 0


def test_system_session_still_sees_every_tenant(two_tenants):
    from cronsentinel.db import system_session
    mine, theirs = two_tenants
    with system_session() as s:
        orgs = {r[0] for r in s.execute(text("SELECT DISTINCT org_id FROM jobs WHERE org_id IN (:a, :b)"), {"a": mine, "b": theirs}).all()}
    assert orgs == {mine, theirs}


def test_role_switch_does_not_leak_to_the_next_transaction_on_the_same_connection(two_tenants):
    """SET LOCAL ROLE must end with the transaction — pooled connections are reused."""
    from cronsentinel.db import engine
    with engine.connect() as c:
        with c.begin():
            c.execute(text("SET LOCAL ROLE jobwatch_system"))
            assert c.execute(text("SELECT current_user")).scalar() == "jobwatch_system"
        with c.begin():
            assert c.execute(text("SELECT current_user")).scalar() != "jobwatch_system"
            assert c.execute(text("SELECT count(*) FROM jobs")).scalar() == 0


def test_tenant_query_uses_the_org_id_index(two_tenants):
    """The performance half of R21: the policy predicate must be an index condition, not a filter."""
    from cronsentinel.db import tenant_session
    mine, _ = two_tenants
    with tenant_session(mine) as s:
        s.execute(text("SET LOCAL enable_seqscan = off"))   # tiny test tables would otherwise pick a seq scan legitimately
        plan = "\n".join(r[0] for r in s.execute(text("EXPLAIN SELECT count(*) FROM jobs")).all())
    assert "Index" in plan and "org_id" in plan, plan
    assert "bypass" not in plan, plan


# SECURITY DEFINER functions that do DDL (need table ownership, touch partitions directly where the
# parent's RLS does not apply). Everything else that is SECURITY DEFINER must be owned by
# jobwatch_system — see migration 0013.
DDL_DEFINERS = {"ensure_month_partition", "drop_partition_if_empty"}


def test_dml_definer_functions_are_owned_by_the_system_role():
    """A definer function runs as its owner, and FORCE RLS applies to the owner unless it is a
    superuser. Our test owners are superusers, so a wrongly-owned function works here and silently
    does nothing on managed Postgres (demonstrated: purge deleted 0 of 5 rows). This checks the
    ownership invariant directly, since the behaviour itself cannot be seen with a superuser owner."""
    from cronsentinel.db import engine
    with engine.connect() as c:
        rows = c.execute(text("""SELECT p.proname, pg_get_userbyid(p.proowner) FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'public' AND p.prosecdef""")).all()
    wrong = sorted(f"{name} (owner {owner})" for name, owner in rows
                   if name not in DDL_DEFINERS and owner != "jobwatch_system")
    assert not wrong, f"SECURITY DEFINER functions that will hit FORCE RLS as a non-superuser owner: {wrong}"
    assert {n for n, _ in rows} >= {"purge_job", "purge_org", "prune_status_page_job"}
