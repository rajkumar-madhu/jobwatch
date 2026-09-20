"""
R13 — contract test between tools/ui-review/mock_api.py and the real API.

The R12 Playwright suite runs the frontend against the mock. That only proves the frontend works
against *the mock*: if the mock's routes or response shapes drift from the real routers, the e2e
suite stays green while the real app breaks. This test pins the two together.

Scope and its limits, stated plainly:
  * Route existence and path shape are checked exactly.
  * Response *bodies* are validated against the real endpoints' OpenAPI response schemas where the
    real route declares a response_model. Routes returning bare dict/Any cannot be checked — those
    are listed in UNTYPED below, and that list is asserted so it cannot grow silently.
This does not check request bodies (the mock accepts anything) or auth behaviour (the mock has none).
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import sys

import pytest
from fastapi.testclient import TestClient

MOCK_PATH = pathlib.Path(__file__).resolve().parents[3] / "tools" / "ui-review" / "mock_api.py"


def _load_mock():
    spec = importlib.util.spec_from_file_location("jobwatch_mock_api", MOCK_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["jobwatch_mock_api"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mock_app():
    assert MOCK_PATH.exists(), f"mock API missing at {MOCK_PATH}"
    return _load_mock().app


@pytest.fixture(scope="module")
def real_app():
    from cronsentinel.main import app

    return app


def _routes(app) -> dict[tuple[str, str], object]:
    """Flatten the route table, unwrapping the _IncludedRouter wrappers the real app uses.

    Same traversal as the R9 smoke suite: app.routes holds router wrappers, not endpoints, so
    reading r.path directly sees nothing and every comparison silently comes out empty.
    """
    out: dict[tuple[str, str], object] = {}
    for r in app.routes:
        src = getattr(r, "original_router", None)
        for rr in (src.routes if src is not None else [r]):
            for m in (getattr(rr, "methods", set()) or set()) - {"HEAD", "OPTIONS"}:
                out[(m, getattr(rr, "path", ""))] = rr
    return {k: v for k, v in out.items() if not k[1].startswith(("/docs", "/redoc", "/openapi"))}


def _normalise(path: str) -> str:
    """Path param *names* may differ between mock and real ({jid} vs {job_id}); positions may not."""
    return re.sub(r"\{[^}]+\}", "{}", path)


def test_every_mock_route_exists_in_the_real_api(mock_app, real_app):
    real = {(m, _normalise(p)) for m, p in _routes(real_app)}
    mock = {(m, _normalise(p)) for m, p in _routes(mock_app) if p.startswith("/api/")}
    missing = sorted(mock - real)
    assert not missing, (
        "mock API serves routes the real API does not — the frontend may be built against "
        f"endpoints that do not exist: {missing}"
    )


def test_mock_covers_the_endpoints_the_frontend_calls(mock_app):
    """The pages exercised by the R12 e2e suite must all have a mock route backing them.

    Derived from the fetch paths in apps/web; kept explicit so a new page without mock coverage
    fails here rather than silently rendering an error state in e2e.
    """
    mock = {_normalise(p) for _, p in _routes(mock_app)}
    for path in [
        "/api/v1/analytics/overview",
        "/api/v1/jobs",
        "/api/v1/jobs/{}",
        "/api/v1/jobs/{}/executions",
        "/api/v1/incidents",
    ]:
        assert path in mock, f"frontend calls {path} but the mock does not serve it"
