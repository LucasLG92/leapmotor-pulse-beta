"""Permisos nativos separados de BFF/CSRF; OAuth público sintético introspeccionado."""

import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

from fastapi.testclient import TestClient
import httpx2 as httpx
import pytest

from pulse.app import create_app
from pulse.config import Config
from pulse.native_auth import NativeAccessValidator


def config(**changes):
    return Config(**(dict(auth_mode="oidc", public_url="https://pulse.example.org",
                         oidc_issuer="https://id.example.org", oidc_client_id="pulse-web",
                         oidc_client_secret="synthetic-web-secret", allowed_users=("fixture",),
                         hosts=("pulse.example.org",), native_enabled=True, native_client_id="pulse-native",
                         native_audience="https://pulse.example.org/api/native",
                         native_introspection_url="https://id.example.org/api/oidc/introspection",
                         native_userinfo_url="https://id.example.org/api/oidc/userinfo",
                         native_introspection_client_id="pulse-introspector",
                         native_introspection_client_secret="synthetic-introspection-secret") | changes))


@pytest.fixture
def clients(tmp_path):
    calls = []
    def introspection(request):
        calls.append(request.url.path)
        if request.method == "GET":
            subjects = {"Bearer fixture-first-access": "fixture-first", "Bearer fixture-second-access": "fixture-second"}
            return httpx.Response(200, json={"sub": subjects[request.headers["authorization"]]})
        access = parse_qs(request.content.decode())["token"][0]
        subjects = {"fixture-first-access": "fixture-first", "fixture-second-access": "fixture-second"}
        if access not in subjects:
            return httpx.Response(200, json={"active": False})
        return httpx.Response(200, json={"active": True, "iss": "https://id.example.org",
                                        "sub": subjects[access], "client_id": "pulse-native",
                                        "token_type": "Bearer", "exp": int(time.time()) + 300,
                                        "aud": "https://pulse.example.org/api/native",
                                        "scope": "openid pulse.widget.read"})
    selected = config()
    app = create_app(tmp_path / "pulse.sqlite3", config=selected, native_transport=httpx.MockTransport(introspection))
    with TestClient(app, base_url=selected.public_url) as web:
        other = TestClient(app, base_url=selected.public_url)
        native = TestClient(app, base_url=selected.public_url)
        for client, subject in ((web, "fixture-first"), (other, "fixture-second")):
            sid, session = app.state.sessions.create({"id": subject, "name": "Fixture"})
            client.cookies.set(selected.cookie_name, sid, domain="pulse.example.org", path="/")
            client.headers["X-Pulse-CSRF"] = session.csrf
        yield web, other, native, calls, app
        other.close()
        native.close()


def profile(web, **changes):
    response = web.post("/api/widget-profiles", json={"name": "Solo batería y carga",
                       "fields": ["battery_pct", "charging"], "appearance": "system",
                       "privacy_mode": "visible", "max_age_minutes": 60} | changes)
    assert response.status_code == 200
    return response.json()["profile"]


def pair(web, selected):
    response = web.post(f'/api/widget-profiles/{selected["id"]}/pairings', json={})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    return response.json()["pairing_code"]


def register(native, code, access="fixture-first-access", installation=None):
    return native.post("/api/native/widget-grants", headers={"Authorization": f"Bearer {access}"},
                       json={"pairing_code": code, "installation_id": installation or secrets.token_urlsafe(32),
                             "device_name": "Teléfono de pruebas"})


def snapshot(native, registered):
    return native.get(f'/api/native/widget-grants/{registered["grant"]["id"]}/snapshot',
                      headers={"Authorization": f'Bearer {registered["widget_token"]}'})


@pytest.mark.parametrize("app_bootstrap", [False, True], ids=["widget", "app"])
def test_expiry_during_userinfo_does_not_create_access_or_consume_pairing(tmp_path, monkeypatch, app_bootstrap):
    now = [1000.9]
    expiry = [1001]

    def validator(selected, *, transport=None):
        return NativeAccessValidator(selected, transport=transport, clock=lambda: now[0])

    monkeypatch.setattr("pulse.native.NativeAccessValidator", validator)

    def handler(request):
        if request.method == "POST":
            scope = "openid profile pulse.app.access" if app_bootstrap else "openid pulse.widget.read"
            return httpx.Response(200, json={"active": True, "iss": "https://id.example.org",
                "sub": "fixture-first", "client_id": "pulse-native", "exp": expiry[0],
                "aud": "https://pulse.example.org/api/native", "scope": scope})
        now[0] = 1001
        return httpx.Response(200, json={"sub": "fixture-first", "preferred_username": "fixture"})

    selected = config(native_app_enabled=app_bootstrap)
    app = create_app(tmp_path / "pulse.sqlite3", config=selected, native_transport=httpx.MockTransport(handler))
    with TestClient(app, base_url=selected.public_url) as web:
        native = TestClient(app, base_url=selected.public_url)
        sid, session = app.state.sessions.create({"id": "fixture-first", "name": "Fixture"})
        web.cookies.set(selected.cookie_name, sid, domain="pulse.example.org", path="/")
        web.headers["X-Pulse-CSRF"] = session.csrf
        selected_profile = profile(web)
        code = pair(web, selected_profile)
        grants_url = f'/api/widget-profiles/{selected_profile["id"]}/native-grants'
        before = len(app.state.sessions.records)

        def exchange():
            if app_bootstrap:
                return native.post("/api/native/app-sessions", json={},
                                   headers={"Authorization": "Bearer fixture-first-access"})
            return register(native, code)

        rejected = exchange()
        assert rejected.status_code == 401
        assert rejected.headers["cache-control"] == "no-store"
        assert "set-cookie" not in rejected.headers
        assert len(app.state.sessions.records) == before
        assert web.get(grants_url).json()["items"] == []

        # A new, valid token can still use the unconsumed pairing or create a session.
        expiry[0] = 2000
        assert exchange().status_code == 200
        assert len(app.state.sessions.records) == before + int(app_bootstrap)
        assert len(web.get(grants_url).json()["items"]) == int(not app_bootstrap)
        native.close()


def test_end_to_end_pair_snapshot_update_and_web_revoke(clients):
    web, other, native, calls, app = clients
    selected = profile(web)
    assert web.get("/api/native-capabilities").json()["enabled"] is True
    code = pair(web, selected)
    response = register(native, code)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    registered = response.json()
    assert calls == ["/api/oidc/introspection", "/api/oidc/userinfo"]
    assert register(native, code).status_code == 401
    assert web.post("/api/demo", json={"enabled": True}).status_code == 200
    first = snapshot(native, registered)
    assert first.status_code == 200
    data = first.json()
    assert data["source"] == "demo" and data["physical_freshness"] == "unknown"
    assert set(data["values"]) == {"battery_pct", "charging"}
    assert type(data["values"]["charging"]) is bool
    assert first.headers["cache-control"] == "no-store"
    assert first.headers["x-content-type-options"] == "nosniff"
    assert "set-cookie" not in first.headers
    grant_id = registered["grant"]["id"]
    url = f'/api/widget-profiles/{selected["id"]}/native-grants'
    metadata = web.get(url).json()["items"]
    assert len(metadata) == 1
    assert set(metadata[0]) == {"id", "profile_id", "device_name", "created_at", "expires_at", "last_seen_at"}
    assert other.get(url).status_code == 404
    assert web.put(f'/api/widget-profiles/{selected["id"]}', json={"privacy_mode": "masked"}).status_code == 200
    changed = snapshot(native, registered)
    assert changed.status_code == 200
    assert changed.json()["profile"]["revision"] == selected["revision"] + 1
    assert all(value is None for value in changed.json()["values"].values())
    assert web.request("DELETE", url + "/" + grant_id, json={}, headers={"X-Pulse-CSRF": "bad"}).status_code == 403
    assert other.request("DELETE", url + "/" + grant_id, json={}).status_code == 404
    assert web.request("DELETE", url + "/" + grant_id, json={}).status_code == 200
    assert snapshot(native, registered).status_code == 401
    assert web.get(url).json()["items"] == []


def test_v2_energy_profile_is_manual_only_and_masking_survives_http_serialization(clients):
    web, _, native, calls, _ = clients
    selected = profile(web, name="Mi energía", fields=["energy_kwh", "energy_cost_eur"])
    registered = register(native, pair(web, selected)).json()
    empty = snapshot(native, registered).json()
    assert empty["schema_version"] == 2 and empty["kind"] == "energy"
    assert empty["source"] == "manual_energy" and empty["temporal_status"] == "unavailable"
    assert empty["valid_until"] is None and all(value is None for value in empty["values"].values())
    now = datetime.now(timezone.utc)
    assert web.put("/api/settings", json={"tariff_eur_kwh": .15}).status_code == 200
    assert web.post("/api/charges", json={
        "started_at": (now - timedelta(hours=2)).isoformat(),
        "ended_at": (now - timedelta(hours=1)).isoformat(), "kwh": 2,
        "pricing_method": "manual",
    }).status_code == 200
    assert web.post("/api/demo", json={"enabled": True}).status_code == 200
    before_calls = len(calls)
    result = snapshot(native, registered)
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
    data = result.json()
    assert data["values"] == {"energy_kwh": 2.0, "energy_cost_eur": .30}
    assert data["source"] == "manual_energy" and data["reason_code"] == "energy_calculated"
    window = data["energy_window"]
    assert window["ended_at"] == data["observed_at"] == data["server_received_at"]
    assert datetime.fromisoformat(window["ended_at"].replace("Z", "+00:00")) - datetime.fromisoformat(window["started_at"].replace("Z", "+00:00")) == timedelta(days=30)
    assert len(calls) == before_calls  # The snapshot never introspects or calls the vehicle provider.
    assert web.put(f'/api/widget-profiles/{selected["id"]}', json={"privacy_mode": "masked"}).status_code == 200
    masked = snapshot(native, registered).json()
    assert masked["values"] == {"energy_kwh": None, "energy_cost_eur": None}


def test_v2_car_closure_is_a_boolean_and_mixed_profiles_are_rejected(clients):
    web, _, native, _, _ = clients
    selected = profile(web, fields=["locked", "charging"])
    registered = register(native, pair(web, selected)).json()
    assert web.post("/api/demo", json={"enabled": True}).status_code == 200
    data = snapshot(native, registered).json()
    assert data["kind"] == "vehicle" and data["energy_window"] is None
    assert type(data["values"]["locked"]) is bool
    mixed = web.put(f'/api/widget-profiles/{selected["id"]}', json={"fields": ["locked", "energy_kwh"]})
    assert mixed.status_code == 400
    unchanged = snapshot(native, registered).json()
    assert unchanged["profile"]["fields"] == ["locked", "charging"]


def test_cross_owner_pairing_cannot_be_consumed(clients):
    web, other, native, _, _ = clients
    selected = profile(web)
    code = pair(web, selected)
    assert register(native, code, "fixture-second-access").status_code == 401
    assert register(native, code).status_code == 200
    assert other.post(f'/api/widget-profiles/{selected["id"]}/pairings', json={}).status_code == 404


def test_native_cookie_and_widget_token_never_authorize_other_resources(clients):
    web, _, native, calls, _ = clients
    selected = profile(web)
    registered = register(native, pair(web, selected)).json()
    token = registered["widget_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert native.get("/api/state", headers=headers).status_code == 401
    assert native.get("/api/export", headers=headers).status_code == 401
    assert native.post("/api/commands", headers=headers, json={"command": "unlock", "confirmed": True}).status_code == 401
    before = len(calls)
    assert register(native, secrets.token_urlsafe(32), token).status_code == 401
    assert len(calls) == before
    url = f'/api/native/widget-grants/{registered["grant"]["id"]}/snapshot'
    assert web.get(url).status_code == 401  # Session cookie alone is insufficient.
    assert native.get(url, headers={"Authorization": "Bearer fixture-first-access"}).status_code == 401
    assert len(calls) == before  # Snapshot cannot introspect/consume OAuth.
    assert native.get(url, headers=headers | {"Origin": "https://other.example.org"}).status_code == 403


def test_native_unlink_and_import_revoke_only_affected_owner(clients):
    web, other, native, _, _ = clients
    selected = profile(web)
    second = profile(other)
    first_grant = register(native, pair(web, selected)).json()
    second_grant = register(native, pair(other, second), "fixture-second-access").json()
    exported = web.get("/api/export").json()
    assert exported["schema_version"] == 15
    assert set(exported) == {"schema_version", "exported_at", "mode", "settings", "charges", "journeys",
                             "maintenance", "protection", "command_history", "widget_profiles", "telemetry_snapshots", "charger", "charger_pricing", "observed_events", "charger_energy", "charger_pricing_history"}
    assert "widget_token" not in str(exported) and "code_hash" not in str(exported)
    assert web.post("/api/import", json={"confirmed": True, "data": exported}).status_code == 200
    assert snapshot(native, first_grant).status_code == 401
    assert snapshot(native, second_grant).status_code == 200
    revoked = native.request("DELETE", f'/api/native/widget-grants/{second_grant["grant"]["id"]}', json={},
                             headers={"Authorization": f'Bearer {second_grant["widget_token"]}'})
    assert revoked.status_code == 200
    assert snapshot(native, second_grant).status_code == 401


def test_native_body_origin_and_claim_failures_before_mutation(clients):
    web, _, native, calls, _ = clients
    selected = profile(web)
    code = pair(web, selected)
    assert web.post(f'/api/widget-profiles/{selected["id"]}/pairings', json={}).status_code == 429
    assert register(native, code, "fixture-invalid-access").status_code == 401
    before = len(calls)
    assert native.post("/api/native/widget-grants", json={"oversized": "x" * 4100},
                       headers={"Authorization": "Bearer fixture-first-access"}).status_code == 413
    assert len(calls) == before
    assert native.post("/api/native/widget-grants", content="{}",
                       headers={"Authorization": "Bearer fixture-first-access"}).status_code == 415
    assert native.post("/api/native/widget-grants", json={},
                       headers={"Authorization": "Bearer fixture-first-access", "Origin": "https://other.example.org"}).status_code == 403
    assert native.post("/api/native/widget-grants", json={"pairing_code": code, "installation_id": secrets.token_urlsafe(32),
                       "device_name": "Test", "owner_id": "fixture-second"},
                       headers={"Authorization": "Bearer fixture-first-access"}).status_code == 422
    assert register(native, code).status_code == 200


def test_disabled_by_default_no_introspection_or_link_even_on_loopback(tmp_path):
    with TestClient(create_app(tmp_path / "pulse.sqlite3", config=Config()), base_url="http://127.0.0.1") as client:
        csrf = client.get("/api/session").json()["csrf_token"]
        selected = profile_with_csrf(client, csrf)
        assert client.get("/api/native-capabilities").json()["enabled"] is False
        assert client.post(f'/api/widget-profiles/{selected["id"]}/pairings', json={},
                           headers={"X-Pulse-CSRF": csrf}).status_code == 503
        assert client.post("/api/native/widget-grants", json={},
                           headers={"Authorization": "Bearer fixture-first-access"}).status_code == 503


def profile_with_csrf(client, csrf):
    client.headers["X-Pulse-CSRF"] = csrf
    return profile(client)


def test_recovery_flag_invalidates_existing_permissions_on_restart(tmp_path):
    selected = config()
    path = tmp_path / "pulse.sqlite3"
    app = create_app(path, config=selected)
    with TestClient(app, base_url=selected.public_url):
        store = app.state.store
        chosen = store.create_widget_profile("fixture-first", {"name": "Recovery", "fields": ["battery_pct"],
                          "privacy_mode": "masked", "appearance": "system", "max_age_minutes": 60})
        code = store.create_widget_pairing("fixture-first", chosen["id"])["pairing_code"]
        registered = store.register_widget_grant({"issuer": selected.oidc_issuer, "subject": "fixture-first",
                       "client_id": selected.native_client_id}, code, secrets.token_urlsafe(32), "Recovery test")
    app = create_app(path, config=config(revoke_widget_grants_on_start=True))
    with TestClient(app, base_url=selected.public_url) as native:
        assert app.state.store.list_widget_grants("fixture-first", chosen["id"])["items"] == []
        assert snapshot(native, registered).status_code == 401
