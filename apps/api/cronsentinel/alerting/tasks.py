"""Celery notification tasks. One task per (channel, event). Per-channel rate limit + ledger."""
import hashlib, hmac, json, smtplib
from datetime import datetime, timezone
from email.message import EmailMessage

import httpx
import redis
import structlog
from sqlalchemy import text

from ..celery_app import celery
from ..config import settings
from ..crypto import decrypt_json
from ..db import system_session

log = structlog.get_logger()
_r = redis.Redis.from_url(settings.redis_url, decode_responses=True)

_EMOJI = {"failed": "✕", "missed": "⚠", "late": "⚠", "timeout": "⏱", "recovered": "✓", "flapping": "↕"}


def _fmt(p: dict) -> tuple[str, str]:
    icon = _EMOJI.get(p["condition"], "•")
    title = f"{icon} [{p['severity'].upper()}] {p['job_name']}: {p['new_status']}"
    body = (f"Job: {p['job_name']}\nStatus: {p['prev_status']} → {p['new_status']}\nCondition: {p['condition']}\n"
            f"Time: {p['ts']}\nIncident: {p.get('incident_id') or '-'}\nExecution: {p.get('execution_id') or '-'}")
    return title, body


def _send_email(cfg, title, body):
    msg = EmailMessage(); msg["Subject"] = title; msg["From"] = cfg["from"]; msg["To"] = ", ".join(cfg["to"]); msg.set_content(body)
    with smtplib.SMTP(cfg["host"], int(cfg.get("port", 587)), timeout=15) as smtp:
        if cfg.get("starttls", True): smtp.starttls()
        if cfg.get("username"): smtp.login(cfg["username"], cfg["password"])
        smtp.send_message(msg)


def _send_slack(cfg, title, body, p):
    color = {"critical": "#dc2626", "high": "#ea580c", "medium": "#d97706", "low": "#2563eb"}[p["severity"]]
    if p["condition"] == "recovered": color = "#16a34a"
    httpx.post(cfg["webhook_url"], json={"text": title, "attachments": [{"color": color, "text": body}]}, timeout=10).raise_for_status()


def _send_teams(cfg, title, body, p):
    httpx.post(cfg["webhook_url"], json={"@type": "MessageCard", "@context": "http://schema.org/extensions", "summary": title,
                                          "themeColor": "dc2626" if p["new_status"] in ("failed", "missed", "timeout") else "16a34a",
                                          "title": title, "text": body.replace("\n", "<br>")}, timeout=10).raise_for_status()


def _send_discord(cfg, title, body, p):
    httpx.post(cfg["webhook_url"], json={"content": f"**{title}**\n```{body}```"}, timeout=10).raise_for_status()


def _send_telegram(cfg, title, body, p):
    httpx.post(f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage",
               json={"chat_id": cfg["chat_id"], "text": f"{title}\n{body}"}, timeout=10).raise_for_status()


def _send_webhook(cfg, title, body, p):
    raw = json.dumps({"event": "job.status.changed", "title": title, **p}).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "JobWatch/0.1"}
    if cfg.get("secret"):
        headers["X-WeCrew JobWatch-Signature"] = "sha256=" + hmac.new(cfg["secret"].encode(), raw, hashlib.sha256).hexdigest()
    httpx.post(cfg["url"], content=raw, headers=headers, timeout=10).raise_for_status()


_SENDERS = {"email": lambda c, t, b, p: _send_email(c, t, b), "slack": _send_slack, "teams": _send_teams,
            "discord": _send_discord, "telegram": _send_telegram, "webhook": _send_webhook}
# TODO Phase 6: pagerduty (Events API v2), opsgenie, sms (provider TBD)


@celery.task(bind=True, max_retries=5, default_retry_delay=30)
def send_notification(self, org_id: str, channel_id: str, dedup_key: str, incident_id: str | None, payload: dict):
    with system_session() as s:
        ch = s.execute(text("SELECT kind, config_enc, rate_per_min, enabled FROM notification_channels WHERE id=:id AND org_id=:o"),
                       {"id": channel_id, "o": org_id}).first()
        if not ch or not ch.enabled:
            return
        # per-channel rate limit (D10)
        k = f"chrl:{channel_id}:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}"
        n = _r.incr(k); _r.expire(k, 120)
        if n > ch.rate_per_min:
            s.execute(text("INSERT INTO notification_ledger (org_id, incident_id, channel_id, dedup_key, status, error) VALUES (:o, :i, :c, :k, 'rate_limited', NULL)"),
                      {"o": org_id, "i": incident_id, "c": channel_id, "k": dedup_key})
            return
        cfg = decrypt_json(ch.config_enc)
        title, body = _fmt(payload)
        try:
            _SENDERS[ch.kind](cfg, title, body, payload)
            status, err = "sent", None
        except Exception as e:
            status, err = "failed", str(e)[:500]
        s.execute(text("INSERT INTO notification_ledger (org_id, incident_id, channel_id, dedup_key, status, error) VALUES (:o, :i, :c, :k, :st, :e)"),
                  {"o": org_id, "i": incident_id, "c": channel_id, "k": dedup_key, "st": status, "e": err})
        if incident_id:
            s.execute(text("UPDATE incidents SET last_notified_at=now() WHERE id=:i"), {"i": incident_id})
            s.execute(text("INSERT INTO incident_events (org_id, incident_id, kind, payload) VALUES (:o, :i, 'notified', :p::jsonb)"),
                      {"o": org_id, "i": incident_id, "p": json.dumps({"channel_id": channel_id, "kind": ch.kind, "status": status})})
    if status == "failed":
        raise self.retry(exc=RuntimeError(err))
