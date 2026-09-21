"""R21 — machine secrets: fast verify, legacy argon2 still accepted."""
import time

from argon2 import PasswordHasher

from cronsentinel.auth import generate_api_key, hash_secret, needs_rehash, verify_secret


def test_new_hashes_are_fast_and_verify():
    _, raw = generate_api_key()
    h = hash_secret(raw)
    assert h.startswith("sha256$") and not needs_rehash(h)
    assert verify_secret(raw, h)
    assert not verify_secret(raw + "x", h)
    t = time.perf_counter()
    for _ in range(1000):
        verify_secret(raw, h)
    assert (time.perf_counter() - t) < 0.5, "1,000 verifications should take milliseconds, not seconds"


def test_legacy_argon2_hashes_still_verify_and_are_flagged_for_upgrade():
    _, raw = generate_api_key()
    legacy = PasswordHasher().hash(raw)
    assert verify_secret(raw, legacy) and needs_rehash(legacy)
    assert not verify_secret("wrong", legacy)


def test_garbage_stored_hash_fails_closed():
    assert not verify_secret("anything", "not-a-hash")
    assert not verify_secret("anything", "")
