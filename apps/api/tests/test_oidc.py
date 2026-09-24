"""R7 — id_token verification. Pure: a throwaway RSA key stands in for the Keycloak JWKS."""
import time

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwk, jwt

from cronsentinel.oidc import IdTokenError, verify_id_token

ISS = "https://kc.example.test/realms/jobwatch"
AUD = "jobwatch-web"


def _keypair(kid="k1"):
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = priv.private_bytes(encoding=__import__("cryptography").hazmat.primitives.serialization.Encoding.PEM,
                             format=__import__("cryptography").hazmat.primitives.serialization.PrivateFormat.PKCS8,
                             encryption_algorithm=__import__("cryptography").hazmat.primitives.serialization.NoEncryption()).decode()
    pub = jwk.construct(priv.public_key().public_bytes(
        encoding=__import__("cryptography").hazmat.primitives.serialization.Encoding.PEM,
        format=__import__("cryptography").hazmat.primitives.serialization.PublicFormat.SubjectPublicKeyInfo).decode(),
        algorithm="RS256").to_dict()
    pub = {k: (v.decode() if isinstance(v, bytes) else v) for k, v in pub.items()}
    pub["kid"] = kid
    return pem, {"keys": [pub]}


def _token(pem, *, kid="k1", alg="RS256", access_token=None, **over):
    now = int(time.time())
    claims = {"iss": ISS, "aud": AUD, "sub": "user-1", "email": "a@b.test", "name": "A",
              "iat": now, "exp": now + 300, "nonce": "n1", **over}
    return jwt.encode(claims, pem, algorithm=alg, headers={"kid": kid}, access_token=access_token)


def test_valid_token_passes():
    pem, jwks = _keypair()
    c = verify_id_token(_token(pem), jwks=jwks, issuer=ISS, audience=AUD, nonce="n1")
    assert c["sub"] == "user-1" and c["email"] == "a@b.test"


def test_signature_from_a_different_key_is_rejected():
    pem, _ = _keypair()
    _, other_jwks = _keypair()
    with pytest.raises(IdTokenError):
        verify_id_token(_token(pem), jwks=other_jwks, issuer=ISS, audience=AUD, nonce="n1")


def test_alg_none_is_rejected():
    _, jwks = _keypair()
    import base64
    import json as _json
    def b64(d): return base64.urlsafe_b64encode(_json.dumps(d).encode()).rstrip(b"=").decode()
    # jose refuses to *mint* alg=none, so forge the wire format directly
    tok = b64({"alg": "none", "typ": "JWT"}) + "." + b64({"iss": ISS, "aud": AUD, "sub": "x", "exp": int(time.time()) + 300}) + "."
    with pytest.raises(IdTokenError, match="disallowed alg"):
        verify_id_token(tok, jwks=jwks, issuer=ISS, audience=AUD)


def test_hs256_signed_with_a_guessable_secret_is_rejected():
    """Classic confusion attack: sign HS256 using the public key material as the HMAC secret."""
    _, jwks = _keypair()
    tok = jwt.encode({"iss": ISS, "aud": AUD, "sub": "x", "exp": int(time.time()) + 300}, key="secret", algorithm="HS256")
    with pytest.raises(IdTokenError, match="disallowed alg"):
        verify_id_token(tok, jwks=jwks, issuer=ISS, audience=AUD)


def test_wrong_issuer_is_rejected():
    pem, jwks = _keypair()
    with pytest.raises(IdTokenError):
        verify_id_token(_token(pem, iss="https://evil.test/realms/x"), jwks=jwks, issuer=ISS, audience=AUD, nonce="n1")


def test_token_for_another_client_is_rejected():
    pem, jwks = _keypair()
    with pytest.raises(IdTokenError):
        verify_id_token(_token(pem, aud="some-other-client"), jwks=jwks, issuer=ISS, audience=AUD, nonce="n1")


def test_expired_token_is_rejected():
    pem, jwks = _keypair()
    now = int(time.time())
    with pytest.raises(IdTokenError):
        verify_id_token(_token(pem, iat=now - 4000, exp=now - 3600), jwks=jwks, issuer=ISS, audience=AUD, nonce="n1")


def test_nonce_mismatch_is_rejected():
    pem, jwks = _keypair()
    with pytest.raises(IdTokenError, match="nonce"):
        verify_id_token(_token(pem, nonce="replayed"), jwks=jwks, issuer=ISS, audience=AUD, nonce="n1")


def test_unknown_kid_is_rejected():
    pem, jwks = _keypair(kid="k1")
    with pytest.raises(IdTokenError, match="no JWKS key"):
        verify_id_token(_token(pem, kid="k9"), jwks=jwks, issuer=ISS, audience=AUD, nonce="n1")


def test_multi_audience_requires_matching_azp():
    pem, jwks = _keypair()
    ok = _token(pem, aud=[AUD, "other"], azp=AUD)
    assert verify_id_token(ok, jwks=jwks, issuer=ISS, audience=AUD, nonce="n1")["sub"] == "user-1"
    bad = _token(pem, aud=[AUD, "other"], azp="other")
    with pytest.raises(IdTokenError, match="azp"):
        verify_id_token(bad, jwks=jwks, issuer=ISS, audience=AUD, nonce="n1")


def test_token_without_sub_is_rejected():
    pem, jwks = _keypair()
    with pytest.raises(IdTokenError, match="sub"):
        verify_id_token(_token(pem, sub=""), jwks=jwks, issuer=ISS, audience=AUD, nonce="n1")


# R35 — found by testing against a REAL Keycloak, not this synthetic suite: every id_token issued
# alongside an access_token (i.e. every authorization_code exchange — what routers/auth.py's
# callback always does) carries an `at_hash` claim, per OIDC Core §3.1.3.6. Before this round
# verify_id_token() had no way to check it and no caller passed one, so jose's decoder raised a
# raw JWTClaimsError on every real login. These are the tests that should have existed already.
def test_token_with_at_hash_is_accepted_when_the_matching_access_token_is_supplied():
    pem, jwks = _keypair()
    tok = _token(pem, access_token="the-real-access-token")
    claims = verify_id_token(tok, jwks=jwks, issuer=ISS, audience=AUD, nonce="n1", access_token="the-real-access-token")
    assert claims["sub"] == "user-1" and "at_hash" in claims


def test_token_with_at_hash_is_not_checked_when_the_caller_supplies_no_access_token():
    # The OIDC spec makes at_hash checkable only when the verifier actually holds the matching
    # access_token; a caller that legitimately doesn't have one (or, before this round, simply
    # forgot to pass it — routers/auth.py's callback did exactly that) is not treated as an error.
    # This is why the real fix was wiring access_token through at the call site (routers/auth.py),
    # not making verify_id_token reject every at_hash-bearing token when the arg is omitted —
    # doing that would have turned "login always 401s against Keycloak" into "login always 401s
    # against Keycloak or any other spec-compliant IdP, with no way to opt out."
    pem, jwks = _keypair()
    tok = _token(pem, access_token="the-real-access-token")
    claims = verify_id_token(tok, jwks=jwks, issuer=ISS, audience=AUD, nonce="n1")
    assert claims["sub"] == "user-1"


def test_token_with_mismatched_at_hash_is_rejected():
    # a swapped/wrong access_token must be caught, not silently accepted because *some* value
    # was passed — this is what actually justifies checking the claim instead of just tolerating it.
    pem, jwks = _keypair()
    tok = _token(pem, access_token="the-real-access-token")
    with pytest.raises(IdTokenError, match="at_hash"):
        verify_id_token(tok, jwks=jwks, issuer=ISS, audience=AUD, nonce="n1", access_token="a-different-token")


def test_token_without_at_hash_is_unaffected_by_the_access_token_param():
    # backward compatibility: a token that never had at_hash (e.g. some non-Keycloak IdPs, or
    # Keycloak's direct-grant flow with no access_token requested) verifies the same whether or
    # not the caller happens to pass one.
    pem, jwks = _keypair()
    tok = _token(pem)  # no access_token -> no at_hash claim at all
    assert verify_id_token(tok, jwks=jwks, issuer=ISS, audience=AUD, nonce="n1")["sub"] == "user-1"
    assert verify_id_token(tok, jwks=jwks, issuer=ISS, audience=AUD, nonce="n1",
                           access_token="irrelevant-since-no-at_hash-claim-exists")["sub"] == "user-1"
