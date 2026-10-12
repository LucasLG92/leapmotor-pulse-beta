"""Durable, bounded operational state for generic backup-alert delivery."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import re
import uuid
from typing import Any

from pulse.widget_profiles import WidgetProfileValidationError, validate_owner_id


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[0-9a-f]{32}\Z")
_PROFILES = frozenset({"sqlite", "host"})
_STATUSES = frozenset({"healthy", "alert", "unavailable"})
_REASONS = frozenset({None, "missing", "invalid", "stale"})
_KINDS = frozenset({"backup_alert", "backup_recovery"})
_MAX_SOURCE_EVENTS = 64
_MAX_STORED_EVENTS = 2048
_MAX_STORED_DELIVERIES = 32768


class BackupAlertStoreMixin:
    """Small operational ledger; it is deliberately absent from JSON exports."""

    def _create_backup_alert_schema(self, connection) -> None:
        connection.execute(
            """CREATE TABLE backup_alert_runtime (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                scope TEXT NOT NULL CHECK (length(scope) IN (0, 64)),
                updated_at TEXT NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE backup_alert_observations (
                scope TEXT NOT NULL CHECK (length(scope) = 64),
                profile TEXT NOT NULL CHECK (profile IN ('sqlite', 'host')),
                status TEXT NOT NULL CHECK (status IN ('healthy', 'alert', 'unavailable')),
                checked_at TEXT,
                fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 64),
                journal_id TEXT CHECK (journal_id IS NULL OR
                    (length(journal_id) = 32 AND journal_id NOT GLOB '*[^0-9a-f]*')),
                sequence INTEGER NOT NULL CHECK (sequence >= 0),
                journal_gap INTEGER NOT NULL CHECK (journal_gap IN (0, 1)),
                reason_code TEXT CHECK (reason_code IS NULL OR
                    reason_code IN ('missing', 'invalid', 'stale')),
                updated_at TEXT NOT NULL,
                PRIMARY KEY (scope, profile)
            )"""
        )
        connection.execute(
            """CREATE TABLE backup_alert_events (
                scope TEXT NOT NULL CHECK (length(scope) = 64),
                event_id TEXT NOT NULL CHECK (
                    length(event_id) = 32 AND event_id NOT GLOB '*[^0-9a-f]*'
                ),
                profile TEXT NOT NULL CHECK (profile IN ('sqlite', 'host')),
                journal_id TEXT CHECK (journal_id IS NULL OR
                    (length(journal_id) = 32 AND journal_id NOT GLOB '*[^0-9a-f]*')),
                source_event_id TEXT NOT NULL CHECK (
                    length(source_event_id) = 32 AND source_event_id NOT GLOB '*[^0-9a-f]*'
                ),
                source_sequence INTEGER CHECK (source_sequence IS NULL OR source_sequence > 0),
                kind TEXT NOT NULL CHECK (kind IN ('backup_alert', 'backup_recovery')),
                origin TEXT NOT NULL CHECK (origin IN ('journal', 'availability', 'journal_gap')),
                created_at TEXT NOT NULL,
                PRIMARY KEY (scope, event_id)
            )"""
        )
        connection.execute(
            "CREATE INDEX idx_backup_alert_events_recent "
            "ON backup_alert_events(scope, created_at, event_id)"
        )
        connection.execute(
            """CREATE TABLE backup_alert_deliveries (
                scope TEXT NOT NULL CHECK (length(scope) = 64),
                event_id TEXT NOT NULL CHECK (
                    length(event_id) = 32 AND event_id NOT GLOB '*[^0-9a-f]*'
                ),
                subscription_id TEXT NOT NULL CHECK (
                    length(subscription_id) = 32 AND subscription_id NOT GLOB '*[^0-9a-f]*'
                ),
                revision INTEGER NOT NULL CHECK (revision > 0),
                status TEXT NOT NULL CHECK (status IN ('pending', 'accepted', 'failed', 'uncertain')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (scope, event_id, subscription_id, revision)
            )"""
        )
        connection.execute(
            "CREATE INDEX idx_backup_alert_deliveries_recent "
            "ON backup_alert_deliveries(updated_at DESC)"
        )

    def ensure_backup_alert_scope(self, scope: Any) -> bool:
        """Activate one configuration scope and invalidate consent on scope changes."""
        selected = _scope(scope)
        now = _format_datetime(self._clock_now())
        with self._transaction() as connection:
            return self._activate_scope(connection, selected, now)

    def _activate_scope(self, connection, scope: str, now: str) -> bool:
        current = connection.execute(
            "SELECT scope FROM backup_alert_runtime WHERE id = 1"
        ).fetchone()
        if current is None:
            connection.execute(
                "INSERT INTO backup_alert_runtime(id, scope, updated_at) VALUES (1, ?, ?)",
                (scope, now),
            )
            return True
        if current["scope"] == scope:
            return False
        connection.execute(
            "UPDATE backup_alert_deliveries SET status = 'uncertain', updated_at = ? "
            "WHERE status = 'pending'", (now,)
        )
        connection.execute(
            """UPDATE push_subscriptions
               SET backup_alerts_enabled = 0, backup_alerts_consented_at = NULL,
                   revision = revision + CASE WHEN backup_alerts_enabled = 1 THEN 1 ELSE 0 END,
                   last_result = CASE WHEN last_result = 'pending' THEN 'uncertain' ELSE last_result END,
                   updated_at = CASE WHEN backup_alerts_enabled = 1 OR last_result = 'pending'
                                     THEN ? ELSE updated_at END""",
            (now,),
        )
        connection.execute("DELETE FROM backup_alert_observations")
        connection.execute("DELETE FROM backup_alert_events")
        connection.execute(
            "INSERT INTO backup_alert_runtime(id, scope, updated_at) VALUES (1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET scope = excluded.scope, updated_at = excluded.updated_at",
            (scope, now),
        )
        self._prune_backup_alert_deliveries(connection)
        return True

    def observe_backup_reports(self, scope: Any, reports: Any) -> dict[str, Any]:
        """Atomically persist snapshots, journal cursors, gaps, and synthetic transitions."""
        selected = _scope(scope)
        clean_reports = _reports(reports)
        now = _format_datetime(self._clock_now())
        with self._transaction() as connection:
            self._activate_scope(connection, selected, now)
            for report in clean_reports:
                self._observe_one(connection, selected, report, now)
            self._prune_backup_alert_events(connection, selected)
            self._prune_backup_alert_deliveries(connection)
            return {"scope_active": True, "event_count": connection.execute(
                "SELECT COUNT(*) FROM backup_alert_events WHERE scope = ?", (selected,)
            ).fetchone()[0]}

    def _observe_one(self, connection, scope: str, report: dict[str, Any], now: str) -> None:
        profile = report["profile"]
        previous = connection.execute(
            "SELECT * FROM backup_alert_observations WHERE scope = ? AND profile = ?",
            (scope, profile),
        ).fetchone()
        old_status = previous["status"] if previous is not None else None
        prior_journal = previous["journal_id"] if previous is not None else None
        prior_sequence = previous["sequence"] if previous is not None else 0
        gap_latched = bool(previous["journal_gap"]) if previous is not None else False
        journal_id = report["journal_id"] or prior_journal
        sequence = report["sequence"] if report["journal_id"] else prior_sequence
        gap_now = False
        new_journal = bool(report["journal_id"] and prior_journal
                           and report["journal_id"] != prior_journal)
        if new_journal:
            gap_now = True
            sequence = report["sequence"]
        elif report["journal_id"] and prior_journal == report["journal_id"]:
            if report["sequence"] < prior_sequence:
                gap_now = True
                sequence = report["sequence"]
            elif report["sequence"] > prior_sequence:
                first_sequence = report["events"][0]["sequence"] if report["events"] else None
                if first_sequence is None or first_sequence > prior_sequence + 1:
                    gap_now = True
        elif report["journal_id"] and prior_journal is None:
            if report["events"] and report["events"][0]["sequence"] > 1:
                gap_now = True

        if report["journal_id"] and not new_journal and report["sequence"] >= prior_sequence:
            threshold = 0 if previous is None else prior_sequence
            for event in report["events"]:
                if event["sequence"] > threshold:
                    self._insert_backup_event(
                        connection, scope, event["id"], profile, report["journal_id"],
                        event["sequence"], event["kind"], "journal", event["at"],
                    )
        if gap_now and not gap_latched:
            self._insert_backup_event(
                connection, scope, uuid.uuid4().hex, profile, journal_id, None,
                "backup_alert", "journal_gap", now,
            )
            gap_latched = True

        current_status = report["status"]
        if current_status == "unavailable" and old_status != "unavailable":
            self._insert_backup_event(
                connection, scope, uuid.uuid4().hex, profile, journal_id, None,
                "backup_alert", "availability", now,
            )
        elif old_status == "unavailable" and current_status == "healthy":
            self._insert_backup_event(
                connection, scope, uuid.uuid4().hex, profile, journal_id, None,
                "backup_recovery", "availability", now,
            )

        connection.execute(
            """INSERT INTO backup_alert_observations
               (scope, profile, status, checked_at, fingerprint, journal_id, sequence,
                journal_gap, reason_code, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(scope, profile) DO UPDATE SET
                 status = excluded.status, checked_at = excluded.checked_at,
                 fingerprint = excluded.fingerprint, journal_id = excluded.journal_id,
                 sequence = excluded.sequence, journal_gap = excluded.journal_gap,
                 reason_code = excluded.reason_code, updated_at = excluded.updated_at""",
            (scope, profile, current_status, report["checked_at"], report["fingerprint"],
             journal_id, sequence, int(gap_latched), report["reason_code"], now),
        )

    def _insert_backup_event(self, connection, scope, source_event_id, profile, journal_id,
                             sequence, kind, origin, created_at):
        # Source event IDs are only unique inside a logical profile and journal.
        identity = hashlib.sha256(
            f"{scope}\n{profile}\n{journal_id or ''}\n{source_event_id}".encode("ascii")
        ).hexdigest()[:32]
        connection.execute(
            """INSERT OR IGNORE INTO backup_alert_events
               (scope, event_id, profile, journal_id, source_event_id, source_sequence,
                kind, origin, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (scope, identity, profile, journal_id, source_event_id, sequence, kind, origin, created_at),
        )

    def list_backup_alert_candidates(self, scope: Any, owner_id: Any) -> list[dict[str, Any]]:
        selected = _scope(scope)
        _owner(owner_id)
        with self._read_transaction() as connection:
            active = connection.execute(
                "SELECT scope FROM backup_alert_runtime WHERE id = 1"
            ).fetchone()
            if active is None or active["scope"] != selected:
                return []
            return [dict(row) for row in connection.execute(
                """SELECT event_id, kind, created_at
                   FROM backup_alert_events WHERE scope = ?
                   ORDER BY created_at, rowid LIMIT ?""",
                (selected, _MAX_STORED_EVENTS),
            ).fetchall()]

    def reserve_backup_alert_delivery(
        self, scope: Any, owner_id: Any, item_id: Any, revision: Any,
        event_id: Any, consented_at: Any, min_interval_seconds: Any = 60,
    ) -> dict[str, Any] | None:
        selected, owner, item, event = _scope(scope), _owner(owner_id), _item_id(item_id), _item_id(event_id)
        if type(revision) is not int or revision < 1:
            raise _error("Los datos del aviso de copias no son válidos.")
        if type(min_interval_seconds) is not int or not 0 <= min_interval_seconds <= 86_400:
            raise _error("Los datos del aviso de copias no son válidos.")
        if consented_at is None:
            return None
        consent_time = _datetime(consented_at)
        now = self._clock_now()
        now_text = _format_datetime(now)
        with self._transaction() as connection:
            active = connection.execute(
                "SELECT scope FROM backup_alert_runtime WHERE id = 1"
            ).fetchone()
            if active is None or active["scope"] != selected:
                return None
            row = connection.execute(
                "SELECT * FROM push_subscriptions WHERE owner_id = ? AND id = ?",
                (owner, item),
            ).fetchone()
            if (row is None or row["revision"] != revision or not row["backup_alerts_enabled"]
                    or row["backup_alerts_consented_at"] != _format_datetime(consent_time)
                    or _stored_datetime(row["expires_at"]) <= now):
                return None
            alert = connection.execute(
                "SELECT created_at FROM backup_alert_events WHERE scope = ? AND event_id = ?",
                (selected, event),
            ).fetchone()
            if alert is None or _stored_datetime(alert["created_at"]) < consent_time:
                return None
            prior = connection.execute(
                """SELECT status FROM backup_alert_deliveries
                   WHERE scope = ? AND event_id = ? AND subscription_id = ? AND revision = ?""",
                (selected, event, item, revision),
            ).fetchone()
            if prior is not None:
                return None
            if row["last_result"] == "pending":
                return None
            if row["last_attempt_at"] is not None:
                last_attempt = _stored_datetime(row["last_attempt_at"])
                if now < last_attempt + timedelta(seconds=min_interval_seconds):
                    return None
            connection.execute(
                """INSERT INTO backup_alert_deliveries
                   (scope, event_id, subscription_id, revision, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'pending', ?, ?)""",
                (selected, event, item, revision, now_text, now_text),
            )
            connection.execute(
                """UPDATE push_subscriptions SET last_attempt_at = ?, last_result = 'pending',
                   updated_at = ? WHERE owner_id = ? AND id = ? AND revision = ?""",
                (now_text, now_text, owner, item, revision),
            )
            return dict(row) | {"last_attempt_at": now_text, "last_result": "pending", "updated_at": now_text}

    def finish_backup_alert_delivery(
        self, scope: Any, owner_id: Any, item_id: Any, revision: Any,
        event_id: Any, result: Any,
    ) -> bool:
        selected, owner, item, event = _scope(scope), _owner(owner_id), _item_id(item_id), _item_id(event_id)
        if type(revision) is not int or revision < 1 or result not in {"accepted", "failed", "uncertain"}:
            raise _error("Los datos del aviso de copias no son válidos.")
        now_text = _format_datetime(self._clock_now())
        with self._transaction() as connection:
            cursor = connection.execute(
                """UPDATE backup_alert_deliveries SET status = ?, updated_at = ?
                   WHERE scope = ? AND event_id = ? AND subscription_id = ?
                     AND revision = ? AND status = 'pending'""",
                (result, now_text, selected, event, item, revision),
            )
            if cursor.rowcount != 1:
                return False
            if result == "accepted":
                connection.execute(
                    """UPDATE push_subscriptions SET last_result = 'accepted',
                       last_accepted_at = ?, updated_at = ?
                       WHERE owner_id = ? AND id = ? AND revision = ? AND last_result = 'pending'""",
                    (now_text, now_text, owner, item, revision),
                )
            else:
                connection.execute(
                    """UPDATE push_subscriptions SET last_result = ?, updated_at = ?
                       WHERE owner_id = ? AND id = ? AND revision = ? AND last_result = 'pending'""",
                    (result, now_text, owner, item, revision),
                )
            return True

    def _prune_backup_alert_events(self, connection, scope: str) -> None:
        connection.execute(
            """DELETE FROM backup_alert_events WHERE scope = ? AND event_id NOT IN (
                 SELECT event_id FROM backup_alert_events WHERE scope = ?
                 ORDER BY created_at DESC, rowid DESC LIMIT ?
               )""",
            (scope, scope, _MAX_STORED_EVENTS),
        )

    def _prune_backup_alert_deliveries(self, connection) -> None:
        connection.execute(
            """DELETE FROM backup_alert_deliveries WHERE rowid NOT IN (
                 SELECT rowid FROM backup_alert_deliveries
                 ORDER BY updated_at DESC, rowid DESC LIMIT ?
               )""",
            (_MAX_STORED_DELIVERIES,),
        )


def _reports(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise _error("Los informes del monitor de copias no son válidos.")
    if any(not isinstance(report, dict) for report in value):
        raise _error("Los informes del monitor de copias no son válidos.")
    profiles = [report.get("profile") for report in value]
    if any(not isinstance(profile, str) for profile in profiles) or set(profiles) != _PROFILES:
        raise _error("Los informes del monitor de copias no son válidos.")
    clean = []
    for report in value:
        if set(report) != {"profile", "status", "checked_at", "journal_id", "sequence",
                           "events", "reason_code", "fingerprint"}:
            raise _error("Los informes del monitor de copias no son válidos.")
        if (not isinstance(report["status"], str) or report["status"] not in _STATUSES
                or (report["reason_code"] is not None and
                    (not isinstance(report["reason_code"], str) or report["reason_code"] not in _REASONS))):
            raise _error("Los informes del monitor de copias no son válidos.")
        if not isinstance(report["fingerprint"], str) or _SHA256.fullmatch(report["fingerprint"]) is None:
            raise _error("Los informes del monitor de copias no son válidos.")
        checked_at = report["checked_at"]
        if checked_at is not None:
            _datetime(checked_at)
        journal = report["journal_id"]
        if journal is not None and (not isinstance(journal, str) or _ID.fullmatch(journal) is None):
            raise _error("Los informes del monitor de copias no son válidos.")
        sequence = report["sequence"]
        if type(sequence) is not int or not 0 <= sequence <= 2**53 - 1:
            raise _error("Los informes del monitor de copias no son válidos.")
        events = report["events"]
        if not isinstance(events, list) or len(events) > _MAX_SOURCE_EVENTS:
            raise _error("Los informes del monitor de copias no son válidos.")
        prior_sequence = sequence - len(events)
        prior_time = None
        for event in events:
            if (not isinstance(event, dict) or set(event) != {"id", "sequence", "kind", "at"}
                    or not isinstance(event["id"], str) or _ID.fullmatch(event["id"]) is None
                    or type(event["sequence"]) is not int or event["sequence"] != prior_sequence + 1
                    or not isinstance(event["kind"], str) or event["kind"] not in _KINDS):
                raise _error("Los informes del monitor de copias no son válidos.")
            parsed = _datetime(event["at"])
            if checked_at is not None and parsed > _datetime(checked_at):
                raise _error("Los informes del monitor de copias no son válidos.")
            if prior_time is not None and parsed < prior_time:
                raise _error("Los informes del monitor de copias no son válidos.")
            prior_sequence, prior_time = event["sequence"], parsed
        if prior_sequence != sequence or (sequence == 0) != (not events):
            raise _error("Los informes del monitor de copias no son válidos.")
        if report["status"] == "unavailable":
            if report["reason_code"] is None:
                raise _error("Los informes del monitor de copias no son válidos.")
            if report["reason_code"] in {"missing", "invalid"} and (
                checked_at is not None or journal is not None or sequence != 0 or events
            ):
                raise _error("Los informes del monitor de copias no son válidos.")
            if report["reason_code"] == "stale" and (checked_at is None or journal is None):
                raise _error("Los informes del monitor de copias no son válidos.")
        elif report["reason_code"] is not None or checked_at is None or journal is None:
            raise _error("Los informes del monitor de copias no son válidos.")
        clean.append({
            "profile": report["profile"], "status": report["status"], "checked_at": checked_at,
            "journal_id": journal, "sequence": sequence,
            "events": [{"id": event["id"], "sequence": event["sequence"], "kind": event["kind"],
                        "at": event["at"]} for event in events],
            "reason_code": report["reason_code"], "fingerprint": report["fingerprint"],
        })
    return tuple(sorted(clean, key=lambda report: report["profile"]))


def _scope(value: Any) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _error("La configuración del monitor de copias no es válida.")
    return value


def _owner(value: Any) -> str:
    try:
        return validate_owner_id(value)
    except WidgetProfileValidationError:
        raise _error("Los datos del aviso de copias no son válidos.") from None


def _item_id(value: Any) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise _error("Los datos del aviso de copias no son válidos.")
    return value


def _datetime(value: Any) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise _error("Los datos del aviso de copias no son válidos.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        raise _error("Los datos del aviso de copias no son válidos.") from None


def _stored_datetime(value: Any) -> datetime:
    try:
        return _datetime(value)
    except Exception:
        raise _error("No se pudo acceder al almacenamiento Pulse.", 500) from None


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _error(message: str, status: int = 400) -> Exception:
    from pulse.domain import DomainError
    return DomainError(message, status)
