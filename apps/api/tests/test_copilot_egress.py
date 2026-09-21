"""R16 — Copilot data boundary: which endpoints count as internal, and what pseudonymisation does."""
import pytest

from cronsentinel.config import settings
from cronsentinel.copilot import egress


@pytest.mark.parametrize("url", [
    "http://vllm:8000", "http://ollama:11434", "http://localhost:8000", "http://127.0.0.1:8000",
    "http://10.2.3.4:8000", "http://172.20.0.5", "http://192.168.1.9", "http://[::1]:8000", "http://[fd00::1]",
    "http://vllm.ai.svc:8000", "http://vllm.ai.svc.cluster.local", "http://llm.corp.internal", "http://gpu-box.lan",
])
def test_internal_endpoints(url):
    assert egress.is_internal(url)


@pytest.mark.parametrize("url", [
    "https://api.openai.com", "https://api.anthropic.com", "https://llm.example.com",
    "http://8.8.8.8", "http://169.254.169.254",       # cloud metadata: link-local, never "internal"
    "http://svc.evil.com",                            # suffix match must be on a label boundary
    "", "not-a-url",
])
def test_external_endpoints(url):
    assert not egress.is_internal(url)


def test_external_refused_by_default(monkeypatch):
    monkeypatch.setattr(settings, "copilot_base_url", "https://api.openai.com")
    monkeypatch.setattr(settings, "copilot_allow_external", False)
    with pytest.raises(egress.EgressRefused):
        egress.check()


def test_external_allowed_signals_pseudonymise(monkeypatch):
    monkeypatch.setattr(settings, "copilot_base_url", "https://api.openai.com")
    monkeypatch.setattr(settings, "copilot_allow_external", True)
    assert egress.check() is True


def test_internal_needs_no_pseudonymisation(monkeypatch):
    monkeypatch.setattr(settings, "copilot_base_url", "http://vllm:8000")
    monkeypatch.setattr(settings, "copilot_allow_external", False)
    assert egress.check() is False


def test_pseudonymiser_hides_topology_everywhere_and_round_trips():
    ctx = {"jobs": [{"name": "backup", "host": "prod-db-01",
                     "executions": [{"host": "prod-db-01", "pod": "backup-7f9c", "node": "ip-10-0-3-7", "failure_reason": "connect to 10.0.3.7:5432 refused"}],
                     "stderr_tails": [{"text": "could not reach prod-db-01 from backup-7f9c"}]}],
           "incident": {"signals": [{"host": "prod-db-01", "infra": ["disk 91%"]}]}}
    ps = egress.Pseudonymiser()
    out = ps.apply(ctx)
    flat = repr(out)
    for real in ("prod-db-01", "backup-7f9c", "ip-10-0-3-7", "10.0.3.7"):
        assert real not in flat, f"{real} leaked into the outbound context"
    # Same host → same token, so the model can still correlate across jobs.
    assert out["jobs"][0]["host"] == out["jobs"][0]["executions"][0]["host"] == out["incident"]["signals"][0]["host"]
    # Non-topology content survives.
    assert out["jobs"][0]["name"] == "backup" and "disk 91%" in flat

    answer = {"root_cause": f"{out['jobs'][0]['host']} ran out of connections", "affected_resources": [out["jobs"][0]["host"]]}
    back = ps.restore(answer)
    assert back == {"root_cause": "prod-db-01 ran out of connections", "affected_resources": ["prod-db-01"]}


def test_restore_is_noop_without_mappings():
    assert egress.Pseudonymiser().restore({"a": "host-1"}) == {"a": "host-1"}
