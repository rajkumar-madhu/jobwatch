"""
R16 — what actually leaves the building when a user asks the Copilot a question.

A throwaway HTTP server stands in for the LLM and records the exact request body. The assertions are
on those bytes, not on what the code intends to send.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from sqlalchemy import text

from cronsentinel.config import settings

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs postgres (DATABASE_URL)")

HOST, POD, NODE, IP = "prod-db-01", "backup-7f9c2", "ip-10-0-3-7", "10.0.3.7"
SECRET = "password=hunter2-very-secret"


class _LLM(BaseHTTPRequestHandler):
    captured: list[dict] = []
    reply: str = ""
    fail = False

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        _LLM.captured.append(body)
        if _LLM.fail:
            self.send_response(500); self.end_headers(); return
        out = json.dumps({"choices": [{"message": {"content": _LLM.reply}}]}).encode()
        self.send_response(200); self.send_header("content-type", "application/json"); self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


@pytest.fixture
def llm():
    srv = HTTPServer(("127.0.0.1", 0), _LLM)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _LLM.captured, _LLM.fail = [], False
    _LLM.reply = json.dumps({"summary": "db unreachable", "root_cause": "see host", "confidence": 0.8})
    yield f"127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture
def seeded(org):
    """A failing job on a named host with a secret-bearing failure reason and k8s event."""
    from cronsentinel.auth import generate_api_key, hash_secret
    from cronsentinel.db import system_session
    with system_session() as s:
        s.execute(text("UPDATE organizations SET plan='team' WHERE id=:o"), {"o": org["id"]})
        srv = s.execute(text("INSERT INTO servers (org_id, hostname) VALUES (:o,:h) RETURNING id"), {"o": org["id"], "h": HOST}).scalar()
        job = s.execute(text("""INSERT INTO jobs (org_id, workspace_id, server_id, name, kind, heartbeat_token, schedule_expr, tz, grace_s, status, command)
            VALUES (:o,:w,:s,'nightly-backup','cron',:t,'0 2 * * *','UTC',60,'failed',:c) RETURNING id"""),
            {"o": org["id"], "w": org["ws"], "s": srv, "t": uuid.uuid4().hex, "c": f"pg_dump -h {IP} {SECRET}"}).scalar()
        s.execute(text("""INSERT INTO executions (id, org_id, job_id, status, scheduled_ts, server_received_ts, host, pod, node, failure_reason, exit_code)
            VALUES (:e,:o,:j,'failed',now(),now(),:h,:p,:n,:fr,2)"""),
            {"e": f"x-{uuid.uuid4().hex}", "o": org["id"], "j": job, "h": HOST, "p": POD, "n": NODE,
             "fr": f"connect to {IP}:5432 failed; dsn postgres://backup:s3cr3tpw@{HOST}/app"})
        prefix, raw = generate_api_key()
        s.execute(text("INSERT INTO api_keys (org_id, name, prefix, key_hash, role) VALUES (:o,'k',:p,:h,'developer')"),
                  {"o": org["id"], "p": prefix, "h": hash_secret(raw)})
    return {"key": raw, "job_id": str(job)}


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from cronsentinel.main import app
    return TestClient(app, raise_server_exceptions=False)


def _ask(client, seeded, q="why did nightly-backup fail?"):
    return client.post("/api/v1/copilot/ask", headers={"X-API-Key": seeded["key"]}, json={"question": q, "job_id": seeded["job_id"]})


def test_internal_endpoint_gets_topology_but_never_secrets(client, seeded, llm, monkeypatch):
    monkeypatch.setattr(settings, "copilot_base_url", f"http://{llm}")
    monkeypatch.setattr(settings, "copilot_allow_external", False)
    r = _ask(client, seeded, q=f"why? stderr said {SECRET}")
    assert r.status_code == 200, r.text
    sent = json.dumps(_LLM.captured[0])
    # Self-hosted model: topology is fine to send — that is what makes the answer useful.
    assert HOST in sent
    # Secrets are never fine, internal or not: command, failure_reason DSN, and the question itself.
    for leak in ("hunter2", "s3cr3tpw"):
        assert leak not in sent, f"secret {leak!r} reached the LLM"


def test_external_endpoint_is_refused_and_nothing_is_sent(client, seeded, llm, monkeypatch):
    # Point at a public-looking name; the refusal must happen before any request is attempted.
    monkeypatch.setattr(settings, "copilot_base_url", "https://api.example-llm.com")
    monkeypatch.setattr(settings, "copilot_allow_external", False)
    r = _ask(client, seeded)
    assert r.status_code == 503
    assert "COPILOT_ALLOW_EXTERNAL" in r.json()["detail"]
    assert _LLM.captured == []


def test_external_allowed_sends_no_topology_and_restores_it_in_the_answer(client, seeded, llm, monkeypatch):
    # The fake server is on 127.0.0.1, so to exercise the external path we tell egress it is external.
    monkeypatch.setattr(settings, "copilot_base_url", f"http://{llm}")
    monkeypatch.setattr(settings, "copilot_allow_external", True)
    from cronsentinel.copilot import egress
    monkeypatch.setattr(egress, "is_internal", lambda url: False)
    _LLM.reply = json.dumps({"summary": "host-1 refused connections", "root_cause": "host-1 at ip-1 is down",
                             "affected_resources": ["host-1"], "confidence": 0.7})
    r = _ask(client, seeded)
    assert r.status_code == 200, r.text
    sent = json.dumps(_LLM.captured[0])
    for real in (HOST, POD, NODE, IP):
        assert real not in sent, f"topology {real!r} reached an external LLM"
    for leak in ("hunter2", "s3cr3tpw"):
        assert leak not in sent
    # The authorised user sees real names again.
    ans = r.json()["answer"]
    assert HOST in ans["summary"] and HOST in ans["affected_resources"]
    assert IP in ans["root_cause"]


def test_llm_failure_does_not_echo_the_endpoint_url(client, seeded, llm, monkeypatch):
    monkeypatch.setattr(settings, "copilot_base_url", f"http://{llm}")
    monkeypatch.setattr(settings, "copilot_allow_external", False)
    _LLM.fail = True
    r = _ask(client, seeded)
    assert r.status_code == 502
    assert llm not in r.text and "127.0.0.1" not in r.text
