"""
R17 — the whole product, end to end, on real processes.

Every earlier suite tested a piece: ingest in one test, the processor in another, the rule engine
with a hand-fed message. Nothing proved that a heartbeat arriving at ingest ends up as a webhook
landing on the customer's endpoint. This does, against the stack started by scripts/fullstack.sh
(API, ingest, exec-processor, schedule-generator, reconciler, rule-engine, outbound-exporter and
the Celery notifier, over real Postgres, NATS JetStream and Redis).

Everything is driven through public HTTP: the job, channel and rule are created via the API; the
heartbeats go to ingest; the result is read back from the API and from a webhook receiver. The DB
is touched only to mint the first API key and set the plan — there is no endpoint for either
without a Keycloak login.

Skipped unless JOBWATCH_FULLSTACK=1, because it needs the stack running and takes ~2 minutes
(the missed-run leg has to wait for a real schedule slot to pass).
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.skipif(os.getenv("JOBWATCH_FULLSTACK") != "1",
                                reason="needs scripts/fullstack.sh up; set JOBWATCH_FULLSTACK=1")

API = f"http://127.0.0.1:{os.getenv('API_PORT', '18000')}"
INGEST = f"http://127.0.0.1:{os.getenv('INGEST_PORT', '18010')}"


class _Hook(BaseHTTPRequestHandler):
    received: list[dict] = []

    def do_POST(self):  # noqa: N802
        raw = self.rfile.read(int(self.headers.get("content-length", 0)))
        try:
            _Hook.received.append(json.loads(raw))
        except ValueError:
            _Hook.received.append({"_raw": raw.decode(errors="replace")})
        self.send_response(200); self.end_headers()

    def log_message(self, *a):
        pass


def wait_for(fn, timeout: float, every: float = 1.0, what: str = "condition"):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(every)
    raise AssertionError(f"timed out after {timeout:.0f}s waiting for {what} (last: {last!r})")


@pytest.fixture(scope="module")
def hook():
    srv = HTTPServer(("127.0.0.1", 0), _Hook)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Hook.received = []
    yield f"http://127.0.0.1:{srv.server_port}/jobwatch"
    srv.shutdown()


@pytest.fixture(scope="module")
def tenant():
    from cronsentinel.auth import generate_api_key, hash_secret
    from cronsentinel.db import system_session
    oid = uuid.uuid4()
    with system_session() as s:
        s.execute(text("INSERT INTO organizations (id, name, slug, plan) VALUES (:i, :n, :n, 'team')"), {"i": oid, "n": f"fs-{oid.hex[:8]}"})
        ws = s.execute(text("INSERT INTO workspaces (org_id, name) VALUES (:o, 'default') RETURNING id"), {"o": oid}).scalar()
        prefix, raw = generate_api_key()
        s.execute(text("INSERT INTO api_keys (org_id, name, prefix, key_hash, role) VALUES (:o, 'fullstack', :p, :h, 'admin')"),
                  {"o": oid, "p": prefix, "h": hash_secret(raw)})
    yield {"org": oid, "ws": str(ws), "h": {"X-API-Key": raw}}
    with system_session() as s:
        s.execute(text("SELECT purge_org(:o)"), {"o": oid})
        s.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": oid})


@pytest.fixture(scope="module")
def wired(tenant, hook):
    """Channel + rules + a job that runs every minute with zero grace — created via the API."""
    c = httpx.Client(base_url=API, headers=tenant["h"], timeout=10)
    ch = c.post("/api/v1/alerts/channels", json={"kind": "webhook", "name": "capture", "config": {"url": hook}})
    assert ch.status_code in (200, 201), ch.text
    ch_id = ch.json()["id"]
    for cond in ("failed", "missed"):
        r = c.post("/api/v1/alerts/rules", json={"name": f"fs-{cond}", "condition": cond, "severity": "high", "channel_ids": [ch_id]})
        assert r.status_code in (200, 201), r.text
    job = c.post("/api/v1/jobs", json={"workspace_id": tenant["ws"], "name": "fullstack-cron", "kind": "cron",
                                       "schedule_expr": "* * * * *", "grace_s": 0, "expected_runtime_s": 5})
    assert job.status_code == 201, job.text
    return {"client": c, "job": job.json()}


def _job(wired):
    r = wired["client"].get(f"/api/v1/jobs/{wired['job']['id']}")
    assert r.status_code == 200, r.text
    return r.json()


def _diag(wired) -> str:
    """Status plus the last executions and slots — the only useful thing to see when a leg times out."""
    from cronsentinel.db import system_session
    jid = wired["job"]["id"]
    with system_session() as s:
        ex = s.execute(text("SELECT status::text, scheduled_ts, server_received_ts, exit_code FROM executions WHERE job_id=:j ORDER BY server_received_ts DESC LIMIT 6"), {"j": jid}).all()
        sl = s.execute(text("SELECT scheduled_for, state::text FROM expected_runs WHERE job_id=:j AND scheduled_for < now() + interval '2 min' ORDER BY scheduled_for DESC LIMIT 6"), {"j": jid}).all()
        js = s.execute(text("SELECT status::text FROM jobs WHERE id=:j"), {"j": jid}).scalar()
        st = s.execute(text("SELECT job_state::text AS job_state, status::text AS status, last_status::text AS last_status, consecutive_failures, last_run_at FROM jobs WHERE id=:j"), {"j": jid}).mappings().first()
    return f"\njobs.status={js}\njob={dict(st) if st else None}\nexecutions={[tuple(map(str, r)) for r in ex]}\nslots={[tuple(map(str, r)) for r in sl]}"


def _hooks_for(cond: str):
    return [h for h in _Hook.received if cond in json.dumps(h).lower()]


def test_1_services_are_healthy():
    assert httpx.get(f"{API}/readyz", timeout=5).status_code == 200, "API not ready — is the broker up?"
    assert httpx.get(f"{INGEST}/healthz", timeout=5).status_code == 200


def test_2_schedule_generator_materialises_slots(wired):
    """R3's slot engine: a new cron job must get future expected_runs without anyone asking."""
    from cronsentinel.db import system_session
    jid = wired["job"]["id"]
    def slots():
        with system_session() as s:
            return s.execute(text("SELECT count(*) FROM expected_runs WHERE job_id=:j AND scheduled_for > now()"), {"j": jid}).scalar()
    n = wait_for(slots, 90, 2, "schedule-generator to materialise future slots")
    assert n >= 1


def test_3_success_heartbeat_makes_the_job_healthy(wired):
    tok = wired["job"]["heartbeat_token"]
    r = httpx.post(f"{INGEST}/heartbeat/{tok}/success", timeout=10)
    assert r.status_code in (200, 202), r.text
    wait_for(lambda: _job(wired)["status"] in ("healthy", "recovered") and _job(wired)["status"],
             45, 1, "exec-processor to mark the job healthy")


def test_4_failure_heartbeat_reaches_the_customer_webhook(wired):
    """ingest -> NATS CS_EXEC -> exec-processor -> job_state -> CS_STATUS -> rule-engine -> Celery -> webhook."""
    tok = wired["job"]["heartbeat_token"]
    r = httpx.post(f"{INGEST}/api/v1/heartbeat/{tok}", json={"status": "fail", "exit_code": 2, "stderr_tail": "disk full"}, timeout=10)
    assert r.status_code in (200, 202), r.text
    try:
        wait_for(lambda: _job(wired)["status"] == "failed", 45, 1, "job status to become failed")
    except AssertionError as e:
        raise AssertionError(str(e) + _diag(wired)) from None
    got = wait_for(lambda: _hooks_for("fail"), 60, 1, "the failure alert to reach the webhook receiver")
    assert got, _Hook.received


def test_5_an_incident_is_opened(wired):
    c = wired["client"]
    inc = wait_for(lambda: [i for i in c.get("/api/v1/incidents").json() if wired["job"]["id"] in [str(x) for x in i["affected_job_ids"]]],
                   45, 1, "an incident referencing the failed job")
    assert inc[0]["status"] in ("open", "acknowledged")


def test_6_a_missed_run_is_detected_and_alerted_with_no_heartbeat_at_all(wired):
    """The core promise of the product: silence is noticed. Recover the job, then send nothing and
    wait for the reconciler to settle the next slot as missed and the rule engine to alert."""
    tok = wired["job"]["heartbeat_token"]
    httpx.post(f"{INGEST}/heartbeat/{tok}/success", timeout=10)
    wait_for(lambda: _job(wired)["status"] in ("healthy", "recovered"), 45, 1, "recovery before the silence leg")
    before = len(_hooks_for("miss"))
    # A '* * * * *' slot with zero grace: worst case ~60s until the next slot plus one reconciler tick.
    wait_for(lambda: _job(wired)["status"] in ("missed", "late"), 150, 2, "reconciler to flag the silent job")
    wait_for(lambda: len(_hooks_for("miss")) > before or len(_hooks_for("late")) > 0, 60, 1, "the missed-run alert to reach the webhook")
