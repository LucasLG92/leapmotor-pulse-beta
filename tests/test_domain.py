import copy
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from pulse import domain
from pulse.domain import DomainError, EXPORT_VERSION, SCHEMA_VERSION, Store
from schema_helpers import downgrade_backup_alerts_to_v17, downgrade_telemetry_to_v15


def make_store(tmp_path):
    return Store(tmp_path / "pulse.sqlite3")


def make_v1_database(path, *, demo=True):
    """Create a representative schema-1 database with data in each collection."""
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE app_meta (
                id INTEGER PRIMARY KEY CHECK (id = 1), demo_enabled INTEGER NOT NULL,
                protection_enabled INTEGER NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE settings (
                id INTEGER PRIMARY KEY CHECK (id = 1), vehicle_name TEXT NOT NULL,
                model TEXT NOT NULL, tariff_eur_kwh REAL NOT NULL, home_label TEXT NOT NULL
            );
            CREATE TABLE manual_charges (
                id TEXT PRIMARY KEY, started_at TEXT NOT NULL, kwh REAL NOT NULL, cost_eur REAL NOT NULL
            );
            CREATE TABLE demo_charges (
                id TEXT PRIMARY KEY, started_at TEXT NOT NULL, kwh REAL NOT NULL, cost_eur REAL NOT NULL
            );
            CREATE TABLE demo_journeys (
                id TEXT PRIMARY KEY, started_at TEXT NOT NULL, from_label TEXT NOT NULL,
                to_label TEXT NOT NULL, distance_km REAL NOT NULL, duration_min INTEGER NOT NULL,
                energy_kwh REAL NOT NULL
            );
            CREATE TABLE manual_maintenance (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, due_date TEXT NOT NULL, completed INTEGER NOT NULL
            );
            CREATE TABLE demo_maintenance (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, due_date TEXT NOT NULL, completed INTEGER NOT NULL
            );
            CREATE TABLE demo_protection_events (
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL
            );
            INSERT INTO app_meta VALUES (1, 1, 1, '2026-10-01T00:00:00Z');
            INSERT INTO settings VALUES (1, 'B10 anterior', 'B10', 0.32, 'Garaje');
            INSERT INTO manual_charges VALUES ('legacy-manual', '2026-10-01T08:00:00Z', 10, 3.20);
            INSERT INTO demo_charges VALUES ('legacy-demo', '2026-10-02T08:00:00Z', 8, 2.56);
            INSERT INTO demo_journeys VALUES
                ('legacy-trip', '2026-10-02T09:00:00Z', 'Origen ficticio', 'Destino ficticio', 12, 20, 2);
            INSERT INTO manual_maintenance VALUES ('legacy-service', 'Pastillas', '2027-01-01', 1);
            INSERT INTO demo_maintenance VALUES ('legacy-demo-service', 'Ejemplo', '2027-02-01', 0);
            INSERT INTO demo_protection_events VALUES
                ('legacy-event', '2026-10-02T10:00:00Z', 'movement', 'Evento ficticio antiguo.');
            PRAGMA user_version = 1;
            """
        )
        if not demo:
            connection.execute("UPDATE app_meta SET demo_enabled=0, protection_enabled=0 WHERE id=1")


def test_initial_state_matches_unconfigured_api_contract_and_persists(tmp_path):
    path = tmp_path / "pulse.sqlite3"
    state = Store(path).state("Leapmotor Pulse", "0.1")

    assert state["app"] == {"name": "Leapmotor Pulse", "version": "0.1"}
    assert state["mode"] == "unconfigured"
    assert state["vehicle"] is None
    assert state["energy"] == {
        "sessions": [],
        "totals": {"kwh": 0.0, "cost_eur": 0.0, "distance_km": 0.0, "consumption_kwh_100km": 0.0},
        "totals_by_source": {
            "manual": {"kwh": 0.0, "cost_eur": 0.0, "distance_km": 0.0, "consumption_kwh_100km": 0.0},
            "demo": {"kwh": 0.0, "cost_eur": 0.0, "distance_km": 0.0, "consumption_kwh_100km": 0.0},
        },
    }
    assert state["journeys"] == []
    assert state["maintenance"] == []
    assert state["protection"] == {"enabled": False, "events": [], "observed_count": 0}
    assert state["command_history"] == []
    assert all(command["enabled"] is False for command in state["commands"])
    assert set(state) == {
        "app", "mode", "updated_at", "vehicle", "settings", "energy", "journeys",
        "maintenance", "protection", "commands", "connection",
        "command_history",
    }

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert Store(path).state("Leapmotor Pulse", "0.1") == state


def test_manual_records_persist_and_demo_off_preserves_them(tmp_path):
    store = make_store(tmp_path)
    store.update_settings({"tariff_eur_kwh": 0.30})
    charge = store.add_charge({"started_at": "2026-10-07T10:00:00+02:00", "kwh": 10})
    maintenance = store.add_maintenance({"title": "Revisar neumáticos", "due_date": "2027-03-15"})

    # A second Store instance verifies that each operation committed to disk.
    reopened = Store(tmp_path / "pulse.sqlite3")
    assert reopened.state("Pulse", "0.1")["energy"]["sessions"] == [charge]
    assert reopened.state("Pulse", "0.1")["maintenance"] == [maintenance]

    store.set_demo(True)
    demo_state = store.state("Pulse", "0.1")
    assert demo_state["mode"] == "demo"
    assert demo_state["vehicle"] is not None
    assert {item["source"] for item in demo_state["energy"]["sessions"]} == {"manual", "demo"}
    assert len(demo_state["journeys"]) > 0
    assert any(item["id"] == maintenance["id"] for item in demo_state["maintenance"])

    store.set_demo(True)
    repeated = store.state("Pulse", "0.1")
    assert len(repeated["energy"]["sessions"]) == len(demo_state["energy"]["sessions"])
    assert len(repeated["journeys"]) == len(demo_state["journeys"])

    store.set_demo(False)
    unconfigured = Store(tmp_path / "pulse.sqlite3").state("Pulse", "0.1")
    assert unconfigured["mode"] == "unconfigured"
    assert unconfigured["vehicle"] is None
    assert unconfigured["energy"]["sessions"] == [charge]
    assert unconfigured["journeys"] == []
    assert unconfigured["protection"] == {"enabled": False, "events": [], "observed_count": 0}
    assert unconfigured["maintenance"] == [maintenance]

    store.set_demo(True)
    reenabled = store.state("Pulse", "0.1")
    assert len(reenabled["energy"]["sessions"]) == len(demo_state["energy"]["sessions"])
    assert len(reenabled["journeys"]) == len(demo_state["journeys"])


def test_charge_cost_is_a_snapshot_of_the_tariff_at_entry(tmp_path):
    store = make_store(tmp_path)
    store.update_settings({"tariff_eur_kwh": 0.40})
    session = store.add_charge({"started_at": "2026-10-07T08:00:00Z", "kwh": 12.5})
    assert session["cost_eur"] == 5.0
    assert session["source"] == "manual"

    store.update_settings({"tariff_eur_kwh": 0.90})
    persisted = Store(tmp_path / "pulse.sqlite3").state("Pulse", "0.1")["energy"]["sessions"]
    assert persisted == [session]


def test_maintenance_completion_persists_for_manual_and_demo_records(tmp_path):
    store = make_store(tmp_path)
    manual = store.add_maintenance({"title": "Filtro de habitáculo", "due_date": "2027-05-01"})
    store.complete_maintenance(manual["id"], True)
    assert Store(tmp_path / "pulse.sqlite3").state("Pulse", "0.1")["maintenance"] == [
        {**manual, "completed": True}
    ]

    store.set_demo(True)
    store.complete_maintenance("demo-maintenance-1", True)
    assert any(
        item["id"] == "demo-maintenance-1" and item["completed"]
        for item in store.state("Pulse", "0.1")["maintenance"]
    )

    store.set_demo(False)
    assert Store(tmp_path / "pulse.sqlite3").state("Pulse", "0.1")["maintenance"] == [
        {**manual, "completed": True}
    ]
    with pytest.raises(DomainError) as missing_demo_item:
        store.complete_maintenance("demo-maintenance-1", False)
    assert missing_demo_item.value.status == 404


def test_commands_and_protection_are_blocked_until_explicit_demo(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(DomainError) as command_error:
        store.command("lock", confirmed=True)
    assert command_error.value.status == 409

    with pytest.raises(DomainError) as protection_error:
        store.set_protection(True)
    assert protection_error.value.status == 409
    with pytest.raises(DomainError) as event_error:
        store.simulate_event()
    assert event_error.value.status == 409

    store.set_demo(True)
    with pytest.raises(DomainError):
        store.command("unknown", confirmed=False)
    assert store.command_history() == []
    result = store.command("lock", confirmed=True)
    assert result["status"] == "simulated"
    assert "No se envió ninguna orden" in result["message"]
    event = store.simulate_event()
    state = store.state("Pulse", "0.1")
    assert event in state["protection"]["events"]
    assert all(item["kind"] and item["message"] for item in state["protection"]["events"])
    assert state["vehicle"]["locked"] is True
    assert state["command_history"][0]["command"] == "lock"
    assert state["command_history"][0]["status"] == "simulated"


def test_demo_commands_persist_vehicle_state_and_demo_off_clears_only_demo_history(tmp_path):
    store = make_store(tmp_path)
    manual = store.add_charge({"started_at": "2026-10-07T08:00:00Z", "kwh": 4})
    store.set_demo(True)
    store.command("unlock", confirmed=True)
    store.command("climate_on", confirmed=True)
    store.command("charge_start", confirmed=True)

    state = Store(tmp_path / "pulse.sqlite3").state("Pulse", "0.1")
    assert state["vehicle"]["locked"] is False
    assert state["vehicle"]["climate_on"] is True
    assert state["vehicle"]["charging"] is True
    assert state["vehicle"]["observed_at"] != "2026-10-07T08:30:00Z"
    assert [item["command"] for item in state["command_history"]] == [
        "charge_start", "climate_on", "unlock"
    ]

    store.set_demo(False)
    reset = store.state("Pulse", "0.1")
    assert reset["command_history"] == []
    assert reset["energy"]["sessions"] == [manual]
    store.set_demo(True)
    assert store.command_history() == []


def test_demo_fixture_uses_injected_clock_and_state_totals_are_separated(tmp_path):
    fixed = datetime(2026, 10, 8, 15, 0, tzinfo=timezone.utc)
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: fixed)
    store.add_charge({"started_at": "2026-10-08T10:00:00Z", "kwh": 10})
    store.set_demo(True)
    state = store.state("Pulse", "0.1")
    assert state["vehicle"]["observed_at"] == "2026-10-08T15:00:00.000000Z"
    assert state["energy"]["totals_by_source"]["manual"]["kwh"] == 10
    assert state["energy"]["totals_by_source"]["demo"]["kwh"] == 44.6
    assert state["energy"]["totals"]["kwh"] == 54.6
    assert all("ficticia" in item["from_label"] or "ficticio" in item["from_label"] for item in state["journeys"])
    assert all(item["started_at"] < "2026-10-08T15:00:00.000000Z" for item in state["journeys"])


def test_schema_v1_migrates_data_and_leaves_historical_tariffs_unknown(tmp_path):
    path = tmp_path / "nested" / "legacy.sqlite3"
    path.parent.mkdir()
    make_v1_database(path)
    store = Store(path)
    state = store.state("Pulse", "0.1")

    assert state["mode"] == "demo"
    assert state["settings"] == {
        "vehicle_name": "B10 anterior", "model": "B10", "tariff_eur_kwh": 0.32,
        "home_label": "Garaje", "pricing_method": "manual", "vehicle_color": None,
    }
    sessions = {item["id"]: item for item in state["energy"]["sessions"]}
    assert sessions["legacy-manual"]["cost_eur"] == 3.2
    assert sessions["legacy-manual"]["tariff_eur_kwh"] is None
    assert sessions["legacy-demo"]["cost_eur"] == 2.56
    assert any(item["id"] == "legacy-trip" for item in state["journeys"])
    assert any(item["id"] == "legacy-service" for item in state["maintenance"])
    assert any(item["id"] == "legacy-demo-service" for item in state["maintenance"])
    assert any(item["id"] == "legacy-event" for item in state["protection"]["events"])
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION

    with pytest.raises(DomainError, match="tariff_eur_kwh"):
        store.update_charge("legacy-manual", {"kwh": 11})
    edited = store.update_charge("legacy-manual", {"kwh": 11, "tariff_eur_kwh": 0.4})
    assert edited["cost_eur"] == 4.4


def test_v1_unconfigured_migration_does_not_reveal_demo_data(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    make_v1_database(path, demo=False)
    state = Store(path).state("Pulse", "0.1")
    assert state["mode"] == "unconfigured"
    assert [item["id"] for item in state["energy"]["sessions"]] == ["legacy-manual"]
    assert state["journeys"] == []
    assert state["protection"]["events"] == []
    assert [item["id"] for item in state["maintenance"]] == ["legacy-service"]


def test_schema_v2_migrates_additively_and_preserves_existing_data(tmp_path, monkeypatch):
    path = tmp_path / "v2.sqlite3"
    make_v1_database(path)
    # Build a real schema-2 fixture by stopping the later migrations after V1->V2.
    with monkeypatch.context() as patcher:
        patcher.setattr(Store, "_migrate_v2_to_v3", lambda self, connection: None)
        patcher.setattr(Store, "_migrate_v3_to_v4", lambda self, connection: None)
        patcher.setattr(Store, "_migrate_v4_to_v5", lambda self, connection: None)
        patcher.setattr(Store, "_migrate_v5_to_v6", lambda self, connection: None)
        patcher.setattr(Store, "_migrate_v6_to_v7", lambda self, connection: None)
        patcher.setattr(Store, "_migrate_v8_to_v9", lambda self, connection: None)
        patcher.setattr(Store, "_migrate_v9_to_v10", lambda self, connection: None)
        patcher.setattr(Store, "_migrate_v10_to_v11", lambda self, connection: None)
        patcher.setattr(Store, "_migrate_v11_to_v12", lambda self, connection: None)
        patcher.setattr(Store, "_migrate_v12_to_v13", lambda self, connection: None)
        patcher.setattr(Store, "_migrate_v15_to_v16", lambda self, connection: None)
        patcher.setattr(Store, "_migrate_v16_to_v17", lambda self, connection: None)
        patcher.setattr(Store, "_migrate_v17_to_v18", lambda self, connection: None)
        Store(path)
    with sqlite3.connect(path) as connection:
        downgrade_backup_alerts_to_v17(connection)
        connection.execute("DROP TABLE IF EXISTS charger_pricing_history")
        connection.execute("DROP TABLE IF EXISTS charger_energy_intervals")
        connection.execute("DROP TABLE IF EXISTS charger_energy_epochs")
        connection.execute("ALTER TABLE settings DROP COLUMN vehicle_color")
        connection.execute("PRAGMA user_version = 2")
        connection.execute(
            "INSERT INTO demo_command_journal VALUES (?, 'unlock', 'simulated', ?, ?)",
            ("legacy-command", "2026-10-02T11:00:00Z",
             "Desbloquear: simulación completada. No se envió ninguna orden al vehículo."),
        )
        before = {
            "manual": connection.execute(
                "SELECT id, started_at, kwh, cost_eur, tariff_eur_kwh, cost_cents FROM manual_charges"
            ).fetchall(),
            "demo_vehicle": connection.execute("SELECT * FROM demo_vehicle").fetchall(),
            "command": connection.execute("SELECT * FROM demo_command_journal").fetchall(),
        }

    migrated = Store(path)
    after = migrated.state("Pulse", "0.1")
    assert after["settings"]["pricing_method"] == "manual"
    assert {item["id"] for item in after["energy"]["sessions"]} == {
        "legacy-manual", "legacy-demo"
    }
    assert after["vehicle"] is not None
    assert after["command_history"][0]["id"] == "legacy-command"
    assert migrated.telemetry_latest() is None
    assert migrated.telemetry_history() == {"items": [], "total": 0, "limit": 100, "offset": 0}
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute(
            "SELECT id, started_at, kwh, cost_eur, tariff_eur_kwh, cost_cents FROM manual_charges"
        ).fetchall() == before["manual"]
        assert connection.execute("SELECT * FROM demo_vehicle").fetchall() == before["demo_vehicle"]
        assert connection.execute("SELECT * FROM demo_command_journal").fetchall() == before["command"]
        indexes = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='manual_charges'"
            )
        }
    assert "idx_manual_charges_started_at" in indexes


def test_schema_v3_migrates_charge_pricing_and_preserves_indexes(tmp_path, monkeypatch):
    path = tmp_path / "v3.sqlite3"
    with monkeypatch.context() as patcher:
        patcher.setattr(Store, "_migrate_v3_to_v4", lambda self, connection: None)
        patcher.setattr(Store, "_migrate_v4_to_v5", lambda self, connection: None)
        patcher.setattr(Store, "_migrate_v5_to_v6", lambda self, connection: None)
        patcher.setattr(Store, "_migrate_v6_to_v7", lambda self, connection: None)
        patcher.setattr(Store, "_migrate_v8_to_v9", lambda self, connection: None)
        patcher.setattr(Store, "_migrate_v9_to_v10", lambda self, connection: None)
        patcher.setattr(Store, "_migrate_v10_to_v11", lambda self, connection: None)
        patcher.setattr(Store, "_migrate_v11_to_v12", lambda self, connection: None)
        patcher.setattr(Store, "_migrate_v12_to_v13", lambda self, connection: None)
        patcher.setattr(Store, "_migrate_v17_to_v18", lambda self, connection: None)
        Store(path)
    with sqlite3.connect(path) as connection:
        downgrade_telemetry_to_v15(connection)
        connection.execute("DROP TABLE IF EXISTS charger_pricing_history")
        connection.execute("DROP TABLE IF EXISTS charger_energy_intervals")
        connection.execute("DROP TABLE IF EXISTS charger_energy_epochs")
        connection.execute("ALTER TABLE settings DROP COLUMN vehicle_color")
        connection.execute("PRAGMA user_version = 3")
        connection.execute(
            """INSERT INTO manual_charges
               (id, started_at, kwh, cost_eur, tariff_eur_kwh, cost_cents)
               VALUES ('legacy-v3', '2026-10-07T08:00:00.000000Z', 5, 1, 0.2, 100)"""
        )
        connection.execute(
            """INSERT INTO demo_charges
               (id, started_at, kwh, cost_eur, tariff_eur_kwh, cost_cents)
               VALUES ('legacy-v3-demo', '2026-10-06T08:00:00.000000Z', 8, 1.36, 0.17, 136)"""
        )

    migrated = Store(path)
    charge = migrated.get_charge("legacy-v3")
    assert charge == {
        "id": "legacy-v3",
        "started_at": "2026-10-07T08:00:00.000000Z",
        "ended_at": None,
        "kwh": 5.0,
        "cost_eur": 1.0,
        "source": "manual",
        "pricing_method": "manual",
        "tariff_eur_kwh": 0.2,
        "pricing": None,
    }
    assert migrated.get_settings()["pricing_method"] == "manual"
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='manual_charges'"
            )
        }
        assert "idx_manual_charges_started_at" in indexes
        demo_charge = connection.execute(
            "SELECT id, cost_eur, tariff_eur_kwh, cost_cents, ended_at, pricing_method, pricing_json "
            "FROM demo_charges WHERE id = 'legacy-v3-demo'"
        ).fetchone()
        assert demo_charge == ("legacy-v3-demo", 1.36, 0.17, 136, None, None, None)
        connection.execute(
            """INSERT INTO manual_charges
               (id, started_at, kwh, cost_eur, tariff_eur_kwh, cost_cents,
                ended_at, pricing_method, pricing_json)
               VALUES ('negative-pvpc-db-check', '2026-10-07T08:00:00Z', 1, -0.5,
                       -0.5, -50, '2026-10-07T09:00:00Z', 'pvpc', '{}')"""
        )


def test_schema_v5_migrates_to_v6_without_changing_existing_records(tmp_path):
    path = tmp_path / "v5.sqlite3"
    store = Store(path)
    charge = store.add_charge({"started_at": "2026-10-08T08:00:00Z", "kwh": 7})
    maintenance = store.add_maintenance({"title": "Revisión", "due_date": "2027-03-01"})
    before = store.export_data()

    # Recreate the v5 boundary while preserving representative pre-existing rows.
    with sqlite3.connect(path) as connection:
        downgrade_telemetry_to_v15(connection)
        connection.execute("DROP TABLE push_subscriptions")
        connection.execute("DROP TABLE abrp_preference")
        connection.execute("DROP TABLE observed_events")
        connection.execute("DROP TABLE IF EXISTS charger_pricing_history")
        connection.execute("DROP TABLE charger_energy_intervals")
        connection.execute("DROP TABLE charger_energy_epochs")
        connection.execute("DROP TRIGGER invalidate_charger_pricing_on_session_change")
        connection.execute("DROP TABLE charger_pricing")
        connection.execute("DROP TABLE charger_sessions")
        connection.execute("DROP TABLE charger_observations")
        connection.execute("ALTER TABLE settings DROP COLUMN vehicle_color")
        connection.execute("PRAGMA user_version = 5")

    migrated = Store(path)
    after = migrated.export_data()
    after.pop("exported_at")
    before.pop("exported_at")
    assert after == before
    assert migrated.get_charge(charge["id"])["id"] == charge["id"]
    assert any(
        item["id"] == maintenance["id"]
        for item in migrated.state("Pulse", "0.1")["maintenance"]
    )
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(push_subscriptions)")
        }
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='push_subscriptions'"
            )
        }
    assert columns == {
        "id", "owner_id", "endpoint_hash", "session_hash", "name", "ciphertext",
        "created_at", "updated_at", "expires_at", "revision", "last_attempt_at",
        "last_accepted_at", "last_result", "backup_alerts_enabled",
        "backup_alerts_consented_at",
    }
    assert "sqlite_autoindex_push_subscriptions_1" in indexes
    assert "idx_push_subscriptions_owner_updated" in indexes
    assert "idx_push_subscriptions_expiry" in indexes


def test_json_archive_v6_and_import_preserves_private_push_rows(tmp_path):
    from datetime import timedelta

    store = Store(tmp_path / "pulse.sqlite3")
    expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    row = store.upsert_push_subscription(
        "owner-a",
        "a" * 64,
        "b" * 64,
        "Teléfono",
        "gAAAA-fake-encrypted-text",
        expiry.isoformat(),
    )
    archive = store.export_data()
    assert SCHEMA_VERSION == 18
    assert archive["schema_version"] == EXPORT_VERSION == 15
    assert archive["charger_energy"] == {"epochs": [], "intervals": []}
    assert "push_subscriptions" not in archive
    assert row["ciphertext"] not in json.dumps(archive, ensure_ascii=False)

    store.add_charge({"started_at": "2026-10-08T08:00:00Z", "kwh": 2})
    assert store.import_data(archive) == {"ok": True, "mode": "unconfigured"}
    assert store.list_push_subscriptions("owner-a") == [row]

    future_schema = copy.deepcopy(archive)
    future_schema["schema_version"] = EXPORT_VERSION + 1
    with pytest.raises(DomainError, match="versión"):
        store.import_data(future_schema)


def test_schema_v11_migration_preserves_existing_charge_costs(tmp_path):
    path = tmp_path / "v11.sqlite3"
    store = Store(path)
    store.update_settings({"tariff_eur_kwh": 0.23})
    charge = store.add_charge({"started_at": "2026-10-08T08:00:00Z", "kwh": 7})
    with sqlite3.connect(path) as connection:
        downgrade_telemetry_to_v15(connection)
        connection.execute("DROP TABLE IF EXISTS charger_pricing_history")
        connection.execute("DROP TABLE charger_energy_intervals")
        connection.execute("DROP TABLE charger_energy_epochs")
        connection.execute("ALTER TABLE settings DROP COLUMN vehicle_color")
        connection.execute("PRAGMA user_version = 11")

    migrated = Store(path)
    assert SCHEMA_VERSION == 18
    restored = migrated.get_charge(charge["id"])
    assert restored["cost_eur"] == 1.61
    assert restored["tariff_eur_kwh"] == 0.23
    archive = migrated.export_data()
    assert archive["charger_energy"] == {"epochs": [], "intervals": []}
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_json_v9_import_preserves_charge_cost_and_exports_current(tmp_path):
    source = Store(tmp_path / "source.sqlite3")
    source.update_settings({"tariff_eur_kwh": 0.23})
    charge = source.add_charge({"started_at": "2026-10-08T08:00:00Z", "kwh": 7})
    legacy = source.export_data()
    legacy["schema_version"] = 9
    legacy["settings"].pop("vehicle_color")
    legacy.pop("charger_pricing_history")
    legacy.pop("charger_energy")

    destination = Store(tmp_path / "destination.sqlite3")
    assert destination.import_data(legacy) == {"ok": True, "mode": "unconfigured"}
    restored = destination.get_charge(charge["id"])
    assert restored["cost_eur"] == 1.61
    assert restored["tariff_eur_kwh"] == 0.23
    current = destination.export_data()
    assert current["schema_version"] == 15
    assert current["charger_energy"] == {"epochs": [], "intervals": []}

def test_schema_v3_archive_import_remains_compatible(tmp_path):
    source = make_store(tmp_path)
    source.set_demo(True)
    v3 = copy.deepcopy(source.export_data())
    v3["schema_version"] = 3
    v3.pop("charger")
    v3.pop("charger_pricing")
    v3.pop("observed_events")
    v3.pop("charger_pricing_history")
    v3.pop("charger_energy")
    v3["settings"].pop("pricing_method")
    v3["settings"].pop("vehicle_color")
    for charge in v3["charges"]:
        charge.pop("ended_at")
        charge.pop("pricing_method")
        charge.pop("pricing")

    destination = Store(tmp_path / "restore-v3.sqlite3")
    assert destination.import_data(v3) == {"ok": True, "mode": "demo"}
    restored = destination.export_data()
    assert restored["schema_version"] == 15
    assert restored["settings"]["pricing_method"] == "manual"
    assert len(restored["charges"]) == len(v3["charges"])
    assert all(charge["pricing_method"] is None for charge in restored["charges"] if charge["source"] == "demo")


def test_charge_crud_keeps_rate_snapshot_and_blocks_demo_charge_mutation(tmp_path):
    store = make_store(tmp_path)
    store.update_settings({"tariff_eur_kwh": 0.4})
    charge = store.add_charge({"started_at": "2026-10-07T08:00:00Z", "kwh": 10})
    store.update_settings({"tariff_eur_kwh": 1.2})
    date_only = store.update_charge(charge["id"], {"started_at": "2026-10-08T08:00:00Z"})
    assert date_only["cost_eur"] == 4.0
    assert date_only["tariff_eur_kwh"] == 0.4
    changed = store.update_charge(charge["id"], {"kwh": 12})
    assert changed["cost_eur"] == 4.8
    assert changed["tariff_eur_kwh"] == 0.4
    repriced = store.update_charge(charge["id"], {"tariff_eur_kwh": 0.5})
    assert repriced["cost_eur"] == 6.0
    store.set_demo(True)
    with pytest.raises(DomainError) as demo_edit:
        store.update_charge("demo-charge-1", {"kwh": 20})
    assert demo_edit.value.status == 404
    with pytest.raises(DomainError) as demo_delete:
        store.delete_charge("demo-charge-1")
    assert demo_delete.value.status == 404
    assert store.delete_charge(charge["id"]) == {"ok": True, "id": charge["id"]}


def test_maintenance_crud_uses_stored_source_and_compatibility_method(tmp_path):
    store = make_store(tmp_path)
    manual = store.add_maintenance({"title": "Revisar", "due_date": "2027-01-01"})
    updated = store.update_maintenance(
        manual["id"], {"title": "Revisar frenos", "due_date": "2027-02-01"}
    )
    assert updated == {
        "id": manual["id"], "title": "Revisar frenos", "due_date": "2027-02-01", "completed": False
    }
    assert store.complete_maintenance(manual["id"], True) == {
        "ok": True, "id": manual["id"], "completed": True
    }
    store.set_demo(True)
    assert store.update_maintenance("demo-maintenance-1", {"completed": True})["completed"] is True
    assert store.delete_maintenance("demo-maintenance-1") == {
        "ok": True, "id": "demo-maintenance-1"
    }
    store.set_demo(False)
    assert store.state("Pulse", "0.1")["maintenance"] == [{**updated, "completed": True}]


def test_state_caps_charge_preview_but_totals_and_pagination_cover_full_history(tmp_path):
    store = make_store(tmp_path)
    for index in range(105):
        store.add_charge(
            {
                "started_at": f"2026-10-01T00:{index // 60:02d}:{index % 60:02d}Z",
                "kwh": 1,
            }
        )

    state = store.state("Pulse", "0.1")
    assert len(state["energy"]["sessions"]) == 100
    assert state["energy"]["totals"]["kwh"] == 105
    page = store.list_charges(limit=10, offset=100)
    assert page["total"] == 105
    assert len(page["items"]) == 5
    assert page["limit"] == 10 and page["offset"] == 100
    with pytest.raises(DomainError):
        store.list_charges(limit=101)


def test_initialization_is_explicit_and_reads_do_not_run_schema_setup(tmp_path, monkeypatch):
    path = tmp_path / "missing" / "nested" / "pulse.sqlite3"
    store = Store(path)
    monkeypatch.setattr(store, "_ensure_schema", lambda _: pytest.fail("schema ran on a read"))
    writer = sqlite3.connect(path, timeout=0.05, isolation_level=None)
    try:
        writer.execute("BEGIN IMMEDIATE")
        # WAL readers remain available while another connection owns the writer lock.
        store.state("Pulse", "0.1")
        store.export_data()
    finally:
        writer.rollback()
        writer.close()
    assert path.exists()


def test_concurrent_store_reads_and_writes_remain_consistent(tmp_path):
    store = make_store(tmp_path)
    dates = [f"2026-10-07T08:{index // 60:02d}:{index % 60:02d}Z" for index in range(24)]

    def write(index):
        return store.add_charge({"started_at": dates[index], "kwh": index + 1})

    def read(_index):
        state = store.state("Pulse", "0.1")
        assert state["energy"]["totals"]["kwh"] >= 0
        return state

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(write, index) for index in range(24)]
        futures += [executor.submit(read, index) for index in range(48)]
        results = [future.result(timeout=20) for future in futures]

    final = store.state("Pulse", "0.1")
    assert len(final["energy"]["sessions"]) == 24
    assert final["energy"]["totals"]["kwh"] == sum(range(1, 25))
    assert sum(1 for result in results if isinstance(result, dict) and "source" in result) == 24


def test_sqlite_version_guard_accepts_supported_releases_and_rejects_unpatched_runtime(monkeypatch):
    monkeypatch.setattr(domain.sqlite3, "sqlite_version_info", (3, 53, 1))
    domain._require_supported_sqlite()
    monkeypatch.setattr(domain.sqlite3, "sqlite_version_info", (3, 44, 6))
    domain._require_supported_sqlite()
    monkeypatch.setattr(domain.sqlite3, "sqlite_version_info", (3, 50, 7))
    domain._require_supported_sqlite()
    monkeypatch.setattr(domain.sqlite3, "sqlite_version_info", (3, 50, 6))
    with pytest.raises(RuntimeError, match="Runtime detectado"):
        domain._require_supported_sqlite()


def test_import_validates_entire_document_before_replacing_data(tmp_path):
    store = make_store(tmp_path)
    store.add_charge({"started_at": "2026-10-07T08:00:00Z", "kwh": 9})
    before = store.state("Pulse", "0.1")
    document = copy.deepcopy(store.export_data())
    document["settings"]["vehicle_name"] = "Este cambio no debe aplicarse"
    document["maintenance"].append(
        {"id": "bad-item", "title": "Elemento inválido", "due_date": "2027-01-01", "completed": 1, "source": "manual"}
    )

    with pytest.raises(DomainError):
        store.import_data(document)

    assert Store(tmp_path / "pulse.sqlite3").state("Pulse", "0.1") == before


def test_export_import_round_trip_replaces_pulse_data(tmp_path):
    store = make_store(tmp_path)
    store.set_demo(True)
    store.add_charge({"started_at": "2026-10-07T08:00:00Z", "kwh": 11.25})
    exported = json.dumps(store.export_data(), allow_nan=False)

    store.set_demo(False)
    store.add_maintenance({"title": "Dato que se reemplazará", "due_date": "2028-02-01"})
    restored = store.import_data(exported)

    assert restored == {"ok": True, "mode": "demo"}
    exported_document = json.loads(exported)
    restored_document = Store(tmp_path / "pulse.sqlite3").export_data()
    assert isinstance(restored_document.pop("exported_at"), str)
    exported_document.pop("exported_at")
    assert restored_document == exported_document


def test_import_accepts_version_one_export_and_reexports_version_four(tmp_path):
    source = make_store(tmp_path)
    source.set_demo(True)
    source.add_charge({"started_at": "2026-10-07T08:00:00Z", "kwh": 6})
    document = source.export_data()
    document["schema_version"] = 1
    document.pop("charger")
    document.pop("charger_pricing")
    document.pop("observed_events")
    document.pop("charger_pricing_history")
    document.pop("charger_energy")
    document.pop("command_history")
    document.pop("demo_vehicle")
    document.pop("widget_profiles")
    document.pop("telemetry_snapshots")
    document["settings"].pop("pricing_method")
    document["settings"].pop("vehicle_color")
    for charge in document["charges"]:
        charge.pop("tariff_eur_kwh")
        charge.pop("ended_at")
        charge.pop("pricing_method")
        charge.pop("pricing")

    destination = Store(tmp_path / "second.sqlite3")
    assert destination.import_data(document) == {"ok": True, "mode": "demo"}
    restored = destination.export_data()
    assert restored["schema_version"] == 15
    assert restored["mode"] == "demo"
    assert len(restored["charges"]) == len(document["charges"])
    assert all(item["tariff_eur_kwh"] is None for item in restored["charges"])
    assert restored["demo_vehicle"]["locked"] is True
    assert restored["command_history"] == []
    assert restored["widget_profiles"] == []
    assert restored["telemetry_snapshots"] == []


def test_v2_import_rejects_physical_or_inconsistent_demo_data_atomically(tmp_path):
    store = make_store(tmp_path)
    store.set_demo(True)
    store.command("lock", confirmed=True)
    before = store.state("Pulse", "0.1")
    exported = store.export_data()
    exported["schema_version"] = 2
    exported.pop("charger")
    exported.pop("charger_pricing")
    exported.pop("observed_events")
    exported.pop("charger_pricing_history")
    exported.pop("charger_energy")
    exported.pop("widget_profiles")
    exported.pop("telemetry_snapshots")
    exported["settings"].pop("pricing_method")
    exported["settings"].pop("vehicle_color")
    for charge in exported["charges"]:
        charge.pop("ended_at")
        charge.pop("pricing_method")
        charge.pop("pricing")

    assert store.validate_import(exported) == {"ok": True}

    bad_status = copy.deepcopy(exported)
    bad_status["command_history"][0]["status"] = "executed"
    with pytest.raises(DomainError):
        store.import_data(bad_status)

    bad_message = copy.deepcopy(exported)
    bad_message["command_history"][0]["message"] = "Orden enviada al vehículo."
    with pytest.raises(DomainError):
        store.import_data(bad_message)

    bad_cost = copy.deepcopy(exported)
    bad_cost["charges"][0]["cost_eur"] += 1
    with pytest.raises(DomainError):
        store.import_data(bad_cost)

    wrong_mode = copy.deepcopy(exported)
    wrong_mode["mode"] = "unconfigured"
    with pytest.raises(DomainError):
        store.import_data(wrong_mode)

    duplicate_field = json.dumps(exported, allow_nan=False).replace(
        '"schema_version": 2', '"schema_version": 2, "schema_version": 2'
    )
    with pytest.raises(DomainError, match="duplicados"):
        store.import_data(duplicate_field)

    assert store.state("Pulse", "0.1") == before


def test_v3_demo_restore_requires_the_persisted_demo_vehicle_snapshot(tmp_path):
    store = make_store(tmp_path)
    store.set_demo(True)
    store.command("unlock", confirmed=True)
    before = store.export_data()
    incomplete = copy.deepcopy(before)
    incomplete["schema_version"] = 3
    incomplete.pop("charger")
    incomplete.pop("charger_pricing")
    incomplete.pop("observed_events")
    incomplete.pop("charger_pricing_history")
    incomplete.pop("charger_energy")
    incomplete["settings"].pop("pricing_method")
    incomplete["settings"].pop("vehicle_color")
    for charge in incomplete["charges"]:
        charge.pop("ended_at")
        charge.pop("pricing_method")
        charge.pop("pricing")
    incomplete.pop("widget_profiles")
    incomplete.pop("telemetry_snapshots")
    incomplete.pop("demo_vehicle")

    with pytest.raises(DomainError):
        store.import_data(incomplete)
    after = store.export_data()
    before.pop("exported_at")
    after.pop("exported_at")
    assert after == before


@pytest.mark.parametrize(
    "settings",
    [
        {"tariff_eur_kwh": True},
        {"tariff_eur_kwh": float("nan")},
        {"tariff_eur_kwh": 5.01},
        {"vehicle_name": "\n"},
        {"api_key": "do-not-store"},
    ],
)
def test_invalid_settings_are_rejected(tmp_path, settings):
    with pytest.raises(DomainError):
        make_store(tmp_path).update_settings(settings)


@pytest.mark.parametrize(
    "values",
    [
        {"started_at": "2026-10-07T08:00:00Z", "kwh": True},
        {"started_at": "2026-10-07T08:00:00Z", "kwh": float("nan")},
        {"started_at": "2026-10-07T08:00:00", "kwh": 8},
        {"started_at": "invalid", "kwh": 8},
        {"started_at": "2026-10-07T08:00:00Z", "kwh": 0},
        {"started_at": "2026-10-07T08:00:00Z", "kwh": 8, "source": "demo"},
    ],
)
def test_invalid_manual_charges_are_rejected(tmp_path, values):
    with pytest.raises(DomainError):
        make_store(tmp_path).add_charge(values)


def test_import_rejects_non_finite_json_unknown_fields_and_oversize_documents(tmp_path):
    store = make_store(tmp_path)
    exported = store.export_data()
    with pytest.raises(DomainError):
        store.import_data('{"value": NaN}')

    with pytest.raises(DomainError):
        store.import_data({**exported, "secret": "never accepted"})

    with pytest.raises(DomainError):
        store.import_data(" " * 1_000_001)
