"""NATS consumer: exec.* → processor.process → job.status.changed events (consumed by rule engine in Phase 2)."""
import asyncio
import json

import structlog
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy

from .. import events
from ..config import settings
from ..db import system_session
from ..processor import process

log = structlog.get_logger()


def handle_one(s, ev: dict) -> dict | None:
    """Pure-ish step, extracted so the integration harness can drive the pipeline without a loop."""
    res = process(s, ev)
    if res and res["prev_status"] != res["new_status"]:
        return res
    return None


async def drain(js, sub, limit: int = 50, timeout: float = 5) -> int:
    """One fetch/handle/ack pass. Returns how many messages were handled."""
    try:
        msgs = await sub.fetch(limit, timeout=timeout)
    except TimeoutError:
        return 0
    n = 0
    for m in msgs:
        try:
            ev = json.loads(m.data)
            with system_session() as s:
                res = handle_one(s, ev)
            if res:
                await js.publish(f"jobstatus.{res['org_id']}", json.dumps(res).encode())
            await m.ack()
            n += 1
        except Exception as e:
            log.error("process failed", error=str(e))
            await m.nak(delay=5)
    return n


async def subscribe(js, durable: str = "exec-processor"):
    return await js.pull_subscribe("exec.>", durable=durable, stream=settings.nats_stream,
                                   config=ConsumerConfig(ack_policy=AckPolicy.EXPLICIT, deliver_policy=DeliverPolicy.ALL, max_deliver=10, ack_wait=30))


async def main():
    nc, js = await events.connect()
    sub = await js.pull_subscribe("exec.>", durable="exec-processor", stream=settings.nats_stream,
                                  config=ConsumerConfig(ack_policy=AckPolicy.EXPLICIT, deliver_policy=DeliverPolicy.ALL, max_deliver=10, ack_wait=30))
    log.info("exec-processor started")
    while True:
        await drain(js, sub)


if __name__ == "__main__":
    asyncio.run(main())
