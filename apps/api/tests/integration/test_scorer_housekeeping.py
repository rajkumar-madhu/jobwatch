"""R23 — scorer housekeeping: partition rescue, phase isolation, partition drop, retention batches."""
import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs postgres (DATABASE_URL)")


def _future_month(months: int) -> datetime:
    now = datetime.now(UTC)
    y, m = divmod(now.month - 1 + months, 12)
    return datetime(now.year + y, m + 1, 15, tzinfo=UTC)


def test_partition_is_created_even_when_rows_are_stranded_in_default(make_job, org):
    """Before migration 0012 this raised CheckViolation, and kept raising every hour after."""
    from cronsentinel.db import system_session
    j = make_job()
    # A month with no partition yet (beyond MONTHS_AHEAD). Earlier runs leave their rescued
    # partition behind — only the owner could drop it — so search rather than hard-code.
    with system_session() as s:
        existing = set(s.execute(text("SELECT relname FROM pg_class WHERE relname LIKE 'executions_2%'")).scalars())
    when = next(_future_month(m) for m in range(8, 400) if f"executions_{_future_month(m):%Y%m}" not in existing)
    part = f"executions_{when:%Y%m}"
    with system_session() as s:
        s.execute(text("INSERT INTO executions (id, org_id, job_id, status, scheduled_ts, server_received_ts) "
                       "VALUES (:e, :o, :j, 'success', :t, :t)"), {"e": f"skew-{uuid.uuid4().hex}", "o": org["id"], "j": j, "t": when})
        assert s.execute(text("SELECT count(*) FROM executions_default WHERE scheduled_ts = :t"), {"t": when}).scalar() == 1
    with system_session() as s:
        s.execute(text("SELECT ensure_month_partition('executions', :d)"), {"d": when.date()})
    with system_session() as s:
        assert s.execute(text("SELECT count(*) FROM executions_default WHERE scheduled_ts = :t"), {"t": when}).scalar() == 0
        assert s.execute(text(f'SELECT count(*) FROM "{part}"')).scalar() == 1
        # and it is a real, attached partition, still reachable through the parent
        assert s.execute(text("SELECT count(*) FROM executions WHERE scheduled_ts = :t"), {"t": when}).scalar() == 1
        s.execute(text("DELETE FROM executions WHERE scheduled_ts = :t"), {"t": when})
    # left in place: an empty future partition is harmless, and only the owner may drop it


def test_a_failing_phase_does_not_stop_the_others(monkeypatch):
    from cronsentinel.workers import scorer
    ran = []
    monkeypatch.setattr(scorer, "ensure_partitions", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(scorer, "score", lambda: ran.append("score") or 0)
    monkeypatch.setattr(scorer, "usage", lambda: ran.append("usage"))
    monkeypatch.setattr(scorer, "retention", lambda: ran.append("retention") or {})
    monkeypatch.setattr(scorer, "drop_empty_partitions", lambda: ran.append("drop") or [])
    scorer.tick()
    assert ran == ["score", "usage", "retention", "drop"]


def test_empty_old_partitions_are_dropped_and_non_empty_ones_kept(make_job, org):
    from cronsentinel.db import system_session
    from cronsentinel.workers import scorer
    j = make_job()
    with system_session() as s:
        s.execute(text("SELECT ensure_month_partition('executions', DATE '2024-01-01')"))
        s.execute(text("SELECT ensure_month_partition('executions', DATE '2024-02-01')"))
        s.execute(text("INSERT INTO executions (id, org_id, job_id, status, scheduled_ts, server_received_ts) "
                       "VALUES (:e, :o, :j, 'success', '2024-02-10', '2024-02-10')"), {"e": f"keep-{uuid.uuid4().hex}", "o": org["id"], "j": j})
    dropped = scorer.drop_empty_partitions()
    with system_session() as s:
        exists = set(s.execute(text("SELECT relname FROM pg_class WHERE relname IN ('executions_202401','executions_202402')")).scalars())
        s.execute(text("DELETE FROM executions WHERE scheduled_ts = '2024-02-10'"))
    scorer.drop_empty_partitions()   # now empty, so it goes too
    assert "executions_202401" in dropped and "executions_202401" not in exists
    assert "executions_202402" in exists, "a partition that still holds rows was dropped"
    # never the current month, however empty
    assert datetime.now(UTC).strftime("executions_%Y%m") not in dropped


def test_retention_deletes_in_batches_and_takes_logs_with_it(make_job, org, monkeypatch):
    from cronsentinel.db import system_session
    from cronsentinel.workers import scorer
    monkeypatch.setattr(scorer, "DELETE_BATCH", 7)      # force several batches
    j = make_job()
    u = uuid.uuid4().hex
    with system_session() as s:
        s.execute(text("UPDATE organizations SET plan='free' WHERE id=:o"), {"o": org["id"]})   # 7-day retention
        s.execute(text("""INSERT INTO executions (id, org_id, job_id, status, scheduled_ts, server_received_ts)
            SELECT 'ret-' || :u || '-' || g, :o, :j, 'success', now() - interval '20 days' - g * interval '1 minute', now() - interval '20 days'
            FROM generate_series(1, 30) g"""), {"o": org["id"], "j": j, "u": u})
        s.execute(text("INSERT INTO execution_logs (org_id, execution_id, stream, chunk_idx, content) SELECT :o, 'ret-' || :u || '-' || g, 'stdout', 0, 'x' FROM generate_series(1, 30) g"),
                  {"o": org["id"], "u": u})
        fresh = f"fresh-{uuid.uuid4().hex}"
        s.execute(text("INSERT INTO executions (id, org_id, job_id, status, scheduled_ts, server_received_ts) VALUES (:e, :o, :j, 'success', now(), now())"),
                  {"e": fresh, "o": org["id"], "j": j})
    out = scorer.retention()
    assert out["executions"] >= 30
    with system_session() as s:
        assert s.execute(text("SELECT count(*) FROM executions WHERE org_id=:o AND id LIKE 'ret-%'"), {"o": org["id"]}).scalar() == 0
        assert s.execute(text("SELECT count(*) FROM execution_logs WHERE org_id=:o AND execution_id LIKE 'ret-%'"), {"o": org["id"]}).scalar() == 0
        assert s.execute(text("SELECT count(*) FROM executions WHERE id=:e"), {"e": fresh}).scalar() == 1, "retention deleted in-window data"


def test_drop_function_refuses_anything_it_should_not_touch():
    from cronsentinel.db import system_session
    from datetime import datetime, UTC
    with system_session() as s:
        for target in ("jobs", "executions", "executions_default", "organizations", datetime.now(UTC).strftime("executions_%Y%m")):
            assert s.execute(text("SELECT drop_partition_if_empty(:p)"), {"p": target}).scalar() is False, target
        assert s.execute(text("SELECT to_regclass('jobs') IS NOT NULL")).scalar()
