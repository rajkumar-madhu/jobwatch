"""R24 — incremental log-storage metering must always equal a full recount."""
import os
import uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs postgres (DATABASE_URL)")


def _metered(s, org):
    """org_storage total plus anything still in the ledger — what a compaction would produce."""
    return s.execute(text("""SELECT COALESCE((SELECT bytes FROM org_storage WHERE org_id=:o), 0)
                                  + COALESCE((SELECT sum(delta) FROM storage_ledger WHERE org_id=:o), 0)"""), {"o": org}).scalar()


def _recount(s, org):
    return s.execute(text("SELECT COALESCE(sum(octet_length(content)), 0) FROM execution_logs WHERE org_id=:o"), {"o": org}).scalar()


def _logs(s, org, n, content="héllo wörld ✓", prefix=None):
    prefix = prefix or uuid.uuid4().hex
    s.execute(text("INSERT INTO execution_logs (org_id, execution_id, stream, chunk_idx, content) "
                   "SELECT :o, :p || '-' || g, 'stdout', 0, :c FROM generate_series(1, :n) g"),
              {"o": org, "p": prefix, "c": content, "n": n})
    return prefix


def test_inserts_deletes_and_updates_keep_the_meter_exact(org):
    from cronsentinel.db import system_session
    o = org["id"]
    with system_session() as s:
        p = _logs(s, o, 50)
        _logs(s, o, 20, content="plain ascii")
        s.execute(text("INSERT INTO execution_logs (org_id, execution_id, stream, chunk_idx, content) VALUES (:o, :e, 'stdout', 0, 'dup') "
                       "ON CONFLICT DO NOTHING"), {"o": o, "e": f"{p}-1"})         # conflict: must add nothing
        s.execute(text("DELETE FROM execution_logs WHERE org_id=:o AND execution_id LIKE :p"), {"o": o, "p": f"{p}-1%"})
        s.execute(text("UPDATE execution_logs SET content = content || ' more' WHERE org_id=:o AND execution_id LIKE :p"), {"o": o, "p": f"{p}-2%"})
    with system_session() as s:
        assert _metered(s, o) == _recount(s, o) > 0


def test_bytes_not_characters():
    """The old meter used length() — characters. 'ü' is 1 character, 2 bytes."""
    from cronsentinel.db import system_session
    with system_session() as s:
        assert s.execute(text("SELECT octet_length('ü'), length('ü')")).first() == (2, 1)


def test_compaction_folds_the_ledger_and_survives_deleted_orgs(org):
    from cronsentinel.db import system_session
    from cronsentinel.workers import scorer
    gone = uuid.uuid4()
    with system_session() as s:
        _logs(s, org["id"], 10)
        s.execute(text("INSERT INTO organizations (id, name, slug) VALUES (:i, :n, :n)"), {"i": gone, "n": f"gone-{gone.hex[:8]}"})
        _logs(s, gone, 5)
    with system_session() as s:
        s.execute(text("SELECT purge_org(:o)"), {"o": gone})          # writes negative deltas...
        s.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": gone})   # ...then the org goes
    scorer.usage()                                                      # must not trip org_storage's FK
    with system_session() as s:
        assert s.execute(text("SELECT count(*) FROM storage_ledger")).scalar() == 0
        assert s.execute(text("SELECT count(*) FROM org_storage WHERE org_id=:o"), {"o": gone}).scalar() == 0
        assert s.execute(text("SELECT bytes FROM org_storage WHERE org_id=:o"), {"o": org["id"]}).scalar() == _recount(s, org["id"])
        usage = s.execute(text("SELECT storage_bytes FROM usage_records WHERE org_id=:o ORDER BY period DESC LIMIT 1"), {"o": org["id"]}).scalar()
        assert usage == _recount(s, org["id"])


def test_retention_deletes_are_metered(make_job, org):
    from cronsentinel.db import system_session
    from cronsentinel.workers import scorer
    j = make_job()
    u = uuid.uuid4().hex
    with system_session() as s:
        s.execute(text("UPDATE organizations SET plan='free' WHERE id=:o"), {"o": org["id"]})
        s.execute(text("""INSERT INTO executions (id, org_id, job_id, status, scheduled_ts, server_received_ts)
            SELECT 'm-' || :u || '-' || g, :o, :j, 'success', now() - interval '30 days', now() - interval '30 days' FROM generate_series(1, 12) g"""),
            {"u": u, "o": org["id"], "j": j})
        _logs(s, org["id"], 12, prefix=f"m-{u}")
    scorer.retention()
    with system_session() as s:
        assert _metered(s, org["id"]) == _recount(s, org["id"])


def test_tenant_log_reads_can_use_the_org_index(org):
    from cronsentinel.db import tenant_session
    with tenant_session(org["id"]) as s:
        s.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(r[0] for r in s.execute(text("EXPLAIN SELECT count(*) FROM execution_logs")).all())
    assert "execution_logs_org_idx" in plan, plan
