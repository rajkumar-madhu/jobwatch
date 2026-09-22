"""JobWatch API service."""
import logging
import time as _time
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from sqlalchemy import text

from . import events
from .config import settings
from .db import engine
from .routers import (
    agents,
    alerting,
    analytics,
    billing,
    copilot,
    executions,
    incidents,
    integrations,
    jobs,
    k8s,
    logs,
    org,
    gdpr,
    overview,
    platform,
    status_pages,
    topology,
)
from .routers import auth as authr

logging.basicConfig(level=settings.log_level)
log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.nc, app.state.js = None, None
    try:
        app.state.nc, app.state.js = await events.connect()
    except Exception as e:
        log.warning("nats unavailable, using sync fallback", error=str(e))
    yield
    if app.state.nc:
        await app.state.nc.drain()


app = FastAPI(title="JobWatch API", version="0.1.0", lifespan=lifespan, docs_url="/docs", openapi_url="/openapi.json")
app.add_middleware(CORSMiddleware, allow_origins=[settings.web_public_url], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
for r in (jobs.router, executions.router, org.router, overview.router, alerting.router, incidents.router, agents.admin, logs.router, authr.router, status_pages.router, status_pages.public, k8s.router, topology.router, analytics.router, copilot.router, billing.router, integrations.router, platform.router, gdpr.router):
    app.include_router(r)


HTTP_LAT = Histogram("cronsentinel_http_request_seconds", "HTTP latency", ["method", "path", "status"])
HTTP_REQ = Counter("cronsentinel_http_requests_total", "HTTP requests", ["method", "path", "status"])


@app.middleware("http")
async def security_headers(request: Request, call_next):
    t0 = _time.perf_counter()
    resp = await call_next(request)
    path = request.scope.get("route").path if request.scope.get("route") else "unmatched"
    HTTP_LAT.labels(request.method, path, resp.status_code).observe(_time.perf_counter() - t0)
    HTTP_REQ.labels(request.method, path, resp.status_code).inc()
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    if hasattr(request.state, "rl_remaining"):
        resp.headers["X-RateLimit-Remaining"] = str(request.state.rl_remaining)
    return resp


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    """R8: `js is not None` stayed true after the broker went away, so the pod kept serving Ready
    while nothing could be published. Report the live connection state instead, and fail the probe
    when the broker is unreachable so traffic is shed rather than silently dropped."""
    with engine.connect() as c:
        c.execute(text("SELECT 1"))
    # getattr: app.state.js is only set by the lifespan handler, so any caller that reaches
    # /readyz before startup finished (or in a test without lifespan) got AttributeError -> 500
    # instead of an honest 'not ready'.
    nats_ok = getattr(app.state, "js", None) is not None and events.is_connected()
    if not nats_ok:
        raise HTTPException(503, "nats unavailable")
    return {"status": "ready", "nats": True}


@app.get("/metrics")
def metrics():
    # TODO: ingest lag + reconciler lag gauges pushed from workers (needs pushgateway or shared registry)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
