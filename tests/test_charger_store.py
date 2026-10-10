"""Persistence tests for normalized evcc data; no evcc endpoint is contacted."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import copy
import json
import sqlite3

import pytest

from pulse.domain import DomainError, EXPORT_VERSION, SCHEMA_VERSION, Store
from pulse.charger_store import (
    MAX_CHARGER_ACTIVE_SECONDS,
    MAX_CHARGER_SCOPES,
    MAX_CHARGER_SESSIONS,
    MAX_CHARGER_UPSTREAM_ID,
    session_id as canonical_session_id,
)


UTC = timezone.utc
NOW = datetime(2026, 10, 8, 15, 0, tzinfo=UTC)


class Clock:
    def __init__(self, value: datetime = NOW):
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **delta: int) -> None:
        self.value += timedelta(**delta)


def make_store(tmp_path, name="pulse.sqlite3", clock=None):
    return Store(tmp_path / name, clock=clock or Clock())


def scope(index=1):
    return f"{index:064x}"


def observation(*, received_at="2026-10-08T14:00:00Z", **changes):
    value = {
        "received_at": received_at,
        "loadpoint": "Garage",
        "connected": True,
        "charging": True,
        "power_w": 7_200,
        "charged_kwh": 12.5,
        "active_seconds": 3_600,
    }
    value.update(changes)
    return value


def session(
    index=1,
    *,
    source_scope=None,
    received_at="2026-10-08T14:00:00Z",
    **changes,
):
    value = {
        "upstream_id": index,
        "loadpoint": "Garage",
        "started_at": "2026-10-08T12:00:00Z",
        "ended_at": "2026-10-08T13:00:00Z",
        "kwh": 10.0,
        "active_seconds": 3_000,
        "received_at": received_at,
    }
    value.update(changes)
    if "id" not in changes:
        value["id"] = canonical_session_id(
            source_scope or scope(), value["upstream_id"], value["started_at"],
            value["loadpoint"],
        )
    return value


def test_v6_to_v7_migration_preserves_domain_data_and_creates_charger_tables(tmp_path):
    path = tmp_path / "migration.sqlite3"
    clock = Clock()
    store = Store(path, clock=clock)
    charge = store.add_charge({"started_at": "2026-10-08T10:00:00Z", "kwh": 5})

    # Recreate the exact pre-V7 shape while keeping the existing domain rows.
    with sqlite3.connect(path) as connection:
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
        connection.execute("PRAGMA user_version = 6")

    migrated = Store(path, clock=clock)
    assert migrated.get_charge(charge["id"]) == charge
    assert migrated.charger_observation(scope()) is None
    assert migrated.charger_sessions(scope()) == {
        "items": [], "total": 0, "limit": 20, "offset": 0,
    }
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 15
        assert {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )} >= {"charger_observations", "charger_sessions"}
    assert SCHEMA_VERSION == 15


def test_observation_and_sessions_persist_dedupe_and_accept_newer_corrections(tmp_path):
    clock = Clock()
    store = make_store(tmp_path, clock=clock)
    first = observation(received_at="2026-10-08T16:00:00+02:00")
    record = session(received_at="2026-10-08T16:00:00+02:00")

    assert store.ingest_charger(scope(), first, [record]) == {"changed": True}
    assert store.ingest_charger(scope(), first, [record]) == {"changed": False}
    assert store.charger_observation(scope()) == {
        **first,
        "received_at": "2026-10-08T14:00:00.000000Z",
        "power_w": 7_200.0,
        "charged_kwh": 12.5,
    }
    assert store.charger_sessions(scope())["items"] == [{
        **record,
        "received_at": "2026-10-08T14:00:00.000000Z",
        "started_at": "2026-10-08T12:00:00.000000Z",
        "ended_at": "2026-10-08T13:00:00.000000Z",
        "kwh": 10.0,
    }]

    corrected = session(
        received_at="2026-10-08T14:01:00Z", ended_at="2026-10-08T13:01:00Z",
        kwh=10.5, active_seconds=3_050,
    )
    assert corrected["id"] == record["id"]
    assert store.ingest_charger(scope(), sessions=[corrected]) == {"changed": True}
    assert store.charger_sessions(scope())["items"][0]["kwh"] == 10.5
    stale = session(received_at="2026-10-08T13:59:00Z", kwh=9.0)
    assert store.ingest_charger(scope(), sessions=[stale]) == {"changed": False}
    assert store.charger_sessions(scope())["items"][0]["kwh"] == 10.5

    other_scope = scope(2)
    other = session(
        2, source_scope=other_scope, received_at="2026-10-08T14:02:00Z", kwh=4
    )
    assert store.ingest_charger(other_scope, sessions=[other])["changed"] is True
    assert store.charger_sessions(scope())["items"][0]["kwh"] == 10.5
    assert store.charger_sessions(other_scope)["items"][0]["kwh"] == 4.0


def test_session_id_normalizes_equivalent_utc_instants_and_precision():
    utc = canonical_session_id(scope(), 42, "2026-10-08T12:00:00Z", "Garage")
    offset = canonical_session_id(scope(), 42, "2026-10-08T14:00:00+02:00", "Garage")
    zero_fraction = canonical_session_id(
        scope(), 42, "2026-10-08T12:00:00.000000Z", "Garage"
    )
    assert utc == offset == zero_fraction


def test_fractional_active_seconds_roundtrip_and_31_day_boundary(tmp_path):
    clock = Clock()
    store = make_store(tmp_path, clock=clock)
    exact_boundary = session(
        started_at="2026-09-07T13:00:00Z",
        ended_at="2026-10-08T13:00:00Z",
        active_seconds=MAX_CHARGER_ACTIVE_SECONDS,
        received_at="2026-10-08T14:00:00Z",
        upstream_id=MAX_CHARGER_UPSTREAM_ID,
    )
    current = observation(active_seconds=MAX_CHARGER_ACTIVE_SECONDS)
    assert store.ingest_charger(scope(), current, [exact_boundary])["changed"] is True
    observed = store.charger_observation(scope())
    closed = store.charger_sessions(scope())["items"][0]
    assert isinstance(observed["active_seconds"], float)
    assert isinstance(closed["active_seconds"], float)
    assert observed["active_seconds"] == MAX_CHARGER_ACTIVE_SECONDS
    assert closed["upstream_id"] == MAX_CHARGER_UPSTREAM_ID

    fractional_observation = observation(active_seconds=12.375)
    fractional_session = session(2, source_scope=scope(2), active_seconds=3_599.5)
    assert store.ingest_charger(
        scope(2), fractional_observation, [fractional_session]
    )["changed"] is True
    exported = store.export_data()
    destination = make_store(tmp_path, "fractional-restore.sqlite3", clock)
    destination.import_data(json.dumps(exported, allow_nan=False))
    assert destination.charger_observation(scope(2))["active_seconds"] == 12.375
    assert destination.charger_sessions(scope(2))["items"][0]["active_seconds"] == 3_599.5
    assert type(destination.charger_observation(scope(2))["active_seconds"]) is float
    assert type(destination.charger_sessions(scope(2))["items"][0]["active_seconds"]) is float


def test_same_id_identity_conflicts_and_discordant_batch_duplicates_are_rejected(tmp_path):
    store = make_store(tmp_path)
    original = session()
    store.ingest_charger(scope(), sessions=[original])

    changed_identity = session(
        received_at="2026-10-08T14:01:00Z", started_at="2026-10-08T11:59:00Z", kwh=11,
        id=original["id"],
    )
    with pytest.raises(DomainError, match="identidad"):
        store.ingest_charger(scope(), sessions=[changed_identity])

    with pytest.raises(DomainError, match="discordantes"):
        store.ingest_charger(scope(), sessions=[original, {**original, "kwh": 11}])
    assert store.charger_sessions(scope())["items"] == [original | {
        "started_at": "2026-10-08T12:00:00.000000Z",
        "ended_at": "2026-10-08T13:00:00.000000Z",
        "received_at": "2026-10-08T14:00:00.000000Z",
    }]


def test_parallel_ingests_do_not_lose_sessions(tmp_path):
    store = make_store(tmp_path)
    records = [
        session(index, received_at="2026-10-08T14:00:00Z", upstream_id=index)
        for index in range(1, 33)
    ]
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda item: store.ingest_charger(scope(), sessions=[item]), records))
    page = store.charger_sessions(scope(), limit=100)
    assert page["total"] == 32
    assert {item["id"] for item in page["items"]} == {item["id"] for item in records}


@pytest.mark.parametrize("bad_observation", [
    {**observation(), "connected": 1},
    {**observation(), "connected": False},
    {**observation(), "power_w": True},
    {**observation(), "power_w": float("nan")},
    {**observation(), "power_w": 10**400},
    {**observation(), "charged_kwh": -0.1},
    {**observation(), "active_seconds": 31 * 24 * 60 * 60 + 1},
    {**observation(), "active_seconds": True},
    {**observation(), "unexpected": "field"},
    {key: value for key, value in observation().items() if key != "charging"},
    {**observation(), "received_at": "2026-10-08T14:00:00"},
])
def test_observation_rejects_wrong_types_ranges_and_shapes(tmp_path, bad_observation):
    store = make_store(tmp_path)
    with pytest.raises(DomainError):
        store.ingest_charger(scope(), observation=bad_observation)
    assert store.charger_observation(scope()) is None


@pytest.mark.parametrize("bad_session", [
    {**session(), "upstream_id": True},
    {**session(), "upstream_id": MAX_CHARGER_UPSTREAM_ID + 1},
    {**session(), "upstream_id": 10**400},
    {**session(), "kwh": True},
    {**session(), "kwh": 10**400},
    {**session(), "kwh": 501},
    {**session(), "active_seconds": 3_601},
    {**session(), "active_seconds": True},
    {**session(), "ended_at": "2026-10-08T12:00:00Z"},
    {**session(), "started_at": "2026-09-07T12:00:00Z"},
    {**session(), "received_at": "2026-10-08T15:02:00Z"},
    {**session(), "vehicle": "B10"},
])
def test_sessions_reject_bad_types_ranges_duration_future_and_extra_fields(tmp_path, bad_session):
    store = make_store(tmp_path)
    with pytest.raises(DomainError):
        store.ingest_charger(scope(), sessions=[bad_session])
    assert store.charger_sessions(scope())["total"] == 0


def test_scope_and_batch_limits_and_global_retention(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(DomainError):
        store.charger_observation("not-a-hash")
    with pytest.raises(DomainError):
        store.charger_sessions(scope(), limit=True)

    for index in range(1, MAX_CHARGER_SCOPES + 1):
        store.ingest_charger(scope(index), observation=observation())
    with pytest.raises(DomainError, match="16 scopes"):
        store.ingest_charger(scope(MAX_CHARGER_SCOPES + 1), observation=observation())
    assert store.charger_observation(scope(MAX_CHARGER_SCOPES + 1)) is None

    # Fill the global cap in two bounded batches; the newest closed session wins.
    batch = []
    for index in range(1, MAX_CHARGER_SESSIONS + 1):
        ended = NOW - timedelta(seconds=MAX_CHARGER_SESSIONS - index + 10)
        started = ended - timedelta(minutes=1)
        batch.append(session(
            index,
            started_at=started.isoformat().replace("+00:00", "Z"),
            ended_at=ended.isoformat().replace("+00:00", "Z"),
            active_seconds=30,
            received_at="2026-10-08T14:00:00Z",
        ))
    # Use one existing scope so this checks the session quota, not the scope cap.
    store.ingest_charger(scope(), sessions=batch)
    newest = session(
        1_001,
        started_at="2026-10-08T14:58:59Z",
        ended_at="2026-10-08T14:59:59Z",
        active_seconds=30,
        received_at="2026-10-08T14:59:00Z",
    )
    store.ingest_charger(scope(), sessions=[newest])
    all_ids = set()
    for offset in range(0, MAX_CHARGER_SESSIONS, 100):
        all_ids.update(item["id"] for item in store.charger_sessions(
            scope(), limit=100, offset=offset
        )["items"])
    assert len(all_ids) == MAX_CHARGER_SESSIONS
    assert newest["id"] in all_ids
    assert session(1)["id"] not in all_ids

    with pytest.raises(DomainError, match="hasta 1000"):
        store.ingest_charger(scope(), sessions=batch + [newest])


def test_export_import_roundtrip_and_rejection_are_atomic(tmp_path):
    clock = Clock()
    source = make_store(tmp_path, "source.sqlite3", clock)
    source.ingest_charger(
        scope(), observation=observation(active_seconds=12.375),
        sessions=[session(active_seconds=3_599.5)],
    )
    archive = source.export_data()
    assert archive["schema_version"] == EXPORT_VERSION == 13
    assert archive["charger"] == [{
        "scope": scope(), "observation": observation() | {
            "received_at": "2026-10-08T14:00:00.000000Z",
            "power_w": 7200.0, "charged_kwh": 12.5,
            "active_seconds": 12.375,
        },
        "sessions": [session() | {
            "started_at": "2026-10-08T12:00:00.000000Z",
            "ended_at": "2026-10-08T13:00:00.000000Z",
            "received_at": "2026-10-08T14:00:00.000000Z",
            "kwh": 10.0,
            "active_seconds": 3_599.5,
        }],
    }]
    serialized = json.dumps(archive, allow_nan=False)
    charger_serialized = json.dumps(archive["charger"], allow_nan=False)
    for private in ("token", "vehicle", "meter", "url", "identity"):
        assert private not in charger_serialized.lower()

    destination = make_store(tmp_path, "destination.sqlite3", clock)
    destination.ingest_charger(scope(2), observation=observation())
    assert destination.import_data(serialized) == {"ok": True, "mode": "unconfigured"}
    restored = destination.export_data()
    assert restored["charger"] == archive["charger"]

    before = copy.deepcopy(restored)
    invalid = copy.deepcopy(archive)
    invalid["charger"][0]["sessions"][0]["kwh"] = 501
    with pytest.raises(DomainError):
        destination.import_data(invalid)
    after = destination.export_data()
    before.pop("exported_at")
    after.pop("exported_at")
    assert after == before


@pytest.mark.parametrize("version", [1, 2, 3, 4, 5])
def test_legacy_exports_replace_charger_data_with_empty_dataset(tmp_path, version):
    clock = Clock()
    store = make_store(tmp_path, f"legacy-{version}.sqlite3", clock)
    store.ingest_charger(scope(), observation=observation(), sessions=[session()])
    legacy = store.export_data()
    legacy["schema_version"] = version
    legacy["settings"].pop("vehicle_color")
    legacy.pop("charger_pricing_history")
    legacy.pop("charger_energy")
    legacy.pop("charger")
    legacy.pop("charger_pricing")
    legacy.pop("observed_events")
    if version < 2:
        legacy.pop("command_history")
    if version < 3:
        legacy.pop("widget_profiles")
        legacy.pop("telemetry_snapshots")
    if version < 4:
        legacy["settings"].pop("pricing_method")
        for charge in legacy["charges"]:
            charge.pop("ended_at")
            charge.pop("pricing_method")
            charge.pop("pricing")
    assert store.import_data(legacy) == {"ok": True, "mode": "unconfigured"}
    assert store.charger_observation(scope()) is None
    assert store.charger_sessions(scope())["total"] == 0


def test_import_rejects_duplicate_scope_ids_and_future_dates_before_replacement(tmp_path):
    clock = Clock()
    store = make_store(tmp_path, clock=clock)
    store.ingest_charger(scope(), observation=observation(), sessions=[session()])
    before = store.export_data()
    duplicate_scope = copy.deepcopy(before)
    duplicate_scope["charger"].append(copy.deepcopy(duplicate_scope["charger"][0]))
    future = copy.deepcopy(before)
    future["charger"][0]["observation"]["received_at"] = "2026-10-08T15:02:00Z"
    duplicate_id = copy.deepcopy(before)
    duplicate_id["charger"][0]["sessions"].append(copy.deepcopy(
        duplicate_id["charger"][0]["sessions"][0]
    ))
    incoherent = copy.deepcopy(before)
    incoherent["charger"][0]["observation"]["connected"] = False
    for invalid in (duplicate_scope, future, duplicate_id, incoherent):
        with pytest.raises(DomainError):
            store.import_data(invalid)
        after = store.export_data()
        assert after["charger"] == before["charger"]


def test_import_rejects_duplicate_natural_identity_and_false_id_before_next_ingest(tmp_path):
    store = make_store(tmp_path)
    original = session()
    store.ingest_charger(scope(), sessions=[original])
    before = store.export_data()

    invalid = copy.deepcopy(before)
    duplicate_identity = copy.deepcopy(invalid["charger"][0]["sessions"][0])
    duplicate_identity["id"] = "f" * 32
    invalid["charger"][0]["sessions"].append(duplicate_identity)
    with pytest.raises(DomainError, match="canónica"):
        store.import_data(invalid)

    after = store.export_data()
    assert after["charger"] == before["charger"]
    assert store.ingest_charger(scope(), sessions=[original]) == {"changed": False}
    assert store.charger_sessions(scope())["total"] == 1
