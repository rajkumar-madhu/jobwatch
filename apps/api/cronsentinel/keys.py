"""Purpose-separated key derivation (R8).

One secret was doing three jobs: encrypting channel/destination configs at rest, signing session
cookies, and (via the same string) anything else that needed a key. That is a real weakness —
a signing oracle or a leak in one path compromises the others, and rotating the secret for one
purpose silently invalidates the others.

HKDF-SHA256 over the root secret with a per-purpose `info` label. Deriving is cheap and
deterministic, so nothing needs to be stored: the same root secret yields the same subkeys, but a
subkey reveals nothing about the root or its siblings.
"""
import base64
import functools

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .config import settings

SALT = b"jobwatch/v1"


@functools.lru_cache(maxsize=8)
def derive(purpose: str, length: int = 32) -> bytes:
    """Subkey for `purpose`. Purposes must be stable strings — changing one rotates that key and
    invalidates everything it protected (sessions log out; encrypted configs become unreadable)."""
    if not settings.secret_encryption_key:
        raise RuntimeError("SECRET_ENCRYPTION_KEY is not set")
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=SALT,
                info=f"jobwatch:{purpose}".encode()).derive(settings.secret_encryption_key.encode())


def fernet_key(purpose: str = "config-encryption") -> bytes:
    return base64.urlsafe_b64encode(derive(purpose))


def signing_key(purpose: str) -> str:
    return base64.urlsafe_b64encode(derive(purpose)).decode()
