"""R5 integration harness. Needs DATABASE_URL pointing at a Postgres with migrations at head.
Everything here talks to the real DB through the same session helpers the app uses; NATS and
Celery are not required (workers' tick() functions are called directly, deliveries are patched)."""
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs postgres (DATABASE_URL)")


@pytest.fixture(scope="session", autouse=True)
def _require_db():
    if not os.getenv("DATABASE_URL"):
        pytest.skip("needs postgres")


@pytest.fixture
def org():
    from cronsentinel.db import system_session
    oid = uuid.uuid4()
    with system_session() as s:
        s.execute(text("INSERT INTO organizations (id, name, slug) VALUES (:id, :n, :n)"), {"id": oid, "n": f"it-{oid.hex[:8]}"})
        ws = s.execute(text("INSERT INTO workspaces (org_id, name) VALUES (:o, 'w') RETURNING id"), {"o": oid}).scalar()
    yield {"id": oid, "ws": ws}
    with system_session() as s:
        # execution_events has no FK to organizations (append-only ledger); clean it explicitly
        s.execute(text("DELETE FROM execution_events WHERE org_id=:id"), {"id": oid})
        s.execute(text("DELETE FROM organizations WHERE id=:id"), {"id": oid})


@pytest.fixture
def make_job(org):
    from cronsentinel.db import system_session

    def _mk(schedule="*/5 * * * *", grace_s=60, expected_runtime_s=30, created_ago=timedelta(minutes=30), **kw):
        with system_session() as s:
            return s.execute(text("""INSERT INTO jobs (org_id, workspace_id, name, kind, heartbeat_token, schedule_expr, tz, grace_s,
                expected_runtime_s, created_at, status) VALUES (:o, :w, :n, 'cron', :t, :sch, 'UTC', :g, :r, :c, 'unknown') RETURNING id"""),
                {"o": org["id"], "w": org["ws"], "n": kw.get("name", "it-job"), "t": uuid.uuid4().hex, "sch": schedule, "g": grace_s,
                 "r": expected_runtime_s, "c": datetime.now(UTC) - created_ago}).scalar()
    return _mk


def now():
    return datetime.now(UTC)
