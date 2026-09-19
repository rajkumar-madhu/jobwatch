"""NATS consumer: jobstatus.* → alerting.engine.handle_status_change."""
import asyncio
import json

import structlog
from nats.js.api import AckPolicy, ConsumerConfig

from .. import events
from ..alerting.engine import handle_status_change

log = structlog.get_logger()


async def subscribe(js, durable: str = "rule-engine"):
    return await js.pull_subscribe("jobstatus.>", durable=durable, stream=events.STREAM_STATUS,
                                   config=ConsumerConfig(ack_policy=AckPolicy.EXPLICIT, max_deliver=5, ack_wait=30))


async def drain(sub, limit: int = 20, timeout: float = 5) -> int:
    """One fetch/handle/ack pass, extracted for the integration harness."""
    try:
        msgs = await sub.fetch(limit, timeout=timeout)
    except TimeoutError:
        return 0
    n = 0
    for m in msgs:
        try:
            await asyncio.to_thread(handle_status_change, json.loads(m.data))
            await m.ack()
            n += 1
        except Exception as e:
            log.error("rule engine failed", error=str(e)); await m.nak(delay=5)
    return n


async def main():
    nc, js = await events.connect()
    sub = await subscribe(js)
    log.info("rule-engine started")
    while True:
        await drain(sub)


if __name__ == "__main__":
    asyncio.run(main())
