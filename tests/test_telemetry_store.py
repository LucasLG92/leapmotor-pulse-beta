import copy
from datetime import datetime, timedelta, timezone

import pytest

from pulse.domain import DomainError, Store


SOURCE = "MATE-API B10ReadClient"


def snapshot(**overrides):
    value = {
        "source": SOURCE,
        "observed_at": "2026-10-08T16:00:00+01:00",
        "provider_received_at": "2026-10-08T15:00:30Z",
        "vehicle": {
            "battery_pct": 74.5,
            "range_km": 321,
            "odometer_km": 12500.25,
            "locked": None,
            "climate_on": None,
            "charging": None,
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
        {"vehicle": {"locked": 1}},
        {"field_sources": {"battery_pct": "unknown_source"}},
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
