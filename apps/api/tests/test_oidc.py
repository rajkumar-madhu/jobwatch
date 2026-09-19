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


def _token(pem, *, kid="k1", alg="RS256", **over):
    now = int(time.time())
    claims = {"iss": ISS, "aud": AUD, "sub": "user-1", "email": "a@b.test", "name": "A",
              "iat": now, "exp": now + 300, "nonce": "n1", **over}
    return jwt.encode(claims, pem, algorithm=alg, headers={"kid": kid})


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
