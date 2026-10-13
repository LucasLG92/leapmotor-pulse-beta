"""Persistent ledger tests using synthetic PVPC prices only."""

from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
import json
import sqlite3
from zoneinfo import ZoneInfo

import pytest

from pulse import pvpc
from pulse.charger_store import MAX_CHARGER_SESSIONS, session_id as canonical_session_id
from pulse.domain import DomainError, EXPORT_VERSION, SCHEMA_VERSION, Store
from schema_helpers import downgrade_telemetry_to_v15


UTC = timezone.utc
MADRID = ZoneInfo("Europe/Madrid")
NOW = datetime(2026, 10, 8, 15, 0, tzinfo=UTC)


class Clock:
    def __call__(self):
        return NOW


def scope(index=1):
    return f"{index:064x}"


def session(index=1, *, source_scope=None, **changes):
    value = {
        "upstream_id": index,
        "loadpoint": "Garage",
        "started_at": "2026-10-08T12:00:00Z",
        "ended_at": "2026-10-08T13:00:00Z",
        "kwh": 10.0,
        "active_seconds": 3_000.0,
        "received_at": "2026-10-08T14:00:00Z",
    }
    value.update(changes)
    value["id"] = canonical_session_id(
        source_scope or scope(), value["upstream_id"], value["started_at"], value["loadpoint"]
    )
    return value


def synthetic_day(day: date, price: float = 0.125):
    start = datetime.combine(day, time.min, tzinfo=MADRID).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=MADRID).astimezone(UTC)
    intervals = []
    cursor = start
    while cursor < end:
        next_cursor = cursor + timedelta(hours=1)
        intervals.append({
            "started_at": cursor.isoformat().replace("+00:00", "Z"),
            "ended_at": next_cursor.isoformat().replace("+00:00", "Z"),
            "price_eur_kwh": price,
        })
        cursor = next_cursor
    return pvpc.validate_day_snapshot({
        "date": day.isoformat(),
        "region": "peninsula",
        "timezone": "Europe/Madrid",
        "source": "REE REData PVPC 1001",
        "fetched_at": "2026-10-08T14:00:00Z",
        "intervals": intervals,
    })


def quote_for(record=None, *, price=0.125):
    record = record or session()
    return pvpc.quote_charge(
        record["started_at"], record["ended_at"], record["kwh"],
        [synthetic_day(date(2026, 10, 8), price)], calculated_at=NOW,
    )


def make_store(tmp_path, name="pulse.sqlite3"):
    return Store(tmp_path / name, clock=Clock())


def put_session(store, record=None):
    record = record or session()
    store.ingest_charger(scope(), sessions=[record])
    return record


def test_v8_migration_preserves_sessions_and_installs_cascading_ledger(tmp_path):
    store = make_store(tmp_path)
    record = put_session(store)
    path = tmp_path / "pulse.sqlite3"
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
        connection.execute("PRAGMA user_version = 8")

    migrated = Store(path, clock=Clock())
    assert SCHEMA_VERSION == 19
    assert migrated.charger_sessions(scope())["items"] == [record | {
        "started_at": "2026-10-08T12:00:00.000000Z",
        "ended_at": "2026-10-08T13:00:00.000000Z",
        "received_at": "2026-10-08T14:00:00.000000Z",
    }]
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 19
        assert connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='charger_pricing'"
        ).fetchone() is not None
        trigger_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND name='invalidate_charger_pricing_on_session_change'"
        ).fetchone()[0]
        assert "ended_at" in trigger_sql and "kwh" in trigger_sql


def test_quotes_persist_without_overwrite_and_status_tracks_global_setting(tmp_path):
    store = make_store(tmp_path)
    record = put_session(store)
    assert store.priced_charger_sessions(scope())["items"][0]["pricing_status"] == "disabled"

    store.update_settings({"pricing_method": "pvpc"})
    candidate = store.charger_pricing_candidates(scope())[0]
    assert candidate["id"] == record["id"]
    assert store.priced_charger_sessions(scope())["items"][0]["pricing_status"] == "pending"
    quote = quote_for(candidate)
    assert store.save_charger_pricing(scope(), candidate, quote) == {"changed": True}
    assert store.save_charger_pricing(scope(), candidate, quote_for(candidate, price=0.25)) == {
        "changed": False
    }

    ready = store.priced_charger_sessions(scope())["items"][0]
    assert ready["pvpc"]["cost_eur"] == quote["cost_eur"]
    assert ready["pvpc"]["tariff_eur_kwh"] == quote["tariff_eur_kwh"]
    assert ready["pricing_status"] == "ready"
    assert store.charger_pricing_candidates(scope()) == []

    store.update_settings({"pricing_method": "manual"})
    assert store.priced_charger_sessions(scope())["items"][0] == ready
    reopened = Store(tmp_path / "pulse.sqlite3", clock=Clock())
    assert reopened.priced_charger_sessions(scope())["items"][0] == ready
    assert EXPORT_VERSION == 15


def test_received_at_only_update_preserves_quote_but_energy_correction_invalidates(tmp_path):
    store = make_store(tmp_path)
    record = put_session(store)
    store.update_settings({"pricing_method": "pvpc"})
    quote = quote_for(record)
    assert store.save_charger_pricing(scope(), record, quote) == {"changed": True}
    stored_quote = store.priced_charger_sessions(scope())["items"][0]["pvpc"]

    received_only = {
        **record,
        "received_at": "2026-10-08T14:05:00Z",
        "active_seconds": 3_100,
    }
    assert store.ingest_charger(scope(), sessions=[received_only]) == {"changed": True}
    assert store.priced_charger_sessions(scope())["items"][0]["pvpc"] == stored_quote

    corrected = {
        **received_only,
        "received_at": "2026-10-08T14:06:00Z",
        "kwh": 11.0,
    }
    assert store.ingest_charger(scope(), sessions=[corrected]) == {"changed": True}
    priced = store.priced_charger_sessions(scope())["items"][0]
    assert priced["pvpc"] is None
    assert priced["pricing_status"] == "pending"
    assert [item["id"] for item in store.charger_pricing_candidates(scope())] == [record["id"]]


def test_stale_and_disabled_writes_are_ignored_but_received_at_is_not_pricing_input(tmp_path):
    store = make_store(tmp_path)
    record = put_session(store)
    store.update_settings({"pricing_method": "pvpc"})
    quote = quote_for(record)
    newer_received = {**record, "received_at": "2026-10-08T14:05:00Z"}
    store.ingest_charger(scope(), sessions=[newer_received])
    assert store.save_charger_pricing(scope(), record, quote) == {"changed": True}

    other = session(2, received_at="2026-10-08T14:06:00Z")
    put_session(store, other)
    changed_inputs = {**other, "received_at": "2026-10-08T14:07:00Z", "kwh": 9.5}
    store.ingest_charger(scope(), sessions=[changed_inputs])
    assert store.save_charger_pricing(scope(), other, quote_for(other)) == {"changed": False}

    store.update_settings({"pricing_method": "manual"})
    third = session(3)
    put_session(store, third)
    assert store.save_charger_pricing(scope(), third, quote_for(third)) == {"changed": False}


def test_status_marks_long_sessions_ineligible_when_pvpc_is_enabled(tmp_path):
    store = make_store(tmp_path)
    long_session = session(
        ended_at="2026-10-08T14:00:00Z",
        started_at="2026-10-05T13:00:00Z",
        active_seconds=3_000,
    )
    put_session(store, long_session)
    store.update_settings({"pricing_method": "pvpc"})
    item = store.priced_charger_sessions(scope())["items"][0]
    assert item["pvpc"] is None
    assert item["pricing_status"] == "ineligible"


def test_export_import_roundtrip_and_legacy_versions_leave_ledger_empty(tmp_path):
    source = make_store(tmp_path, "source.sqlite3")
    record = put_session(source)
    source.update_settings({"pricing_method": "pvpc"})
    quote = quote_for(record)
    source.save_charger_pricing(scope(), record, quote)
    stored_quote = source.priced_charger_sessions(scope())["items"][0]["pvpc"]
    archive = source.export_data()
    assert archive["schema_version"] == 15
    assert archive["charger_pricing"] == [
        {"session_id": record["id"], "quote": stored_quote}
    ]

    destination = make_store(tmp_path, "destination.sqlite3")
    assert destination.import_data(archive) == {"ok": True, "mode": "unconfigured"}
    assert destination.export_data()["charger_pricing"] == archive["charger_pricing"]
    assert destination.priced_charger_sessions(scope())["items"][0]["pvpc"] == stored_quote

    for version in range(1, 8):
        legacy = deepcopy(archive)
        legacy["schema_version"] = version
        legacy["settings"].pop("vehicle_color")
        legacy.pop("charger_pricing_history")
        legacy.pop("charger_energy")
        legacy.pop("charger_pricing")
        legacy.pop("observed_events")
        if version < 6:
            legacy.pop("charger")
        if version < 3:
            legacy.pop("widget_profiles")
            legacy.pop("telemetry_snapshots")
        if version < 2:
            legacy.pop("command_history")
        if version < 4:
            legacy["settings"].pop("pricing_method")
            for charge in legacy["charges"]:
                charge.pop("ended_at")
                charge.pop("pricing_method")
                charge.pop("pricing")
        assert destination.import_data(legacy) == {"ok": True, "mode": "unconfigured"}
        with sqlite3.connect(destination.path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM charger_pricing").fetchone()[0] == 0
            session_count = connection.execute("SELECT COUNT(*) FROM charger_sessions").fetchone()[0]
        assert session_count == (1 if version in (6, 7) else 0)


def test_export_rejects_archives_over_the_existing_restore_budget(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    monkeypatch.setattr("pulse.domain.MAX_V15_IMPORT_BYTES", 1)
    with pytest.raises(DomainError) as error:
        store.export_data()
    assert error.value.status == 413


@pytest.mark.parametrize("mutation", ["missing_session", "duplicate", "cost", "timestamp"])
def test_invalid_pricing_archive_is_rejected_before_import_mutates_store(tmp_path, mutation):
    source = make_store(tmp_path, "source.sqlite3")
    record = put_session(source)
    source.update_settings({"pricing_method": "pvpc"})
    source.save_charger_pricing(scope(), record, quote_for(record))
    invalid = deepcopy(source.export_data())
    entry = invalid["charger_pricing"][0]
    if mutation == "missing_session":
        entry["session_id"] = "f" * 32
    elif mutation == "duplicate":
        invalid["charger_pricing"].append(deepcopy(entry))
    elif mutation == "cost":
        entry["quote"]["cost_eur"] += 0.1
    else:
        entry["quote"]["pricing"]["calculated_at"] = "not-a-timestamp"

    destination = make_store(tmp_path, "destination.sqlite3")
    prior = destination.add_charge({"started_at": "2026-10-08T10:00:00Z", "kwh": 1.0})
    before = destination.export_data()
    with pytest.raises(DomainError):
        destination.validate_import(invalid)
    with pytest.raises(DomainError):
        destination.import_data(invalid)
    after = destination.export_data()
    before.pop("exported_at")
    after.pop("exported_at")
    assert after == before
    assert destination.get_charge(prior["id"]) == prior


def test_retention_cascades_ledger_for_pruned_session(tmp_path):
    store = make_store(tmp_path)
    records = []
    for index in range(1, MAX_CHARGER_SESSIONS + 1):
        ended = NOW - timedelta(seconds=MAX_CHARGER_SESSIONS - index + 10)
        started = ended - timedelta(hours=1)
        records.append(session(
            index,
            started_at=started.isoformat().replace("+00:00", "Z"),
            ended_at=ended.isoformat().replace("+00:00", "Z"),
            active_seconds=3_000,
            received_at="2026-10-08T14:59:00Z",
        ))
    store.ingest_charger(scope(), sessions=records)
    oldest = records[0]
    store.update_settings({"pricing_method": "pvpc"})
    assert store.save_charger_pricing(scope(), oldest, quote_for(oldest)) == {"changed": True}

    newest = session(
        MAX_CHARGER_SESSIONS + 1,
        started_at="2026-10-08T13:59:59Z",
        ended_at="2026-10-08T14:59:59Z",
        received_at="2026-10-08T14:59:59Z",
        active_seconds=3_000,
    )
    store.ingest_charger(scope(), sessions=[newest])
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM charger_pricing WHERE session_id = ?", (oldest["id"],)
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM charger_sessions WHERE id = ?", (oldest["id"],)
        ).fetchone()[0] == 0
