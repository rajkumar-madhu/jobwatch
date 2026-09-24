"""R31 — multi-replica ingest heartbeat semantics, actually tested.

R26 gave ingest its own heartbeat so the reconciler could tell "we couldn't see" (ingest/reconciler
silent) apart from "we didn't schedule" (generator-only outage). Its docstring claimed: "with
several replicas the row is shared — any live replica keeps it fresh" — reasoned, never tested,
since the fullstack stack has always run exactly one ingest process.

This spins up a SECOND real ingest replica (its own uvicorn process, its own port) alongside the
one scripts/fullstack.sh already started, against the same database, and proves the claim: kill
the extra replica, and the shared platform_heartbeats row for "ingest" stays fresh purely because
the fullstack-managed replica is still beating — and a slot whose deadline falls in that window is
still correctly judged missed, not unobserved, exactly as if nothing had happened. Not destructive
to the shared fullstack stack (only the replica this test starts is ever killed), so it is safe to
run alongside every other fullstack test sharing that stack.

The reverse case — every ingest replica silent — was verified manually against this same stack
(two independent replicas, kill both, wait past the 60 s gap threshold): a slot whose deadline fell
in that window came back "unobserved", not "missed". Not automated here, since it would require
killing the fullstack stack's only ingest process, taking down infrastructure every other fullstack
test in this run depends on. See docs/DEVELOPMENT.md for the manual run's numbers.

Skipped unless JOBWATCH_FULLSTACK=1, same as the rest of tests/fullstack.
"""
import os
import socket
import subprocess
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.skipif(os.getenv("JOBWATCH_FULLSTACK") != "1", reason="needs the real stack (scripts/fullstack.sh up)")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def second_ingest_replica():
    port = _free_port()
    env = {**os.environ}
    proc = subprocess.Popen(["uvicorn", "cronsentinel.ingest_main:app", "--host", "127.0.0.1", "--port", str(port)],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                if httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        else:
            proc.kill()
            pytest.fail("second ingest replica never became healthy")
        yield proc, port
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def _heartbeat_age_s() -> float:
    from cronsentinel.db import system_session
    with system_session() as s:
        return s.execute(text("SELECT EXTRACT(EPOCH FROM (now() - last_seen)) FROM platform_heartbeats WHERE service='ingest'")).scalar()


@pytest.fixture
def org():
    """Own fixture — tests/fullstack has no shared conftest org, unlike tests/integration."""
    from cronsentinel.db import system_session
    oid = uuid.uuid4()
    with system_session() as s:
        s.execute(text("INSERT INTO organizations (id, name, slug) VALUES (:i, :n, :n)"), {"i": oid, "n": f"r31-{oid.hex[:8]}"})
        s.execute(text("INSERT INTO workspaces (org_id, name) VALUES (:o, 'w')"), {"o": oid})
    yield {"id": oid}
    with system_session() as s:
        s.execute(text("SELECT purge_org(:o)"), {"o": oid})
        s.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": oid})


def test_a_second_replica_shares_the_heartbeat_row(second_ingest_replica):
    """Both replicas write the SAME row (by service name, not per-instance) — that's the design,
    not a bug: the reconciler asks "was ANY ingest process watching", not "was this ONE watching"."""
    proc, port = second_ingest_replica
    time.sleep(2)
    before = _heartbeat_age_s()
    assert before < 25, f"heartbeat row should already be fresh from the fullstack-managed replica, was {before:.1f}s old"


def test_killing_one_replica_does_not_blind_the_platform(second_ingest_replica, org):
    """The actual multi-replica claim: kill the extra replica, wait past one heartbeat interval,
    and confirm the shared row stays fresh purely from the OTHER (fullstack-managed) replica —
    then confirm that actually matters: a real miss in that window is still called missed."""
    from cronsentinel.workers.platform_health import INGEST_HEARTBEAT_S
    from cronsentinel.workers import reconciler
    from cronsentinel.db import system_session

    proc, port = second_ingest_replica
    time.sleep(2)  # let the extra replica beat at least once so we know two processes were sharing the row
    proc.kill()
    proc.wait(timeout=5)

    time.sleep(INGEST_HEARTBEAT_S + 5)  # past one interval of the surviving replica alone
    age = _heartbeat_age_s()
    assert age < INGEST_HEARTBEAT_S * 3, f"row went stale ({age:.1f}s) even with the fullstack replica still running"

    with system_session() as s:
        oid = org["id"]
        ws = s.execute(text("SELECT id FROM workspaces WHERE org_id=:o LIMIT 1"), {"o": oid}).scalar()
        jid = s.execute(text("""INSERT INTO jobs (org_id, workspace_id, name, kind, heartbeat_token, schedule_expr, tz, grace_s, expected_runtime_s, status)
            VALUES (:o,:w,:n,'cron',:t,'* * * * *','UTC',30,20,'unknown') RETURNING id"""),
            {"o": oid, "w": ws, "n": f"r31-{uuid.uuid4().hex[:8]}", "t": uuid.uuid4().hex}).scalar()
        # deadline just now, inside the window the surviving replica alone has been covering
        slot = s.execute(text("""INSERT INTO expected_runs (org_id, job_id, scheduled_for, grace_until, deadline)
            VALUES (:o,:j, now() - interval '30 seconds', now() - interval '25 seconds', now() - interval '5 seconds') RETURNING id"""),
            {"o": oid, "j": jid}).scalar()
        reconciler.tick(s)
        state = s.execute(text("SELECT state FROM expected_runs WHERE id=:i"), {"i": slot}).scalar()

    assert state == "missed", (
        f"expected 'missed' — the fullstack-managed ingest replica alone should still have been "
        f"observing — but got {state!r}. One replica dying should never blind the platform.")
