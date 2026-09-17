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


async def main():
    nc, js = await events.connect()
    sub = await js.pull_subscribe("exec.>", durable="exec-processor", stream=settings.nats_stream,
                                  config=ConsumerConfig(ack_policy=AckPolicy.EXPLICIT, deliver_policy=DeliverPolicy.ALL, max_deliver=10, ack_wait=30))
    log.info("exec-processor started")
    while True:
        try:
            msgs = await sub.fetch(50, timeout=5)
        except asyncio.TimeoutError:
            continue
        for m in msgs:
            try:
                ev = json.loads(m.data)
                with system_session() as s:
                    res = process(s, ev)
                if res and res["prev_status"] != res["new_status"]:
                    await js.publish(f"jobstatus.{ev['org_id']}", json.dumps(res).encode())
                await m.ack()
            except Exception as e:
                log.error("process failed", error=str(e))
                await m.nak(delay=5)


if __name__ == "__main__":
    asyncio.run(main())
