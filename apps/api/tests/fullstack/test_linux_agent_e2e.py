"""R35 — the Linux agent, compiled and run against the real stack.

Until this round the Go agent under agents/linux-agent had never been compiled: every claim about
it — exit codes preserved, env var values never collected, `cs-run` alias, buffered delivery — was
inspection of source that no toolchain had ever checked. This test builds the binary (or uses
AGENT_BIN) and drives the real enrol → wrap → flush → process chain end to end:

  1. an admin API key mints a one-time bootstrap token via the API;
  2. `cronsentinel-agent enroll` exchanges it at the ingest service for a long-lived agent key
     (CRONSENTINEL_CONFIG points the config at a temp dir, so no /etc access is needed);
  3. `cs-run` (the symlink alias packaging/install.sh creates) wraps a command that prints to both
     streams and exits 3, with a secret-looking value in its environment;
  4. `cronsentinel-agent run` is started long enough to flush the buffer to POST /agent/v1/events;
  5. the execution row the exec-processor writes is checked: status failed, exit_code 3, host set,
     env_var_names contains only allowlisted NAMES, and the secret VALUE appears nowhere — not in
     the execution row, not in execution_events, not in the on-disk buffer.

Skipped unless JOBWATCH_FULLSTACK=1 (needs scripts/fullstack.sh up) and either AGENT_BIN is set
or a `go` toolchain is on PATH.
"""
import json
import os
import shutil
import signal
import subprocess
import time
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.skipif(os.getenv("JOBWATCH_FULLSTACK") != "1", reason="needs the real stack (scripts/fullstack.sh up)")

API = f"http://127.0.0.1:{os.getenv('API_PORT', '18000')}"
INGEST = f"http://127.0.0.1:{os.getenv('INGEST_PORT', '18010')}"
AGENT_SRC = Path(__file__).resolve().parents[4] / "agents" / "linux-agent"
SECRET_VALUE = "hunter2-" + uuid.uuid4().hex  # unique so a grep for it is unambiguous


@pytest.fixture(scope="module")
def agent_bin(tmp_path_factory) -> Path:
    if os.getenv("AGENT_BIN"):
        return Path(os.environ["AGENT_BIN"])
    if not shutil.which("go"):
        pytest.skip("no AGENT_BIN and no go toolchain on PATH")
    out = tmp_path_factory.mktemp("bin") / "cronsentinel-agent"
    subprocess.run(["go", "build", "-o", str(out), "./cmd/cronsentinel-agent"], cwd=AGENT_SRC, check=True,
                   env={**os.environ, "CGO_ENABLED": "0", "GOTOOLCHAIN": "local"})
    return out


@pytest.fixture(scope="module")
def tenant():
    from cronsentinel.auth import generate_api_key, hash_secret
    from cronsentinel.db import system_session
    oid = uuid.uuid4()
    with system_session() as s:
        s.execute(text("INSERT INTO organizations (id, name, slug, plan) VALUES (:i, :n, :n, 'team')"), {"i": oid, "n": f"r35-{oid.hex[:8]}"})
        ws = s.execute(text("INSERT INTO workspaces (org_id, name) VALUES (:o, 'default') RETURNING id"), {"o": oid}).scalar()
        prefix, raw = generate_api_key()
        s.execute(text("INSERT INTO api_keys (org_id, name, prefix, key_hash, role) VALUES (:o, 'r35', :p, :h, 'admin')"),
                  {"o": oid, "p": prefix, "h": hash_secret(raw)})
    yield {"org": oid, "ws": str(ws), "h": {"X-API-Key": raw}}
    with system_session() as s:
        s.execute(text("SELECT purge_org(:o)"), {"o": oid})
        s.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": oid})


def _wait(fn, timeout: float, what: str, every: float = 1.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(every)
    raise AssertionError(f"timed out after {timeout:.0f}s waiting for {what} (last: {last!r})")


def test_agent_enrolls_wraps_and_delivers(agent_bin, tenant, tmp_path):
    from cronsentinel.db import system_session

    c = httpx.Client(base_url=API, headers=tenant["h"], timeout=10)
    tok = c.post("/api/v1/agents/bootstrap-token", json={"name": "r35-host"})
    assert tok.status_code == 201, tok.text
    job = c.post("/api/v1/jobs", json={"workspace_id": tenant["ws"], "name": "r35-wrapped", "kind": "cron",
                                       "schedule_expr": "* * * * *", "grace_s": 0, "expected_runtime_s": 5})
    assert job.status_code == 201, job.text
    job_token = job.json()["heartbeat_token"]

    cfg_path = tmp_path / "etc" / "agent.json"
    env = {**os.environ, "CRONSENTINEL_CONFIG": str(cfg_path)}

    # 1-2. enroll: one-time bootstrap token → long-lived agent key, config written where we said
    r = subprocess.run([agent_bin, "enroll", "--server", INGEST, "--token", tok.json()["token"], "--name", "r35-host"],
                       env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    cfg = json.loads(cfg_path.read_text())
    assert cfg["agent_key"].startswith("csa_") and cfg["server_url"] == INGEST
    cfg["buffer_path"] = str(tmp_path / "buffer.jsonl")
    cfg["flush_every"] = 1_000_000_000  # 1s, in Go time.Duration nanoseconds
    cfg["metrics_every"] = 2_000_000_000
    cfg_path.write_text(json.dumps(cfg))
    # the token is one-time
    again = subprocess.run([agent_bin, "enroll", "--server", INGEST, "--token", tok.json()["token"]], env=env, capture_output=True, text=True)
    assert again.returncode != 0 and "401" in again.stderr

    # 3. wrap via the cs-run alias (packaging/install.sh: ln -sf cronsentinel-agent cs-run)
    cs_run = tmp_path / "cs-run"
    cs_run.symlink_to(agent_bin)
    r = subprocess.run([cs_run, "--job", job_token, "--", "sh", "-c", "echo out-line; echo err-line >&2; exit 3"],
                       env={**env, "DB_PASSWORD": SECRET_VALUE, "PATH": os.environ["PATH"]}, capture_output=True, text=True)
    assert r.returncode == 3, "wrapper must exit with the child's exit code so cron mail/behaviour is preserved"
    assert "out-line" in r.stdout and "err-line" in r.stderr, "streams pass through the wrapper"
    buffered = (tmp_path / "buffer.jsonl").read_text()
    kinds = [json.loads(l)["kind"] for l in buffered.splitlines() if l.strip()]
    assert kinds == ["start", "fail"], kinds
    assert SECRET_VALUE not in buffered and "DB_PASSWORD" not in buffered

    # 4. daemon flushes the buffer to the ingest service
    daemon = subprocess.Popen([agent_bin, "run"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        def _row():
            with system_session() as s:
                return s.execute(text("SELECT status::text, exit_code, host, env_var_names, meta::text, duration_ms FROM executions "
                                      "WHERE org_id=:o AND job_id=:j AND status IN ('failed','success')"),
                                 {"o": tenant["org"], "j": job.json()["id"]}).first()
        row = _wait(_row, 60, "execution row from the exec-processor", every=2)
    finally:
        daemon.send_signal(signal.SIGTERM)
        try:
            daemon.wait(10)
        except subprocess.TimeoutExpired:
            daemon.kill()

    # 5. what the platform recorded
    status, exit_code, host, env_names, meta, duration = row
    assert (status, exit_code) == ("failed", 3)
    assert host and duration is not None and duration >= 0
    assert set(env_names) <= {"PATH", "HOME", "SHELL", "USER", "LANG", "TZ"}, env_names
    assert "DB_PASSWORD" not in env_names and SECRET_VALUE not in meta
    with system_session() as s:
        leaked = s.execute(text("SELECT count(*) FROM execution_events WHERE org_id=:o AND payload::text LIKE :v"),
                           {"o": tenant["org"], "v": f"%{SECRET_VALUE}%"}).scalar()
        agent_row = s.execute(text("SELECT status::text, last_seen_at, version FROM agents WHERE org_id=:o"), {"o": tenant["org"]}).first()
    assert leaked == 0
    assert agent_row is not None and agent_row.last_seen_at is not None and agent_row.version == "0.1.0"
