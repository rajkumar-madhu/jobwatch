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
from nats.js.api import RetentionPolicy, StreamConfig

from .config import settings

SUBJECT_EXEC = "exec.>"
SUBJECT_STATUS = "jobstatus.>"
STREAM_STATUS = "CS_STATUS"


async def connect(max_reconnect_attempts: int = -1):
    nc = await nats.connect(settings.nats_url, max_reconnect_attempts=max_reconnect_attempts)
    js = nc.jetstream()
    await ensure_streams(js)
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
