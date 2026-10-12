"""Bootstrap sintético de sesiones BFF independientes para la app Capacitor."""

import asyncio
import os
import time
from urllib.parse import parse_qs

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient

from pulse.app import create_app
from pulse.config import Config
from pulse.domain import DomainError
from pulse.native_auth import NativeAccessValidator


ISSUER = "https://id.example.org"
AUDIENCE = "https://pulse.example.org/api/native"
APP_SCOPES = "openid profile pulse.app.access"
WIDGET_SCOPES = "openid pulse.widget.read"


def native_config(**changes):
    values = dict(auth_mode="oidc", public_url="https://pulse.example.org",
                  oidc_issuer=ISSUER, oidc_client_id="pulse-web",
                  oidc_client_secret="synthetic-web-secret", allowed_users=("fixture",),
                  hosts=("pulse.example.org",), native_enabled=True, native_app_enabled=True,
                  native_client_id="pulse-public", native_audience=AUDIENCE,
                  native_introspection_url=ISSUER + "/api/oidc/introspection",
                  native_userinfo_url=ISSUER + "/api/oidc/userinfo",
                  native_introspection_client_id="pulse-introspector",
                  native_introspection_client_secret="synthetic-introspection-secret")
    return Config(**(values | changes))


def claims(scope=APP_SCOPES, *, subject="fixture-subject", **changes):
    return {"active": True, "iss": ISSUER, "sub": subject, "client_id": "pulse-public",
            "exp": int(time.time()) + 300, "aud": AUDIENCE, "scope": scope} | changes


def native_handler(token_profiles=None, *, scopes=None, subjects=None, refresh_tokens=()):
    token_profiles = token_profiles or {"synthetic-app-access": {"sub": "fixture-subject",
                       "preferred_username": "fixture", "name": "Fixture User"}}
    scopes = scopes or {}
    subjects = subjects or {}
    refresh_tokens = set(refresh_tokens)

    def handler(request):
        if request.method == "POST":
            token = parse_qs(request.content.decode())["token"][0]
            return httpx.Response(200, json=claims(scopes.get(token, APP_SCOPES),
                                                   subject=subjects.get(token, "fixture-subject")))
        token = request.headers["authorization"].removeprefix("Bearer ")
        if token in refresh_tokens:
            return httpx.Response(401, json={"error": "invalid_token"})
        return httpx.Response(200, json=token_profiles.get(token, token_profiles["synthetic-app-access"]))

    return handler


def validate_app(handler, token="synthetic-app-access", config=None):
    selected = config or native_config()
    validator = NativeAccessValidator(selected, transport=httpx.MockTransport(handler), clock=lambda: 1000)
    return asyncio.run(validator.app_identity(token))


def test_app_identity_uses_same_introspection_gate_and_sanitizes_name():
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "POST":
            assert parse_qs(request.content.decode()) == {
                "token": ["synthetic-app-access"], "token_type_hint": ["access_token"]}
            return httpx.Response(200, json=claims())
        assert request.headers["authorization"] == "Bearer synthetic-app-access"
        return httpx.Response(200, json={"sub": "fixture-subject", "preferred_username": "fixture",
                                        "name": "  Lucas\u0000 Pulse\u202e " + "x" * 200})

    identity = validate_app(handler)
    assert [request.method for request in requests] == ["POST", "GET"]
    assert identity.session_user()["id"] == "fixture-subject"
    assert len(identity.session_user()["name"]) == 128
    assert identity.session_user()["name"].startswith("Lucas Pulse")
    assert "\u0000" not in identity.session_user()["name"]
    assert "\u202e" not in identity.session_user()["name"]
    assert "fixture-subject" not in repr(identity)
    assert "fixture" not in repr(identity)


@pytest.mark.parametrize("scope", [WIDGET_SCOPES, APP_SCOPES + " pulse.widget.read",
                                    "openid profile pulse.app.access pulse.command"])
def test_app_identity_rejects_widget_and_extra_scopes(scope):
    with pytest.raises(DomainError) as error:
        validate_app(native_handler(scopes={"synthetic-app-access": scope}))
    assert error.value.status == 401


def test_app_identity_rejects_refresh_userinfo_subject_mismatch_and_unknown_user():
    refresh = native_handler(refresh_tokens={"synthetic-app-access"})
    with pytest.raises(DomainError) as error:
        validate_app(refresh)
    assert error.value.status == 401

    mismatch = native_handler(subjects={"synthetic-app-access": "introspected-subject"})
    with pytest.raises(DomainError) as error:
        validate_app(mismatch)
    assert error.value.status == 401

    unknown = native_handler(token_profiles={"synthetic-app-access": {
        "sub": "fixture-subject", "preferred_username": "unlisted", "name": "Ignored"}})
    with pytest.raises(DomainError) as error:
        validate_app(unknown)
    assert error.value.status == 403


def test_app_identity_rejects_widget_permission_before_introspection():
    calls = []
    with pytest.raises(DomainError) as error:
        validate_app(lambda request: calls.append(request), token="pw_synthetic-widget-permission")
    assert error.value.status == 401
    assert calls == []


def test_native_app_configuration_is_closed_by_default_and_strict(monkeypatch):
    assert Config().native_app_enabled is False
    with pytest.raises(ValueError):
        Config(native_app_enabled=1)
    with pytest.raises(ValueError):
        Config(native_app_enabled=True, native_enabled=False)

    for key in tuple(os.environ):
        if key.startswith("PULSE_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PULSE_NATIVE_APP_ENABLED", "false")
    assert Config.from_env().native_app_enabled is False
    monkeypatch.setenv("PULSE_NATIVE_APP_ENABLED", "yes")
    with pytest.raises(ValueError, match="PULSE_NATIVE_APP_ENABLED"):
        Config.from_env()


def test_bootstrap_creates_secure_new_bff_session_and_csrf_access(tmp_path):
    selected = native_config()
    app = create_app(tmp_path / "pulse.sqlite3", config=selected,
                     native_transport=httpx.MockTransport(native_handler()))
    with TestClient(app, base_url=selected.public_url) as client:
        old_sid, old_record = app.state.sessions.create({"id": "old-user", "name": "Old user"})
        client.cookies.set(selected.cookie_name, old_sid, domain="pulse.example.org", path="/")

        response = client.post("/api/native/app-sessions", json={},
                               headers={"Authorization": "Bearer synthetic-app-access"})
        assert response.status_code == 200
        assert response.json() == {"ok": True, "expires_in": 1800}
        assert response.headers["cache-control"] == "no-store"
        cookie = response.headers["set-cookie"]
        assert selected.cookie_name in cookie
        assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
        assert "Max-Age=1800" in cookie
        assert old_sid not in response.text

        new_sid = client.cookies.get(selected.cookie_name)
        assert new_sid and new_sid != old_sid
        assert app.state.sessions.get(old_sid) is old_record
        assert old_record.user == {"id": "old-user", "name": "Old user"}
        native_record = app.state.sessions.get(new_sid)
        assert native_record is not None
        assert native_record.user == {"id": "fixture-subject", "name": "Fixture User"}
        assert native_record.ttl == 1800
        assert "fixture-subject" not in repr(native_record)

        session = client.get("/api/session")
        assert session.status_code == 200
        assert session.json()["authenticated"] is True
        assert session.json()["user"] == {"id": "fixture-subject", "name": "Fixture User"}
        assert new_sid not in session.text
        assert client.get("/api/state").status_code == 200
        changed = client.post("/api/demo", json={"enabled": True},
                              headers={"X-Pulse-CSRF": session.json()["csrf_token"]})
        assert changed.status_code == 200


def test_bootstrap_rejections_do_not_create_session_or_use_widget_refresh(tmp_path):
    profiles = {
        "synthetic-app-access": {"sub": "fixture-subject", "preferred_username": "fixture", "name": "Fixture"},
        "synthetic-outsider": {"sub": "outsider-subject", "preferred_username": "outsider", "name": "Outsider"},
    }
    scopes = {"synthetic-widget-access": WIDGET_SCOPES}
    handler = native_handler(profiles, scopes=scopes,
                             subjects={"synthetic-outsider": "outsider-subject",
                                       "synthetic-mismatch": "introspected"},
                             refresh_tokens={"synthetic-refresh"})
    selected = native_config()
    app = create_app(tmp_path / "pulse.sqlite3", config=selected,
                     native_transport=httpx.MockTransport(handler))
    with TestClient(app, base_url=selected.public_url) as client:
        before = len(app.state.sessions.records)
        cases = [("synthetic-widget-access", 401), ("synthetic-refresh", 401),
                 ("synthetic-outsider", 403), ("synthetic-mismatch", 401)]
        for token, status in cases:
            response = client.post("/api/native/app-sessions", json={},
                                   headers={"Authorization": f"Bearer {token}"})
            assert response.status_code == status
            assert response.headers["cache-control"] == "no-store"
        assert len(app.state.sessions.records) == before


def test_bootstrap_disabled_and_strictly_empty_payload(tmp_path):
    selected = native_config(native_app_enabled=False)
    app = create_app(tmp_path / "disabled.sqlite3", config=selected,
                     native_transport=httpx.MockTransport(native_handler()))
    with TestClient(app, base_url=selected.public_url) as client:
        disabled = client.post("/api/native/app-sessions", json={},
                               headers={"Authorization": "Bearer synthetic-app-access"})
        assert disabled.status_code == 503

    selected = native_config()
    app = create_app(tmp_path / "payload.sqlite3", config=selected,
                     native_transport=httpx.MockTransport(native_handler()))
    with TestClient(app, base_url=selected.public_url) as client:
        invalid = client.post("/api/native/app-sessions", json={"user": "fixture"},
                              headers={"Authorization": "Bearer synthetic-app-access"})
        assert invalid.status_code == 422
        assert app.state.sessions.records == {}


def test_session_ttl_override_expires_and_respects_configured_upper_bound(tmp_path):
    selected = native_config(session_ttl=60)
    app = create_app(tmp_path / "short-ttl.sqlite3", config=selected,
                     native_transport=httpx.MockTransport(native_handler()))
    with TestClient(app, base_url=selected.public_url) as client:
        response = client.post("/api/native/app-sessions", json={},
                               headers={"Authorization": "Bearer synthetic-app-access"})
        assert response.status_code == 200
        assert response.json()["expires_in"] == 60
        assert "Max-Age=60" in response.headers["set-cookie"]
        sid = client.cookies.get(selected.cookie_name)
        record = app.state.sessions.get(sid)
        assert record and record.ttl == 60
        with pytest.raises(ValueError):
            app.state.sessions.create({"id": "other", "name": "Other"}, ttl=61)
        record.created -= 61
        expired = client.get("/api/state")
        assert expired.status_code == 401
        assert sid not in app.state.sessions.records


def test_max_sessions_limit_keeps_existing_session_unchanged(tmp_path):
    selected = native_config(max_sessions=1)
    app = create_app(tmp_path / "session-limit.sqlite3", config=selected,
                     native_transport=httpx.MockTransport(native_handler()))
    with TestClient(app, base_url=selected.public_url) as client:
        old_sid, old_record = app.state.sessions.create({"id": "old-user", "name": "Old user"})
        client.cookies.set(selected.cookie_name, old_sid, domain="pulse.example.org", path="/")
        response = client.post("/api/native/app-sessions", json={},
                               headers={"Authorization": "Bearer synthetic-app-access"})
        assert response.status_code == 429
        assert app.state.sessions.get(old_sid) is old_record
        assert len(app.state.sessions.records) == 1
