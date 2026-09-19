"""WeCrew JobWatch ingest tier — heartbeat endpoints only, scaled independently."""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import events
from .routers import agents, heartbeat, k8s


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.nc, app.state.js = None, None
    try:
        app.state.nc, app.state.js = await events.connect()
    except Exception:
        pass  # sync fallback in router
    yield
    if app.state.nc:
        await app.state.nc.drain()


app = FastAPI(title="JobWatch Ingest", version="0.1.0", lifespan=lifespan, docs_url=None)
app.include_router(heartbeat.router)
app.include_router(agents.agent)
app.include_router(k8s.agent)


@app.get("/healthz")
def healthz():
    return {"status": "ok", "nats": getattr(app.state, "js", None) is not None and events.is_connected()}
