"""R25 — platform-level facts a tenant is entitled to see: when we were not watching."""
from fastapi import APIRouter, Depends
from sqlalchemy import text

from ..auth import Principal, current_principal
from ..db import tenant_session
from ..schemas import MonitoringGapOut, MonitoringGapsOut

router = APIRouter(prefix="/api/v1/platform", tags=["platform"])


@router.get("/monitoring-gaps", response_model=MonitoringGapsOut)
def monitoring_gaps(p: Principal = Depends(current_principal)):
    with tenant_session(p.org_id) as s:
        gaps = s.execute(text("""SELECT id, service, started_at, ended_at, slots_unobserved FROM monitoring_gaps
            WHERE ended_at > now() - interval '30 days' ORDER BY started_at DESC LIMIT 100""")).mappings().all()
        n = s.execute(text("""SELECT count(*) FROM expected_runs WHERE state='unobserved'
            AND deadline > now() - interval '30 days'""")).scalar()
    return MonitoringGapsOut(gaps=[MonitoringGapOut(**g) for g in gaps], unobserved_slots_30d=n)
