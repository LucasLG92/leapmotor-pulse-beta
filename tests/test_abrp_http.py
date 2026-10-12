"""Contrato HTTP real con DB temporal y transporte original simulado."""

from datetime import datetime, timezone
from functools import partial
import json

from fastapi.testclient import TestClient

from pulse.abrp_policy import AbrpConfig
from pulse.app import create_app
from pulse.config import Config
from pulse.domain import Store
from pulse.pvpc import PvpcError


async def no_pvpc(_day):
    raise PvpcError("Fixture sin red.")


class SyntheticTransport:
    def __init__(self):
        self.sent = []

    def connect(self, timeout_seconds):
        return object()

    def send(self, session, api_key, user_token, *, utc_seconds, soc_percent,
             timeout_seconds, request_lock, authorize):
        with request_lock:
            permitted = authorize()
        if not permitted:
            return False
        self.sent.append({"utc": utc_seconds, "soc": soc_percent})
        return True

    def close(self, _session):
        pass


def make_app(tmp_path, *, enabled=False):
    abrp = AbrpConfig(enabled=enabled, api_key="TEST_ONLY_API_KEY", user_token="TEST_ONLY_USER_TOKEN")
    transport = SyntheticTransport()
    app = create_app(tmp_path / "pulse.sqlite3", config=Config(hosts=("testserver",), abrp=abrp),
                     abrp_transport=transport, pvpc_fetcher=no_pvpc)
    return app, transport


def put(client, enabled):
    csrf = client.get("/api/session").json()["csrf_token"]
    return client.put("/api/integrations/abrp", json={"enabled": enabled}, headers={"X-Pulse-CSRF": csrf})


def test_disabled_status_no_network_or_private_values(tmp_path):
    app, transport = make_app(tmp_path)
    with TestClient(app) as client:
        state = client.get("/api/state")
        assert state.status_code == 200 and state.headers["cache-control"] == "no-store"
        assert state.json()["integrations"]["abrp"] == {
            "enabled": False, "consent": False, "status": "disabled", "reason_code": "disabled",
            "fields": ["battery_pct", "observed_at"], "max_age_seconds": 300,
            "last_shared_at": None, "validation": "not_qualified",
        }
        assert put(client, True).status_code == 409
        assert put(client, False).status_code == 200
        export = client.get("/api/export")
        for marker in ("TEST_ONLY_API_KEY", "TEST_ONLY_USER_TOKEN", app.state.config.abrp.scope):
            assert marker not in state.text and marker not in export.text
            assert marker not in repr(app.state.config)
        assert export.json()["schema_version"] == 15
        assert transport.sent == []


def test_consent_route_preserves_csrf_origin_types_and_no_reflection(tmp_path):
    app, transport = make_app(tmp_path, enabled=True)
    with TestClient(app) as client:
        csrf = client.get("/api/session").json()["csrf_token"]
        path = "/api/integrations/abrp"
        assert client.put(path, json={"enabled": True}).status_code == 403
        assert client.put(path, json={"enabled": True}, headers={"X-Pulse-CSRF": csrf, "Origin": "https://invalid.example"}).status_code == 403
        for payload in ({"enabled": "true"}, {"enabled": 1}, {"enabled": True, "token": "PRIVATE_SENTINEL"}, {}):
            response = client.put(path, json=payload, headers={"X-Pulse-CSRF": csrf})
            assert response.status_code == 422
            assert "PRIVATE_SENTINEL" not in response.text
            assert response.headers["cache-control"] == "no-store"
        assert client.post(path, json={"enabled": True}, headers={"X-Pulse-CSRF": csrf}).status_code == 405
        assert transport.sent == []


def test_explicit_disconnect_clears_persisted_permission_even_when_server_disabled(tmp_path):
    app, transport = make_app(tmp_path)
    store = Store(app.state.coordinator.path)
    store.set_abrp_consent(app.state.config.abrp.scope, True)
    with TestClient(app) as client:
        assert put(client, False).status_code == 200
        assert not Store(app.state.coordinator.path).abrp_context(app.state.config.abrp.scope)["consent"]
        assert client.get("/api/state").json()["integrations"]["abrp"]["status"] == "disabled"
        assert transport.sent == []


def test_only_live_callback_sends_and_demo_revocation_is_allowed(tmp_path):
    app, transport = make_app(tmp_path, enabled=True)
    with TestClient(app) as client:
        assert put(client, True).status_code == 200
        now = datetime.now(timezone.utc)
        reading = client.portal.call(partial(app.state.coordinator.call, "record_snapshot", {
            "source": "MATE-API B10ReadClient", "observed_at": now.isoformat(),
            "provider_received_at": now.isoformat(),
            "vehicle": {"battery_pct": 52.5, "range_km": None, "odometer_km": None,
                        "locked": None, "climate_on": None, "charging": None},
            "field_sources": {"battery_pct": "soc_percent", "range_km": None, "odometer_km": None,
                              "locked": None, "climate_on": None, "charging": None},
        }, write=True))
        client.portal.call(app.state.abrp.poll_once)
        assert transport.sent == []  # Persisted/imported records alone are ineligible.
        client.portal.call(app.state.abrp.observe, reading)
        client.portal.call(app.state.abrp.poll_once)
        assert transport.sent == [{"utc": int(now.timestamp()), "soc": 52.5}]
        state = client.get("/api/state").json()
        assert state["integrations"]["abrp"]["status"] == "shared"
        assert state["integrations"]["abrp"]["validation"] == "not_qualified"
        csrf = client.get("/api/session").json()["csrf_token"]
        assert client.post("/api/demo", json={"enabled": True}, headers={"X-Pulse-CSRF": csrf}).status_code == 200
        assert put(client, True).status_code == 409
        assert put(client, False).status_code == 200
        assert not Store(app.state.coordinator.path).abrp_context(app.state.config.abrp.scope)["consent"]
        assert len(transport.sent) == 1


def test_import_revokes_consent_and_does_not_share_imported_observation(tmp_path):
    app, transport = make_app(tmp_path, enabled=True)
    with TestClient(app) as client:
        assert put(client, True).status_code == 200
        archive = client.get("/api/export").json()
        csrf = client.get("/api/session").json()["csrf_token"]
        response = client.post("/api/import", json={"data": archive, "confirmed": True}, headers={"X-Pulse-CSRF": csrf})
        assert response.status_code == 200
        assert not client.get("/api/state").json()["integrations"]["abrp"]["consent"]
        client.portal.call(app.state.abrp.poll_once)
        assert transport.sent == []
        assert "abrp" not in json.dumps(client.get("/api/export").json())


def test_callback_during_restore_cannot_requalify_imported_id(tmp_path):
    app, transport = make_app(tmp_path, enabled=True)
    with TestClient(app) as client:
        assert put(client, True).status_code == 200
        now = datetime.now(timezone.utc).isoformat()
        reading = client.portal.call(partial(app.state.coordinator.call, "record_snapshot", {
            "source": "MATE-API B10ReadClient", "observed_at": now, "provider_received_at": now,
            "vehicle": {"battery_pct": 40, "range_km": None, "odometer_km": None,
                        "locked": None, "climate_on": None, "charging": None},
            "field_sources": {"battery_pct": "soc_percent", "range_km": None, "odometer_km": None,
                              "locked": None, "climate_on": None, "charging": None},
        }, write=True))
        archive = client.get("/api/export").json()
        original_restore = app.state.coordinator.restore

        async def interleave(document, **kwargs):
            app.state.abrp.observe(reading)
            return await original_restore(document, **kwargs)

        app.state.coordinator.restore = interleave
        csrf = client.get("/api/session").json()["csrf_token"]
        try:
            restored = client.post("/api/import", json={"data": archive, "confirmed": True}, headers={"X-Pulse-CSRF": csrf})
        finally:
            app.state.coordinator.restore = original_restore
        assert restored.status_code == 200
        assert put(client, True).status_code == 200
        client.portal.call(app.state.abrp.poll_once)
        assert transport.sent == []
