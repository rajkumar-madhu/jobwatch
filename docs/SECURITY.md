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

## R7 — id_token verification and webhook signatures

### OIDC (`cronsentinel/oidc.py`)
The Phase-1 callback used `jwt.get_unverified_claims(id_token)` with a TODO. TLS to the token
endpoint is not a substitute: without verification, anything the endpoint returns — a token from a
second realm on the same host, a token minted for a different client, an expired one, or one
replayed from an earlier login — became a session.

`verify_id_token` now checks, in order: **alg allow-list** (RS256/RS512/ES256 — never `none`, never
HS* so the client secret cannot be abused as an HMAC key), **signature** against the JWKS key
matching `kid`, **iss**, **aud** (plus `azp` when the token has several audiences), **exp/iat**
with 60s leeway, and **nonce**.

`/auth/login` now issues a `nonce` alongside `state` and stores both under the state key; the
callback deletes the key first (state is single-use, so a replayed callback 400s) and requires the
id_token's nonce to match. A verification failure triggers **one** JWKS refetch before rejecting,
because a rotated signing key is indistinguishable from a bad signature on the cached set. A token
with no `email` claim now 401s with a clear message instead of a 500.

### Stripe webhook (`routers/billing.py`)
`_verify` parsed the signature header into a dict, which kept only the **last** `v1=`. Stripe sends
several `v1=` values while a webhook secret is being rotated, in no guaranteed order, so roughly
half of all events would have been rejected mid-rotation. It now accepts any matching `v1`, still
enforces the 300s timestamp window (a captured webhook is not replayable), and compares with
`hmac.compare_digest`.

A malformed event (`checkout.session.completed` with no `plan`/`org_id` metadata) previously raised
`KeyError` → 500. It now 400s, and because the `stripe_events` insert is in the same transaction,
the event id is **not** recorded — so a corrected retry is still processed rather than deduped away.

### Still open
- Session cookie JWT is signed with `SECRET_ENCRYPTION_KEY`, the same key used to encrypt channel
  and destination configs. Separate purposes should use separate derived keys.
- No CSRF token on cookie-authenticated POSTs (SameSite=Lax only).
- Refresh tokens are not stored; sessions simply expire at `SESSION_TTL_S`.
- Keycloak itself is still unverified against a live server — the flow is proven against a fake
  provider that implements discovery, JWKS and the token endpoint.
