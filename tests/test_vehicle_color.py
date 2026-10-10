import copy
import sqlite3

import pytest
from fastapi.testclient import TestClient

from pulse.app import create_app
from pulse.domain import DomainError, EXPORT_VERSION, SCHEMA_VERSION, Store


COLORS = (
    "starry_night_blue",
    "midnight_purple",
    "tundra_grey",
    "metallic_black",
    "stellar_silver",
    "lunar_white",
)


def test_vehicle_color_is_optional_validated_and_persists_without_losing_other_settings(tmp_path):
    path = tmp_path / "pulse.sqlite3"
    store = Store(path)

    assert store.get_settings()["vehicle_color"] is None
    for color in COLORS:
        store.update_settings({"vehicle_color": color})
        assert Store(path).get_settings()["vehicle_color"] == color

    store.update_settings({"vehicle_color": "starry_night_blue"})
    store.update_settings({"tariff_eur_kwh": 0.42})
    settings = Store(path).get_settings()
    assert settings["vehicle_color"] == "starry_night_blue"
    assert settings["tariff_eur_kwh"] == 0.42

    store.update_settings({"vehicle_color": None})
    assert Store(path).get_settings()["vehicle_color"] is None


@pytest.mark.parametrize("value", ["red", "Azul Noche Estrellada", "", 1, True, {}])
def test_invalid_vehicle_color_is_rejected(tmp_path, value):
    with pytest.raises(DomainError):
        Store(tmp_path / "pulse.sqlite3").update_settings({"vehicle_color": value})


def test_current_export_round_trip_and_historical_v12_import(tmp_path):
    source = Store(tmp_path / "source.sqlite3")
    source.update_settings({"vehicle_color": "lunar_white", "vehicle_name": "Coche guardado"})
    source.add_charge({"started_at": "2026-10-08T08:00:00Z", "kwh": 5})
    exported = source.export_data()

    assert exported["schema_version"] == EXPORT_VERSION == 13
    assert exported["settings"]["vehicle_color"] == "lunar_white"
    destination = Store(tmp_path / "destination.sqlite3")
    assert destination.import_data(exported) == {"ok": True, "mode": "unconfigured"}
    assert Store(tmp_path / "destination.sqlite3").get_settings()["vehicle_color"] == "lunar_white"
    assert len(destination.state("Pulse", "0.1")["energy"]["sessions"]) == 1

    historical = copy.deepcopy(exported)
    historical["schema_version"] = 12
    historical["settings"].pop("vehicle_color")
    assert destination.import_data(historical) == {"ok": True, "mode": "unconfigured"}
    restored = destination.export_data()
    assert restored["schema_version"] == 13
    assert restored["settings"]["vehicle_color"] is None
    assert restored["settings"]["vehicle_name"] == "Coche guardado"
    assert len(restored["charges"]) == 1


def test_schema_14_migration_adds_null_color_and_preserves_existing_settings_and_records(tmp_path):
    path = tmp_path / "pulse.sqlite3"
    store = Store(path)
    store.update_settings({
        "vehicle_name": "Mi B10", "model": "B10", "tariff_eur_kwh": 0.37,
        "home_label": "Garaje", "pricing_method": "pvpc",
    })
    charge = store.add_charge({
        "started_at": "2026-10-08T08:00:00Z", "ended_at": "2026-10-08T08:30:00Z",
        "kwh": 3, "pricing_method": "manual",
    })
    prior_settings = store.get_settings()
    prior_settings.pop("vehicle_color")

    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE settings DROP COLUMN vehicle_color")
        connection.execute("PRAGMA user_version = 14")

    migrated = Store(path)
    settings = migrated.get_settings()
    assert SCHEMA_VERSION == 15
    assert settings["vehicle_color"] is None
    settings.pop("vehicle_color")
    assert settings == prior_settings
    assert migrated.get_charge(charge["id"])["id"] == charge["id"]
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 15


def test_settings_api_returns_and_persists_vehicle_color(tmp_path):
    path = tmp_path / "pulse.sqlite3"
    app = create_app(path, hosts=["testserver"])
    with TestClient(app) as client:
        assert client.get("/api/state").json()["settings"]["vehicle_color"] is None
        csrf = client.get("/api/session").json()["csrf_token"]
        headers = {"X-Pulse-CSRF": csrf}

        updated = client.put(
            "/api/settings", json={"vehicle_color": "stellar_silver"}, headers=headers
        )
        assert updated.status_code == 200, updated.text
        assert client.get("/api/state").json()["settings"]["vehicle_color"] == "stellar_silver"

        partial = client.put("/api/settings", json={"vehicle_name": "Mi B10"}, headers=headers)
        assert partial.status_code == 200, partial.text
        assert client.get("/api/state").json()["settings"]["vehicle_color"] == "stellar_silver"

        invalid = client.put("/api/settings", json={"vehicle_color": "red"}, headers=headers)
        assert invalid.status_code in {400, 422}

    reopened = create_app(path, hosts=["testserver"])
    with TestClient(reopened) as client:
        assert client.get("/api/state").json()["settings"]["vehicle_color"] == "stellar_silver"
