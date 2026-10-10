"""Normalized, bounded storage for one operator-selected evcc loadpoint.

This module deliberately has no import-time dependency on ``pulse.domain``;
the mixin is composed into Store there, while validation helpers stay usable by
the export/import validator without creating an import cycle.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re
import sqlite3
from typing import Any, Mapping


MAX_CHARGER_SCOPES = 16
MAX_CHARGER_SESSIONS = 1_000
MAX_CHARGER_BATCH = 1_000
MAX_CHARGER_LOADPOINT_LENGTH = 80
MAX_CHARGER_ACTIVE_SECONDS = 31 * 24 * 60 * 60
MAX_CHARGER_UPSTREAM_ID = 2**53 - 1
MAX_CHARGER_PAGE_SIZE = 100
FUTURE_TOLERANCE_SECONDS = 60

_SCOPE_RE = re.compile(r"[0-9a-f]{64}\Z")
_SESSION_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
_OBSERVATION_FIELDS = frozenset({
    "received_at", "loadpoint", "connected", "charging", "power_w",
    "charged_kwh", "active_seconds",
})
_SESSION_FIELDS = frozenset({
    "id", "upstream_id", "loadpoint", "started_at", "ended_at", "kwh",
    "active_seconds", "received_at",
})
_ARCHIVE_SCOPE_FIELDS = frozenset({"scope", "observation", "sessions"})


class ChargerDataError(ValueError):
    """A normalized charger payload failed its strict storage contract."""


def validate_scope(value: Any) -> str:
    if not isinstance(value, str) or not _SCOPE_RE.fullmatch(value):
        raise ChargerDataError("scope debe ser un SHA-256 hexadecimal.")
    return value


def session_id(
    scope: str, upstream_id: int, started_at: datetime | str, loadpoint: str
) -> str:
    """Return evcc's stable opaque ID for one scope and closed session identity."""
    normalized_scope = validate_scope(scope)
    if type(upstream_id) is not int or not 1 <= upstream_id <= MAX_CHARGER_UPSTREAM_ID:
        raise ChargerDataError("upstream_id debe ser un entero positivo seguro para JSON.")
    normalized_loadpoint = _loadpoint(loadpoint)
    if isinstance(started_at, datetime):
        parsed = started_at
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ChargerDataError("started_at debe incluir una zona horaria.")
        parsed = parsed.astimezone(timezone.utc)
    elif isinstance(started_at, str):
        if len(started_at) > 64:
            raise ChargerDataError("started_at debe ser una fecha ISO con zona horaria.")
        try:
            parsed = datetime.fromisoformat(
                started_at[:-1] + "+00:00" if started_at.endswith("Z") else started_at
            )
        except ValueError:
            raise ChargerDataError("started_at debe ser una fecha ISO con zona horaria.") from None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ChargerDataError("started_at debe incluir una zona horaria.")
        parsed = parsed.astimezone(timezone.utc)
    else:
        raise ChargerDataError("started_at debe ser una fecha ISO con zona horaria.")
    # isoformat's auto precision matches the evcc reader: omit fractional
    # seconds when zero, otherwise retain the parsed fractional precision.
    normalized_start = parsed.isoformat().replace("+00:00", "Z")
    canonical = json.dumps(
        [normalized_scope, upstream_id, normalized_start, normalized_loadpoint],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def validate_observation(value: Any) -> dict[str, Any]:
    raw = _exact_mapping(value, _OBSERVATION_FIELDS, "observation")
    connected = _boolean(raw["connected"], "connected")
    charging = _boolean(raw["charging"], "charging")
    if charging and not connected:
        raise ChargerDataError("charging requiere connected.")
    result = {
        "received_at": _datetime(raw["received_at"], "received_at"),
        "loadpoint": _loadpoint(raw["loadpoint"]),
        "connected": connected,
        "charging": charging,
        "power_w": _nullable_number(raw["power_w"], "power_w", maximum=1_000_000),
        "charged_kwh": _nullable_number(raw["charged_kwh"], "charged_kwh", maximum=500),
        "active_seconds": _nullable_number(
            raw["active_seconds"], "active_seconds", maximum=MAX_CHARGER_ACTIVE_SECONDS
        ),
    }
    return result


def validate_session(value: Any, scope: str) -> dict[str, Any]:
    raw = _exact_mapping(value, _SESSION_FIELDS, "session")
    item_id = raw["id"]
    if not isinstance(item_id, str) or not _SESSION_ID_RE.fullmatch(item_id):
        raise ChargerDataError("id de sesión debe ser hexadecimal de 32 caracteres.")
    upstream_id = raw["upstream_id"]
    if type(upstream_id) is not int or not 1 <= upstream_id <= MAX_CHARGER_UPSTREAM_ID:
        raise ChargerDataError("upstream_id debe ser un entero positivo.")
    loadpoint = _loadpoint(raw["loadpoint"])
    started_at = _datetime(raw["started_at"], "started_at")
    ended_at = _datetime(raw["ended_at"], "ended_at")
    started = _parse_utc(started_at)
    ended = _parse_utc(ended_at)
    if ended <= started:
        raise ChargerDataError("ended_at debe ser posterior a started_at.")
    duration_seconds = (ended - started).total_seconds()
    if duration_seconds > MAX_CHARGER_ACTIVE_SECONDS:
        raise ChargerDataError("La sesión no puede superar los 31 días.")
    active_seconds = _nullable_number(
        raw["active_seconds"], "active_seconds", maximum=duration_seconds
    )
    if item_id != session_id(scope, upstream_id, started_at, loadpoint):
        raise ChargerDataError("id no coincide con la identidad canónica de la sesión.")
    return {
        "id": item_id,
        "upstream_id": upstream_id,
        "loadpoint": loadpoint,
        "started_at": started_at,
        "ended_at": ended_at,
        "kwh": _number(raw["kwh"], "kwh", maximum=500),
        "active_seconds": active_seconds,
        "received_at": _datetime(raw["received_at"], "received_at"),
    }


def validate_charger_archive(value: Any) -> list[dict[str, Any]]:
    """Validate the schema-6 charger export block before any database write."""
    if not isinstance(value, list) or len(value) > MAX_CHARGER_SCOPES:
        raise ChargerDataError("charger debe contener como máximo 16 scopes.")
    result: list[dict[str, Any]] = []
    seen_scopes: set[str] = set()
    seen_sessions: set[str] = set()
    total_sessions = 0
    for raw_scope in value:
        scope_row = _exact_mapping(raw_scope, _ARCHIVE_SCOPE_FIELDS, "charger scope")
        scope = validate_scope(scope_row["scope"])
        if scope in seen_scopes:
            raise ChargerDataError("charger contiene scopes duplicados.")
        seen_scopes.add(scope)
        observation_raw = scope_row["observation"]
        observation = None if observation_raw is None else validate_observation(observation_raw)
        raw_sessions = scope_row["sessions"]
        if not isinstance(raw_sessions, list):
            raise ChargerDataError("sessions debe ser una lista.")
        total_sessions += len(raw_sessions)
        if total_sessions > MAX_CHARGER_SESSIONS:
            raise ChargerDataError("charger supera los 1000 registros de sesión.")
        sessions: list[dict[str, Any]] = []
        for raw_session in raw_sessions:
            session = validate_session(raw_session, scope)
            if session["id"] in seen_sessions:
                raise ChargerDataError("charger contiene identificadores de sesión duplicados.")
            seen_sessions.add(session["id"])
            sessions.append(session)
        if observation is None and not sessions:
            raise ChargerDataError("Cada scope exportado debe contener datos.")
        result.append({"scope": scope, "observation": observation, "sessions": sessions})
    return result


def validate_charger_future_dates(
    scopes: list[dict[str, Any]], now: datetime, *, tolerance_seconds: int = FUTURE_TOLERANCE_SECONDS
) -> None:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ChargerDataError("El reloj de Store debe devolver una fecha con zona horaria.")
    limit = now.astimezone(timezone.utc) + timedelta(seconds=tolerance_seconds)
    for scope_row in scopes:
        observation = scope_row["observation"]
        if observation is not None and _parse_utc(observation["received_at"]) > limit:
            raise ChargerDataError("received_at no puede superar en más de 60 segundos el reloj de Store.")
        for session in scope_row["sessions"]:
            for field in ("started_at", "ended_at", "received_at"):
                if _parse_utc(session[field]) > limit:
                    raise ChargerDataError(
                        f"{field} no puede superar en más de 60 segundos el reloj de Store."
                    )


class ChargerStoreMixin:
    """Store methods for bounded evcc observations and closed sessions."""

    def _migrate_v6_to_v7(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """CREATE TABLE charger_observations (
                scope TEXT PRIMARY KEY CHECK (
                    length(scope) = 64 AND scope NOT GLOB '*[^0-9a-f]*'
                ),
                received_at TEXT NOT NULL,
                loadpoint TEXT NOT NULL CHECK (length(loadpoint) BETWEEN 1 AND 80),
                connected INTEGER NOT NULL CHECK (connected IN (0, 1)),
                charging INTEGER NOT NULL CHECK (charging IN (0, 1)),
                power_w REAL CHECK (power_w IS NULL OR (power_w >= 0 AND power_w <= 1000000)),
                charged_kwh REAL CHECK (
                    charged_kwh IS NULL OR (charged_kwh >= 0 AND charged_kwh <= 500)
                ),
                active_seconds REAL CHECK (
                    active_seconds IS NULL OR active_seconds BETWEEN 0 AND 2678400
                ),
                CHECK (charging = 0 OR connected = 1)
            )"""
        )
        connection.execute(
            """CREATE TABLE charger_sessions (
                id TEXT PRIMARY KEY CHECK (
                    length(id) = 32 AND id NOT GLOB '*[^0-9a-f]*'
                ),
                scope TEXT NOT NULL CHECK (
                    length(scope) = 64 AND scope NOT GLOB '*[^0-9a-f]*'
                ),
                upstream_id INTEGER NOT NULL CHECK (
                    upstream_id BETWEEN 1 AND 9007199254740991
                ),
                loadpoint TEXT NOT NULL CHECK (length(loadpoint) BETWEEN 1 AND 80),
                started_at TEXT NOT NULL,
                ended_at TEXT NOT NULL CHECK (ended_at > started_at),
                kwh REAL NOT NULL CHECK (kwh >= 0 AND kwh <= 500),
                active_seconds REAL CHECK (
                    active_seconds IS NULL OR active_seconds BETWEEN 0 AND 2678400
                ),
                received_at TEXT NOT NULL,
                UNIQUE (scope, upstream_id, started_at, loadpoint)
            )"""
        )
        connection.execute(
            "CREATE INDEX idx_charger_sessions_scope_order "
            "ON charger_sessions(scope, ended_at DESC, id DESC)"
        )
        connection.execute(
            "CREATE INDEX idx_charger_sessions_global_order "
            "ON charger_sessions(ended_at DESC, id DESC)"
        )

    def ingest_charger(
        self,
        scope: Any,
        observation: Any = None,
        sessions: Any = None,
    ) -> dict[str, bool]:
        """Atomically ingest one latest observation and a batch of closed sessions."""
        try:
            normalized_scope = validate_scope(scope)
            normalized_observation = (
                None if observation is None else validate_observation(observation)
            )
            if sessions is None:
                raw_sessions: list[Any] = []
            elif isinstance(sessions, list) and len(sessions) <= MAX_CHARGER_BATCH:
                raw_sessions = sessions
            else:
                raise ChargerDataError("sessions debe ser una lista de hasta 1000 elementos.")
            normalized_sessions: dict[str, dict[str, Any]] = {}
            for raw_session in raw_sessions:
                session = validate_session(raw_session, normalized_scope)
                previous = normalized_sessions.get(session["id"])
                if previous is not None and previous != session:
                    raise ChargerDataError("Un id de sesión aparece con datos discordantes.")
                normalized_sessions[session["id"]] = session
            future_block = [{
                "scope": normalized_scope,
                "observation": normalized_observation,
                "sessions": list(normalized_sessions.values()),
            }]
            validate_charger_future_dates(future_block, self._clock_now())
        except ChargerDataError as exc:
            raise _domain_error(str(exc)) from None

        try:
            with self._transaction() as connection:
                before_observation = connection.execute(
                    "SELECT * FROM charger_observations WHERE scope = ?", (normalized_scope,)
                ).fetchone()
                before_sessions = _session_snapshot(connection)

                if normalized_observation is not None:
                    current = before_observation
                    if current is None or normalized_observation["received_at"] > current["received_at"]:
                        self._write_charger_observation(
                            connection, normalized_scope, normalized_observation
                        )

                for session in normalized_sessions.values():
                    current = connection.execute(
                        "SELECT * FROM charger_sessions WHERE id = ?", (session["id"],)
                    ).fetchone()
                    if current is not None:
                        stable_fields = (
                            (current["scope"], normalized_scope),
                            (current["upstream_id"], session["upstream_id"]),
                            (current["loadpoint"], session["loadpoint"]),
                            (current["started_at"], session["started_at"]),
                        )
                        if any(old != new for old, new in stable_fields):
                            raise ChargerDataError("Un id de sesión ya está asociado a otra identidad.")
                        if session["received_at"] > current["received_at"]:
                            self._record_charger_pricing_correction(
                                connection, normalized_scope, current, session
                            )
                            self._write_charger_session(connection, normalized_scope, session)
                    else:
                        self._write_charger_session(connection, normalized_scope, session)

                connection.execute(
                    """DELETE FROM charger_sessions WHERE id IN (
                           SELECT id FROM charger_sessions
                           ORDER BY ended_at DESC, id DESC LIMIT -1 OFFSET ?
                       )""",
                    (MAX_CHARGER_SESSIONS,),
                )
                scope_count = connection.execute(
                    """SELECT COUNT(*) FROM (
                           SELECT scope FROM charger_observations
                           UNION
                           SELECT scope FROM charger_sessions
                       )"""
                ).fetchone()[0]
                if scope_count > MAX_CHARGER_SCOPES:
                    raise ChargerDataError("El almacén supera los 16 scopes de cargador.")

                after_observation = connection.execute(
                    "SELECT * FROM charger_observations WHERE scope = ?", (normalized_scope,)
                ).fetchone()
                after_sessions = _session_snapshot(connection)
                changed = (
                    _row_values(before_observation) != _row_values(after_observation)
                    or before_sessions != after_sessions
                )
                return {"changed": changed}
        except ChargerDataError as exc:
            raise _domain_error(str(exc)) from None

    def charger_observation(self, scope: Any) -> dict[str, Any] | None:
        try:
            normalized_scope = validate_scope(scope)
        except ChargerDataError as exc:
            raise _domain_error(str(exc)) from None
        with self._read_transaction() as connection:
            row = connection.execute(
                "SELECT * FROM charger_observations WHERE scope = ?", (normalized_scope,)
            ).fetchone()
            return None if row is None else _observation_from_row(row)

    def charger_sessions(self, scope: Any, limit: Any = 20, offset: Any = 0) -> dict[str, Any]:
        try:
            normalized_scope = validate_scope(scope)
            page_limit = _page_int(limit, "limit", minimum=1, maximum=MAX_CHARGER_PAGE_SIZE)
            page_offset = _page_int(offset, "offset", minimum=0, maximum=2_147_483_647)
        except ChargerDataError as exc:
            raise _domain_error(str(exc)) from None
        with self._read_transaction() as connection:
            total = int(connection.execute(
                "SELECT COUNT(*) FROM charger_sessions WHERE scope = ?", (normalized_scope,)
            ).fetchone()[0])
            rows = connection.execute(
                """SELECT * FROM charger_sessions WHERE scope = ?
                   ORDER BY ended_at DESC, id DESC LIMIT ? OFFSET ?""",
                (normalized_scope, page_limit, page_offset),
            ).fetchall()
            return {
                "items": [_session_from_row(row) for row in rows],
                "total": total,
                "limit": page_limit,
                "offset": page_offset,
            }

    def _export_charger(self, connection: sqlite3.Connection) -> list[dict[str, Any]]:
        scopes = [row[0] for row in connection.execute(
            """SELECT scope FROM charger_observations
               UNION SELECT scope FROM charger_sessions ORDER BY scope"""
        ).fetchall()]
        result: list[dict[str, Any]] = []
        for scope in scopes:
            observation_row = connection.execute(
                "SELECT * FROM charger_observations WHERE scope = ?", (scope,)
            ).fetchone()
            session_rows = connection.execute(
                """SELECT * FROM charger_sessions WHERE scope = ?
                   ORDER BY ended_at DESC, id DESC""",
                (scope,),
            ).fetchall()
            result.append({
                "scope": scope,
                "observation": (
                    None if observation_row is None else _observation_from_row(observation_row)
                ),
                "sessions": [_session_from_row(row) for row in session_rows],
            })
        return result

    def _replace_charger_archive(
        self, connection: sqlite3.Connection, scopes: list[dict[str, Any]]
    ) -> None:
        connection.execute("DELETE FROM charger_observations")
        connection.execute("DELETE FROM charger_sessions")
        for scope_row in scopes:
            observation = scope_row["observation"]
            if observation is not None:
                self._write_charger_observation(connection, scope_row["scope"], observation)
            for session in scope_row["sessions"]:
                self._write_charger_session(connection, scope_row["scope"], session)

    @staticmethod
    def _write_charger_observation(
        connection: sqlite3.Connection, scope: str, item: Mapping[str, Any]
    ) -> None:
        connection.execute(
            """INSERT INTO charger_observations
               (scope, received_at, loadpoint, connected, charging, power_w,
                charged_kwh, active_seconds)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(scope) DO UPDATE SET received_at=excluded.received_at,
                   loadpoint=excluded.loadpoint, connected=excluded.connected,
                   charging=excluded.charging, power_w=excluded.power_w,
                   charged_kwh=excluded.charged_kwh, active_seconds=excluded.active_seconds""",
            (
                scope, item["received_at"], item["loadpoint"], int(item["connected"]),
                int(item["charging"]), item["power_w"], item["charged_kwh"],
                item["active_seconds"],
            ),
        )

    @staticmethod
    def _write_charger_session(
        connection: sqlite3.Connection, scope: str, item: Mapping[str, Any]
    ) -> None:
        connection.execute(
            """INSERT INTO charger_sessions
               (id, scope, upstream_id, loadpoint, started_at, ended_at, kwh,
                active_seconds, received_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET ended_at=excluded.ended_at,
                   kwh=excluded.kwh, active_seconds=excluded.active_seconds,
                   received_at=excluded.received_at""",
            (
                item["id"], scope, item["upstream_id"], item["loadpoint"],
                item["started_at"], item["ended_at"], item["kwh"],
                item["active_seconds"], item["received_at"],
            ),
        )


def _exact_mapping(value: Any, keys: frozenset[str], name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ChargerDataError(f"{name} contiene campos no admitidos o incompletos.")
    return dict(value)


def _loadpoint(value: Any) -> str:
    if not isinstance(value, str):
        raise ChargerDataError("loadpoint debe ser texto.")
    result = value.strip()
    if not 1 <= len(result) <= MAX_CHARGER_LOADPOINT_LENGTH:
        raise ChargerDataError("loadpoint debe contener entre 1 y 80 caracteres.")
    if any(ord(character) < 32 or ord(character) == 127 for character in result):
        raise ChargerDataError("loadpoint contiene caracteres no permitidos.")
    try:
        result.encode("utf-8")
    except UnicodeEncodeError:
        raise ChargerDataError("loadpoint contiene texto no válido.") from None
    return result


def _boolean(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise ChargerDataError(f"{name} debe ser booleano.")
    return value


def _number(value: Any, name: str, *, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ChargerDataError(f"{name} debe ser numérico.")
    # Compare integers and floats to bounds before converting. Python integers
    # can be arbitrarily large, while float(huge_int) raises OverflowError.
    if value < 0 or value > maximum:
        raise ChargerDataError(f"{name} está fuera de rango.")
    if isinstance(value, float) and not math.isfinite(value):
        raise ChargerDataError(f"{name} está fuera de rango.")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ChargerDataError(f"{name} debe ser numérico.") from None
    if not math.isfinite(result):
        raise ChargerDataError(f"{name} está fuera de rango.")
    return result


def _nullable_number(value: Any, name: str, *, maximum: float) -> float | None:
    return None if value is None else _number(value, name, maximum=maximum)


def _datetime(value: Any, name: str) -> str:
    if not isinstance(value, str) or len(value) > 64:
        raise ChargerDataError(f"{name} debe ser una fecha ISO con zona horaria.")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        raise ChargerDataError(f"{name} debe ser una fecha ISO con zona horaria.") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ChargerDataError(f"{name} debe incluir una zona horaria.")
    return _format_utc(parsed)


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _page_int(value: Any, name: str, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ChargerDataError(f"{name} está fuera de rango.")
    return value


def _session_snapshot(connection: sqlite3.Connection) -> tuple[tuple[Any, ...], ...]:
    rows = connection.execute("SELECT * FROM charger_sessions ORDER BY id").fetchall()
    return tuple(tuple(row) for row in rows)


def _row_values(row: sqlite3.Row | None) -> tuple[Any, ...] | None:
    return None if row is None else tuple(row)


def _observation_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "received_at": row["received_at"],
        "loadpoint": row["loadpoint"],
        "connected": bool(row["connected"]),
        "charging": bool(row["charging"]),
        "power_w": None if row["power_w"] is None else float(row["power_w"]),
        "charged_kwh": None if row["charged_kwh"] is None else float(row["charged_kwh"]),
        "active_seconds": None if row["active_seconds"] is None else float(row["active_seconds"]),
    }


def _session_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "upstream_id": int(row["upstream_id"]),
        "loadpoint": row["loadpoint"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "kwh": float(row["kwh"]),
        "active_seconds": None if row["active_seconds"] is None else float(row["active_seconds"]),
        "received_at": row["received_at"],
    }


def _domain_error(message: str, status: int = 400) -> Exception:
    # Runtime import avoids domain -> charger_store -> domain during module load.
    from pulse.domain import DomainError

    return DomainError(message, status)
