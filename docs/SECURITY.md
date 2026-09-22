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

## R8 — key separation, CSRF, fail-fast broker

### Purpose-derived keys (`cronsentinel/keys.py`)
One secret was signing session cookies *and* encrypting channel/destination configs. HKDF-SHA256
over `SECRET_ENCRYPTION_KEY` now yields a distinct subkey per purpose (`session-cookie`,
`csrf-token`, `config-encryption`). A token forged with the session key no longer validates as a
CSRF token — `test_csrf_token_signed_with_the_session_key_is_rejected` is exactly that check, and
it only passes because the keys differ.

**Upgrade step:** config ciphertext written before R8 used `sha256(SECRET_ENCRYPTION_KEY)` and will
not decrypt. Run `python scripts/reencrypt_configs.py` (idempotent, `--dry-run` supported) with the
API stopped, before deploying R8. Existing session cookies are invalidated — users sign in again.

### CSRF (`cronsentinel/csrf.py`)
SameSite=Lax is a browser-side default with gaps: embedded webviews, relaxed settings, and — the
real one — a **same-site subdomain** attacker, for whom the cookie is not cross-site at all.
`/auth/session` now returns a `csrf_token` signed with its own subkey and bound to the session's
`sub`; cookie-authenticated writes must echo it in `X-CSRF-Token`. Enforced centrally in
`current_principal`, plus explicitly on `/auth/orgs` and `/auth/switch`, which bypass it. API-key
and bearer requests are exempt — browsers do not attach those automatically, so requiring a token
would break every script without adding safety. The SPA keeps the token **in memory only**
(localStorage would hand it to the XSS the token exists to contain) and refreshes once on a 403.

### Fail-fast on an unreachable broker
`events.connect()` defaulted to `max_reconnect_attempts=-1`: a typo'd `NATS_URL` left a worker
parked inside `await connect()` — Ready, silent, processing nothing. Now bounded (30 attempts × 2s)
so the supervisor restarts it visibly, with connection callbacks feeding `is_connected()`.
`/readyz` previously reported `js is not None`, which stayed true after the broker vanished; it now
returns 503 when the connection is actually down, so traffic is shed instead of dropped.

### Also fixed
`audit()` wrote `request.client.host` into an `inet` column. Any non-address value — a hostname, a
unix socket, `testclient` — raised `InvalidTextRepresentation` and failed the **entire write** the
audit call was attached to, so a logging concern could 500 a job creation. Non-addresses are now
stored as NULL. The R4 integrations router also called `audit()` with the wrong signature, which
would have 500'd every signal-destination write.

## R10 — RBAC matrix

Role ladder: `viewer < developer < sre < devops < admin < owner`.

`tests/integration/test_rbac_matrix.py` asserts the full grid for all 32 gated write endpoints:
every role **below** the threshold gets 403, and the lowest permitted role is **not** refused. The
required role is read off the live dependency graph, so an endpoint that ships without
`require_role(...)` fails `test_roles_below_the_threshold_are_refused` rather than passing
silently. Two endpoints are ungated on purpose and listed with reasons (`/billing/webhook`, which
is authenticated by Stripe signature; `/jobs/schedule/preview`, a pure cron utility).

Also asserted: the role comes from the key, not from a request header; a key cannot touch another
tenant's objects (404 via RLS, never 200); a revoked key is 401.

### Behaviour change
`POST /api/v1/copilot/ask` had **no** role gate, so a viewer could spend LLM budget and plan usage
on every question. It now requires `developer`. `/copilot/history` and `/copilot/suggestions`
remain readable by viewers. If you want viewers to be able to ask, lower this deliberately rather
than by omission.

### Two more 500s
`DELETE /api/v1/api-keys/{key_id}` and `POST /auth/switch/{org_id}` typed their path parameter as
`str`, so a malformed id reached Postgres and raised `invalid input syntax for type uuid` → 500.
Both are now `UUID`, giving a 422. (`execution_id` stays `str` — those are agent-minted ULIDs.)

### Still open
- Depth, not breadth: the matrix proves who may call what, not that each handler's business rules
  are right.
- `PATCH /api/v1/jobs/{id}` cannot change a job's `name` — not a bug, but there is no rename API.
- Role changes do not invalidate an existing CSRF token or session until TTL.


## Outbound requests to tenant-supplied URLs (R18)

Webhook channels, Slack/Teams/Discord incoming-webhook URLs and signal destinations are fetched from
inside the cluster. Every such request goes through `cronsentinel/netguard.py`:

- **Connect-time address check.** The guard resolves the name itself, drops any non-public address,
  and connects to a validated address — so a name that resolves publicly when saved and to
  `169.254.169.254` when sent (DNS rebinding) is still blocked. TLS verifies against the URL's
  hostname, not the IP (tested with a local CA: a wrong name on the same IP fails verification).
- **Blocked ranges** (SaaS mode): private, loopback, link-local (cloud metadata), multicast,
  reserved, unspecified, CGNAT `100.64.0.0/10`, and IPv4-mapped IPv6 forms of all of these.
- **No redirects, no proxy env.** A public URL answering `302 → 169.254.169.254` is not followed;
  `HTTP(S)_PROXY` is ignored so the proxy is not validated in place of the target.
- **Forbidden headers.** Tenants cannot set `Host`, framing headers, or the headers cloud metadata
  services require (`Metadata-Flavor`, `Metadata`, `X-aws-ec2-metadata-token*`) — the difference
  between a blind SSRF and a credential leak. Checked at save time *and* send time, because rows
  saved before R18 were never validated.
- **No oracle.** "Send test", the delivery log, `disabled_reason` and the notification ledger
  store a category (`destination not allowed`, `timed out`, `connection failed`,
  `HTTP 503 from destination`) — never the raw exception, which named internal addresses and told
  refused from timeout apart, i.e. a port scanner run from the API pod.

`OUTBOUND_ALLOW_PRIVATE` switches off the address check only (scheme, redirect and header rules
stay). **Helm default `false`** (multi-tenant). **Compose default `true`** (single-tenant
self-hosted, where internal webhooks are normal). A multi-tenant deploy must never set it true.

Known limits: the guard uses `httpcore.ConnectionPool._network_backend`, a private attribute
(httpcore 1.0.x) — `tests/test_netguard.py::test_guard_hook_is_actually_installed` fails if a
dependency upgrade stops honouring it, rather than the guard silently becoming a no-op. NAT64
(`64:ff9b::/96`) embeddings are not unwrapped. Telegram is a fixed host and not routed through the
guard. Email uses the operator's SMTP settings, not tenant input.


## Row-level security roles (R21)

- `jobwatch_app` — the login role. Tenant sessions set `app.org_id`; the only policy that applies
  is `org_id = app_org_id()`.
- `jobwatch_system` — NOLOGIN. System sessions (workers, cross-tenant lookups) `SET LOCAL ROLE
  jobwatch_system`, which has `USING (true)` on every tenant table.
- `jobwatch_app` is a member of `jobwatch_system` **WITH INHERIT FALSE, SET TRUE**. A policy `TO`
  a role applies to its members; with INHERIT TRUE, the `true` policy would apply to every tenant
  query. `tests/integration/test_rls_system_role.py` asserts this, and was mutation-checked:
  flipping to INHERIT TRUE made tenants read and write other tenants' rows, and 6 tests failed.
- `SET LOCAL ROLE` ends with the transaction, so pooled connections return to `jobwatch_app`
  (tested).
- Requires PostgreSQL 16.

## Machine secrets (R21)

API keys and agent keys are 256-bit random tokens hashed with SHA-256 (`sha256$…`). Argon2 is
deliberately not used: it protects low-entropy passwords, and at ~180 ms per verify on every
request it throttled ingest and gave anyone with a key prefix a cheap CPU-exhaustion lever.
Hashes are unpeppered so rotating `SECRET_ENCRYPTION_KEY` does not revoke every key. Pre-R21 argon2
hashes still verify and are rewritten on first successful use. There are no user passwords
(Keycloak handles users); if one is ever introduced it must use argon2.


## SECURITY DEFINER functions and FORCE RLS (R24)

A definer function runs as its owner, and `FORCE ROW LEVEL SECURITY` applies RLS to the owner —
unless the owner is a superuser. Every environment this project is tested in (sandbox, CI, default
compose) has a superuser owner, so a wrongly-owned definer function works there and **silently
does nothing** on managed Postgres, where the owner usually is not a superuser. Demonstrated with a
non-superuser owner on a scratch table: a definer purge deleted 0 of 5 rows and returned success;
owned by `jobwatch_system`, 5 of 5.

That applied to `purge_org()` and `purge_job()` (tenant offboarding, job-delete cleanup) and the
status-page prune trigger, all from R11. Migration 0013 re-owns every DML-only definer function to
`jobwatch_system`. DDL functions (`ensure_month_partition`, `drop_partition_if_empty`) keep the
table owner — `DROP` needs ownership, and they work on partitions directly, where the parent's RLS
does not apply. `test_dml_definer_functions_are_owned_by_the_system_role` checks the ownership
invariant for every current and future definer function, since the behaviour itself cannot be
observed with a superuser owner.

**Recommendation not implemented:** run CI's migrations as a non-superuser owner so this class of
bug fails a test instead of relying on a structural check.
