"""Durable operational behavior for backup notices, using normalized fixtures only."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3
import uuid

import pytest

from pulse.domain import DomainError, Store


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, **delta):
        self.now += timedelta(**delta)


SCOPE = hashlib.sha256(b"test-only-scope").hexdigest()
JOURNAL = "1" * 32
OTHER_JOURNAL = "2" * 32
EVENT = "a" * 32


def report(profile, clock, *, status="healthy", journal_id=JOURNAL, sequence=0,
           events=(), reason_code=None, checked_at=None):
    checked = checked_at or clock.now
    if status == "unavailable" and reason_code in {"missing", "invalid"}:
        checked, journal_id, sequence, events = None, None, 0, ()
    fingerprint_input = {"profile": profile, "status": status, "journal": journal_id,
                         "sequence": sequence, "reason": reason_code}
    fingerprint = hashlib.sha256(json.dumps(fingerprint_input, sort_keys=True).encode()).hexdigest()
    return {"profile": profile, "status": status,
            "checked_at": checked.isoformat().replace("+00:00", "Z") if checked else None,
            "journal_id": journal_id, "sequence": sequence, "events": list(events),
            "reason_code": reason_code, "fingerprint": fingerprint}


def reports(clock, sqlite_report=None, host_report=None):
    return (sqlite_report or report("sqlite", clock), host_report or report("host", clock))


def source_event(event_id, sequence, kind, at):
    return {"id": event_id, "sequence": sequence, "kind": kind,
            "at": at.isoformat().replace("+00:00", "Z")}


def subscribed(store, clock, *, owner="owner-a", endpoint="endpoint-a", session="session-a"):
    return store.upsert_push_subscription(
        owner, hashlib.sha256(endpoint.encode()).hexdigest(), hashlib.sha256(session.encode()).hexdigest(),
        "Test device", "gAAAA-test-ciphertext",
        (clock.now + timedelta(hours=4)).isoformat(),
    )


def test_initial_healthy_and_unchanged_reports_do_not_create_events(tmp_path):
    clock = Clock()
    store = Store(tmp_path / "pulse.sqlite3", clock=clock)
    store.observe_backup_reports(SCOPE, reports(clock))
    store.observe_backup_reports(SCOPE, reports(clock))
    assert store.list_backup_alert_candidates(SCOPE, "owner-a") == []


def test_source_journal_deduplicates_by_scope_profile_journal_and_event(tmp_path):
    clock = Clock()
    store = Store(tmp_path / "pulse.sqlite3", clock=clock)
    store.observe_backup_reports(SCOPE, reports(clock))
    same_raw_event = source_event(EVENT, 1, "backup_alert", clock.now)
    store.observe_backup_reports(SCOPE, reports(clock,
        report("sqlite", clock, status="alert", sequence=1, events=[same_raw_event]),
        report("host", clock, status="alert", sequence=1, events=[same_raw_event])))
    store.observe_backup_reports(SCOPE, reports(clock,
        report("sqlite", clock, status="alert", sequence=1, events=[same_raw_event]),
        report("host", clock, status="alert", sequence=1, events=[same_raw_event])))
    candidates = store.list_backup_alert_candidates(SCOPE, "owner-a")
    assert len(candidates) == 2
    assert len({item["event_id"] for item in candidates}) == 2


def test_journal_replacement_marks_coverage_uncertain_without_importing_old_entries(tmp_path):
    clock = Clock()
    store = Store(tmp_path / "pulse.sqlite3", clock=clock)
    store.observe_backup_reports(SCOPE, reports(clock))
    first = source_event(EVENT, 1, "backup_alert", clock.now)
    store.observe_backup_reports(SCOPE, reports(clock,
        report("sqlite", clock, status="alert", sequence=1, events=[first])))
    replacement_event = source_event(EVENT, 1, "backup_recovery", clock.now)
    store.observe_backup_reports(SCOPE, reports(clock,
        report("sqlite", clock, status="healthy", journal_id=OTHER_JOURNAL, sequence=1,
               events=[replacement_event])))
    with sqlite3.connect(store.path) as db:
        observation = db.execute(
            "SELECT journal_id, sequence, journal_gap FROM backup_alert_observations "
            "WHERE scope = ? AND profile = 'sqlite'", (SCOPE,),
        ).fetchone()
        events = db.execute(
            "SELECT source_event_id, origin, kind FROM backup_alert_events "
            "WHERE scope = ? AND profile = 'sqlite' ORDER BY rowid", (SCOPE,),
        ).fetchall()
    assert observation == (OTHER_JOURNAL, 1, 1)
    assert [row[1:] for row in events] == [("journal", "backup_alert"), ("journal_gap", "backup_alert")]
    assert all(row[0] != EVENT or row[1] == "journal" for row in events)


def test_missing_report_alert_and_healthy_recovery_are_durable_once(tmp_path):
    clock = Clock()
    path = tmp_path / "pulse.sqlite3"
    store = Store(path, clock=clock)
    store.observe_backup_reports(SCOPE, reports(clock))
    missing = reports(clock, report("sqlite", clock, status="unavailable", reason_code="missing"))
    store.observe_backup_reports(SCOPE, missing)
    store.observe_backup_reports(SCOPE, reports(clock,
        report("sqlite", clock, status="unavailable", reason_code="invalid")))
    alerts = store.list_backup_alert_candidates(SCOPE, "owner-a")
    assert len(alerts) == 1 and alerts[0]["kind"] == "backup_alert"

    store = Store(path, clock=clock)
    store.observe_backup_reports(SCOPE, reports(clock))
    events = store.list_backup_alert_candidates(SCOPE, "owner-a")
    assert [event["kind"] for event in events] == ["backup_alert", "backup_recovery"]


def test_cooldown_does_not_reserve_event_and_pending_uncertain_never_replays(tmp_path):
    clock = Clock()
    store = Store(tmp_path / "pulse.sqlite3", clock=clock)
    row = subscribed(store, clock)
    consent = clock.now.isoformat().replace("+00:00", "Z")
    row = store.set_backup_alert_consent("owner-a", row["id"], True, consent,
                                         expected_revision=row["revision"])
    store.observe_backup_reports(SCOPE, reports(clock))
    event = source_event(EVENT, 1, "backup_alert", clock.now + timedelta(seconds=1))
    clock.advance(seconds=2)
    store.observe_backup_reports(SCOPE, reports(clock,
        report("sqlite", clock, status="alert", sequence=1, events=[event])))
    candidate = store.list_backup_alert_candidates(SCOPE, "owner-a")[0]
    push_attempt = store.reserve_push_attempt("owner-a", row["id"], row["revision"])
    store.finish_push_attempt("owner-a", row["id"], row["revision"], "accepted")
    assert store.reserve_backup_alert_delivery(SCOPE, "owner-a", row["id"], row["revision"],
        candidate["event_id"], row["backup_alerts_consented_at"]) is None
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM backup_alert_deliveries").fetchone()[0] == 0

    clock.advance(seconds=60)
    reserved = store.reserve_backup_alert_delivery(SCOPE, "owner-a", row["id"], row["revision"],
        candidate["event_id"], row["backup_alerts_consented_at"])
    assert reserved and reserved["last_result"] == "pending"
    assert store.reserve_backup_alert_delivery(SCOPE, "owner-a", row["id"], row["revision"],
        candidate["event_id"], row["backup_alerts_consented_at"]) is None
    assert store.finish_backup_alert_delivery(SCOPE, "owner-a", row["id"], row["revision"],
        candidate["event_id"], "uncertain")
    reopened = Store(tmp_path / "pulse.sqlite3", clock=clock)
    assert reopened.reserve_backup_alert_delivery(SCOPE, "owner-a", row["id"], row["revision"],
        candidate["event_id"], row["backup_alerts_consented_at"]) is None
    assert store.get_push_subscription("owner-a", row["id"])["last_result"] == "uncertain"


def test_rebinding_same_session_invalidates_consent_and_pending_backup_attempt(tmp_path):
    clock = Clock()
    store = Store(tmp_path / "pulse.sqlite3", clock=clock)
    row = subscribed(store, clock)
    consent = clock.now.isoformat().replace("+00:00", "Z")
    row = store.set_backup_alert_consent("owner-a", row["id"], True, consent,
                                         expected_revision=row["revision"])
    store.observe_backup_reports(SCOPE, reports(clock))
    event = source_event(EVENT, 1, "backup_alert", clock.now)
    clock.advance(seconds=1)
    store.observe_backup_reports(SCOPE, reports(clock,
        report("sqlite", clock, status="alert", sequence=1, events=[event])))
    candidate = store.list_backup_alert_candidates(SCOPE, "owner-a")[0]
    reserved = store.reserve_backup_alert_delivery(SCOPE, "owner-a", row["id"], row["revision"],
        candidate["event_id"], row["backup_alerts_consented_at"])
    assert reserved and reserved["last_result"] == "pending"

    rebound = subscribed(store, clock, session="session-a")
    assert rebound["id"] == row["id"] and rebound["revision"] == row["revision"] + 1
    assert rebound["backup_alerts_enabled"] == 0
    assert rebound["backup_alerts_consented_at"] is None
    with sqlite3.connect(store.path) as db:
        delivery_status = db.execute(
            "SELECT status FROM backup_alert_deliveries WHERE subscription_id = ?",
            (row["id"],),
        ).fetchone()[0]
    assert delivery_status == "uncertain"
    assert store.reserve_backup_alert_delivery(SCOPE, "owner-a", rebound["id"],
        rebound["revision"], candidate["event_id"], None) is None


def test_scope_change_invalidates_consent_and_clears_old_observations(tmp_path):
    clock = Clock()
    store = Store(tmp_path / "pulse.sqlite3", clock=clock)
    row = subscribed(store, clock)
    row = store.set_backup_alert_consent("owner-a", row["id"], True,
        clock.now.isoformat().replace("+00:00", "Z"), expected_revision=row["revision"])
    store.observe_backup_reports(SCOPE, reports(clock))
    replacement_scope = hashlib.sha256(b"replacement-scope").hexdigest()
    assert store.ensure_backup_alert_scope(replacement_scope)
    updated = store.get_push_subscription("owner-a", row["id"])
    assert updated["revision"] == row["revision"] + 1
    assert updated["backup_alerts_enabled"] == 0 and updated["backup_alerts_consented_at"] is None
    assert store.list_backup_alert_candidates(SCOPE, "owner-a") == []
    assert store.list_backup_alert_candidates(replacement_scope, "owner-a") == []


def test_reports_reject_malformed_contract_without_writing_partial_state(tmp_path):
    clock = Clock()
    store = Store(tmp_path / "pulse.sqlite3", clock=clock)
    malformed_extra_field = list(reports(clock))
    malformed_extra_field[1] = malformed_extra_field[1] | {"source_path": "/private/fixture"}
    malformed_unhashable_profile = list(reports(clock))
    malformed_unhashable_profile[1] = malformed_unhashable_profile[1] | {"profile": []}
    malformed_missing_metadata = list(reports(clock))
    malformed_missing_metadata[0] = malformed_missing_metadata[0] | {
        "status": "unavailable", "reason_code": "missing",
    }
    malformed_stale_without_timestamp = list(reports(clock))
    malformed_stale_without_timestamp[0] = report(
        "sqlite", clock, status="unavailable", reason_code="stale",
    ) | {"checked_at": None}
    for malformed in (malformed_extra_field, malformed_unhashable_profile,
                       malformed_missing_metadata, malformed_stale_without_timestamp):
        with pytest.raises(DomainError):
            store.observe_backup_reports(SCOPE, malformed)
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM backup_alert_runtime").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM backup_alert_observations").fetchone()[0] == 0


def test_v17_migration_preserves_push_rows_and_keeps_alert_consent_disabled(tmp_path):
    clock = Clock()
    path = tmp_path / "migration.sqlite3"
    store = Store(path, clock=clock)
    row = subscribed(store, clock)
    archive = store.export_data()
    with sqlite3.connect(path) as db:
        for table in ("backup_alert_deliveries", "backup_alert_events",
                      "backup_alert_observations", "backup_alert_runtime"):
            db.execute(f"DROP TABLE {table}")
        db.execute("ALTER TABLE push_subscriptions DROP COLUMN backup_alerts_consented_at")
        db.execute("ALTER TABLE push_subscriptions DROP COLUMN backup_alerts_enabled")
        db.execute("DROP TABLE IF EXISTS manual_charge_requests")
        db.execute("PRAGMA user_version = 17")

    migrated = Store(path, clock=clock)
    upgraded = migrated.get_push_subscription("owner-a", row["id"])
    assert upgraded["id"] == row["id"] and upgraded["revision"] == row["revision"]
    assert upgraded["created_at"] == row["created_at"] and upgraded["ciphertext"] == row["ciphertext"]
    assert upgraded["backup_alerts_enabled"] == 0 and upgraded["backup_alerts_consented_at"] is None
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 19
        assert db.execute("PRAGMA table_info(push_subscriptions)").fetchall()[-2][1:] == (
            "backup_alerts_enabled", "INTEGER", 1, "0", 0)
        assert db.execute("SELECT COUNT(*) FROM backup_alert_events").fetchone()[0] == 0
    assert archive["schema_version"] == 15
    assert "push_subscriptions" not in archive
