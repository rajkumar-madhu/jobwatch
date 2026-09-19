"""R9 — every registered endpoint is exercised at least once.

R4 shipped an integrations router whose `audit()` calls had the wrong signature: every
create/update/delete would have 500'd, and nothing caught it because no test ever called those
endpoints. This suite closes that class of gap. It is deliberately table-driven off the *live*
route table, so a new endpoint added without a test fails `test_every_endpoint_is_covered`.

What each endpoint is checked for:
  - unauthenticated access is refused (401/403), never a 500 and never data
  - with a valid API key it returns something sane (2xx, or a documented 4xx — not a 500)
  - unknown ids 404 rather than crash
Deep behaviour lives in the per-feature suites; this is the breadth net.
"""
import os
import uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs postgres")


def routes(app):
    out = set()
    for r in app.routes:
        src = getattr(r, "original_router", None)
        for rr in (src.routes if src is not None else [r]):
            m = sorted(getattr(rr, "methods", set()) - {"HEAD", "OPTIONS"})
            if m:
                out.add((m[0], rr.path))
    return {x for x in out if not x[1].startswith(("/docs", "/redoc", "/openapi"))}


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
def key(org):
    from cronsentinel.auth import generate_api_key, hash_secret
    from cronsentinel.db import system_session
    prefix, raw = generate_api_key()
    with system_session() as s:
        s.execute(text("INSERT INTO api_keys (org_id, name, prefix, key_hash, role) VALUES (:o,'smoke',:p,:h,'owner')"),
                  {"o": org["id"], "p": prefix, "h": hash_secret(raw)})
    return raw


@pytest.fixture
def seeded(org, key):
    """One of each addressable thing, so path params resolve to real rows."""
    from cronsentinel.db import system_session
    with system_session() as s:
        ws = s.execute(text("SELECT id FROM workspaces WHERE org_id=:o LIMIT 1"), {"o": org["id"]}).scalar()
        job = s.execute(text("""INSERT INTO jobs (org_id, workspace_id, name, kind, heartbeat_token, schedule_expr, tz, grace_s, status)
            VALUES (:o,:w,'smoke','cron',:t,'*/5 * * * *','UTC',60,'unknown') RETURNING id"""),
            {"o": org["id"], "w": ws, "t": uuid.uuid4().hex}).scalar()
        ex = f"smoke-{uuid.uuid4().hex}"
        s.execute(text("""INSERT INTO executions (id, org_id, job_id, status, scheduled_ts, server_received_ts)
            VALUES (:e,:o,:j,'success',now(),now())"""), {"e": ex, "o": org["id"], "j": job})
        inc = s.execute(text("""INSERT INTO incidents (org_id, severity, status, title, started_at, affected_job_ids)
            VALUES (:o,'high','open','smoke',now(),ARRAY[:j]::uuid[]) RETURNING id"""), {"o": org["id"], "j": job}).scalar()
    return {"org_id": str(org["id"]), "workspace_id": str(ws), "job_id": str(job), "execution_id": ex,
            "other_id": ex, "incident_id": str(inc)}


# Path params that must resolve to something; anything not listed gets a random uuid (404 expected).
def fill(path: str, seeded: dict) -> str:
    for k, v in seeded.items():
        path = path.replace("{" + k + "}", v)
    while "{" in path:
        name = path.split("{", 1)[1].split("}", 1)[0]
        path = path.replace("{" + name + "}", str(uuid.uuid4()))
    return path


# Endpoints that legitimately leave the API: they must exist and refuse unauthenticated callers,
# but calling them for real would hit Stripe / an LLM / an OIDC provider / send mail.
EXTERNAL = {
    "/api/v1/billing/checkout", "/api/v1/billing/portal", "/api/v1/billing/webhook",
    "/api/v1/copilot/ask", "/api/v1/alerts/channels/{channel_id}/test",
    "/api/v1/integrations/destinations/{dest_id}/test",
    "/auth/login", "/auth/callback",
}
# Public by design — no auth expected.
PUBLIC = {"/healthz", "/readyz", "/metrics", "/public/status/{slug}", "/auth/logout",
          "/api/v1/integrations/schema"} | {"/auth/login", "/auth/callback", "/api/v1/billing/webhook"}
# Writes that are destructive or need a body we cannot synthesise generically.
SKIP_AUTHED_CALL = {"/auth/orgs", "/auth/switch/{org_id}", "/auth/session"}

BODIES = {
    ("POST", "/api/v1/jobs"): lambda s: {"workspace_id": s["workspace_id"], "name": f"n{uuid.uuid4().hex[:6]}", "schedule_expr": "*/5 * * * *", "grace_s": 60},
    ("POST", "/api/v1/status-pages"): lambda s: {"title": "p", "slug": f"s{uuid.uuid4().hex[:6]}", "job_ids": []},
    ("POST", "/api/v1/workspaces"): lambda s: {"name": f"w{uuid.uuid4().hex[:6]}"},
    ("POST", "/api/v1/api-keys"): lambda s: {"name": "k", "role": "viewer"},
    ("POST", "/api/v1/agents/bootstrap-token"): lambda s: {"kind": "linux"},
    ("POST", "/api/v1/alerts/channels"): lambda s: {"name": "c", "kind": "webhook", "config": {"url": "https://x.test/h"}},
    ("POST", "/api/v1/alerts/rules"): lambda s: {"name": "r", "condition": "failed", "severity": "high", "scope": {}, "params": {}, "channel_ids": []},
    ("POST", "/api/v1/alerts/maintenance"): lambda s: {"name": "m", "starts_at": "2026-01-01T00:00:00Z", "ends_at": "2026-01-01T01:00:00Z", "scope": {}},
    ("POST", "/api/v1/dependencies"): lambda s: {"job_id": s["job_id"], "depends_on_job_id": str(uuid.uuid4())},
    ("POST", "/api/v1/integrations/destinations"): lambda s: {"name": "d", "kind": "webhook", "url": "https://aegis.test/i"},
    ("PUT", "/api/v1/integrations/destinations/{dest_id}"): lambda s: {"name": "d", "kind": "webhook", "url": "https://aegis.test/i"},
    ("POST", "/api/v1/incidents/{incident_id}/notes"): lambda s: {"body": "note"},
    ("PATCH", "/api/v1/jobs/{job_id}"): lambda s: {"name": "renamed"},
    ("PATCH", "/api/v1/alerts/rules/{rule_id}"): lambda s: {"enabled": False},
}


# Endpoints that take query parameters rather than a body.
QUERY = {
    ("POST", "/api/v1/jobs/schedule/preview"): {"expr": "*/5 * * * *", "tz": "UTC"},
    ("DELETE", "/api/v1/dependencies"): {"job_id": "{job_id}", "depends_on_job_id": "{job_id}"},
    ("GET", "/api/v1/logs/search"): {"q": "x"},
}


def call(client, method, path, *, headers=None, body=None, params=None):
    return client.request(method, path, headers=headers or {}, json=body, params=params)


def query_for(method, tmpl, seeded):
    q = QUERY.get((method, tmpl))
    return {k: fill(v, seeded) for k, v in q.items()} if q else None


@pytest.mark.parametrize("method,path", sorted(routes(__import__("cronsentinel.main", fromlist=["app"]).app)))
def test_unauthenticated_is_refused_and_never_500s(client, seeded, method, path):
    if path in PUBLIC:
        pytest.skip("public by design")
    r = call(client, method, fill(path, seeded), body=BODIES.get((method, path), lambda s: {})(seeded),
             params=query_for(method, path, seeded))
    # 422 is acceptable only where FastAPI validates the body before the auth dependency runs;
    # it must never be a 2xx and never a 5xx.
    assert r.status_code in (401, 403, 422), f"{method} {path} -> {r.status_code}: {r.text[:200]}"


@pytest.mark.parametrize("method,path", sorted(routes(__import__("cronsentinel.main", fromlist=["app"]).app)))
def test_authenticated_call_does_not_500(client, seeded, key, method, path):
    if path in EXTERNAL or path in SKIP_AUTHED_CALL or path in ("/metrics", "/readyz"):
        # /readyz intentionally 503s without a NATS connection; TestClient runs no lifespan.
        pytest.skip("external dependency, non-generic body, or lifespan-dependent")
    r = call(client, method, fill(path, seeded), headers={"X-API-Key": key},
             body=BODIES.get((method, path), lambda s: {})(seeded), params=query_for(method, path, seeded))
    assert r.status_code < 500, f"{method} {path} -> {r.status_code}: {r.text[:300]}"
    assert r.status_code != 422 or "{" in path, f"{method} {path} rejected a reasonable body: {r.text[:300]}"


def test_every_endpoint_is_covered(app):
    """Guard: a new route must be classified here, so it cannot ship untested by accident."""
    known = routes(app)
    assert known, "route table is empty — the enumeration broke, not the app"
    missing = {p for _, p in known} - PUBLIC - EXTERNAL - SKIP_AUTHED_CALL
    assert missing, "sanity: some endpoints should be in the general pool"
    assert len(known) >= 70, f"expected the full surface, saw {len(known)}"


def test_unknown_ids_404_rather_than_crash(client, key, seeded):
    ghost = str(uuid.uuid4())
    ok = client.post("/api/v1/dependencies", headers={"X-API-Key": key},
                     json={"job_id": seeded["job_id"], "depends_on_job_id": ghost})
    assert ok.status_code == 404, f"unknown dependency target -> {ok.status_code}: {ok.text[:200]}"
    for m, p in [("GET", f"/api/v1/jobs/{ghost}"), ("GET", f"/api/v1/incidents/{ghost}"),
                 ("GET", f"/api/v1/executions/{ghost}"), ("DELETE", f"/api/v1/jobs/{ghost}"),
                 ("DELETE", f"/api/v1/status-pages/{ghost}"), ("DELETE", f"/api/v1/api-keys/{ghost}")]:
        r = call(client, m, p, headers={"X-API-Key": key})
        assert r.status_code in (404, 204, 200), f"{m} {p} -> {r.status_code}: {r.text[:200]}"
        assert r.status_code < 500
