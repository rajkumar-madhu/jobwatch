"""Symmetric encryption for channel/integration configs. Key derived from SECRET_ENCRYPTION_KEY
for the 'config-encryption' purpose only (R8). TODO Phase 6: per-org data keys wrapped by KMS."""
import json

from cryptography.fernet import Fernet

from .keys import fernet_key

# R8: purpose-derived subkey, not the raw root secret — see cronsentinel/keys.py.
# NOTE: this changes the key for existing ciphertext. Rows encrypted before R8 must be re-encrypted
# with scripts/reencrypt_configs.py or they will fail to decrypt.
_f = Fernet(fernet_key("config-encryption"))


def encrypt_json(d: dict) -> bytes:
    return _f.encrypt(json.dumps(d).encode())


def decrypt_json(b: bytes) -> dict:
    return json.loads(_f.decrypt(bytes(b)).decode())
