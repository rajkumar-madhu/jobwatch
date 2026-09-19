"""Requires live Postgres (DATABASE_URL). Skipped otherwise."""
import os
import uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs postgres")


def test_rls_blocks_cross_tenant():
    from cronsentinel.db import system_session, tenant_session
    a, b = uuid.uuid4(), uuid.uuid4()
    with system_session() as s:
        for o in (a, b):
            s.execute(text("INSERT INTO organizations (id, name, slug) VALUES (:id, :n, :n)"), {"id": o, "n": f"t-{o}"})
            ws = s.execute(text("INSERT INTO workspaces (org_id, name) VALUES (:o, 'w') RETURNING id"), {"o": o}).scalar()
            s.execute(text("INSERT INTO jobs (org_id, workspace_id, name, heartbeat_token) VALUES (:o, :w, 'j', :t)"), {"o": o, "w": ws, "t": str(o)})
    with tenant_session(a) as s:
        assert s.execute(text("SELECT count(*) FROM jobs")).scalar() == 1
        assert s.execute(text("SELECT count(*) FROM jobs WHERE org_id=:b"), {"b": b}).scalar() == 0
