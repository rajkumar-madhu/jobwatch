import base64
import hashlib
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException

from cronsentinel.routers import auth


@pytest.mark.parametrize('register', [False, True])
def test_login_binds_s256_challenge_to_server_side_verifier(monkeypatch, register):
    saved = {}
    monkeypatch.setattr(auth, '_r', SimpleNamespace(setex=lambda key, ttl, value: saved.update({key: value})))
    monkeypatch.setattr(auth, '_discovery', lambda: {'authorization_endpoint': 'https://idp.test/protocol/openid-connect/auth'})
    response = auth.login(register=register)
    query = parse_qs(urlparse(response.headers['location']).query)
    state = json.loads(saved['oauth:' + query['state'][0]])
    verifier = state['code_verifier']
    assert 43 <= len(verifier) <= 128
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    assert query['code_challenge_method'] == ['S256']
    assert query['code_challenge'] == [challenge]
    assert 'code_verifier' not in query


def test_callback_sends_saved_verifier_to_token_endpoint(monkeypatch):
    saved = json.dumps({'next': '/', 'nonce': 'nonce', 'code_verifier': 'v' * 64})
    monkeypatch.setattr(auth, '_r', SimpleNamespace(get=lambda key: saved, delete=lambda key: None))
    monkeypatch.setattr(auth, '_discovery', lambda: {'token_endpoint': 'https://idp.test/token'})
    requests = []
    def post(url, *, data, timeout):
        requests.append(data)
        return SimpleNamespace(status_code=400)
    monkeypatch.setattr(auth.httpx, 'post', post)
    with pytest.raises(HTTPException):
        auth.callback(code='code', state='state')
    assert requests[0]['code_verifier'] == 'v' * 64
