"""Consentimiento duradero y reserva atómica; sin credenciales ni red externa."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest

from pulse.domain import DomainError, EXPORT_VERSION, SCHEMA_VERSION, Store
from schema_helpers import downgrade_telemetry_to_v15


NOW = datetime(2026, 10, 9, 4, 0, tzinfo=timezone.utc)
SCOPE = "a" * 64
OTHER = "b" * 64


def snapshot(store, *, soc=52.5):
    return store.record_snapshot({
        "source": "MATE-API B10ReadClient", "observed_at": NOW.isoformat(),
        "provider_received_at": NOW.isoformat(),
        "vehicle": {"battery_pct": soc, "range_km": None, "odometer_km": None,
                    "locked": None, "climate_on": None, "charging": None},
        "field_sources": {"battery_pct": "soc_percent", "range_km": None, "odometer_km": None,
                          "locked": None, "climate_on": None, "charging": None},
    })


def test_db10_migration_preserves_business_and_defaults_to_no_consent(tmp_path):
    path = tmp_path / "pulse.sqlite3"
    store = Store(path, clock=lambda: NOW)
    store.add_charge({"started_at": NOW.isoformat(), "kwh": 7})
    snapshot(store)
    before = store.export_data()
    with sqlite3.connect(path) as db:
        downgrade_telemetry_to_v15(db)
        db.execute("DROP TABLE abrp_preference")
        db.execute("DROP TABLE IF EXISTS charger_pricing_history")
        db.execute("DROP TABLE charger_energy_intervals")
        db.execute("DROP TABLE charger_energy_epochs")
        db.execute("ALTER TABLE settings DROP COLUMN vehicle_color")
        db.execute("PRAGMA user_version = 10")
    migrated = Store(path, clock=lambda: NOW)
    assert SCHEMA_VERSION == 17
    assert migrated.export_data() == before
    assert before["schema_version"] == EXPORT_VERSION == 15
    context = migrated.abrp_context(SCOPE)
    assert context["snapshot"] == store.telemetry_latest()
    assert not context["consent"]
    assert context["generation"] == 0
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 17


def test_consent_survives_restart_but_never_another_binding(tmp_path):
    path = tmp_path / "pulse.sqlite3"
    store = Store(path, clock=lambda: NOW)
    first = store.set_abrp_consent(SCOPE, True)
    restarted = Store(path, clock=lambda: NOW)
    assert restarted.abrp_context(SCOPE)["consent"]
    assert not restarted.abrp_context(OTHER)["consent"]
    second = restarted.set_abrp_consent(OTHER, True)
    assert second["generation"] > first["generation"]
    assert not restarted.abrp_context(SCOPE)["consent"]
    assert restarted.abrp_context(OTHER)["last_attempt_key"] is None
    third = restarted.set_abrp_consent(OTHER, False)
    assert third["generation"] > second["generation"]
    assert not Store(path).abrp_context(OTHER)["consent"]


def test_atomic_reserve_checks_latest_consent_generation_and_persistent_cooldown(tmp_path):
    path = tmp_path / "pulse.sqlite3"
    store = Store(path, clock=lambda: NOW)
    first = snapshot(store)
    generation = store.set_abrp_consent(SCOPE, True)["generation"]
    key = "c" * 64
    assert store.reserve_abrp(SCOPE, generation, key, first["id"], attempt_at=NOW)
    reopened = Store(path)
    assert not reopened.reserve_abrp(SCOPE, generation, key, first["id"], attempt_at=NOW + timedelta(hours=1))
    second = snapshot(reopened, soc=54)
    assert not reopened.reserve_abrp(SCOPE, generation, "d" * 64, first["id"], attempt_at=NOW + timedelta(hours=1))
    assert not reopened.reserve_abrp(SCOPE, generation, "d" * 64, second["id"], attempt_at=NOW - timedelta(seconds=1))
    assert not reopened.reserve_abrp(SCOPE, generation, "d" * 64, second["id"], attempt_at=NOW + timedelta(seconds=299))
    assert reopened.reserve_abrp(SCOPE, generation, "d" * 64, second["id"], attempt_at=NOW + timedelta(seconds=300))
    reopened.set_abrp_consent(SCOPE, False)
    assert not reopened.reserve_abrp(SCOPE, generation, "e" * 64, second["id"], attempt_at=NOW + timedelta(hours=1))


def test_demo_never_grants_or_reserves_or_parses_private_telemetry(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3")
    reading = snapshot(store)
    generation = store.set_abrp_consent(SCOPE, True)["generation"]
    store.set_demo(True)
    with pytest.raises(DomainError) as error:
        store.set_abrp_consent(SCOPE, True)
    assert error.value.status == 409
    assert not store.reserve_abrp(SCOPE, generation, "c" * 64, reading["id"], attempt_at=NOW)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE telemetry_snapshots SET field_sources_json = '{invalid-json'")
    assert store.abrp_context(SCOPE)["snapshot"] is None
    assert store.set_abrp_consent(SCOPE, False)["consent"] is False


def test_valid_import_revokes_invalid_import_preserves_and_export_excludes_preferences(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: NOW)
    snapshot(store)
    generation = store.set_abrp_consent(SCOPE, True)["generation"]
    archive = store.export_data()
    assert archive["schema_version"] == 15
    text = json.dumps(archive)
    assert "abrp" not in text and SCOPE not in text
    invalid = deepcopy(archive)
    invalid["settings"]["tariff_eur_kwh"] = -1
    with pytest.raises(DomainError):
        store.import_data(invalid)
    assert store.abrp_context(SCOPE)["consent"]
    assert store.abrp_context(SCOPE)["generation"] == generation
    store.import_data(archive)
    context = store.abrp_context(SCOPE)
    assert not context["consent"] and context["generation"] > generation
    assert context["last_attempt_key"] is None
    assert store.export_data() == archive


@pytest.mark.parametrize("scope,enabled", [("private-input", True), (SCOPE, 1), (SCOPE, "true"), (None, False)])
def test_preference_invalid_inputs_are_rejected_without_reflection(tmp_path, scope, enabled):
    store = Store(tmp_path / "pulse.sqlite3")
    with pytest.raises(DomainError) as error:
        store.set_abrp_consent(scope, enabled)
    assert "private-input" not in str(error.value)
    assert not store.abrp_context(SCOPE)["consent"]
