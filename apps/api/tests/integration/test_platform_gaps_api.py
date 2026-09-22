"""R25 — tenants can see when the platform was not watching, but only their own unobserved count."""
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs postgres (DATABASE_URL)")


@pytest.fixture
def api_client(org):
    """A viewer-role API key: the lowest role must be able to see why its slots were unobserved."""
    from fastapi.testclient import TestClient
    from cronsentinel.auth import generate_api_key, hash_secret
    from cronsentinel.db import system_session
    from cronsentinel.main import app
    prefix, raw = generate_api_key()
    with system_session() as s:
        s.execute(text("INSERT INTO api_keys (org_id, name, prefix, key_hash, role) VALUES (:o,'gaps',:p,:h,'viewer')"),
                  {"o": org["id"], "p": prefix, "h": hash_secret(raw)})
    c = TestClient(app, raise_server_exceptions=False)
    c.headers["X-API-Key"] = raw
    return c


def test_gaps_are_global_but_unobserved_count_is_per_tenant(api_client, make_job, org):
    from cronsentinel.db import system_session
    import uuid
    other = uuid.uuid4()
    with system_session() as s:
        s.execute(text("DELETE FROM monitoring_gaps"))
        s.execute(text("INSERT INTO monitoring_gaps (service, started_at, ended_at, slots_unobserved) VALUES ('schedule-generator', now() - interval '2 hours', now() - interval '1 hour', 3)"))
        s.execute(text("INSERT INTO organizations (id, name, slug) VALUES (:i, :n, :n)"), {"i": other, "n": f"it-{other.hex[:8]}"})
        j = make_job()
        for o, jid in ((org["id"], j), (other, j)):
            s.execute(text("""INSERT INTO expected_runs (org_id, job_id, scheduled_for, grace_until, deadline, state, settled_at)
                VALUES (:o, :j, :t, :t, :t, 'unobserved', now())"""), {"o": o, "j": jid, "t": datetime.now(UTC) - timedelta(minutes=90)})
    try:
        r = api_client.get("/api/v1/platform/monitoring-gaps")
        assert r.status_code == 200, r.text
        body = r.json()
        assert [g["service"] for g in body["gaps"]] == ["schedule-generator"]
        assert body["gaps"][0]["slots_unobserved"] == 3
        assert body["unobserved_slots_30d"] == 1          # the other tenant's row is invisible under RLS
    finally:
        with system_session() as s:
            s.execute(text("DELETE FROM expected_runs WHERE org_id=:o"), {"o": other})
            s.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": other})
