"""Server activation is not physical evidence, and discloses no configuration values."""

import base64
import json

from fastapi.testclient import TestClient

from pulse.app import create_app
from pulse.config import Config
from pulse.mqtt_policy import MqttConfig
from pulse.push_policy import PushConfig
from pulse.evcc import EvccConfig
from pulse.navigation_config import NavigationConfig
from pulse.abrp_policy import AbrpConfig
from pulse.providers.base import UnconfiguredProvider


FLAGS = {
    "vehicle_reader_enabled", "charger_enabled", "interval_energy_enabled",
    "native_app_enabled", "native_widgets_enabled", "web_push_enabled",
    "home_assistant_enabled", "abrp_enabled", "navigation_enabled",
    "physical_commands_enabled",
}


def test_disabled_setup_stays_disabled_in_demo(tmp_path):
    app = create_app(tmp_path / "pulse.sqlite3", config=Config(hosts=("testserver",)))
    with TestClient(app) as client:
        initial = client.get("/api/state")
        assert initial.status_code == 200
        assert initial.headers["cache-control"] == "no-store"
        setup = initial.json()["setup"]
        assert set(setup) == FLAGS | {"auth_mode", "evidence_scope"}
        assert setup["auth_mode"] == "local"
        assert setup["evidence_scope"] == "configuration_only"
        assert all(setup[key] is False for key in FLAGS)
        csrf = client.get("/api/session").json()["csrf_token"]
        assert client.post("/api/demo", json={"enabled": True},
                           headers={"X-Pulse-CSRF": csrf}).status_code == 200
        assert client.get("/api/state").json()["setup"] == setup


def test_setup_is_protected_redacted_and_does_not_probe_integrations(tmp_path, monkeypatch):
    # Disable background starts for this endpoint test; any provider probe fails.
    async def no_start(*args):
        pass

    for name in ("Collector", "MqttBridge", "PushService", "AbrpBridge", "ChargerReader", "Electricity"):
        monkeypatch.setattr(f"pulse.app.{name}.start", no_start)

    class Reader:
        def connection_status(self):
            return UnconfiguredProvider().connection_status()

        def fetch_snapshot(self):
            raise AssertionError("A state GET must not start a vehicle read")

    private_values = ("synthetic-web-secret", "synthetic-introspection-secret",
                      "synthetic-api-key", "synthetic-user-token", "broker.example.org",
                      "https://evcc.example.org", "private-loadpoint", "operator@example.org")
    config = Config(auth_mode="oidc", public_url="https://pulse.example.org",
                    oidc_issuer="https://id.example.org", oidc_client_id="pulse-web",
                    oidc_client_secret=private_values[0], allowed_users=("fixture",),
                    hosts=("pulse.example.org",), native_enabled=True, native_app_enabled=True,
                    native_client_id="pulse-native", native_audience="https://pulse.example.org/api/native",
                    native_introspection_url="https://id.example.org/api/oidc/introspection",
                    native_userinfo_url="https://id.example.org/api/oidc/userinfo",
                    native_introspection_client_id="pulse-introspector",
                    native_introspection_client_secret=private_values[1],
                    mqtt=MqttConfig(enabled=True, host=private_values[4], instance="fixture",
                                    fields=("battery_pct",)),
                    push=PushConfig(enabled=True,
                                    vapid_private_key=base64.urlsafe_b64encode((1).to_bytes(32, "big")).decode().rstrip("="),
                                    storage_key=base64.urlsafe_b64encode(b"0" * 32).decode(),
                                    contact="mailto:" + private_values[7]),
                    evcc=EvccConfig(enabled=True, origin=private_values[5], loadpoint=private_values[6],
                                    energy_history_enabled=True),
                    navigation=NavigationConfig(enabled=True, contact=private_values[7]),
                    abrp=AbrpConfig(enabled=True, api_key=private_values[2], user_token=private_values[3]))
    app = create_app(tmp_path / "pulse.sqlite3", config=config, provider=Reader())
    with TestClient(app, base_url=config.public_url) as client:
        assert client.get("/api/state").status_code == 401
        assert client.get("/api/state", headers={"Remote-User": "fixture"}).status_code == 401
        sid, _ = app.state.sessions.create({"id": "fixture", "name": "Fixture"})
        client.cookies.set(config.cookie_name, sid, domain="pulse.example.org", path="/")
        response = client.get("/api/state")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        setup = response.json()["setup"]
        assert setup["auth_mode"] == "oidc"
        assert setup["evidence_scope"] == "configuration_only"
        assert setup["physical_commands_enabled"] is False
        assert all(setup[key] is True for key in FLAGS - {"physical_commands_enabled"})
        assert all(value not in json.dumps(setup) for value in private_values)
        assert all(value not in response.text for value in private_values)
