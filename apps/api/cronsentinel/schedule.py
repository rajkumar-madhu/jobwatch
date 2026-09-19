"""Server-side schedule evaluation (D4)."""
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from cron_descriptor import ExpressionDescriptor, Options
from croniter import croniter


def validate(expr: str) -> bool:
    return croniter.is_valid(expr)


def human(expr: str) -> str:
    try:
        o = Options(); o.use_24hour_time_format = False; o.casing_type = 2  # sentence case
        return str(ExpressionDescriptor(expr, o))
    except Exception:
        return expr


def next_runs(expr: str, tz: str = "UTC", n: int = 5, after: datetime | None = None) -> list[datetime]:
    zone = ZoneInfo(tz)
    base = (after or datetime.now(UTC)).astimezone(zone)
    it = croniter(expr, base)
    return [it.get_next(datetime).astimezone(UTC) for _ in range(n)]


def next_run(expr: str, tz: str = "UTC", after: datetime | None = None) -> datetime:
    return next_runs(expr, tz, 1, after)[0]


def prev_run(expr: str, tz: str = "UTC", before: datetime | None = None) -> datetime:
    zone = ZoneInfo(tz)
    base = (before or datetime.now(UTC)).astimezone(zone)
    return croniter(expr, base).get_prev(datetime).astimezone(UTC)
