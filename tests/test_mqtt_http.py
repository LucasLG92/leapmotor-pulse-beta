"""Integración BFF/Store del puente; sin un broker ni consultas al fabricante."""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from pulse.app import create_app
from pulse.config import Config
from pulse.domain import Store
from pulse.mqtt_policy import MqttConfig


class NeverStarted:
    async def start(self, *args, **kwargs):
        raise AssertionError("MQTT desactivado no debe iniciar un transporte")

    async def close(self, **kwargs):
        raise AssertionError("MQTT desactivado no debe usar un transporte")


def test_disabled_bridge_uses_no_network_and_public_status_has_no_credentials(tmp_path):
    mqtt = MqttConfig(host="broker.example", username="TEST_ONLY_USER",
                      password="TEST_ONLY_PASSWORD", instance="test-house",
                      fields=("battery_pct",))
    app = create_app(tmp_path / "pulse.sqlite3", config=Config(hosts=("testserver",), mqtt=mqtt),
                     mqtt_transport=NeverStarted())
    with TestClient(app) as client:
        response = client.get("/api/state")
        assert response.status_code == 200
        state = response.json()
        assert state["integrations"]["mqtt"] == {
            "enabled": False, "status": "disabled", "data_status": "no_data",
            "fields": ["battery_pct"], "max_age_seconds": 900,
            "observed_at": None, "last_published_at": None, "reason_code": "disabled",
        }
        assert response.headers["cache-control"] == "no-store"
        csrf = client.get("/api/session").json()["csrf_token"]
        export = client.get("/api/export")
        assert export.status_code == 200
        assert export.json()["schema_version"] == 12
        for value in ("broker.example", "TEST_ONLY_USER", "TEST_ONLY_PASSWORD", "test-house"):
            assert value not in response.text
            assert value not in export.text
        denied = client.put("/api/settings", json={"mqtt": {"enabled": True}},
                            headers={"X-Pulse-CSRF": csrf})
        assert denied.status_code == 422


def test_mqtt_context_returns_stored_telemetry_and_demo_flag_only(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3")
    assert store.mqtt_context() == {"demo_enabled": False, "snapshot": None}
    observed = datetime.now(timezone.utc).isoformat()
    snapshot = store.record_snapshot({
        "source": "MATE-API B10ReadClient", "observed_at": observed,
        "provider_received_at": observed,
        "vehicle": {"battery_pct": 72.5, "range_km": 320, "odometer_km": 100,
                    "locked": True, "climate_on": False, "charging": False},
        "field_sources": {"battery_pct": "precise_soc_percent", "range_km": "expected_range_km",
                          "odometer_km": "odometer_km", "locked": None,
                          "climate_on": None, "charging": None},
    })
    assert store.mqtt_context() == {"demo_enabled": False, "snapshot": snapshot}
    store.set_demo(True)
    assert store.mqtt_context() == {"demo_enabled": True, "snapshot": snapshot}
    store.set_demo(False)
    assert store.mqtt_context() == {"demo_enabled": False, "snapshot": snapshot}
