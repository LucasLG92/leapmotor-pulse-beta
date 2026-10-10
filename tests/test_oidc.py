"""Authlib completo contra un IdP sintético firmado; ningún tráfico externo."""

import base64
import hashlib
import time
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from fastapi.testclient import TestClient
from joserfc import jwk, jwt

from pulse.app import create_app
from pulse.auth import build_oidc, Sessions
from pulse.config import Config


ISSUER = "https://identity.example.org"
CLIENT = "pulse-test"


@pytest.mark.parametrize("fault,expected", [(None, 303), ("signature", 401), ("nonce", 401),
    ("audience", 401), ("issuer", 401), ("expired", 401), ("userinfo_sub", 401), ("user_denied", 403)])
def test_signed_oidc_pkce_and_claim_validation(tmp_path, fault, expected):
    config = Config(auth_mode="oidc", public_url="https://pulse.example.org", oidc_issuer=ISSUER,
                    oidc_client_id=CLIENT, oidc_client_secret="synthetic-secret",
                    allowed_users=("operator",), hosts=("pulse.example.org",))
    key = jwk.RSAKey.generate_key(2048)
    public = key.as_dict(private=False)
    public["kid"] = "pulse-test-key"
    context = {}

    def provider(request):
        assert request.url.host == "identity.example.org"
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            return httpx2.Response(200, json={"issuer": ISSUER, "authorization_endpoint": ISSUER + "/authorize",
                "token_endpoint": ISSUER + "/token", "userinfo_endpoint": ISSUER + "/userinfo",
                "jwks_uri": ISSUER + "/jwks", "id_token_signing_alg_values_supported": ["RS256"],
                "code_challenge_methods_supported": ["S256"], "response_types_supported": ["code"]})
        if path == "/jwks":
            return httpx2.Response(200, json={"keys": [public]})
        if path == "/token":
            form = parse_qs(request.content.decode())
            verifier = form["code_verifier"][0]
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            assert challenge == context["code_challenge"]
            assert form["redirect_uri"] == [config.callback_url]
            claims = {"iss": ISSUER, "sub": "test-owner", "aud": CLIENT, "iat": int(time.time()),
                      "exp": int(time.time()) + 300, "nonce": context["nonce"]}
            if fault == "nonce":
                claims["nonce"] = "wrong-nonce"
            if fault == "audience":
                claims["aud"] = "another-client"
            if fault == "issuer":
                claims["iss"] = "https://another.example.org"
            if fault == "expired":
                claims["exp"] = int(time.time()) - 300
            signing_key = jwk.RSAKey.generate_key(2048) if fault == "signature" else key
            id_token = jwt.encode({"alg": "RS256", "kid": public["kid"]}, claims, signing_key)
            return httpx2.Response(200, json={"access_token": "synthetic-access", "id_token": id_token,
                                             "token_type": "Bearer", "expires_in": 300})
        if path == "/userinfo":
            assert request.headers["authorization"] == "Bearer synthetic-access"
            return httpx2.Response(200, json={"sub": "different-sub" if fault == "userinfo_sub" else "test-owner",
                "preferred_username": "intruder" if fault == "user_denied" else "operator", "name": "Test User"})
        raise AssertionError(f"Unexpected synthetic IdP endpoint {path}")

    oidc = build_oidc(config)
    oidc.client_kwargs["transport"] = httpx2.MockTransport(provider)
    app = create_app(tmp_path / "pulse.sqlite3", config=config, oidc_client=oidc)
    with TestClient(app, base_url=config.public_url) as client:
        client.get("/api/session")
        old_sid = client.cookies.get(config.cookie_name)
        login = client.get("/api/auth/login", follow_redirects=False)
        assert login.status_code == 302, login.text
        params = parse_qs(urlsplit(login.headers["location"]).query)
        context.update({key: values[0] for key, values in params.items()})
        assert context["code_challenge_method"] == "S256"
        callback = "/api/auth/callback?code=synthetic-code&state=" + context["state"]
        result = client.get(callback, follow_redirects=False)
        assert result.status_code == expected, result.text
        assert "synthetic-access" not in result.text + str(result.headers)
        assert client.get("/api/state").status_code == (200 if expected == 303 else 401)
        if expected == 303:
            assert client.cookies.get(config.cookie_name) != old_sid
            assert client.get("/api/session").json()["user"] == {"id": "test-owner", "name": "Test User"}
        # El state queda consumido incluso si la validación falla.
        assert client.get(callback, follow_redirects=False).status_code == 401


def test_anonymous_capacity_does_not_evict_authenticated_session():
    config = Config(max_sessions=3)
    sessions = Sessions(config)
    owner_sid, owner = sessions.create({"id": "owner", "name": "Test User"})
    for _ in range(20):
        sessions.create()
    assert len(sessions.records) == 3
    assert sessions.get(owner_sid) is owner
    assert owner.valid
    anonymous = next(record for record in sessions.records.values() if record.user is None)
    anonymous.created -= 601
    assert not sessions.active(anonymous)
