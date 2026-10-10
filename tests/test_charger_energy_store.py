from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from pulse.charger_energy_store import (
    CONTRACT_REVISION,
    MAX_ENERGY_EPOCHS,
    MAX_ENERGY_SCOPES,
    MAX_ENERGY_SLOTS,
    ChargerEnergyError,
    ChargerEnergyStoreMixin,
    energy_epoch_id,
    validate_charger_energy_archive,
    validate_charger_energy_future_dates,
    validate_energy_slot,
)
from pulse.domain import DomainError


class EnergyStore(ChargerEnergyStoreMixin):
    """Small persisted host for testing the mixin without Store schema changes."""

    def __init__(self, path, now: datetime | None = None):
        self.path = str(path)
        self.clock = now or datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._migrate_v11_to_v12(connection)
            connection.commit()

    def _connect(self):
        connection = sqlite3.connect(self.path, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _transaction(self):
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def _read_transaction(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _clock_now(self):
        return self.clock

    def _now(self):
        return self.clock.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def scope(number: int = 1) -> str:
    return f"{number:064x}"


def identity(entity: str = "home", loadpoint: str = "Garage") -> dict[str, str]:
    return {
        "entity": entity,
        "loadpoint": loadpoint,
        "contract_revision": CONTRACT_REVISION,
    }


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def observe(store: EnergyStore, *, at: datetime, source_scope: str | None = None, who=None):
    result = store.observe_charger_energy(
        source_scope or scope(), who if who is not None else identity(), received_at=iso(at)
    )
    return result["epoch"]


def slot(epoch_id: str, started_at: datetime, *, kwh=2.5, return_kwh=0.0, received_at=None):
    ended_at = started_at + timedelta(minutes=15)
    return {
        "epoch_id": epoch_id,
        "started_at": iso(started_at),
        "ended_at": iso(ended_at),
        "kwh": kwh,
        "return_kwh": return_kwh,
        "received_at": iso(received_at or ended_at),
    }


def test_energy_slot_normalizes_utc_and_validates_calendar_boundary():
    normalized = validate_energy_slot({
        "epoch_id": "a" * 32,
        "started_at": "2026-01-01T01:15:00+01:00",
        "ended_at": "2026-01-01T00:30:00Z",
        "kwh": 4,
        "return_kwh": 0,
        "received_at": "2026-01-01T00:45:00Z",
    })
    assert normalized == {
        "epoch_id": "a" * 32,
        "started_at": "2026-01-01T00:15:00.000000Z",
        "ended_at": "2026-01-01T00:30:00.000000Z",
        "kwh": 4.0,
        "return_kwh": 0.0,
        "received_at": "2026-01-01T00:45:00.000000Z",
    }


@pytest.mark.parametrize("change", [
    {"started_at": "2026-01-01T00:01:00Z"},
    {"started_at": "2026-01-01T00:15:00"},
    {"ended_at": "2026-01-01T00:29:59Z"},
    {"received_at": "2026-01-01T00:14:59Z"},
    {"started_at": "2013-12-31T23:45:00Z", "ended_at": "2014-01-01T00:00:00Z"},
    {"kwh": True},
    {"kwh": -0.1},
    {"kwh": 500.01},
    {"kwh": float("nan")},
    {"return_kwh": float("inf")},
    {"return_kwh": 10**400},
    {"extra": "field"},
])
def test_energy_slot_rejects_invalid_times_numbers_and_shapes(change):
    start = "2026-01-01T00:15:00Z"
    end = "2026-01-01T00:30:00Z"
    item = {
        "epoch_id": "b" * 32,
        "started_at": start,
        "ended_at": end,
        "kwh": 1,
        "return_kwh": 0,
        "received_at": end,
    }
    if change.get("started_at", start).startswith("2013"):
        item.update({"started_at": start, "ended_at": end})
    item.update(change)
    with pytest.raises(ChargerEnergyError):
        validate_energy_slot(item)


def test_observation_change_absence_and_old_receipt_do_not_reopen(tmp_path):
    store = EnergyStore(tmp_path / "energy.sqlite3")
    start = datetime(2026, 10, 9, 10, tzinfo=timezone.utc)
    first = store.observe_charger_energy(
        scope(), identity(loadpoint=" Garage "), received_at=iso(start)
    )
    assert first["epoch"]["loadpoint"] == "Garage"
    assert first["epoch"]["id"] == energy_epoch_id(
        scope(), "home", "Garage", CONTRACT_REVISION, iso(start)
    )

    same = store.observe_charger_energy(
        scope(), identity(), received_at=iso(start + timedelta(minutes=2))
    )
    assert same["changed"] is True
    assert same["epoch"]["id"] == first["epoch"]["id"]
    assert same["epoch"]["last_seen_at"] == iso(start + timedelta(minutes=2))

    changed_at = start + timedelta(minutes=4)
    changed = store.observe_charger_energy(
        scope(), identity(entity="other"), received_at=iso(changed_at)
    )
    assert changed["epoch"]["entity"] == "other"
    assert changed["epoch"]["id"] != first["epoch"]["id"]
    with store._read_transaction() as connection:
        closed = connection.execute(
            "SELECT closed_at FROM charger_energy_epochs WHERE id = ?",
            (first["epoch"]["id"],),
        ).fetchone()[0]
    assert closed == iso(changed_at)

    absent_at = start + timedelta(minutes=5)
    absent = store.observe_charger_energy(scope(), None, received_at=iso(absent_at))
    assert absent == {"changed": True, "epoch": None}
    stale = store.observe_charger_energy(
        scope(), identity(), received_at=iso(absent_at - timedelta(seconds=1))
    )
    assert stale == {"changed": False, "epoch": None}
    reopened = store.observe_charger_energy(
        scope(), identity(), received_at=iso(absent_at + timedelta(seconds=1))
    )
    assert reopened["epoch"]["id"] != changed["epoch"]["id"]


def test_restart_closes_epoch_and_requires_a_later_observation(tmp_path):
    store = EnergyStore(tmp_path / "restart.sqlite3")
    start = datetime(2026, 10, 9, 10, tzinfo=timezone.utc)
    current = observe(store, at=start)
    ended = store.end_charger_energy_epoch(scope(), received_at=iso(start + timedelta(minutes=1)))
    assert ended["changed"] is True
    assert ended["epoch"]["id"] == current["id"]
    assert ended["epoch"]["closed_at"] == iso(start + timedelta(minutes=1))
    assert store.end_charger_energy_epoch(scope(), received_at=iso(start))["changed"] is False
    same_time = observe(store, at=start + timedelta(minutes=1))
    assert same_time is None
    resumed = observe(store, at=start + timedelta(minutes=2))
    assert resumed["id"] != current["id"]


def test_ingest_corrects_only_on_later_receipt_and_keeps_gaps(tmp_path):
    store = EnergyStore(tmp_path / "ingest.sqlite3")
    first_seen = datetime(2026, 10, 9, 10, tzinfo=timezone.utc)
    epoch = observe(store, at=first_seen)
    last_seen = first_seen + timedelta(minutes=45)
    store.observe_charger_energy(scope(), identity(), received_at=iso(last_seen))
    first = slot(epoch["id"], first_seen, received_at=last_seen)
    second = slot(
        epoch["id"], first_seen + timedelta(minutes=30), kwh=3,
        received_at=last_seen,
    )
    result = store.ingest_charger_energy(scope(), epoch["id"], [first, second])
    assert result == {"changed": True, "saved": 2}
    summary = store.charger_energy_summary(scope())
    assert summary == {
        "retained_intervals": 2,
        "total_kwh": 5.5,
        "first_started_at": iso(first_seen),
        "last_ended_at": iso(first_seen + timedelta(minutes=45)),
        "last_received_at": iso(last_seen),
        "retention_limit": MAX_ENERGY_SLOTS,
        "validation": "not_qualified",
    }

    corrected = slot(
        epoch["id"], first_seen, kwh=4,
        received_at=last_seen + timedelta(seconds=1),
    )
    assert store.ingest_charger_energy(scope(), epoch["id"], [corrected]) == {
        "changed": True, "saved": 1,
    }
    assert store.ingest_charger_energy(scope(), epoch["id"], [first]) == {
        "changed": False, "saved": 0,
    }
    with pytest.raises(DomainError, match="mismo received_at"):
        store.ingest_charger_energy(
            scope(), epoch["id"], [{**corrected, "kwh": 7}]
        )
    assert store.charger_energy_summary(scope())["total_kwh"] == 7


def test_ingest_validates_whole_batch_before_writing_and_ignores_old_epoch(tmp_path):
    store = EnergyStore(tmp_path / "atomic.sqlite3")
    started = datetime(2026, 10, 9, 10, tzinfo=timezone.utc)
    epoch = observe(store, at=started)
    last_seen = started + timedelta(minutes=30)
    store.observe_charger_energy(scope(), identity(), received_at=iso(last_seen))
    good = slot(epoch["id"], started, received_at=last_seen)
    invalid = {
        **slot(epoch["id"], started + timedelta(minutes=15), received_at=last_seen),
        "kwh": True,
    }
    with pytest.raises(DomainError):
        store.ingest_charger_energy(scope(), epoch["id"], [good, invalid])
    assert store.charger_energy_summary(scope())["retained_intervals"] == 0

    replacement = store.observe_charger_energy(
        scope(), identity(entity="new"), received_at=iso(last_seen + timedelta(minutes=1))
    )
    assert store.ingest_charger_energy(scope(), epoch["id"], [good]) == {
        "changed": False, "saved": 0,
    }
    assert replacement["epoch"]["entity"] == "new"


def test_duplicate_identical_slots_coalesce_but_conflicting_equal_receipt_rejects(tmp_path):
    store = EnergyStore(tmp_path / "duplicates.sqlite3")
    start = datetime(2026, 10, 9, 10, tzinfo=timezone.utc)
    epoch = observe(store, at=start)
    last_seen = start + timedelta(minutes=15)
    store.observe_charger_energy(scope(), identity(), received_at=iso(last_seen))
    item = slot(epoch["id"], start, received_at=last_seen)
    assert store.ingest_charger_energy(scope(), epoch["id"], [item, item]) == {
        "changed": True, "saved": 1,
    }
    with pytest.raises(DomainError, match="mismo received_at"):
        store.ingest_charger_energy(
            scope(), epoch["id"], [item, {**item, "return_kwh": 1}]
        )
    assert store.charger_energy_summary(scope())["total_kwh"] == 2.5


def test_archive_is_strict_and_future_bounds_apply_to_every_timestamp(tmp_path):
    store = EnergyStore(tmp_path / "archive.sqlite3")
    start = datetime(2026, 10, 9, 10, tzinfo=timezone.utc)
    epoch = observe(store, at=start)
    store.observe_charger_energy(
        scope(), identity(), received_at=iso(start + timedelta(minutes=15))
    )
    item = slot(epoch["id"], start, received_at=start + timedelta(minutes=15))
    store.ingest_charger_energy(scope(), epoch["id"], [item])
    with store._read_transaction() as connection:
        archive = store._export_charger_energy(connection)
    assert validate_charger_energy_archive(archive) == archive
    validate_charger_energy_future_dates(archive, start + timedelta(days=1))

    duplicate = {
        "epochs": [*archive["epochs"], archive["epochs"][0]],
        "intervals": archive["intervals"],
    }
    with pytest.raises(ChargerEnergyError, match="duplicadas"):
        validate_charger_energy_archive(duplicate)
    wrong_reference = {
        **archive,
        "intervals": [{**archive["intervals"][0], "epoch_id": "c" * 32}],
    }
    with pytest.raises(ChargerEnergyError, match="referenciar"):
        validate_charger_energy_archive(wrong_reference)
    future = {
        **archive,
        "epochs": [{
            **archive["epochs"][0],
            "closed_at": iso(start + timedelta(minutes=16, seconds=1)),
        }],
    }
    with pytest.raises(ChargerEnergyError, match="60 segundos"):
        validate_charger_energy_future_dates(future, start)


def test_archive_restore_closes_open_epochs_and_preserves_candidate_history(tmp_path):
    source = EnergyStore(tmp_path / "source.sqlite3")
    started = datetime(2026, 10, 9, 10, tzinfo=timezone.utc)
    epoch = observe(source, at=started)
    last_seen = started + timedelta(minutes=15)
    source.observe_charger_energy(scope(), identity(), received_at=iso(last_seen))
    source.ingest_charger_energy(
        scope(), epoch["id"], [slot(epoch["id"], started, received_at=last_seen)]
    )
    with source._read_transaction() as connection:
        archive = source._export_charger_energy(connection)

    restore_time = started + timedelta(days=1)
    destination = EnergyStore(tmp_path / "destination.sqlite3", now=restore_time)
    with destination._transaction() as connection:
        destination._replace_charger_energy_archive(connection, archive)
    with destination._read_transaction() as connection:
        restored = destination._export_charger_energy(connection)
    assert restored["epochs"][0]["closed_at"] == iso(restore_time)
    assert restored["intervals"] == archive["intervals"]

    next_epoch = observe(destination, at=restore_time + timedelta(seconds=1))
    assert next_epoch["id"] != epoch["id"]
    with destination._read_transaction() as connection:
        after = destination._export_charger_energy(connection)
    assert len(after["epochs"]) == 2
    assert len(after["intervals"]) == 1


def test_global_slot_limit_keeps_newest_intervals(tmp_path):
    store = EnergyStore(tmp_path / "slot-retention.sqlite3")
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    last_end = started + timedelta(minutes=15 * (MAX_ENERGY_SLOTS + 1))
    epoch = observe(store, at=started)
    store.observe_charger_energy(scope(), identity(), received_at=iso(last_end))
    intervals = [
        slot(
            epoch["id"],
            started + timedelta(minutes=15 * index),
            kwh=1,
            received_at=last_end,
        )
        for index in range(MAX_ENERGY_SLOTS + 1)
    ]
    for offset in range(0, len(intervals), 100):
        store.ingest_charger_energy(scope(), epoch["id"], intervals[offset:offset + 100])
    with store._read_transaction() as connection:
        rows = connection.execute(
            "SELECT started_at FROM charger_energy_intervals ORDER BY ended_at ASC"
        ).fetchall()
    assert len(rows) == MAX_ENERGY_SLOTS
    assert rows[0]["started_at"] == iso(started + timedelta(minutes=15))
    assert store.charger_energy_summary(scope())["retained_intervals"] == MAX_ENERGY_SLOTS


def test_epoch_cap_prunes_old_closed_epoch_and_scope_cap_rejects_safely(tmp_path):
    store = EnergyStore(tmp_path / "epoch-retention.sqlite3")
    first = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for index in range(MAX_ENERGY_EPOCHS + 1):
        store.observe_charger_energy(
            scope(), identity(entity=f"entity-{index}"),
            received_at=iso(first + timedelta(minutes=index)),
        )
    with store._read_transaction() as connection:
        count = connection.execute("SELECT COUNT(*) FROM charger_energy_epochs").fetchone()[0]
        oldest = connection.execute(
            "SELECT MIN(first_seen_at) FROM charger_energy_epochs"
        ).fetchone()[0]
    assert count == MAX_ENERGY_EPOCHS
    assert oldest == iso(first + timedelta(minutes=1))

    scoped = EnergyStore(tmp_path / "scope-retention.sqlite3")
    for index in range(MAX_ENERGY_SCOPES):
        scoped.observe_charger_energy(
            scope(index + 1), identity(), received_at=iso(first)
        )
    with pytest.raises(DomainError, match="16 scopes"):
        scoped.observe_charger_energy(
            scope(MAX_ENERGY_SCOPES + 1), identity(), received_at=iso(first)
    )
    with scoped._read_transaction() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM charger_energy_epochs"
        ).fetchone()[0]
        assert count == MAX_ENERGY_SCOPES


def test_empty_summary_has_no_cost_or_qualification_claim(tmp_path):
    store = EnergyStore(tmp_path / "empty.sqlite3")
    assert store.charger_energy_summary(scope()) == {
        "retained_intervals": 0,
        "total_kwh": 0.0,
        "first_started_at": None,
        "last_ended_at": None,
        "last_received_at": None,
        "retention_limit": MAX_ENERGY_SLOTS,
        "validation": "not_qualified",
    }
