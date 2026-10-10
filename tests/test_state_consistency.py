"""Consistencia entre el resumen de estado y su snapshot de telemetría."""

import sqlite3
from datetime import datetime, timezone
from functools import partial

from fastapi.testclient import TestClient

from pulse.app import create_app
from pulse.config import Config
from pulse.domain import Store
from pulse.evcc import EvccConfig
from pulse.mqtt_policy import MqttConfig
from pulse.navigation_config import NavigationConfig
from pulse.push_policy import PushConfig


SOURCE = "MATE-API B10ReadClient"


def _config():
    return Config(
        hosts=("testserver",),
        native_enabled=False,
        native_app_enabled=False,
        mqtt=MqttConfig(enabled=False),
        push=PushConfig(enabled=False),
        evcc=EvccConfig(enabled=False),
        navigation=NavigationConfig(enabled=False),
    )


def _snapshot(odometer_km):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "source": SOURCE,
        "observed_at": now,
        "provider_received_at": now,
        "vehicle": {
            "battery_pct": 61.0,
            "range_km": 275.0,
            "odometer_km": odometer_km,
            "locked": True,
            "climate_on": False,
            "charging": False,
        },
        "field_sources": {
            "battery_pct": "precise_soc_percent",
            "range_km": "expected_range_km",
            "odometer_km": "odometer_km",
            "locked": None,
            "climate_on": None,
            "charging": None,
        },
    }


def _assert_state_pair(response, vehicle_name, odometer_km):
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    state = response.json()
    assert state["settings"]["vehicle_name"] == vehicle_name
    assert state["telemetry"]["latest"]["vehicle"]["odometer_km"] == odometer_km


def test_api_state_keeps_settings_and_snapshot_from_one_read(tmp_path):
    path = tmp_path / "pulse.sqlite3"
    store = Store(path)
    store.update_settings({"vehicle_name": "Vehículo A", "model": "Modelo A"})
    store.record_snapshot(_snapshot(1001.0))

    app = create_app(path, config=_config(), provider=None)
    with TestClient(app) as client:
        coordinator = app.state.coordinator
        original_call = coordinator.call
        injected = False

        async def interleave_after_state(method, *args, **kwargs):
            nonlocal injected
            result = await original_call(method, *args, **kwargs)
            if method in {"state", "state_context"} and not injected:
                injected = True
                await original_call(
                    "update_settings",
                    {"vehicle_name": "Vehículo B", "model": "Modelo B"},
                    write=True,
                )
                await original_call("record_snapshot", _snapshot(2002.0), write=True)
            return result

        coordinator.call = interleave_after_state
        try:
            first = client.get("/api/state")
        finally:
            coordinator.call = original_call

        assert injected
        _assert_state_pair(first, "Vehículo A", 1001.0)
        _assert_state_pair(client.get("/api/state"), "Vehículo B", 2002.0)


def test_demo_state_does_not_parse_or_expose_real_telemetry(tmp_path):
    path = tmp_path / "pulse.sqlite3"
    store = Store(path)
    store.record_snapshot(_snapshot(3003.0))
    store.set_demo(True)
    connection = sqlite3.connect(path)
    try:
        result = connection.execute(
            "UPDATE telemetry_snapshots SET field_sources_json = ? WHERE rowid = "
            "(SELECT MAX(rowid) FROM telemetry_snapshots)",
            ("{invalid-json",),
        )
        assert result.rowcount == 1
        connection.commit()
    finally:
        connection.close()

    with TestClient(create_app(path, config=_config(), provider=None)) as client:
        response = client.get("/api/state")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        state = response.json()
        assert state["mode"] == "demo"
        assert state["connection"]["status"] == "demo"
        assert state["telemetry"] is None

        coordinator = client.app.state.coordinator
        client.portal.call(partial(coordinator.call, "set_demo", False, write=True))
        normal = client.get("/api/state")
        assert normal.status_code == 500
        assert normal.headers["cache-control"] == "no-store"
        assert normal.json() == {
            "detail": "El almacén contiene un snapshot de telemetría inválido."
        }
