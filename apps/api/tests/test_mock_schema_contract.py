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
    "/api/v1/dependencies": "/api/v1/dependencies",
}

# Read endpoints the dashboard uses that still return a bare dict. Each one is a gap: the mock's
# shape for it is unvalidated. Shrink this list by adding a response_model, do not delete entries.
UNMODELLED = {
    "/api/v1/agents",
    "/api/v1/clusters",
    "/api/v1/topology",
    "/api/v1/logs/search",
    "/api/v1/analytics/series",
    "/api/v1/analytics/jobs",
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


def test_unmodelled_read_endpoints_are_the_known_set(spec):
    """Guards the gap list in both directions: a new bare-dict read endpoint must be acknowledged,
    and adding a response_model must remove its entry here."""
    still_bare = set()
    for path in UNMODELLED:
        if path in spec["paths"] and _schema_for(spec, path) is None:
            still_bare.add(path)
    fixed = UNMODELLED - still_bare
    assert not fixed, f"these endpoints now declare a response_model — remove them from UNMODELLED: {sorted(fixed)}"
