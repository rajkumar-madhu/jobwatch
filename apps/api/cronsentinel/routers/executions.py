from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from ..auth import Principal, current_principal
from ..db import tenant_session

router = APIRouter(prefix="/api/v1/executions", tags=["executions"])


@router.get("/{execution_id}")
def get_execution(execution_id: str, p: Principal = Depends(current_principal)):
    with tenant_session(p.org_id) as s:
        e = s.execute(text("SELECT * FROM executions WHERE id=:id"), {"id": execution_id}).first()
        if not e: raise HTTPException(404)
        events = s.execute(text("SELECT sequence, kind, agent_ts, server_ts, payload FROM execution_events WHERE execution_id=:id ORDER BY sequence"),
                           {"id": execution_id}).all()
        logs = s.execute(text("SELECT stream, chunk_idx, content FROM execution_logs WHERE execution_id=:id ORDER BY stream, chunk_idx"),
                         {"id": execution_id}).all()
    d = dict(e._mapping); d["status"] = str(d["status"])
    return {"execution": d, "timeline": [dict(r._mapping) for r in events],
            "logs": {st: "".join(r.content for r in logs if r.stream == st) for st in ("stdout", "stderr")}}


@router.get("/{execution_id}/compare/{other_id}")
def compare(execution_id: str, other_id: str, p: Principal = Depends(current_principal)):
    with tenant_session(p.org_id) as s:
        rows = s.execute(text("SELECT id, status, duration_ms, exit_code, host, scheduled_ts, agent_ts_start, agent_ts_end FROM executions WHERE id IN (:a, :b)"),
                         {"a": execution_id, "b": other_id}).all()
    if len(rows) != 2: raise HTTPException(404)
    a, b = ({**dict(r._mapping), "status": str(r.status)} for r in rows)
    delta = None
    if a["duration_ms"] and b["duration_ms"]:
        delta = {"duration_ms": b["duration_ms"] - a["duration_ms"], "pct": round((b["duration_ms"] - a["duration_ms"]) / a["duration_ms"] * 100, 1)}
    return {"a": a, "b": b, "delta": delta}
