"""R29 — self-service organization export and deletion.

Two irreversible-adjacent actions an owner should be able to do without opening a support ticket:
pull everything the platform holds about the org (export), and delete the org outright (deletion).

Export is a one-shot, user-triggered read, not a hot path — returned as a single JSON body rather
than paginated. Config secrets (`notification_channels.config_enc`, `integrations.config_enc`) are
envelope-encrypted and were never decryptable by an ordinary read path before this; the export does
not change that — it returns metadata (kind, name, created_at) and explicitly never touches
config_enc. Raw execution history/logs are excluded by design: they are operational telemetry
already reachable via the existing paginated API, not org configuration or personal data, and a
full-history export could be unboundedly large for an old, busy org. `memberships`/`users` (email,
name, role) ARE included — that is the actual personal data GDPR export is about.

Deletion requires the caller to type the org's own slug back (checked server-side, never trusted
from a hidden field) and is refused while a paid subscription is active, to avoid silently deleting
billing records while Stripe may still be charging — the org must cancel billing first via the
existing /billing/portal flow. `purge_org()` handles the tables with no FK to organizations
(partitioned execution/log tables, ledgers); every other org_id-bearing table cascades on
`DELETE FROM organizations` (verified: every FK to organizations is ON DELETE CASCADE). No audit_logs
row is written for the deletion itself — audit_logs is one of the tables purge_org() empties, so a
row in it would not survive the action it describes; the durable record is a structured log line.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text
import structlog

from ..auth import Principal, current_principal, require_role
from ..db import tenant_session

log = structlog.get_logger()
router = APIRouter(prefix="/api/v1/org", tags=["org"])


@router.get("/export")
def export_org(p: Principal = Depends(current_principal)):
    with tenant_session(p.org_id) as s:
        org = s.execute(text("SELECT id, name, slug, plan, created_at FROM organizations WHERE id=:o"), {"o": p.org_id}).mappings().first()
        if not org:
            raise HTTPException(404)
        sections = {
            "organization": dict(org),
            "members": [dict(r) for r in s.execute(text(
                "SELECT u.email, u.name, m.role, m.created_at AS joined_at FROM memberships m "
                "JOIN users u ON u.id=m.user_id WHERE m.org_id=:o ORDER BY m.created_at"), {"o": p.org_id}).mappings()],
            "workspaces": [dict(r) for r in s.execute(text(
                "SELECT id, name, created_at FROM workspaces WHERE org_id=:o ORDER BY created_at"), {"o": p.org_id}).mappings()],
            "jobs": [dict(r) for r in s.execute(text(
                "SELECT id, name, kind, schedule_expr, tz, grace_s, expected_runtime_s, paused, tags, created_at "
                "FROM jobs WHERE org_id=:o ORDER BY created_at"), {"o": p.org_id}).mappings()],
            "notification_channels": [dict(r) for r in s.execute(text(   # never config_enc — see module docstring
                "SELECT id, kind, name, created_at FROM notification_channels WHERE org_id=:o ORDER BY created_at"), {"o": p.org_id}).mappings()],
            "alert_rules": [dict(r) for r in s.execute(text(
                "SELECT id, name, condition, severity, enabled FROM alert_rules WHERE org_id=:o ORDER BY name"), {"o": p.org_id}).mappings()],
            "integrations": [dict(r) for r in s.execute(text(            # never config_enc
                "SELECT id, kind, name, created_at FROM integrations WHERE org_id=:o ORDER BY created_at"), {"o": p.org_id}).mappings()],
            "api_keys": [dict(r) for r in s.execute(text(                # never key_hash
                "SELECT id, name, prefix, role, created_at, revoked_at FROM api_keys WHERE org_id=:o ORDER BY created_at"), {"o": p.org_id}).mappings()],
            "agents": [dict(r) for r in s.execute(text(                  # never key_hash
                "SELECT id, kind, host_id, name, version, status, created_at FROM agents WHERE org_id=:o ORDER BY created_at"), {"o": p.org_id}).mappings()],
            "incidents": [dict(r) for r in s.execute(text(
                "SELECT id, severity, status, title, started_at, acknowledged_at, resolved_at, affected_job_ids "
                "FROM incidents WHERE org_id=:o ORDER BY started_at DESC LIMIT 500"), {"o": p.org_id}).mappings()],
            "status_pages": [dict(r) for r in s.execute(text(
                "SELECT id, slug, title, visibility, job_ids FROM status_pages WHERE org_id=:o ORDER BY slug"), {"o": p.org_id}).mappings()],
            "subscription": (lambda r: dict(r) if r else None)(s.execute(text(
                "SELECT plan, status, trial_ends_at, current_period_end FROM subscriptions WHERE org_id=:o"), {"o": p.org_id}).mappings().first()),
            "usage_records": [dict(r) for r in s.execute(text(
                "SELECT period, jobs_count, executions_count, storage_bytes FROM usage_records WHERE org_id=:o "
                "ORDER BY period DESC LIMIT 24"), {"o": p.org_id}).mappings()],
        }
    log.info("org data export", org_id=str(p.org_id), actor=str(p.user_id or p.api_key_id))
    return sections


class DeleteOrgIn(BaseModel):
    confirm_slug: str


@router.delete("/", status_code=204)
def delete_org(body: DeleteOrgIn, request: Request, p: Principal = Depends(require_role("owner"))):
    with tenant_session(p.org_id) as s:
        org = s.execute(text("SELECT slug, name FROM organizations WHERE id=:o"), {"o": p.org_id}).mappings().first()
        if not org:
            raise HTTPException(404)
        if body.confirm_slug != org["slug"]:
            raise HTTPException(400, "confirm_slug does not match this organization's slug")
        sub = s.execute(text("SELECT plan, status FROM subscriptions WHERE org_id=:o"), {"o": p.org_id}).mappings().first()
        if sub and sub["plan"] != "free" and sub["status"] == "active":
            raise HTTPException(409, "Cancel the active subscription before deleting the organization (see /billing).")
        s.execute(text("SELECT purge_org(CAST(:o AS uuid))"), {"o": str(p.org_id)})
        s.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": p.org_id})
    # Not audit_logs: purge_org() empties it, so a row describing this action would not survive the
    # action itself. This structured log line is the durable record instead.
    log.warning("organization deleted (self-service)", org_id=str(p.org_id), slug=org["slug"], name=org["name"],
                actor=str(p.user_id or p.api_key_id), ip=request.client.host)
