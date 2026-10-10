"""Store-level tests for native widget pairing and scoped grants.

Credentials and identities are synthetic. No provider, OIDC server, network, or
physical widget is contacted by this module.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import json
import sqlite3

import pytest

from pulse.domain import DomainError, Store
from pulse.native_store import MAX_WIDGET_GRANTS_PER_PROFILE


UTC = timezone.utc
IDENTITY_BASE = {
    "issuer": "https://issuer.example.invalid",
    "client_id": "pulse-native-test",
}


class Clock:
    def __init__(self, value: datetime | None = None):
        self.value = value or datetime(2026, 10, 8, 15, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **delta: int) -> None:
        self.value += timedelta(**delta)


def create_profile(store: Store, owner: str = "owner-a") -> dict:
    return store.create_widget_profile(
        owner,
        {"name": "Widget", "fields": ["battery_pct", "charging"]},
    )


def issue_pairing(store: Store, profile: dict, owner: str = "owner-a") -> str:
    return store.create_widget_pairing(owner, profile["id"])["pairing_code"]


def identity(owner: str = "owner-a") -> dict[str, str]:
    return {**IDENTITY_BASE, "subject": owner}


def random_256(value: int) -> str:
    return base64.urlsafe_b64encode(value.to_bytes(32, "big")).decode("ascii").rstrip("=")


def register(
    store: Store,
    profile: dict,
    *,
    owner: str = "owner-a",
    pairing_code: str | None = None,
    installation_id: str | None = None,
    device_name: str = "Synthetic iPhone",
):
    return store.register_widget_grant(
        identity(owner),
        pairing_code or issue_pairing(store, profile, owner),
        installation_id or random_256(1),
        device_name,
    )


def status_of(callable_):
    with pytest.raises(DomainError) as error:
        callable_()
    return error.value.status


def pvpc_quote():
    from datetime import date, time
    from zoneinfo import ZoneInfo

    from pulse.pvpc import quote_charge, validate_day_snapshot

    day = date(2026, 1, 15)
    zone = ZoneInfo("Europe/Madrid")
    start = datetime.combine(day, time.min, tzinfo=zone).astimezone(UTC)
    intervals = []
    for index in range(24):
        interval_start = start + timedelta(hours=index)
        intervals.append({
            "started_at": interval_start.isoformat().replace("+00:00", "Z"),
            "ended_at": (interval_start + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            "price_eur_kwh": 0.125,
        })
    day_snapshot = validate_day_snapshot({
        "date": day.isoformat(),
        "region": "peninsula",
        "timezone": "Europe/Madrid",
        "source": "REE REData PVPC 1001",
        "fetched_at": "2026-10-08T14:00:00Z",
        "intervals": intervals,
    })
    return quote_charge(
        "2026-01-15T08:30:00Z",
        "2026-01-15T09:30:00Z",
        2,
        [day_snapshot],
        calculated_at=datetime(2026, 10, 8, 14, tzinfo=UTC),
    )


def test_v4_to_current_migration_preserves_pvpc_profiles_and_adds_cascading_tables(tmp_path):
    path = tmp_path / "migration.sqlite3"
    store = Store(path)
    store.update_settings({"pricing_method": "pvpc", "tariff_eur_kwh": 0.42})
    charge = store.add_charge(
        {
            "started_at": "2026-01-15T08:30:00Z",
            "ended_at": "2026-01-15T09:30:00Z",
            "kwh": 2,
            "pricing_method": "pvpc",
        },
        pricing_snapshot=pvpc_quote(),
    )
    profile = create_profile(store)

    # A V4 database has the exact same domain/export tables and no native tables.
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE widget_grants")
        connection.execute("DROP TABLE widget_pairings")
        connection.execute("DROP TABLE push_subscriptions")
        connection.execute("DROP TABLE abrp_preference")
        connection.execute("DROP TABLE observed_events")
        connection.execute("DROP TABLE IF EXISTS charger_pricing_history")
        connection.execute("DROP TABLE charger_energy_intervals")
        connection.execute("DROP TABLE charger_energy_epochs")
        connection.execute("DROP TRIGGER invalidate_charger_pricing_on_session_change")
        connection.execute("DROP TABLE charger_pricing")
        connection.execute("DROP TABLE charger_sessions")
        connection.execute("DROP TABLE charger_observations")
        connection.execute("PRAGMA user_version = 4")

    migrated = Store(path)
    assert migrated.get_charge(charge["id"]) == charge
    assert migrated.get_settings()["pricing_method"] == "pvpc"
    assert migrated.list_widget_profiles("owner-a")["items"] == [profile]
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 14
        assert connection.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0] == 0
        for table in ("widget_pairings", "widget_grants"):
            foreign_keys = connection.execute(
                f"PRAGMA foreign_key_list({table})"
            ).fetchall()
            assert any(
                row[2] == "widget_profiles" and row[6].upper() == "CASCADE"
                for row in foreign_keys
            )


def test_pairing_cooldown_is_atomic_and_code_expires_after_five_minutes(tmp_path):
    clock = Clock()
    store = Store(tmp_path / "pairing.sqlite3", clock=clock)
    profile = create_profile(store)
    issued = store.create_widget_pairing("owner-a", profile["id"])
    code = issued["pairing_code"]
    assert len(code) == 43
    assert datetime.fromisoformat(issued["expires_at"].replace("Z", "+00:00")) == (
        clock.value + timedelta(minutes=5)
    )

    with sqlite3.connect(store.path) as connection:
        before = connection.execute(
            "SELECT code_hash, created_at, expires_at, consumed_at FROM widget_pairings"
        ).fetchone()
        assert before[0] == hashlib.sha256(code.encode("ascii")).hexdigest()
        assert code not in repr(before)

    assert status_of(lambda: store.create_widget_pairing("owner-a", profile["id"])) == 429
    with sqlite3.connect(store.path) as connection:
        after = connection.execute(
            "SELECT code_hash, created_at, expires_at, consumed_at FROM widget_pairings"
        ).fetchone()
    assert after == before

    assert status_of(lambda: register(
        store, profile, pairing_code="malformed", installation_id=random_256(2)
    )) == 401
    clock.advance(minutes=5)
    assert status_of(lambda: register(
        store, profile, pairing_code=code, installation_id=random_256(2)
    )) == 401


def test_pairing_rejects_foreign_subject_then_can_be_used_once_concurrently(tmp_path):
    store = Store(tmp_path / "race.sqlite3")
    profile = create_profile(store, "owner-a")
    code = issue_pairing(store, profile)
    assert status_of(lambda: store.register_widget_grant(
        identity("owner-b"), code, random_256(2), "Foreign device"
    )) == 401

    def redeem(index):
        try:
            value = store.register_widget_grant(
                identity(), code, random_256(index + 1), f"Device {index}"
            )
            return ("ok", value["grant"]["id"])
        except DomainError as error:
            return ("error", error.status)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(redeem, range(8)))
    successes = [item for item in results if item[0] == "ok"]
    failures = [item for item in results if item[0] == "error"]
    assert len(successes) == 1
    assert failures == [("error", 401)] * 7
    assert len(store.list_widget_grants("owner-a", profile["id"])["items"]) == 1


def test_grants_expire_after_thirty_days_and_reads_do_not_extend_ttl(tmp_path):
    clock = Clock()
    store = Store(tmp_path / "expiry.sqlite3", clock=clock)
    profile = create_profile(store)
    result = register(store, profile)
    token = result["widget_token"]
    grant = result["grant"]
    assert token.startswith("pw_") and len(token) == 46
    expected_expiry = clock.value + timedelta(days=30)
    assert datetime.fromisoformat(grant["expires_at"].replace("Z", "+00:00")) == expected_expiry
    context = store.widget_grant_context(token, grant["id"])
    assert context["grant"]["expires_at"] == grant["expires_at"]
    assert context["issuer"] == IDENTITY_BASE["issuer"]
    assert context["client_id"] == IDENTITY_BASE["client_id"]
    assert "subject" not in context and "installation_id" not in context
    assert set(context["grant"]) == {
        "id", "profile_id", "device_name", "created_at", "expires_at", "last_seen_at"
    }

    clock.advance(days=30)
    assert store.list_widget_grants("owner-a", profile["id"]) == {"items": []}
    assert status_of(lambda: store.widget_grant_context(token, grant["id"])) == 401

    # Issuing after expiry prunes the old grant and allows another bounded row.
    replacement = register(store, profile, installation_id=random_256(11))
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM widget_grants").fetchone()[0] == 1
    assert store.widget_grant_context(
        replacement["widget_token"], replacement["grant"]["id"]
    )["grant"]["id"] == replacement["grant"]["id"]


def test_cap_failure_keeps_pairing_unconsumed_and_relink_replaces_same_installation(tmp_path):
    clock = Clock()
    store = Store(tmp_path / "cap.sqlite3", clock=clock)
    profile = create_profile(store)
    original = []
    installations = [random_256(index + 10) for index in range(MAX_WIDGET_GRANTS_PER_PROFILE + 1)]
    for index in range(MAX_WIDGET_GRANTS_PER_PROFILE):
        code = issue_pairing(store, profile)
        original.append(store.register_widget_grant(
            identity(), code, installations[index], f"Device {index}"
        ))
        clock.advance(seconds=30)

    code = issue_pairing(store, profile)
    assert status_of(lambda: store.register_widget_grant(
        identity(), code, installations[-1], "Sixth device"
    )) == 409
    with sqlite3.connect(store.path) as connection:
        row = connection.execute(
            "SELECT consumed_at FROM widget_pairings WHERE profile_id = ?",
            (profile["id"],),
        ).fetchone()
    assert row == (None,)
    assert len(store.list_widget_grants("owner-a", profile["id"])["items"]) == 5

    # A later explicit link for an already linked installation frees its old row first.
    clock.advance(seconds=30)
    replacement_code = issue_pairing(store, profile)
    replacement = store.register_widget_grant(
        identity(), replacement_code, installations[0], "Renamed device"
    )
    assert len(store.list_widget_grants("owner-a", profile["id"])["items"]) == 5
    assert status_of(lambda: store.widget_grant_context(
        original[0]["widget_token"], original[0]["grant"]["id"]
    )) == 401
    assert store.widget_grant_context(
        replacement["widget_token"], replacement["grant"]["id"]
    )["grant"]["device_name"] == "Renamed device"


def test_owner_revocation_profile_changes_and_recovery_revoke_access(tmp_path):
    clock = Clock()
    store = Store(tmp_path / "revoke.sqlite3", clock=clock)
    profile_a = create_profile(store, "owner-a")
    profile_b = create_profile(store, "owner-b")
    grant_a = register(store, profile_a, owner="owner-a")
    grant_b = register(store, profile_b, owner="owner-b", installation_id=random_256(20))

    assert status_of(lambda: store.list_widget_grants("owner-b", profile_a["id"])) == 404
    assert status_of(lambda: store.revoke_widget_grant(
        "owner-b", profile_a["id"], grant_a["grant"]["id"]
    )) == 404
    assert store.widget_grant_context(
        grant_a["widget_token"], grant_a["grant"]["id"]
    )["owner_id"] == "owner-a"

    clock.advance(seconds=30)
    pending_a = issue_pairing(store, profile_a, "owner-a")
    updated_a = store.update_widget_profile(
        "owner-a",
        profile_a["id"],
        {"name": "Updated", "fields": ["charging"], "privacy_mode": "visible"},
    )
    context_a = store.widget_snapshot_context(
        grant_a["widget_token"], grant_a["grant"]["id"]
    )
    assert context_a["owner_id"] == "owner-a"
    assert context_a["profile"] == updated_a
    assert context_a["profile"]["revision"] == profile_a["revision"] + 1
    assert context_a["profile"]["fields"] == ["charging"]
    assert context_a["profile"]["privacy_mode"] == "visible"
    assert status_of(lambda: store.register_widget_grant(
        identity("owner-a"), pending_a, random_256(19), "Before profile edit"
    )) == 401
    assert store.widget_grant_context(
        grant_b["widget_token"], grant_b["grant"]["id"]
    )["owner_id"] == "owner-b"

    clock.advance(seconds=30)
    fresh = register(store, profile_b, owner="owner-b", installation_id=random_256(21))
    assert store.revoke_widget_grant(
        "owner-b", profile_b["id"], fresh["grant"]["id"]
    ) == {"ok": True}
    assert status_of(lambda: store.revoke_widget_grant(
        "owner-b", profile_b["id"], fresh["grant"]["id"]
    )) == 404
    assert status_of(lambda: store.widget_grant_context(
        fresh["widget_token"], fresh["grant"]["id"]
    )) == 401

    clock.advance(seconds=30)
    native = register(store, profile_b, owner="owner-b", installation_id=random_256(22))
    assert store.revoke_native_widget_grant(
        native["widget_token"], native["grant"]["id"]
    ) == {"ok": True}
    assert status_of(lambda: store.widget_grant_context(
        native["widget_token"], native["grant"]["id"]
    )) == 401

    clock.advance(seconds=30)
    pending = issue_pairing(store, profile_b, "owner-b")
    assert store.revoke_all_widget_grants() == {"ok": True}
    assert status_of(lambda: store.register_widget_grant(
        identity("owner-b"), pending, random_256(23), "After recovery"
    )) == 401
    assert status_of(lambda: store.widget_grant_context(
        grant_b["widget_token"], grant_b["grant"]["id"]
    )) == 401

    store.delete_widget_profile("owner-b", profile_b["id"])
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM widget_grants").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM widget_pairings").fetchone()[0] == 0


def test_profile_deletion_cascades_live_grant_and_pending_code(tmp_path):
    clock = Clock()
    store = Store(tmp_path / "profile-cascade.sqlite3", clock=clock)
    profile = create_profile(store)
    grant = register(store, profile)
    clock.advance(seconds=30)
    pending = issue_pairing(store, profile)

    assert store.delete_widget_profile("owner-a", profile["id"]) == {
        "ok": True, "id": profile["id"]
    }
    assert status_of(lambda: store.widget_grant_context(
        grant["widget_token"], grant["grant"]["id"]
    )) == 401
    assert status_of(lambda: store.register_widget_grant(
        identity(), pending, random_256(55), "Deleted profile"
    )) == 401
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM widget_grants").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM widget_pairings").fetchone()[0] == 0


def test_import_export_scopes_profiles_and_invalidates_only_the_imported_owner(tmp_path):
    store = Store(tmp_path / "scoped-import.sqlite3")
    profile_a = create_profile(store, "owner-a")
    profile_b = create_profile(store, "owner-b")
    grant_a = register(store, profile_a, owner="owner-a")
    grant_b = register(store, profile_b, owner="owner-b", installation_id=random_256(30))

    archive_a = store.export_data(owner_id="owner-a")
    assert [item["owner_id"] for item in archive_a["widget_profiles"]] == ["owner-a"]
    assert store.import_data(archive_a, owner_id="owner-a") == {
        "ok": True, "mode": "unconfigured"
    }
    assert store.list_widget_profiles("owner-b")["items"] == [profile_b]
    assert status_of(lambda: store.widget_grant_context(
        grant_a["widget_token"], grant_a["grant"]["id"]
    )) == 401
    assert store.widget_grant_context(
        grant_b["widget_token"], grant_b["grant"]["id"]
    )["owner_id"] == "owner-b"

    invalid = json.loads(json.dumps(archive_a))
    invalid["widget_profiles"].append(store.export_data(owner_id="owner-b")["widget_profiles"][0])
    assert status_of(lambda: store.validate_import(invalid, owner_id="owner-a")) == 400
    assert status_of(lambda: store.import_data(invalid, owner_id="owner-a")) == 400
    assert store.widget_grant_context(
        grant_b["widget_token"], grant_b["grant"]["id"]
    )["owner_id"] == "owner-b"

    # A valid V1 restore has no profile collection and removes only the requested owner.
    legacy_v1 = {
        "schema_version": 1,
        "exported_at": "2026-10-08T15:00:00Z",
        "mode": "unconfigured",
        "settings": {
            "vehicle_name": "Mi Leapmotor B10",
            "model": "B10",
            "tariff_eur_kwh": 0.15,
            "home_label": "Casa",
        },
        "charges": [],
        "journeys": [],
        "maintenance": [],
        "protection": {"enabled": False, "events": []},
    }
    assert store.validate_import(legacy_v1, owner_id="owner-a") == {"ok": True}
    store.import_data(legacy_v1, owner_id="owner-a")
    assert store.list_widget_profiles("owner-a")["items"] == []
    assert store.list_widget_profiles("owner-b")["items"] == [profile_b]
    assert store.widget_grant_context(
        grant_b["widget_token"], grant_b["grant"]["id"]
    )["owner_id"] == "owner-b"


def test_export_and_sqlite_persist_only_hashes_and_reopen_keeps_grant(tmp_path):
    path = tmp_path / "hashes.sqlite3"
    clock = Clock()
    store = Store(path, clock=clock)
    profile = create_profile(store)
    pairing_code = issue_pairing(store, profile)
    installation_id = random_256(40)
    issued = store.register_widget_grant(
        identity(), pairing_code, installation_id, "Synthetic tablet"
    )
    widget_token = issued["widget_token"]

    exported = json.dumps(store.export_data(), allow_nan=False, separators=(",", ":"))
    if any(value in exported for value in (pairing_code, installation_id, widget_token)):
        pytest.fail("La exportación contiene material de enlace o credenciales nativas.")
    assert "widget_grants" not in exported and "widget_pairings" not in exported

    with sqlite3.connect(path) as connection:
        stored_code_hash = connection.execute(
            "SELECT code_hash FROM widget_pairings WHERE profile_id = ?",
            (profile["id"],),
        ).fetchone()[0]
        stored_install_hash, stored_token_hash = connection.execute(
            "SELECT installation_hash, token_hash FROM widget_grants WHERE id = ?",
            (issued["grant"]["id"],),
        ).fetchone()
        dump = "\n".join(row[0] for row in connection.iterdump())
    assert stored_code_hash == hashlib.sha256(pairing_code.encode("ascii")).hexdigest()
    assert stored_install_hash == hashlib.sha256(installation_id.encode("ascii")).hexdigest()
    assert stored_token_hash == hashlib.sha256(widget_token.encode("ascii")).hexdigest()
    if any(value in dump for value in (pairing_code, installation_id, widget_token)):
        pytest.fail("SQLite contiene un secreto sin hash.")

    reopened = Store(path, clock=clock)
    context = reopened.widget_grant_context(widget_token, issued["grant"]["id"])
    assert context["grant"] == issued["grant"]
    assert "owner_id" not in context["profile"]


def test_widget_snapshot_context_reads_one_latest_snapshot_and_minimal_demo(tmp_path, monkeypatch):
    clock = Clock()
    store = Store(tmp_path / "snapshot-context.sqlite3", clock=clock)
    profile = create_profile(store)
    grant = register(store, profile)
    telemetry = store.record_snapshot({
        "source": "MATE-API B10ReadClient",
        "observed_at": "2026-10-08T14:59:00Z",
        "provider_received_at": "2026-10-08T14:59:30Z",
        "vehicle": {
            "battery_pct": 74.5,
            "range_km": 321,
            "odometer_km": 12500.25,
            "locked": None,
            "climate_on": None,
            "charging": True,
        },
        "field_sources": {
            "battery_pct": "precise_soc_percent",
            "range_km": "expected_range_km",
            "odometer_km": "odometer_km",
            "locked": None,
            "climate_on": None,
            "charging": None,
        },
    })

    # This endpoint helper must not invoke full app state or any historical query.
    monkeypatch.setattr(store, "state", lambda *_args, **_kwargs: pytest.fail("state() llamado"))
    monkeypatch.setattr(store, "list_charges", lambda *_args, **_kwargs: pytest.fail("charges consultadas"))
    monkeypatch.setattr(store, "energy_analytics", lambda *_args, **_kwargs: pytest.fail("analytics consultados"))
    monkeypatch.setattr(store, "telemetry_history", lambda *_args, **_kwargs: pytest.fail("historial consultado"))
    context = store.widget_snapshot_context(grant["widget_token"], grant["grant"]["id"])
    assert context["mode"] == "unconfigured"
    assert context["telemetry"] == telemetry
    assert context["demo_vehicle"] is None
    assert context["issuer"] == IDENTITY_BASE["issuer"]
    assert context["client_id"] == IDENTITY_BASE["client_id"]
    assert context["profile"]["privacy_mode"] == "masked"

    store.set_demo(True)
    demo = store.widget_snapshot_context(grant["widget_token"], grant["grant"]["id"])
    assert demo["mode"] == "demo"
    assert set(demo["demo_vehicle"]) == {
        *profile["fields"], "observed_at"
    }
    assert "odometer_km" not in demo["demo_vehicle"]
    assert "locked" not in demo["demo_vehicle"]
    assert demo["telemetry"] == telemetry
    assert demo["grant"]["last_seen_at"] is None
