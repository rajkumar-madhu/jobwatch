"""R4 end-to-end: destination config → fanout → delivery ledger (Celery patched, HTTP patched)."""
import uuid
from unittest.mock import patch

from sqlalchemy import text


def _dest(org, url="https://aegis.example.test/ingest", secret="s3cr3t-s3cr3t-s3cr3t", event_types=()):
    from cronsentinel.crypto import encrypt_json
    from cronsentinel.db import system_session
    with system_session() as s:
        return s.execute(text("""INSERT INTO signal_destinations (org_id, name, kind, config_enc, event_types)
            VALUES (:o, 'aegis', 'webhook', :c, :et) RETURNING id"""),
            {"o": org["id"], "c": encrypt_json({"url": url, "secret": secret}), "et": list(event_types)}).scalar()


def test_fanout_targets_only_matching_destinations(org):
    from cronsentinel.db import system_session
    from cronsentinel.outbound import schema
    from cronsentinel.workers.outbound_exporter import fanout
    a = _dest(org)                                   # all events
    b = _dest(org, event_types=["incident.opened"])  # incidents only
    sig = schema.from_jobstatus({"org_id": str(org["id"]), "job_id": str(uuid.uuid4()), "new_state": "failing", "prev_state": "ok",
                                 "new_status": "failed", "prev_status": "healthy"}, {"name": "x"})
    with patch("cronsentinel.workers.outbound_exporter.deliver_signal") as task:
        with system_session() as s:
            n = fanout(s, sig)
    assert n == 1
    called = [c.args[1] for c in task.delay.call_args_list]
    assert called == [str(a)] and str(b) not in called


def test_delivery_is_signed_and_ledgered(org):
    from cronsentinel.db import system_session
    from cronsentinel.outbound import deliver, schema
    d = _dest(org)
    env = schema.from_jobstatus({"org_id": str(org["id"]), "job_id": "j", "new_state": "ok", "prev_state": "unknown",
                                 "new_status": "healthy", "prev_status": "unknown"}, {"name": "x"}).envelope()
    seen = {}

    class R: status_code = 202
    def fake_post(url, content, headers, timeout):
        seen.update(url=url, headers=headers, body=content); return R()
    with patch.object(deliver.httpx, "post", fake_post):
        deliver.deliver_signal.apply(args=(str(org["id"]), str(d), env)).get()
    assert seen["url"] == "https://aegis.example.test/ingest"
    assert seen["headers"]["X-JobWatch-Signature"] == deliver.sign("s3cr3t-s3cr3t-s3cr3t", seen["body"])
    assert seen["headers"]["X-JobWatch-Signal-Id"] == env["signal_id"]
    with system_session() as s:
        row = s.execute(text("SELECT status, response_code, attempts FROM signal_deliveries WHERE destination_id=:d AND signal_id=:sid"),
                        {"d": d, "sid": env["signal_id"]}).first()
    assert (row.status, row.response_code, row.attempts) == ("sent", 202, 1)


def test_delivery_failure_is_recorded_and_retried(org):
    from cronsentinel.db import system_session
    from cronsentinel.outbound import deliver, schema
    d = _dest(org)
    env = schema.from_agent({"id": "a", "org_id": str(org["id"])}, False).envelope()

    class R: status_code = 503
    with patch.object(deliver.httpx, "post", lambda *a, **k: R()):
        try:
            deliver.deliver_signal.apply(args=(str(org["id"]), str(d), env), throw=True).get()
        except Exception:
            pass
    with system_session() as s:
        row = s.execute(text("SELECT status, last_error FROM signal_deliveries WHERE destination_id=:d AND signal_id=:sid"),
                        {"d": d, "sid": env["signal_id"]}).first()
    assert row.status in ("failed", "dead") and "503" in row.last_error


def test_tenant_cannot_see_another_tenants_destinations(org):
    from cronsentinel.db import system_session, tenant_session
    other = uuid.uuid4()
    with system_session() as s:
        s.execute(text("INSERT INTO organizations (id, name, slug) VALUES (:id, :n, :n)"), {"id": other, "n": f"o-{other.hex[:8]}"})
    _dest(org); _dest({"id": other})
    with tenant_session(org["id"]) as s:
        assert s.execute(text("SELECT count(*) FROM signal_destinations")).scalar() == 1
    with system_session() as s:
        s.execute(text("DELETE FROM organizations WHERE id=:id"), {"id": other})
