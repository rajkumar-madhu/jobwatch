"""OIDC id_token verification (R7).

Closes the Phase-1 stub `jwt.get_unverified_claims(...)  # TODO verify against JWKS`. TLS to the
token endpoint is not sufficient on its own: without checking the signature, issuer, audience,
expiry and nonce, any token the endpoint (or anything that can impersonate it, or a
misconfigured/second realm sharing the host) returns is accepted verbatim, and a replayed
authorization code yields a valid session.

Pure-ish: `verify_id_token` takes the JWKS as data so it is testable without a live Keycloak.
"""
import time

import httpx
from jose import jwt
from jose.exceptions import JWTError

ALLOWED_ALGS = ("RS256", "RS512", "ES256")  # never "none", never HS* (client secret as HMAC key)
LEEWAY_S = 60

_jwks_cache: dict[str, tuple[float, dict]] = {}
JWKS_TTL_S = 3600


class IdTokenError(Exception):
    pass


def fetch_jwks(jwks_uri: str, *, force: bool = False, ttl: int = JWKS_TTL_S) -> dict:
    hit = _jwks_cache.get(jwks_uri)
    if hit and not force and time.time() - hit[0] < ttl:
        return hit[1]
    jwks = httpx.get(jwks_uri, timeout=5).raise_for_status().json()
    _jwks_cache[jwks_uri] = (time.time(), jwks)
    return jwks


def _key_for(jwks: dict, kid: str | None) -> dict:
    keys = jwks.get("keys", [])
    if kid:
        for k in keys:
            if k.get("kid") == kid:
                return k
        raise IdTokenError(f"no JWKS key for kid {kid!r}")
    if len(keys) == 1:
        return keys[0]
    raise IdTokenError("id_token has no kid and the JWKS has multiple keys")


def verify_id_token(token: str, *, jwks: dict, issuer: str, audience: str, nonce: str | None = None,
                    access_token: str | None = None, now: float | None = None) -> dict:
    """Return the verified claims, or raise IdTokenError. Checks, in order:
    alg allow-list, signature, iss, aud (incl. azp for multi-audience tokens), exp/iat, nonce.
    When the id_token carries at_hash (Keycloak does), pass access_token so jose can verify it."""
    try:
        header = jwt.get_unverified_header(token)
    except JWTError as e:
        raise IdTokenError(f"malformed id_token: {e}") from e
    alg = header.get("alg")
    if alg not in ALLOWED_ALGS:
        raise IdTokenError(f"disallowed alg {alg!r}")
    key = _key_for(jwks, header.get("kid"))
    try:
        claims = jwt.decode(
            token, key, algorithms=list(ALLOWED_ALGS), issuer=issuer, audience=audience,
            access_token=access_token,
            options={"verify_aud": True, "verify_iss": True, "verify_exp": True,
                     "verify_signature": True, "leeway": LEEWAY_S},
        )
    except JWTError as e:
        raise IdTokenError(f"id_token rejected: {e}") from e

    # jose accepts a list aud containing our client; when it does, OIDC requires azp to be us.
    aud = claims.get("aud")
    if isinstance(aud, list) and len(aud) > 1 and claims.get("azp") not in (None, audience):
        raise IdTokenError("azp does not match the client id")
    t = now if now is not None else time.time()
    iat = claims.get("iat")
    if iat is not None and iat - LEEWAY_S > t:
        raise IdTokenError("id_token issued in the future")
    if nonce is not None and claims.get("nonce") != nonce:
        raise IdTokenError("nonce mismatch (possible replay)")
    if not claims.get("sub"):
        raise IdTokenError("id_token has no sub")
    return claims
