# Security

- **Tenant isolation**: `org_id` on every tenant table, PostgreSQL RLS forced, API sets `app.org_id` per request; `tests/test_tenant_isolation.py` asserts cross-tenant reads return nothing.
- **RBAC**: owner > admin > devops > sre > developer > viewer. Writes ≥ developer; alerting/agents/integrations ≥ devops; keys/members/billing/status pages ≥ admin. Enforced by `require_role()` dependencies.
- **Credentials**: API keys and agent keys argon2-hashed, shown once, revocable; channel/integration configs Fernet-encrypted (`crypto.py`), never returned by API; Stripe webhooks HMAC-verified with replay window.
- **Sessions**: httpOnly, SameSite=Lax, Secure in prod; OAuth `state` in Redis (10 min).
- **Redaction**: every log tail passes `redaction.redact()` (key/token/password patterns, connection strings, private keys, high-entropy blobs) before storage and again before the Copilot prompt.
- **Agents**: read-only discovery, env var *names* only (allow-listed), TLS 1.2+, hardened systemd unit / distroless non-root pod, read-only K8s RBAC.
- **Rate limiting**: Redis sliding window per API key / user / heartbeat token / agent / IP (enroll).
- **Audit**: all mutating API calls write `audit_logs` (actor, action, target, IP).
- **Web**: CSP/X-Frame-Options/nosniff headers, CORS pinned to `WEB_PUBLIC_URL`.
- **Copilot**: read-only; commands are suggestions rendered as text; model endpoint self-hosted by default.

Open items: id_token JWKS signature verification in `/auth/callback`; per-org KMS-wrapped data keys; CSRF token for cookie-auth POSTs from third-party origins (currently relies on SameSite=Lax + CORS).
