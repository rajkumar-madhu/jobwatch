"""NATS JetStream publish/consume helpers."""
import json

import nats
from nats.js.api import RetentionPolicy, StreamConfig

from .config import settings

SUBJECT_EXEC = "exec.>"
SUBJECT_STATUS = "jobstatus.>"


async def connect():
    nc = await nats.connect(settings.nats_url, max_reconnect_attempts=-1)
    js = nc.jetstream()
    try:
        await js.add_stream(StreamConfig(name=settings.nats_stream, subjects=[SUBJECT_EXEC, SUBJECT_STATUS], retention=RetentionPolicy.WORK_QUEUE))
    except Exception:
        pass  # exists
    return nc, js


async def publish_exec_event(js, org_id: str, payload: dict):
    await js.publish(f"exec.{org_id}", json.dumps(payload, default=str).encode())
