"""CSRF protection for cookie-authenticated writes (R8).

SameSite=Lax alone is not enough. It blocks cross-site *cookies* on top-level POSTs in current
browsers, but it is a browser-side default with real gaps: older/embedded webviews, any browser
where the user relaxed it, and — importantly — same-site subdomain attackers, for whom the cookie
is not cross-site at all. A tenant on a subdomain, or an XSS on any `*.jobwatch` host, could
otherwise drive state-changing requests with the victim's session.

Double-submit with a signed token: `/auth/session` hands the SPA a token bound to the session's
`sub`, sent back as `X-CSRF-Token` on writes and compared against the session cookie. Because the
token is signed with its own derived subkey and carries the sub, an attacker cannot mint or guess
one, and a token from another user's session does not validate.

Only cookie-authenticated requests are checked: API keys and bearer tokens are not sent
automatically by browsers, so they are not forgeable this way.
"""
import time

from fastapi import HTTPException, Request
from jose import jwt

from .keys import signing_key

HEADER = "X-CSRF-Token"
TTL_S = 12 * 3600
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


def issue(sub: str) -> str:
    return jwt.encode({"sub": sub, "exp": int(time.time()) + TTL_S}, signing_key("csrf-token"), algorithm="HS256")


def valid(token: str | None, sub: str) -> bool:
    if not token:
        return False
    try:
        return jwt.decode(token, signing_key("csrf-token"), algorithms=["HS256"]).get("sub") == sub
    except Exception:
        return False


def enforce(request: Request, sub: str) -> None:
    """Raise 403 unless this cookie-authenticated write carries a matching CSRF token."""
    if request.method in SAFE_METHODS:
        return
    if not valid(request.headers.get(HEADER), sub):
        raise HTTPException(403, "missing or invalid CSRF token; fetch one from /auth/session")
