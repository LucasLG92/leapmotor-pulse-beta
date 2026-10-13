"""Storage, validation, revision, and archive tests for maintenance plans."""

from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
import sqlite3

import pytest

from pulse import maintenance_plans as plans_module
from pulse.domain import DomainError, EXPORT_VERSION, SCHEMA_VERSION, Store


NOW = datetime(2024, 2, 10, 12, 0, tzinfo=timezone.utc)


def values(**changes):
    result = {
        "title": "Revisión anual",
        "interval_months": 12,
        "interval_km": None,
        "last_date": "2023-10-13",
        "last_odometer_km": None,
        "notes": "",
    }
    result.update(changes)
    return result


def test_schema_20_migrates_to_21_and_reopens_without_data_loss(tmp_path):
    path = tmp_path / "pulse.sqlite3"
    store = Store(path, clock=lambda: NOW)
    charge = store.add_charge({"started_at": "2024-02-09T08:00:00Z", "kwh": 12})
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE maintenance_completions")
        connection.execute("DROP TABLE maintenance_plans")
        connection.execute("DROP TABLE maintenance_plan_request_tombstones")
        connection.execute("DROP TABLE navigation_favorites")
        connection.execute("PRAGMA user_version = 20")

    reopened = Store(path, clock=lambda: NOW)
    assert reopened.get_charge(charge["id"]) == charge
    assert Store(path, clock=lambda: NOW).list_maintenance_plans("owner-a") == {
        "items": [], "total": 0,
    }
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 21
        plan_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(maintenance_plans)")
        }
        names = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {"maintenance_plans", "maintenance_completions", "navigation_favorites"} <= names
    assert "creation_fingerprint" in plan_columns
    assert SCHEMA_VERSION == 21
    assert EXPORT_VERSION == 17


def test_create_is_owner_scoped_and_month_due_date_clamps_for_leap_february(tmp_path):
    due_now = datetime(2024, 3, 1, 12, 0, tzinfo=timezone.utc)
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: due_now)
    leap = store.create_maintenance_plan("owner-a", values(
        interval_months=1, last_date="2024-01-31",
    ))
    common = store.create_maintenance_plan("owner-b", values(
        interval_months=1, last_date="2023-01-31",
    ))

    assert leap["due_date"] == "2024-02-29"
    assert leap["coverage"] == {"date": "due", "km": "not_configured"}
    assert leap["status"] == "due"
    assert common["due_date"] == "2023-02-28"
    assert store.list_maintenance_plans("owner-a")["items"] == [leap]
    assert store.list_maintenance_plans("owner-b")["items"] == [common]


def test_create_replays_same_attempt_after_commit_without_duplicate_or_revision_change(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: NOW)
    request_id = "a" * 32

    created = store.create_maintenance_plan("owner-a", values(), request_id=request_id)
    # The second call represents a client retry after losing the first response.
    replayed = store.create_maintenance_plan("owner-a", values(), request_id=request_id)

    assert replayed["id"] == created["id"] == request_id
    assert replayed["revision"] == created["revision"] == 1
    assert replayed["title"] == created["title"]
    assert store.list_maintenance_plans("owner-a")["total"] == 1
    with store._read_transaction() as connection:
        persisted = connection.execute(
            "SELECT creation_fingerprint FROM maintenance_plans WHERE id = ?",
            (request_id,),
        ).fetchone()
    assert persisted["creation_fingerprint"] == plans_module.attempt_fingerprint(values())
    assert "creation_fingerprint" not in replayed


def test_create_request_id_conflicts_on_changed_body_foreign_owner_and_deleted_plan(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: NOW)
    request_id = "b" * 32
    created = store.create_maintenance_plan("owner-a", values(), request_id=request_id)

    with pytest.raises(DomainError) as changed:
        store.create_maintenance_plan(
            "owner-a", values(title="Otra tarea"), request_id=request_id,
        )
    assert changed.value.status == 409

    with pytest.raises(DomainError) as foreign:
        store.create_maintenance_plan("owner-b", values(), request_id=request_id)
    assert foreign.value.status == 404

    store.delete_maintenance_plan("owner-a", created["id"], created["revision"])
    with pytest.raises(DomainError) as deleted:
        store.create_maintenance_plan("owner-a", values(), request_id=request_id)
    assert deleted.value.status == 409
    assert store.list_maintenance_plans("owner-a")["total"] == 0


def test_create_replay_keeps_original_fingerprint_after_plan_edit(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: NOW)
    request_id = "c" * 32
    original = values()
    created = store.create_maintenance_plan("owner-a", original, request_id=request_id)
    edited_values = values(title="Batería", notes="Cambio posterior")
    edited = store.update_maintenance_plan(
        "owner-a", created["id"], created["revision"], edited_values,
    )
    completed = store.complete_maintenance_plan(
        "owner-a", created["id"], edited["revision"], "2024-02-10", None, None,
    )

    replayed = store.create_maintenance_plan("owner-a", original, request_id=request_id)
    assert replayed["id"] == created["id"]
    assert replayed["revision"] == completed["revision"] == 3
    assert replayed["title"] == "Batería"
    assert replayed["notes"] == "Cambio posterior"
    assert replayed["last_date"] == "2024-02-10"
    assert len(replayed["history"]) == 1
    with pytest.raises(DomainError) as changed:
        store.create_maintenance_plan(
            "owner-a", edited_values, request_id=request_id,
        )
    assert changed.value.status == 409
    with store._read_transaction() as connection:
        persisted = connection.execute(
            "SELECT creation_fingerprint FROM maintenance_plans WHERE id = ?",
            (request_id,),
        ).fetchone()
    assert persisted["creation_fingerprint"] == plans_module.attempt_fingerprint(original)


def test_restored_plan_has_no_creation_fingerprint_and_replay_fails_closed(tmp_path):
    source = Store(tmp_path / "source.sqlite3", clock=lambda: NOW)
    request_id = "d" * 32
    created = source.create_maintenance_plan("owner-a", values(), request_id=request_id)
    with source._read_transaction() as connection:
        archive = source.export_maintenance_plans(connection, "owner-a")
    assert "creation_fingerprint" not in archive[0]

    restored = Store(tmp_path / "restored.sqlite3", clock=lambda: NOW)
    validated = plans_module.validate_maintenance_plan_archive(archive, NOW)
    with restored._transaction() as connection:
        restored.replace_maintenance_plans(connection, "owner-a", validated)
    with restored._read_transaction() as connection:
        fingerprint = connection.execute(
            "SELECT creation_fingerprint FROM maintenance_plans WHERE id = ?",
            (created["id"],),
        ).fetchone()[0]
    assert fingerprint is None
    with pytest.raises(DomainError) as replay:
        restored.create_maintenance_plan("owner-a", values(), request_id=request_id)
    assert replay.value.status == 409
    assert restored.list_maintenance_plans("owner-a")["total"] == 1


@pytest.mark.parametrize("archive_version", [16, 17])
def test_legacy_restore_without_plan_invalidates_an_attempt_id(tmp_path, archive_version):
    store = Store(tmp_path / f"pulse-v{archive_version}.sqlite3", clock=lambda: NOW)
    request_id = "f" * 32
    store.create_maintenance_plan("owner-a", values(), request_id=request_id)
    archive = store.export_data(owner_id="owner-a")
    if archive_version == 17:
        archive["maintenance_plans"] = []
    else:
        archive["schema_version"] = 16
        archive.pop("maintenance_plans")
        archive.pop("navigation_favorites")

    store.import_data(archive, owner_id="owner-a")
    with pytest.raises(DomainError) as replay:
        store.create_maintenance_plan("owner-a", values(), request_id=request_id)
    assert replay.value.status == 409
    assert store.list_maintenance_plans("owner-a")["total"] == 0
    with store._read_transaction() as connection:
        tombstone = connection.execute(
            """SELECT owner_id FROM maintenance_plan_request_tombstones
               WHERE plan_id = ?""",
            (request_id,),
        ).fetchone()
    assert tombstone["owner_id"] == "owner-a"


def test_restore_tombstones_are_owner_scoped_and_block_id_transfer(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: NOW)
    request_id = "1" * 32
    store.create_maintenance_plan("owner-a", values(), request_id=request_id)
    owner_b = store.create_maintenance_plan(
        "owner-b", values(title="Propietario B"), request_id="2" * 32,
    )
    empty_owner_a = store.export_data(owner_id="owner-a")
    empty_owner_a["maintenance_plans"] = []
    store.import_data(empty_owner_a, owner_id="owner-a")

    assert store.list_maintenance_plans("owner-b")["items"][0]["id"] == owner_b["id"]
    with pytest.raises(DomainError) as foreign_replay:
        store.create_maintenance_plan("owner-b", values(), request_id=request_id)
    assert foreign_replay.value.status == 404

    with store._read_transaction() as connection:
        owner_b_archive = store.export_maintenance_plans(connection, "owner-b")
    collision = deepcopy(owner_b_archive)
    collision[0]["id"] = request_id
    with pytest.raises(DomainError) as foreign_restore:
        with store._transaction() as connection:
            store.replace_maintenance_plans(connection, "owner-b", collision)
    assert foreign_restore.value.status == 409
    assert store.list_maintenance_plans("owner-b")["items"][0]["id"] == owner_b["id"]
    assert store.create_maintenance_plan("owner-b", values(title="Nuevo de B"))["title"] == "Nuevo de B"


def test_restore_tombstone_limit_and_failed_import_are_atomic(tmp_path, monkeypatch):
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: NOW)
    first = store.create_maintenance_plan("owner-a", values(title="Primero"), request_id="3" * 32)
    second = store.create_maintenance_plan("owner-a", values(title="Segundo"), request_id="4" * 32)
    monkeypatch.setattr(plans_module, "MAX_INVALIDATED_MAINTENANCE_PLAN_IDS_PER_OWNER", 1)
    with pytest.raises(DomainError) as owner_limit:
        with store._transaction() as connection:
            store.replace_maintenance_plans(connection, "owner-a", [])
    assert owner_limit.value.status == 429

    monkeypatch.setattr(plans_module, "MAX_INVALIDATED_MAINTENANCE_PLAN_IDS_PER_OWNER", 10)
    monkeypatch.setattr(plans_module, "MAX_INVALIDATED_MAINTENANCE_PLAN_IDS_GLOBAL", 1)
    with pytest.raises(DomainError) as over_limit:
        with store._transaction() as connection:
            store.replace_maintenance_plans(connection, "owner-a", [])
    assert over_limit.value.status == 429
    assert store.list_maintenance_plans("owner-a")["total"] == 2

    monkeypatch.setattr(plans_module, "MAX_INVALIDATED_MAINTENANCE_PLAN_IDS_GLOBAL", 10)
    with store._read_transaction() as connection:
        malformed = deepcopy(store.export_maintenance_plans(connection, "owner-a"))
    malformed[0]["title"] = None
    with pytest.raises(DomainError) as failed_import:
        with store._transaction() as connection:
            store.replace_maintenance_plans(connection, "owner-a", malformed)
    assert failed_import.value.status == 500
    assert store.list_maintenance_plans("owner-a")["total"] == 2
    with store._read_transaction() as connection:
        tombstone_count = connection.execute(
            "SELECT COUNT(*) FROM maintenance_plan_request_tombstones"
        ).fetchone()[0]
    assert tombstone_count == 0
    assert store.create_maintenance_plan(
        "owner-a", values(title="Primero"), request_id=first["id"],
    )["id"] == first["id"]
    assert store.create_maintenance_plan(
        "owner-a", values(title="Segundo"), request_id=second["id"],
    )["id"] == second["id"]


def test_readonly_restore_preflight_does_not_write_or_invalidate_ids(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: NOW)
    plan = store.create_maintenance_plan("owner-a", values(), request_id="5" * 32)

    with store._read_transaction() as connection:
        connection.execute("PRAGMA query_only = ON")
        store.validate_maintenance_plan_replacement(connection, "owner-a", [])

    assert store.list_maintenance_plans("owner-a")["items"][0]["id"] == plan["id"]
    with store._read_transaction() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM maintenance_plan_request_tombstones"
        ).fetchone()[0]
    assert count == 0


def test_readonly_restore_preflight_rejects_quota_and_owner_without_writes(
    tmp_path, monkeypatch,
):
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: NOW)
    store.create_maintenance_plan("owner-a", values(title="Uno"))
    store.create_maintenance_plan("owner-a", values(title="Dos"))
    foreign = store.create_maintenance_plan("owner-b", values(title="Ajeno"))
    with store._read_transaction() as connection:
        owner_a_rows = store.export_maintenance_plans(connection, "owner-a")
        owner_b_rows = store.export_maintenance_plans(connection, "owner-b")

    monkeypatch.setattr(plans_module, "MAX_MAINTENANCE_PLANS_GLOBAL", 2)
    with store._read_transaction() as connection:
        connection.execute("PRAGMA query_only = ON")
        with pytest.raises(DomainError) as quota:
            store.validate_maintenance_plan_replacement(connection, "owner-a", owner_a_rows)
    assert quota.value.status == 429

    with store._read_transaction() as connection:
        connection.execute("PRAGMA query_only = ON")
        with pytest.raises(DomainError) as owner_conflict:
            store.validate_maintenance_plan_replacement(connection, "owner-a", owner_b_rows)
    assert owner_conflict.value.status == 409
    assert {item["id"] for item in store.list_maintenance_plans("owner-a")["items"]} == {
        row["id"] for row in owner_a_rows
    }
    assert store.list_maintenance_plans("owner-b")["items"][0]["id"] == foreign["id"]
    with store._read_transaction() as connection:
        tombstones = connection.execute(
            "SELECT COUNT(*) FROM maintenance_plan_request_tombstones"
        ).fetchone()[0]
    assert tombstones == 0


@pytest.mark.parametrize("request_id", ["", "A" * 32, "x" * 32, "a" * 31, "a" * 33])
def test_create_request_id_must_be_lowercase_uuid_hex(request_id, tmp_path):
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: NOW)
    with pytest.raises(DomainError):
        store.create_maintenance_plan("owner-a", values(), request_id=request_id)
    assert store.list_maintenance_plans("owner-a")["total"] == 0


def test_odometer_is_never_assumed_and_can_drive_due_status_only_when_supplied(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: NOW)
    plan = store.create_maintenance_plan("owner-a", values(
        interval_months=None, interval_km=15_000, last_date=None,
        last_odometer_km=50_000,
    ))
    assert plan["due_odometer_km"] == 65_000
    assert plan["status"] == "unknown"
    assert plan["due_reasons"] == ["km_unknown"]
    assert plan["coverage"] == {"date": "not_configured", "km": "unknown"}

    upcoming = store.list_maintenance_plans("owner-a", odometer_km=64_999)["items"][0]
    due = store.list_maintenance_plans("owner-a", odometer_km=65_000)["items"][0]
    assert upcoming["status"] == "upcoming"
    assert upcoming["due_reasons"] == []
    assert due["status"] == "due"
    assert due["due_reasons"] == ["km_due"]


@pytest.mark.parametrize("patch", [
    {"title": " "},
    {"title": "x" * 121},
    {"notes": "x" * 2001},
    {"interval_months": 0},
    {"interval_months": True},
    {"interval_months": 121},
    {"interval_km": 0},
    {"interval_km": 200_001},
    {"interval_km": 10_000, "last_odometer_km": None},
    {"interval_months": None, "interval_km": None},
    {"last_date": "2024-02-30"},
    {"last_date": "2024-02-11"},
    {"last_odometer_km": 2_000_001},
    {"request_id": "e" * 32},
    {"unexpected": True},
])
def test_plan_input_ranges_and_shape_are_strict(tmp_path, patch):
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: NOW)
    with pytest.raises(DomainError):
        store.create_maintenance_plan("owner-a", values(**patch))
    assert store.list_maintenance_plans("owner-a")["total"] == 0


def test_completion_is_revision_guarded_and_keeps_nullable_cost_history(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: NOW)
    plan = store.create_maintenance_plan("owner-a", values(
        interval_months=12, interval_km=10_000, last_date="2023-02-10",
        last_odometer_km=30_000,
    ))
    completed = store.complete_maintenance_plan(
        "owner-a", plan["id"], plan["revision"], "2024-02-10", 40_000, None,
    )

    assert completed["revision"] == 2
    assert completed["last_date"] == "2024-02-10"
    assert completed["last_odometer_km"] == 40_000
    assert len(completed["history"]) == 1
    assert completed["history"][0]["cost_eur"] is None
    assert completed["history"][0]["odometer_km"] == 40_000
    with pytest.raises(DomainError) as replay:
        store.complete_maintenance_plan(
            "owner-a", plan["id"], plan["revision"], "2024-02-10", 40_000, None,
        )
    assert replay.value.status == 409

    with pytest.raises(DomainError) as decreasing:
        store.complete_maintenance_plan(
            "owner-a", plan["id"], completed["revision"], "2024-02-10", 39_999, 10.5,
        )
    assert decreasing.value.status == 400
    unchanged = store.list_maintenance_plans("owner-a")["items"][0]
    assert unchanged["revision"] == 2
    assert len(unchanged["history"]) == 1


def test_null_completion_odometer_keeps_prior_manual_base(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: NOW)
    plan = store.create_maintenance_plan("owner-a", values(last_odometer_km=44_000))
    completed = store.complete_maintenance_plan(
        "owner-a", plan["id"], plan["revision"], "2024-02-10", None, None,
    )
    assert completed["last_odometer_km"] == 44_000
    assert completed["history"][0]["odometer_km"] is None


def test_explicit_rebase_and_soft_delete_preserve_history_and_archive_roundtrip(tmp_path):
    path = tmp_path / "pulse.sqlite3"
    store = Store(path, clock=lambda: NOW)
    plan = store.create_maintenance_plan("owner-a", values(
        interval_months=None, interval_km=10_000,
        last_date=None, last_odometer_km=50_000,
    ))
    completed = store.complete_maintenance_plan(
        "owner-a", plan["id"], 1, "2024-02-10", 60_000, 125.75,
    )
    edited = store.update_maintenance_plan(
        "owner-a", plan["id"], completed["revision"], values(
            title="Batería",
            interval_months=None,
            interval_km=8_000,
            last_date=None,
            last_odometer_km=58_000,
        ),
    )
    assert edited["last_odometer_km"] == 58_000
    assert len(edited["history"]) == 1
    store.delete_maintenance_plan("owner-a", plan["id"], edited["revision"])
    assert store.list_maintenance_plans("owner-a")["total"] == 0

    with store._read_transaction() as connection:
        archive = store.export_maintenance_plans(connection, "owner-a")
        assert "status" not in archive[0]
        assert "due_date" not in archive[0]
    validated = plans_module.validate_maintenance_plan_archive(archive, NOW)
    assert validated == archive

    restored = Store(tmp_path / "restored.sqlite3", clock=lambda: NOW)
    with restored._transaction() as connection:
        restored.replace_maintenance_plans(connection, "owner-a", validated)
    with restored._read_transaction() as connection:
        assert restored.export_maintenance_plans(connection, "owner-a") == archive


def test_archive_rejects_corruption_foreign_owner_and_duplicate_history_ids(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: NOW)
    plan = store.create_maintenance_plan("owner-a", values())
    with store._read_transaction() as connection:
        archive = store.export_maintenance_plans(connection, "owner-a")
    assert archive[0]["id"] == plan["id"]

    malformed = deepcopy(archive)
    malformed[0]["interval_km"] = 10_000
    malformed[0]["last_odometer_km"] = None
    with pytest.raises(DomainError):
        plans_module.validate_maintenance_plan_archive(malformed, NOW)

    foreign = deepcopy(archive)
    foreign[0]["owner_id"] = "owner-b"
    with pytest.raises(DomainError):
        with store._transaction() as connection:
            store.replace_maintenance_plans(connection, "owner-a", foreign)

    completed = store.complete_maintenance_plan(
        "owner-a", plan["id"], plan["revision"], "2024-02-10", None, None,
    )
    with store._read_transaction() as connection:
        duplicate = plans_module.validate_maintenance_plan_archive(
            store.export_maintenance_plans(connection, "owner-a"), NOW
        )
    duplicate[0]["history"].append(deepcopy(duplicate[0]["history"][0]))
    with pytest.raises(DomainError):
        plans_module.validate_maintenance_plan_archive(duplicate, NOW)
    assert completed["revision"] == 2


def test_restore_rejects_identifiers_owned_by_another_user(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: NOW)
    owner_a = store.create_maintenance_plan("owner-a", values())
    owner_b = store.create_maintenance_plan("owner-b", values(title="Otro"))
    with store._read_transaction() as connection:
        archive_b = store.export_maintenance_plans(connection, "owner-b")
    collision = deepcopy(archive_b)
    collision[0]["id"] = owner_a["id"]

    with pytest.raises(DomainError) as conflict:
        with store._transaction() as connection:
            store.replace_maintenance_plans(connection, "owner-b", collision)
    assert conflict.value.status == 409
    assert store.list_maintenance_plans("owner-b")["items"][0]["id"] == owner_b["id"]


def test_unscoped_export_is_empty_only_when_no_maintenance_data_exists(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: NOW)
    with store._read_transaction() as connection:
        assert store.export_maintenance_plans(connection, None) == []
    plan = store.create_maintenance_plan("owner-a", values())
    with store._read_transaction() as connection:
        with pytest.raises(DomainError) as blocked:
            store.export_maintenance_plans(connection, None)
    assert blocked.value.status == 403
    assert store.list_maintenance_plans("owner-a")["items"][0]["id"] == plan["id"]


def test_deleted_rows_count_toward_owner_and_global_limits(tmp_path, monkeypatch):
    monkeypatch.setattr(plans_module, "MAX_MAINTENANCE_PLANS_PER_OWNER", 1)
    monkeypatch.setattr(plans_module, "MAX_MAINTENANCE_PLANS_GLOBAL", 2)
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: NOW)
    first = store.create_maintenance_plan("owner-a", values())
    store.delete_maintenance_plan("owner-a", first["id"], first["revision"])
    with pytest.raises(DomainError) as owner_limit:
        store.create_maintenance_plan("owner-a", values(title="Otro"))
    assert owner_limit.value.status == 429

    store.create_maintenance_plan("owner-b", values())
    with pytest.raises(DomainError) as global_limit:
        store.create_maintenance_plan("owner-c", values())
    assert global_limit.value.status == 429


def test_list_pagination_and_optional_odometer_validate_bounds(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: NOW)
    for index in range(3):
        store.create_maintenance_plan("owner-a", values(title=f"Plan {index}"))
    page = store.list_maintenance_plans("owner-a", limit=2, offset=1)
    assert page["total"] == 3
    assert len(page["items"]) == 2
    with pytest.raises(DomainError):
        store.list_maintenance_plans("owner-a", limit=101)
    with pytest.raises(DomainError):
        store.list_maintenance_plans("owner-a", odometer_km=True)


def test_list_and_export_batch_history_queries_for_many_plans(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: NOW)
    plans = [
        store.create_maintenance_plan("owner-a", values(title=f"Plan {index}"))
        for index in range(3)
    ]
    for plan in plans:
        store.complete_maintenance_plan(
            "owner-a", plan["id"], plan["revision"], "2024-02-10", None, None,
        )

    statements = []
    original_read_transaction = store._read_transaction

    @contextmanager
    def traced_read_transaction():
        with original_read_transaction() as connection:
            connection.set_trace_callback(statements.append)
            yield connection

    store._read_transaction = traced_read_transaction
    listed = store.list_maintenance_plans("owner-a", limit=10)
    list_history_queries = [
        query for query in statements if "FROM maintenance_completions" in query
    ]
    assert len(list_history_queries) == 1
    assert sum(len(item["history"]) for item in listed["items"]) == 3

    with store._read_transaction() as connection:
        statements = []
        connection.set_trace_callback(statements.append)
        exported = store.export_maintenance_plans(connection, "owner-a")
        export_history_queries = [
            query for query in statements if "FROM maintenance_completions" in query
        ]
        assert len(export_history_queries) == 2  # One count plus one batched fetch.
        assert sum(len(item["history"]) for item in exported) == 3
