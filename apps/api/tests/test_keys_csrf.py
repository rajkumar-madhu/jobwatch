"""R8 — key separation and CSRF token semantics (pure)."""
import pytest

from cronsentinel import csrf
from cronsentinel.keys import derive, fernet_key, signing_key


def test_purposes_derive_different_keys():
    assert derive("session-cookie") != derive("config-encryption") != derive("csrf-token")
    assert derive("session-cookie") != derive("csrf-token")


def test_derivation_is_deterministic():
    assert derive("session-cookie") == derive("session-cookie")


def test_subkey_is_not_the_root_secret():
    from cronsentinel.config import settings
    assert settings.secret_encryption_key.encode() not in derive("config-encryption")


def test_fernet_key_is_valid_length():
    from cryptography.fernet import Fernet
    Fernet(fernet_key())   # raises if not 32 url-safe base64 bytes


def test_session_key_differs_from_csrf_key():
    assert signing_key("session-cookie") != signing_key("csrf-token")


def test_csrf_token_roundtrips():
    t = csrf.issue("user-sub-1")
    assert csrf.valid(t, "user-sub-1")


def test_csrf_token_is_bound_to_its_subject():
    """A token lifted from another session must not validate."""
    assert not csrf.valid(csrf.issue("user-a"), "user-b")


def test_missing_or_garbage_csrf_is_invalid():
    assert not csrf.valid(None, "s")
    assert not csrf.valid("not-a-jwt", "s")


def test_csrf_token_signed_with_the_session_key_is_rejected():
    """Key separation is what makes this fail — with one shared secret it would pass."""
    from jose import jwt
    forged = jwt.encode({"sub": "s", "exp": 9999999999}, signing_key("session-cookie"), algorithm="HS256")
    assert not csrf.valid(forged, "s")


def test_enforce_skips_safe_methods():
    class R:
        method = "GET"
        headers: dict = {}
    csrf.enforce(R(), "s")   # no raise


def test_enforce_rejects_write_without_token():
    from fastapi import HTTPException

    class R:
        method = "POST"
        headers: dict = {}
    with pytest.raises(HTTPException) as e:
        csrf.enforce(R(), "s")
    assert e.value.status_code == 403
