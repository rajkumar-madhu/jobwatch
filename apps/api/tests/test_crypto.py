from cronsentinel.crypto import decrypt_json, encrypt_json


def test_roundtrip():
    d = {"webhook_url": "https://hooks.slack.com/x", "n": 1}
    enc = encrypt_json(d)
    assert b"hooks.slack" not in enc
    assert decrypt_json(enc) == d
