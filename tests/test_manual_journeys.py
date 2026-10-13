"""Domain, migration, reservation, restore, and report tests for manual journeys."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest

from pulse import domain as domain_module
from pulse.domain import DomainError, EXPORT_VERSION, SCHEMA_VERSION, Store
from pulse import manual_journeys as manual_journeys_module


NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)


def values(**changes):
    result = {
        "started_at": "2026-10-12T09:00:00Z",
        "ended_at": "2026-10-12T10:00:00Z",
        "from_label": "Casa",
        "to_label": "Trabajo",
        "distance_km": 30,
        "energy_kwh": 6,
        "stops": [],
    }
    result.update(changes)
    return result


def create(store, owner="owner-a", data=None, request=None):
    data = values() if data is None else data
    request = store.reserve_manual_journey_creation(owner) if request is None else request
    return request, store.add_manual_journey(owner, request["id"], data)


def test_schema_19_migrates_to_20_without_changing_existing_charge(tmp_path):
    path = tmp_path / "pulse.sqlite3"
    store = Store(path, clock=lambda: NOW)
    charge = store.add_charge({"started_at": "2026-10-12T08:00:00Z", "kwh": 12})

    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE manual_journey_requests")
        connection.execute("DROP TABLE manual_journeys")
        connection.execute("PRAGMA user_version = 19")

    reopened = Store(path, clock=lambda: NOW)
    assert reopened.get_charge(charge["id"]) == charge
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 20
        names = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {"manual_journeys", "manual_journey_requests"} <= names
    assert SCHEMA_VERSION == 20


def test_creation_normalizes_zones_calculates_stops_and_survives_restart(tmp_path):
    path = tmp_path / "pulse.sqlite3"
    store = Store(path, clock=lambda: NOW)
    request, journey = create(store, data=values(
        started_at="2026-10-12T11:00:00+02:00",
        ended_at="2026-10-12T12:10:00+02:00",
        stops=[
            {"started_at": "2026-10-12T11:20:00+02:00", "ended_at": "2026-10-12T11:25:00+02:00"},
            {"started_at": "2026-10-12T11:40:00+02:00", "ended_at": "2026-10-12T11:50:00+02:00"},
        ],
    ))

    assert journey["started_at"] == "2026-10-12T09:00:00.000000Z"
    assert journey["ended_at"] == "2026-10-12T10:10:00.000000Z"
    assert journey["duration_min"] == 70
    assert journey["paused_min"] == 15
    assert journey["active_min"] == 55
    assert journey["consumption_kwh_100km"] == 20
    reopened = Store(path, clock=lambda: NOW)
    assert reopened.list_manual_journeys("owner-a")["items"] == [journey]
    assert reopened.add_manual_journey("owner-a", request["id"], values(
        started_at="2026-10-12T11:00:00+02:00",
        ended_at="2026-10-12T12:10:00+02:00",
        stops=[
            {"started_at": "2026-10-12T11:20:00+02:00", "ended_at": "2026-10-12T11:25:00+02:00"},
            {"started_at": "2026-10-12T11:40:00+02:00", "ended_at": "2026-10-12T11:50:00+02:00"},
        ],
    )) == journey


def test_null_values_zero_distance_and_optional_labels_are_preserved(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: NOW)
    _, journey = create(store, data=values(
        from_label="   ", to_label=None, distance_km=0, energy_kwh=2,
    ))
    assert journey["from_label"] is None
    assert journey["to_label"] is None
    assert journey["distance_km"] == 0
    assert journey["energy_kwh"] == 2
    assert journey["consumption_kwh_100km"] is None

    _, unknown = create(store, data=values(
        started_at="2026-10-11T09:00:00Z", ended_at="2026-10-11T10:00:00Z",
        distance_km=None, energy_kwh=None,
    ))
    assert unknown["distance_km"] is None
    assert unknown["energy_kwh"] is None
    assert unknown["consumption_kwh_100km"] is None


@pytest.mark.parametrize("patch", [
    {"ended_at": "2026-10-12T09:00:00Z"},
    {"started_at": "2026-10-12T09:00:00"},
    {"started_at": "0001-01-01T00:00:00+23:59", "ended_at": "0001-01-01T01:00:00+23:59"},
    {"ended_at": "2026-10-13T12:06:00Z"},
    {"distance_km": True},
    {"distance_km": float("nan")},
    {"distance_km": 50_000.1},
    {"distance_km": 5e-324, "energy_kwh": 5_000},
    {"energy_kwh": -0.01},
    {"from_label": "x" * 121},
    {"stops": [{"started_at": "2026-10-12T09:30:00Z", "ended_at": "2026-10-12T09:40:00Z"},
                {"started_at": "2026-10-12T09:35:00Z", "ended_at": "2026-10-12T09:45:00Z"}]},
    {"stops": [{"started_at": "2026-10-12T10:01:00Z", "ended_at": "2026-10-12T10:02:00Z"}]},
    {"stops": [{"started_at": "2026-10-12T09:30:00Z", "ended_at": "2026-10-12T09:40:00Z", "label": "private"}]},
    {"stops": [{"started_at": "2026-10-12T09:40:00Z", "ended_at": "2026-10-12T09:45:00Z"},
                {"started_at": "2026-10-12T09:20:00Z", "ended_at": "2026-10-12T09:25:00Z"}]},
    {"stops": [{"started_at": "2026-10-12T09:00:00Z", "ended_at": "2026-10-12T09:01:00Z"}] * 33},
])
def test_invalid_closed_journey_inputs_are_rejected_without_consuming_reservation(tmp_path, patch):
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: NOW)
    request = store.reserve_manual_journey_creation("owner-a")
    with pytest.raises(DomainError):
        store.add_manual_journey("owner-a", request["id"], values(**patch))
    valid = store.add_manual_journey("owner-a", request["id"], values())
    assert valid["revision"] == 1


def test_owner_scoped_list_and_request_replay_are_private(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: NOW)
    request_a, journey_a = create(store, "owner-a")
    request_b, journey_b = create(store, "owner-b")

    assert store.list_manual_journeys("owner-a")["items"] == [journey_a]
    assert store.list_manual_journeys("owner-b")["items"] == [journey_b]
    with pytest.raises(DomainError) as wrong_owner:
        store.add_manual_journey("owner-b", request_a["id"], values())
    assert wrong_owner.value.status == 409


def test_same_reservation_concurrent_posts_link_exactly_one_journey(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: NOW)
    request = store.reserve_manual_journey_creation("owner-a")
    with ThreadPoolExecutor(max_workers=8) as pool:
        created = list(pool.map(
            lambda _: store.add_manual_journey("owner-a", request["id"], values()), range(16)
        ))
    assert len({item["id"] for item in created}) == 1
    assert store.list_manual_journeys("owner-a")["total"] == 1
    with sqlite3.connect(store.path) as connection:
        row = connection.execute(
            "SELECT attempt_fingerprint, journey_id FROM manual_journey_requests WHERE id = ?",
            (request["id"],),
        ).fetchone()
        assert row[0] is not None and row[1] == created[0]["id"]


def test_failed_insert_and_link_are_atomic_and_reservation_can_retry(tmp_path):
    path = tmp_path / "pulse.sqlite3"
    store = Store(path, clock=lambda: NOW)
    request = store.reserve_manual_journey_creation("owner-a")
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TRIGGER reject_journey_link BEFORE UPDATE ON manual_journey_requests
               BEGIN SELECT RAISE(ABORT, 'private trigger detail'); END"""
        )
    with pytest.raises(DomainError) as failed:
        store.add_manual_journey("owner-a", request["id"], values())
    assert "private trigger detail" not in str(failed.value)
    assert store.list_manual_journeys("owner-a")["total"] == 0
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT attempt_fingerprint FROM manual_journey_requests WHERE id = ?",
            (request["id"],),
        ).fetchone()[0] is None
        connection.execute("DROP TRIGGER reject_journey_link")
    assert store.add_manual_journey("owner-a", request["id"], values())["revision"] == 1


def test_reservation_ttl_owner_and_global_capacity_and_pruning(tmp_path, monkeypatch):
    now = [NOW]
    monkeypatch.setattr(manual_journeys_module, "MAX_REQUESTS_PER_OWNER", 2)
    monkeypatch.setattr(manual_journeys_module, "MAX_REQUESTS_GLOBAL", 3)
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: now[0])
    first = store.reserve_manual_journey_creation("owner-a")
    second = store.reserve_manual_journey_creation("owner-a")
    expiry = datetime.fromisoformat(first["expires_at"].replace("Z", "+00:00"))
    assert expiry - NOW == timedelta(days=7)
    with pytest.raises(DomainError) as owner_limit:
        store.reserve_manual_journey_creation("owner-a")
    assert owner_limit.value.status == 429
    store.reserve_manual_journey_creation("owner-b")
    with pytest.raises(DomainError) as global_limit:
        store.reserve_manual_journey_creation("owner-c")
    assert global_limit.value.status == 429

    now[0] += timedelta(days=7)
    with pytest.raises(DomainError) as expired:
        store.add_manual_journey("owner-a", first["id"], values())
    assert expired.value.status == 409
    renewed = store.reserve_manual_journey_creation("owner-a")
    assert renewed["id"] not in {first["id"], second["id"]}
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM manual_journey_requests"
        ).fetchone()[0] == 1


def test_edit_idempotency_revision_conflict_and_deleted_create_tombstone(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: NOW)
    request, original = create(store)
    updated_data = values(to_label="Otra oficina", distance_km=31)
    updated = store.update_manual_journey(
        "owner-a", original["id"], original["revision"], updated_data
    )
    assert updated["revision"] == 2
    repeated = store.update_manual_journey(
        "owner-a", original["id"], original["revision"], updated_data
    )
    assert repeated == updated
    with pytest.raises(DomainError) as stale:
        store.update_manual_journey(
            "owner-a", original["id"], original["revision"], values(to_label="stale")
        )
    assert stale.value.status == 409
    with pytest.raises(DomainError) as private:
        store.update_manual_journey("owner-b", original["id"], 2, updated_data)
    assert private.value.status == 404

    store.delete_manual_journey("owner-a", original["id"], 2)
    with pytest.raises(DomainError) as deleted_replay:
        store.add_manual_journey("owner-a", request["id"], values())
    assert deleted_replay.value.status == 409
    assert store.list_manual_journeys("owner-a")["total"] == 0


def test_max_revision_restore_keeps_edit_replay_delete_and_export_consistent(tmp_path):
    now = [NOW]
    path = tmp_path / "pulse.sqlite3"
    store = Store(path, clock=lambda: now[0])
    create(store)
    archive = store.export_data(owner_id="owner-a")
    archive["manual_journeys"][0]["revision"] = manual_journeys_module.MAX_MANUAL_JOURNEY_REVISION
    store.import_data(archive, owner_id="owner-a")

    restored = store.list_manual_journeys("owner-a")["items"][0]
    max_revision = manual_journeys_module.MAX_MANUAL_JOURNEY_REVISION
    assert restored["revision"] == max_revision

    now[0] += timedelta(seconds=1)
    assert store.update_manual_journey(
        "owner-a", restored["id"], max_revision, values()
    ) == restored
    reservation = store.reserve_manual_journey_creation("owner-a")
    with sqlite3.connect(path) as connection:
        before_row = connection.execute(
            "SELECT * FROM manual_journeys WHERE id = ?", (restored["id"],)
        ).fetchone()
        before_request = connection.execute(
            "SELECT * FROM manual_journey_requests WHERE id = ?", (reservation["id"],)
        ).fetchone()
        before_state_updated_at = connection.execute(
            "SELECT updated_at FROM app_meta WHERE id = 1"
        ).fetchone()[0]

    now[0] += timedelta(seconds=1)
    with pytest.raises(DomainError) as overflow:
        store.update_manual_journey(
            "owner-a", restored["id"], max_revision,
            values(to_label="Destino editado"),
        )
    assert overflow.value.status == 409
    assert str(overflow.value) == "El trayecto alcanzó la revisión máxima y no puede editarse."
    assert store.list_manual_journeys("owner-a")["items"] == [restored]
    exported = store.export_data()
    assert exported["manual_journeys"][0]["revision"] == max_revision
    assert store.validate_import(exported) == {"ok": True}

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT * FROM manual_journeys WHERE id = ?", (restored["id"],)
        ).fetchone() == before_row
        assert connection.execute(
            "SELECT * FROM manual_journey_requests WHERE id = ?", (reservation["id"],)
        ).fetchone() == before_request
        assert connection.execute(
            "SELECT updated_at FROM app_meta WHERE id = 1"
        ).fetchone()[0] == before_state_updated_at

    store.delete_manual_journey("owner-a", restored["id"], max_revision)
    assert store.list_manual_journeys("owner-a")["total"] == 0


def test_report_has_known_and_paired_denominators_and_madrid_month_keys(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: NOW)
    create(store, data=values(
        started_at="2026-10-01T00:30:00+02:00",
        ended_at="2026-10-01T01:30:00+02:00",
        distance_km=50, energy_kwh=10,
    ))
    create(store, data=values(
        started_at="2026-09-30T10:00:00Z", ended_at="2026-09-30T11:00:00Z",
        distance_km=30, energy_kwh=None,
    ))
    create(store, data=values(
        started_at="2026-10-02T10:00:00Z", ended_at="2026-10-02T11:00:00Z",
        distance_km=None, energy_kwh=5,
    ))

    report = store.report_manual_journeys("owner-a", days=30)
    assert report["source"] == "manual" and report["coverage"] == "unknown"
    assert report["period"]["days"] == 30 and report["period"]["timezone"] == "UTC"
    assert report["totals"] == {
        "count": 3,
        "distance_km": 80,
        "energy_kwh": 15,
        "known_distance_count": 2,
        "known_energy_count": 2,
        "paired_count": 1,
        "paired_distance_km": 50,
        "paired_energy_kwh": 10,
        "consumption_kwh_100km": 20,
    }
    assert [item["month_key"] for item in report["months"]] == ["2026-09", "2026-10"]
    october = report["months"][1]
    assert october["count"] == 2 and october["consumption_kwh_100km"] == 20
    assert store.report_manual_journeys("owner-a", days=90)["period"]["days"] == 90
    with pytest.raises(DomainError):
        store.report_manual_journeys("owner-a", days=31)


def test_report_summarizes_many_rows_with_maximum_stop_lists(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: NOW)
    stops = [
        {
            "started_at": f"2026-10-12T09:{index + 1:02d}:00Z",
            "ended_at": f"2026-10-12T09:{index + 1:02d}:30Z",
        }
        for index in range(32)
    ]
    for _ in range(64):
        create(store, data=values(stops=stops))

    report = store.report_manual_journeys("owner-a", days=30)
    expected = {
        "count": 64,
        "distance_km": 1_920,
        "energy_kwh": 384,
        "known_distance_count": 64,
        "known_energy_count": 64,
        "paired_count": 64,
        "paired_distance_km": 1_920,
        "paired_energy_kwh": 384,
        "consumption_kwh_100km": 20,
    }
    assert report["totals"] == expected
    october = next(item for item in report["months"] if item["month_key"] == "2026-10")
    assert october == {"month_key": "2026-10", **expected}


def test_report_preserves_large_and_small_finite_sums_without_losing_pairs(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: NOW)
    create(store, data=values(distance_km=50_000, energy_kwh=5_000))
    create(store, data=values(
        started_at="2026-10-11T09:00:00Z",
        ended_at="2026-10-11T10:00:00Z",
        distance_km=0.0000006,
        energy_kwh=0.0000006,
    ))

    report = store.report_manual_journeys("owner-a", days=30)
    assert report["totals"] == {
        "count": 2,
        "distance_km": 50_000.0000006,
        "energy_kwh": 5_000.0000006,
        "known_distance_count": 2,
        "known_energy_count": 2,
        "paired_count": 2,
        "paired_distance_km": 50_000.0000006,
        "paired_energy_kwh": 5_000.0000006,
        "consumption_kwh_100km": 10,
    }


def test_report_preserves_tiny_positive_sums_alongside_zero_and_null(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: NOW)
    create(store, data=values(distance_km=1e-8, energy_kwh=1e-8))
    create(store, data=values(distance_km=0, energy_kwh=0))
    create(store, data=values(distance_km=None, energy_kwh=None))

    totals = store.report_manual_journeys("owner-a", days=30)["totals"]
    assert totals == {
        "count": 3,
        "distance_km": 1e-8,
        "energy_kwh": 1e-8,
        "known_distance_count": 2,
        "known_energy_count": 2,
        "paired_count": 1,
        "paired_distance_km": 1e-8,
        "paired_energy_kwh": 1e-8,
        "consumption_kwh_100km": 100,
    }


def test_json16_export_restore_is_owner_scoped_and_invalidates_only_that_owner(tmp_path):
    path = tmp_path / "pulse.sqlite3"
    store = Store(path, clock=lambda: NOW)
    request_a, journey_a = create(store, "owner-a")
    request_b, journey_b = create(store, "owner-b", data=values(
        started_at="2026-10-11T09:00:00Z", ended_at="2026-10-11T10:00:00Z"
    ))
    archive = store.export_data(owner_id="owner-a")
    assert archive["schema_version"] == EXPORT_VERSION == 16
    assert [item["id"] for item in archive["manual_journeys"]] == [journey_a["id"]]
    store.import_data(archive, owner_id="owner-a")

    assert store.list_manual_journeys("owner-a")["items"] == [journey_a]
    assert store.list_manual_journeys("owner-b")["items"] == [journey_b]
    with pytest.raises(DomainError) as invalidated:
        store.add_manual_journey("owner-a", request_a["id"], values())
    assert invalidated.value.status == 409
    assert store.add_manual_journey(
        "owner-b", request_b["id"], values(
            started_at="2026-10-11T09:00:00Z", ended_at="2026-10-11T10:00:00Z"
        ),
    ) == journey_b
    assert Store(path, clock=lambda: NOW).list_manual_journeys("owner-a")["items"] == [journey_a]


def test_restore_failure_rolls_back_manual_rows_and_reservations(tmp_path):
    path = tmp_path / "pulse.sqlite3"
    store = Store(path, clock=lambda: NOW)
    request, journey = create(store)
    archive = store.export_data(owner_id="owner-a")
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TRIGGER reject_manual_journey_restore BEFORE INSERT ON manual_journeys
               BEGIN SELECT RAISE(ABORT, 'private trigger detail'); END"""
        )
    with pytest.raises(DomainError) as failed:
        store.import_data(archive, owner_id="owner-a")
    assert "private trigger detail" not in str(failed.value)
    assert store.list_manual_journeys("owner-a")["items"] == [journey]
    assert store.add_manual_journey("owner-a", request["id"], values()) == journey


def test_owner_restore_rejects_cross_owner_id_before_deleting_any_data(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: NOW)
    _, kept = create(store, "owner-a")
    _, foreign = create(store, "owner-b")
    archive = store.export_data(owner_id="owner-a")
    foreign_archive = store.export_data(owner_id="owner-b")["manual_journeys"][0]
    foreign_archive["owner_id"] = "owner-a"
    archive["manual_journeys"] = [foreign_archive]

    with pytest.raises(DomainError) as conflict:
        store.import_data(archive, owner_id="owner-a")
    assert conflict.value.status == 409
    assert store.list_manual_journeys("owner-a")["items"] == [kept]
    assert store.list_manual_journeys("owner-b")["items"] == [foreign]


def test_json15_restore_clears_only_target_owner_manual_journeys(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: NOW)
    request_b, journey_b = create(store, "owner-b")
    create(store, "owner-a")
    legacy = store.export_data(owner_id="owner-a")
    legacy["schema_version"] = 15
    legacy.pop("manual_journeys")

    store.import_data(legacy, owner_id="owner-a")
    assert store.list_manual_journeys("owner-a")["total"] == 0
    assert store.list_manual_journeys("owner-b")["items"] == [journey_b]
    assert store.add_manual_journey("owner-b", request_b["id"], values()) == journey_b


def test_json16_strict_archive_rejects_invalid_fields_or_derived_metrics_without_mutation(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: NOW)
    _, journey = create(store)
    archive = store.export_data(owner_id="owner-a")
    invalid_documents = []

    missing = deepcopy(archive)
    del missing["manual_journeys"][0]["stops"]
    invalid_documents.append(missing)
    extra = deepcopy(archive)
    extra["manual_journeys"][0]["private"] = "not allowed"
    invalid_documents.append(extra)
    inconsistent = deepcopy(archive)
    inconsistent["manual_journeys"][0]["duration_min"] += 1
    invalid_documents.append(inconsistent)
    duplicate = deepcopy(archive)
    duplicate["manual_journeys"].append(deepcopy(duplicate["manual_journeys"][0]))
    invalid_documents.append(duplicate)

    for invalid in invalid_documents:
        with pytest.raises(DomainError):
            store.import_data(invalid, owner_id="owner-a")
        assert store.list_manual_journeys("owner-a")["items"] == [journey]


def test_invalid_restore_preserves_reservation_and_linked_journey(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: NOW)
    request, journey = create(store)
    invalid = store.export_data(owner_id="owner-a")
    invalid["schema_version"] = 17
    with pytest.raises(DomainError):
        store.import_data(invalid, owner_id="owner-a")
    assert store.add_manual_journey("owner-a", request["id"], values()) == journey


def test_restore_rejects_huge_derived_metric_without_mutating_rows_or_ledger(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: NOW)
    request, journey = create(store)
    pending = store.reserve_manual_journey_creation("owner-a")
    invalid = store.export_data(owner_id="owner-a")
    invalid["manual_journeys"][0]["duration_min"] = 10 ** 400

    with pytest.raises(DomainError) as rejected:
        store.import_data(invalid, owner_id="owner-a")
    assert rejected.value.status == 400
    assert store.list_manual_journeys("owner-a")["items"] == [journey]
    assert store.add_manual_journey("owner-a", request["id"], values()) == journey
    assert store.add_manual_journey(
        "owner-a", pending["id"], values(
            started_at="2026-10-11T09:00:00Z",
            ended_at="2026-10-11T10:00:00Z",
        ),
    )["revision"] == 1


def test_manual_journey_export_enforces_budget_incrementally_and_restores(tmp_path, monkeypatch):
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: NOW)
    stops = [
        {
            "started_at": f"2026-10-12T09:{index + 1:02d}:00Z",
            "ended_at": f"2026-10-12T09:{index + 1:02d}:30Z",
        }
        for index in range(32)
    ]
    for _ in range(24):
        create(store, data=values(stops=stops))
    before = {
        row["id"]: row
        for row in store.list_manual_journeys("owner-a", limit=100)["items"]
    }
    sample = {**next(iter(before.values())), "owner_id": "owner-a"}
    item_bytes = len(json.dumps(
        sample, allow_nan=False, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8"))
    monkeypatch.setattr(domain_module, "MAX_V16_IMPORT_BYTES", item_bytes + 3)
    seen = []
    original_item = manual_journeys_module._journey_item

    def track_item(row):
        seen.append(row["id"])
        return original_item(row)

    monkeypatch.setattr(manual_journeys_module, "_journey_item", track_item)
    with pytest.raises(DomainError) as oversized:
        store.export_data(owner_id="owner-a")
    assert oversized.value.status == 413
    assert len(seen) == 2
    assert {
        row["id"]: row
        for row in store.list_manual_journeys("owner-a", limit=100)["items"]
    } == before

    monkeypatch.setattr(manual_journeys_module, "_journey_item", original_item)
    monkeypatch.setattr(domain_module, "MAX_V16_IMPORT_BYTES", 5_000_000)
    archive = store.export_data(owner_id="owner-a")
    assert {row["id"] for row in archive["manual_journeys"]} == set(before)
    store.import_data(archive, owner_id="owner-a")
    after = {
        row["id"]: row
        for row in store.list_manual_journeys("owner-a", limit=100)["items"]
    }
    assert after == before
