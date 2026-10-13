import copy
import base64
from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from pulse.domain import DomainError, Store
from schema_helpers import downgrade_telemetry_to_v15


PROFILE_INPUT = {"name": "Panel semanal", "fields": ["battery_pct", "charging"]}


def make_store(tmp_path, filename="pulse.sqlite3"):
    fixed = datetime(2026, 10, 8, 15, 0, tzinfo=timezone.utc)
    return Store(tmp_path / filename, clock=lambda: fixed)


def test_profile_contract_defaults_owner_scope_and_revision(tmp_path):
    store = make_store(tmp_path)
    profile = store.create_widget_profile("user-a", PROFILE_INPUT)

    assert set(profile) == {
        "id", "name", "fields", "appearance", "privacy_mode", "max_age_minutes",
        "revision", "created_at", "updated_at",
    }
    assert profile["name"] == "Panel semanal"
    assert profile["fields"] == ["battery_pct", "charging"]
    assert profile["appearance"] == "system"
    assert profile["privacy_mode"] == "masked"
    assert profile["max_age_minutes"] == 60
    assert profile["revision"] == 1
    assert "owner_id" not in profile

    other = store.create_widget_profile("user-b", {"name": "Oscuro", "fields": ["range_km"]})
    listed = store.list_widget_profiles("user-a")
    assert set(listed) == {"items"}
    assert listed["items"] == [profile]
    assert store.list_widget_profiles("user-b")["items"] == [other]

    changed = store.update_widget_profile(
        "user-a", profile["id"], {"privacy_mode": "visible", "max_age_minutes": 120}
    )
    assert changed["privacy_mode"] == "visible"
    assert changed["max_age_minutes"] == 120
    assert changed["revision"] == 2
    assert changed["created_at"] == profile["created_at"]
    assert changed["updated_at"] == profile["updated_at"]


@pytest.mark.parametrize(
    "data",
    [
        {"name": " ", "fields": ["battery_pct"]},
        {"name": "x" * 61, "fields": ["battery_pct"]},
        {"name": "x", "fields": []},
        {"name": "x", "fields": ["battery_pct", "battery_pct"]},
        {"name": "x", "fields": ["vin"]},
        {"name": "x", "fields": ["battery_pct"], "owner_id": "another-user"},
        {"name": "x", "fields": ["battery_pct"], "appearance": "auto"},
        {"name": "x", "fields": ["battery_pct"], "privacy_mode": "hidden"},
        {"name": "x", "fields": ["battery_pct"], "max_age_minutes": True},
        {"name": "x", "fields": ["battery_pct"], "max_age_minutes": 14},
        {"name": "x", "fields": ["battery_pct"], "max_age_minutes": 121},
    ],
)
def test_profile_rejects_unrecognized_or_invalid_values(tmp_path, data):
    with pytest.raises(DomainError):
        make_store(tmp_path).create_widget_profile("user-a", data)


def test_vehicle_and_energy_profiles_derive_one_family_from_one_to_three_fields(tmp_path):
    store = make_store(tmp_path)
    vehicle = store.create_widget_profile(
        "user-a", {"name": "Cierre", "fields": ["locked", "charging"]}
    )
    three_fields = store.create_widget_profile(
        "user-a", {"name": "Vehículo", "fields": ["battery_pct", "range_km", "charging"]}
    )
    energy = store.create_widget_profile(
        "user-a", {"name": "Registros", "fields": ["energy_kwh", "energy_cost_eur"]}
    )
    assert vehicle["fields"] == ["locked", "charging"]
    assert len(three_fields["fields"]) == 3
    assert energy["fields"] == ["energy_kwh", "energy_cost_eur"]

    for fields in (
        ["battery_pct", "energy_kwh"],
        ["energy_cost_eur", "locked"],
        ["battery_pct", "battery_pct"],
        ["energy_kwh", "energy_cost_eur", "energy_kwh", "energy_cost_eur"],
    ):
        with pytest.raises(DomainError):
            store.create_widget_profile("user-a", {"name": "Inválido", "fields": fields})
    with pytest.raises(DomainError):
        store.create_widget_profile(
            "user-a", {"name": "Familia editable", "fields": ["energy_kwh"], "kind": "energy"}
        )

    switched = store.update_widget_profile(
        "user-a", vehicle["id"], {"fields": ["energy_kwh"]}
    )
    assert switched["fields"] == ["energy_kwh"]
    with pytest.raises(DomainError):
        store.update_widget_profile(
            "user-a", switched["id"], {"fields": ["energy_kwh", "charging"]}
        )


def test_profile_limit_is_per_owner_and_cross_owner_mutations_are_not_found(tmp_path):
    store = make_store(tmp_path)
    created = [
        store.create_widget_profile("user-a", {"name": f"Perfil {i}", "fields": ["charging"]})
        for i in range(5)
    ]
    store.create_widget_profile("user-b", {"name": "B", "fields": ["range_km"]})
    with pytest.raises(DomainError) as too_many:
        store.create_widget_profile("user-a", {"name": "Sexto", "fields": ["charging"]})
    assert too_many.value.status == 409

    for operation in (
        lambda: store.update_widget_profile("user-b", created[0]["id"], {"name": "No"}),
        lambda: store.delete_widget_profile("user-b", created[0]["id"]),
    ):
        with pytest.raises(DomainError) as not_found:
            operation()
        assert not_found.value.status == 404
    assert len(store.list_widget_profiles("user-a")["items"]) == 5


def test_owner_scoped_export_restore_preserves_other_users_and_rejects_foreign_rows(tmp_path):
    source = make_store(tmp_path, "source.sqlite3")
    own = source.create_widget_profile("user-a", PROFILE_INPUT)
    source.create_widget_profile("user-b", {"name": "B", "fields": ["range_km"]})
    own_export = source.export_data(owner_id="user-a")
    assert [item["owner_id"] for item in own_export["widget_profiles"]] == ["user-a"]

    destination = make_store(tmp_path, "destination.sqlite3")
    destination.create_widget_profile("user-a", {"name": "Reemplazado", "fields": ["charging"]})
    other = destination.create_widget_profile(
        "user-b", {"name": "Debe conservarse", "fields": ["range_km"]}
    )
    destination.import_data(own_export, owner_id="user-a")
    assert destination.list_widget_profiles("user-a")["items"] == [own]
    assert destination.list_widget_profiles("user-b")["items"] == [other]

    before = destination.export_data()
    foreign = copy.deepcopy(own_export)
    foreign["widget_profiles"][0]["owner_id"] = "user-b"
    with pytest.raises(DomainError) as rejected:
        destination.import_data(foreign, owner_id="user-a")
    assert rejected.value.status == 400
    assert destination.export_data() == before


def test_validate_import_is_read_only_and_applies_owner_scope(tmp_path):
    source = make_store(tmp_path, "validation-source.sqlite3")
    source.create_widget_profile("user-a", PROFILE_INPUT)
    document = source.export_data(owner_id="user-a")

    destination = make_store(tmp_path, "validation-destination.sqlite3")
    destination.create_widget_profile("user-a", {"name": "Own", "fields": ["charging"]})
    destination.create_widget_profile("user-b", {"name": "Other", "fields": ["range_km"]})
    before = destination.export_data()
    assert destination.validate_import(document, owner_id="user-a") == {"ok": True}
    assert destination.export_data() == before

    with pytest.raises(DomainError):
        destination.validate_import(document, owner_id="user-b")
    assert destination.export_data() == before


def test_legacy_import_with_explicit_owner_only_clears_that_users_profiles(tmp_path):
    source = make_store(tmp_path, "legacy-source.sqlite3")
    legacy = source.export_data()
    legacy["schema_version"] = 1
    legacy.pop("charger")
    legacy.pop("charger_pricing")
    legacy.pop("observed_events")
    legacy.pop("charger_pricing_history")
    legacy.pop("charger_energy")
    legacy["settings"].pop("pricing_method")
    legacy["settings"].pop("vehicle_color")
    legacy.pop("command_history")
    legacy.pop("demo_vehicle", None)
    legacy.pop("widget_profiles")
    legacy.pop("telemetry_snapshots")

    destination = make_store(tmp_path, "legacy-destination.sqlite3")
    destination.create_widget_profile("user-a", PROFILE_INPUT)
    preserved = destination.create_widget_profile(
        "user-b", {"name": "B keep", "fields": ["range_km"]}
    )
    destination.import_data(legacy, owner_id="user-a")
    assert destination.list_widget_profiles("user-a")["items"] == []
    assert destination.list_widget_profiles("user-b")["items"] == [preserved]


def test_version_three_import_validates_profile_owner_count_and_fields_atomically(tmp_path):
    store = make_store(tmp_path)
    store.create_widget_profile("user-a", PROFILE_INPUT)
    before = store.export_data()
    invalid = copy.deepcopy(before)
    template = copy.deepcopy(invalid["widget_profiles"][0])
    invalid["widget_profiles"] = [
        {**template, "id": f"{number:032x}"} for number in range(1, 7)
    ]
    with pytest.raises(DomainError):
        store.import_data(invalid)
    invalid = copy.deepcopy(before)
    invalid["widget_profiles"][0]["owner_id"] = "\n"
    with pytest.raises(DomainError):
        store.import_data(invalid)
    assert store.export_data() == before


def test_export_v7_round_trips_new_fields_but_v6_rejects_them_atomically(tmp_path):
    source = make_store(tmp_path, "v7-source.sqlite3")
    profile = source.create_widget_profile(
        "user-a", {"name": "Energía", "fields": ["energy_kwh", "energy_cost_eur"]}
    )
    archive = source.export_data(owner_id="user-a")
    assert archive["schema_version"] == 15

    destination = make_store(tmp_path, "v7-destination.sqlite3")
    destination.import_data(archive, owner_id="user-a")
    assert destination.list_widget_profiles("user-a")["items"] == [profile]

    legacy = copy.deepcopy(archive)
    legacy["schema_version"] = 6
    legacy["settings"].pop("vehicle_color")
    legacy.pop("charger_pricing")
    legacy.pop("observed_events")
    legacy.pop("charger_pricing_history")
    legacy.pop("charger_energy")
    before = destination.export_data()
    with pytest.raises(DomainError):
        destination.validate_import(legacy, owner_id="user-a")
    with pytest.raises(DomainError):
        destination.import_data(legacy, owner_id="user-a")
    assert destination.export_data() == before

    old_source = make_store(tmp_path, "v6-source.sqlite3")
    old_profile = old_source.create_widget_profile("user-a", PROFILE_INPUT)
    old_archive = old_source.export_data(owner_id="user-a")
    old_archive["schema_version"] = 6
    old_archive["settings"].pop("vehicle_color")
    old_archive.pop("charger_pricing")
    old_archive.pop("observed_events")
    old_archive.pop("charger_pricing_history")
    old_archive.pop("charger_energy")
    old_destination = make_store(tmp_path, "v6-destination.sqlite3")
    old_destination.import_data(old_archive, owner_id="user-a")
    assert old_destination.list_widget_profiles("user-a")["items"] == [old_profile]


def test_v7_to_current_migration_preserves_widget_profiles_and_grants(tmp_path):
    path = tmp_path / "version-guard.sqlite3"
    store = make_store(tmp_path, path.name)
    profile = store.create_widget_profile("user-a", PROFILE_INPUT)
    pairing = store.create_widget_pairing("user-a", profile["id"])
    installation_id = base64.urlsafe_b64encode(bytes(range(32))).decode("ascii").rstrip("=")
    grant = store.register_widget_grant(
        {
            "issuer": "https://issuer.example.invalid",
            "subject": "user-a",
            "client_id": "pulse-native-test",
        },
        pairing["pairing_code"],
        installation_id,
        "Synthetic tablet",
    )
    with sqlite3.connect(path) as connection:
        downgrade_telemetry_to_v15(connection)
        connection.execute("DROP TABLE abrp_preference")
        connection.execute("DROP TABLE observed_events")
        connection.execute("DROP TABLE IF EXISTS charger_pricing_history")
        connection.execute("DROP TABLE charger_energy_intervals")
        connection.execute("DROP TABLE charger_energy_epochs")
        connection.execute("DROP TRIGGER invalidate_charger_pricing_on_session_change")
        connection.execute("DROP TABLE charger_pricing")
        connection.execute("ALTER TABLE settings DROP COLUMN vehicle_color")
        connection.execute("DROP TABLE IF EXISTS manual_charge_requests")
        tables_before = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
        connection.execute("PRAGMA user_version = 7")

    migrated = Store(path, clock=lambda: datetime(2026, 10, 8, 15, 0, tzinfo=timezone.utc))
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 19
        tables_after = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
    assert set(tables_after) == set(tables_before) | {
        ("abrp_preference",), ("charger_pricing",), ("observed_events",),
            ("charger_energy_epochs",), ("charger_energy_intervals",),
            ("charger_pricing_history",),
            ("backup_alert_runtime",), ("backup_alert_observations",),
            ("backup_alert_events",), ("backup_alert_deliveries",),
            ("manual_charge_requests",),
        }
    assert migrated.list_widget_profiles("user-a")["items"] == [profile]
    assert migrated.widget_grant_context(
        grant["widget_token"], grant["grant"]["id"]
    )["profile"] == profile


def test_energy_widget_context_aggregates_only_manual_charges_without_telemetry(
    tmp_path, monkeypatch
):
    fixed = datetime(2026, 10, 8, 15, 0, tzinfo=timezone.utc)
    store = Store(tmp_path / "energy-context.sqlite3", clock=lambda: fixed)
    profile = store.create_widget_profile(
        "user-a",
        {
            "name": "Energía registrada",
            "fields": ["energy_kwh", "energy_cost_eur"],
            "privacy_mode": "visible",
        },
    )
    pairing = store.create_widget_pairing("user-a", profile["id"])
    installation_id = base64.urlsafe_b64encode(bytes(range(32))).decode("ascii").rstrip("=")
    grant = store.register_widget_grant(
        {
            "issuer": "https://issuer.example.invalid",
            "subject": "user-a",
            "client_id": "pulse-native-test",
        },
        pairing["pairing_code"],
        installation_id,
        "Synthetic tablet",
    )

    at_start = fixed - timedelta(days=30)
    at_start_id = store.add_charge({"started_at": at_start.isoformat(), "kwh": 2})["id"]
    at_end_id = store.add_charge({"started_at": fixed.isoformat(), "kwh": 1.25})["id"]
    store.add_charge(
        {"started_at": (at_start - timedelta(seconds=1)).isoformat(), "kwh": 50}
    )
    store.add_charge(
        {"started_at": (fixed + timedelta(seconds=1)).isoformat(), "kwh": 60}
    )
    store.set_demo(True)
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE manual_charges SET cost_eur = ?, cost_cents = NULL WHERE id = ?",
            (0.75, at_start_id),
        )
        connection.execute(
            "UPDATE manual_charges SET cost_eur = ?, cost_cents = NULL WHERE id = ?",
            (-1.0, at_end_id),
        )
        connection.execute(
            """INSERT INTO demo_charges (id, started_at, kwh, cost_eur)
               VALUES ('demo-fixture', ?, 500, 2000)""",
            (fixed.isoformat(),),
        )

    store.record_snapshot(
        {
            "source": "MATE-API B10ReadClient",
            "observed_at": fixed.isoformat(),
            "provider_received_at": fixed.isoformat(),
            "vehicle": {
                "battery_pct": 50, "range_km": 200, "odometer_km": 10,
                "locked": True, "climate_on": False, "charging": False,
            },
            "field_sources": {
                "battery_pct": "precise_soc_percent", "range_km": "expected_range_km",
                "odometer_km": "odometer_km", "locked": None, "climate_on": None,
                "charging": None,
            },
        }
    )
    monkeypatch.setattr(store, "energy_analytics", lambda *_args, **_kwargs: pytest.fail("analytics llamadas"))
    monkeypatch.setattr(store, "telemetry_history", lambda *_args, **_kwargs: pytest.fail("historial llamado"))
    monkeypatch.setattr(store, "list_charges", lambda *_args, **_kwargs: pytest.fail("cargas listadas"))
    monkeypatch.setattr("pulse.domain._telemetry_snapshot_item", lambda *_args: pytest.fail("telemetría leída"))
    monkeypatch.setattr("pulse.domain._read_demo_vehicle", lambda *_args: pytest.fail("demo leída"))

    context = store.widget_snapshot_context(grant["widget_token"], grant["grant"]["id"])
    assert context["profile"] == profile
    assert "telemetry" not in context and "demo_vehicle" not in context and "mode" not in context
    assert context["energy"] == {
        "calculated_at": "2026-10-08T15:00:00.000000Z",
        "window_start": "2026-09-08T15:00:00.000000Z",
        "window_end": "2026-10-08T15:00:00.000000Z",
        "sessions": 2,
        "kwh": 3.25,
        "cost_eur": -0.25,
    }
