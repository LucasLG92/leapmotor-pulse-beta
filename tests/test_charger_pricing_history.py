"""Behavior tests for the bounded PVPC quote history."""

from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
import json
import sqlite3
import uuid
from zoneinfo import ZoneInfo

import pytest

from pulse import pvpc
from pulse.charger_store import session_id as canonical_session_id
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


def history(store, record=None, *, limit=10, offset=0):
    record = record or session()
    return store.charger_pricing_history(
        scope(), record["id"], limit=limit, offset=offset
    )


def test_real_quote_insert_creates_one_private_history_event_and_duplicate_does_not(tmp_path):
    store = make_store(tmp_path)
    record = put_session(store)
    store.update_settings({"pricing_method": "pvpc"})
    quote = quote_for(record)

    assert store.save_charger_pricing(scope(), record, quote) == {"changed": True}
    assert store.save_charger_pricing(scope(), record, quote_for(record, price=0.25)) == {
        "changed": False
    }
    stored_quote = store.priced_charger_sessions(scope())["items"][0]["pvpc"]

    page = history(store, record)
    assert page["total"] == 1
    assert page["retention_limit"] == 2_048
    item = page["items"][0]
    assert set(item) == {
        "id", "kind", "recorded_at", "session", "quote", "corrected_session"
    }
    assert item["kind"] == "priced"
    assert item["session"] == {
        "started_at": "2026-10-08T12:00:00.000000Z",
        "ended_at": "2026-10-08T13:00:00.000000Z",
        "kwh": 10.0,
    }
    assert item["quote"] == stored_quote
    assert item["corrected_session"] is None
    assert "scope" not in item and "upstream_id" not in item["session"]
    assert SCHEMA_VERSION == 19
    assert EXPORT_VERSION == 15

    with pytest.raises(DomainError) as error:
        store.charger_pricing_history(scope(2), record["id"])
    assert error.value.status == 404


def test_newer_kwh_correction_preserves_old_quote_then_repricing_appends_again(tmp_path):
    store = make_store(tmp_path)
    record = put_session(store)
    store.update_settings({"pricing_method": "pvpc"})
    store.save_charger_pricing(scope(), record, quote_for(record))
    original_quote = store.priced_charger_sessions(scope())["items"][0]["pvpc"]

    corrected = {
        **record,
        "received_at": "2026-10-08T14:05:00Z",
        "kwh": 11.0,
    }
    assert store.ingest_charger(scope(), sessions=[corrected]) == {"changed": True}
    page = history(store, record)
    assert page["total"] == 2
    invalidated, priced = page["items"]
    assert [invalidated["kind"], priced["kind"]] == ["invalidated", "priced"]
    assert invalidated["session"]["kwh"] == 10.0
    assert invalidated["quote"] == original_quote
    assert invalidated["corrected_session"]["kwh"] == 11.0
    assert priced["corrected_session"] is None
    assert store.priced_charger_sessions(scope())["items"][0]["pvpc"] is None

    corrected_session = store.charger_sessions(scope())["items"][0]
    corrected_quote = quote_for(corrected_session)
    assert store.save_charger_pricing(scope(), corrected_session, corrected_quote) == {
        "changed": True
    }
    page = history(store, record)
    assert page["total"] == 3
    assert [item["kind"] for item in page["items"]] == [
        "priced", "invalidated", "priced"
    ]
    stored_corrected_quote = store.priced_charger_sessions(scope())["items"][0]["pvpc"]
    assert page["items"][0]["quote"] == stored_corrected_quote


@pytest.mark.parametrize(
    "changes",
    [
        {"kwh": 0.0},
        {"ended_at": "2026-10-08T14:00:00Z"},
    ],
    ids=["zero-energy", "end-time"],
)
def test_energy_or_end_time_correction_invalidates_saved_quote(tmp_path, changes):
    store = make_store(tmp_path)
    record = put_session(store)
    store.update_settings({"pricing_method": "pvpc"})
    store.save_charger_pricing(scope(), record, quote_for(record))
    old_quote = store.priced_charger_sessions(scope())["items"][0]["pvpc"]

    corrected = {
        **record,
        **changes,
        "received_at": "2026-10-08T14:06:00Z",
    }
    assert store.ingest_charger(scope(), sessions=[corrected]) == {"changed": True}
    page = history(store, record)
    assert page["total"] == 2
    invalidated = page["items"][0]
    assert invalidated["kind"] == "invalidated"
    assert invalidated["quote"] == old_quote
    assert invalidated["corrected_session"]["kwh"] == changes.get("kwh", 10.0)
    assert invalidated["corrected_session"]["ended_at"].startswith(
        changes.get("ended_at", "2026-10-08T13:00:00Z")[:19]
    )


def test_received_at_only_and_older_receipt_do_not_create_correction_events(tmp_path):
    store = make_store(tmp_path)
    record = put_session(store)
    store.update_settings({"pricing_method": "pvpc"})
    store.save_charger_pricing(scope(), record, quote_for(record))

    received_only = {
        **record,
        "received_at": "2026-10-08T14:05:00Z",
        "active_seconds": 3_100.0,
    }
    assert store.ingest_charger(scope(), sessions=[received_only]) == {"changed": True}
    stale_correction = {
        **received_only,
        "received_at": "2026-10-08T14:04:00Z",
        "kwh": 11.0,
    }
    assert store.ingest_charger(scope(), sessions=[stale_correction]) == {"changed": False}
    assert history(store, record)["total"] == 1
    assert store.priced_charger_sessions(scope())["items"][0]["pvpc"] is not None


def test_stale_quote_candidate_after_correction_does_not_create_history(tmp_path):
    store = make_store(tmp_path)
    old = put_session(store)
    corrected = {**old, "received_at": "2026-10-08T14:05:00Z", "kwh": 11.0}
    store.ingest_charger(scope(), sessions=[corrected])
    store.update_settings({"pricing_method": "pvpc"})

    assert store.save_charger_pricing(scope(), old, quote_for(old)) == {"changed": False}
    assert history(store, old)["total"] == 0


def test_quote_and_history_insert_roll_back_together_on_history_write_failure(tmp_path):
    store = make_store(tmp_path)
    record = put_session(store)
    store.update_settings({"pricing_method": "pvpc"})
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            """CREATE TRIGGER fail_history_insert BEFORE INSERT ON charger_pricing_history
               BEGIN SELECT RAISE(ABORT, 'injected write failure'); END"""
        )

    with pytest.raises(DomainError) as error:
        store.save_charger_pricing(scope(), record, quote_for(record))
    assert error.value.status == 500
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM charger_pricing").fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM charger_pricing_history"
        ).fetchone()[0] == 0


def test_v12_migration_creates_explicit_migration_baseline(tmp_path):
    store = make_store(tmp_path)
    record = put_session(store)
    store.update_settings({"pricing_method": "pvpc"})
    store.save_charger_pricing(scope(), record, quote_for(record))
    quote = store.priced_charger_sessions(scope())["items"][0]["pvpc"]

    with sqlite3.connect(store.path) as connection:
        downgrade_telemetry_to_v15(connection)
        connection.execute("DROP TABLE charger_pricing_history")
        connection.execute("ALTER TABLE settings DROP COLUMN vehicle_color")
        connection.execute("DROP TABLE IF EXISTS manual_charge_requests")
        connection.execute("PRAGMA user_version = 12")
    migrated = Store(store.path, clock=Clock())

    page = history(migrated, record)
    assert page["total"] == 1
    assert page["items"][0]["kind"] == "baseline"
    assert page["items"][0]["quote"] == quote
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 19
        assert connection.execute(
            "PRAGMA foreign_key_list(charger_pricing_history)"
        ).fetchone()[6] == "CASCADE"


def test_json11_restore_preserves_events_and_json10_restore_creates_archive_baseline(tmp_path):
    source = make_store(tmp_path, "source.sqlite3")
    record = put_session(source)
    source.update_settings({"pricing_method": "pvpc"})
    source.save_charger_pricing(scope(), record, quote_for(record))
    archive11 = source.export_data()
    assert archive11["schema_version"] == 15
    archive11["schema_version"] = 11  # Preserve this historical compatibility fixture.
    archive11["settings"].pop("vehicle_color")
    original_events = deepcopy(archive11["charger_pricing_history"])

    destination11 = make_store(tmp_path, "destination11.sqlite3")
    assert destination11.import_data(archive11) == {"ok": True, "mode": "unconfigured"}
    assert destination11.export_data()["charger_pricing_history"] == original_events

    archive10 = deepcopy(archive11)
    archive10["schema_version"] = 10
    archive10.pop("charger_pricing_history")
    destination10 = make_store(tmp_path, "destination10.sqlite3")
    assert destination10.import_data(archive10) == {"ok": True, "mode": "unconfigured"}
    restored = history(destination10, record)
    assert restored["total"] == 1
    assert restored["items"][0]["kind"] == "baseline"
    assert restored["items"][0]["quote"] == original_events[0]["quote"]


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown_field", "nan", "future_recorded_at", "future_pvpc_download",
        "wrong_reference", "wrong_scope", "boolean_energy", "bad_quote",
    ],
)
def test_import_rejects_corrupt_or_future_history_before_mutation(tmp_path, mutation):
    source = make_store(tmp_path, "source.sqlite3")
    record = put_session(source)
    source.update_settings({"pricing_method": "pvpc"})
    source.save_charger_pricing(scope(), record, quote_for(record))
    archive = deepcopy(source.export_data())
    event = archive["charger_pricing_history"][0]
    if mutation == "unknown_field":
        event["extra"] = "rejected"
    elif mutation == "nan":
        event["quote"]["cost_eur"] = float("nan")
    elif mutation == "future_recorded_at":
        event["recorded_at"] = "2026-10-08T15:01:01Z"
    elif mutation == "future_pvpc_download":
        event["quote"]["pricing"]["intervals"][0]["price_fetched_at"] = (
            "2026-10-08T15:01:01Z"
        )
    elif mutation == "wrong_reference":
        event["session"]["upstream_id"] += 1
    elif mutation == "wrong_scope":
        event["scope"] = scope(2)
    elif mutation == "boolean_energy":
        event["session"]["kwh"] = True
    else:
        event["quote"]["pricing"]["intervals"][0]["cost_eur"] += 0.1

    destination = make_store(tmp_path, "destination.sqlite3")
    prior = destination.add_charge({"started_at": "2026-10-08T10:00:00Z", "kwh": 1.0})
    before = destination.export_data()
    with pytest.raises(DomainError):
        destination.validate_import(archive)
    with pytest.raises(DomainError):
        destination.import_data(archive)
    after = destination.export_data()
    before.pop("exported_at")
    after.pop("exported_at")
    assert after == before
    assert destination.get_charge(prior["id"]) == prior


def test_history_retention_is_global_and_session_deletion_cascades(tmp_path):
    store = make_store(tmp_path)
    record = put_session(store)
    store.update_settings({"pricing_method": "pvpc"})
    quote = quote_for(record)
    store.save_charger_pricing(scope(), record, quote)
    original_id = history(store, record)["items"][0]["id"]

    with store._transaction() as connection:
        for _ in range(2_047):
            event_id = uuid.uuid4().hex
            event = {
                "id": event_id,
                "scope": scope(),
                "kind": "priced",
                "origin": "pulse",
                "recorded_at": "2026-10-08T15:00:00.000000Z",
                "session": {
                    **record,
                    "started_at": "2026-10-08T12:00:00.000000Z",
                    "ended_at": "2026-10-08T13:00:00.000000Z",
                    "received_at": "2026-10-08T14:00:00.000000Z",
                },
                "quote": quote,
                "corrected_session": None,
            }
            connection.execute(
                """INSERT INTO charger_pricing_history (id, session_id, event_json)
                   VALUES (?, ?, ?)""",
                (event_id, record["id"], json.dumps(event, separators=(",", ":"))),
            )
        canonical = store.charger_sessions(scope())["items"][0]
        store._append_charger_pricing_event(
            connection, "priced", scope(), canonical, quote, origin="pulse"
        )

    page = history(store, record, limit=1)
    assert page["total"] == 2_048
    with sqlite3.connect(store.path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        assert connection.execute(
            "SELECT COUNT(*) FROM charger_pricing_history"
        ).fetchone()[0] == 2_048
        assert connection.execute(
            "SELECT COUNT(*) FROM charger_pricing_history WHERE id = ?", (original_id,)
        ).fetchone()[0] == 0
        connection.execute("DELETE FROM charger_sessions WHERE id = ?", (record["id"],))
        assert connection.execute(
            "SELECT COUNT(*) FROM charger_pricing_history"
        ).fetchone()[0] == 0
