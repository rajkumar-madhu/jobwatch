from cronsentinel.redaction import redact


def test_redacts_kv_and_urls():
    out = redact("password=hunter2 postgresql://u:secret@db/x token: abc")
    assert "hunter2" not in out and "secret@" not in out


def test_keeps_plain_text():
    assert redact("Processing 1200 rows done") == "Processing 1200 rows done"
