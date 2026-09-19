"""R2 end-to-end: a dead agent must turn its jobs UNKNOWN, and UNKNOWN must not reach the rule engine."""
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text


def now():
    return datetime.now(UTC)


def test_agent_offline_makes_jobs_unknown_not_failing(make_job, org):
    from cronsentinel.db import system_session
    from cronsentinel.workers import reconciler
    from cronsentinel.workers.schedule_generator import tick
    j = make_job(schedule="* * * * *", grace_s=30, expected_runtime_s=10, created_ago=timedelta(minutes=10))
    with system_session() as s:
        aid = s.execute(text("""INSERT INTO agents (org_id, kind, name, key_hash, key_prefix, status, last_seen_at, heartbeat_interval_s)
            VALUES (:o, 'linux', 'dead-host', 'x', 'csa_x', 'active', :seen, 60) RETURNING id"""),
            {"o": org["id"], "seen": now() - timedelta(minutes=30)}).scalar()
        sid = s.execute(text("INSERT INTO servers (org_id, agent_id, hostname) VALUES (:o, :a, :h) RETURNING id"),
                        {"o": org["id"], "a": aid, "h": f"dead-{uuid.uuid4().hex[:6]}"}).scalar()
        s.execute(text("UPDATE jobs SET server_id=:s WHERE id=:j"), {"s": sid, "j": j})
        tick(s)
    with system_session() as s:
        changes = reconciler.tick(s)
        jb = s.execute(text("SELECT job_state::text, unknown_reason FROM jobs WHERE id=:j"), {"j": j}).first()
    # slots are still settled as missed (the schedule did not run) …
    with system_session() as s:
        missed = s.execute(text("SELECT count(*) FROM expected_runs WHERE job_id=:j AND state='missed'"), {"j": j}).scalar()
    assert missed >= 8
    # … but the job is UNKNOWN/agent_offline, not FAILING, and the emitted change says so
    assert (jb.job_state, jb.unknown_reason) == ("unknown", "agent_offline")
    ev = next(c for c in changes if c["job_id"] == str(j))
    assert ev["new_state"] == "unknown" and ev["unknown_reason"] == "agent_offline"


def test_rule_engine_drops_unknown_events(org):
    """No incident, no notification, regardless of rules configured."""
    from cronsentinel.alerting import engine
    from cronsentinel.db import system_session
    ev = {"org_id": str(org["id"]), "job_id": str(uuid.uuid4()), "prev_status": "healthy", "new_status": "missed",
          "new_state": "unknown", "unknown_reason": "agent_offline"}
    with system_session() as s:
        before = s.execute(text("SELECT count(*) FROM incidents WHERE org_id=:o"), {"o": org["id"]}).scalar()
    engine.handle_status_change(ev)
    with system_session() as s:
        after = s.execute(text("SELECT count(*) FROM incidents WHERE org_id=:o"), {"o": org["id"]}).scalar()
    assert after == before
