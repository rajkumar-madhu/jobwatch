"""Envelope-ish symmetric encryption for channel/integration configs. Key from SECRET_ENCRYPTION_KEY.
TODO Phase 6: per-org data keys wrapped by KMS."""
import base64, hashlib, json

from cryptography.fernet import Fernet

from .config import settings

_f = Fernet(base64.urlsafe_b64encode(hashlib.sha256(settings.secret_encryption_key.encode()).digest()))


def encrypt_json(d: dict) -> bytes:
    return _f.encrypt(json.dumps(d).encode())


def decrypt_json(b: bytes) -> dict:
    return json.loads(_f.decrypt(bytes(b)).decode())
