"""Agent enrollment/management (admin API) + agent-facing ingest endpoints (X-Agent-Key)."""
import json
import secrets
from datetime import UTC, datetime
from uuid import UUID

import redis
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from .. import ratelimit, schedule
from ..auth import (
    Principal,
    audit,
    current_principal,
    hash_secret,
    require_role,
    verify_secret,
)
from ..config import settings
from ..db import system_session, tenant_session

admin = APIRouter(prefix="/api/v1/agents", tags=["agents"])
agent = APIRouter(prefix="/agent/v1", tags=["agent-ingest"])
_r = redis.Redis.from_url(settings.redis_url, decode_responses=True)


# ---------- admin ----------
class BootstrapIn(BaseModel):
    name: str | None = None
    ttl_hours: int = Field(24, ge=1, le=168)


@admin.post("/bootstrap-token", status_code=201)
def bootstrap_token(body: BootstrapIn, request: Request, p: Principal = Depends(require_role("devops"))):
    tok = "csb_" + secrets.token_urlsafe(24)
    _r.setex(f"bootstrap:{tok}", body.ttl_hours * 3600, json.dumps({"org_id": str(p.org_id), "name": body.name}))
    with tenant_session(p.org_id) as s:
        audit(s, p, "agent.bootstrap_token", "agent", tok[:12], ip=request.client.host)
    return {"token": tok, "expires_in_s": body.ttl_hours * 3600,
            "install": f"curl -fsSL https://<server>/install.sh | sudo bash -s -- --server https://<ingest> --token {tok}"}


@admin.get("")
def list_agents(p: Principal = Depends(current_principal)):
    with tenant_session(p.org_id) as s:
        rows = s.execute(text("SELECT id, kind, name, host_id, version, status, last_seen_at, skew_ms, revoked_at, created_at, "
                              "(SELECT count(*) FROM jobs j WHERE j.server_id IN (SELECT id FROM servers WHERE agent_id=agents.id)) AS jobs FROM agents ORDER BY name")).all()
    return [dict(r._mapping) for r in rows]


@admin.post("/{agent_id}/rotate")
def rotate(agent_id: UUID, request: Request, p: Principal = Depends(require_role("devops"))):
    raw = "csa_" + secrets.token_urlsafe(32)
    with tenant_session(p.org_id) as s:
        n = s.execute(text("UPDATE agents SET key_hash=:h, key_prefix=:pfx WHERE id=:id AND revoked_at IS NULL"), {"h": hash_secret(raw), "pfx": raw[:12], "id": str(agent_id)}).rowcount
        if not n: raise HTTPException(404)
        audit(s, p, "agent.rotate", "agent", str(agent_id), ip=request.client.host)
    _r.delete(f"agentkey:{agent_id}")
    return {"agent_key": raw, "note": "update /etc/cronsentinel/agent.json and restart agent"}


@admin.post("/{agent_id}/revoke", status_code=204)
def revoke(agent_id: UUID, request: Request, p: Principal = Depends(require_role("devops"))):
    with tenant_session(p.org_id) as s:
        if not s.execute(text("UPDATE agents SET revoked_at=now(), status='revoked' WHERE id=:id"), {"id": str(agent_id)}).rowcount: raise HTTPException(404)
        audit(s, p, "agent.revoke", "agent", str(agent_id), ip=request.client.host)
    _r.delete(f"agentkey:{agent_id}")


# ---------- agent-facing ----------
class EnrollIn(BaseModel):
    bootstrap_token: str
    name: str
    host_id: str
    kind: str = "linux"
    version: str = "unknown"


@agent.post("/enroll")
def enroll(body: EnrollIn, request: Request):
    ratelimit.check(f"enroll:{request.client.host}", 10, request)
    raw = _r.get(f"bootstrap:{body.bootstrap_token}")
    if not raw: raise HTTPException(401, "invalid or expired bootstrap token")
    meta = json.loads(raw); org = meta["org_id"]
    key = "csa_" + secrets.token_urlsafe(32)
    with system_session() as s:
        existing = s.execute(text("SELECT id FROM agents WHERE org_id=:o AND host_id=:h AND revoked_at IS NULL"), {"o": org, "h": body.host_id}).first()
        if existing:  # re-enroll same host: rotate key in place
            aid = existing.id
            s.execute(text("UPDATE agents SET key_hash=:h, key_prefix=:p, name=:n, version=:v, status='active', last_seen_at=now() WHERE id=:id"),
                      {"h": hash_secret(key), "p": key[:12], "n": meta.get("name") or body.name, "v": body.version, "id": aid})
        else:
            aid = s.execute(text("INSERT INTO agents (org_id, kind, host_id, name, version, key_hash, key_prefix, status, last_seen_at) "
                                 "VALUES (:o, :k, :h, :n, :v, :kh, :kp, 'active', now()) RETURNING id"),
                            {"o": org, "k": body.kind, "h": body.host_id, "n": meta.get("name") or body.name, "v": body.version, "kh": hash_secret(key), "kp": key[:12]}).scalar()
            s.execute(text("INSERT INTO servers (org_id, agent_id, hostname) VALUES (:o, :a, :h) ON CONFLICT (org_id, hostname) DO UPDATE SET agent_id=EXCLUDED.agent_id"),
                      {"o": org, "a": aid, "h": body.name})
        s.execute(text("INSERT INTO audit_logs (org_id, actor_type, action, target_type, target_id, ip) VALUES (:o, 'agent', 'agent.enroll', 'agent', :a, :ip)"),
                  {"o": org, "a": str(aid), "ip": request.client.host})
    _r.delete(f"bootstrap:{body.bootstrap_token}")  # one-time
    return {"agent_id": str(aid), "agent_key": key}


def agent_principal(request: Request, x_agent_key: str = Header(...), x_agent_id: str = Header(...)) -> dict:
    cached = _r.get(f"agentkey:{x_agent_id}")
    if cached:
        h, org = cached.split("|", 1)
    else:
        with system_session() as s:
            row = s.execute(text("SELECT key_hash, org_id FROM agents WHERE id=:id AND revoked_at IS NULL"), {"id": x_agent_id}).first()
        if not row: raise HTTPException(401, "unknown or revoked agent")
        h, org = row.key_hash, str(row.org_id)
        _r.setex(f"agentkey:{x_agent_id}", 300, f"{h}|{org}")
    if not verify_secret(x_agent_key, h): raise HTTPException(401, "invalid agent key")
    ratelimit.check(f"agent:{x_agent_id}", settings.ingest_rate_per_min, request)
    return {"agent_id": x_agent_id, "org_id": org}


class DiscoveredJob(BaseModel):
    fingerprint: str
    kind: str
    name: str
    schedule: str | None = ""
    command: str
    user: str
    source: str
    working_dir: str | None = None


class DiscoveryIn(BaseModel):
    jobs: list[DiscoveredJob]


@agent.post("/discovery")
def discovery(body: DiscoveryIn, a: dict = Depends(agent_principal)):
    """Upsert jobs by fingerprint (D7). New fingerprints create jobs in the default workspace; existing ones refresh metadata."""
    org, aid = a["org_id"], a["agent_id"]
    tokens = {}
    with system_session() as s:
        srv = s.execute(text("SELECT id FROM servers WHERE org_id=:o AND agent_id=:a LIMIT 1"), {"o": org, "a": aid}).first()
        ws = s.execute(text("SELECT id FROM workspaces WHERE org_id=:o ORDER BY created_at LIMIT 1"), {"o": org}).scalar()
        lim = s.execute(text("SELECT pl.max_jobs, (SELECT count(*) FROM jobs WHERE org_id=:o) AS n FROM organizations x JOIN plan_limits pl ON pl.plan=x.plan WHERE x.id=:o"), {"o": org}).first()
        n = lim.n
        seen = set()
        for j in body.jobs:
            seen.add(j.fingerprint)
            sched = j.schedule or None
            if sched and not schedule.validate(sched): sched = None  # systemd OnCalendar etc. stored raw in meta only
            row = s.execute(text("SELECT id, heartbeat_token FROM jobs WHERE org_id=:o AND fingerprint=:f"), {"o": org, "f": j.fingerprint}).first()
            if row:
                s.execute(text("UPDATE jobs SET command=:c, run_as_user=:u, working_dir=:wd, server_id=COALESCE(:srv, server_id), updated_at=now(), "
                               "schedule_expr=COALESCE(:sch, schedule_expr) WHERE id=:id"),
                          {"c": j.command, "u": j.user, "wd": j.working_dir, "srv": srv.id if srv else None, "sch": sched, "id": row.id})
                tokens[j.fingerprint] = row.heartbeat_token
                continue
            if lim.max_jobs is not None and n >= lim.max_jobs:
                continue  # plan cap: surfaced via /api/v1/agents list + TODO: discovery_skipped counter
            name = j.name
            k = 1
            while s.execute(text("SELECT 1 FROM jobs WHERE workspace_id=:w AND name=:n"), {"w": ws, "n": name}).first():
                k += 1; name = f"{j.name}-{k}"
            tok = secrets.token_urlsafe(18)
            s.execute(text(
                "INSERT INTO jobs (org_id, workspace_id, name, kind, source, fingerprint, heartbeat_token, schedule_expr, command, run_as_user, working_dir, server_id, status, next_expected_at, tags) "
                "VALUES (:o, :w, :n, :k, 'agent', :f, :t, :sch, :c, :u, :wd, :srv, :st, :nx, ARRAY['discovered'])"),
                {"o": org, "w": ws, "n": name, "k": j.kind, "f": j.fingerprint, "t": tok, "sch": sched, "c": j.command, "u": j.user, "wd": j.working_dir,
                 "srv": srv.id if srv else None, "st": "healthy" if sched else "unknown", "nx": schedule.next_run(sched) if sched else None})
            tokens[j.fingerprint] = tok; n += 1
        s.execute(text("UPDATE agents SET last_seen_at=now() WHERE id=:a"), {"a": aid})
        # TODO: jobs previously discovered by this agent but absent now → flag 'possibly removed' (not auto-deleted)
    return {"tokens": tokens, "count": len(tokens)}


class EventsIn(BaseModel):
    events: list[dict]


@agent.post("/events")
async def events(body: EventsIn, request: Request, a: dict = Depends(agent_principal)):
    """Batch of wrapper/passive events. Resolves job by token or fingerprint, publishes to NATS."""
    org, aid = a["org_id"], a["agent_id"]
    now = datetime.now(UTC)
    accepted = dropped = 0
    js = request.app.state.js
    with system_session() as s:
        for e in body.events[:500]:
            job_id = None
            if e.get("job_token"):
                job_id = s.execute(text("SELECT id FROM jobs WHERE org_id=:o AND heartbeat_token=:t"), {"o": org, "t": e["job_token"]}).scalar()
            if not job_id and e.get("fingerprint"):
                job_id = s.execute(text("SELECT id FROM jobs WHERE org_id=:o AND fingerprint=:f"), {"o": org, "f": e["fingerprint"]}).scalar()
            if not job_id:
                dropped += 1; continue  # unknown job: wait for next discovery cycle
            kind = e.get("kind")
            if kind == "progress":
                s.execute(text("INSERT INTO execution_events (org_id, execution_id, agent_id, sequence, kind, agent_ts, payload) VALUES (:o, :eid, :a, :seq, 'progress', :ts, '{}') ON CONFLICT DO NOTHING"),
                          {"o": org, "eid": e["execution_id"], "a": aid, "seq": e.get("sequence", 0), "ts": e.get("agent_ts")})
                accepted += 1; continue
            ev = {"org_id": org, "job_id": str(job_id), "agent_id": aid, "kind": kind, "execution_id": e.get("execution_id"), "sequence": e.get("sequence", 0),
                  "agent_ts": e.get("agent_ts"), "server_ts": now.isoformat(), "duration_ms": e.get("duration_ms"), "exit_code": e.get("exit_code"),
                  "host": e.get("host"), "stdout_tail": e.get("stdout_tail"), "stderr_tail": e.get("stderr_tail"),
                  "meta": {**(e.get("meta") or {}), "env_var_names": e.get("env_var_names") or []}}
            if js is not None:
                await js.publish(f"exec.{org}", json.dumps(ev).encode())
            else:
                from ..processor import process
                process(s, ev)
            accepted += 1
        s.execute(text("UPDATE agents SET last_seen_at=now() WHERE id=:a"), {"a": aid})
    return {"accepted": accepted, "dropped": dropped}


@agent.post("/metrics")
def metrics(body: dict, a: dict = Depends(agent_principal)):
    with system_session() as s:
        srv = s.execute(text("SELECT id FROM servers WHERE org_id=:o AND agent_id=:a LIMIT 1"), {"o": a["org_id"], "a": a["agent_id"]}).first()
        if not srv: raise HTTPException(409, "no server bound to agent")
        s.execute(text("INSERT INTO host_metrics (org_id, server_id, ts, cpu_pct, mem_pct, load1, disk_pct, inode_pct, io_wait_ms) "
                       "VALUES (:o, :s, :ts, :cpu, :mem, :l1, :disk, :ino, :iow) ON CONFLICT DO NOTHING"),
                  {"o": a["org_id"], "s": srv.id, "ts": body.get("ts") or datetime.now(UTC), "cpu": body.get("cpu_pct"), "mem": body.get("mem_pct"),
                   "l1": body.get("load1"), "disk": body.get("disk_pct"), "ino": body.get("inode_pct"), "iow": body.get("io_wait_pct")})
    return {"ok": True}


@agent.post("/heartbeat")
def agent_heartbeat(body: dict, a: dict = Depends(agent_principal)):
    skew = 0
    if body.get("agent_ts"):
        try:
            skew = int((datetime.now(UTC) - datetime.fromisoformat(body["agent_ts"].replace("Z", "+00:00"))).total_seconds() * 1000)
        except Exception:
            pass
    with system_session() as s:
        # R3 open item: agents report their own heartbeat interval so "offline" is per-agent (3× interval), not a fixed 10 min
        hb = body.get("heartbeat_interval_s")
        hb = int(hb) if isinstance(hb, (int, float)) and 10 <= hb <= 3600 else None
        s.execute(text("UPDATE agents SET last_seen_at=now(), version=COALESCE(:v, version), skew_ms=:sk, status='active', "
                       "heartbeat_interval_s=COALESCE(:hb, heartbeat_interval_s) WHERE id=:a"),
                  {"v": body.get("version"), "sk": skew, "hb": hb, "a": a["agent_id"]})
    return {"ok": True, "skew_ms": skew, "server_ts": datetime.now(UTC).isoformat()}
