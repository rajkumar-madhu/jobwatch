"""
R13 — does the mock API return the same *shape* as the real API?

R12's Playwright suite proves the frontend works against tools/ui-review/mock_api.py. It proves
nothing about the real backend: if the mock returns `{"items": [...]}` where the real router
returns a bare list, or a field the real one dropped, e2e stays green and production breaks.

The real routers almost all return bare dicts (only 3 of 77 declare a response_model), so there is
no OpenAPI schema to validate against — see test_mock_contract.py for the route-level checks that
need no DB. This test instead calls both APIs for the same endpoint and diffs the key structure.

What "shape" means here: the set of dict keys at each path, and whether a node is a list, a dict or
a leaf. Leaf *values* and types are deliberately not compared — the mock's demo data is meant to
differ. Keys the mock invents that the real API never returns are failures, because a page can be
built on one and will render blank in production.

Two traps this test has to avoid, both of which produced false drift on the first run:
  * An empty real collection has no item paths at all, so every mock item field looks "invented".
    Each compared endpoint therefore seeds a real row, and a still-empty real container SKIPS with
    a reason rather than passing silently — an endpoint that cannot be seeded is not covered, and
    should say so instead of reporting green.
  * Dynamic maps keyed by data (analytics `by_status`) are values, not fields. Their children are
    excluded by DYNAMIC_MAPS below.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import sys
import uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs postgres (DATABASE_URL)")

MOCK_PATH = pathlib.Path(__file__).resolve().parents[4] / "tools" / "ui-review" / "mock_api.py"

# Endpoints the dashboard reads and that the fixture below can seed on the real side. Writes are
# excluded: the mock does not implement them, and the frontend's write paths are covered by the
# R9/R10 backend suites.
#
# Deliberately NOT covered, because seeding a real row needs a cluster/agent/LLM that does not
# exist in this harness — the mock's shape for these is unchecked and that is a known gap:
#   /api/v1/clusters, /api/v1/topology, /api/v1/agents, /api/v1/copilot/*, /api/v1/billing
ENDPOINTS = [
    "/api/v1/jobs",
    "/api/v1/jobs/{job_id}",
    "/api/v1/jobs/{job_id}/executions",
    "/api/v1/incidents",
    "/api/v1/analytics/overview",
    "/api/v1/workspaces",
    "/api/v1/alerts/rules",
    "/api/v1/alerts/channels",
    "/api/v1/status-pages",
    "/api/v1/dependencies",
]

# Paths whose child keys are data, not schema: the map is keyed by status/tag values.
DYNAMIC_MAPS = ("$.by_status",)


@pytest.fixture(scope="module")
def mock_client():
    assert MOCK_PATH.exists(), f"mock API missing at {MOCK_PATH}"
    from fastapi.testclient import TestClient
    spec = importlib.util.spec_from_file_location("jobwatch_mock_api", MOCK_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["jobwatch_mock_api"] = mod
    spec.loader.exec_module(mod)
    return TestClient(mod.app, raise_server_exceptions=False)


@pytest.fixture
def real_client():
    from fastapi.testclient import TestClient
    from cronsentinel.main import app
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def key(org):
    from cronsentinel.auth import generate_api_key, hash_secret
    from cronsentinel.db import system_session
    prefix, raw = generate_api_key()
    with system_session() as s:
        s.execute(text("INSERT INTO api_keys (org_id, name, prefix, key_hash, role) VALUES (:o,'contract',:p,:h,'owner')"),
                  {"o": org["id"], "p": prefix, "h": hash_secret(raw)})
    return raw


@pytest.fixture
def seeded(org, key):
    """One populated row per compared collection, with optional fields filled.

    Tags, durations and exit codes are set deliberately: a NULL or empty value on the real side
    makes the corresponding mock field look invented.
    """
    from cronsentinel.db import system_session
    with system_session() as s:
        ws = s.execute(text("SELECT id FROM workspaces WHERE org_id=:o LIMIT 1"), {"o": org["id"]}).scalar()
        jobs = []
        for name in ("contract-a", "contract-b"):
            jobs.append(s.execute(text("""INSERT INTO jobs (org_id, workspace_id, name, kind, heartbeat_token,
                schedule_expr, tz, grace_s, expected_runtime_s, status, tags, description)
                VALUES (:o,:w,:n,'cron',:t,'*/5 * * * *','UTC',60,30,'healthy',ARRAY['prod','db'],'seeded') RETURNING id"""),
                {"o": org["id"], "w": ws, "n": name, "t": uuid.uuid4().hex}).scalar())
        job, dep = jobs
        ex = f"contract-{uuid.uuid4().hex}"
        s.execute(text("""INSERT INTO executions (id, org_id, job_id, status, scheduled_ts, server_received_ts,
            agent_ts_start, agent_ts_end, duration_ms, exit_code, host)
            VALUES (:e,:o,:j,'success',now(),now(),now(),now(),1234,0,'host-1')"""), {"e": ex, "o": org["id"], "j": job})
        s.execute(text("""INSERT INTO incidents (org_id, severity, status, title, started_at, affected_job_ids)
            VALUES (:o,'high','open','contract',now(),ARRAY[:j]::uuid[])"""), {"o": org["id"], "j": job})
        ch = s.execute(text("""INSERT INTO notification_channels (org_id, name, kind, config_enc, enabled)
            VALUES (:o,'contract','webhook',:c,true) RETURNING id"""),
            {"o": org["id"], "c": b""}).scalar()
        s.execute(text("""INSERT INTO alert_rules (org_id, name, condition, severity, scope, params, channel_ids, enabled)
            VALUES (:o,'contract','failed','high','{}'::jsonb,'{}'::jsonb,ARRAY[:c]::uuid[],true)"""),
            {"o": org["id"], "c": ch})
        s.execute(text("""INSERT INTO status_pages (org_id, title, slug, job_ids, visibility)
            VALUES (:o,'contract',:s,ARRAY[:j]::uuid[],'public')"""),
            {"o": org["id"], "s": f"c{uuid.uuid4().hex[:8]}", "j": job})
        s.execute(text("""INSERT INTO job_dependencies (org_id, job_id, depends_on_job_id)
            VALUES (:o,:j,:d)"""), {"o": org["id"], "j": job, "d": dep})
    return {"job_id": str(job), "execution_id": ex}


def shape(node, path="$", out=None):
    """Flatten a JSON document to {path: kind}. List items are merged at [*] so order and length
    do not matter — one item is enough to describe the collection."""
    out = {} if out is None else out
    if isinstance(node, dict):
        out[path] = "object"
        for k, v in node.items():
            shape(v, f"{path}.{k}", out)
    elif isinstance(node, list):
        out[path] = "array"
        for item in node:
            shape(item, f"{path}[*]", out)
    else:
        out.setdefault(path, "leaf")
    return out


def _mock_job_id(mock_client) -> str:
    """The mock keys its fixtures by its own uuid5 ids and 500s on anything else, so path params
    must come from the mock itself rather than from the real DB."""
    body = mock_client.get("/api/v1/jobs").json()
    items = body["items"] if isinstance(body, dict) else body
    return items[0]["id"]


def _both(real_client, mock_client, key, seeded, path):
    real_url = path.replace("{job_id}", seeded["job_id"])
    mock_url = path.replace("{job_id}", _mock_job_id(mock_client))
    real = real_client.get(real_url, headers={"X-API-Key": key})
    mock = mock_client.get(mock_url)
    assert real.status_code == 200, f"real API returned {real.status_code} for {path}: {real.text[:200]}"
    assert mock.status_code == 200, f"mock returned {mock.status_code} for {path}: {mock.text[:200]}"
    return real.json(), mock.json()


def _empty(node) -> bool:
    return node in ([], {}, None)


@pytest.mark.parametrize("path", ENDPOINTS)
def test_mock_invents_no_fields_the_real_api_does_not_return(real_client, mock_client, key, seeded, path):
    real, mock = _both(real_client, mock_client, key, seeded, path)
    if _empty(real):
        pytest.skip(f"real {path} returned empty even after seeding — item shape is not covered here")

    real_shape, mock_shape = shape(real), shape(mock)
    invented = sorted(
        p for p in set(mock_shape) - set(real_shape)
        if not p.startswith(DYNAMIC_MAPS)
        # A path under an empty real container tells us nothing; only compare where real has data.
        and not any(parent in real_shape and _empty_at(real, parent) for parent in _parents(p))
    )
    assert not invented, (
        f"{path}: the mock returns fields the real API does not — a page built against these "
        f"will render blank in production: {invented}"
    )


def _parents(path: str):
    parts, cur = path.replace("[*]", ".[*]").split("."), ""
    for part in parts:
        cur = part if not cur else f"{cur}.{part}" if part != "[*]" else f"{cur}[*]"
        yield cur


def _empty_at(doc, path: str) -> bool:
    node = doc
    for part in path.replace("[*]", ".[*]").split(".")[1:]:
        if part == "[*]":
            if not isinstance(node, list) or not node:
                return True
            node = node[0]
        else:
            if not isinstance(node, dict) or part not in node:
                return True
            node = node[part]
    return _empty(node)


@pytest.mark.parametrize("path", ENDPOINTS)
def test_container_kind_matches(real_client, mock_client, key, seeded, path):
    """A bare list vs {"items": [...]} is the drift most likely to slip through: both render as
    'no data' in the UI rather than as an error."""
    real, mock = _both(real_client, mock_client, key, seeded, path)
    assert type(real) is type(mock), f"{path}: real returns {type(real).__name__}, mock returns {type(mock).__name__}"
