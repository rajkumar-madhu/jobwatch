"""Ingest tier. Token → job (Redis cached) → NATS publish. No auth header needed: token IS the auth."""
import json
from datetime import UTC, datetime

import redis
from starlette.concurrency import run_in_threadpool
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text

from .. import ratelimit
from ..config import settings
from ..db import system_session
from ..schemas import HeartbeatBody

router = APIRouter(tags=["heartbeat"])
_r = redis.Redis.from_url(settings.redis_url, decode_responses=True)


def _resolve(token: str) -> tuple[str, str]:
    cached = _r.get(f"hb:{token}")
    if cached:
        org, job = cached.split("|")
        return org, job
    with system_session() as s:
        row = s.execute(text("SELECT org_id, id FROM jobs WHERE heartbeat_token=:t AND paused=false"), {"t": token}).first()
    if not row:
        raise HTTPException(404, "unknown heartbeat token")
    _r.setex(f"hb:{token}", 300, f"{row.org_id}|{row.id}")
    return str(row.org_id), str(row.id)


async def _emit(request: Request, token: str, body: HeartbeatBody):
    # R22: token resolution (Redis, DB on a miss) and the rate-limit INCR are synchronous network
    # calls. Run on the event loop they serialise every request in the process behind them — tiny
    # against local Redis, a hard ceiling against a remote one. One threadpool hop for both.
    def _auth():
        o, j = _resolve(token)
        ratelimit.check(f"hb:{token}", settings.ingest_rate_per_min, request)
        return o, j
    org, job = await run_in_threadpool(_auth)
    ev = {
        "org_id": org, "job_id": job, "kind": body.status, "execution_id": body.execution_id,
        "sequence": body.sequence, "agent_ts": body.agent_ts.isoformat() if body.agent_ts else None,
        "server_ts": datetime.now(UTC).isoformat(), "duration_ms": body.duration_ms,
        "exit_code": body.exit_code, "host": body.host or request.client.host,
        "stdout_tail": body.stdout_tail, "stderr_tail": body.stderr_tail, "meta": body.meta,
    }
    js = request.app.state.js
    if js is not None:
        await js.publish(f"exec.{org}", json.dumps(ev).encode())
    else:  # TODO: remove sync fallback once NATS is mandatory in all envs
        from ..processor import process
        def _inline():
            with system_session() as s:
                process(s, ev)
        await run_in_threadpool(_inline)
    return {"ok": True, "execution_id": body.execution_id}


@router.get("/ping/{token}")
async def ping(token: str, request: Request):
    """Simple mode: curl https://host/ping/<token> == success."""
    return await _emit(request, token, HeartbeatBody(status="success"))


@router.get("/heartbeat/{token}/{status}")
@router.post("/heartbeat/{token}/{status}")
async def heartbeat_path(token: str, status: str, request: Request):
    if status not in ("start", "success", "fail"):
        raise HTTPException(400, "status must be start|success|fail")
    return await _emit(request, token, HeartbeatBody(status=status))


@router.post("/api/v1/heartbeat/{token}")
async def heartbeat_json(token: str, body: HeartbeatBody, request: Request):
    return await _emit(request, token, body)
