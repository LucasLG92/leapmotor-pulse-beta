from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import re
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from pulse.domain import DomainError, Store


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: int) -> None:
        self.now += timedelta(**delta)


def _store(tmp_path, clock: Clock) -> Store:
    return Store(tmp_path / "pulse.sqlite3", clock=clock)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _values(clock: Clock, *, owner: str = "owner-a", endpoint: str = "endpoint-a") -> dict:
    return {
        "owner_id": owner,
        "endpoint_hash": _digest(endpoint),
        "session_hash": _digest(f"session-{endpoint}"),
        "name": "  Móvil  ",
        "ciphertext": "gAAAA-synthetic-encrypted-payload",
        "expires_at": (clock.now + timedelta(hours=1)).isoformat(),
    }


def _upsert(store: Store, clock: Clock, **overrides) -> dict:
    values = _values(clock, **{key: value for key, value in overrides.items() if key in {"owner", "endpoint"}})
    values.update({key: value for key, value in overrides.items() if key not in {"owner", "endpoint"}})
    return store.upsert_push_subscription(**values)


def test_owner_scope_hides_foreign_rows_and_global_endpoint_uniqueness(tmp_path):
    clock = Clock()
    store = _store(tmp_path, clock)
    first = _upsert(store, clock)
    second = _upsert(store, clock, owner="owner-b", endpoint="endpoint-b")

    assert re.fullmatch(r"[a-f0-9]{32}", first["id"])
    assert first["name"] == "Móvil"
    assert first["created_at"] == first["updated_at"] == "2026-10-08T12:00:00.000000Z"
    assert first["expires_at"] == "2026-10-08T13:00:00.000000Z"
    assert first["revision"] == 1
    assert first["last_attempt_at"] is None
    assert first["last_accepted_at"] is None
    assert first["last_result"] == "never"
    assert store.list_push_subscriptions("owner-a") == [first]
    assert store.list_push_subscriptions() == [first, second]
    assert store.get_push_subscription("owner-a", first["id"]) == first

    for action in (
        lambda: store.get_push_subscription("owner-b", first["id"]),
        lambda: store.delete_push_subscription("owner-b", first["id"]),
    ):
        with pytest.raises(DomainError) as error:
            action()
        assert error.value.status == 404
        assert str(error.value) == "No se encontró la suscripción Push."
    assert store.list_push_subscriptions("owner-a") == [first]
    with pytest.raises(DomainError) as invalid_id:
        store.get_push_subscription("owner-a", "../invalid")
    assert invalid_id.value.status == 400
    assert store.list_push_subscriptions("owner-a") == [first]

    conflict = _values(clock, owner="owner-secret", endpoint="endpoint-a")
    with pytest.raises(DomainError) as error:
        store.upsert_push_subscription(**conflict)
    assert error.value.status == 409
    assert "owner-secret" not in str(error.value)
    assert store.list_push_subscriptions() == [first, second]
    assert store.delete_push_subscription("owner-a", first["id"]) == {"ok": True}
    assert store.list_push_subscriptions("owner-a") == []


def test_push_demo_enabled_tracks_persisted_demo_state_and_import(tmp_path):
    clock = Clock()
    store = _store(tmp_path, clock)
    unconfigured_archive = store.export_data()

    assert store.push_demo_enabled() is False
    store.set_demo(True)
    assert store.push_demo_enabled() is True
    assert store.import_data(unconfigured_archive) == {"ok": True, "mode": "unconfigured"}
    assert store.push_demo_enabled() is False


def test_upsert_increments_generation_and_preserves_attempt_timestamps(tmp_path):
    clock = Clock()
    store = _store(tmp_path, clock)
    initial = _upsert(store, clock)
    attempt = store.reserve_push_attempt("owner-a", initial["id"], initial["revision"])
    assert attempt["last_result"] == "pending"
    assert store.finish_push_attempt("owner-a", initial["id"], 1, "accepted") is True
    accepted = store.get_push_subscription("owner-a", initial["id"])
    clock.advance(seconds=20)

    refreshed = store.upsert_push_subscription(
        "owner-a",
        initial["endpoint_hash"],
        _digest("rotated-session"),
        "Coche",
        "gAAAA-new-synthetic-ciphertext",
        (clock.now + timedelta(hours=1)).isoformat(),
    )
    assert refreshed["id"] == initial["id"]
    assert refreshed["created_at"] == initial["created_at"]
    assert refreshed["revision"] == 2
    assert refreshed["name"] == "Coche"
    assert refreshed["last_attempt_at"] == accepted["last_attempt_at"]
    assert refreshed["last_accepted_at"] == accepted["last_accepted_at"]
    assert refreshed["last_result"] == "accepted"

    with pytest.raises(DomainError) as cooldown:
        store.reserve_push_attempt("owner-a", initial["id"], 2)
    assert cooldown.value.status == 409
    clock.advance(seconds=40)
    pending = store.reserve_push_attempt("owner-a", initial["id"], 2)
    assert pending["last_result"] == "pending"

    rotated = store.upsert_push_subscription(
        "owner-a",
        initial["endpoint_hash"],
        _digest("rotated-again"),
        "Coche nuevo",
        "gAAAA-latest-synthetic-ciphertext",
        (clock.now + timedelta(hours=1)).isoformat(),
    )
    assert rotated["revision"] == 3
    assert rotated["last_result"] == "uncertain"
    assert rotated["last_attempt_at"] == pending["last_attempt_at"]
    assert rotated["last_accepted_at"] == accepted["last_accepted_at"]
    assert store.finish_push_attempt("owner-a", initial["id"], 2, "accepted") is False


def test_concurrent_rebind_survives_stale_provider_delete_and_exact_revision_deletes(tmp_path):
    clock = Clock()
    store = _store(tmp_path, clock)
    initial = _upsert(store, clock)
    start = threading.Barrier(2)

    def rebind():
        start.wait()
        return store.upsert_push_subscription(
            "owner-a",
            initial["endpoint_hash"],
            _digest("new-session"),
            "Actualizada",
            "gAAAA-current-generation-ciphertext",
            (clock.now + timedelta(hours=1)).isoformat(),
        )

    def stale_provider_delete():
        start.wait()
        return store.delete_push_subscription(
            "owner-a", initial["id"], expected_revision=initial["revision"]
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        rebind_future = executor.submit(rebind)
        stale_delete_future = executor.submit(stale_provider_delete)
        refreshed = rebind_future.result(timeout=10)
        stale_result = stale_delete_future.result(timeout=10)

    current_rows = store.list_push_subscriptions("owner-a")
    assert len(current_rows) == 1
    assert current_rows[0]["name"] == "Actualizada"
    assert current_rows[0]["ciphertext"] == "gAAAA-current-generation-ciphertext"
    assert refreshed == current_rows[0]
    if stale_result == {"ok": False}:
        assert refreshed["id"] == initial["id"]
        assert refreshed["revision"] == 2
    else:
        assert stale_result == {"ok": True}
        assert refreshed["id"] != initial["id"]
        assert refreshed["revision"] == 1
    assert store.delete_push_subscription(
        "owner-a", initial["id"], expected_revision=initial["revision"]
    ) == {"ok": False}

    assert store.delete_push_subscription(
        "owner-a", current_rows[0]["id"], expected_revision=current_rows[0]["revision"]
    ) == {"ok": True}
    assert store.list_push_subscriptions("owner-a") == []


def test_attempt_reservation_enforces_revision_pending_cooldown_and_expiry(tmp_path):
    clock = Clock()
    store = _store(tmp_path, clock)
    row = _upsert(store, clock, expires_at=(clock.now + timedelta(hours=2)).isoformat())
    pending = store.reserve_push_attempt("owner-a", row["id"], 1)
    assert pending["last_attempt_at"] == "2026-10-08T12:00:00.000000Z"
    with pytest.raises(DomainError) as active_lease:
        store.reserve_push_attempt("owner-a", row["id"], 1)
    assert active_lease.value.status == 409
    assert store.finish_push_attempt("owner-a", row["id"], 1, "failed") is True
    failed = store.get_push_subscription("owner-a", row["id"])
    assert failed["last_result"] == "failed"
    assert failed["last_accepted_at"] is None

    with pytest.raises(DomainError) as early:
        store.reserve_push_attempt("owner-a", row["id"], 1)
    assert early.value.status == 409
    clock.advance(seconds=59)
    with pytest.raises(DomainError) as cooldown:
        store.reserve_push_attempt("owner-a", row["id"], 1)
    assert cooldown.value.status == 409
    clock.advance(seconds=1)
    retry = store.reserve_push_attempt("owner-a", row["id"], 1)
    assert retry["last_result"] == "pending"
    assert store.finish_push_attempt("owner-a", row["id"], 1, "uncertain") is True

    with pytest.raises(DomainError) as stale_revision:
        store.reserve_push_attempt("owner-a", row["id"], 2)
    assert stale_revision.value.status == 409
    assert store.get_push_subscription("owner-a", row["id"])["revision"] == 1

    expiring = _upsert(
        store,
        clock,
        endpoint="endpoint-expiring",
        expires_at=(clock.now + timedelta(seconds=30)).isoformat(),
    )
    clock.advance(seconds=30)
    with pytest.raises(DomainError) as expired:
        store.reserve_push_attempt("owner-a", expiring["id"], 1)
    assert expired.value.status == 409


def test_finish_updates_only_pending_current_generation_and_tracks_acceptance(tmp_path):
    clock = Clock()
    store = _store(tmp_path, clock)
    row = _upsert(store, clock)

    assert store.finish_push_attempt("owner-a", row["id"], 1, "accepted") is False
    pending = store.reserve_push_attempt("owner-a", row["id"], 1)
    clock.advance(seconds=5)
    assert store.finish_push_attempt("owner-a", row["id"], 1, "uncertain") is True
    uncertain = store.get_push_subscription("owner-a", row["id"])
    assert uncertain["last_attempt_at"] == pending["last_attempt_at"]
    assert uncertain["last_accepted_at"] is None
    assert uncertain["last_result"] == "uncertain"
    assert store.finish_push_attempt("owner-a", row["id"], 1, "accepted") is False

    clock.advance(seconds=55)
    store.reserve_push_attempt("owner-a", row["id"], 1)
    clock.advance(seconds=2)
    assert store.finish_push_attempt("owner-a", row["id"], 1, "accepted") is True
    accepted = store.get_push_subscription("owner-a", row["id"])
    assert accepted["last_accepted_at"] == "2026-10-08T12:01:02.000000Z"
    assert accepted["last_result"] == "accepted"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("owner_id", "owner-secret\n"),
        ("owner_id", "o" * 256),
        ("endpoint_hash", "A" * 64),
        ("session_hash", "x" * 64),
        ("name", "\n"),
        ("name", "x" * 61),
        ("ciphertext", "plaintext-auth-secret"),
        ("ciphertext", "gAAAA" + "x" * 8192),
        ("expires_at", "2026-10-08T13:00:00"),
        ("expires_at", "2026-10-08T11:59:59Z"),
        ("expires_at", "2026-10-09T12:00:01Z"),
    ],
)
def test_invalid_push_data_is_rejected_without_database_changes(tmp_path, field, value):
    clock = Clock()
    store = _store(tmp_path, clock)
    values = _values(clock)
    values[field] = value
    before = store.list_push_subscriptions()

    with pytest.raises(DomainError) as error:
        store.upsert_push_subscription(**values)
    assert error.value.status == 400
    assert str(error.value) == "Los datos de la suscripción Push no son válidos."
    assert "secret" not in str(error.value)
    assert store.list_push_subscriptions() == before


def test_owner_quota_counts_expired_rows_and_rebind_does_not_use_another_slot(tmp_path):
    clock = Clock()
    store = _store(tmp_path, clock)
    rows = [
        _upsert(store, clock, endpoint=f"owner-endpoint-{index}")
        for index in range(8)
    ]
    clock.advance(hours=2)
    values = _values(clock, endpoint="owner-endpoint-new")
    with pytest.raises(DomainError) as quota:
        store.upsert_push_subscription(**values)
    assert quota.value.status == 409

    rebound = store.upsert_push_subscription(
        "owner-a",
        rows[0]["endpoint_hash"],
        _digest("new-session"),
        "Rebautizado",
        "gAAAA-rebound-ciphertext",
        (clock.now + timedelta(hours=1)).isoformat(),
    )
    assert rebound["revision"] == 2
    assert len(store.list_push_subscriptions("owner-a")) == 8


def test_global_quota_counts_expired_rows(tmp_path):
    clock = Clock()
    store = _store(tmp_path, clock)
    for index in range(256):
        owner = f"owner-{index // 8:02d}"
        _upsert(store, clock, owner=owner, endpoint=f"global-endpoint-{index}")
    assert len(store.list_push_subscriptions()) == 256

    clock.advance(hours=2)
    with pytest.raises(DomainError) as quota:
        store.upsert_push_subscription(
            **_values(clock, owner="owner-extra", endpoint="global-endpoint-extra")
        )
    assert quota.value.status == 409
    assert len(store.list_push_subscriptions()) == 256


def test_sqlite_backup_contains_no_plain_endpoint_or_webpush_keys(tmp_path):
    clock = Clock()
    store = _store(tmp_path, clock)
    endpoint = "synthetic-endpoint-plaintext"
    auth = "synthetic-auth-plaintext"
    p256dh = "synthetic-p256dh-plaintext"
    row = store.upsert_push_subscription(
        "owner-a",
        _digest(endpoint),
        _digest("synthetic-session"),
        "Móvil",
        "gAAAA-only-ciphertext-token",
        (clock.now + timedelta(hours=1)).isoformat(),
    )

    backup_path = tmp_path / "backup.sqlite3"
    with sqlite3.connect(store.path) as source, sqlite3.connect(backup_path) as target:
        source.backup(target)
    backup_bytes = backup_path.read_bytes()
    assert row["endpoint_hash"] in backup_bytes.decode("latin-1")
    assert row["ciphertext"] in backup_bytes.decode("latin-1")
    for plain_value in (endpoint, auth, p256dh):
        assert plain_value.encode("utf-8") not in backup_bytes

    with sqlite3.connect(backup_path) as connection:
        names = {
            row[1] for row in connection.execute("PRAGMA table_info(push_subscriptions)")
        }
    assert "endpoint" not in names
    assert "auth" not in names
    assert "p256dh" not in names
