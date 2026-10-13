"""Observed-event detector, archive validation, and SQLite history tests."""

from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest

from pulse.domain import DomainError, EXPORT_VERSION, SCHEMA_VERSION, Store
from pulse.observed_events import (
    MAX_OBSERVED_EVENTS,
    OBSERVED_EVENT_SOURCE,
    ObservedEventError,
    select_changes,
    validate_observed_event_future_dates,
    validate_observed_events_archive,
)
from schema_helpers import downgrade_telemetry_to_v15


UTC = timezone.utc
NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self, value: datetime = NOW):
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **delta: int) -> None:
        self.value += timedelta(**delta)


def iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def observed_snapshot(
    index: int,
    observed_at: datetime,
    *,
    received_at: datetime | None = None,
    locked: object = False,
    charging: object = False,
    source: str = OBSERVED_EVENT_SOURCE,
) -> dict:
    received_at = received_at or observed_at + timedelta(seconds=30)
    return {
        "id": f"{index:032x}",
        "source": source,
        "observed_at": iso(observed_at),
        "server_received_at": iso(received_at),
        "vehicle": {"locked": locked, "charging": charging},
    }


def provider_snapshot(observed_at: datetime, *, locked: bool, charging: bool) -> dict:
    return {
        "source": OBSERVED_EVENT_SOURCE,
        "observed_at": iso(observed_at),
        "provider_received_at": iso(observed_at),
        "vehicle": {
            "battery_pct": 62,
            "range_km": 280,
            "odometer_km": 4_200,
            "locked": locked,
            "climate_on": False,
            "charging": charging,
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


def make_store(tmp_path, name="pulse.sqlite3", clock=None):
    return Store(tmp_path / name, clock=clock or Clock())


def one_change_pair(
    *,
    start: datetime = NOW - timedelta(minutes=1),
    first_received: datetime | None = None,
    second_received: datetime | None = None,
    before: bool = False,
    after: bool = True,
):
    first = observed_snapshot(
        1,
        start,
        received_at=first_received,
        locked=before,
        charging=False,
    )
    second = observed_snapshot(
        2,
        start + timedelta(seconds=1),
        received_at=second_received,
        locked=after,
        charging=False,
    )
    events, baseline_id = select_changes(first, second)
    assert baseline_id == second["id"]
    assert len(events) == 1
    return first, second, events[0]


def test_detector_seeds_then_records_only_strict_boolean_changes():
    first = observed_snapshot(1, NOW - timedelta(minutes=1), locked=False, charging=False)
    events, baseline_id = select_changes(None, first)
    assert events == []
    assert baseline_id == first["id"]

    current = observed_snapshot(
        2,
        NOW - timedelta(minutes=1) + timedelta(seconds=1),
        locked=True,
        charging=True,
    )
    events, baseline_id = select_changes(first, current)
    assert [item["field"] for item in events] == ["locked", "charging"]
    assert [(item["before"], item["after"]) for item in events] == [(False, True)] * 2
    assert baseline_id == current["id"]
    assert set(events[0]) == {
        "id", "field", "source", "before", "after", "previous_observed_at",
        "observed_at", "previous_received_at", "received_at",
        "previous_snapshot_id", "snapshot_id",
    }
    assert events[0]["source"] == OBSERVED_EVENT_SOURCE


def test_detector_clears_bad_or_demo_baselines_and_keeps_valid_fields_independent():
    observed = NOW - timedelta(minutes=1)
    first = observed_snapshot(1, observed, locked=0, charging=False)
    current = observed_snapshot(
        2,
        observed + timedelta(seconds=1),
        locked=True,
        charging=True,
    )
    events, baseline_id = select_changes(first, current)
    assert [event["field"] for event in events] == ["charging"]
    assert baseline_id == current["id"]

    missing_locked = observed_snapshot(
        3,
        observed + timedelta(seconds=2),
        charging=False,
    )
    del missing_locked["vehicle"]["locked"]
    events, baseline_id = select_changes(current, missing_locked)
    assert [event["field"] for event in events] == ["charging"]
    assert baseline_id == missing_locked["id"]

    assert select_changes(first, current, demo=True) == ([], None)
    assert select_changes(first, {**current, "source": "demo"}) == ([], None)
    assert select_changes(first, {**current, "id": "not-a-snapshot-id"}) == ([], None)
    assert select_changes(first, {**current, "observed_at": None}) == ([], None)
    stale_previous = observed_snapshot(
        4,
        observed - timedelta(minutes=1),
        received_at=observed + timedelta(seconds=301),
    )
    assert select_changes(stale_previous, current) == ([], current["id"])
    future = {
        **current,
        "observed_at": iso(NOW),
        "server_received_at": iso(NOW - timedelta(seconds=1)),
    }
    assert select_changes(first, future) == ([], None)


@pytest.mark.parametrize(
    "previous,current,expected_baseline",
    [
        (
            observed_snapshot(
                1,
                NOW - timedelta(minutes=5),
                received_at=NOW - timedelta(minutes=5) + timedelta(seconds=30),
            ),
            observed_snapshot(
                2,
                NOW - timedelta(minutes=5) + timedelta(seconds=1),
                received_at=NOW - timedelta(minutes=5) + timedelta(seconds=302),
                locked=True,
            ),
            None,
        ),
        (
            observed_snapshot(
                1,
                NOW - timedelta(minutes=3),
                received_at=NOW - timedelta(minutes=3) + timedelta(seconds=301),
            ),
            observed_snapshot(
                2, NOW - timedelta(minutes=3) + timedelta(seconds=1), locked=True
            ),
            f"{2:032x}",
        ),
        (
            observed_snapshot(1, NOW - timedelta(minutes=1)),
            observed_snapshot(
                2,
                NOW - timedelta(minutes=1),
                received_at=NOW,
                locked=True,
            ),
            f"{1:032x}",
        ),
        (
            observed_snapshot(1, NOW - timedelta(minutes=1)),
            observed_snapshot(
                2,
                NOW - timedelta(minutes=2),
                received_at=NOW,
                locked=True,
            ),
            f"{1:032x}",
        ),
    ],
)
def test_repeated_older_and_stale_observations_never_create_changes(
    previous, current, expected_baseline
):
    events, next_id = select_changes(previous, current)
    assert events == []
    assert next_id == expected_baseline


def test_duplicate_snapshot_id_cannot_create_an_event():
    observed = NOW - timedelta(minutes=1)
    previous = observed_snapshot(1, observed, locked=False)
    current = observed_snapshot(
        2, observed + timedelta(seconds=1), locked=True
    )
    current["id"] = previous["id"]
    assert select_changes(previous, current) == ([], previous["id"])


def test_detector_seeds_after_long_gaps_and_receipt_clock_regression():
    observed = NOW - timedelta(minutes=20)
    first = observed_snapshot(1, observed, received_at=observed + timedelta(seconds=30))

    long_gap = observed_snapshot(
        2,
        observed + timedelta(seconds=601),
        received_at=observed + timedelta(seconds=631),
        locked=True,
    )
    assert select_changes(first, long_gap) == ([], long_gap["id"])

    receipt_regression = observed_snapshot(
        3,
        observed + timedelta(seconds=2),
        received_at=observed + timedelta(seconds=2),
        locked=True,
    )
    assert select_changes(first, receipt_regression) == ([], receipt_regression["id"])

    boundary = observed_snapshot(
        4,
        observed + timedelta(seconds=600),
        received_at=observed + timedelta(seconds=630),
        locked=True,
    )
    events, next_id = select_changes(first, boundary)
    assert [event["field"] for event in events] == ["locked"]
    assert next_id == boundary["id"]


def test_detector_accepts_the_300_second_snapshot_age_boundary():
    observed = NOW - timedelta(minutes=10)
    first = observed_snapshot(
        1, observed, received_at=observed + timedelta(seconds=300)
    )
    current = observed_snapshot(
        2,
        observed + timedelta(seconds=1),
        received_at=observed + timedelta(seconds=301),
        locked=True,
    )
    events, baseline_id = select_changes(first, current)
    assert [event["field"] for event in events] == ["locked"]
    assert baseline_id == current["id"]

    stale_current = {
        **current,
        "server_received_at": iso(
            observed + timedelta(seconds=302)
        ),
    }
    assert select_changes(first, stale_current) == ([], None)


def test_event_hash_excludes_receipt_times_and_archive_crosschecks_present_anchors():
    first, second, event = one_change_pair()
    shifted_received = {
        **second,
        "id": f"{3:032x}",
        "server_received_at": iso(
            datetime.fromisoformat(second["server_received_at"].replace("Z", "+00:00"))
            + timedelta(seconds=1)
        ),
    }
    duplicate_logical, _ = select_changes(first, shifted_received)
    assert duplicate_logical[0]["id"] == event["id"]
    assert duplicate_logical[0]["received_at"] != event["received_at"]

    anchors = [
        {**first, "vehicle": {**first["vehicle"]}},
        {**second, "vehicle": {**second["vehicle"]}},
    ]
    assert validate_observed_events_archive([event], anchors) == [event]
    wrong_anchor = {**second, "vehicle": {**second["vehicle"], "locked": False}}
    with pytest.raises(ObservedEventError, match="anclaje current"):
        validate_observed_events_archive([event], [anchors[0], wrong_anchor])


def test_archive_validation_rejects_bad_ids_sources_shapes_order_and_duplicate_events():
    first, second, event = one_change_pair()
    assert validate_observed_events_archive([event], [first, second]) == [event]
    bad_cases = [
        {**event, "id": "0" * 64},
        {**event, "source": "another-provider"},
        {**event, "before": 0},
        {**event, "before": event["after"]},
        {**event, "snapshot_id": event["previous_snapshot_id"]},
        {**event, "observed_at": event["previous_observed_at"]},
        {**event, "unexpected": True},
    ]
    for bad in bad_cases:
        with pytest.raises(ObservedEventError):
            validate_observed_events_archive([bad], [])
    with pytest.raises(ObservedEventError, match="duplicados"):
        validate_observed_events_archive([event, event], [])
    with pytest.raises(ObservedEventError, match="1000"):
        validate_observed_events_archive([event] * (MAX_OBSERVED_EVENTS + 1), [])


def test_future_date_validator_requires_aware_now_and_rejects_future_event_dates():
    _, _, event = one_change_pair()
    validate_observed_event_future_dates([event], NOW)
    with pytest.raises(ObservedEventError, match="futuro"):
        validate_observed_event_future_dates([event], NOW - timedelta(minutes=2))
    with pytest.raises(ObservedEventError, match="zona horaria"):
        validate_observed_event_future_dates([event], datetime(2026, 10, 9, 12, 0))


def test_store_records_paginates_and_roundtrips_observed_changes(tmp_path):
    clock = Clock()
    source = make_store(tmp_path, "source.sqlite3", clock)
    first_observed = NOW - timedelta(seconds=60)
    source.record_snapshot(provider_snapshot(first_observed, locked=False, charging=False))
    clock.advance(seconds=1)
    source.record_snapshot(provider_snapshot(first_observed + timedelta(seconds=1), locked=True, charging=True))

    page = source.observed_events(limit=1)
    assert page["total"] == 2
    assert page["limit"] == 1
    assert page["offset"] == 0
    assert page["items"][0]["field"] == "charging"
    assert source.observed_events(limit=10, offset=1)["items"][0]["field"] == "locked"
    with pytest.raises(DomainError):
        source.observed_events(limit=True)
    with pytest.raises(DomainError):
        source.observed_events(limit=101)

    exported = source.export_data()
    assert exported["schema_version"] == EXPORT_VERSION == 15
    assert exported["observed_events"] == source.observed_events(limit=100)["items"]
    destination = make_store(tmp_path, "destination.sqlite3", clock)
    destination.import_data(json.dumps(exported, allow_nan=False))
    assert destination.observed_events(limit=100) == source.observed_events(limit=100)


def test_store_deduplicates_event_ids_and_rejects_invalid_import_atomically(tmp_path):
    clock = Clock()
    store = make_store(tmp_path, clock=clock)
    first_observed = NOW - timedelta(seconds=60)
    store.record_snapshot(provider_snapshot(first_observed, locked=False, charging=False))
    clock.advance(seconds=1)
    store.record_snapshot(provider_snapshot(first_observed + timedelta(seconds=1), locked=True, charging=False))
    before = store.export_data()
    event_rows = before["observed_events"]
    assert len(event_rows) == 1

    with store._transaction() as connection:
        store._append_observed_events(connection, event_rows)
    assert store.observed_events()["total"] == 1

    invalid = json.loads(json.dumps(before))
    invalid["observed_events"][0]["id"] = "0" * 64
    with pytest.raises(DomainError, match="observed_events"):
        store.import_data(json.dumps(invalid, allow_nan=False))
    assert store.export_data() == before


def test_legacy_exports_and_v9_database_migration_start_with_empty_history(tmp_path):
    clock = Clock()
    path = tmp_path / "migration.sqlite3"
    store = Store(path, clock=clock)
    first_observed = NOW - timedelta(seconds=60)
    stored = store.record_snapshot(
        provider_snapshot(first_observed, locked=False, charging=False)
    )
    clock.advance(seconds=1)
    store.record_snapshot(
        provider_snapshot(first_observed + timedelta(seconds=1), locked=True, charging=False)
    )

    with sqlite3.connect(path) as connection:
        downgrade_telemetry_to_v15(connection)
        connection.execute("DROP TABLE abrp_preference")
        connection.execute("DROP TABLE observed_events")
        connection.execute("DROP TABLE IF EXISTS charger_pricing_history")
        connection.execute("DROP TABLE charger_energy_intervals")
        connection.execute("DROP TABLE charger_energy_epochs")
        connection.execute("ALTER TABLE settings DROP COLUMN vehicle_color")
        connection.execute("DROP TABLE IF EXISTS manual_charge_requests")
        connection.execute("PRAGMA user_version = 9")
    migrated = Store(path, clock=clock)
    assert SCHEMA_VERSION == 19
    assert migrated.telemetry_latest()["id"] != stored["id"]
    assert migrated.observed_events() == {
        "items": [], "total": 0, "limit": 20, "offset": 0,
    }
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 19
        assert connection.execute(
            "SELECT COUNT(*) FROM telemetry_snapshots"
        ).fetchone()[0] == 2

    legacy = migrated.export_data()
    legacy["schema_version"] = 8
    for item in legacy["telemetry_snapshots"]:
        for field in (
            "interior_temperature_c", "battery_min_temperature_c", "front_left_pressure_kpa",
            "front_right_pressure_kpa", "rear_left_pressure_kpa", "rear_right_pressure_kpa",
            "vehicle_security_code", "sentry_code",
        ):
            item["vehicle"].pop(field)
            item["field_sources"].pop(field)
    legacy["settings"].pop("vehicle_color")
    legacy.pop("observed_events")
    legacy.pop("charger_pricing_history")
    legacy.pop("charger_energy")
    restored = make_store(tmp_path, "legacy.sqlite3", clock)
    restored.import_data(json.dumps(legacy, allow_nan=False))
    assert restored.observed_events()["total"] == 0


def test_store_retains_only_latest_1000_rows_and_restore_keeps_query_order(tmp_path):
    store = make_store(tmp_path)
    base = NOW - timedelta(minutes=20)
    events = []
    previous = observed_snapshot(1, base, locked=False)
    for index in range(2, MAX_OBSERVED_EVENTS + 2):
        current = observed_snapshot(
            index,
            base + timedelta(seconds=index - 1),
            locked=index % 2 == 0,
        )
        generated, _ = select_changes(previous, current)
        assert len(generated) == 1
        events.extend(generated)
        previous = current

    with store._transaction() as connection:
        for event in events:
            store._append_observed_events(connection, [event])
    latest = store.observed_events(limit=100)
    assert latest["total"] == MAX_OBSERVED_EVENTS
    assert len(latest["items"]) == 100
    assert latest["items"][0]["id"] == events[-1]["id"]
    assert store.observed_events(limit=1, offset=MAX_OBSERVED_EVENTS - 1)["items"][0]["id"] == events[0]["id"]

    archive = store.export_data()
    restored = make_store(tmp_path, "retained-restore.sqlite3")
    restored.import_data(json.dumps(archive, allow_nan=False))
    assert restored.observed_events(limit=100) == store.observed_events(limit=100)
