"""Validation, ownership, filtering, and legacy support for journey annotations."""

from copy import deepcopy
from datetime import datetime, timezone
import sqlite3

import pytest

from pulse.domain import DomainError, Store
from pulse.manual_journeys import (
    ManualJourneyValidationError,
    canonicalize_manual_journey,
    create_manual_journey_schema,
    migrate_manual_journey_annotations,
    validate_manual_journey_archive,
)


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


def create(store, owner="owner-a", data=None):
    request = store.reserve_manual_journey_creation(owner)
    journey = store.add_manual_journey(
        owner, request["id"], values() if data is None else data
    )
    return request, journey


def test_old_request_defaults_annotations_and_new_values_are_canonical():
    old = canonicalize_manual_journey(values())
    assert old["notes"] is None
    assert old["tags"] == []

    annotated = canonicalize_manual_journey(values(
        notes="  Ruta con parada  ",
        tags=[" Viaje ", "VIAJE", "Straße", "STRASSE"],
    ))
    assert annotated["notes"] == "Ruta con parada"
    assert annotated["tags"] == ["viaje", "strasse"]


def test_consumed_schema20_reservation_accepts_exact_legacy_retry_only(tmp_path):
    from pulse.manual_charge_requests import attempt_fingerprint
    store = Store(tmp_path / 'legacy-replay.sqlite3', clock=lambda: NOW)
    request, journey = create(store)
    canonical = canonicalize_manual_journey(values())
    legacy = {key: value for key, value in canonical.items() if key not in {'notes', 'tags'}}
    with store._transaction() as connection:
        connection.execute('UPDATE manual_journey_requests SET attempt_fingerprint=? WHERE id=?',
            (attempt_fingerprint(legacy), request['id']))
        connection.execute('DROP TABLE maintenance_plan_request_tombstones')
        connection.execute('DROP TABLE maintenance_completions')
        connection.execute('DROP TABLE maintenance_plans')
        connection.execute('DROP TABLE navigation_favorites')
        connection.execute('ALTER TABLE manual_journeys DROP COLUMN notes')
        connection.execute('ALTER TABLE manual_journeys DROP COLUMN tags_json')
        connection.execute('PRAGMA user_version=20')
    upgraded = Store(store.path, clock=lambda: NOW)
    assert upgraded.add_manual_journey('owner-a', request['id'], values())['id'] == journey['id']
    with pytest.raises(DomainError):
        upgraded.add_manual_journey('owner-a', request['id'], values(notes='Otra intención'))
    with pytest.raises(DomainError):
        upgraded.add_manual_journey('owner-a', request['id'], values(tags=['trabajo']))


def test_old_editor_preserves_annotations_and_new_editor_can_clear_them(tmp_path):
    store = Store(tmp_path / 'old-editor.sqlite3', clock=lambda: NOW)
    _, journey = create(store, data=values(notes='Mi nota', tags=['viaje']))
    edited = store.update_manual_journey('owner-a', journey['id'], journey['revision'], values(to_label='Otro destino'))
    assert edited['notes'] == 'Mi nota' and edited['tags'] == ['viaje']
    cleared = store.update_manual_journey('owner-a', edited['id'], edited['revision'], values(to_label='Otro destino', notes=None, tags=[]))
    assert cleared['notes'] is None and cleared['tags'] == []


@pytest.mark.parametrize("patch", [
    {"notes": "x" * 2_001},
    {"notes": "nota\ncon salto"},
    {"notes": "\n"},
    {"notes": " " * 2_001},
    {"notes": "\ud800"},
    {"tags": ["x"] * 9},
    {"tags": ["x" * 33]},
    {"tags": ["  "]},
    {"tags": ["viaje\x00"]},
    {"notas": "campo desconocido"},
])
def test_invalid_annotations_and_unknown_fields_are_rejected(patch):
    with pytest.raises(ManualJourneyValidationError):
        canonicalize_manual_journey(values(**patch))


def test_schema20_annotations_migration_is_idempotent_and_preserves_rows():
    connection = sqlite3.connect(":memory:")
    create_manual_journey_schema(connection)
    connection.execute(
        """INSERT INTO manual_journeys
           (id, owner_id, revision, started_at, ended_at, from_label, to_label,
            distance_km, energy_kwh, stops_json, created_at, updated_at)
           VALUES (?, ?, 1, ?, ?, ?, ?, 30, 6, '[]', ?, ?)""",
        (
            "a" * 32, "owner-a", "2026-10-12T09:00:00.000000Z",
            "2026-10-12T10:00:00.000000Z", "Casa", "Trabajo",
            "2026-10-12T10:00:00.000000Z", "2026-10-12T10:00:00.000000Z",
        ),
    )

    migrate_manual_journey_annotations(connection)
    migrate_manual_journey_annotations(connection)
    row = connection.execute(
        "SELECT notes, tags_json, distance_km FROM manual_journeys"
    ).fetchone()
    assert row == (None, "[]", 30.0)
    columns = {column[1] for column in connection.execute("PRAGMA table_info(manual_journeys)")}
    assert {"notes", "tags_json"} <= columns
    connection.close()


def test_create_edit_delete_and_same_request_replay_include_annotations(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: NOW)
    request, original = create(store, data=values(notes="  Recado  ", tags=["PERSONAL"]))
    assert original["notes"] == "Recado"
    assert original["tags"] == ["personal"]
    assert store.add_manual_journey(
        "owner-a", request["id"], values(notes="Recado", tags=["personal"])
    ) == original

    with pytest.raises(DomainError) as changed_replay:
        store.add_manual_journey(
            "owner-a", request["id"], values(notes="Otro", tags=["personal"])
        )
    assert changed_replay.value.status == 409
    with pytest.raises(DomainError) as changed_tags_replay:
        store.add_manual_journey(
            "owner-a", request["id"], values(notes="Recado", tags=["personal", "otro"])
        )
    assert changed_tags_replay.value.status == 409

    updated_values = values(notes="Compra semanal", tags=["Casa", "Compra"])
    updated = store.update_manual_journey(
        "owner-a", original["id"], original["revision"], updated_values
    )
    assert updated["revision"] == 2
    assert updated["notes"] == "Compra semanal"
    assert updated["tags"] == ["casa", "compra"]
    assert store.update_manual_journey(
        "owner-a", original["id"], original["revision"], updated_values
    ) == updated

    with pytest.raises(DomainError) as wrong_owner:
        store.delete_manual_journey("owner-b", original["id"], updated["revision"])
    assert wrong_owner.value.status == 404
    assert store.list_manual_journeys("owner-b")["total"] == 0
    store.delete_manual_journey("owner-a", original["id"], updated["revision"])
    assert store.list_manual_journeys("owner-a")["total"] == 0
    with pytest.raises(DomainError) as deleted_replay:
        store.add_manual_journey("owner-a", request["id"], updated_values)
    assert deleted_replay.value.status == 409


def test_search_and_tag_filters_are_owner_scoped_unicode_casefolded_and_paged(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: NOW)
    _, matching = create(store, data=values(notes="Visita en Straße", tags=["RUTA", "Café"]))
    create(store, data=values(
        started_at="2026-10-11T09:00:00Z", ended_at="2026-10-11T10:00:00Z",
        notes="Otra salida", tags=["ruta"],
    ))
    create(store, owner="owner-b", data=values(notes="Straße", tags=["ruta"]))

    filtered = store.list_manual_journeys(
        "owner-a", limit=1, offset=0, tag="Ruta", search="STRASSE"
    )
    assert filtered == {"items": [matching], "total": 1}
    normalized_search = store.list_manual_journeys(
        "owner-a", search="CAFE\u0301"
    )
    assert normalized_search["items"] == [matching]
    assert store.list_manual_journeys(
        "owner-a", search="' OR 1=1 --"
    )["total"] == 0
    normalized_search = store.list_manual_journeys(
        "owner-a", search="CAFE\u0301"
    )
    assert normalized_search["items"] == [matching]
    assert store.list_manual_journeys(
        "owner-a", search="' OR 1=1 --"
    )["total"] == 0
    first_page = store.list_manual_journeys("owner-a", limit=1, offset=0)
    second_page = store.list_manual_journeys("owner-a", limit=1, offset=1)
    assert first_page["total"] == second_page["total"] == 2
    assert first_page["items"][0] != second_page["items"][0]

    with pytest.raises(DomainError):
        store.list_manual_journeys("owner-a", search="x" * 101)
    with pytest.raises(DomainError):
        store.list_manual_journeys("owner-a", tag="x" * 33)
    with pytest.raises(DomainError):
        store.list_manual_journeys("owner-a", offset=2_147_483_648)


def test_schema16_archive_defaults_annotations_and_schema17_is_exact(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: NOW)
    _, journey = create(store, data=values(notes="Viaje", tags=["ocio"]))
    archive = store.export_data(owner_id="owner-a")

    legacy_row = deepcopy(archive["manual_journeys"][0])
    legacy_row.pop("notes")
    legacy_row.pop("tags")
    legacy_rows = validate_manual_journey_archive(
        [legacy_row], now=NOW, schema_version=16
    )
    assert legacy_rows[0]["notes"] is None
    assert legacy_rows[0]["tags"] == []

    current_row = archive["manual_journeys"][0]
    current_rows = validate_manual_journey_archive(
        [current_row], now=NOW, schema_version=17
    )
    assert current_rows[0]["notes"] == journey["notes"]
    assert current_rows[0]["tags"] == journey["tags"]
    with pytest.raises(DomainError):
        validate_manual_journey_archive([legacy_row], now=NOW, schema_version=17)
    with pytest.raises(DomainError):
        validate_manual_journey_archive([current_row], now=NOW, schema_version=16)
    extra_field = deepcopy(current_row)
    extra_field["unexpected"] = True
    with pytest.raises(DomainError):
        validate_manual_journey_archive([extra_field], now=NOW, schema_version=17)

    legacy_archive = deepcopy(archive)
    legacy_archive["schema_version"] = 16
    legacy_archive["manual_journeys"] = [legacy_row]
    legacy_archive.pop("maintenance_plans")
    legacy_archive.pop("navigation_favorites")
    store.import_data(legacy_archive, owner_id="owner-a")
    restored = store.list_manual_journeys("owner-a")["items"][0]
    assert restored["id"] == journey["id"]
    assert restored["notes"] is None and restored["tags"] == []
