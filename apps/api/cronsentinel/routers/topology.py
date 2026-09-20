"""Dependencies (DAG) + topology tree (spec §14, §15)."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text

from ..alerting.correlation import downstream_impact
from ..auth import Principal, audit, current_principal, require_role
from ..db import tenant_session
from ..schemas import DependencyGraph

router = APIRouter(prefix="/api/v1", tags=["topology"])


class DepIn(BaseModel):
    job_id: UUID
    depends_on_job_id: UUID


@router.get("/dependencies", response_model=DependencyGraph)
def dependencies(p: Principal = Depends(current_principal)):
    with tenant_session(p.org_id) as s:
        edges = [dict(r._mapping) for r in s.execute(text("SELECT job_id, depends_on_job_id FROM job_dependencies")).all()]
        ids = {str(e["job_id"]) for e in edges} | {str(e["depends_on_job_id"]) for e in edges}
        nodes = [dict(r._mapping) | {"status": str(r.status)} for r in s.execute(text("SELECT id, name, status, last_run_at FROM jobs WHERE id = ANY(CAST(:ids AS uuid[]))"), {"ids": list(ids)}).all()] if ids else []
    return {"nodes": nodes, "edges": edges}


@router.post("/dependencies", status_code=201)
def add_dependency(body: DepIn, request: Request, p: Principal = Depends(require_role("developer"))):
    if body.job_id == body.depends_on_job_id: raise HTTPException(400, "a job cannot depend on itself")
    with tenant_session(p.org_id) as s:
        # Both jobs must exist *in this tenant*. Without this the FK violation surfaced as a 500,
        # and the error told a caller nothing about which id was wrong. RLS means a job in another
        # org is simply not visible here, so this doubles as the tenancy check.
        found = {str(r.id) for r in s.execute(text("SELECT id FROM jobs WHERE id = ANY(CAST(:ids AS uuid[]))"),
                                              {"ids": [str(body.job_id), str(body.depends_on_job_id)]}).all()}
        missing = [i for i in (str(body.job_id), str(body.depends_on_job_id)) if i not in found]
        if missing:
            raise HTTPException(404, f"unknown job id(s): {', '.join(missing)}")
        # cycle check: would depends_on already (transitively) depend on job?
        cyc = s.execute(text("""WITH RECURSIVE up AS (SELECT depends_on_job_id AS j FROM job_dependencies WHERE job_id=:d
            UNION SELECT jd.depends_on_job_id FROM job_dependencies jd JOIN up ON jd.job_id=up.j) SELECT 1 FROM up WHERE j=:j LIMIT 1"""), {"d": str(body.depends_on_job_id), "j": str(body.job_id)}).first()
        if cyc: raise HTTPException(409, "would create a cycle")
        s.execute(text("INSERT INTO job_dependencies (org_id, job_id, depends_on_job_id) VALUES (:o, :j, :d) ON CONFLICT DO NOTHING"), {"o": str(p.org_id), "j": str(body.job_id), "d": str(body.depends_on_job_id)})
        audit(s, p, "dependency.add", "job", str(body.job_id), {"depends_on": str(body.depends_on_job_id)}, request.client.host if request.client else None)
    return {"ok": True}


@router.delete("/dependencies", status_code=204)
def remove_dependency(job_id: UUID, depends_on_job_id: UUID, p: Principal = Depends(require_role("developer"))):
    with tenant_session(p.org_id) as s:
        s.execute(text("DELETE FROM job_dependencies WHERE job_id=:j AND depends_on_job_id=:d"), {"j": str(job_id), "d": str(depends_on_job_id)})


@router.get("/jobs/{job_id}/impact")
def impact(job_id: UUID, p: Principal = Depends(current_principal)):
    with tenant_session(p.org_id) as s:
        up = [dict(r._mapping) | {"status": str(r.status)} for r in s.execute(text("SELECT j.id, j.name, j.status FROM job_dependencies d JOIN jobs j ON j.id=d.depends_on_job_id WHERE d.job_id=:id"), {"id": str(job_id)}).all()]
        down = downstream_impact(s, str(p.org_id), str(job_id))
    return {"upstream": up, "downstream": down}


@router.get("/topology")
def topology(p: Principal = Depends(current_principal)):
    """Org → (Server → User → Job) and Org → (Cluster → Namespace → CronJob). Statuses roll up worst-first."""
    RANK = {"failed": 4, "timeout": 4, "missed": 3, "late": 2, "running": 1, "recovered": 1, "healthy": 0, "unknown": 0, "paused": 0}
    def worst(xs): return max(xs, key=lambda x: RANK.get(x, 0)) if xs else "healthy"
    with tenant_session(p.org_id) as s:
        servers = s.execute(text("SELECT s.id, s.hostname, j.id AS job_id, j.name, j.status::text, j.run_as_user FROM servers s LEFT JOIN jobs j ON j.server_id=s.id ORDER BY s.hostname, j.run_as_user, j.name")).all()
        clusters = s.execute(text("SELECT c.id, c.name AS cluster, k.namespace, j.id AS job_id, k.name, j.status::text FROM clusters c LEFT JOIN k8s_cronjobs k ON k.cluster_id=c.id LEFT JOIN jobs j ON j.id=k.job_id ORDER BY c.name, k.namespace, k.name")).all()
        orphans = [dict(r._mapping) | {"status": str(r.status)} for r in s.execute(text("SELECT id, name, status FROM jobs WHERE server_id IS NULL AND cluster_id IS NULL ORDER BY name")).all()]
    srv = {}
    for r in servers:
        d = srv.setdefault(str(r.id), {"id": str(r.id), "name": r.hostname, "users": {}})
        if r.job_id: d["users"].setdefault(r.run_as_user or "unknown", []).append({"id": str(r.job_id), "name": r.name, "status": r.status})
    cl = {}
    for r in clusters:
        d = cl.setdefault(str(r.id), {"id": str(r.id), "name": r.cluster, "namespaces": {}})
        if r.job_id: d["namespaces"].setdefault(r.namespace, []).append({"id": str(r.job_id), "name": r.name, "status": r.status})
    def roll_srv(d):
        users = [{"name": u, "status": worst([j["status"] for j in js]), "jobs": js} for u, js in d["users"].items()]
        return {"id": d["id"], "name": d["name"], "status": worst([u["status"] for u in users]), "users": users}
    def roll_cl(d):
        nss = [{"name": n, "status": worst([j["status"] for j in js]), "cronjobs": js} for n, js in d["namespaces"].items()]
        return {"id": d["id"], "name": d["name"], "status": worst([n["status"] for n in nss]), "namespaces": nss}
    out = {"servers": [roll_srv(d) for d in srv.values()], "clusters": [roll_cl(d) for d in cl.values()], "heartbeat_only": orphans}
    out["status"] = worst([x["status"] for x in out["servers"] + out["clusters"]] + [j["status"] for j in orphans])
    return out
