"""
R14 — validate the UI-review mock's responses against the real API's OpenAPI schemas.

R13 could only diff key structure, because only 3 of 77 endpoints declared a response_model and
the spec described everything else as "object, additionalProperties: true". R14 added models to
the dashboard read endpoints, so those endpoints now have a real schema the mock can be checked
against — including field *types*, which the R13 key-diff never saw.

This runs without a database: it reads the spec off the app object and calls only the mock.
Endpoints still without a model are listed in UNMODELLED and asserted, so the list shrinks
deliberately rather than the coverage quietly staying flat.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

jsonschema = pytest.importorskip("jsonschema", reason="jsonschema not installed")

MOCK_PATH = pathlib.Path(__file__).resolve().parents[3] / "tools" / "ui-review" / "mock_api.py"

# real path -> mock path. Only endpoints that now declare a response_model.
COVERED = {
    "/api/v1/jobs": "/api/v1/jobs",
    "/api/v1/incidents": "/api/v1/incidents",
    "/api/v1/alerts/channels": "/api/v1/alerts/channels",
    "/api/v1/alerts/rules": "/api/v1/alerts/rules",
    "/api/v1/status-pages": "/api/v1/status-pages",
    "/api/v1/workspaces": "/api/v1/workspaces",
    "/api/v1/analytics/overview": "/api/v1/analytics/overview",
    "/api/v1/platform/monitoring-gaps": "/api/v1/platform/monitoring-gaps",   # R25
    "/api/v1/dependencies": "/api/v1/dependencies",
    # R15 additions
    "/api/v1/agents": "/api/v1/agents",
    "/api/v1/clusters": "/api/v1/clusters",
    "/api/v1/topology": "/api/v1/topology",
    "/api/v1/analytics/jobs": "/api/v1/analytics/jobs",
    # R16 additions
    "/api/v1/logs/search": "/api/v1/logs/search",
    "/api/v1/integrations/destinations": "/api/v1/integrations/destinations",
    "/api/v1/integrations/deliveries": "/api/v1/integrations/deliveries",
    "/api/v1/integrations/schema": "/api/v1/integrations/schema",
}

# Read endpoints the dashboard uses that still return a bare dict. Each one is a gap: the mock's
# shape for it is unvalidated. Shrink this list by adding a response_model, do not delete entries.
# Read endpoints that still return a bare dict, *discovered from the route table* (see
# test_every_read_endpoint_is_modelled_or_acknowledged). Before R16 this was a hand-written list and
# the guard only checked the paths already on it — so R4's integrations reads, never listed, were
# never flagged. Every entry below is a GET whose response shape nothing validates.
UNMODELLED: set[str] = {
    # R29: a compliance export, not a dashboard read — its shape is a nested tree of whichever
    # sections exist for the org, not worth a matching pydantic tree for a one-off admin action.
    "/api/v1/org/export",
    # Generated from the route table at R16 — detail/drill-down reads off the dashboard's main
    # paths. Each is a known gap: nothing validates its response shape.
    "/api/v1/alerts/ledger",
    "/api/v1/alerts/maintenance",
    "/api/v1/analytics/mttr",
    "/api/v1/analytics/report",
    "/api/v1/api-keys",
    "/api/v1/audit-logs",
    "/api/v1/billing",
    "/api/v1/clusters/{cluster_id}/cronjobs",
    "/api/v1/copilot/history",
    "/api/v1/copilot/suggestions",
    "/api/v1/executions/{execution_id}",
    "/api/v1/executions/{execution_id}/compare/{other_id}",
    "/api/v1/incidents/{incident_id}",
    "/api/v1/jobs/{job_id}/impact",
    "/api/v1/jobs/{job_id}/next-runs",
    "/api/v1/me",
}


@pytest.fixture(scope="module")
def spec():
    from cronsentinel.main import app

    return app.openapi()


@pytest.fixture(scope="module")
def mock_client():
    from fastapi.testclient import TestClient

    assert MOCK_PATH.exists(), f"mock API missing at {MOCK_PATH}"
    s = importlib.util.spec_from_file_location("jobwatch_mock_api", MOCK_PATH)
    mod = importlib.util.module_from_spec(s)
    sys.modules["jobwatch_mock_api"] = mod
    s.loader.exec_module(mod)
    return TestClient(mod.app, raise_server_exceptions=False)


def _schema_for(spec: dict, path: str) -> dict | None:
    op = spec["paths"].get(path, {}).get("get")
    if not op:
        return None
    sc = op.get("responses", {}).get("200", {}).get("content", {}).get("application/json", {}).get("schema")
    if not sc or (sc.get("additionalProperties") is True and "properties" not in sc):
        return None
    # Resolve $refs against the spec itself so nested models validate too.
    return {**sc, "components": spec.get("components", {})}


@pytest.mark.parametrize("real_path,mock_path", sorted(COVERED.items()), ids=sorted(COVERED))
def test_mock_response_validates_against_the_real_schema(spec, mock_client, real_path, mock_path):
    schema = _schema_for(spec, real_path)
    assert schema is not None, f"{real_path} is listed as covered but declares no response_model"

    r = mock_client.get(mock_path)
    assert r.status_code == 200, f"mock returned {r.status_code} for {mock_path}"

    # Nullable columns are typed `X | None`, which FastAPI emits as anyOf — jsonschema handles it.
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(r.json()), key=lambda e: list(e.absolute_path))
    assert not errors, "mock response violates the real API's schema:\n" + "\n".join(
        f"  $.{'.'.join(str(p) for p in e.absolute_path)}: {e.message}" for e in errors[:15]
    )


def _bare_reads(spec) -> set[str]:
    return {p for p, v in spec["paths"].items()
            if p.startswith("/api/v1/") and "get" in v and _schema_for(spec, p) is None}


def test_every_read_endpoint_is_modelled_or_acknowledged(spec):
    """Scans the whole route table. A new GET without a response_model fails here until it is either
    modelled or consciously added to UNMODELLED — this is the check R15 claimed to have and didn't."""
    unacknowledged = sorted(_bare_reads(spec) - UNMODELLED)
    assert not unacknowledged, f"read endpoints with no response_model and no UNMODELLED entry: {unacknowledged}"


def test_unmodelled_list_has_no_stale_entries(spec):
    """The other direction: once an endpoint gains a model, its UNMODELLED entry must go."""
    stale = sorted(UNMODELLED - _bare_reads(spec))
    assert not stale, f"these now declare a response_model (or no longer exist) — remove from UNMODELLED: {stale}"
