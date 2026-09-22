"""R22 — /agent/v1/events on the ingest app. Until R22 no test called it: the R9 smoke suite walks
the main API's routes only, so the agents' main write path was covered by nothing but the load
test. Runs with app.state.js = None, i.e. the inline-processing path, so results land in the DB."""
import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs postgres (DATABASE_URL)")


@pytest.fixture
def agent(org):
    from cronsentinel.auth import hash_secret
    from cronsentinel.db import system_session
    key = "csa_" + uuid.uuid4().hex + uuid.uuid4().hex
    with system_session() as s:
        aid = s.execute(text("""INSERT INTO agents (org_id, kind, host_id, name, version, status, key_hash, key_prefix)
            VALUES (:o, 'linux', :h, 'it', '1', 'active', :kh, :kp) RETURNING id"""),
            {"o": org["id"], "h": f"h-{uuid.uuid4().hex[:6]}", "kh": hash_secret(key), "kp": key[:12]}).scalar()
        tok_job = s.execute(text("""INSERT INTO jobs (org_id, workspace_id, name, kind, heartbeat_token, status)
            VALUES (:o, :w, 'by-token', 'heartbeat', :t, 'healthy') RETURNING id, heartbeat_token"""),
            {"o": org["id"], "w": org["ws"], "t": uuid.uuid4().hex}).first()
        fp_job = s.execute(text("""INSERT INTO jobs (org_id, workspace_id, name, kind, heartbeat_token, fingerprint, status)
            VALUES (:o, :w, 'by-fp', 'cron', :t, :f, 'healthy') RETURNING id, fingerprint"""),
            {"o": org["id"], "w": org["ws"], "t": uuid.uuid4().hex, "f": f"fp-{uuid.uuid4().hex}"}).first()
    yield {"id": str(aid), "key": key, "tok_job": tok_job, "fp_job": fp_job}
    with system_session() as s:
        s.execute(text("DELETE FROM agents WHERE id=:a"), {"a": aid})


@pytest.fixture
def ingest():
    from fastapi.testclient import TestClient
    from cronsentinel.ingest_main import app
    app.state.js = None          # inline processing: no broker in this test
    return TestClient(app, raise_server_exceptions=False)


def _post(ingest, agent, events):
    return ingest.post("/agent/v1/events", json={"events": events}, headers={"X-Agent-Id": agent["id"], "X-Agent-Key": agent["key"]})


def _now():
    return datetime.now(UTC).isoformat()


def test_batch_resolves_by_token_and_fingerprint_and_drops_unknown(ingest, agent):
    e1, e2 = f"t-{uuid.uuid4().hex}", f"f-{uuid.uuid4().hex}"
    r = _post(ingest, agent, [
        {"job_token": agent["tok_job"].heartbeat_token, "kind": "success", "execution_id": e1, "sequence": 1, "agent_ts": _now(), "exit_code": 0},
        {"fingerprint": agent["fp_job"].fingerprint, "kind": "fail", "execution_id": e2, "sequence": 1, "agent_ts": _now(), "exit_code": 3},
        {"job_token": "nope", "kind": "success", "execution_id": "x", "sequence": 1, "agent_ts": _now()},
    ])
    assert r.status_code == 200, r.text
    assert r.json() == {"accepted": 2, "dropped": 1}
    from cronsentinel.db import system_session
    with system_session() as s:
        rows = dict(s.execute(text("SELECT id, status::text FROM executions WHERE id IN (:a, :b)"), {"a": e1, "b": e2}).all())
    assert rows == {e1: "success", e2: "failed"}


def test_start_and_success_in_one_batch_end_as_success(ingest, agent):
    """Order matters: a batch carrying start then success for one execution must not end 'running'."""
    eid = f"o-{uuid.uuid4().hex}"
    tok = agent["tok_job"].heartbeat_token
    r = _post(ingest, agent, [
        {"job_token": tok, "kind": "start", "execution_id": eid, "sequence": 0, "agent_ts": _now()},
        {"job_token": tok, "kind": "success", "execution_id": eid, "sequence": 1, "agent_ts": _now(), "duration_ms": 1200, "exit_code": 0},
    ])
    assert r.status_code == 200 and r.json()["accepted"] == 2
    from cronsentinel.db import system_session
    with system_session() as s:
        assert s.execute(text("SELECT status::text FROM executions WHERE id=:e"), {"e": eid}).scalar() == "success"


def test_progress_events_are_stored(ingest, agent):
    eid = f"p-{uuid.uuid4().hex}"
    r = _post(ingest, agent, [{"job_token": agent["tok_job"].heartbeat_token, "kind": "progress", "execution_id": eid, "sequence": 3, "agent_ts": _now()}])
    assert r.status_code == 200 and r.json() == {"accepted": 1, "dropped": 0}
    from cronsentinel.db import system_session
    with system_session() as s:
        assert s.execute(text("SELECT count(*) FROM execution_events WHERE execution_id=:e AND kind='progress'"), {"e": eid}).scalar() == 1


def test_wrong_agent_key_is_rejected(ingest, agent):
    # R25: was key[:-1] + "0" — the key is hex, so 1 run in 16 the last char already is "0" and the
    # "wrong" key equalled the right one (a 200 that looked like an auth hole in the full suite).
    flipped = "1" if agent["key"][-1] == "0" else "0"
    r = ingest.post("/agent/v1/events", json={"events": []}, headers={"X-Agent-Id": agent["id"], "X-Agent-Key": agent["key"][:-1] + flipped})
    assert r.status_code == 401


def test_agent_cannot_resolve_another_orgs_job(ingest, agent):
    """Tokens are resolved within the agent's org only."""
    from cronsentinel.db import system_session
    other = uuid.uuid4()
    with system_session() as s:
        s.execute(text("INSERT INTO organizations (id, name, slug) VALUES (:i, :n, :n)"), {"i": other, "n": f"x-{other.hex[:8]}"})
        ws = s.execute(text("INSERT INTO workspaces (org_id, name) VALUES (:o, 'w') RETURNING id"), {"o": other}).scalar()
        tok = s.execute(text("INSERT INTO jobs (org_id, workspace_id, name, kind, heartbeat_token, status) VALUES (:o, :w, 'theirs', 'heartbeat', :t, 'healthy') RETURNING heartbeat_token"),
                        {"o": other, "w": ws, "t": uuid.uuid4().hex}).scalar()
    try:
        r = _post(ingest, agent, [{"job_token": tok, "kind": "success", "execution_id": f"c-{uuid.uuid4().hex}", "sequence": 1, "agent_ts": _now()}])
        assert r.json() == {"accepted": 0, "dropped": 1}
    finally:
        with system_session() as s:
            s.execute(text("SELECT purge_org(:o)"), {"o": other}); s.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": other})
