import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from .. import ratelimit
from ..auth import Principal, current_principal, require_role
from ..config import settings
from ..copilot import context, llm
from ..db import tenant_session

router = APIRouter(prefix="/api/v1/copilot", tags=["copilot"])

SUGGESTED = ["Why did the most recent failure happen?", "Which jobs failed after the last deployment?", "Which jobs have increasing duration?",
             "Why did this job miss its schedule?", "Which servers have the most cron failures?", "What changed before this incident?"]


class AskIn(BaseModel):
    question: str
    job_id: str | None = None
    incident_id: str | None = None


@router.post("/ask")
def ask(body: AskIn, p: Principal = Depends(require_role("developer"))):
    with tenant_session(p.org_id) as s:
        feats = s.execute(text("SELECT pl.features FROM organizations o JOIN plan_limits pl ON pl.plan=o.plan WHERE o.id=:o"), {"o": str(p.org_id)}).scalar() or []
        if "ai" not in feats and "all" not in feats: raise HTTPException(402, "AI diagnostics require the Team plan or higher")
        ratelimit.check(f"copilot:{p.org_id}", settings.copilot_rate_per_min)
        ctx = context.build(s, str(p.org_id), body.job_id, body.incident_id, body.question)
        if not settings.copilot_base_url:
            raise HTTPException(503, "Copilot LLM endpoint not configured (COPILOT_BASE_URL)")
        try:
            answer, model, ms = llm.ask(body.question, ctx)
        except Exception as e:
            raise HTTPException(502, f"LLM call failed: {str(e)[:200]}")
        sid = s.execute(text("INSERT INTO copilot_sessions (org_id, user_id, incident_id, job_id, question, answer, context_summary, model, latency_ms) VALUES (:o, :u, :i, :j, :q, CAST(:a AS jsonb), CAST(:c AS jsonb), :m, :ms) RETURNING id"),
                        {"o": str(p.org_id), "u": str(p.user_id) if p.user_id else None, "i": body.incident_id, "j": body.job_id, "q": body.question, "a": json.dumps(answer), "c": json.dumps(context.summarize(ctx)), "m": model, "ms": ms}).scalar()
        if body.incident_id and answer.get("root_cause"):
            s.execute(text("UPDATE incidents SET root_cause=COALESCE(root_cause, :rc) WHERE id=:i"), {"rc": f"[copilot, {answer['confidence']:.0%}] {answer['root_cause']}", "i": body.incident_id})
    return {"id": sid, "answer": answer, "context": context.summarize(ctx), "model": model, "latency_ms": ms}


@router.get("/history")
def history(p: Principal = Depends(current_principal), limit: int = 20):
    with tenant_session(p.org_id) as s:
        return [dict(r._mapping) for r in s.execute(text("SELECT id, question, answer, job_id, incident_id, model, latency_ms, created_at FROM copilot_sessions ORDER BY created_at DESC LIMIT :l"), {"l": min(limit, 100)}).all()]


@router.get("/suggestions")
def suggestions(p: Principal = Depends(current_principal)):
    return {"questions": SUGGESTED}
