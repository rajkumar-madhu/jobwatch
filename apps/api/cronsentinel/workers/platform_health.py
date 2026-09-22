"""R25 — platform heartbeats and monitoring-gap detection (see migration 0014).

Every worker pass calls heartbeat(service). If the previous heartbeat is older than the service's
gap threshold, the interval [previous, now) is recorded as a monitoring gap. The reconciler then
refuses to call slots inside any gap 'missed'.
"""
from __future__ import annotations

from datetime import timedelta

import structlog
from sqlalchemy import text

log = structlog.get_logger()

# A pass can legitimately overrun one interval; three is a break, not a slow tick.
GAP_MULTIPLIER = 3
INGEST_HEARTBEAT_S = 20

# R26: which services' silence makes a slot unobservable. Ingest receives the agent's reports and
# the reconciler judges them; if either was down, an empty slot is *unseen*, not missed. The
# generator is deliberately absent: its outage only means slots did not exist yet — retro_match
# recovers the runs that did happen, and a slot that is still empty after that is an honest miss.
OBSERVING_SERVICES = ("ingest", "reconciler")


def heartbeat(s, service: str, interval_s: int) -> bool:
    """Record this pass; return True if a gap was closed by it."""
    prev = s.execute(text("""
        INSERT INTO platform_heartbeats (service, last_seen) VALUES (:svc, now())
        ON CONFLICT (service) DO UPDATE SET last_seen = now()
        RETURNING (SELECT last_seen FROM platform_heartbeats WHERE service = :svc) AS prev"""),
        {"svc": service}).scalar()
    # The RETURNING subquery sees the pre-update row inside the same statement (snapshot); on
    # first-ever insert it is NULL, which is not a gap — it is the first observation.
    if prev is None:
        return False
    gap = s.execute(text("SELECT now() - :prev > :thr"), {"prev": prev, "thr": timedelta(seconds=interval_s * GAP_MULTIPLIER)}).scalar()
    if not gap:
        return False
    s.execute(text("INSERT INTO monitoring_gaps (service, started_at, ended_at) VALUES (:svc, :a, now())"), {"svc": service, "a": prev})
    log.warning("monitoring gap recorded", service=service, since=str(prev))
    return True


# Slots whose deadline fell inside a recorded gap of an observing service, or inside the still-open
# silence of one that has not reported for longer than its threshold. Predicate on expected_runs
# `er`; bind :observing (list), :self_service and :thr_s.
IN_GAP_SQL = """
    EXISTS (SELECT 1 FROM monitoring_gaps g WHERE g.service = ANY(:observing)
            AND er.deadline >= g.started_at AND er.deadline < g.ended_at)
    OR EXISTS (SELECT 1 FROM platform_heartbeats h
               WHERE h.service = ANY(:observing) AND h.service <> :self_service
                 AND h.last_seen < now() - make_interval(secs => :thr_s)
                 AND er.deadline >= h.last_seen)
"""
