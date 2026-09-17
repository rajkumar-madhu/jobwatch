"""Pure rule evaluation logic — no I/O. Tested in tests/test_rules.py."""
from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

BAD = {"failed", "timeout", "missed"}
CONDITIONS = {"failed", "missed", "late", "runtime_exceeded", "recovered", "consecutive_failures", "sla_breach"}


@dataclass
class JobCtx:
    id: str
    name: str
    tags: list[str]
    environment_id: str | None
    workspace_id: str
    team_id: str | None
    consecutive_failures: int = 0
    success_rate: float | None = None  # over sla_window, 0-100
    sla_target: float | None = None


@dataclass
class Rule:
    id: str
    condition: str
    scope: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)
    severity: str = "medium"
    channel_ids: list[str] = field(default_factory=list)
    business_hours: dict | None = None
    repeat_interval_s: int | None = None


def scope_matches(rule: Rule, job: JobCtx) -> bool:
    sc = rule.scope or {}
    if not sc:
        return True
    if sc.get("job_ids") and job.id in sc["job_ids"]:
        return True
    if sc.get("tags") and set(sc["tags"]) & set(job.tags):
        return True
    if sc.get("environment_ids") and job.environment_id in sc["environment_ids"]:
        return True
    if sc.get("workspace_ids") and job.workspace_id in sc["workspace_ids"]:
        return True
    if sc.get("team_ids") and job.team_id in sc["team_ids"]:
        return True
    return False


def condition_fires(rule: Rule, prev: str, new: str, job: JobCtx) -> bool:
    c = rule.condition
    if c == "failed":
        return new == "failed"
    if c == "missed":
        return new == "missed"
    if c == "late":
        return new == "late"
    if c == "runtime_exceeded":
        return new == "timeout"
    if c == "recovered":
        return new == "recovered"
    if c == "consecutive_failures":
        return new in ("failed", "timeout") and job.consecutive_failures >= int(rule.params.get("count", 3))
    if c == "sla_breach":
        return job.sla_target is not None and job.success_rate is not None and job.success_rate < job.sla_target
    return False


def in_business_hours(bh: dict | None, now: datetime) -> bool:
    """bh = {"tz": "Asia/Kolkata", "days": [1..5] (Mon=1), "start": "09:00", "end": "18:00"}. None ⇒ always."""
    if not bh:
        return True
    local = now.astimezone(ZoneInfo(bh.get("tz", "UTC")))
    if local.isoweekday() not in bh.get("days", [1, 2, 3, 4, 5]):
        return False
    st = time.fromisoformat(bh.get("start", "00:00")); en = time.fromisoformat(bh.get("end", "23:59"))
    return st <= local.time() <= en


def is_flapping(change_ts: list[datetime], now: datetime, window_s: int = 600, threshold: int = 3) -> bool:
    """D10: ≥threshold status changes in window ⇒ flapping (single 'flapping' alert instead of N)."""
    recent = [t for t in change_ts if (now - t).total_seconds() <= window_s]
    return len(recent) >= threshold


def dedup_key(rule_id: str, job_id: str, condition: str) -> str:
    return f"{rule_id}:{job_id}:{condition}"


def should_repeat(last_sent: datetime | None, repeat_interval_s: int | None, now: datetime) -> bool:
    if last_sent is None:
        return True
    if not repeat_interval_s:
        return False
    return (now - last_sent).total_seconds() >= repeat_interval_s
