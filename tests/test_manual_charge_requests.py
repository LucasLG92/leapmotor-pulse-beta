"""Server-owned, short-lived idempotency for manual charge creation."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from pulse.domain import DomainError, EXPORT_VERSION, SCHEMA_VERSION, Store


START = "2026-10-01T10:00:00+02:00"
ATTEMPT = {"started_at": START, "kwh": 10}


def _add(store, reservation, owner="owner-a", attempt=ATTEMPT, **values):
    submitted = dict(attempt)
    submitted.update(values)
    return store.add_charge(
        submitted,
        request_id=reservation["id"],
        owner_id=owner,
        request_attempt=attempt,
    )


def test_reservation_replay_is_atomic_and_returns_the_current_charge(tmp_path):
    path = tmp_path / "pulse.sqlite3"
    store = Store(path)
    reservation = store.reserve_charge_creation("owner-a")
    assert len(reservation["id"]) == 32
    assert all(character in "0123456789abcdef" for character in reservation["id"])
    assert reservation["expires_at"]

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(_add, store, reservation) for _ in range(8)]
        charges = [future.result(timeout=20) for future in futures]
    assert len({charge["id"] for charge in charges}) == 1
    assert store.list_charges()["total"] == 1

    edited = store.update_charge(
        charges[0]["id"], {"kwh": 11, "tariff_eur_kwh": 0.15}
    )
    current = store.preflight_charge_creation("owner-a", reservation["id"], ATTEMPT)
    assert current["charge"] == edited

    reopened = Store(path)
    replay = _add(reopened, reservation)
    assert replay == edited
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 19
        assert connection.execute("SELECT COUNT(*) FROM manual_charge_requests").fetchone()[0] == 1
    assert SCHEMA_VERSION == 19
    archive = reopened.export_data()
    assert archive["schema_version"] == EXPORT_VERSION == 15
    assert "manual_charge_requests" not in str(archive)


def test_canonical_input_survives_settings_change_but_conflicts_and_owners_fail(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3")
    reservation = store.reserve_charge_creation("owner-a")
    first = _add(store, reservation)
    store.update_settings({"pricing_method": "pvpc"})

    equivalent = {"started_at": "2026-10-01T08:00:00Z", "kwh": 10.0}
    preflight = store.preflight_charge_creation("owner-a", reservation["id"], equivalent)
    assert preflight["charge"] == first
    # The method was resolved after the original body, so a later setting must not
    # change replay identity or require a new PVPC snapshot.
    assert _add(store, reservation, attempt=equivalent, pricing_method="pvpc") == first

    with pytest.raises(DomainError) as changed:
        store.preflight_charge_creation(
            "owner-a", reservation["id"], {"started_at": START, "kwh": 11}
        )
    assert changed.value.status == 409
    for owner, request_id in (("owner-b", reservation["id"]), ("owner-a", "f" * 32)):
        with pytest.raises(DomainError) as rejected:
            store.preflight_charge_creation(owner, request_id, ATTEMPT)
        assert rejected.value.status == 409
    assert store.list_charges()["total"] == 1


def test_deleted_linked_charge_cannot_be_recreated(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3")
    reservation = store.reserve_charge_creation("owner-a")
    charge = _add(store, reservation)
    store.delete_charge(charge["id"])

    with pytest.raises(DomainError) as replay:
        store.preflight_charge_creation("owner-a", reservation["id"], ATTEMPT)
    assert replay.value.status == 409
    with pytest.raises(DomainError) as direct_retry:
        _add(store, reservation)
    assert direct_retry.value.status == 409
    assert store.list_charges()["total"] == 0


def test_corrupt_partial_success_link_is_rejected_without_recreating_charge(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3")
    for field in ("attempt_fingerprint", "charge_id"):
        reservation = store.reserve_charge_creation("owner-a")
        charge = _add(store, reservation)
        with sqlite3.connect(store.path) as connection:
            connection.execute("PRAGMA ignore_check_constraints = ON")
            connection.execute(
                f"UPDATE manual_charge_requests SET {field} = NULL WHERE id = ?",
                (reservation["id"],),
            )

        with pytest.raises(DomainError) as preflight:
            store.preflight_charge_creation("owner-a", reservation["id"], ATTEMPT)
        assert preflight.value.status == 409
        with pytest.raises(DomainError) as final_add:
            _add(store, reservation)
        assert final_add.value.status == 409
        assert store.get_charge(charge["id"]) == charge
        assert store.list_charges()["total"] == (1 if field == "attempt_fingerprint" else 2)


def test_charge_and_ledger_link_roll_back_together_on_write_failure(tmp_path):
    path = tmp_path / "pulse.sqlite3"
    store = Store(path)
    reservation = store.reserve_charge_creation("owner-a")
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TRIGGER reject_request_link BEFORE UPDATE ON manual_charge_requests
               BEGIN SELECT RAISE(ABORT, 'private trigger detail'); END"""
        )

    with pytest.raises(DomainError) as failure:
        _add(store, reservation)
    assert "private trigger detail" not in str(failure.value)
    assert store.list_charges()["total"] == 0
    assert store.preflight_charge_creation(
        "owner-a", reservation["id"], ATTEMPT
    )["charge"] is None

    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER reject_request_link")
    created = _add(store, reservation)
    assert store.list_charges()["items"] == [created]


def test_expiry_is_checked_on_replay_and_pruned_on_the_next_reservation(tmp_path):
    now = [datetime(2026, 10, 1, tzinfo=timezone.utc)]
    path = tmp_path / "pulse.sqlite3"
    store = Store(path, clock=lambda: now[0])
    reservation = store.reserve_charge_creation("owner-a")
    charge = _add(store, reservation)
    now[0] += timedelta(days=7)

    with pytest.raises(DomainError) as expired:
        store.preflight_charge_creation("owner-a", reservation["id"], ATTEMPT)
    assert expired.value.status == 409
    with pytest.raises(DomainError) as expired_add:
        _add(store, reservation)
    assert expired_add.value.status == 409

    renewed = store.reserve_charge_creation("owner-a")
    assert renewed["id"] != reservation["id"]
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM manual_charge_requests").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM manual_charges").fetchone()[0] == 1
    assert store.get_charge(charge["id"]) == charge


def test_clock_rollback_and_invalid_reservation_window_reject_replay(tmp_path):
    now = [datetime(2026, 10, 1, tzinfo=timezone.utc)]
    path = tmp_path / "pulse.sqlite3"
    store = Store(path, clock=lambda: now[0])
    reservation = store.reserve_charge_creation("owner-a")

    with sqlite3.connect(path) as connection:
        created_at = now[0] + timedelta(minutes=1)
        expires_at = created_at + timedelta(days=7)
        connection.execute(
            "UPDATE manual_charge_requests SET created_at = ?, expires_at = ? WHERE id = ?",
            (created_at.isoformat().replace("+00:00", "Z"),
             expires_at.isoformat().replace("+00:00", "Z"), reservation["id"]),
        )
    with pytest.raises(DomainError) as clock_rewound:
        store.preflight_charge_creation("owner-a", reservation["id"], ATTEMPT)
    assert clock_rewound.value.status == 409
    with pytest.raises(DomainError) as clock_rewound_add:
        _add(store, reservation)
    assert clock_rewound_add.value.status == 409

    with sqlite3.connect(path) as connection:
        created_at = now[0].isoformat().replace("+00:00", "Z")
        expires_at = (now[0] + timedelta(days=8)).isoformat().replace("+00:00", "Z")
        connection.execute(
            "UPDATE manual_charge_requests SET created_at = ?, expires_at = ? WHERE id = ?",
            (created_at, expires_at, reservation["id"]),
        )
    with pytest.raises(DomainError) as invalid_ttl:
        store.preflight_charge_creation("owner-a", reservation["id"], ATTEMPT)
    assert invalid_ttl.value.status == 409
    assert store.list_charges()["total"] == 0

    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE manual_charge_requests SET expires_at = 'not-a-date' WHERE id = ?",
            (reservation["id"],),
        )
    with pytest.raises(DomainError) as malformed_date:
        store.preflight_charge_creation("owner-a", reservation["id"], ATTEMPT)
    assert malformed_date.value.status == 409

    too_long = {
        "started_at": "2026-10-01T10:00:00Z",
        "ended_at": "2026-10-04T10:00:01Z",
        "kwh": 10,
    }
    with pytest.raises(DomainError) as invalid_charge_window:
        store.preflight_charge_creation("owner-a", reservation["id"], too_long)
    assert invalid_charge_window.value.status == 400


def test_owner_and_global_capacity_and_durable_expired_pruning(tmp_path):
    path = tmp_path / "pulse.sqlite3"
    store = Store(path)
    for _ in range(128):
        store.reserve_charge_creation("owner-full")
    with pytest.raises(DomainError) as owner_full:
        store.reserve_charge_creation("owner-full")
    assert owner_full.value.status == 429

    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM manual_charge_requests")
        now = datetime.now(timezone.utc)
        created = now.isoformat(timespec="microseconds").replace("+00:00", "Z")
        expires = (now + timedelta(days=7)).isoformat(timespec="microseconds").replace("+00:00", "Z")
        connection.executemany(
            """INSERT INTO manual_charge_requests (id, owner_id, created_at, expires_at)
               VALUES (?, ?, ?, ?)""",
            [
                (f"{index:032x}", f"owner-{index // 128}", created, expires)
                for index in range(2048)
            ],
        )
    with pytest.raises(DomainError) as global_full:
        store.reserve_charge_creation("owner-new")
    assert global_full.value.status == 429
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM manual_charge_requests").fetchone()[0] == 2048

    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE manual_charge_requests SET expires_at = '2000-01-01T00:00:00Z'")
    renewed = store.reserve_charge_creation("owner-new")
    assert len(renewed["id"]) == 32
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM manual_charge_requests").fetchone()[0] == 1


def test_valid_import_invalidates_reservations_but_failed_import_preserves_them(tmp_path):
    path = tmp_path / "pulse.sqlite3"
    store = Store(path)
    reservation = store.reserve_charge_creation("owner-a")
    charge = _add(store, reservation)
    archive = store.export_data()

    invalid = dict(archive)
    invalid["schema_version"] = EXPORT_VERSION + 1
    with pytest.raises(DomainError):
        store.import_data(invalid)
    assert store.preflight_charge_creation("owner-a", reservation["id"], ATTEMPT)["charge"] == charge

    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TRIGGER reject_charge_restore BEFORE INSERT ON manual_charges
               BEGIN SELECT RAISE(ABORT, 'private trigger detail'); END"""
        )
    with pytest.raises(DomainError) as failed_restore:
        store.import_data(archive)
    assert "private trigger detail" not in str(failed_restore.value)
    assert store.preflight_charge_creation("owner-a", reservation["id"], ATTEMPT)["charge"] == charge

    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER reject_charge_restore")
    store.import_data(archive)
    with pytest.raises(DomainError) as invalidated:
        store.preflight_charge_creation("owner-a", reservation["id"], ATTEMPT)
    assert invalidated.value.status == 409


def test_raw_sqlite_backup_keeps_successful_request_ledger(tmp_path):
    source_path = tmp_path / "pulse.sqlite3"
    backup_path = tmp_path / "backup.sqlite3"
    store = Store(source_path)
    reservation = store.reserve_charge_creation("owner-a")
    charge = _add(store, reservation)

    with sqlite3.connect(source_path) as source, sqlite3.connect(backup_path) as target:
        source.backup(target)
    restored = Store(backup_path)
    assert restored.preflight_charge_creation("owner-a", reservation["id"], ATTEMPT)["charge"] == charge


def test_schema_18_migrates_to_19_without_changing_existing_charges(tmp_path):
    path = tmp_path / "pulse.sqlite3"
    store = Store(path)
    charge = store.add_charge(ATTEMPT)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE manual_charge_requests")
        connection.execute("PRAGMA user_version = 18")

    migrated = Store(path)
    assert migrated.get_charge(charge["id"]) == charge
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 19
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='manual_charge_requests'"
        ).fetchone() is not None
        valid_window = ("2026-10-01T00:00:00Z", "2026-10-08T00:00:00Z")
        invalid_links = (
            ("a" * 32, "owner-a", *valid_window, None, "b" * 32),
            ("c" * 32, "owner-a", *valid_window, "d" * 64, None),
            ("e" * 32, "owner-a", *valid_window, "g" * 64, "f" * 32),
            ("1" * 32, "owner-a", *valid_window, "2" * 64, "g" * 32),
        )
        for row in invalid_links:
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    """INSERT INTO manual_charge_requests
                       (id, owner_id, created_at, expires_at, attempt_fingerprint, charge_id)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    row,
                )
