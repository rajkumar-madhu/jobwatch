"""NATS JetStream publish/consume helpers.

Two streams, deliberately:

* `CS_EXEC` — `exec.>`, WORK_QUEUE retention. Ingest hands each execution event to exactly one
  exec-processor; acking removes it. Competing consumers on the same durable scale horizontally.
* `CS_STATUS` — `jobstatus.>`, INTEREST retention. Status changes fan out to *several* independent
  durables (rule-engine, outbound-exporter, and anything added later).

They must not be one stream. A WORK_QUEUE stream permits only one filtered consumer per subject
("filtered consumer not unique on workqueue stream") and deletes a message once any consumer acks
it — so the second subscriber to `jobstatus.>` fails to start, and if it did start it would steal
messages from the first. Found by the R6 pipeline harness.
"""
import json

import nats
import structlog
from nats.js.api import RetentionPolicy, StreamConfig

from .config import settings

log = structlog.get_logger()

SUBJECT_EXEC = "exec.>"
SUBJECT_STATUS = "jobstatus.>"
STREAM_STATUS = "CS_STATUS"


CONNECT_TIMEOUT_S = 5
MAX_RECONNECT = 30          # ~30 attempts * 2s wait ≈ 1 min of outage tolerated, then the process exits
RECONNECT_WAIT_S = 2

# Set by the connection callbacks so a readiness probe can fail while the broker is unreachable
# instead of the pod sitting Ready and quietly processing nothing.
_connected = False


def is_connected() -> bool:
    return _connected


async def connect(max_reconnect_attempts: int = MAX_RECONNECT):
    """Connect, or raise. The old default (-1) retried forever: a typo'd NATS_URL left a worker
    hanging in `await connect()` — Ready, logging nothing, consuming nothing — instead of
    crash-looping where an operator would see it. Bounded retries let the supervisor restart us,
    and the callbacks keep `is_connected()` honest for probes."""
    global _connected

    async def _closed(*_):
        global _connected
        _connected = False
        log.error("nats connection closed")

    async def _disconnected(*_):
        global _connected
        _connected = False
        log.warning("nats disconnected")

    async def _reconnected(*_):
        global _connected
        _connected = True
        log.info("nats reconnected")

    nc = await nats.connect(settings.nats_url, max_reconnect_attempts=max_reconnect_attempts,
                            reconnect_time_wait=RECONNECT_WAIT_S, connect_timeout=CONNECT_TIMEOUT_S,
                            closed_cb=_closed, disconnected_cb=_disconnected, reconnected_cb=_reconnected)
    js = nc.jetstream()
    await ensure_streams(js)
    _connected = True
    log.info("nats connected", url=settings.nats_url)
    return nc, js


async def ensure_streams(js):
    for cfg in (
        StreamConfig(name=settings.nats_stream, subjects=[SUBJECT_EXEC], retention=RetentionPolicy.WORK_QUEUE),
        StreamConfig(name=STREAM_STATUS, subjects=[SUBJECT_STATUS], retention=RetentionPolicy.INTEREST),
    ):
        try:
            await js.add_stream(cfg)
        except Exception:
            try:
                await js.update_stream(cfg)   # widen/repair an existing stream (e.g. pre-split deploys)
            except Exception:
                pass  # exists and is already compatible, or we lack permission to change it


async def publish_exec_event(js, org_id: str, payload: dict):
    await js.publish(f"exec.{org_id}", json.dumps(payload, default=str).encode())


async def publish_status_event(js, org_id: str, payload: dict):
    await js.publish(f"jobstatus.{org_id}", json.dumps(payload, default=str).encode())
