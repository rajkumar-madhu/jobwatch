"""R10 — the RBAC matrix: every write endpoint, every role.

R9 proved endpoints respond; it only checked the unauthenticated boundary. `require_role(...)` had
never been verified: nothing showed that a viewer key cannot delete a job, or that the decorator is
even attached to the endpoints that need it. This suite asserts the whole grid, and derives the
expected role from the live route table so a missing decorator shows up as a failure rather than
as silence.
"""
import os
import uuid

import pytest
from sqlalchemy import text

from cronsentinel.auth import ROLE_ORDER

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs postgres")

# Endpoints deliberately outside the role system, with the reason.
UNGATED = {
    "/api/v1/billing/webhook": "authenticated by Stripe signature, not by a principal",
    "/api/v1/jobs/schedule/preview": "pure cron-expression utility, touches no tenant data",
}
# Calls that would leave the process (Stripe, LLM, outbound HTTP). The 403 case is still checked —
# authorisation is decided before any external call — but the allowed case is not invoked.
EXTERNAL = {
    "/api/v1/billing/checkout", "/api/v1/billing/portal", "/api/v1/copilot/ask",
    "/api/v1/alerts/channels/{channel_id}/test", "/api/v1/integrations/destinations/{dest_id}/test",
}


def required_roles(app) -> dict[tuple[str, str], str | None]:
    """(method, path) -> role required by require_role(), read off the live dependency graph."""
    import inspect
    out = {}
    for r in app.routes:
        src = getattr(r, "original_router", None)
        for rr in (src.routes if src is not None else [r]):
            m = sorted(getattr(rr, "methods", set()) - {"HEAD", "OPTIONS"})
            dep = getattr(rr, "dependant", None)
            if not m or not dep:
                continue
            role = None
            for d in dep.dependencies:
                if getattr(d.call, "__qualname__", "").startswith("require_role"):
                    role = inspect.getclosurevars(d.call).nonlocals.get("role")
            out[(m[0], rr.path)] = role
    return out


@pytest.fixture(scope="module")
def app():
    from cronsentinel.main import app as a
    return a


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from cronsentinel.main import app
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def keys(org):
    """One API key per role, all in the same org."""
    from cronsentinel.auth import generate_api_key, hash_secret
    from cronsentinel.db import system_session
    out = {}
    with system_session() as s:
        for role in ROLE_ORDER:
            prefix, raw = generate_api_key()
            s.execute(text("INSERT INTO api_keys (org_id, name, prefix, key_hash, role) VALUES (:o,:n,:p,:h,:r)"),
                      {"o": org["id"], "n": role, "p": prefix, "h": hash_secret(raw), "r": role})
            out[role] = raw
    return out


@pytest.fixture
def seeded(org):
    from cronsentinel.crypto import encrypt_json
    from cronsentinel.db import system_session
    with system_session() as s:
        ws = s.execute(text("SELECT id FROM workspaces WHERE org_id=:o LIMIT 1"), {"o": org["id"]}).scalar()
        job = s.execute(text("""INSERT INTO jobs (org_id, workspace_id, name, kind, heartbeat_token, schedule_expr, tz, grace_s, status)
            VALUES (:o,:w,'rbac','cron',:t,'*/5 * * * *','UTC',60,'unknown') RETURNING id"""),
            {"o": org["id"], "w": ws, "t": uuid.uuid4().hex}).scalar()
        inc = s.execute(text("""INSERT INTO incidents (org_id, severity, status, title, started_at, affected_job_ids)
            VALUES (:o,'high','open','rbac',now(),ARRAY[:j]::uuid[]) RETURNING id"""), {"o": org["id"], "j": job}).scalar()
        rule = s.execute(text("""INSERT INTO alert_rules (org_id, name, condition, scope, params, severity, channel_ids, enabled)
            VALUES (:o,'r','failed','{}','{}','high','{}',true) RETURNING id"""), {"o": org["id"]}).scalar()
        chan = s.execute(text("""INSERT INTO notification_channels (org_id, name, kind, config_enc)
            VALUES (:o,'c','webhook',:c) RETURNING id"""), {"o": org["id"], "c": encrypt_json({"url": "https://x.test/h"})}).scalar()
        dest = s.execute(text("""INSERT INTO signal_destinations (org_id, name, kind, config_enc)
            VALUES (:o,'d','webhook',:c) RETURNING id"""), {"o": org["id"], "c": encrypt_json({"url": "https://a.test/i"})}).scalar()
        mw = s.execute(text("""INSERT INTO maintenance_windows (org_id, starts_at, ends_at, scope)
            VALUES (:o,now(),now()+interval '1 hour','{}') RETURNING id"""), {"o": org["id"]}).scalar()
        agent = s.execute(text("""INSERT INTO agents (org_id, kind, name, key_hash, key_prefix, status)
            VALUES (:o,'linux','a','x',:p,'active') RETURNING id"""), {"o": org["id"], "p": f"csa_{uuid.uuid4().hex[:6]}"}).scalar()
        sp = s.execute(text("""INSERT INTO status_pages (org_id, title, slug, job_ids)
            VALUES (:o,'p',:s,'{}') RETURNING id"""), {"o": org["id"], "s": f"sp{uuid.uuid4().hex[:6]}"}).scalar()
        key_id = s.execute(text("SELECT id FROM api_keys WHERE org_id=:o LIMIT 1"), {"o": org["id"]}).scalar()
    return {"workspace_id": str(ws), "job_id": str(job), "incident_id": str(inc), "rule_id": str(rule),
            "channel_id": str(chan), "dest_id": str(dest), "mw_id": str(mw), "agent_id": str(agent),
            "page_id": str(sp), "key_id": str(key_id), "org_id": str(org["id"])}


BODIES = {
    ("POST", "/api/v1/jobs"): lambda s: {"workspace_id": s["workspace_id"], "name": f"n{uuid.uuid4().hex[:6]}", "schedule_expr": "*/5 * * * *", "grace_s": 60},
    ("PATCH", "/api/v1/jobs/{job_id}"): lambda s: {"description": f"r{uuid.uuid4().hex[:6]}"},
    ("POST", "/api/v1/workspaces"): lambda s: {"name": f"w{uuid.uuid4().hex[:6]}"},
    ("POST", "/api/v1/api-keys"): lambda s: {"name": "k", "role": "viewer"},
    ("POST", "/api/v1/agents/bootstrap-token"): lambda s: {"kind": "linux"},
    ("POST", "/api/v1/alerts/channels"): lambda s: {"name": "c", "kind": "webhook", "config": {"url": "https://x.test/h"}},
    ("POST", "/api/v1/alerts/rules"): lambda s: {"name": "r", "condition": "failed", "severity": "high", "scope": {}, "params": {}, "channel_ids": []},
    ("PATCH", "/api/v1/alerts/rules/{rule_id}"): lambda s: {"enabled": False},
    ("POST", "/api/v1/alerts/maintenance"): lambda s: {"starts_at": "2030-01-01T00:00:00Z", "ends_at": "2030-01-01T01:00:00Z", "scope": {}},
    ("POST", "/api/v1/dependencies"): lambda s: {"job_id": s["job_id"], "depends_on_job_id": s["job_id"]},
    ("POST", "/api/v1/status-pages"): lambda s: {"title": "p", "slug": f"s{uuid.uuid4().hex[:6]}", "job_ids": []},
    ("POST", "/api/v1/integrations/destinations"): lambda s: {"name": "d", "kind": "webhook", "url": "https://aegis.test/i"},
    ("PUT", "/api/v1/integrations/destinations/{dest_id}"): lambda s: {"name": "d", "kind": "webhook", "url": "https://aegis.test/i"},
    ("POST", "/api/v1/incidents/{incident_id}/notes"): lambda s: {"body": "n"},
    ("POST", "/api/v1/copilot/ask"): lambda s: {"question": "why did it fail?"},
}
QUERY = {("DELETE", "/api/v1/dependencies"): {"job_id": "{job_id}", "depends_on_job_id": "{job_id}"}}


def fill(path, seeded):
    for k, v in seeded.items():
        path = path.replace("{" + k + "}", v)
    while "{" in path:
        n = path.split("{", 1)[1].split("}", 1)[0]
        path = path.replace("{" + n + "}", str(uuid.uuid4()))
    return path


def gated_writes(app):
    out = []
    for (m, p), role in required_roles(app).items():
        if m == "GET" or not p.startswith("/api/v1") or p in UNGATED:
            continue
        out.append((m, p, role))
    return sorted(out, key=lambda x: (x[1], x[0]))


def _call(client, m, p, seeded, key):
    q = QUERY.get((m, p))
    return client.request(m, fill(p, seeded), headers={"X-API-Key": key},
                          json=BODIES.get((m, p), lambda s: {})(seeded),
                          params={k: fill(v, seeded) for k, v in q.items()} if q else None)


@pytest.mark.parametrize("method,path,role", gated_writes(__import__("cronsentinel.main", fromlist=["app"]).app))
def test_roles_below_the_threshold_are_refused(client, seeded, keys, method, path, role):
    assert role is not None, f"{method} {path} is a write with no require_role() — add one or list it in UNGATED"
    below = ROLE_ORDER[:ROLE_ORDER.index(role)]
    for r in below:
        resp = _call(client, method, path, seeded, keys[r])
        assert resp.status_code == 403, f"{r} reached {method} {path} -> {resp.status_code}: {resp.text[:160]}"


@pytest.mark.parametrize("method,path,role", gated_writes(__import__("cronsentinel.main", fromlist=["app"]).app))
def test_the_lowest_permitted_role_is_allowed(client, seeded, keys, method, path, role):
    if path in EXTERNAL:
        pytest.skip("would call an external service")
    resp = _call(client, method, path, seeded, keys[role])
    assert resp.status_code != 403, f"{role} was refused its own endpoint {method} {path}: {resp.text[:160]}"
    assert resp.status_code < 500, f"{method} {path} -> {resp.status_code}: {resp.text[:200]}"


def test_owner_can_reach_everything_below_it(client, seeded, keys, app):
    for m, p, role in gated_writes(app):
        if p in EXTERNAL:
            continue
        assert _call(client, m, p, seeded, keys["owner"]).status_code != 403, f"owner refused {m} {p}"


def test_viewer_can_still_read(client, seeded, keys):
    for p in ("/api/v1/jobs", "/api/v1/incidents", "/api/v1/analytics/overview", "/api/v1/topology"):
        assert client.get(p, headers={"X-API-Key": keys["viewer"]}).status_code == 200


def test_role_is_taken_from_the_key_not_the_request(client, seeded, keys):
    """A viewer cannot promote itself by asserting a role in a header."""
    r = client.request("DELETE", f"/api/v1/jobs/{seeded['job_id']}",
                       headers={"X-API-Key": keys["viewer"], "X-Role": "owner", "X-Org-Id": seeded["org_id"]})
    assert r.status_code == 403


def test_a_key_cannot_act_on_another_tenants_objects(client, seeded, keys, org):
    """Cross-tenant writes must 404 (RLS hides the row), never 200."""
    from cronsentinel.auth import generate_api_key, hash_secret
    from cronsentinel.db import system_session
    other = uuid.uuid4()
    with system_session() as s:
        s.execute(text("INSERT INTO organizations (id, name, slug) VALUES (:i,:n,:n)"), {"i": other, "n": f"o{other.hex[:8]}"})
        prefix, raw = generate_api_key()
        s.execute(text("INSERT INTO api_keys (org_id, name, prefix, key_hash, role) VALUES (:o,'x',:p,:h,'owner')"),
                  {"o": other, "p": prefix, "h": hash_secret(raw)})
    try:
        for m, p in [("DELETE", f"/api/v1/jobs/{seeded['job_id']}"),
                     ("PATCH", f"/api/v1/jobs/{seeded['job_id']}"),
                     ("DELETE", f"/api/v1/status-pages/{seeded['page_id']}"),
                     ("DELETE", f"/api/v1/integrations/destinations/{seeded['dest_id']}")]:
            r = client.request(m, p, headers={"X-API-Key": raw}, json={"description": "x", "title": "x", "slug": "x", "kind": "webhook", "url": "https://a.test/i", "name": "x"})
            assert r.status_code == 404, f"{m} {p} across tenants -> {r.status_code}"
    finally:
        with system_session() as s:
            s.execute(text("DELETE FROM organizations WHERE id=:i"), {"i": other})


def test_revoked_key_is_refused(client, seeded, org):
    from cronsentinel.auth import generate_api_key, hash_secret
    from cronsentinel.db import system_session
    prefix, raw = generate_api_key()
    with system_session() as s:
        s.execute(text("""INSERT INTO api_keys (org_id, name, prefix, key_hash, role, revoked_at)
            VALUES (:o,'dead',:p,:h,'owner', now())"""), {"o": org["id"], "p": prefix, "h": hash_secret(raw)})
    assert client.get("/api/v1/jobs", headers={"X-API-Key": raw}).status_code == 401
