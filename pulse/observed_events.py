"""Observed lock and charging changes from admissible B10 read snapshots.

The detector is deliberately independent from ``pulse.domain`` so the store
can compose this mixin without introducing an import cycle.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import sqlite3
from typing import Any


OBSERVED_EVENT_SOURCE = "MATE-API B10ReadClient"
MAX_OBSERVED_EVENTS = 1_000
MAX_OBSERVED_EVENTS_PAGE_SIZE = 100
_MAX_SNAPSHOT_AGE = timedelta(seconds=300)
_MAX_OBSERVATION_GAP = timedelta(seconds=600)
_SNAPSHOT_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
_EVENT_ID_RE = re.compile(r"[0-9a-f]{64}\Z")
_EVENT_FIELDS = frozenset({
    "id", "field", "source", "before", "after", "previous_observed_at",
    "observed_at", "previous_received_at", "received_at",
    "previous_snapshot_id", "snapshot_id",
})
_STATE_FIELDS = ("locked", "charging")
_EVENT_TIMESTAMPS = (
    "previous_observed_at", "observed_at", "previous_received_at", "received_at",
)


class ObservedEventError(ValueError):
    """An observed-event payload or operation violates its storage contract."""


def select_changes(
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any],
    demo: bool = False,
) -> tuple[list[dict[str, Any]], str | None]:
    """Compare two stored snapshots and return changes plus the next baseline ID.

    Only the ``locked`` and ``charging`` booleans are considered. Invalid or
    stale snapshots clear the baseline; duplicate and older observations keep
    the last accepted baseline, even when their state values differ.
    """
    if type(demo) is not bool:
        raise ObservedEventError("demo debe ser booleano.")
    if demo:
        return [], None

    current_info = _snapshot_info(current)
    if current_info is None:
        return [], None
    if previous is None:
        return [], current_info["id"]

    previous_info = _snapshot_info(previous)
    if previous_info is None:
        # A valid current read can safely seed a new comparison window after
        # the old reference becomes unusable; it must not be discarded too.
        return [], current_info["id"]

    if current_info["id"] == previous_info["id"]:
        return [], previous_info["id"]

    # An equal or older observation cannot move the comparison cursor, even
    # if the provider returned different state values for that same instant.
    if current_info["observed"] <= previous_info["observed"]:
        return [], previous_info["id"]

    # A server clock correction starts a fresh comparison window.
    if current_info["received"] < previous_info["received"]:
        return [], current_info["id"]

    gap = current_info["observed"] - previous_info["observed"]
    if gap > _MAX_OBSERVATION_GAP:
        return [], current_info["id"]

    events: list[dict[str, Any]] = []
    for field in _STATE_FIELDS:
        before = previous_info["vehicle"].get(field)
        after = current_info["vehicle"].get(field)
        if type(before) is not bool or type(after) is not bool or before == after:
            continue
        event = {
            "field": field,
            "source": OBSERVED_EVENT_SOURCE,
            "before": before,
            "after": after,
            "previous_observed_at": previous_info["observed_text"],
            "observed_at": current_info["observed_text"],
            "previous_received_at": previous_info["received_text"],
            "received_at": current_info["received_text"],
            "previous_snapshot_id": previous_info["id"],
            "snapshot_id": current_info["id"],
        }
        event["id"] = _event_id(event)
        events.append(event)
    return events, current_info["id"]


def validate_observed_events_archive(
    value: Any, snapshots: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Validate an imported event collection and cross-check retained anchors."""
    if not isinstance(value, list) or len(value) > MAX_OBSERVED_EVENTS:
        raise ObservedEventError("observed_events debe ser una lista de hasta 1000 eventos.")
    anchors = _snapshot_anchors(snapshots)
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, Mapping) or set(raw) != _EVENT_FIELDS:
            raise ObservedEventError("Cada evento debe contener exactamente los campos permitidos.")
        event_id = raw["id"]
        if not isinstance(event_id, str) or not _EVENT_ID_RE.fullmatch(event_id):
            raise ObservedEventError("id debe ser SHA-256 hexadecimal en minúsculas.")
        if event_id in seen:
            raise ObservedEventError("observed_events contiene ids duplicados.")
        seen.add(event_id)

        field = raw["field"]
        if field not in _STATE_FIELDS:
            raise ObservedEventError("field debe ser locked o charging.")
        if raw["source"] != OBSERVED_EVENT_SOURCE:
            raise ObservedEventError("source no corresponde al lector B10 admitido.")
        before, after = raw["before"], raw["after"]
        if type(before) is not bool or type(after) is not bool or before == after:
            raise ObservedEventError("before y after deben ser booleanos distintos.")

        previous_id = raw["previous_snapshot_id"]
        snapshot_id = raw["snapshot_id"]
        if (
            not isinstance(previous_id, str)
            or not _SNAPSHOT_ID_RE.fullmatch(previous_id)
            or not isinstance(snapshot_id, str)
            or not _SNAPSHOT_ID_RE.fullmatch(snapshot_id)
            or previous_id == snapshot_id
        ):
            raise ObservedEventError("Los ids de snapshot deben ser hexadecimales y distintos.")

        parsed: dict[str, datetime] = {}
        normalized: dict[str, str] = {}
        for name in _EVENT_TIMESTAMPS:
            parsed[name], normalized[name] = _parse_timestamp(
                raw[name], name, allow_datetime=False
            )
        previous_observed = parsed["previous_observed_at"]
        observed = parsed["observed_at"]
        previous_received = parsed["previous_received_at"]
        received = parsed["received_at"]
        gap = observed - previous_observed
        if gap <= timedelta(0) or gap > _MAX_OBSERVATION_GAP:
            raise ObservedEventError("Las observaciones deben avanzar como máximo 600 segundos.")
        if previous_received < previous_observed or received < observed:
            raise ObservedEventError("Una observación no puede ser posterior a su recepción.")
        if previous_received > received:
            raise ObservedEventError("Las recepciones no pueden retroceder.")
        if (
            previous_received - previous_observed > _MAX_SNAPSHOT_AGE
            or received - observed > _MAX_SNAPSHOT_AGE
        ):
            raise ObservedEventError("La edad de cada observación no puede superar 300 segundos.")

        event = {
            "id": event_id,
            "field": field,
            "source": OBSERVED_EVENT_SOURCE,
            "before": before,
            "after": after,
            "previous_observed_at": normalized["previous_observed_at"],
            "observed_at": normalized["observed_at"],
            "previous_received_at": normalized["previous_received_at"],
            "received_at": normalized["received_at"],
            "previous_snapshot_id": previous_id,
            "snapshot_id": snapshot_id,
        }
        if _event_id(event) != event_id:
            raise ObservedEventError("id no coincide con el hash del cambio lógico.")
        _crosscheck_anchor(event, anchors.get(previous_id), previous=True)
        _crosscheck_anchor(event, anchors.get(snapshot_id), previous=False)
        result.append(event)
    return result


def validate_observed_event_future_dates(
    events: list[dict[str, Any]], now: datetime
) -> None:
    """Reject archived event timestamps later than the supplied aware clock."""
    now_value, _ = _parse_timestamp(now, "now")
    if not isinstance(events, list):
        raise ObservedEventError("observed_events debe ser una lista.")
    for event in events:
        if not isinstance(event, Mapping):
            raise ObservedEventError("Cada evento debe ser un objeto.")
        for name in _EVENT_TIMESTAMPS:
            parsed, _ = _parse_timestamp(event.get(name), name, allow_datetime=False)
            if parsed > now_value:
                raise ObservedEventError(f"{name} no puede estar en el futuro.")


class ObservedEventsStoreMixin:
    """SQLite persistence for a bounded, immutable observed-event history."""

    def _migrate_v9_to_v10(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """CREATE TABLE observed_events (
                id TEXT PRIMARY KEY CHECK (
                    length(id) = 64 AND id NOT GLOB '*[^0-9a-f]*'
                ),
                field TEXT NOT NULL CHECK (field IN ('locked', 'charging')),
                source TEXT NOT NULL CHECK (source = 'MATE-API B10ReadClient'),
                before INTEGER NOT NULL CHECK (before IN (0, 1)),
                after INTEGER NOT NULL CHECK (after IN (0, 1)),
                previous_observed_at TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                previous_received_at TEXT NOT NULL,
                received_at TEXT NOT NULL,
                previous_snapshot_id TEXT NOT NULL CHECK (
                    length(previous_snapshot_id) = 32
                    AND previous_snapshot_id NOT GLOB '*[^0-9a-f]*'
                ),
                snapshot_id TEXT NOT NULL CHECK (
                    length(snapshot_id) = 32 AND snapshot_id NOT GLOB '*[^0-9a-f]*'
                ),
                CHECK (before <> after),
                CHECK (previous_observed_at < observed_at),
                CHECK (previous_observed_at <= previous_received_at),
                CHECK (observed_at <= received_at),
                CHECK (previous_received_at <= received_at)
            )"""
        )

    def _append_observed_events(
        self, connection: sqlite3.Connection, events: list[dict[str, Any]]
    ) -> None:
        validated = validate_observed_events_archive(events, [])
        for event in validated:
            connection.execute(
                """INSERT OR IGNORE INTO observed_events (
                       id, field, source, before, after, previous_observed_at,
                       observed_at, previous_received_at, received_at,
                       previous_snapshot_id, snapshot_id
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event["id"], event["field"], event["source"],
                    int(event["before"]), int(event["after"]),
                    event["previous_observed_at"], event["observed_at"],
                    event["previous_received_at"], event["received_at"],
                    event["previous_snapshot_id"], event["snapshot_id"],
                ),
            )
        connection.execute(
            """DELETE FROM observed_events WHERE id NOT IN (
                   SELECT id FROM observed_events ORDER BY rowid DESC LIMIT ?
               )""",
            (MAX_OBSERVED_EVENTS,),
        )

    def observed_events(self, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        page_limit = _bounded_int(limit, "limit", 1, MAX_OBSERVED_EVENTS_PAGE_SIZE)
        page_offset = _bounded_int(offset, "offset", 0, 2_147_483_647)
        with self._read_transaction() as connection:
            total = int(connection.execute(
                "SELECT COUNT(*) FROM observed_events"
            ).fetchone()[0])
            rows = connection.execute(
                """SELECT * FROM observed_events
                   ORDER BY rowid DESC LIMIT ? OFFSET ?""",
                (page_limit, page_offset),
            ).fetchall()
            return {
                "items": [_event_from_row(row) for row in rows],
                "total": total,
                "limit": page_limit,
                "offset": page_offset,
            }

    def _export_observed_events(
        self, connection: sqlite3.Connection
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT * FROM observed_events ORDER BY rowid DESC"
        ).fetchall()
        if len(rows) > MAX_OBSERVED_EVENTS:
            raise ObservedEventError("observed_events supera el límite de 1000 eventos.")
        return validate_observed_events_archive(
            [_event_from_row(row) for row in rows], []
        )

    def _replace_observed_events(
        self, connection: sqlite3.Connection, events: list[dict[str, Any]]
    ) -> None:
        validated = validate_observed_events_archive(events, [])
        connection.execute("DELETE FROM observed_events")
        for event in reversed(validated):
            connection.execute(
                """INSERT INTO observed_events (
                       id, field, source, before, after, previous_observed_at,
                       observed_at, previous_received_at, received_at,
                       previous_snapshot_id, snapshot_id
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event["id"], event["field"], event["source"],
                    int(event["before"]), int(event["after"]),
                    event["previous_observed_at"], event["observed_at"],
                    event["previous_received_at"], event["received_at"],
                    event["previous_snapshot_id"], event["snapshot_id"],
                ),
            )


def _snapshot_info(snapshot: Any) -> dict[str, Any] | None:
    if not isinstance(snapshot, Mapping) or snapshot.get("source") != OBSERVED_EVENT_SOURCE:
        return None
    snapshot_id = snapshot.get("id")
    if not isinstance(snapshot_id, str) or not _SNAPSHOT_ID_RE.fullmatch(snapshot_id):
        return None
    try:
        observed, observed_text = _parse_timestamp(snapshot.get("observed_at"), "observed_at")
        received, received_text = _parse_timestamp(
            snapshot.get("server_received_at"), "server_received_at"
        )
    except ObservedEventError:
        return None
    if observed > received or received - observed > _MAX_SNAPSHOT_AGE:
        return None
    vehicle = snapshot.get("vehicle", {})
    if vehicle is None:
        vehicle = {}
    if not isinstance(vehicle, Mapping):
        return None
    return {
        "id": snapshot_id,
        "observed": observed,
        "observed_text": observed_text,
        "received": received,
        "received_text": received_text,
        "vehicle": vehicle,
    }


def _parse_timestamp(
    value: Any, name: str, *, allow_datetime: bool = True
) -> tuple[datetime, str]:
    if isinstance(value, datetime) and allow_datetime:
        parsed = value
    elif isinstance(value, str) and len(value) <= 64:
        try:
            parsed = datetime.fromisoformat(
                value[:-1] + "+00:00" if value.endswith("Z") else value
            )
        except ValueError:
            raise ObservedEventError(f"{name} debe ser una fecha ISO con zona horaria.") from None
    else:
        raise ObservedEventError(f"{name} debe ser una fecha ISO con zona horaria.")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ObservedEventError(f"{name} debe incluir una zona horaria.")
    utc_value = parsed.astimezone(timezone.utc)
    normalized = utc_value.isoformat(timespec="microseconds").replace("+00:00", "Z")
    return utc_value, normalized


def _event_id(event: Mapping[str, Any]) -> str:
    logical_change = [
        OBSERVED_EVENT_SOURCE,
        event["field"],
        event["previous_observed_at"],
        event["observed_at"],
        event["before"],
        event["after"],
    ]
    canonical = json.dumps(
        logical_change,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _snapshot_anchors(snapshots: Any) -> dict[str, Mapping[str, Any]]:
    if not isinstance(snapshots, list):
        raise ObservedEventError("telemetry_snapshots debe ser una lista.")
    anchors: dict[str, Mapping[str, Any]] = {}
    for snapshot in snapshots:
        if not isinstance(snapshot, Mapping):
            raise ObservedEventError("Cada snapshot debe ser un objeto.")
        snapshot_id = snapshot.get("id")
        if not isinstance(snapshot_id, str) or not _SNAPSHOT_ID_RE.fullmatch(snapshot_id):
            raise ObservedEventError("Un snapshot de anclaje tiene un id no válido.")
        if snapshot_id in anchors:
            raise ObservedEventError("telemetry_snapshots contiene ids duplicados.")
        anchors[snapshot_id] = snapshot
    return anchors


def _crosscheck_anchor(
    event: Mapping[str, Any], snapshot: Mapping[str, Any] | None, *, previous: bool
) -> None:
    if snapshot is None:
        return
    side = "previous" if previous else "current"
    observed_name = "previous_observed_at" if previous else "observed_at"
    received_name = "previous_received_at" if previous else "received_at"
    state_name = "before" if previous else "after"
    try:
        observed, _ = _parse_timestamp(snapshot.get("observed_at"), "observed_at")
        received, _ = _parse_timestamp(snapshot.get("server_received_at"), "server_received_at")
    except ObservedEventError as exc:
        raise ObservedEventError(f"El anclaje {side} contiene fechas no válidas: {exc}") from None
    if snapshot.get("source") != OBSERVED_EVENT_SOURCE:
        raise ObservedEventError(f"El anclaje {side} no procede del lector B10 admitido.")
    event_observed, _ = _parse_timestamp(event[observed_name], observed_name, allow_datetime=False)
    event_received, _ = _parse_timestamp(event[received_name], received_name, allow_datetime=False)
    vehicle = snapshot.get("vehicle")
    if not isinstance(vehicle, Mapping) or type(vehicle.get(event["field"])) is not bool:
        raise ObservedEventError(f"El anclaje {side} no contiene el booleano estricto del evento.")
    if (
        observed != event_observed
        or received != event_received
        or vehicle[event["field"]] is not event[state_name]
    ):
        raise ObservedEventError(f"El evento no coincide con su anclaje {side}.")


def _event_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "field": row["field"],
        "source": row["source"],
        "before": bool(row["before"]),
        "after": bool(row["after"]),
        "previous_observed_at": row["previous_observed_at"],
        "observed_at": row["observed_at"],
        "previous_received_at": row["previous_received_at"],
        "received_at": row["received_at"],
        "previous_snapshot_id": row["previous_snapshot_id"],
        "snapshot_id": row["snapshot_id"],
    }


def _bounded_int(value: Any, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ObservedEventError(f"{name} está fuera del rango permitido.")
    return value
