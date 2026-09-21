"""R6 — the real message pipeline: NATS JetStream + the actual worker code + Postgres.

Needs DATABASE_URL and a NATS server at NATS_URL. The workers' loops were split into `drain()`
so the harness can step them deterministically instead of racing a background task.
"""
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

pytest_plugins = ()


def now():
    return datetime.now(UTC)


async def quiesce(js, *, exec_sub=None, rule_sub=None):
    """Drain leftovers so a test only sees its own messages. CS_EXEC is WORK_QUEUE (one consumer,
    shared durable); CS_STATUS is INTEREST, so per-test durables are fine there but start at ALL."""
    from cronsentinel.workers import exec_processor, rule_engine
    for _ in range(5):
        a = await exec_processor.drain(js, exec_sub, timeout=1) if exec_sub else 0
        b = await rule_engine.drain(rule_sub, timeout=1) if rule_sub else 0
        if not a and not b:
            return


@pytest.fixture
async def js():
    from cronsentinel import events
    nc, j = await events.connect()
    # R17: start from empty streams. Leftovers from another run (e.g. the full-stack test's
    # reconciler publishing jobstatus events) made drain() counts wrong. These tests own the local
    # broker — do not run them while scripts/fullstack.sh is up, it would lose its messages.
    for stream in ("CS_EXEC", "CS_STATUS"):
        try:
            await j.purge_stream(stream)
        except Exception:
            pass  # stream not created yet; events.connect() ensures it on first publish
    yield j
    await nc.close()   # drain() waits on the shared durables' inflight and stalls the suite


@pytest.mark.asyncio
async def test_exec_event_flows_nats_to_db_and_emits_status(js, make_job, org):
    """agent event → exec.* → exec-processor → executions row + jobstatus.* with a usable payload."""
    from cronsentinel.db import system_session
    from cronsentinel.workers import exec_processor
    j = make_job(schedule="* * * * *", grace_s=120, expected_runtime_s=60, created_ago=timedelta(minutes=2))
    eid = f"e2e-{uuid.uuid4().hex}"
    sub = await exec_processor.subscribe(js)
    status_sub = await js.pull_subscribe(f"jobstatus.{org['id']}", durable=f"t{uuid.uuid4().hex[:8]}", stream="CS_STATUS")
    await quiesce(js, exec_sub=sub)

    await js.publish(f"exec.{org['id']}", json.dumps({
        "org_id": str(org["id"]), "job_id": str(j), "kind": "start", "execution_id": eid,
        "sequence": 0, "server_ts": now().isoformat(), "meta": {}}).encode())
    assert await exec_processor.drain(js, sub, timeout=5) == 1

    with system_session() as s:
        row = s.execute(text("SELECT status::text FROM executions WHERE id=:e"), {"e": eid}).first()
    assert row.status == "running"

    msgs = await status_sub.fetch(1, timeout=5)
    ev = json.loads(msgs[0].data)
    await msgs[0].ack()
    # the contract the rule engine and the outbound exporter both consume
    for k in ("org_id", "job_id", "prev_status", "new_status", "new_state", "occurred_at"):
        assert k in ev, f"jobstatus payload missing {k}"
    assert ev["org_id"] == str(org["id"]) and ev["new_status"] == "running"


@pytest.mark.asyncio
async def test_failure_flows_all_the_way_to_an_incident(js, make_job, org):
    """exec.fail → exec-processor → jobstatus.* → rule-engine → incident row. No mocks in between."""
    from cronsentinel.db import system_session
    from cronsentinel.workers import exec_processor, rule_engine
    j = make_job(schedule="* * * * *", grace_s=120, expected_runtime_s=60, created_ago=timedelta(minutes=2))
    with system_session() as s:  # a rule that fires on any failure, no channels (notification is a separate task)
        s.execute(text("""INSERT INTO alert_rules (org_id, name, condition, scope, params, severity, channel_ids, enabled)
            VALUES (:o, 'any failure', 'failed', '{}', '{}', 'high', '{}', true)"""), {"o": org["id"]})
    # per-test durables: the shared ones carry a backlog from other tests, and INTEREST retention
    # keeps status messages until every durable has acked them
    exec_sub = await exec_processor.subscribe(js)
    rule_sub = await rule_engine.subscribe(js, durable=f"t{uuid.uuid4().hex[:8]}")
    await quiesce(js, exec_sub=exec_sub, rule_sub=rule_sub)
    eid = f"e2e-{uuid.uuid4().hex}"
    base = {"org_id": str(org["id"]), "job_id": str(j), "execution_id": eid, "server_ts": now().isoformat(), "meta": {}}
    await js.publish(f"exec.{org['id']}", json.dumps({**base, "kind": "start", "sequence": 0}).encode())
    await exec_processor.drain(js, exec_sub, timeout=5)
    await rule_engine.drain(rule_sub, timeout=3)
    await js.publish(f"exec.{org['id']}", json.dumps({**base, "kind": "fail", "sequence": 1, "exit_code": 2, "stderr_tail": "boom"}).encode())
    assert await exec_processor.drain(js, exec_sub, timeout=5) == 1
    assert await rule_engine.drain(rule_sub, timeout=5) >= 1
    await rule_engine.drain(rule_sub, timeout=1)  # sweep any trailing status message

    with system_session() as s:
        inc = s.execute(text("SELECT title, severity::text, status::text, affected_job_ids FROM incidents WHERE org_id=:o"), {"o": org["id"]}).all()
    assert len(inc) == 1 and inc[0].severity == "high" and str(j) in [str(x) for x in inc[0].affected_job_ids]


@pytest.mark.asyncio
async def test_unknown_status_change_creates_no_incident(js, make_job, org):
    """R2 suppression holds across the real transport, not just in the unit test."""
    from cronsentinel.db import system_session
    from cronsentinel.workers import rule_engine
    j = make_job()
    with system_session() as s:
        s.execute(text("""INSERT INTO alert_rules (org_id, name, condition, scope, params, severity, channel_ids, enabled)
            VALUES (:o, 'any failure', 'failed', '{}', '{}', 'high', '{}', true)"""), {"o": org["id"]})
    sub = await rule_engine.subscribe(js, durable=f"t{uuid.uuid4().hex[:8]}")
    await quiesce(js, rule_sub=sub)
    await js.publish(f"jobstatus.{org['id']}", json.dumps({
        "org_id": str(org["id"]), "job_id": str(j), "prev_status": "healthy", "new_status": "missed",
        "new_state": "unknown", "unknown_reason": "agent_offline"}).encode())
    assert await rule_engine.drain(sub, timeout=5) == 1
    with system_session() as s:
        assert s.execute(text("SELECT count(*) FROM incidents WHERE org_id=:o"), {"o": org["id"]}).scalar() == 0


@pytest.mark.asyncio
async def test_duplicate_delivery_is_idempotent(js, make_job, org):
    """JetStream is at-least-once: the same exec event twice must not double-count."""
    from cronsentinel.db import system_session
    from cronsentinel.workers import exec_processor
    j = make_job()
    sub = await exec_processor.subscribe(js)
    await quiesce(js, exec_sub=sub)
    eid = f"dup-{uuid.uuid4().hex}"
    payload = json.dumps({"org_id": str(org["id"]), "job_id": str(j), "kind": "start", "execution_id": eid,
                          "sequence": 0, "server_ts": now().isoformat(), "meta": {}}).encode()
    await js.publish(f"exec.{org['id']}", payload)
    await js.publish(f"exec.{org['id']}", payload)
    await exec_processor.drain(js, sub, timeout=5)
    with system_session() as s:
        assert s.execute(text("SELECT count(*) FROM executions WHERE id=:e"), {"e": eid}).scalar() == 1
        assert s.execute(text("SELECT count(*) FROM execution_events WHERE execution_id=:e"), {"e": eid}).scalar() == 1
