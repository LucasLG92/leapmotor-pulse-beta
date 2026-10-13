import copy
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from pulse.domain import DomainError, Store
from schema_helpers import downgrade_backup_alerts_to_v17, downgrade_telemetry_to_v15


SOURCE = "MATE-API B10ReadClient"
SECURITY_CODE_FIELDS = ("vehicle_security_code", "sentry_code")


def create_representative_schema16_fixture(tmp_path):
    """Build a portable v16-shaped DB by removing only the v17 security codes."""
    path = tmp_path / "schema16-representative.sqlite3"
    fixed = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
    store = Store(path, clock=lambda: fixed)
    store.update_settings({"vehicle_name": "B10 de migración", "tariff_eur_kwh": 0.23})
    settings = store.get_settings()
    charge = store.add_charge({"started_at": "2026-10-09T10:00:00Z", "kwh": 8})
    maintenance = store.add_maintenance({"title": "Revisión histórica", "due_date": "2027-01-20"})
    profile = store.create_widget_profile(
        "owner-migration", {"name": "Batería", "fields": ["battery_pct"]}
    )
    incoming = snapshot()
    incoming["observed_at"] = "2026-10-09T11:59:00Z"
    incoming["provider_received_at"] = "2026-10-09T11:59:05Z"
    incoming["vehicle"].update(vehicle_security_code=211, sentry_code=12)
    incoming["field_sources"].update(
        vehicle_security_code="vehicle_security_code", sentry_code="sentry_code"
    )
    telemetry = store.record_snapshot(incoming)

    # Schema 16 had all current telemetry fields except these two nullable v17
    # columns and their source keys. Leave every other table and column intact.
    legacy_telemetry = copy.deepcopy(telemetry)
    for field in SECURITY_CODE_FIELDS:
        legacy_telemetry["vehicle"].pop(field)
        legacy_telemetry["field_sources"].pop(field)
    with sqlite3.connect(path) as connection:
        downgrade_backup_alerts_to_v17(connection)
        encoded_sources = connection.execute(
            "SELECT field_sources_json FROM telemetry_snapshots WHERE id = ?",
            (telemetry["id"],),
        ).fetchone()[0]
        sources = json.loads(encoded_sources)
        for field in SECURITY_CODE_FIELDS:
            sources.pop(field)
            connection.execute(f"ALTER TABLE telemetry_snapshots DROP COLUMN {field}")
        connection.execute(
            "UPDATE telemetry_snapshots SET field_sources_json = ? WHERE id = ?",
            (json.dumps(sources, allow_nan=False, separators=(",", ":")), telemetry["id"]),
        )
        connection.execute("DROP TABLE IF EXISTS manual_charge_requests")
        connection.execute("DROP TABLE IF EXISTS manual_journey_requests")
        connection.execute("DROP TABLE IF EXISTS manual_journeys")
        connection.execute("PRAGMA user_version = 16")

    return path, {
        "schema_version": 16,
        "settings": settings,
        "charge": charge,
        "maintenance": maintenance,
        "profile": profile,
        "telemetry": legacy_telemetry,
    }


def snapshot(**overrides):
    value = {
        "source": SOURCE,
        "observed_at": "2026-10-08T16:00:00+01:00",
        "provider_received_at": "2026-10-08T15:00:30Z",
        "vehicle": {
            "battery_pct": 74.5,
            "range_km": 321,
            "odometer_km": 12500.25,
            "interior_temperature_c": 21.5,
            "battery_min_temperature_c": 18.25,
            "front_left_pressure_kpa": 245.0,
            "front_right_pressure_kpa": 246.0,
            "rear_left_pressure_kpa": 247.0,
            "rear_right_pressure_kpa": 248.0,
            "vehicle_security_code": None,
            "sentry_code": None,
            "locked": None,
            "climate_on": None,
            "charging": None,
        },
        "field_sources": {
            "battery_pct": "precise_soc_percent",
            "range_km": "expected_range_km",
            "odometer_km": "odometer_km",
            "interior_temperature_c": "interior_temperature_c",
            "battery_min_temperature_c": "battery_min_temperature_c",
            "front_left_pressure_kpa": "front_left_pressure_kpa",
            "front_right_pressure_kpa": "front_right_pressure_kpa",
            "rear_left_pressure_kpa": "rear_left_pressure_kpa",
            "rear_right_pressure_kpa": "rear_right_pressure_kpa",
            "vehicle_security_code": None,
            "sentry_code": None,
            "locked": None,
            "climate_on": None,
            "charging": None,
        },
    }
    value.update(overrides)
    return value


def make_store(tmp_path, *, clock=None, filename="pulse.sqlite3"):
    if clock is None:
        fixed = datetime(2026, 10, 8, 15, 1, tzinfo=timezone.utc)
        clock = lambda: fixed
    return Store(tmp_path / filename, clock=clock)


def test_snapshot_is_normalized_stamped_and_exposed_as_a_bounded_history(tmp_path):
    store = make_store(tmp_path)
    assert store.telemetry_latest() is None

    item = store.record_snapshot(snapshot())
    assert set(item) == {
        "id", "source", "observed_at", "provider_received_at", "server_received_at",
        "vehicle", "field_sources",
    }
    assert item["source"] == SOURCE
    assert item["observed_at"] == "2026-10-08T15:00:00.000000Z"
    assert item["provider_received_at"] == "2026-10-08T15:00:30.000000Z"
    assert item["server_received_at"] == "2026-10-08T15:01:00.000000Z"
    assert item["vehicle"]["battery_pct"] == 74.5
    assert item["vehicle"]["front_left_pressure_kpa"] == 245.0
    assert item["field_sources"]["interior_temperature_c"] == "interior_temperature_c"
    assert item["vehicle"]["locked"] is None
    assert store.telemetry_latest() == item
    assert store.telemetry_history() == {
        "items": [item], "total": 1, "limit": 100, "offset": 0,
    }


def test_store_preserves_future_and_inconsistent_provider_timestamps(tmp_path):
    fixed = datetime(2026, 10, 8, 15, 1, tzinfo=timezone.utc)
    store = make_store(tmp_path, clock=lambda: fixed)
    item = store.record_snapshot(
        snapshot(
            observed_at="2035-01-01T00:00:00Z",
            provider_received_at="2020-01-01T00:00:00-05:00",
        )
    )
    assert item["observed_at"] == "2035-01-01T00:00:00.000000Z"
    assert item["provider_received_at"] == "2020-01-01T05:00:00.000000Z"
    assert item["server_received_at"] == "2026-10-08T15:01:00.000000Z"


def test_store_accepts_integer_security_code_boundaries_and_keeps_source_names(tmp_path):
    store = make_store(tmp_path)
    incoming = snapshot()
    incoming["vehicle"].update(vehicle_security_code=0, sentry_code=255)
    incoming["field_sources"].update(
        vehicle_security_code="vehicle_security_code", sentry_code="sentry_code"
    )

    item = store.record_snapshot(incoming)
    assert item["vehicle"]["vehicle_security_code"] == 0
    assert item["vehicle"]["sentry_code"] == 255
    assert item["field_sources"]["vehicle_security_code"] == "vehicle_security_code"
    assert item["field_sources"]["sentry_code"] == "sentry_code"
    archive = store.export_data()
    restored = make_store(tmp_path, filename="security-codes.sqlite3")
    restored.import_data(archive)
    assert restored.telemetry_latest() == item


@pytest.mark.parametrize(
    "field,value,source",
    [
        ("vehicle_security_code", True, "vehicle_security_code"),
        ("vehicle_security_code", 1.0, "vehicle_security_code"),
        ("vehicle_security_code", 1.5, "vehicle_security_code"),
        ("vehicle_security_code", "1", "vehicle_security_code"),
        ("vehicle_security_code", float("nan"), "vehicle_security_code"),
        ("vehicle_security_code", float("inf"), "vehicle_security_code"),
        ("vehicle_security_code", -1, "vehicle_security_code"),
        ("vehicle_security_code", 256, "vehicle_security_code"),
        ("vehicle_security_code", 1, "sentry_code"),
        ("sentry_code", 1, "vehicle_security_code"),
        ("sentry_code", None, "sentry_code"),
        ("sentry_code", 1, None),
    ],
)
def test_store_rejects_non_integer_or_unmatched_security_codes_atomically(
    tmp_path, field, value, source
):
    store = make_store(tmp_path)
    incoming = snapshot()
    incoming["vehicle"][field] = value
    incoming["field_sources"][field] = source

    with pytest.raises(DomainError):
        store.record_snapshot(incoming)
    assert store.telemetry_latest() is None


def test_security_code_changes_do_not_create_observed_events(tmp_path):
    store = make_store(tmp_path)
    for code in (10, 11):
        incoming = snapshot()
        incoming["vehicle"]["vehicle_security_code"] = code
        incoming["field_sources"]["vehicle_security_code"] = "vehicle_security_code"
        store.record_snapshot(incoming)

    assert store.observed_events()["items"] == []
    assert store.observed_events()["total"] == 0


@pytest.mark.parametrize(
    "invalid",
    [
        {"vin": "not-accepted"},
        {"vehicle": {"model": "B10"}},
        {"server_received_at": "2026-10-08T15:00:00Z"},
        {"raw_payload": {"private": "data"}},
        {"token": "secret"},
        {"source": "untrusted-provider"},
        {"observed_at": "2026-10-08T15:00:00"},
        {"provider_received_at": "not-a-date"},
        {"vehicle": {"battery_pct": float("nan")}},
        {"vehicle": {"battery_pct": 101}},
        {"vehicle": {"range_km": -1}},
        {"vehicle": {"odometer_km": float("inf")}},
        {"vehicle": {"interior_temperature_c": True}},
        {"vehicle": {"battery_min_temperature_c": float("nan")}},
        {"vehicle": {"front_left_pressure_kpa": "245"}},
        {"vehicle": {"rear_right_pressure_kpa": 1000.1}},
        {"vehicle": {"locked": 1}},
        {"field_sources": {"battery_pct": "unknown_source"}},
        {"field_sources": {"front_left_pressure_kpa": "pressure_kpa"}},
        {"field_sources": {"locked": "lock_state"}},
        {"vehicle": {"battery_pct": None}},
    ],
)
def test_snapshot_rejects_unallowlisted_fields_and_values_atomically(tmp_path, invalid):
    store = make_store(tmp_path)
    value = snapshot()
    if "vehicle" in invalid:
        value["vehicle"].update(invalid.pop("vehicle"))
    if "field_sources" in invalid:
        value["field_sources"].update(invalid.pop("field_sources"))
    value.update(invalid)
    with pytest.raises(DomainError):
        store.record_snapshot(value)
    assert store.telemetry_latest() is None


@pytest.mark.parametrize(
    "limit,offset",
    [(0, 0), (101, 0), (True, 0), (1, -1), (1, 2_147_483_648)],
)
def test_telemetry_history_rejects_unbounded_pagination(tmp_path, limit, offset):
    with pytest.raises(DomainError):
        make_store(tmp_path).telemetry_history(limit=limit, offset=offset)


def test_snapshot_retention_and_tie_order_are_deterministic(tmp_path):
    start = datetime(2026, 10, 8, 15, 0, tzinfo=timezone.utc)
    ticks = iter(start + timedelta(microseconds=index) for index in range(2_100))
    store = make_store(tmp_path, clock=lambda: next(ticks))
    inserted = [store.record_snapshot(snapshot()) for _ in range(1_001)]

    page = store.telemetry_history(limit=100)
    assert page["total"] == 1_000
    assert len(page["items"]) == 100
    assert page["items"][0]["id"] == inserted[-1]["id"]
    last_page = store.telemetry_history(limit=100, offset=900)
    assert len(last_page["items"]) == 100
    retained_ids = {
        item["id"]
        for offset in range(0, page["total"], 100)
        for item in store.telemetry_history(limit=100, offset=offset)["items"]
    }
    assert inserted[0]["id"] not in retained_ids
    assert inserted[-1]["id"] in retained_ids


def test_local_receipt_order_survives_clock_rollback_and_archive_round_trip(tmp_path):
    clock = [datetime(2026, 10, 8, 15, 0, tzinfo=timezone.utc)]
    store = make_store(tmp_path, clock=lambda: clock[0])
    inserted = [store.record_snapshot(snapshot()) for _ in range(1_000)]
    clock[0] = datetime(2026, 10, 7, 15, 0, tzinfo=timezone.utc)
    new_read = store.record_snapshot(snapshot())

    assert new_read["server_received_at"] < inserted[-1]["server_received_at"]
    assert store.telemetry_latest() == new_read
    history = store.telemetry_history(limit=100)
    assert history["total"] == 1_000
    assert history["items"][0] == new_read
    retained_ids = {
        item["id"]
        for offset in range(0, history["total"], 100)
        for item in store.telemetry_history(limit=100, offset=offset)["items"]
    }
    assert inserted[0]["id"] not in retained_ids
    assert inserted[-1]["id"] in retained_ids

    archive = store.export_data()
    assert archive["telemetry_snapshots"][0]["id"] == new_read["id"]
    destination = make_store(
        tmp_path,
        filename="restored.sqlite3",
        clock=lambda: datetime(2026, 10, 10, 15, 0, tzinfo=timezone.utc),
    )
    destination.import_data(archive)
    assert destination.telemetry_latest() == new_read
    assert destination.export_data()["telemetry_snapshots"] == archive["telemetry_snapshots"]


def test_future_receipt_archive_is_rejected_before_writes_and_store_keeps_ingesting(tmp_path):
    store = make_store(tmp_path)
    initial = store.record_snapshot(snapshot())
    before = store.export_data()
    poisoned = copy.deepcopy(before)
    template = poisoned["telemetry_snapshots"][0]
    poisoned["telemetry_snapshots"] = [
        {
            **template,
            "id": f"{index + 1:032x}",
            "server_received_at": "2035-01-01T00:00:00.000000Z",
        }
        for index in range(1_000)
    ]
    with pytest.raises(DomainError, match="server_received_at"):
        store.validate_import(poisoned)
    with pytest.raises(DomainError, match="server_received_at"):
        store.import_data(poisoned)
    assert store.export_data() == before

    received = store.record_snapshot(snapshot())
    assert store.telemetry_latest() == received
    assert received["id"] != initial["id"]
    assert store.telemetry_history()["total"] == 2


def test_v3_round_trip_and_legacy_restore_clear_v3_data_without_mixing(tmp_path):
    source = make_store(tmp_path, filename="source.sqlite3")
    source.create_widget_profile("owner-a", {"name": "Batería", "fields": ["battery_pct"]})
    snapshot_item = source.record_snapshot(snapshot())
    exported = source.export_data()

    destination = make_store(tmp_path, filename="destination.sqlite3")
    destination.create_widget_profile("owner-b", {"name": "Otro", "fields": ["charging"]})
    destination.record_snapshot(snapshot())
    destination.import_data(exported)
    assert destination.export_data() == exported
    assert destination.telemetry_latest() == snapshot_item
    assert destination.list_widget_profiles("owner-a")["items"][0]["name"] == "Batería"
    assert destination.list_widget_profiles("owner-b")["items"] == []

    legacy = copy.deepcopy(exported)
    legacy["schema_version"] = 2
    legacy.pop("manual_journeys")
    legacy["settings"].pop("vehicle_color")
    legacy.pop("charger_pricing_history")
    legacy.pop("charger_energy")
    legacy.pop("charger")
    legacy.pop("charger_pricing")
    legacy.pop("observed_events")
    legacy["settings"].pop("pricing_method")
    legacy.pop("widget_profiles")
    legacy.pop("telemetry_snapshots")
    destination.import_data(legacy)
    assert destination.telemetry_latest() is None
    assert destination.list_widget_profiles("owner-a")["items"] == []


def test_v3_import_rejects_invalid_snapshot_before_any_restore_write(tmp_path):
    store = make_store(tmp_path)
    original = store.record_snapshot(snapshot())
    before = store.export_data()
    invalid = copy.deepcopy(before)
    invalid["settings"]["vehicle_name"] = "No debe aplicarse"
    invalid["telemetry_snapshots"].append(
        {
            **original,
            "id": "0123456789abcdef0123456789abcdef",
            "vehicle": {**original["vehicle"], "battery_pct": 101},
        }
    )

    with pytest.raises(DomainError):
        store.import_data(invalid)
    assert store.export_data() == before


def test_v15_archive_restores_new_fields_and_v14_v13_add_nulls(tmp_path):
    source = make_store(tmp_path, filename="source.sqlite3")
    source_item = source.record_snapshot(snapshot())
    archive = source.export_data()
    assert archive["schema_version"] == 16
    assert len(archive["telemetry_snapshots"][0]["vehicle"]) == 14
    assert len(archive["telemetry_snapshots"][0]["field_sources"]) == 14

    destination = make_store(tmp_path, filename="v15.sqlite3")
    destination.import_data(archive)
    assert destination.telemetry_latest() == source_item

    legacy_v14 = copy.deepcopy(archive)
    legacy_v14["schema_version"] = 14
    legacy_v14.pop("manual_journeys")
    for field in SECURITY_CODE_FIELDS:
        legacy_v14["telemetry_snapshots"][0]["vehicle"].pop(field)
        legacy_v14["telemetry_snapshots"][0]["field_sources"].pop(field)
    restored_v14 = make_store(tmp_path, filename="v14.sqlite3")
    restored_v14.import_data(legacy_v14)
    v14_latest = restored_v14.telemetry_latest()
    assert len(legacy_v14["telemetry_snapshots"][0]["vehicle"]) == 12
    assert len(legacy_v14["telemetry_snapshots"][0]["field_sources"]) == 12
    assert all(v14_latest["vehicle"][field] is None for field in SECURITY_CODE_FIELDS)
    assert all(v14_latest["field_sources"][field] is None for field in SECURITY_CODE_FIELDS)

    legacy_v13 = copy.deepcopy(archive)
    legacy_v13["schema_version"] = 13
    legacy_v13.pop("manual_journeys")
    legacy_item = legacy_v13["telemetry_snapshots"][0]
    for field in (
        "interior_temperature_c", "battery_min_temperature_c", "front_left_pressure_kpa",
        "front_right_pressure_kpa", "rear_left_pressure_kpa", "rear_right_pressure_kpa",
        *SECURITY_CODE_FIELDS,
    ):
        legacy_item["vehicle"].pop(field)
        legacy_item["field_sources"].pop(field)
    restored = make_store(tmp_path, filename="v13.sqlite3")
    restored.import_data(legacy_v13)
    latest = restored.telemetry_latest()
    assert len(legacy_v13["telemetry_snapshots"][0]["vehicle"]) == 6
    assert len(legacy_v13["telemetry_snapshots"][0]["field_sources"]) == 6
    assert all(latest["vehicle"][field] is None for field in (
        "interior_temperature_c", "battery_min_temperature_c", "front_left_pressure_kpa",
        "front_right_pressure_kpa", "rear_left_pressure_kpa", "rear_right_pressure_kpa",
        *SECURITY_CODE_FIELDS,
    ))
    assert all(latest["field_sources"][field] is None for field in (
        "interior_temperature_c", "battery_min_temperature_c", "front_left_pressure_kpa",
        "front_right_pressure_kpa", "rear_left_pressure_kpa", "rear_right_pressure_kpa",
        *SECURITY_CODE_FIELDS,
    ))


@pytest.mark.parametrize(
    "field,value,source",
    [
        ("vehicle_security_code", True, "vehicle_security_code"),
        ("vehicle_security_code", 7.0, "vehicle_security_code"),
        ("vehicle_security_code", 7.5, "vehicle_security_code"),
        ("vehicle_security_code", "7", "vehicle_security_code"),
        ("vehicle_security_code", float("nan"), "vehicle_security_code"),
        ("vehicle_security_code", float("inf"), "vehicle_security_code"),
        ("vehicle_security_code", 256, "vehicle_security_code"),
        ("vehicle_security_code", -1, "vehicle_security_code"),
        ("vehicle_security_code", 10**300, "vehicle_security_code"),
        ("vehicle_security_code", 7, "sentry_code"),
        ("sentry_code", None, "sentry_code"),
        ("sentry_code", 7, None),
    ],
)
def test_v15_archive_rejects_invalid_security_codes_before_any_restore_write(
    tmp_path, field, value, source
):
    store = make_store(tmp_path)
    prior = store.record_snapshot(snapshot())
    before = store.export_data()
    invalid = copy.deepcopy(before)
    invalid["telemetry_snapshots"][0]["vehicle"][field] = value
    invalid["telemetry_snapshots"][0]["field_sources"][field] = source
    with pytest.raises(DomainError):
        store.import_data(invalid)
    assert store.export_data() == before
    assert store.telemetry_latest() == prior


def test_schema_15_migration_adds_nullable_metrics_and_preserves_snapshots(tmp_path):
    path = tmp_path / "migration.sqlite3"
    store = make_store(tmp_path, filename=path.name)
    original = store.record_snapshot(snapshot())
    with sqlite3.connect(path) as connection:
        downgrade_telemetry_to_v15(connection)
        connection.execute("DROP TABLE IF EXISTS manual_charge_requests")
        connection.execute("DROP TABLE IF EXISTS manual_journey_requests")
        connection.execute("DROP TABLE IF EXISTS manual_journeys")
        connection.execute("PRAGMA user_version = 15")

    migrated = Store(path)
    assert migrated.telemetry_latest()["id"] == original["id"]
    assert migrated.telemetry_latest()["server_received_at"] == original["server_received_at"]
    assert migrated.telemetry_latest()["vehicle"]["battery_pct"] == original["vehicle"]["battery_pct"]
    assert migrated.telemetry_latest()["field_sources"]["battery_pct"] == "precise_soc_percent"
    assert migrated.telemetry_latest()["field_sources"]["odometer_km"] == "odometer_km"
    assert all(migrated.telemetry_latest()["vehicle"][field] is None for field in (
        "interior_temperature_c", "battery_min_temperature_c", "front_left_pressure_kpa",
        "front_right_pressure_kpa", "rear_left_pressure_kpa", "rear_right_pressure_kpa",
    ))
    assert all(migrated.telemetry_latest()["vehicle"][field] is None for field in SECURITY_CODE_FIELDS)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 20
    assert all(migrated.telemetry_latest()["field_sources"][field] is None for field in (
        "interior_temperature_c", "battery_min_temperature_c", "front_left_pressure_kpa",
        "front_right_pressure_kpa", "rear_left_pressure_kpa", "rear_right_pressure_kpa",
        *SECURITY_CODE_FIELDS,
    ))


@pytest.mark.parametrize("corruption", ["json", "schema"])
def test_schema_15_migration_rejects_corrupt_snapshot_without_partial_upgrade(
    tmp_path, corruption
):
    path = tmp_path / f"corrupt-{corruption}.sqlite3"
    store = make_store(tmp_path, filename=path.name)
    store.record_snapshot(snapshot())
    with sqlite3.connect(path) as connection:
        downgrade_telemetry_to_v15(connection)
        if corruption == "json":
            connection.execute(
                "UPDATE telemetry_snapshots SET field_sources_json = 'not-json'"
            )
        else:
            connection.execute("ALTER TABLE telemetry_snapshots DROP COLUMN field_sources_json")
        connection.execute("DROP TABLE IF EXISTS manual_charge_requests")
        connection.execute("DROP TABLE IF EXISTS manual_journey_requests")
        connection.execute("DROP TABLE IF EXISTS manual_journeys")
        connection.execute("PRAGMA user_version = 15")

    with pytest.raises(DomainError):
        Store(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 15
        columns = {row[1] for row in connection.execute("PRAGMA table_info(telemetry_snapshots)")}
        assert "interior_temperature_c" not in columns
        assert not set(SECURITY_CODE_FIELDS) & columns


def test_representative_schema16_fixture_migrates_additively_without_inference(tmp_path):
    schema16_path, legacy = create_representative_schema16_fixture(tmp_path)
    assert legacy["schema_version"] == 16
    assert set(legacy["telemetry"]["vehicle"]) == {
        "battery_pct", "range_km", "odometer_km", "interior_temperature_c",
        "battery_min_temperature_c", "front_left_pressure_kpa", "front_right_pressure_kpa",
        "rear_left_pressure_kpa", "rear_right_pressure_kpa", "locked", "climate_on", "charging",
    }

    migration_path = tmp_path / "migrated-copy.sqlite3"
    with sqlite3.connect(schema16_path) as source, sqlite3.connect(migration_path) as target:
        source.backup(target)
    migrated = Store(migration_path)
    expected_telemetry = copy.deepcopy(legacy["telemetry"])
    for field in SECURITY_CODE_FIELDS:
        expected_telemetry["vehicle"][field] = None
        expected_telemetry["field_sources"][field] = None
    assert migrated.telemetry_latest() == expected_telemetry
    assert migrated.get_settings() == legacy["settings"]
    assert migrated.get_charge(legacy["charge"]["id"]) == legacy["charge"]
    maintenance = migrated.state("Pulse", "0.1")["maintenance"]
    assert any(
        item["id"] == legacy["maintenance"]["id"]
        and item["due_date"] == legacy["maintenance"]["due_date"]
        for item in maintenance
    )
    assert migrated.list_widget_profiles("owner-migration")["items"] == [legacy["profile"]]
    with sqlite3.connect(migration_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 20
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(telemetry_snapshots)")
        }
        assert set(SECURITY_CODE_FIELDS) <= columns
        row = connection.execute(
            "SELECT vehicle_security_code, sentry_code, field_sources_json "
            "FROM telemetry_snapshots WHERE id = ?",
            (legacy["telemetry"]["id"],),
        ).fetchone()
        assert row[:2] == (None, None)
        migrated_sources = json.loads(row[2])
        assert all(migrated_sources[field] is None for field in SECURITY_CODE_FIELDS)

    with sqlite3.connect(schema16_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 16
        assert not set(SECURITY_CODE_FIELDS) & {
            row[1] for row in connection.execute("PRAGMA table_info(telemetry_snapshots)")
        }

    invalid_path = tmp_path / "invalid-schema16-copy.sqlite3"
    with sqlite3.connect(schema16_path) as source, sqlite3.connect(invalid_path) as target:
        source.backup(target)
    with sqlite3.connect(invalid_path) as connection:
        connection.execute("UPDATE telemetry_snapshots SET field_sources_json = '['")

    def database_state(path):
        with sqlite3.connect(path) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            columns = tuple(
                row[1] for row in connection.execute("PRAGMA table_info(telemetry_snapshots)")
            )
            rows = tuple(connection.execute(
                "SELECT * FROM telemetry_snapshots ORDER BY rowid"
            ).fetchall())
        return version, columns, rows

    before_failed_migration = database_state(invalid_path)
    with pytest.raises(DomainError):
        Store(invalid_path)
    assert database_state(invalid_path) == before_failed_migration
