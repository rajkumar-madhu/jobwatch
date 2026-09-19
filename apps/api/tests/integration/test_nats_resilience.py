"""R8 — connect() must fail fast on an unreachable broker instead of hanging forever."""
import asyncio
import os
import time

import pytest

pytestmark = pytest.mark.skipif(not os.getenv("NATS_URL"), reason="needs nats")


async def test_unreachable_broker_raises_rather_than_hanging(monkeypatch):
    from cronsentinel import events
    from cronsentinel.config import settings
    monkeypatch.setattr(settings, "nats_url", "nats://127.0.0.1:4999", raising=False)
    t0 = time.monotonic()
    with pytest.raises(Exception):
        await asyncio.wait_for(events.connect(max_reconnect_attempts=1), timeout=20)
    assert time.monotonic() - t0 < 20, "a bad NATS_URL must crash the worker, not park it"
    assert not events.is_connected()


async def test_connect_marks_connected():
    from cronsentinel import events
    nc, _ = await events.connect()
    try:
        assert events.is_connected()
    finally:
        await nc.close()


async def test_readyz_fails_when_nats_is_down(monkeypatch):
    """Ready must go false with the broker, or the pod keeps taking traffic it cannot serve."""
    from fastapi.testclient import TestClient

    from cronsentinel import events
    from cronsentinel.main import app
    app.state.js = object()
    monkeypatch.setattr(events, "is_connected", lambda: False)
    with TestClient(app, raise_server_exceptions=False) as c:
        pass
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/readyz").status_code == 503
