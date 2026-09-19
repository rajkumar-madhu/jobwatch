"""Pure-logic checks for correlation matching (DB-free via fakes)."""
from types import SimpleNamespace

from cronsentinel.alerting import correlation as C


class FakeSession:
    def __init__(self, incidents): self.incidents = incidents
    def execute(self, q, params=None):
        return SimpleNamespace(all=lambda: self.incidents)


def test_matches_same_host():
    inc = [SimpleNamespace(id="i1", affected_job_ids=["a"], correlation_signals=[{"host": "prod-db-01", "job_id": "a"}], started_at=None)]
    iid, why = C.find_related_incident(FakeSession(inc), "o", "b", {"host": "prod-db-01", "upstream": [], "downstream": []})
    assert iid == "i1" and "host" in why


def test_matches_dependency():
    inc = [SimpleNamespace(id="i2", affected_job_ids=["a"], correlation_signals=[{"host": "x"}], started_at=None)]
    iid, why = C.find_related_incident(FakeSession(inc), "o", "b", {"host": "y", "upstream": ["a"], "downstream": []})
    assert iid == "i2" and "upstream" in why


def test_no_match():
    inc = [SimpleNamespace(id="i3", affected_job_ids=["a"], correlation_signals=[{"host": "x"}], started_at=None)]
    assert C.find_related_incident(FakeSession(inc), "o", "b", {"host": "y", "upstream": [], "downstream": []}) == (None, None)
