"""HTTP real del BFF con SQLite temporal, sin llamadas de nube."""

import asyncio
import hashlib
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.responses import RedirectResponse

from pulse.app import create_app
from pulse.config import Config
from pulse.security import MAX_BODY


@pytest.fixture
def client(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>Pulse</html>")
    app = create_app(tmp_path / "data/pulse.sqlite3", hosts=["testserver"], static_dir=dist)
    with TestClient(app) as client:
        yield client


def mutation(client, method, path, data):
    csrf = client.get("/api/session").json()["csrf_token"]
    return client.request(method, path, json=data, headers={"X-Pulse-CSRF": csrf})


def test_preparation_has_no_real_vehicle_and_command_closed(client):
    state = client.get("/api/state").json()
    assert state["mode"] == "unconfigured"
    assert state["vehicle"] is None
    assert not state["connection"]["commands_enabled"]
    assert mutation(client, "POST", "/api/commands", {"command": "unlock", "confirmed": True}).status_code == 409


def test_demo_mutation_persists_and_journal(client):
    assert mutation(client, "POST", "/api/demo", {"enabled": True}).status_code == 200
    before = client.get("/api/state").json()
    assert before["vehicle"]["locked"] is True
    assert mutation(client, "POST", "/api/commands", {"command": "unlock", "confirmed": True}).status_code == 200
    state = client.get("/api/state").json()
    assert state["vehicle"]["locked"] is False
    assert state["connection"]["status"] == "demo"
    journal = client.get("/api/commands/history").json()["items"]
    assert journal[0]["command"] == "unlock" and journal[0]["status"] == "simulated"


def test_crud_manual_survives_demo_off(client):
    charge = mutation(client, "POST", "/api/charges", {"started_at": "2026-10-08T10:00:00Z", "kwh": 20}).json()["charge"]
    result = mutation(client, "PUT", f'/api/charges/{charge["id"]}', {"kwh": 25, "tariff_eur_kwh": 0.2})
    assert result.status_code == 200, result.text
    assert result.json()["charge"]["cost_eur"] == 5
    maintenance = mutation(client, "POST", "/api/maintenance", {"title": "Presión", "due_date": "2026-11-01"}).json()["maintenance"]
    assert mutation(client, "PUT", f'/api/maintenance/{maintenance["id"]}', {"completed": True, "title": "Neumáticos"}).status_code == 200
    mutation(client, "POST", "/api/demo", {"enabled": True})
    mutation(client, "POST", "/api/demo", {"enabled": False})
    assert client.get("/api/charges").json()["total"] == 1
    assert client.get("/api/state").json()["maintenance"][0]["completed"] is True
    assert mutation(client, "DELETE", f'/api/charges/{charge["id"]}', {}).status_code == 200
    assert mutation(client, "DELETE", f'/api/maintenance/{maintenance["id"]}', {}).status_code == 200
    assert client.get("/api/charges").json()["total"] == 0


def test_csrf_cookie_origin_and_json_gate(client):
    response = client.get("/api/session")
    assert response.json()["authenticated"] is True
    assert "HttpOnly" in response.headers["set-cookie"] and "SameSite=lax" in response.headers["set-cookie"]
    csrf = response.json()["csrf_token"]
    assert client.post("/api/demo", json={"enabled": True}).status_code == 403
    assert client.post("/api/demo", json={"enabled": True}, headers={b"X-Pulse-CSRF": b"\xe9"}).status_code == 403
    assert client.post("/api/demo", json={"enabled": True}, headers={"X-Pulse-CSRF": csrf, "Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/demo", content='{"enabled":true}', headers={"X-Pulse-CSRF": csrf, "Content-Type": "text/plain"}).status_code == 415
    assert client.post("/api/demo", json={"enabled": True}, headers={"X-Pulse-CSRF": csrf, "Origin": "http://testserver"}).status_code == 200


@pytest.mark.parametrize("headers", [{"Remote-User": "lucas"}, {"X-Forwarded-For": "127.0.0.1"}, {"Forwarded": "for=127.0.0.1"}])
def test_proxy_header_cannot_authenticate_local(client, headers):
    response = client.get("/api/state", headers=headers)
    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-frame-options"] == "DENY"


def test_validation_does_not_echo_input(client):
    response = mutation(client, "PUT", "/api/settings", {"unknown": "PRIVATE_SENTINEL"})
    assert response.status_code == 422
    assert "PRIVATE_SENTINEL" not in response.text
    response = mutation(client, "POST", "/api/demo", {"enabled": "true"})
    assert response.status_code == 422
    assert mutation(client, "PUT", "/api/settings", {"tariff_eur_kwh": None}).status_code == 400


def test_body_size_limit_for_stream_without_content_length(client):
    csrf = client.get("/api/session").json()["csrf_token"]
    response = client.post("/api/import", content=(b"x" * 65536 for _ in range(MAX_BODY // 65536 + 1)),
                           headers={"X-Pulse-CSRF": csrf, "Content-Type": "application/json"})
    assert response.status_code == 413


def test_restore_preserves_previous_consistent_backup_and_bad_restore_atomic(client):
    mutation(client, "POST", "/api/maintenance", {"title": "Manual", "due_date": "2026-11-01"})
    document = client.get("/api/export").json()
    assert document["schema_version"] == 17
    bad = {**document, "schema_version": 999}
    assert mutation(client, "POST", "/api/import", {"confirmed": True, "data": bad}).status_code == 400
    assert client.get("/api/export").json()["maintenance"] == document["maintenance"]
    assert mutation(client, "POST", "/api/import", {"confirmed": True, "data": document}).status_code == 200
    recovery = list(client.app.state.coordinator.path.parent.glob("recovery/*.sqlite3"))
    assert recovery
    assert recovery[0].stat().st_mode & 0o777 == 0o600


def test_rejected_restores_do_not_create_or_prune_recovery_copies(client):
    mutation(client, "POST", "/api/maintenance", {"title": "Primero", "due_date": "2026-11-01"})
    archive = client.get("/api/export").json()
    mutation(client, "POST", "/api/maintenance", {"title": "Estado anterior valioso", "due_date": "2026-12-01"})
    assert mutation(client, "POST", "/api/import", {"confirmed": True, "data": archive}).status_code == 200
    directory = client.app.state.coordinator.path.parent / "recovery"
    def copies():
        return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in directory.glob("*.sqlite3")}
    before = copies()
    assert len(before) == 1
    for _ in range(6):
        assert mutation(client, "POST", "/api/import", {"confirmed": True,
                          "data": archive | {"schema_version": 999}}).status_code == 400
    assert copies() == before


def test_public_shell_generic_unknown_path_no_traversal(client):
    assert client.get("/settings").status_code == 200
    assert client.get("/readyz").status_code == 200
    assert client.get("/api/nonexistent").status_code == 404
    assert client.get("/LICENSE").status_code == 404
    assert client.get("/settings").headers["cache-control"] == "no-cache"
    assert client.get("/api/state").headers["cache-control"] == "no-store"
    assert "script-src 'self'" in client.get("/").headers["content-security-policy"]
    assert client.get("/", headers={"Host": "evil.example"}).status_code == 400


def test_second_process_owner_rejected_and_lock_released(tmp_path):
    path = tmp_path / "pulse.sqlite3"
    app1, app2 = create_app(path, hosts=["testserver"]), create_app(path, hosts=["testserver"])
    with TestClient(app1):
        with pytest.raises(RuntimeError, match="Otra instancia"):
            with TestClient(app2):
                pass
    with TestClient(create_app(path, hosts=["testserver"])) as client:
        assert client.get("/api/state").status_code == 200


def oidc_config(**kwargs):
    values = dict(auth_mode="oidc", public_url="https://pulse.example.org", oidc_issuer="https://identity.example.org",
                  oidc_client_id="pulse-test", oidc_client_secret="synthetic-secret", allowed_users=("lucas",), hosts=("pulse.example.org",))
    return Config(**(values | kwargs))


class MockOIDC:
    async def load_server_metadata(self):
        return {"issuer": "https://identity.example.org"}

    async def authorize_redirect(self, request, redirect_uri):
        request.session["state"] = "synthetic-state"
        return RedirectResponse("https://identity.example.org/authorize")

    async def authorize_access_token(self, request, **kwargs):
        if request.session.get("state") != request.query_params.get("state"):
            raise ValueError("bad state")
        return {"id_token": "synthetic-id-token", "access_token": "synthetic-access-token",
                "userinfo": {"sub": "test-owner"}}

    async def userinfo(self, token):
        return {"sub": "test-owner", "preferred_username": "lucas", "name": "Lucas"}


def test_oidc_session_rotation_and_logout(tmp_path):
    app = create_app(tmp_path / "pulse.sqlite3", config=oidc_config(), oidc_client=MockOIDC())
    with TestClient(app, base_url="https://pulse.example.org") as client:
        before = client.get("/api/session")
        assert before.json()["authenticated"] is False
        cookie1 = client.cookies.get("__Host-pulse_session")
        assert "Secure" in before.headers["set-cookie"]
        assert "synthetic" not in before.headers["set-cookie"]
        assert client.get("/api/state").status_code == 401
        assert client.get("/api/auth/login", follow_redirects=False).status_code == 307
        result = client.get("/api/auth/callback?state=synthetic-state", follow_redirects=False)
        assert result.status_code == 303
        assert client.cookies.get("__Host-pulse_session") != cookie1
        assert client.get("/api/session").json()["authenticated"] is True
        assert client.get("/api/state").status_code == 200
        csrf = client.get("/api/session").json()["csrf_token"]
        assert client.post("/api/auth/logout", json={}, headers={"X-Pulse-CSRF": csrf}).status_code == 200
        assert client.get("/api/state").status_code == 401
        assert client.get("/api/auth/callback?state=synthetic-state").status_code == 401


def test_oidc_claimless_callback_and_user_denied(tmp_path):
    class Claimless(MockOIDC):
        async def authorize_access_token(self, request, **kwargs):
            return {"access_token": "synthetic"}
    app = create_app(tmp_path / "pulse.sqlite3", config=oidc_config(), oidc_client=Claimless())
    with TestClient(app, base_url="https://pulse.example.org") as client:
        client.get("/api/auth/login", follow_redirects=False)
        assert client.get("/api/auth/callback?state=synthetic-state").status_code == 401
        assert client.get("/api/state").status_code == 401


@pytest.mark.parametrize("kwargs", [{"allowed_users": ()}, {"public_url": "http://pulse.example.org"},
                                     {"hosts": ("*",)}, {"oidc_client_secret": ""}])
def test_oidc_fails_closed_on_incomplete_config(kwargs):
    with pytest.raises(ValueError):
        oidc_config(**kwargs)
