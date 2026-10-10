from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest

from pulse.domain import DomainError, Store


UTC = timezone.utc
NOW = datetime(2026, 10, 8, 20, 0, tzinfo=UTC)


def make_store(tmp_path, *, clock=None, filename="pulse.sqlite3"):
    return Store(tmp_path / filename, clock=clock or (lambda: NOW))


def stamp(value):
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def record_snapshot(store, observed_at, odometer_km, *, locked=False, charging=False):
    return store.record_snapshot(
        {
            "source": "MATE-API B10ReadClient",
            "observed_at": stamp(observed_at),
            "provider_received_at": stamp(observed_at + timedelta(seconds=1)),
            "vehicle": {
                "battery_pct": 70.0,
                "range_km": 300.0,
                "odometer_km": odometer_km,
                "locked": locked,
                "climate_on": False,
                "charging": charging,
            },
            "field_sources": {
                "battery_pct": "soc_percent",
                "range_km": "expected_range_km",
                "odometer_km": "odometer_km",
                "locked": None,
                "climate_on": None,
                "charging": None,
            },
        }
    )


def sql_update(path, column, value, snapshot_id, *, ignore_checks=False):
    connection = sqlite3.connect(path)
    try:
        if ignore_checks:
            connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            f"UPDATE telemetry_snapshots SET {column} = ? WHERE id = ?",
            (value, snapshot_id),
        )
        connection.commit()
    finally:
        connection.close()


def sql_value(path, column, snapshot_id):
    connection = sqlite3.connect(path)
    try:
        return connection.execute(
            f"SELECT {column} FROM telemetry_snapshots WHERE id = ?", (snapshot_id,)
        ).fetchone()[0]
    finally:
        connection.close()


def test_cold_and_warm_payloads_match_and_return_mutation_is_isolated(tmp_path):
    store = make_store(tmp_path)
    store.add_charge({"started_at": stamp(NOW - timedelta(hours=1)), "kwh": 5})
    record_snapshot(store, NOW - timedelta(minutes=10), 100.0)
    record_snapshot(store, NOW - timedelta(minutes=5), 101.0)

    cold = store.energy_analytics(days=30)
    warm = store.energy_analytics(days=30)
    assert warm == cold
    assert warm["observations"]["sample_count"] == 2
    assert warm["observations"]["odometer_delta_km"] == 1.0
    expected = deepcopy(warm)
    other = make_store(tmp_path, filename="other.sqlite3")
    record_snapshot(other, NOW - timedelta(minutes=10), 200.0)
    record_snapshot(other, NOW - timedelta(minutes=5), 205.0)
    assert other.energy_analytics(days=30)["observations"]["odometer_delta_km"] == 5.0
    assert store.energy_analytics(days=30) == expected
    warm["manual"]["sessions"] = 99
    warm["months"][0]["manual"]["kwh"] = 99.0
    warm["observations"]["observed_count"] = 99
    assert store.energy_analytics(days=30) == expected


def test_warm_analytics_refreshes_charges_demo_gate_and_window(tmp_path):
    clock = [NOW]
    store = make_store(tmp_path, clock=lambda: clock[0])
    store.add_charge({"started_at": stamp(NOW - timedelta(days=1)), "kwh": 5})
    record_snapshot(store, NOW - timedelta(minutes=5), 100.0)
    assert store.energy_analytics(days=30)["manual"]["sessions"] == 1

    store.add_charge({"started_at": stamp(NOW - timedelta(days=2)), "kwh": 6})
    after_charge = store.energy_analytics(days=30)
    assert after_charge["manual"]["sessions"] == 2
    assert after_charge["demo"]["sessions"] == 0

    store.set_demo(True)
    after_demo = store.energy_analytics(days=30)
    assert after_demo["manual"]["sessions"] == 2
    assert after_demo["demo"]["sessions"] == 2

    clock[0] += timedelta(days=31)
    after_window = store.energy_analytics(days=30)
    assert after_window["window_end"] == stamp(clock[0])
    assert after_window["manual"]["sessions"] == 0
    assert after_window["demo"]["sessions"] == 0
    assert after_window["observations"]["observed_count"] == 0
    assert after_window["observations"]["outside_window_count"] == 1


def test_warm_analytics_tracks_snapshot_ingestion_retention_and_row_order(tmp_path):
    store = make_store(tmp_path)
    first_observed = NOW - timedelta(hours=1)
    for index in range(1_000):
        record_snapshot(
            store,
            first_observed + timedelta(seconds=index),
            100.0 + index * 0.001,
        )

    before = store.energy_analytics(days=1)
    assert before["observations"]["sample_count"] == 1_000
    assert before["observations"]["first_observed_at"] == stamp(first_observed)
    newest = record_snapshot(
        store, first_observed + timedelta(seconds=1_000), 101.0
    )
    after_ingestion = store.energy_analytics(days=1)
    assert after_ingestion["observations"]["sample_count"] == 1_000
    assert after_ingestion["observations"]["first_observed_at"] == stamp(
        first_observed + timedelta(seconds=1)
    )
    assert after_ingestion["observations"]["last_observed_at"] == stamp(
        first_observed + timedelta(seconds=1_000)
    )

    connection = sqlite3.connect(store.path)
    try:
        rows = connection.execute(
            "SELECT rowid, id FROM telemetry_snapshots ORDER BY rowid ASC"
        ).fetchall()
        first_rowid, first_id = rows[0]
        last_rowid, last_id = rows[-1]
        connection.execute(
            "UPDATE telemetry_snapshots SET rowid = ? WHERE id = ?",
            (last_rowid + 1, first_id),
        )
        connection.execute(
            "UPDATE telemetry_snapshots SET rowid = ? WHERE id = ?",
            (first_rowid, last_id),
        )
        connection.execute(
            "UPDATE telemetry_snapshots SET rowid = ? WHERE id = ?",
            (last_rowid, first_id),
        )
        connection.commit()
    finally:
        connection.close()
    after_reorder = store.energy_analytics(days=1)
    assert newest["id"] == last_id
    assert after_reorder["observations"]["first_observed_at"] == stamp(
        first_observed + timedelta(seconds=1_000)
    )
    assert after_reorder["observations"]["non_monotonic_count"] == 999
    assert after_reorder["observations"]["eligible_interval_count"] == 0


@pytest.mark.parametrize(
    ("column", "replacement", "ignore_checks"),
    [
        ("field_sources_json", "{invalid", False),
        ("locked", 2, True),
        ("source", "untrusted-source", True),
        ("server_received_at", "invalid-date", False),
    ],
)
def test_warm_analytics_revalidates_external_corruption_and_recovers(
    tmp_path, column, replacement, ignore_checks
):
    store = make_store(tmp_path)
    snapshot = record_snapshot(store, NOW - timedelta(minutes=5), 100.0)
    expected = store.energy_analytics(days=30)
    original = sql_value(store.path, column, snapshot["id"])

    sql_update(
        store.path, column, replacement, snapshot["id"], ignore_checks=ignore_checks
    )
    with pytest.raises(DomainError) as error:
        store.energy_analytics(days=30)
    assert error.value.status == 500

    sql_update(store.path, column, original, snapshot["id"], ignore_checks=ignore_checks)
    assert store.energy_analytics(days=30) == expected


def test_warm_cache_refreshes_import_with_reused_snapshot_ids_and_timestamps(tmp_path):
    store = make_store(tmp_path)
    record_snapshot(store, NOW - timedelta(minutes=10), 100.0)
    record_snapshot(store, NOW - timedelta(minutes=5), 101.0)
    before = store.energy_analytics(days=30)
    archive = deepcopy(store.export_data())
    original_identity = [
        (item["id"], item["observed_at"], item["server_received_at"])
        for item in archive["telemetry_snapshots"]
    ]
    archive["telemetry_snapshots"][0]["vehicle"]["odometer_km"] = 103.0

    store.import_data(json.dumps(archive, allow_nan=False))
    restored = store.export_data()
    restored_identity = [
        (item["id"], item["observed_at"], item["server_received_at"])
        for item in restored["telemetry_snapshots"]
    ]
    after = store.energy_analytics(days=30)
    assert restored_identity == original_identity
    assert before["observations"]["odometer_delta_km"] == 1.0
    assert after["observations"]["odometer_delta_km"] == 3.0
