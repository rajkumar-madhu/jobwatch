"""Stripe-compatible subscription flow: checkout → webhook → plan on org. No Stripe SDK dependency (plain REST) so it runs without keys in dev."""
import hashlib, hmac, json, time

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import text

from ..auth import Principal, audit, current_principal, require_role
from ..config import settings
from ..db import system_session, tenant_session

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])
STRIPE = "https://api.stripe.com/v1"


def _stripe(method: str, path: str, data: dict | None = None):
    if not settings.stripe_secret_key: raise HTTPException(503, "Billing not configured (STRIPE_SECRET_KEY)")
    r = httpx.request(method, f"{STRIPE}{path}", auth=(settings.stripe_secret_key, ""), data=data, timeout=15)
    if r.status_code >= 300: raise HTTPException(502, f"stripe: {r.text[:200]}")
    return r.json()


@router.get("")
def status(p: Principal = Depends(current_principal)):
    with tenant_session(p.org_id) as s:
        sub = s.execute(text("SELECT s.plan, s.status, s.trial_ends_at, s.current_period_end, s.cancel_at_period_end, s.stripe_customer_id IS NOT NULL AS has_customer, o.plan AS effective_plan FROM subscriptions s JOIN organizations o ON o.id=s.org_id WHERE s.org_id=:o"), {"o": str(p.org_id)}).first()
        plans = [dict(r._mapping) for r in s.execute(text("SELECT plan, max_jobs, retention_days, features, monthly_usd FROM plan_limits ORDER BY COALESCE(monthly_usd, 99999)")).all()]
        usage = s.execute(text("SELECT (SELECT count(*) FROM jobs) AS jobs, (SELECT count(*) FROM executions WHERE scheduled_ts >= date_trunc('month', now())) AS executions_this_month, (SELECT COALESCE(sum(length(content)),0) FROM execution_logs) AS log_bytes")).first()
        limits = s.execute(text("SELECT pl.max_jobs, pl.retention_days FROM organizations o JOIN plan_limits pl ON pl.plan=o.plan WHERE o.id=:o"), {"o": str(p.org_id)}).first()
    return {"subscription": dict(sub._mapping) if sub else None, "plans": plans, "usage": dict(usage._mapping), "limits": dict(limits._mapping) if limits else None, "configured": bool(settings.stripe_secret_key)}


@router.post("/checkout")
def checkout(plan: str, request: Request, p: Principal = Depends(require_role("admin"))):
    with tenant_session(p.org_id) as s:
        price = s.execute(text("SELECT stripe_price_id FROM plan_limits WHERE plan=:p"), {"p": plan}).scalar()
        if not price: raise HTTPException(400, f"plan {plan} has no Stripe price configured")
        cust = s.execute(text("SELECT stripe_customer_id FROM subscriptions WHERE org_id=:o"), {"o": str(p.org_id)}).scalar()
        if not cust:
            email = s.execute(text("SELECT email FROM users WHERE id=:u"), {"u": str(p.user_id)}).scalar() if p.user_id else None
            cust = _stripe("POST", "/customers", {"metadata[org_id]": str(p.org_id), **({"email": email} if email else {})})["id"]
            s.execute(text("UPDATE subscriptions SET stripe_customer_id=:c WHERE org_id=:o"), {"c": cust, "o": str(p.org_id)})
        sess = _stripe("POST", "/checkout/sessions", {"mode": "subscription", "customer": cust, "line_items[0][price]": price, "line_items[0][quantity]": 1,
                                                       "success_url": f"{settings.web_public_url}/billing?ok=1", "cancel_url": f"{settings.web_public_url}/billing", "metadata[org_id]": str(p.org_id), "metadata[plan]": plan})
        audit(s, p, "billing.checkout", "subscription", plan, ip=request.client.host)
    return {"url": sess["url"]}


@router.post("/portal")
def portal(p: Principal = Depends(require_role("admin"))):
    with tenant_session(p.org_id) as s:
        cust = s.execute(text("SELECT stripe_customer_id FROM subscriptions WHERE org_id=:o"), {"o": str(p.org_id)}).scalar()
    if not cust: raise HTTPException(400, "no billing customer yet")
    return {"url": _stripe("POST", "/billing_portal/sessions", {"customer": cust, "return_url": f"{settings.web_public_url}/billing"})["url"]}


def _verify(payload: bytes, sig_header: str) -> bool:
    try:
        parts = dict(kv.split("=", 1) for kv in sig_header.split(","))
        expected = hmac.new(settings.stripe_webhook_secret.encode(), f"{parts['t']}.".encode() + payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, parts["v1"]) and abs(time.time() - int(parts["t"])) < 300
    except Exception:
        return False


@router.post("/webhook", include_in_schema=False)
async def webhook(request: Request, stripe_signature: str = Header(default="")):
    raw = await request.body()
    if not settings.stripe_webhook_secret or not _verify(raw, stripe_signature): raise HTTPException(400, "bad signature")
    ev = json.loads(raw); obj = ev["data"]["object"]
    with system_session() as s:
        if s.execute(text("INSERT INTO stripe_events (id, type) VALUES (:i, :t) ON CONFLICT DO NOTHING RETURNING 1"), {"i": ev["id"], "t": ev["type"]}).first() is None:
            return {"ok": True, "duplicate": True}
        t = ev["type"]
        if t == "checkout.session.completed":
            s.execute(text("UPDATE subscriptions SET stripe_subscription_id=:sid, plan=:p, status='active' WHERE org_id=:o"), {"sid": obj.get("subscription"), "p": obj["metadata"]["plan"], "o": obj["metadata"]["org_id"]})
            s.execute(text("UPDATE organizations SET plan=:p WHERE id=:o"), {"p": obj["metadata"]["plan"], "o": obj["metadata"]["org_id"]})
        elif t in ("customer.subscription.updated", "customer.subscription.deleted"):
            price = obj["items"]["data"][0]["price"]["id"] if obj.get("items") else None
            plan = s.execute(text("SELECT plan FROM plan_limits WHERE stripe_price_id=:p"), {"p": price}).scalar() if price else None
            status = obj["status"] if t.endswith("updated") else "canceled"
            eff = plan if status in ("active", "trialing") and plan else "free"
            s.execute(text("UPDATE subscriptions SET status=:st, plan=:pl, stripe_price_id=:pr, cancel_at_period_end=:c, current_period_end=to_timestamp(:pe) WHERE stripe_customer_id=:cu"),
                      {"st": status, "pl": eff, "pr": price, "c": obj.get("cancel_at_period_end", False), "pe": obj.get("current_period_end"), "cu": obj["customer"]})
            s.execute(text("UPDATE organizations o SET plan=:pl FROM subscriptions sb WHERE sb.org_id=o.id AND sb.stripe_customer_id=:cu"), {"pl": eff, "cu": obj["customer"]})
        elif t == "invoice.payment_failed":
            s.execute(text("UPDATE subscriptions SET status='past_due' WHERE stripe_customer_id=:cu"), {"cu": obj["customer"]})
            # TODO: notify org admins via notification channel
    return {"ok": True}
