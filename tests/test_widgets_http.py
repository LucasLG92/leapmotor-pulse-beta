"""El editor deriva la propiedad de la sesión; no confía en IDs del navegador."""

import pytest
from fastapi.testclient import TestClient

from pulse.app import create_app
from pulse.config import Config


@pytest.fixture
def owners(tmp_path):
    config = Config(auth_mode="oidc", public_url="https://pulse.example.org",
                    oidc_issuer="https://id.example.org", oidc_client_id="pulse-test",
                    oidc_client_secret="synthetic-fixture", allowed_users=("lucas",),
                    hosts=("pulse.example.org",))
    app = create_app(tmp_path / "pulse.sqlite3", config=config)
    # OIDC criptográfico se prueba por separado; aquí aislamos la autorización de recurso.
    with TestClient(app, base_url=config.public_url) as first:
        second = TestClient(app, base_url=config.public_url)
        for client, subject in ((first, "test-first"), (second, "test-second")):
            sid, record = app.state.sessions.create({"id": subject, "name": "Test"})
            client.cookies.set(config.cookie_name, sid, domain="pulse.example.org", path="/")
            client.headers["X-Pulse-CSRF"] = record.csrf
        yield first, second
        second.close()


def profile_data(**updates):
    return {"name": "Inicio", "fields": ["battery_pct", "range_km"],
            "appearance": "system", "privacy_mode": "masked", "max_age_minutes": 60} | updates


def test_profile_crud_and_cross_owner_access_denied(owners):
    first, second = owners
    created = first.post("/api/widget-profiles", json=profile_data())
    assert created.status_code == 200, created.text
    profile = created.json()["profile"]
    assert "owner_id" not in profile and profile["privacy_mode"] == "masked"
    assert second.get("/api/widget-profiles").json() == {"items": []}
    path = f'/api/widget-profiles/{profile["id"]}'
    assert second.put(path, json={"name": "Ajeno"}).status_code == 404
    assert second.request("DELETE", path, json={}).status_code == 404
    edited = first.put(path, json={"name": "Batería", "fields": ["battery_pct"]})
    assert edited.status_code == 200
    assert edited.json()["profile"]["revision"] == profile["revision"] + 1
    assert first.get("/api/widget-profiles").json()["items"][0]["name"] == "Batería"
    assert first.request("DELETE", path, json={}).status_code == 200
    assert first.get("/api/widget-profiles").json() == {"items": []}


def test_profile_payload_privacy_limits_and_csrf(owners):
    first, _ = owners
    assert first.post("/api/widget-profiles", json=profile_data(owner_id="other")).status_code == 422
    assert first.post("/api/widget-profiles", json=profile_data(fields=["location"])).status_code == 422
    assert first.post("/api/widget-profiles", json=profile_data(max_age_minutes=121)).status_code == 422
    assert first.post("/api/widget-profiles", json=profile_data(max_age_minutes=True)).status_code == 422
    assert first.post("/api/widget-profiles", json=profile_data(fields=["battery_pct", "battery_pct"])).status_code == 400
    assert first.post("/api/widget-profiles", json=profile_data(), headers={"X-Pulse-CSRF": "invalid"}).status_code == 403
    for index in range(5):
        assert first.post("/api/widget-profiles", json=profile_data(name=f"Perfil {index}")).status_code == 200
    assert first.post("/api/widget-profiles", json=profile_data()).status_code == 409
    assert first.get("/api/widget-profiles").headers["cache-control"] == "no-store"


def test_backup_restore_cannot_replace_or_export_another_owners_profiles(owners):
    first, second = owners
    first.post("/api/widget-profiles", json=profile_data(name="Primero"))
    second.post("/api/widget-profiles", json=profile_data(name="Segundo"))
    archive = first.get("/api/export").json()
    assert len(archive["widget_profiles"]) == 1
    assert archive["widget_profiles"][0]["owner_id"] == "test-first"
    assert archive["widget_profiles"][0]["name"] == "Primero"
    foreign = second.get("/api/export").json()
    assert first.post("/api/import", json={"confirmed": True, "data": foreign}).status_code == 400
    assert first.post("/api/import", json={"confirmed": True, "data": archive}).status_code == 200
    assert second.get("/api/widget-profiles").json()["items"][0]["name"] == "Segundo"


def test_anonymous_profile_api_closed(tmp_path):
    config = Config(auth_mode="oidc", public_url="https://pulse.example.org",
                    oidc_issuer="https://id.example.org", oidc_client_id="pulse-test",
                    oidc_client_secret="synthetic-fixture", allowed_users=("lucas",),
                    hosts=("pulse.example.org",))
    with TestClient(create_app(tmp_path / "pulse.sqlite3", config=config), base_url=config.public_url) as client:
        assert client.get("/api/widget-profiles").status_code == 401
        assert client.post("/api/widget-profiles", json=profile_data()).status_code == 401
