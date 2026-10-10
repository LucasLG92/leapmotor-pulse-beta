"""Introspección OAuth real con transporte sintético; nunca credenciales privadas."""

import asyncio
import base64
from urllib.parse import parse_qs

import httpx2 as httpx
import pytest
from starlette.requests import Request

from pulse.config import Config
from pulse.domain import DomainError
from pulse.native_auth import NativeAccessValidator, bearer_token


def native_config(**changes):
    values = dict(auth_mode="oidc", public_url="https://pulse.example.org",
                  oidc_issuer="https://id.example.org", oidc_client_id="pulse-web",
                  oidc_client_secret="synthetic-web-secret", allowed_users=("fixture",),
                  hosts=("pulse.example.org",), native_enabled=True, native_client_id="pulse-native",
                  native_audience="https://pulse.example.org/api/native",
                  native_introspection_url="https://id.example.org/api/oidc/introspection",
                  native_userinfo_url="https://id.example.org/api/oidc/userinfo",
                  native_introspection_client_id="pulse-introspector",
                  native_introspection_client_secret="synthetic-introspection-secret")
    return Config(**(values | changes))


def active_claims(**changes):
    return {"active": True, "iss": "https://id.example.org", "sub": "fixture-owner",
            "client_id": "pulse-native", "token_type": "Bearer", "exp": 2000,
            "aud": "https://pulse.example.org/api/native", "scope": "openid pulse.widget.read"} | changes


def validate_with(handler, token="synthetic-access-token"):
    validator = NativeAccessValidator(native_config(), transport=httpx.MockTransport(handler), clock=lambda: 1000)
    return asyncio.run(validator.identity(token))


def test_introspection_exact_destination_basic_auth_and_form():
    def handler(request):
        if request.method == "GET":
            assert str(request.url) == "https://id.example.org/api/oidc/userinfo"
            assert request.headers["authorization"] == "Bearer synthetic-access-token"
            return httpx.Response(200, json={"sub": "fixture-owner"})
        assert str(request.url) == "https://id.example.org/api/oidc/introspection"
        assert request.method == "POST"
        assert request.headers["content-type"] == "application/x-www-form-urlencoded"
        assert parse_qs(request.content.decode()) == {
            "token": ["synthetic-access-token"], "token_type_hint": ["access_token"]}
        assert request.headers["authorization"] == "Basic " + base64.b64encode(
            b"pulse-introspector:synthetic-introspection-secret").decode()
        return httpx.Response(200, json=active_claims())
    identity = validate_with(handler)
    assert identity.storage_identity() == {"issuer": "https://id.example.org",
                                           "subject": "fixture-owner", "client_id": "pulse-native"}
    assert "fixture-owner" not in repr(identity)


@pytest.mark.parametrize("changes", [
    {"active": False}, {"active": "true"}, {"iss": None}, {"iss": "https://other.example.org"},
    {"sub": ""}, {"sub": True}, {"sub": "bad\nsubject"}, {"client_id": "pulse-web"},
    {"token_type": "id_token"}, {"token_type": None},
    {"aud": "pulse-native"}, {"aud": ["https://pulse.example.org/api/native", "other-api"]},
    {"scope": "openid"}, {"scope": ["pulse.widget.read"]}, {"scope": "pulse.widget.read pulse.command"},
    {"exp": None}, {"exp": 1000}, {"exp": 1999.5}, {"exp": True},
    {"nbf": 1031}, {"iat": 1031}, {"iat": True},
])
def test_missing_or_wrong_authorization_claims_denied(changes):
    with pytest.raises(DomainError) as error:
        validate_with(lambda request: httpx.Response(200, json=active_claims(**changes)))
    assert error.value.status == 401
    assert "subject" not in str(error.value)


def test_widget_permission_is_never_introspected_as_oauth():
    called = []
    with pytest.raises(DomainError) as error:
        validate_with(lambda request: called.append(request), token="pw_synthetic-widget-permission")
    assert error.value.status == 401 and not called


def test_oauth_basic_credentials_are_form_encoded_before_base64():
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"sub": "fixture-owner"})
        encoded = request.headers["authorization"].split(" ", 1)[1]
        assert base64.b64decode(encoded).decode() == "pulse-introspector:fixture%2B+%3A%25"
        return httpx.Response(200, json=active_claims())
    validator = NativeAccessValidator(native_config(native_introspection_client_secret="fixture+ :%"),
                                     transport=httpx.MockTransport(handler), clock=lambda: 1000)
    assert asyncio.run(validator.identity("synthetic-access-token")).subject == "fixture-owner"


@pytest.mark.parametrize("response", [
    httpx.Response(302, headers={"location": "https://other.example.org/steal"}),
    httpx.Response(500, text="upstream-private-error"),
    httpx.Response(200, text="invalid", headers={"content-type": "application/json"}),
    httpx.Response(200, json=active_claims(), headers={"content-length": "65537"}),
    httpx.Response(200, text="x" * 65537, headers={"content-type": "application/json"}),
    httpx.Response(200, json=active_claims(), headers={"content-length": "bad"}),
    httpx.Response(200, content=b'{"active":false,"active":true}', headers={"content-type": "application/json"}),
    httpx.Response(200, content=b'{"active":true,"extra":NaN}', headers={"content-type": "application/json"}),
])
def test_upstream_errors_size_and_redirects_fail_closed(response):
    called = []
    def handler(request):
        called.append(request.url.host)
        return response
    with pytest.raises(DomainError) as error:
        validate_with(handler)
    assert error.value.status == 503
    assert called == ["id.example.org"]
    assert "upstream-private-error" not in str(error.value)


def test_concurrency_bound_and_transport_timeout_error():
    async def run():
        entered = asyncio.Event()
        release = asyncio.Event()
        async def handler(request):
            entered.set()
            await release.wait()
            raise httpx.ConnectTimeout("private-network-error")
        validator = NativeAccessValidator(native_config(), transport=httpx.MockTransport(handler), clock=lambda: 1000)
        first = asyncio.create_task(validator.identity("synthetic-first"))
        second = asyncio.create_task(validator.identity("synthetic-second"))
        await entered.wait()
        with pytest.raises(DomainError) as error:
            await validator.identity("synthetic-third")
        assert error.value.status == 429
        release.set()
        results = await asyncio.gather(first, second, return_exceptions=True)
        assert all(isinstance(result, DomainError) and result.status == 503 for result in results)
        assert validator.slots._value == 2
    asyncio.run(run())


@pytest.mark.parametrize("headers", [[], [(b"authorization", b"Basic abc")],
    [(b"authorization", b"Bearer a"), (b"authorization", b"Bearer b")],
    [(b"authorization", b"Bearer bad token")], [(b"authorization", b"Bearer " + b"x" * 4097)],
    [(b"authorization", b"Bearer \xff")]])
def test_invalid_bearer_header_rejected(headers):
    request = Request({"type": "http", "headers": headers})
    with pytest.raises(DomainError) as error:
        bearer_token(request)
    assert error.value.status == 401


@pytest.mark.parametrize("changes", [
    {"auth_mode": "local"}, {"native_client_id": "pulse-web"},
    {"native_client_id": "pulse-introspector"}, {"native_introspection_client_secret": ""},
    {"native_introspection_url": "https://other.example.org/introspection"},
    {"native_introspection_url": "https://id.example.org/introspection?token=bad"},
    {"native_introspection_url": "http://id.example.org/introspection"},
    {"native_audience": ""}, {"native_audience": "bad\nvalue"}, {"native_enabled": 1},
    {"native_userinfo_url": ""}, {"native_userinfo_url": "https://other.example.org/userinfo"},
])
def test_native_config_incomplete_or_insecure_cannot_enable(changes):
    with pytest.raises(ValueError):
        native_config(**changes)


def test_config_default_disabled_and_secrets_hidden():
    assert not Config().native_enabled
    assert "synthetic-web-secret" not in repr(native_config())
    assert "synthetic-introspection-secret" not in repr(native_config())


def test_missing_token_type_requires_protected_userinfo_same_subject():
    claims = active_claims()
    del claims["token_type"]
    calls = []
    def handler(request):
        calls.append(request.method)
        return httpx.Response(200, json=claims if request.method == "POST" else {"sub": "fixture-owner", "email": "ignored-fixture"})
    identity = validate_with(handler)
    assert calls == ["POST", "GET"]
    assert identity.subject == "fixture-owner" and "ignored-fixture" not in repr(identity)


@pytest.mark.parametrize("response,status", [
    (httpx.Response(401, json={"error": "invalid_token"}), 401),
    (httpx.Response(403, json={}), 401),
    (httpx.Response(200, json={"sub": "different-owner"}), 401),
    (httpx.Response(200, json={}), 401),
    (httpx.Response(302, headers={"location": "https://other.example.org/steal"}), 503),
    (httpx.Response(200, content=b'{"sub":"a","sub":"b"}', headers={"content-type": "application/json"}), 503),
    (httpx.Response(200, content=b'x' * 65537, headers={"content-type": "application/json"}), 503),
])
def test_active_introspected_token_without_access_resource_proof_denied(response, status):
    claims = active_claims()
    del claims["token_type"]  # Stock introspection may report active for refresh too.
    calls = []
    def handler(request):
        calls.append(request.method)
        return httpx.Response(200, json=claims) if request.method == "POST" else response
    with pytest.raises(DomainError) as error:
        validate_with(handler)
    assert error.value.status == status and calls == ["POST", "GET"]


def test_scope_openid_required_for_access_resource_gate():
    with pytest.raises(DomainError) as error:
        validate_with(lambda request: httpx.Response(200, json=active_claims(scope="pulse.widget.read")))
    assert error.value.status == 401
