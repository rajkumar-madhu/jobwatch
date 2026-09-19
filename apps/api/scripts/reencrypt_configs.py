#!/usr/bin/env python
"""One-shot migration for R8 key separation.

Before R8, channel and signal-destination configs were encrypted with sha256(SECRET_ENCRYPTION_KEY).
R8 derives a purpose-scoped subkey instead, so existing ciphertext no longer decrypts. Run this
once, with the API stopped, before deploying R8:

    DATABASE_URL=... SECRET_ENCRYPTION_KEY=... python scripts/reencrypt_configs.py [--dry-run]

Idempotent: rows that already decrypt with the new key are left alone.
"""
import base64
import hashlib
import sys

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text

from cronsentinel.config import settings
from cronsentinel.db import system_session
from cronsentinel.keys import fernet_key

OLD = Fernet(base64.urlsafe_b64encode(hashlib.sha256(settings.secret_encryption_key.encode()).digest()))
NEW = Fernet(fernet_key("config-encryption"))
TABLES = [("notification_channels", "config_enc"), ("signal_destinations", "config_enc")]


def main(dry: bool) -> int:
    moved = skipped = failed = 0
    with system_session() as s:
        for table, col in TABLES:
            try:
                rows = s.execute(text(f"SELECT id, {col} FROM {table}")).all()
            except Exception as e:
                print(f"skip {table}: {e}")
                continue
            for r in rows:
                blob = getattr(r, col)
                if blob is None:
                    continue
                try:
                    NEW.decrypt(blob); skipped += 1; continue   # already migrated
                except InvalidToken:
                    pass
                try:
                    plain = OLD.decrypt(blob)
                except InvalidToken:
                    print(f"FAILED {table}.{r.id}: decrypts with neither key"); failed += 1; continue
                if not dry:
                    s.execute(text(f"UPDATE {table} SET {col} = :c WHERE id = :i"), {"c": NEW.encrypt(plain), "i": r.id})
                moved += 1
    print(f"{'would re-encrypt' if dry else 're-encrypted'}: {moved}, already new: {skipped}, failed: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main("--dry-run" in sys.argv))
