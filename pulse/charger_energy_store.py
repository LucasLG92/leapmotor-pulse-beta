"""Bounded storage for unqualified evcc 15-minute energy candidates.

The records in this module preserve the exact energy buckets returned by an
explicitly selected evcc loadpoint.  They do not associate energy with a
vehicle session, calculate a tariff, or qualify physical energy use.

This module stays independent of ``pulse.domain`` at import time so archive
validators can be used by the store without creating an import cycle.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re
import sqlite3
from typing import Any, Mapping

from pulse.charger_store import ChargerDataError


MAX_ENERGY_SLOTS = 2_048
MAX_ENERGY_EPOCHS = 64
MAX_ENERGY_SCOPES = 16
MAX_ENERGY_BATCH = 100
MAX_ENERGY_LOADPOINT_LENGTH = 80
FUTURE_TOLERANCE_SECONDS = 60
CONTRACT_REVISION = "evcc-0.316.2-15m-v1"

_KNOWN_CONTRACT_REVISIONS = frozenset({CONTRACT_REVISION})
_SCOPE_RE = re.compile(r"[0-9a-f]{64}\Z")
_EPOCH_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
_ENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9:_.-]{0,79}\Z", re.ASCII)
_EPOCH_FIELDS = frozenset({
    "id", "scope", "entity", "loadpoint", "contract_revision",
    "first_seen_at", "last_seen_at", "closed_at",
})
_SLOT_FIELDS = frozenset({
    "epoch_id", "started_at", "ended_at", "kwh", "return_kwh", "received_at",
})
_ARCHIVE_FIELDS = frozenset({"epochs", "intervals"})
_SLOT_SECONDS = 15 * 60
_MIN_SLOT_YEAR = 2014


class ChargerEnergyError(ChargerDataError):
    """An energy-candidate payload or operation failed its strict contract."""


def energy_epoch_id(
    scope: str,
    entity: str,
    loadpoint: str,
    contract_revision: str,
    first_seen_at: str,
) -> str:
    """Return the stable opaque ID for one observed identity epoch."""
    normalized_scope = validate_energy_scope(scope)
    normalized_entity = _entity(entity)
    normalized_loadpoint = _loadpoint(loadpoint)
    normalized_revision = _contract_revision(contract_revision)
    normalized_first_seen = _datetime(first_seen_at, "first_seen_at")
    canonical = json.dumps(
        [
            normalized_scope,
            normalized_entity,
            normalized_loadpoint,
            normalized_revision,
            normalized_first_seen,
        ],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()[:32]


def validate_energy_scope(value: Any) -> str:
    if not isinstance(value, str) or not _SCOPE_RE.fullmatch(value):
        raise ChargerEnergyError("scope debe ser un SHA-256 hexadecimal en minúsculas.")
    return value


def validate_energy_slot(value: Any) -> dict[str, Any]:
    """Validate and normalize one exact 15-minute energy interval."""
    raw = _exact_mapping(value, _SLOT_FIELDS, "interval")
    epoch_id = raw["epoch_id"]
    if not isinstance(epoch_id, str) or not _EPOCH_ID_RE.fullmatch(epoch_id):
        raise ChargerEnergyError("epoch_id debe ser hexadecimal de 32 caracteres.")
    started_at = _datetime(raw["started_at"], "started_at")
    ended_at = _datetime(raw["ended_at"], "ended_at")
    received_at = _datetime(raw["received_at"], "received_at")
    started = _parse_utc(started_at)
    ended = _parse_utc(ended_at)
    received = _parse_utc(received_at)
    if started.year < _MIN_SLOT_YEAR:
        raise ChargerEnergyError("started_at debe pertenecer al año 2014 o posterior.")
    if started.minute % 15 or started.second or started.microsecond:
        raise ChargerEnergyError("started_at debe coincidir con un cuarto de hora UTC.")
    if ended - started != timedelta(seconds=_SLOT_SECONDS):
        raise ChargerEnergyError("ended_at debe estar exactamente 900 segundos después.")
    if ended > received:
        raise ChargerEnergyError("ended_at no puede ser posterior a received_at.")
    return {
        "epoch_id": epoch_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "kwh": _number(raw["kwh"], "kwh"),
        "return_kwh": _number(raw["return_kwh"], "return_kwh"),
        "received_at": received_at,
    }


def validate_charger_energy_archive(value: Any) -> dict[str, list[dict[str, Any]]]:
    """Validate the complete bounded archive block before a database write."""
    raw = _exact_mapping(value, _ARCHIVE_FIELDS, "charger_energy")
    raw_epochs = raw["epochs"]
    raw_intervals = raw["intervals"]
    if not isinstance(raw_epochs, list) or len(raw_epochs) > MAX_ENERGY_EPOCHS:
        raise ChargerEnergyError("epochs debe contener como máximo 64 épocas.")
    if not isinstance(raw_intervals, list) or len(raw_intervals) > MAX_ENERGY_SLOTS:
        raise ChargerEnergyError("intervals debe contener como máximo 2048 intervalos.")

    epochs: list[dict[str, Any]] = []
    epochs_by_id: dict[str, dict[str, Any]] = {}
    open_scopes: set[str] = set()
    for item in raw_epochs:
        epoch = _validate_epoch(item)
        if epoch["id"] in epochs_by_id:
            raise ChargerEnergyError("charger_energy contiene épocas duplicadas.")
        if epoch["closed_at"] is None:
            if epoch["scope"] in open_scopes:
                raise ChargerEnergyError("Cada scope puede tener una sola época abierta.")
            open_scopes.add(epoch["scope"])
        epochs_by_id[epoch["id"]] = epoch
        epochs.append(epoch)
    if len({epoch["scope"] for epoch in epochs}) > MAX_ENERGY_SCOPES:
        raise ChargerEnergyError("charger_energy supera los 16 scopes.")

    intervals: list[dict[str, Any]] = []
    seen_slots: set[tuple[str, str]] = set()
    for item in raw_intervals:
        slot = validate_energy_slot(item)
        key = (slot["epoch_id"], slot["started_at"])
        if key in seen_slots:
            raise ChargerEnergyError("charger_energy contiene intervalos duplicados.")
        seen_slots.add(key)
        epoch = epochs_by_id.get(slot["epoch_id"])
        if epoch is None:
            raise ChargerEnergyError("Cada intervalo debe referenciar una época existente.")
        if (
            _parse_utc(slot["started_at"]) < _parse_utc(epoch["first_seen_at"])
            or _parse_utc(slot["ended_at"]) > _parse_utc(epoch["last_seen_at"])
        ):
            raise ChargerEnergyError(
                "El intervalo queda fuera de los límites observados de su época."
            )
        intervals.append(slot)
    return {"epochs": epochs, "intervals": intervals}


def validate_charger_energy_future_dates(value: Any, now: datetime) -> None:
    """Reject archived timestamps more than 60 seconds after the Store clock."""
    archive = validate_charger_energy_archive(value)
    normalized_now = _datetime_from_datetime(now, "now")
    limit = _parse_utc(normalized_now) + timedelta(seconds=FUTURE_TOLERANCE_SECONDS)
    for epoch in archive["epochs"]:
        for field in ("first_seen_at", "last_seen_at", "closed_at"):
            timestamp = epoch[field]
            if timestamp is not None and _parse_utc(timestamp) > limit:
                raise ChargerEnergyError(
                    f"{field} no puede superar en más de 60 segundos el reloj de Store."
                )
    for slot in archive["intervals"]:
        for field in ("started_at", "ended_at", "received_at"):
            if _parse_utc(slot[field]) > limit:
                raise ChargerEnergyError(
                    f"{field} no puede superar en más de 60 segundos el reloj de Store."
                )


class ChargerEnergyStoreMixin:
    """SQLite persistence for bounded, explicitly observed energy candidates."""

    def _migrate_v11_to_v12(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """CREATE TABLE charger_energy_epochs (
                id TEXT PRIMARY KEY CHECK (
                    length(id) = 32 AND id NOT GLOB '*[^0-9a-f]*'
                ),
                scope TEXT NOT NULL CHECK (
                    length(scope) = 64 AND scope NOT GLOB '*[^0-9a-f]*'
                ),
                entity TEXT NOT NULL CHECK (
                    length(entity) BETWEEN 1 AND 80
                    AND substr(entity, 1, 1) GLOB '[A-Za-z0-9]'
                    AND entity NOT GLOB '*[^A-Za-z0-9:_.-]*'
                ),
                loadpoint TEXT NOT NULL CHECK (length(loadpoint) BETWEEN 1 AND 80),
                contract_revision TEXT NOT NULL CHECK (
                    contract_revision = 'evcc-0.316.2-15m-v1'
                ),
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL CHECK (last_seen_at >= first_seen_at),
                closed_at TEXT CHECK (closed_at IS NULL OR closed_at >= last_seen_at)
            )"""
        )
        connection.execute(
            "CREATE UNIQUE INDEX idx_charger_energy_open_scope "
            "ON charger_energy_epochs(scope) WHERE closed_at IS NULL"
        )
        connection.execute(
            "CREATE INDEX idx_charger_energy_epochs_retention "
            "ON charger_energy_epochs(last_seen_at ASC, id ASC)"
        )
        connection.execute(
            """CREATE TABLE charger_energy_intervals (
                epoch_id TEXT NOT NULL REFERENCES charger_energy_epochs(id) ON DELETE CASCADE,
                started_at TEXT NOT NULL,
                ended_at TEXT NOT NULL CHECK (ended_at > started_at),
                kwh REAL NOT NULL CHECK (kwh >= 0 AND kwh <= 500),
                return_kwh REAL NOT NULL CHECK (return_kwh >= 0 AND return_kwh <= 500),
                received_at TEXT NOT NULL CHECK (ended_at <= received_at),
                PRIMARY KEY (epoch_id, started_at)
            )"""
        )
        connection.execute(
            "CREATE INDEX idx_charger_energy_intervals_global_order "
            "ON charger_energy_intervals(ended_at ASC, epoch_id ASC, started_at ASC)"
        )

    def observe_charger_energy(
        self, scope: Any, identity: Any, *, received_at: Any
    ) -> dict[str, Any]:
        """Record one identity observation, closing stale or changed epochs."""
        try:
            normalized_scope = validate_energy_scope(scope)
            normalized_received_at = _datetime(received_at, "received_at")
            normalized_identity = None if identity is None else _validate_identity(identity)
            received = _parse_utc(normalized_received_at)
            with self._transaction() as connection:
                current = connection.execute(
                    "SELECT * FROM charger_energy_epochs "
                    "WHERE scope = ? AND closed_at IS NULL",
                    (normalized_scope,),
                ).fetchone()
                latest = connection.execute(
                    """SELECT * FROM charger_energy_epochs WHERE scope = ?
                       ORDER BY COALESCE(closed_at, last_seen_at) DESC, id DESC LIMIT 1""",
                    (normalized_scope,),
                ).fetchone()
                cursor_text = None
                if latest is not None:
                    cursor_text = max(latest["last_seen_at"], latest["closed_at"] or "")
                if cursor_text is not None and received <= _parse_utc(cursor_text):
                    return {
                        "changed": False,
                        "epoch": None if current is None else _energy_epoch_from_row(current),
                    }

                if normalized_identity is None:
                    if current is None:
                        return {"changed": False, "epoch": None}
                    connection.execute(
                        "UPDATE charger_energy_epochs SET closed_at = ? WHERE id = ?",
                        (normalized_received_at, current["id"]),
                    )
                    return {"changed": True, "epoch": None}

                if current is not None and _same_epoch_identity(current, normalized_identity):
                    connection.execute(
                        "UPDATE charger_energy_epochs SET last_seen_at = ? WHERE id = ?",
                        (normalized_received_at, current["id"]),
                    )
                    updated = connection.execute(
                        "SELECT * FROM charger_energy_epochs WHERE id = ?", (current["id"],)
                    ).fetchone()
                    return {"changed": True, "epoch": _energy_epoch_from_row(updated)}

                if current is not None:
                    connection.execute(
                        "UPDATE charger_energy_epochs SET closed_at = ? WHERE id = ?",
                        (normalized_received_at, current["id"]),
                    )
                new_epoch = {
                    **normalized_identity,
                    "scope": normalized_scope,
                    "first_seen_at": normalized_received_at,
                    "last_seen_at": normalized_received_at,
                    "closed_at": None,
                }
                new_epoch["id"] = energy_epoch_id(
                    normalized_scope,
                    new_epoch["entity"],
                    new_epoch["loadpoint"],
                    new_epoch["contract_revision"],
                    normalized_received_at,
                )
                connection.execute(
                    """INSERT INTO charger_energy_epochs
                       (id, scope, entity, loadpoint, contract_revision,
                        first_seen_at, last_seen_at, closed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, NULL)""",
                    (
                        new_epoch["id"], new_epoch["scope"], new_epoch["entity"],
                        new_epoch["loadpoint"], new_epoch["contract_revision"],
                        new_epoch["first_seen_at"], new_epoch["last_seen_at"],
                    ),
                )
                self._enforce_energy_retention(connection, preserve_epoch_ids={new_epoch["id"]})
                saved = connection.execute(
                    "SELECT * FROM charger_energy_epochs WHERE id = ?", (new_epoch["id"],)
                ).fetchone()
                if saved is None:
                    raise ChargerEnergyError("La retención no puede descartar la época actual.")
                return {"changed": True, "epoch": _energy_epoch_from_row(saved)}
        except ChargerEnergyError as exc:
            raise _domain_error(str(exc)) from None
        except sqlite3.IntegrityError:
            raise _domain_error("No se pudo guardar la época de energía observada.") from None

    def end_charger_energy_epoch(self, scope: Any, *, received_at: Any) -> dict[str, Any]:
        """Close the current scope epoch at startup/restart without deleting it."""
        try:
            normalized_scope = validate_energy_scope(scope)
            normalized_received_at = _datetime(received_at, "received_at")
            with self._transaction() as connection:
                current = connection.execute(
                    "SELECT * FROM charger_energy_epochs "
                    "WHERE scope = ? AND closed_at IS NULL",
                    (normalized_scope,),
                ).fetchone()
                if current is None:
                    return {"changed": False, "epoch": None}
                close_at = max(normalized_received_at, current["last_seen_at"])
                connection.execute(
                    "UPDATE charger_energy_epochs SET closed_at = ? WHERE id = ?",
                    (close_at, current["id"]),
                )
                closed = connection.execute(
                    "SELECT * FROM charger_energy_epochs WHERE id = ?", (current["id"],)
                ).fetchone()
                return {"changed": True, "epoch": _energy_epoch_from_row(closed)}
        except ChargerEnergyError as exc:
            raise _domain_error(str(exc)) from None

    def end_all_charger_energy_epochs(self, *, received_at: Any) -> dict[str, bool]:
        """One configured reader restarts; every former binding becomes historical."""
        try:
            received = _datetime(received_at, "received_at")
            with self._transaction() as connection:
                result = connection.execute(
                    """UPDATE charger_energy_epochs
                       SET closed_at = MAX(last_seen_at, ?) WHERE closed_at IS NULL""",
                    (received,),
                )
                return {"changed": result.rowcount > 0}
        except ChargerEnergyError as error:
            raise _domain_error(str(error)) from None

    def ingest_charger_energy(
        self, scope: Any, epoch_id: Any, slots: Any
    ) -> dict[str, Any]:
        """Atomically insert or correct intervals for the currently open epoch."""
        try:
            normalized_scope = validate_energy_scope(scope)
            if not isinstance(epoch_id, str) or not _EPOCH_ID_RE.fullmatch(epoch_id):
                raise ChargerEnergyError("epoch_id debe ser hexadecimal de 32 caracteres.")
            if not isinstance(slots, list) or len(slots) > MAX_ENERGY_BATCH:
                raise ChargerEnergyError("slots debe ser una lista de hasta 100 elementos.")
            normalized_slots = _coalesce_slots(slots)
            with self._transaction() as connection:
                epoch = connection.execute(
                    "SELECT * FROM charger_energy_epochs WHERE id = ?", (epoch_id,)
                ).fetchone()
                current = connection.execute(
                    "SELECT id FROM charger_energy_epochs "
                    "WHERE scope = ? AND closed_at IS NULL",
                    (normalized_scope,),
                ).fetchone()
                if (
                    epoch is None
                    or epoch["scope"] != normalized_scope
                    or epoch["closed_at"] is not None
                    or current is None
                    or current["id"] != epoch_id
                ):
                    return {"changed": False, "saved": 0}

                first_seen = _parse_utc(epoch["first_seen_at"])
                last_seen = _parse_utc(epoch["last_seen_at"])
                for slot in normalized_slots:
                    if slot["epoch_id"] != epoch_id:
                        raise ChargerEnergyError(
                            "Todos los intervalos deben pertenecer a epoch_id."
                        )
                    if (
                        _parse_utc(slot["started_at"]) < first_seen
                        or _parse_utc(slot["ended_at"]) > last_seen
                        or _parse_utc(slot["received_at"]) < last_seen
                    ):
                        raise ChargerEnergyError(
                            "El intervalo queda fuera de los límites observados de su época."
                        )

                before_epochs, before_intervals = _energy_snapshot(connection)
                for slot in normalized_slots:
                    previous = connection.execute(
                        """SELECT * FROM charger_energy_intervals
                           WHERE epoch_id = ? AND started_at = ?""",
                        (epoch_id, slot["started_at"]),
                    ).fetchone()
                    if previous is not None:
                        if slot["received_at"] < previous["received_at"]:
                            continue
                        if slot["received_at"] == previous["received_at"]:
                            if (
                                float(previous["kwh"]) != slot["kwh"]
                                or float(previous["return_kwh"]) != slot["return_kwh"]
                                or previous["ended_at"] != slot["ended_at"]
                            ):
                                raise ChargerEnergyError(
                                    "Un intervalo no puede cambiar con el mismo received_at."
                                )
                            continue
                    connection.execute(
                        """INSERT INTO charger_energy_intervals
                           (epoch_id, started_at, ended_at, kwh, return_kwh, received_at)
                           VALUES (?, ?, ?, ?, ?, ?)
                           ON CONFLICT(epoch_id, started_at) DO UPDATE SET
                               ended_at = excluded.ended_at,
                               kwh = excluded.kwh,
                               return_kwh = excluded.return_kwh,
                               received_at = excluded.received_at""",
                        (
                            slot["epoch_id"], slot["started_at"], slot["ended_at"],
                            slot["kwh"], slot["return_kwh"], slot["received_at"],
                        ),
                    )
                self._enforce_energy_retention(connection, preserve_epoch_ids={epoch_id})
                after_epochs, after_intervals = _energy_snapshot(connection)
                changed = (before_epochs, before_intervals) != (after_epochs, after_intervals)
                after_by_key = {
                    (row[0], row[1]): row for row in after_intervals
                }
                before_by_key = {
                    (row[0], row[1]): row for row in before_intervals
                }
                saved = sum(
                    1
                    for slot in normalized_slots
                    if (row := after_by_key.get((epoch_id, slot["started_at"]))) is not None
                    and before_by_key.get((epoch_id, slot["started_at"])) != row
                )
                return {"changed": changed, "saved": saved}
        except ChargerEnergyError as exc:
            raise _domain_error(str(exc)) from None
        except sqlite3.IntegrityError:
            raise _domain_error("No se pudieron guardar los intervalos de energía.") from None

    def charger_energy_summary(self, scope: Any) -> dict[str, Any]:
        """Return candidate totals without assigning price or qualification."""
        try:
            normalized_scope = validate_energy_scope(scope)
        except ChargerEnergyError as exc:
            raise _domain_error(str(exc)) from None
        with self._read_transaction() as connection:
            row = connection.execute(
                """SELECT COUNT(*) AS retained_intervals,
                          COALESCE(SUM(i.kwh), 0) AS total_kwh,
                          MIN(i.started_at) AS first_started_at,
                          MAX(i.ended_at) AS last_ended_at,
                          MAX(i.received_at) AS last_received_at
                   FROM charger_energy_intervals i
                   JOIN charger_energy_epochs e ON e.id = i.epoch_id
                   WHERE e.scope = ?""",
                (normalized_scope,),
            ).fetchone()
            return {
                "retained_intervals": int(row["retained_intervals"]),
                "total_kwh": float(row["total_kwh"]),
                "first_started_at": row["first_started_at"],
                "last_ended_at": row["last_ended_at"],
                "last_received_at": row["last_received_at"],
                "retention_limit": MAX_ENERGY_SLOTS,
                "validation": "not_qualified",
            }

    def _export_charger_energy(
        self, connection: sqlite3.Connection
    ) -> dict[str, list[dict[str, Any]]]:
        epochs = [
            _energy_epoch_from_row(row)
            for row in connection.execute(
                "SELECT * FROM charger_energy_epochs ORDER BY id ASC"
            ).fetchall()
        ]
        intervals = [
            _energy_slot_from_row(row)
            for row in connection.execute(
                """SELECT * FROM charger_energy_intervals
                   ORDER BY ended_at DESC, epoch_id ASC, started_at DESC"""
            ).fetchall()
        ]
        return validate_charger_energy_archive({"epochs": epochs, "intervals": intervals})

    def _replace_charger_energy_archive(
        self, connection: sqlite3.Connection, value: Any
    ) -> None:
        archive = validate_charger_energy_archive(value)
        validate_charger_energy_future_dates(archive, self._clock_now())
        now = _datetime(self._now(), "now")
        connection.execute("DELETE FROM charger_energy_epochs")
        for epoch in archive["epochs"]:
            closed_at = epoch["closed_at"]
            if closed_at is None:
                closed_at = max(now, epoch["last_seen_at"])
            connection.execute(
                """INSERT INTO charger_energy_epochs
                   (id, scope, entity, loadpoint, contract_revision,
                    first_seen_at, last_seen_at, closed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    epoch["id"], epoch["scope"], epoch["entity"], epoch["loadpoint"],
                    epoch["contract_revision"], epoch["first_seen_at"],
                    epoch["last_seen_at"], closed_at,
                ),
            )
        for slot in reversed(archive["intervals"]):
            connection.execute(
                """INSERT INTO charger_energy_intervals
                   (epoch_id, started_at, ended_at, kwh, return_kwh, received_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    slot["epoch_id"], slot["started_at"], slot["ended_at"], slot["kwh"],
                    slot["return_kwh"], slot["received_at"],
                ),
            )

    def _enforce_energy_retention(
        self,
        connection: sqlite3.Connection,
        *,
        preserve_epoch_ids: set[str] | None = None,
    ) -> None:
        """Apply global slot/epoch/scope caps while preserving every open epoch."""
        preserve = preserve_epoch_ids or set()
        connection.execute(
            """DELETE FROM charger_energy_intervals WHERE rowid IN (
                   SELECT rowid FROM charger_energy_intervals
                   ORDER BY ended_at DESC, epoch_id DESC, started_at DESC
                   LIMIT -1 OFFSET ?
               )""",
            (MAX_ENERGY_SLOTS,),
        )

        epoch_count = int(connection.execute(
            "SELECT COUNT(*) FROM charger_energy_epochs"
        ).fetchone()[0])
        excess_epochs = max(0, epoch_count - MAX_ENERGY_EPOCHS)
        if excess_epochs:
            candidates = connection.execute(
                """SELECT id FROM charger_energy_epochs WHERE closed_at IS NOT NULL
                   ORDER BY last_seen_at ASC, id ASC"""
            ).fetchall()
            removable = [row["id"] for row in candidates if row["id"] not in preserve]
            if len(removable) < excess_epochs:
                raise ChargerEnergyError(
                    "No se puede respetar el límite de 64 épocas sin descartar una época abierta."
                )
            connection.executemany(
                "DELETE FROM charger_energy_epochs WHERE id = ?",
                ((item_id,) for item_id in removable[:excess_epochs]),
            )

        scopes = connection.execute(
            """SELECT scope, MAX(last_seen_at) AS last_seen_at,
                      MAX(CASE WHEN closed_at IS NULL THEN 1 ELSE 0 END) AS has_open
               FROM charger_energy_epochs GROUP BY scope
               ORDER BY last_seen_at ASC, scope ASC"""
        ).fetchall()
        excess_scopes = max(0, len(scopes) - MAX_ENERGY_SCOPES)
        if excess_scopes:
            removable_scopes = [row["scope"] for row in scopes if not row["has_open"]]
            if len(removable_scopes) < excess_scopes:
                raise ChargerEnergyError(
                    "No se puede respetar el límite de 16 scopes sin descartar un scope abierto."
                )
            connection.executemany(
                "DELETE FROM charger_energy_epochs WHERE scope = ?",
                ((scope,) for scope in removable_scopes[:excess_scopes]),
            )


def _validate_epoch(value: Any) -> dict[str, Any]:
    raw = _exact_mapping(value, _EPOCH_FIELDS, "epoch")
    scope = validate_energy_scope(raw["scope"])
    entity = _entity(raw["entity"])
    loadpoint = _loadpoint(raw["loadpoint"])
    revision = _contract_revision(raw["contract_revision"])
    first_seen_at = _datetime(raw["first_seen_at"], "first_seen_at")
    last_seen_at = _datetime(raw["last_seen_at"], "last_seen_at")
    closed_at = None if raw["closed_at"] is None else _datetime(raw["closed_at"], "closed_at")
    if _parse_utc(last_seen_at) < _parse_utc(first_seen_at):
        raise ChargerEnergyError("last_seen_at no puede preceder a first_seen_at.")
    if closed_at is not None and _parse_utc(closed_at) < _parse_utc(last_seen_at):
        raise ChargerEnergyError("closed_at no puede preceder a last_seen_at.")
    expected_id = energy_epoch_id(scope, entity, loadpoint, revision, first_seen_at)
    if raw["id"] != expected_id:
        raise ChargerEnergyError("id no coincide con la identidad canónica de la época.")
    return {
        "id": expected_id,
        "scope": scope,
        "entity": entity,
        "loadpoint": loadpoint,
        "contract_revision": revision,
        "first_seen_at": first_seen_at,
        "last_seen_at": last_seen_at,
        "closed_at": closed_at,
    }


def _validate_identity(value: Any) -> dict[str, str]:
    raw = _exact_mapping(
        value, frozenset({"entity", "loadpoint", "contract_revision"}), "identity"
    )
    return {
        "entity": _entity(raw["entity"]),
        "loadpoint": _loadpoint(raw["loadpoint"]),
        "contract_revision": _contract_revision(raw["contract_revision"]),
    }


def _same_epoch_identity(row: sqlite3.Row, identity: Mapping[str, Any]) -> bool:
    return all(row[field] == identity[field] for field in (
        "entity", "loadpoint", "contract_revision"
    ))


def _coalesce_slots(values: list[Any]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in values:
        slot = validate_energy_slot(raw)
        key = (slot["epoch_id"], slot["started_at"])
        previous = by_key.get(key)
        if previous is None:
            by_key[key] = slot
            continue
        if slot["received_at"] == previous["received_at"]:
            if slot != previous:
                raise ChargerEnergyError(
                    "Un intervalo no puede aparecer con datos distintos al mismo received_at."
                )
            continue
        if _parse_utc(slot["received_at"]) > _parse_utc(previous["received_at"]):
            by_key[key] = slot
    return sorted(by_key.values(), key=lambda item: (item["started_at"], item["epoch_id"]))


def _exact_mapping(value: Any, fields: frozenset[str], name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ChargerEnergyError(f"{name} contiene campos no admitidos o incompletos.")
    return dict(value)


def _entity(value: Any) -> str:
    if not isinstance(value, str) or not _ENTITY_RE.fullmatch(value):
        raise ChargerEnergyError("entity debe ser un identificador ASCII de hasta 80 caracteres.")
    return value


def _loadpoint(value: Any) -> str:
    if not isinstance(value, str):
        raise ChargerEnergyError("loadpoint debe ser texto.")
    result = value.strip()
    if not 1 <= len(result) <= MAX_ENERGY_LOADPOINT_LENGTH:
        raise ChargerEnergyError("loadpoint debe contener entre 1 y 80 caracteres.")
    if any(ord(character) < 32 or ord(character) == 127 for character in result):
        raise ChargerEnergyError("loadpoint contiene caracteres de control.")
    try:
        result.encode("utf-8")
    except UnicodeEncodeError:
        raise ChargerEnergyError("loadpoint contiene texto no válido.") from None
    return result


def _contract_revision(value: Any) -> str:
    if not isinstance(value, str) or value not in _KNOWN_CONTRACT_REVISIONS:
        raise ChargerEnergyError("contract_revision no corresponde a un protocolo conocido.")
    return value


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ChargerEnergyError(f"{name} debe ser numérico.")
    if value < 0 or value > 500:
        raise ChargerEnergyError(f"{name} está fuera del rango 0–500 kWh.")
    if isinstance(value, float) and not math.isfinite(value):
        raise ChargerEnergyError(f"{name} debe ser finito.")
    try:
        normalized = float(value)
    except (OverflowError, ValueError):
        raise ChargerEnergyError(f"{name} debe ser numérico.") from None
    if not math.isfinite(normalized):
        raise ChargerEnergyError(f"{name} debe ser finito.")
    return normalized


def _datetime(value: Any, name: str) -> str:
    if not isinstance(value, str) or len(value) > 64:
        raise ChargerEnergyError(f"{name} debe ser una fecha ISO con zona horaria.")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        raise ChargerEnergyError(f"{name} debe ser una fecha ISO con zona horaria.") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ChargerEnergyError(f"{name} debe incluir una zona horaria.")
    return _format_utc(parsed)


def _datetime_from_datetime(value: Any, name: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ChargerEnergyError(f"{name} debe ser una fecha con zona horaria.")
    return _format_utc(value)


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _format_utc(value: datetime) -> str:
    try:
        normalized = value.astimezone(timezone.utc)
        if normalized.year < _MIN_SLOT_YEAR:
            raise ValueError
        return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")
    except (ValueError, OverflowError):
        raise ChargerEnergyError("La fecha UTC queda fuera del intervalo permitido.") from None


def _energy_epoch_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {field: row[field] for field in (
        "id", "scope", "entity", "loadpoint", "contract_revision",
        "first_seen_at", "last_seen_at", "closed_at",
    )}


def _energy_slot_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "epoch_id": row["epoch_id"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "kwh": float(row["kwh"]),
        "return_kwh": float(row["return_kwh"]),
        "received_at": row["received_at"],
    }


def _energy_snapshot(
    connection: sqlite3.Connection,
) -> tuple[tuple[tuple[Any, ...], ...], tuple[tuple[Any, ...], ...]]:
    epochs = tuple(tuple(row) for row in connection.execute(
        "SELECT * FROM charger_energy_epochs ORDER BY id"
    ).fetchall())
    intervals = tuple(tuple(row) for row in connection.execute(
        "SELECT * FROM charger_energy_intervals ORDER BY epoch_id, started_at"
    ).fetchall())
    return epochs, intervals


def _domain_error(message: str) -> Exception:
    # Runtime import avoids domain -> charger_energy_store -> domain at load time.
    from pulse.domain import DomainError

    return DomainError(message)
