"""R6 — Celery against a real Redis broker.

`.apply()` in the other tests runs the task body in-process and proves nothing about serialisation,
routing or the worker actually picking the task up. Here the task is enqueued for real and a worker
subprocess drains the queue.
"""
import json
import os
import subprocess
import sys
import time
import uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.skipif(not os.getenv("REDIS_URL"), reason="needs redis (REDIS_URL)")


def _dest(org, url):
    from cronsentinel.crypto import encrypt_json
    from cronsentinel.db import system_session
    with system_session() as s:
        return s.execute(text("""INSERT INTO signal_destinations (org_id, name, kind, config_enc, event_types)
            VALUES (:o, 'aegis', 'webhook', :c, '{}') RETURNING id"""),
            {"o": org["id"], "c": encrypt_json({"url": url, "secret": "s3cr3t-s3cr3t-s3cr3t"})}).scalar()


def test_task_is_registered_on_the_default_queue():
    """The notify worker's -Q must cover the queue deliver_signal lands on, or signals sit forever."""
    from cronsentinel.celery_app import celery
    celery.loader.import_default_modules()   # `include=` is lazy until the worker (or this) resolves it
    assert "cronsentinel.outbound.deliver.deliver_signal" in celery.tasks
    assert celery.conf.task_default_queue == "notify"
    routes = celery.conf.task_routes or {}
    assert not routes.get("cronsentinel.outbound.deliver.deliver_signal"), "task is routed off the notify queue the worker listens on"


def test_enqueued_signal_is_picked_up_by_a_real_worker(org, tmp_path):
    """Full path: enqueue over Redis → worker subprocess → HTTP POST → delivery ledger row."""
    from cronsentinel.db import system_session
    from cronsentinel.outbound import schema
    from cronsentinel.outbound.deliver import deliver_signal

    # a throwaway receiver that records what it got
    hits = tmp_path / "hits.jsonl"
    srv = tmp_path / "srv.py"
    srv.write_text(f'''
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        open({str(hits)!r}, "a").write(json.dumps({{"headers": dict(self.headers), "body": body.decode()}}) + "\\n")
        self.send_response(202); self.end_headers()
    def log_message(self, *a): pass
HTTPServer(("127.0.0.1", 18099), H).serve_forever()
''')
    recv = subprocess.Popen([sys.executable, str(srv)])
    worker = None
    try:
        time.sleep(1)
        d = _dest(org, "http://127.0.0.1:18099/ingest")
        env = schema.from_jobstatus({"org_id": str(org["id"]), "job_id": str(uuid.uuid4()), "new_state": "failing",
                                     "prev_state": "ok", "new_status": "failed", "prev_status": "healthy"}, {"name": "celery-e2e"}).envelope()
        deliver_signal.delay(str(org["id"]), str(d), env)   # real enqueue over Redis

        # R18: the receiver is on 127.0.0.1, which the SSRF guard blocks in SaaS mode — run the
        # worker in self-hosted mode. test_ssrf_guard.py covers the blocking side.
        worker = subprocess.Popen([sys.executable, "-m", "celery", "-A", "cronsentinel.celery_app.celery",
                                   "worker", "-Q", "notify", "-l", "warning", "--concurrency", "1", "-P", "solo"],
                                  env={**os.environ, "OUTBOUND_ALLOW_PRIVATE": "true"}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 45
        row = None
        while time.time() < deadline:
            with system_session() as s:
                row = s.execute(text("SELECT status, response_code FROM signal_deliveries WHERE destination_id=:d AND signal_id=:sid"),
                                {"d": d, "sid": env["signal_id"]}).first()
            if row and row.status == "sent":
                break
            time.sleep(1)
        assert row is not None and row.status == "sent" and row.response_code == 202
        got = [json.loads(x) for x in hits.read_text().splitlines()]
        assert len(got) == 1
        assert got[0]["headers"]["X-JobWatch-Signal-Id"] == env["signal_id"]
        assert json.loads(got[0]["body"])["schema"] == "jobwatch.signal"
    finally:
        for p in (worker, recv):
            if p:
                p.terminate()
