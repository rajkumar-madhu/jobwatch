import secrets
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text

from .. import schedule
from ..auth import Principal, audit, current_principal, require_role
from ..db import tenant_session
from ..schemas import ExecutionOut, JobCreate, JobOut, JobUpdate

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])

_COLS = ("id, workspace_id, name, description, kind, schedule_expr, tz, expected_runtime_s, grace_s, tags, status, paused, "
         "last_run_at, last_status, next_expected_at, reliability_score, heartbeat_token")


def _out(r) -> JobOut:
    d = dict(r._mapping)
    d["schedule_human"] = schedule.human(d["schedule_expr"]) if d["schedule_expr"] else None
    d["status"] = str(d["status"]); d["last_status"] = str(d["last_status"]) if d["last_status"] else None
    return JobOut(**d)


@router.get("", response_model=dict)
def list_jobs(p: Principal = Depends(current_principal), status: str | None = None, workspace_id: UUID | None = None,
              tag: str | None = None, limit: int = Query(50, le=200), cursor: str | None = None):
    where, params = ["org_id=:org"], {"org": str(p.org_id), "limit": limit + 1}
    if status: where.append("status=:status"); params["status"] = status
    if workspace_id: where.append("workspace_id=:ws"); params["ws"] = str(workspace_id)
    if tag: where.append(":tag = ANY(tags)"); params["tag"] = tag
    if cursor: where.append("name > :cursor"); params["cursor"] = cursor
    with tenant_session(p.org_id) as s:
        rows = s.execute(text(f"SELECT {_COLS} FROM jobs WHERE {' AND '.join(where)} ORDER BY name LIMIT :limit"), params).all()
    items = [_out(r) for r in rows[:limit]]
    return {"items": items, "next_cursor": items[-1].name if len(rows) > limit else None}


@router.post("", response_model=JobOut, status_code=201)
def create_job(body: JobCreate, request: Request, p: Principal = Depends(require_role("developer"))):
    with tenant_session(p.org_id) as s:
        lim = s.execute(text("SELECT pl.max_jobs, (SELECT count(*) FROM jobs WHERE org_id=:org) AS n FROM organizations o "
                             "JOIN plan_limits pl ON pl.plan=o.plan WHERE o.id=:org"), {"org": str(p.org_id)}).first()
        if lim and lim.max_jobs is not None and lim.n >= lim.max_jobs:
            raise HTTPException(402, f"plan limit reached ({lim.max_jobs} jobs). Upgrade to add more.")
        nxt = schedule.next_run(body.schedule_expr, body.tz) if body.schedule_expr else None
        try:
            r = s.execute(text(
                "INSERT INTO jobs (org_id, workspace_id, environment_id, name, description, kind, heartbeat_token, schedule_expr, tz, "
                "expected_runtime_s, grace_s, team_id, tags, sla_target, status, next_expected_at, owner_id) "
                "VALUES (:org, :ws, :env, :name, :desc, :kind, :tok, :expr, :tz, :ert, :grace, :team, :tags, :sla, :st, :nxt, :owner) "
                f"RETURNING {_COLS}"),
                {"org": str(p.org_id), "ws": str(body.workspace_id), "env": str(body.environment_id) if body.environment_id else None,
                 "name": body.name, "desc": body.description, "kind": body.kind, "tok": secrets.token_urlsafe(18),
                 "expr": body.schedule_expr, "tz": body.tz, "ert": body.expected_runtime_s, "grace": body.grace_s,
                 "team": str(body.team_id) if body.team_id else None, "tags": body.tags, "sla": body.sla_target,
                 "st": "healthy" if body.schedule_expr else "unknown", "nxt": nxt, "owner": str(p.user_id) if p.user_id else None}).first()
        except Exception as e:
            if "unique" in str(e).lower():
                raise HTTPException(409, "job name already exists in workspace")
            raise
        audit(s, p, "job.create", "job", str(r.id), {"name": body.name}, request.client.host)
        return _out(r)


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: UUID, p: Principal = Depends(current_principal)):
    with tenant_session(p.org_id) as s:
        r = s.execute(text(f"SELECT {_COLS} FROM jobs WHERE id=:id"), {"id": str(job_id)}).first()
    if not r: raise HTTPException(404)
    return _out(r)


@router.patch("/{job_id}", response_model=JobOut)
def update_job(job_id: UUID, body: JobUpdate, request: Request, p: Principal = Depends(require_role("developer"))):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields: raise HTTPException(400, "nothing to update")
    sets = ", ".join(f"{k}=:{k}" for k in fields)
    with tenant_session(p.org_id) as s:
        if "schedule_expr" in fields or "tz" in fields:
            cur = s.execute(text("SELECT schedule_expr, tz FROM jobs WHERE id=:id"), {"id": str(job_id)}).first()
            if not cur: raise HTTPException(404)
            expr, tz = fields.get("schedule_expr", cur.schedule_expr), fields.get("tz", cur.tz)
            fields["next_expected_at"] = schedule.next_run(expr, tz) if expr else None
            sets += ", next_expected_at=:next_expected_at, late_marked_at=NULL"
        if "paused" in fields:
            sets += ", status=CASE WHEN :paused THEN 'paused'::job_status ELSE 'unknown'::job_status END"
        r = s.execute(text(f"UPDATE jobs SET {sets}, updated_at=now() WHERE id=:id RETURNING {_COLS}"), {**fields, "id": str(job_id)}).first()
        if not r: raise HTTPException(404)
        audit(s, p, "job.update", "job", str(job_id), fields, request.client.host)
        return _out(r)


@router.delete("/{job_id}", status_code=204)
def delete_job(job_id: UUID, request: Request, p: Principal = Depends(require_role("devops"))):
    with tenant_session(p.org_id) as s:
        # purge_job first: executions/expected_runs/logs carry org_id but no FK to jobs (they are
        # partitioned), so a plain DELETE left orphans the reconciler would keep settling. Same
        # transaction, so a failed purge does not half-delete the job.
        if not s.execute(text("SELECT 1 FROM jobs WHERE id=:id"), {"id": str(job_id)}).first():
            raise HTTPException(404)
        s.execute(text("SELECT purge_job(CAST(:id AS uuid))"), {"id": str(job_id)})
        n = s.execute(text("DELETE FROM jobs WHERE id=:id"), {"id": str(job_id)}).rowcount
        if not n: raise HTTPException(404)
        audit(s, p, "job.delete", "job", str(job_id), ip=request.client.host)


@router.get("/{job_id}/next-runs")
def next_runs(job_id: UUID, n: int = Query(5, le=50), p: Principal = Depends(current_principal)):
    with tenant_session(p.org_id) as s:
        r = s.execute(text("SELECT schedule_expr, tz FROM jobs WHERE id=:id"), {"id": str(job_id)}).first()
    if not r: raise HTTPException(404)
    if not r.schedule_expr: return {"human": None, "runs": []}
    return {"human": schedule.human(r.schedule_expr), "runs": schedule.next_runs(r.schedule_expr, r.tz, n)}


@router.get("/{job_id}/executions", response_model=list[ExecutionOut])
def job_executions(job_id: UUID, p: Principal = Depends(current_principal), limit: int = Query(50, le=200)):
    with tenant_session(p.org_id) as s:
        rows = s.execute(text(
            "SELECT id, job_id, status, scheduled_ts, agent_ts_start, agent_ts_end, duration_ms, exit_code, host, skew_ms "
            "FROM executions WHERE job_id=:id ORDER BY scheduled_ts DESC LIMIT :lim"), {"id": str(job_id), "lim": limit}).all()
    return [ExecutionOut(**{**dict(r._mapping), "status": str(r.status)}) for r in rows]


@router.post("/schedule/preview")
def preview_schedule(expr: str, tz: str = "UTC", p: Principal = Depends(current_principal)):
    if not schedule.validate(expr): raise HTTPException(400, "invalid cron expression")
    return {"expr": expr, "human": schedule.human(expr), "next": schedule.next_runs(expr, tz, 5)}
