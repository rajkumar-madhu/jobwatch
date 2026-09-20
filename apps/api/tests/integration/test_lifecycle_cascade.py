"""R11 — what actually happens to related rows when a job, agent or org goes away.

Most child tables cascade via FK, but the high-volume ones (`executions`, `expected_runs`,
`execution_events`, `host_metrics`, ...) are partitioned and carry **no** FK to jobs or
organizations, and several references live in `uuid[]` columns where a FK is impossible
(`status_pages.job_ids`, `incidents.affected_job_ids`). Those are exactly the paths nothing had
ever exercised. This suite pins the intended behaviour so a regression is loud.
"""
import os
import uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs postgres")


def _count(s, table, where, **p):
    return s.execute(text(f"SELECT count(*) FROM {table} WHERE {where}"), p).scalar()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from cronsentinel.main import app
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def key(org):
    from cronsentinel.auth import generate_api_key, hash_secret
    from cronsentinel.db import system_session
    prefix, raw = generate_api_key()
    with system_session() as s:
        s.execute(text("INSERT INTO api_keys (org_id, name, prefix, key_hash, role) VALUES (:o,'lc',:p,:h,'owner')"),
                  {"o": org["id"], "p": prefix, "h": hash_secret(raw)})
    return raw


@pytest.fixture
def job_with_history(org):
    """A job carrying every kind of dependent row we care about."""
    from cronsentinel.db import system_session
    from cronsentinel.workers.schedule_generator import tick
    with system_session() as s:
        ws = s.execute(text("SELECT id FROM workspaces WHERE org_id=:o LIMIT 1"), {"o": org["id"]}).scalar()
        job = s.execute(text("""INSERT INTO jobs (org_id, workspace_id, name, kind, heartbeat_token, schedule_expr, tz, grace_s, expected_runtime_s, created_at, status)
            VALUES (:o,:w,'lc','cron',:t,'*/5 * * * *','UTC',60,30, now() - interval '30 minutes','unknown') RETURNING id"""),
            {"o": org["id"], "w": ws, "t": uuid.uuid4().hex}).scalar()
        other = s.execute(text("""INSERT INTO jobs (org_id, workspace_id, name, kind, heartbeat_token, status)
            VALUES (:o,:w,'lc-other','cron',:t,'unknown') RETURNING id"""), {"o": org["id"], "w": ws, "t": uuid.uuid4().hex}).scalar()
        ex = f"lc-{uuid.uuid4().hex}"
        s.execute(text("""INSERT INTO executions (id, org_id, job_id, status, scheduled_ts, server_received_ts)
            VALUES (:e,:o,:j,'success',now(),now())"""), {"e": ex, "o": org["id"], "j": job})
        # execution_events is keyed by execution_id (text ULID) — it has no job_id column
        s.execute(text("""INSERT INTO execution_events (org_id, execution_id, kind, sequence, payload, server_ts)
            VALUES (:o,:e,'success',1,'{}',now())"""), {"o": org["id"], "e": ex})
        s.execute(text("INSERT INTO job_dependencies (org_id, job_id, depends_on_job_id) VALUES (:o,:a,:b)"),
                  {"o": org["id"], "a": other, "b": job})
        inc = s.execute(text("""INSERT INTO incidents (org_id, severity, status, title, started_at, affected_job_ids)
            VALUES (:o,'high','open','lc',now(),ARRAY[:j]::uuid[]) RETURNING id"""), {"o": org["id"], "j": job}).scalar()
        sp = s.execute(text("""INSERT INTO status_pages (org_id, title, slug, job_ids, visibility)
            VALUES (:o,'p',:s,ARRAY[:j]::uuid[],'public') RETURNING id"""),
            {"o": org["id"], "s": f"lc{uuid.uuid4().hex[:8]}", "j": job}).scalar()
        tick(s)
    return {"job": job, "other": other, "execution": ex, "incident": inc, "page": sp, "org": org["id"]}


def test_fixture_really_created_dependents(job_with_history):
    from cronsentinel.db import system_session
    j = job_with_history["job"]
    with system_session() as s:
        assert _count(s, "expected_runs", "job_id=:j", j=j) > 0
        assert _count(s, "executions", "job_id=:j", j=j) == 1
        assert _count(s, "job_dependencies", "depends_on_job_id=:j", j=j) == 1


def test_deleting_a_job_removes_its_slots_and_executions(client, key, job_with_history):
    """Otherwise the reconciler keeps settling slots for a job that no longer exists, and the
    rows sit in the tenant's partitions forever — neither is FK-protected."""
    from cronsentinel.db import system_session
    j = job_with_history["job"]
    assert client.delete(f"/api/v1/jobs/{j}", headers={"X-API-Key": key}).status_code in (200, 204)
    with system_session() as s:
        assert _count(s, "expected_runs", "job_id=:j", j=j) == 0, "orphan expected_runs left behind"
        assert _count(s, "executions", "job_id=:j", j=j) == 0, "orphan executions left behind"
        assert _count(s, "execution_events", "execution_id=:e", e=job_with_history["execution"]) == 0, "orphan execution_events left behind"


def test_deleting_a_job_cascades_its_dependency_edges(client, key, job_with_history):
    from cronsentinel.db import system_session
    j = job_with_history["job"]
    client.delete(f"/api/v1/jobs/{j}", headers={"X-API-Key": key})
    with system_session() as s:
        assert _count(s, "job_dependencies", "job_id=:j OR depends_on_job_id=:j", j=j) == 0


def test_deleting_a_job_drops_it_from_status_page_arrays(client, key, job_with_history):
    """`status_pages.job_ids` is a uuid[] — no FK can protect it, so the handler must prune it or
    the public page renders a phantom job."""
    from cronsentinel.db import system_session
    j = job_with_history["job"]
    client.delete(f"/api/v1/jobs/{j}", headers={"X-API-Key": key})
    with system_session() as s:
        left = s.execute(text("SELECT job_ids FROM status_pages WHERE id=:p"), {"p": job_with_history["page"]}).scalar()
    assert str(j) not in [str(x) for x in left], "deleted job still referenced by a status page"


def test_public_status_page_survives_a_deleted_job(client, key, job_with_history):
    from cronsentinel.db import system_session
    j = job_with_history["job"]
    with system_session() as s:
        slug = s.execute(text("SELECT slug FROM status_pages WHERE id=:p"), {"p": job_with_history["page"]}).scalar()
    assert client.get(f"/public/status/{slug}").status_code == 200
    client.delete(f"/api/v1/jobs/{j}", headers={"X-API-Key": key})
    r = client.get(f"/public/status/{slug}")
    assert r.status_code == 200, f"public page broke after a job was deleted: {r.text[:200]}"


def test_incident_detail_survives_a_deleted_job(client, key, job_with_history):
    """`incidents.affected_job_ids` keeps history deliberately, so the *detail view* must tolerate
    an id that no longer resolves rather than 500."""
    j, inc = job_with_history["job"], job_with_history["incident"]
    client.delete(f"/api/v1/jobs/{j}", headers={"X-API-Key": key})
    r = client.get(f"/api/v1/incidents/{inc}", headers={"X-API-Key": key})
    assert r.status_code == 200, f"incident detail broke after its job was deleted: {r.text[:200]}"


def test_revoking_an_agent_leaves_its_jobs_but_marks_them_unknown(org, key, client):
    """An agent going away is an agent problem (R2): its jobs must not be deleted, and must not
    march into FAILING either."""
    from cronsentinel.db import system_session
    from cronsentinel.workers import reconciler
    with system_session() as s:
        ws = s.execute(text("SELECT id FROM workspaces WHERE org_id=:o LIMIT 1"), {"o": org["id"]}).scalar()
        a = s.execute(text("""INSERT INTO agents (org_id, kind, name, key_hash, key_prefix, status, last_seen_at, heartbeat_interval_s)
            VALUES (:o,'linux','gone','x',:p,'active', now() - interval '1 hour', 60) RETURNING id"""),
            {"o": org["id"], "p": f"csa_{uuid.uuid4().hex[:6]}"}).scalar()
        sv = s.execute(text("INSERT INTO servers (org_id, agent_id, hostname) VALUES (:o,:a,:h) RETURNING id"),
                       {"o": org["id"], "a": a, "h": f"h{uuid.uuid4().hex[:6]}"}).scalar()
        j = s.execute(text("""INSERT INTO jobs (org_id, workspace_id, name, kind, heartbeat_token, server_id, schedule_expr, tz, grace_s, status)
            VALUES (:o,:w,'agentjob','cron',:t,:s,'*/5 * * * *','UTC',60,'healthy') RETURNING id"""),
            {"o": org["id"], "w": ws, "t": uuid.uuid4().hex, "s": sv}).scalar()
    assert client.post(f"/api/v1/agents/{a}/revoke", headers={"X-API-Key": key}).status_code in (200, 204)
    with system_session() as s:
        assert _count(s, "jobs", "id=:j", j=j) == 1, "revoking an agent must not delete its jobs"
        reconciler.recompute_state(s, j, org["id"])
        st = s.execute(text("SELECT job_state::text, unknown_reason FROM jobs WHERE id=:j"), {"j": j}).first()
    assert st.job_state == "unknown" and st.unknown_reason == "agent_offline"


def test_deleting_an_org_leaves_no_rows_behind(org):
    """Tenant offboarding must actually remove the data. The partitioned tables have no FK, so
    this is the check that they are purged rather than quietly retained."""
    from cronsentinel.db import system_session
    from cronsentinel.workers.schedule_generator import tick
    oid = org["id"]
    with system_session() as s:
        ws = s.execute(text("SELECT id FROM workspaces WHERE org_id=:o LIMIT 1"), {"o": oid}).scalar()
        j = s.execute(text("""INSERT INTO jobs (org_id, workspace_id, name, kind, heartbeat_token, schedule_expr, tz, grace_s, created_at, status)
            VALUES (:o,:w,'x','cron',:t,'*/5 * * * *','UTC',60, now() - interval '20 minutes','unknown') RETURNING id"""),
            {"o": oid, "w": ws, "t": uuid.uuid4().hex}).scalar()
        ex = f"org-{uuid.uuid4().hex}"
        s.execute(text("INSERT INTO executions (id, org_id, job_id, status, scheduled_ts, server_received_ts) VALUES (:e,:o,:j,'success',now(),now())"),
                  {"e": ex, "o": oid, "j": j})
        s.execute(text("INSERT INTO execution_events (org_id, execution_id, kind, sequence, payload, server_ts) VALUES (:o,:e,'success',1,'{}',now())"),
                  {"o": oid, "e": ex})
        s.execute(text("INSERT INTO audit_logs (org_id, actor_type, action, target_type, target_id, payload) VALUES (:o,'user','x','job',:j,'{}')"),
                  {"o": oid, "j": str(j)})
        tick(s)
        s.execute(text("SELECT purge_org(:o)"), {"o": oid})
        s.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": oid})
    with system_session() as s:
        for t in ("executions", "expected_runs", "execution_events", "audit_logs", "host_metrics",
                  "copilot_sessions", "incident_events", "k8s_events", "notification_ledger", "signal_deliveries"):
            assert _count(s, t, "org_id=:o", o=oid) == 0, f"{t} still holds rows for a deleted org"
