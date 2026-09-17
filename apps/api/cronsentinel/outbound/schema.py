"""R4 — the AEGIS-downstream boundary.

JobWatch is upstream. AEGIS (and anything else) consumes JobWatch through *this* contract only:
a versioned, self-describing signal envelope pushed to destinations the tenant configures.
Nothing downstream ever reads JobWatch's database, NATS, or internal APIs, and nothing in
JobWatch imports from AEGIS. That keeps JobWatch sellable standalone and keeps the data boundary
honest — a tenant's job telemetry leaves only where they pointed it.

Contract rules (breaking any of these is a major version bump):
  - envelope fields are stable; `data` is additive-only within a major version
  - `signal_id` is a ULID and is the consumer's idempotency key
  - every signal carries `occurred_at` (event time) and `emitted_at` (send time) separately
  - no secrets, no stdout/stderr bodies, no env — only tails already redacted at ingest
"""
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from ulid import ULID

SCHEMA = "jobwatch.signal"
VERSION = 1

EVENT_TYPES = (
    "job.state_changed",    # four-state transition, with legacy status for convenience
    "slot.settled",         # an expected run reached a terminal state
    "incident.opened",
    "incident.updated",     # ack / note / severity
    "incident.resolved",
    "agent.status",         # online/offline
)


@dataclass(frozen=True)
class Signal:
    event_type: str
    org_id: str
    occurred_at: datetime
    data: dict[str, Any]
    workspace_id: str | None = None
    subject: dict[str, str] = field(default_factory=dict)   # {"job_id":..} / {"incident_id":..} / {"agent_id":..}
    signal_id: str = field(default_factory=lambda: str(ULID()))
    emitted_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def envelope(self) -> dict:
        d = asdict(self)
        d["occurred_at"] = self.occurred_at.isoformat()
        d["emitted_at"] = self.emitted_at.isoformat()
        return {"schema": SCHEMA, "version": VERSION, "source": "wecrew-jobwatch", **d}


def validate_event_type(t: str) -> None:
    if t not in EVENT_TYPES:
        raise ValueError(f"unknown event_type {t!r}; allowed: {', '.join(EVENT_TYPES)}")


# ---- builders: pure, from the internal event dicts already flowing on NATS ------------------

def from_jobstatus(ev: dict, job: dict) -> Signal:
    """`ev` is the jobstatus.* message; `job` is the row projection we attach for the consumer."""
    return Signal(
        event_type="job.state_changed", org_id=ev["org_id"], workspace_id=job.get("workspace_id"),
        occurred_at=_ts(ev.get("occurred_at")), subject={"job_id": ev["job_id"]},
        data={
            "job": {k: job.get(k) for k in ("name", "kind", "schedule_expr", "tz", "environment", "tags", "reliability_score")},
            "state": ev.get("new_state"), "prev_state": ev.get("prev_state"),
            "unknown_reason": ev.get("unknown_reason"),
            "legacy_status": ev.get("new_status"), "legacy_prev_status": ev.get("prev_status"),
            "consecutive_failures": ev.get("consecutive_failures", 0),
            "execution_id": ev.get("execution_id"),
        })


def from_slot(slot: dict, job: dict) -> Signal:
    return Signal(
        event_type="slot.settled", org_id=slot["org_id"], workspace_id=job.get("workspace_id"),
        occurred_at=_ts(slot.get("settled_at")), subject={"job_id": slot["job_id"], "expected_run_id": str(slot["id"])},
        data={"job": {"name": job.get("name")}, "scheduled_for": _iso(slot["scheduled_for"]), "state": slot["state"],
              "execution_id": slot.get("execution_id"), "duration_ms": slot.get("duration_ms"),
              "exit_code": slot.get("exit_code"), "failure_reason": slot.get("failure_reason"),
              "lateness_s": slot.get("lateness_s")})


def from_incident(kind: str, inc: dict) -> Signal:
    return Signal(
        event_type=f"incident.{kind}", org_id=inc["org_id"], workspace_id=inc.get("workspace_id"),
        occurred_at=_ts(inc.get("occurred_at")), subject={"incident_id": inc["id"]},
        data={"title": inc.get("title"), "severity": inc.get("severity"), "status": inc.get("status"),
              "affected_job_ids": inc.get("affected_job_ids", []), "correlation_signals": inc.get("correlation_signals", {}),
              "acked_by": inc.get("acked_by"), "resolution": inc.get("resolution"),
              "url": inc.get("url")})


def from_agent(agent: dict, online: bool) -> Signal:
    return Signal(
        event_type="agent.status", org_id=agent["org_id"], occurred_at=_ts(agent.get("occurred_at")),
        subject={"agent_id": agent["id"]},
        data={"name": agent.get("name"), "host": agent.get("host"), "kind": agent.get("kind"), "online": online,
              "last_seen_at": _iso(agent.get("last_seen_at")), "jobs_affected": agent.get("jobs_affected", 0)})


def _ts(v) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=UTC)
    if isinstance(v, str):
        return datetime.fromisoformat(v)
    return datetime.now(UTC)


def _iso(v):
    return v.isoformat() if isinstance(v, datetime) else v
