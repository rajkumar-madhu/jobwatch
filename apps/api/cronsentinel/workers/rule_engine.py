"""NATS consumer: jobstatus.* → alerting.engine.handle_status_change."""
import asyncio
import json

import structlog
from nats.js.api import AckPolicy, ConsumerConfig

from .. import events
from ..alerting.engine import handle_status_change
from ..config import settings

log = structlog.get_logger()


async def main():
    nc, js = await events.connect()
    sub = await js.pull_subscribe("jobstatus.>", durable="rule-engine", stream=settings.nats_stream,
                                  config=ConsumerConfig(ack_policy=AckPolicy.EXPLICIT, max_deliver=5, ack_wait=30))
    log.info("rule-engine started")
    while True:
        try:
            msgs = await sub.fetch(20, timeout=5)
        except TimeoutError:
            continue
        for m in msgs:
            try:
                await asyncio.to_thread(handle_status_change, json.loads(m.data))
                await m.ack()
            except Exception as e:
                log.error("rule engine failed", error=str(e)); await m.nak(delay=5)


if __name__ == "__main__":
    asyncio.run(main())
