from cronsentinel.copilot.llm import _parse


def test_parses_fenced_json():
    d = _parse('```json\n{"summary":"db timeout","root_cause":"pool exhausted","evidence":["12 timeouts"],"confidence":0.8}\n```')
    assert d["root_cause"] == "pool exhausted" and d["confidence"] == 0.8 and d["remediation"] == []


def test_handles_garbage():
    d = _parse("I think it's the network.")
    assert d["confidence"] <= 0.3 and "root_cause" in d
