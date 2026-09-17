"""R4 — boundary contract tests. If these break, consumers (AEGIS) break."""
import json
from datetime import UTC, datetime

import pytest

from cronsentinel.outbound import schema
from cronsentinel.outbound.deliver import sign

EV = {"org_id": "org", "job_id": "job", "new_state": "failing", "prev_state": "ok", "new_status": "failed",
      "prev_status": "healthy", "consecutive_failures": 2, "occurred_at": "2026-09-17T10:00:00+00:00"}
JOB = {"name": "nightly", "kind": "cron", "workspace_id": "ws", "tags": ["db"], "reliability_score": 91}


def test_envelope_has_stable_header_fields():
    e = schema.from_jobstatus(EV, JOB).envelope()
    for k in ("schema", "version", "source", "event_type", "org_id", "workspace_id", "signal_id", "occurred_at", "emitted_at", "subject", "data"):
        assert k in e
    assert e["schema"] == "jobwatch.signal" and e["version"] == 1 and e["source"] == "wecrew-jobwatch"


def test_event_time_and_emit_time_are_separate():
    e = schema.from_jobstatus(EV, JOB).envelope()
    assert e["occurred_at"] == "2026-09-17T10:00:00+00:00"
    assert e["emitted_at"] != e["occurred_at"]


def test_signal_id_is_unique_per_signal():
    a, b = schema.from_jobstatus(EV, JOB), schema.from_jobstatus(EV, JOB)
    assert a.signal_id != b.signal_id and len(a.signal_id) == 26


def test_job_state_signal_carries_four_state_and_legacy():
    d = schema.from_jobstatus(EV, JOB).envelope()["data"]
    assert d["state"] == "failing" and d["legacy_status"] == "failed" and d["consecutive_failures"] == 2
    assert d["job"]["name"] == "nightly" and d["job"]["reliability_score"] == 91


def test_envelope_is_json_serialisable():
    json.dumps(schema.from_slot({"id": 1, "org_id": "o", "job_id": "j", "scheduled_for": datetime(2026, 9, 17, tzinfo=UTC),
                                  "state": "missed", "settled_at": datetime.now(UTC)}, JOB).envelope())


def test_no_forbidden_fields_leak():
    """Contract: no env, no log bodies, no secrets in any builder output."""
    for sig in (schema.from_jobstatus(EV, JOB), schema.from_agent({"id": "a", "org_id": "o", "host": "h"}, True),
                schema.from_incident("opened", {"id": "i", "org_id": "o"})):
        flat = json.dumps(sig.envelope(), default=str).lower()
        for bad in ("stdout", "stderr", "env", "password", "token", "secret"):
            assert f'"{bad}"' not in flat


def test_unknown_event_type_rejected():
    with pytest.raises(ValueError):
        schema.validate_event_type("job.exploded")


def test_incident_kind_maps_to_event_type():
    assert schema.from_incident("resolved", {"id": "i", "org_id": "o"}).event_type == "incident.resolved"


def test_signature_is_hmac_sha256_hex():
    s = sign("topsecret-topsecret", b'{"a":1}')
    assert s.startswith("sha256=") and len(s) == 7 + 64
