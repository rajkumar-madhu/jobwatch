"""JobWatch API service."""
import logging
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from . import events
from .config import settings
from .db import engine
from .routers import agents, alerting, analytics, auth as authr, billing, copilot, executions, incidents, jobs, k8s, logs, org, overview, status_pages, topology
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from fastapi.responses import Response
import time as _time

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
for r in (jobs.router, executions.router, org.router, overview.router, alerting.router, incidents.router, agents.admin, logs.router, authr.router, status_pages.router, status_pages.public, k8s.router, topology.router, analytics.router, copilot.router, billing.router):
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
    with engine.connect() as c:
        c.execute(text("SELECT 1"))
    return {"status": "ready", "nats": app.state.js is not None}


@app.get("/metrics")
def metrics():
    # TODO: ingest lag + reconciler lag gauges pushed from workers (needs pushgateway or shared registry)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
