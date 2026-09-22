"""WeCrew JobWatch ingest tier — heartbeat endpoints only, scaled independently."""
import asyncio
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from . import events
from .db import system_session
from .routers import agents, heartbeat, k8s
from .workers.platform_health import INGEST_HEARTBEAT_S, heartbeat as platform_heartbeat

log = structlog.get_logger()


async def _heartbeat_loop():
    """R26: ingest is the tier that *sees* executions, so its silence is what makes a slot
    unobserved. Only a live process can heartbeat; a wedged or crashed replica does not, and with
    several replicas the row is shared — any live replica keeps it fresh, which is the right
    semantics (the customer could still be seen). Broker-down does not stop the beat: the router
    falls back to inline processing, so runs are still observed."""
    while True:
        try:
            await asyncio.to_thread(_beat)
        except Exception as e:
            log.warning("ingest heartbeat failed", error=str(e))
        await asyncio.sleep(INGEST_HEARTBEAT_S)


def _beat():
    with system_session() as s:
        platform_heartbeat(s, "ingest", INGEST_HEARTBEAT_S)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.nc, app.state.js = None, None
    try:
        app.state.nc, app.state.js = await events.connect()
    except Exception:
        pass  # sync fallback in router
    hb = asyncio.create_task(_heartbeat_loop())
    yield
    hb.cancel()
    if app.state.nc:
        await app.state.nc.drain()


app = FastAPI(title="JobWatch Ingest", version="0.1.0", lifespan=lifespan, docs_url=None)
app.include_router(heartbeat.router)
app.include_router(agents.agent)
app.include_router(k8s.agent)


@app.get("/healthz")
def healthz():
    return {"status": "ok", "nats": getattr(app.state, "js", None) is not None and events.is_connected()}
