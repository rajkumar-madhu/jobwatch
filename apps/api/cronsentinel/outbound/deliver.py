"""Delivery of signals to tenant-configured destinations. Celery task, retried with backoff; a
destination that fails 20 times in a row is auto-disabled with a reason so a dead AEGIS endpoint
does not burn the queue forever."""
import asyncio
import hashlib
import hmac
import json

import httpx
import nats

from .. import netguard
import structlog
from sqlalchemy import text

from ..celery_app import celery
from ..crypto import decrypt_json
from ..db import system_session

log = structlog.get_logger()
MAX_ATTEMPTS = 6
AUTO_DISABLE_AFTER = 20
UA = "JobWatch-Signals/1"
NATS_CONNECT_TIMEOUT_S = 5


def sign(secret: str, raw: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def _post_webhook(cfg: dict, raw: bytes, signal_id: str) -> int:
    headers = {"Content-Type": "application/json", "User-Agent": UA, "X-JobWatch-Signal-Id": signal_id,
               "X-JobWatch-Schema": "jobwatch.signal/1"}
    if cfg.get("secret"):
        headers["X-JobWatch-Signature"] = sign(cfg["secret"], raw)
    for k, v in (cfg.get("headers") or {}).items():
        headers[k] = v
    # R18: through the SSRF guard. Custom headers are re-checked here, not only at save time,
    # because destinations saved before R18 were never validated.
    r = netguard.post(cfg["url"], content=raw, headers=headers, timeout=10)
    return r.status_code


async def _publish_nats_async(cfg: dict, raw: bytes, signal_id: str, event_type: str) -> None:
    # R18-equivalent for this destination kind: resolve and validate the host at connect time
    # (netguard.resolve_nats_host), not only when the destination was saved — a destination saved
    # before this could point anywhere, and DNS can change under a name that passed check_nats_url
    # at save time. subject_prefix was already validated at save time (see integrations.py).
    connect_url = netguard.resolve_nats_host(cfg["url"])
    kw = {"servers": [connect_url], "connect_timeout": NATS_CONNECT_TIMEOUT_S, "max_reconnect_attempts": 0,
          "allow_reconnect": False}
    if cfg.get("token"):
        kw["token"] = cfg["token"]
    nc = await nats.connect(**kw)
    try:
        subject = f"{cfg['subject_prefix']}.{event_type}"
        await nc.publish(subject, raw, headers={"Nats-Msg-Id": signal_id, "X-JobWatch-Schema": "jobwatch.signal/1"})
        await nc.flush(timeout=NATS_CONNECT_TIMEOUT_S)  # confirms the server accepted it, not that a consumer got it
    finally:
        await nc.close()


def _publish_nats(cfg: dict, raw: bytes, signal_id: str, event_type: str) -> None:
    """R30: was `raise RuntimeError("nats destination not implemented")` since R4 — every tenant
    with a nats destination has had every delivery fail, retry to exhaustion, and auto-disable.
    One short-lived connection per delivery, same shape as _post_webhook's one POST per delivery:
    no persistent async client to keep alive alongside the sync Celery worker pool."""
    asyncio.run(_publish_nats_async(cfg, raw, signal_id, event_type))


@celery.task(bind=True, max_retries=MAX_ATTEMPTS, autoretry_for=(Exception,), retry_backoff=5, retry_backoff_max=600, retry_jitter=True)
def deliver_signal(self, org_id: str, destination_id: str, envelope: dict):
    raw = json.dumps(envelope, separators=(",", ":"), default=str).encode()
    sid, et = envelope["signal_id"], envelope["event_type"]
    with system_session() as s:
        dest = s.execute(text("SELECT kind::text, config_enc, enabled FROM signal_destinations WHERE id=:id AND org_id=:o"),
                         {"id": destination_id, "o": org_id}).first()
        if not dest or not dest.enabled:
            return
        s.execute(text("""INSERT INTO signal_deliveries (org_id, destination_id, signal_id, event_type, attempts)
            VALUES (:o, :d, :sid, :et, 1) ON CONFLICT (destination_id, signal_id) DO UPDATE SET attempts = signal_deliveries.attempts + 1"""),
                  {"o": org_id, "d": destination_id, "sid": sid, "et": et})
        cfg = decrypt_json(dest.config_enc)
    try:
        if dest.kind == "webhook":
            code = _post_webhook(cfg, raw, sid)
            if code >= 400:
                raise RuntimeError(f"HTTP {code}")
        elif dest.kind == "nats":
            _publish_nats(cfg, raw, sid, et)
            code = None   # NATS core pub/sub has no response code; response_code stays NULL for this kind
        else:
            raise RuntimeError(f"unknown kind {dest.kind}")
        with system_session() as s:
            s.execute(text("UPDATE signal_deliveries SET status='sent', sent_at=now(), response_code=:c WHERE destination_id=:d AND signal_id=:sid"),
                      {"c": code, "d": destination_id, "sid": sid})
            s.execute(text("UPDATE signal_destinations SET consecutive_failures=0 WHERE id=:d"), {"d": destination_id})
    except Exception as e:
        final = self.request.retries >= MAX_ATTEMPTS
        # R18: last_error and disabled_reason are tenant-visible (/deliveries, /destinations). The raw
        # exception text named internal addresses and told refused from timeout — log it, store a category.
        log.warning("signal delivery failed", destination=destination_id, error=repr(e)[:500])
        err = netguard.tenant_error(e)
        with system_session() as s:
            s.execute(text("UPDATE signal_deliveries SET status=:st, last_error=:err WHERE destination_id=:d AND signal_id=:sid"),
                      {"st": "dead" if final else "failed", "err": err, "d": destination_id, "sid": sid})
            if final:
                row = s.execute(text("""UPDATE signal_destinations SET consecutive_failures = consecutive_failures + 1,
                    enabled = CASE WHEN consecutive_failures + 1 >= :n THEN false ELSE enabled END,
                    disabled_reason = CASE WHEN consecutive_failures + 1 >= :n THEN 'auto-disabled: ' || :err ELSE disabled_reason END
                    WHERE id=:d RETURNING enabled"""), {"n": AUTO_DISABLE_AFTER, "err": err, "d": destination_id}).first()
                if row and not row.enabled:
                    log.warning("signal destination auto-disabled", destination=destination_id)
        raise
