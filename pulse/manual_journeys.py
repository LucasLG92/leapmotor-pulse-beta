"""Owner-scoped storage and validation for user-entered journeys."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
import json
import math
import re
import sqlite3
from typing import Any
import unicodedata
import uuid
from zoneinfo import ZoneInfo

from pulse.manual_charge_requests import (
    MAX_REQUESTS_GLOBAL,
    MAX_REQUESTS_PER_OWNER,
    REQUEST_TTL_DAYS,
    attempt_fingerprint,
    valid_request_id,
)
from pulse.widget_profiles import WidgetProfileValidationError, validate_owner_id


MAX_MANUAL_JOURNEYS_PER_OWNER = 100_000
MAX_MANUAL_JOURNEY_STOPS = 32
MAX_MANUAL_JOURNEY_LABEL_LENGTH = 120
MAX_MANUAL_JOURNEY_NOTES_LENGTH = 2_000
MAX_MANUAL_JOURNEY_TAGS = 8
MAX_MANUAL_JOURNEY_TAG_LENGTH = 32
MAX_MANUAL_JOURNEY_SEARCH_LENGTH = 100
MAX_MANUAL_JOURNEY_DOCUMENT_ROWS = 10_000
MAX_MANUAL_JOURNEY_DOCUMENT_BYTES = 4 * 1024 * 1024
MAX_MANUAL_JOURNEY_REVISION = 2_147_483_647
MAX_MANUAL_JOURNEY_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
_MONTH_ZONE = ZoneInfo("Europe/Madrid")
# 100,000 rows × bounded binary64 inputs can require fewer than 1,100
# significant decimal digits when an integer total is aligned with a subnormal.
_REPORT_DECIMAL_PRECISION = 1_200
_LEGACY_MANUAL_FIELDS = frozenset({
    "started_at", "ended_at", "from_label", "to_label", "distance_km",
    "energy_kwh", "stops",
})
_ANNOTATION_FIELDS = frozenset({"notes", "tags"})
_MANUAL_FIELDS = _LEGACY_MANUAL_FIELDS | _ANNOTATION_FIELDS
_STORED_FIELDS = (
    "started_at", "ended_at", "from_label", "to_label", "distance_km",
    "energy_kwh", "stops", "notes", "tags",
)


class ManualJourneyValidationError(ValueError):
    """A manual journey or its persisted request ledger is invalid."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _domain_error(message: str, status: int = 400) -> None:
    # DomainError lives in domain.py, which imports this module for its mixin.
    from pulse.domain import DomainError

    raise DomainError(message, status)


def _datetime(value: Any, name: str) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise ManualJourneyValidationError(f"{name} debe ser una fecha ISO con zona horaria.")
    try:
        parsed = datetime.fromisoformat(
            value[:-1] + "+00:00" if value.endswith("Z") else value
        )
    except ValueError:
        raise ManualJourneyValidationError(
            f"{name} debe ser una fecha ISO con zona horaria."
        ) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ManualJourneyValidationError(f"{name} debe incluir una zona horaria.")
    try:
        return parsed.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        raise ManualJourneyValidationError(
            f"{name} queda fuera del rango de fechas admitido al normalizar la zona."
        ) from None


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _number(value: Any, name: str, maximum: float) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ManualJourneyValidationError(f"{name} debe ser un número finito o null.")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ManualJourneyValidationError(f"{name} debe ser un número finito o null.") from None
    if not math.isfinite(result) or result < 0 or result > maximum:
        raise ManualJourneyValidationError(f"{name} está fuera del rango permitido.")
    return 0.0 if result == 0 else result


def _label(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ManualJourneyValidationError(f"{name} debe ser texto o null.")
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > MAX_MANUAL_JOURNEY_LABEL_LENGTH:
        raise ManualJourneyValidationError(f"{name} supera 120 caracteres.")
    try:
        normalized.encode("utf-8")
    except UnicodeEncodeError:
        raise ManualJourneyValidationError(f"{name} contiene texto no válido.") from None
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise ManualJourneyValidationError(f"{name} contiene caracteres no permitidos.")
    return normalized


def _text_without_controls(value: Any, name: str, maximum: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ManualJourneyValidationError(f"{name} debe ser texto o null.")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ManualJourneyValidationError(f"{name} contiene texto no válido.") from None
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise ManualJourneyValidationError(f"{name} contiene caracteres de control.")
    if len(value) > maximum:
        raise ManualJourneyValidationError(f"{name} supera {maximum} caracteres.")
    normalized = value.strip()
    return normalized or None


def _tags(value: Any) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_MANUAL_JOURNEY_TAGS:
        raise ManualJourneyValidationError("tags debe contener como máximo 8 etiquetas.")
    result: list[str] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, str):
            raise ManualJourneyValidationError("Cada etiqueta debe ser texto.")
        if len(raw) > MAX_MANUAL_JOURNEY_TAG_LENGTH:
            raise ManualJourneyValidationError("Cada etiqueta supera 32 caracteres.")
        try:
            raw.encode("utf-8")
        except UnicodeEncodeError:
            raise ManualJourneyValidationError("Una etiqueta contiene texto no válido.") from None
        if any(unicodedata.category(character) == "Cc" for character in raw):
            raise ManualJourneyValidationError("Una etiqueta contiene caracteres de control.")
        normalized = unicodedata.normalize("NFC", raw.strip()).casefold()
        if not normalized:
            raise ManualJourneyValidationError("Las etiquetas no pueden estar vacías.")
        if len(normalized) > MAX_MANUAL_JOURNEY_TAG_LENGTH:
            raise ManualJourneyValidationError("Cada etiqueta supera 32 caracteres.")
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _casefold_text(value: str | None) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("searchable journey text must be a string")
    return unicodedata.normalize("NFC", value).casefold()


def _search_term(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        _domain_error("search debe ser texto.")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        _domain_error("search contiene texto no válido.")
    if len(value) > MAX_MANUAL_JOURNEY_SEARCH_LENGTH:
        _domain_error("search supera 100 caracteres.")
    if any(unicodedata.category(character) == "Cc" for character in value):
        _domain_error("search contiene caracteres de control.")
    return _casefold_text(value.strip()) or None


def canonicalize_manual_journey(
    values: Any, *, now: datetime | None = None
) -> dict[str, Any]:
    """Validate user intent, normalize times to UTC, and calculate derived durations."""
    if (
        not isinstance(values, Mapping)
        or not _LEGACY_MANUAL_FIELDS <= set(values)
        or set(values) - _MANUAL_FIELDS
    ):
        raise ManualJourneyValidationError(
            "El trayecto debe incluir todos sus campos y no admitir campos adicionales."
        )
    started = _datetime(values["started_at"], "started_at")
    ended = _datetime(values["ended_at"], "ended_at")
    if ended <= started:
        raise ManualJourneyValidationError("ended_at debe ser posterior a started_at.")
    if now is not None and ended > now.astimezone(timezone.utc) + timedelta(minutes=5):
        raise ManualJourneyValidationError("ended_at no puede superar el reloj en más de 5 minutos.")

    distance = _number(values["distance_km"], "distance_km", 50_000)
    energy = _number(values["energy_kwh"], "energy_kwh", 5_000)
    raw_stops = values["stops"]
    if not isinstance(raw_stops, list) or len(raw_stops) > MAX_MANUAL_JOURNEY_STOPS:
        raise ManualJourneyValidationError("stops debe contener como máximo 32 intervalos.")

    stops: list[dict[str, str]] = []
    previous_end: datetime | None = None
    paused_seconds = 0.0
    for raw in raw_stops:
        if not isinstance(raw, Mapping) or set(raw) != {"started_at", "ended_at"}:
            raise ManualJourneyValidationError(
                "Cada parada debe contener exactamente started_at y ended_at."
            )
        stop_started = _datetime(raw["started_at"], "stops.started_at")
        stop_ended = _datetime(raw["ended_at"], "stops.ended_at")
        if stop_ended <= stop_started:
            raise ManualJourneyValidationError("Cada parada debe tener duración positiva.")
        if stop_started < started or stop_ended > ended:
            raise ManualJourneyValidationError("Las paradas deben quedar dentro del trayecto.")
        if previous_end is not None and stop_started < previous_end:
            raise ManualJourneyValidationError("Las paradas deben estar ordenadas y no solaparse.")
        previous_end = stop_ended
        paused_seconds += (stop_ended - stop_started).total_seconds()
        stops.append({
            "started_at": _format_datetime(stop_started),
            "ended_at": _format_datetime(stop_ended),
        })

    duration_seconds = (ended - started).total_seconds()
    duration_min = round(duration_seconds / 60, 6)
    paused_min = round(paused_seconds / 60, 6)
    active_min = round((duration_seconds - paused_seconds) / 60, 6)
    consumption = None
    if distance is not None and distance > 0 and energy is not None:
        try:
            raw_consumption = energy / distance * 100
        except OverflowError:
            raise ManualJourneyValidationError(
                "consumption_kwh_100km queda fuera del rango numérico admitido."
            ) from None
        if not math.isfinite(raw_consumption):
            raise ManualJourneyValidationError(
                "consumption_kwh_100km queda fuera del rango numérico admitido."
            )
        consumption = round(raw_consumption, 6)
    return {
        "started_at": _format_datetime(started),
        "ended_at": _format_datetime(ended),
        "from_label": _label(values["from_label"], "from_label"),
        "to_label": _label(values["to_label"], "to_label"),
        "distance_km": distance,
        "energy_kwh": energy,
        "stops": stops,
        "notes": _text_without_controls(
            values.get("notes"), "notes", MAX_MANUAL_JOURNEY_NOTES_LENGTH
        ),
        "tags": _tags(values.get("tags", [])),
        "duration_min": duration_min,
        "paused_min": paused_min,
        "active_min": active_min,
        "consumption_kwh_100km": consumption,
    }


def _owner_id(value: Any) -> str:
    try:
        return validate_owner_id(value)
    except WidgetProfileValidationError as exc:
        _domain_error(str(exc))
    raise AssertionError("unreachable")


def _same_journey_data(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return all(left[field] == right[field] for field in _STORED_FIELDS)


def _journey_item(row: sqlite3.Row) -> dict[str, Any]:
    try:
        stops = json.loads(row["stops_json"])
        tags = json.loads(row["tags_json"])
        canonical = canonicalize_manual_journey({
            "started_at": row["started_at"],
            "ended_at": row["ended_at"],
            "from_label": row["from_label"],
            "to_label": row["to_label"],
            "distance_km": row["distance_km"],
            "energy_kwh": row["energy_kwh"],
            "stops": stops,
            "notes": row["notes"],
            "tags": tags,
        })
        if canonical["started_at"] != row["started_at"] or canonical["ended_at"] != row["ended_at"]:
            raise ValueError("noncanonical timestamps")
        if canonical["tags"] != tags or canonical["notes"] != row["notes"]:
            raise ValueError("noncanonical annotations")
        if type(row["revision"]) is not int or row["revision"] < 1:
            raise ValueError("invalid revision")
        if MAX_MANUAL_JOURNEY_ID_RE.fullmatch(row["id"]) is None:
            raise ValueError("invalid id")
    except (ManualJourneyValidationError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        _domain_error("El diario de trayectos contiene un registro no válido.", 500)
    return {
        "id": row["id"],
        "revision": row["revision"],
        "source": "manual",
        "coverage": "unknown",
        **canonical,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _insert_journey(
    connection: sqlite3.Connection,
    *,
    journey_id: str,
    owner: str,
    data: Mapping[str, Any],
    created_at: str,
    updated_at: str,
    revision: int = 1,
) -> None:
    connection.execute(
        """INSERT INTO manual_journeys
           (id, owner_id, revision, started_at, ended_at, from_label, to_label,
            distance_km, energy_kwh, stops_json, notes, tags_json, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            journey_id, owner, revision, data["started_at"], data["ended_at"],
            data["from_label"], data["to_label"], data["distance_km"],
            data["energy_kwh"], json.dumps(
                data["stops"], ensure_ascii=False, allow_nan=False, separators=(",", ":")
            ), data["notes"], json.dumps(
                data["tags"], ensure_ascii=False, allow_nan=False, separators=(",", ":")
            ), created_at, updated_at,
        ),
    )


def create_manual_journey_schema(connection: sqlite3.Connection) -> None:
    """Add durable owner-scoped records and short-lived idempotency reservations."""
    connection.execute(
        """CREATE TABLE manual_journeys (
            id TEXT PRIMARY KEY
                CHECK (length(id) = 32 AND id NOT GLOB '*[^0-9a-f]*'),
            owner_id TEXT NOT NULL CHECK (length(owner_id) BETWEEN 1 AND 255),
            revision INTEGER NOT NULL CHECK (revision >= 1),
            started_at TEXT NOT NULL,
            ended_at TEXT NOT NULL,
            from_label TEXT CHECK (from_label IS NULL OR length(from_label) <= 120),
            to_label TEXT CHECK (to_label IS NULL OR length(to_label) <= 120),
            distance_km REAL CHECK (distance_km IS NULL OR distance_km BETWEEN 0 AND 50000),
            energy_kwh REAL CHECK (energy_kwh IS NULL OR energy_kwh BETWEEN 0 AND 5000),
            stops_json TEXT NOT NULL CHECK (
                json_valid(stops_json) AND json_type(stops_json) = 'array'
            ),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        "CREATE INDEX idx_manual_journeys_owner_started "
        "ON manual_journeys(owner_id, started_at DESC, id)"
    )
    connection.execute(
        """CREATE TABLE manual_journey_requests (
            id TEXT PRIMARY KEY
                CHECK (length(id) = 32 AND id NOT GLOB '*[^0-9a-f]*'),
            owner_id TEXT NOT NULL CHECK (length(owner_id) BETWEEN 1 AND 255),
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            attempt_fingerprint TEXT,
            journey_id TEXT,
            CHECK (
                (attempt_fingerprint IS NULL AND journey_id IS NULL)
                OR (attempt_fingerprint IS NOT NULL
                    AND length(attempt_fingerprint) = 64
                    AND attempt_fingerprint NOT GLOB '*[^0-9a-f]*'
                    AND journey_id IS NOT NULL
                    AND length(journey_id) = 32
                    AND journey_id NOT GLOB '*[^0-9a-f]*')
            )
        )"""
    )
    connection.execute(
        "CREATE INDEX idx_manual_journey_requests_expiry "
        "ON manual_journey_requests(expires_at)"
    )
    connection.execute(
        "CREATE INDEX idx_manual_journey_requests_owner_expiry "
        "ON manual_journey_requests(owner_id, expires_at)"
    )


def migrate_manual_journey_annotations(connection: sqlite3.Connection) -> None:
    """Add notes and tags to an existing manual journey table, once per column."""
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(manual_journeys)")
    }
    if not columns:
        raise sqlite3.OperationalError("manual_journeys table is missing")
    if "notes" not in columns:
        connection.execute(
            """ALTER TABLE manual_journeys ADD COLUMN notes TEXT
               CHECK (notes IS NULL OR length(notes) <= 2000)"""
        )
    if "tags_json" not in columns:
        connection.execute(
            """ALTER TABLE manual_journeys ADD COLUMN tags_json TEXT NOT NULL DEFAULT '[]'
               CHECK (json_valid(tags_json) AND json_type(tags_json) = 'array')"""
        )


def validate_manual_journey_archive(
    value: Any, *, now: datetime, schema_version: int = 17
) -> list[dict[str, Any]]:
    """Validate legacy schema-16 or current schema-17 journey rows."""
    from pulse.domain import DomainError

    if type(schema_version) is not int or schema_version not in {16, 17}:
        raise DomainError("La versión del archivo de trayectos manuales no es válida.")
    if not isinstance(value, list) or len(value) > 100_000:
        raise DomainError("manual_journeys debe ser una lista dentro del límite permitido.")
    rows: list[dict[str, Any]] = []
    ids: set[str] = set()
    expected = {
        "id", "owner_id", "revision", "source", "coverage",
        *(_MANUAL_FIELDS if schema_version == 17 else _LEGACY_MANUAL_FIELDS),
        "duration_min", "paused_min", "active_min", "consumption_kwh_100km",
        "created_at", "updated_at",
    }
    for raw in value:
        if not isinstance(raw, Mapping) or set(raw) != expected:
            raise DomainError(
                f"Cada trayecto manual debe cumplir el esquema de exportación {schema_version}."
            )
        journey_id = raw["id"]
        if not isinstance(journey_id, str) or MAX_MANUAL_JOURNEY_ID_RE.fullmatch(journey_id) is None:
            raise DomainError("El identificador de trayecto manual no es válido.")
        if journey_id in ids:
            raise DomainError("La importación contiene identificadores de trayecto manual duplicados.")
        ids.add(journey_id)
        if (
            type(raw["revision"]) is not int
            or not 1 <= raw["revision"] <= MAX_MANUAL_JOURNEY_REVISION
        ):
            raise DomainError("revision debe ser un entero positivo válido.")
        if raw["source"] != "manual" or raw["coverage"] != "unknown":
            raise DomainError("Los trayectos manuales requieren source manual y coverage unknown.")
        try:
            owner = validate_owner_id(raw["owner_id"])
        except WidgetProfileValidationError as exc:
            raise DomainError(str(exc)) from None
        try:
            manual_values = {field: raw[field] for field in _LEGACY_MANUAL_FIELDS}
            if schema_version == 17:
                manual_values.update({field: raw[field] for field in _ANNOTATION_FIELDS})
            data = canonicalize_manual_journey(manual_values, now=now)
            created = _datetime(raw["created_at"], "created_at")
            updated = _datetime(raw["updated_at"], "updated_at")
        except ManualJourneyValidationError as exc:
            raise DomainError(str(exc), exc.status) from None
        if schema_version == 17 and (
            data["notes"] != raw["notes"] or data["tags"] != raw["tags"]
        ):
            raise DomainError("Las anotaciones del trayecto no están normalizadas.")
        if updated < created or updated > now.astimezone(timezone.utc) + timedelta(minutes=5):
            raise DomainError("updated_at no puede preceder a created_at ni ser futuro.")
        if created > now.astimezone(timezone.utc) + timedelta(minutes=5):
            raise DomainError("created_at no puede ser futuro.")
        derived = ("duration_min", "paused_min", "active_min", "consumption_kwh_100km")
        for key in derived:
            actual = raw[key]
            expected_value = data[key]
            if expected_value is None:
                if actual is not None:
                    raise DomainError(f"{key} no coincide con los datos del trayecto.")
            else:
                if isinstance(actual, bool) or not isinstance(actual, (int, float)):
                    raise DomainError(f"{key} no coincide con los datos del trayecto.")
                try:
                    numeric_actual = float(actual)
                except (OverflowError, TypeError, ValueError):
                    raise DomainError(
                        f"{key} no coincide con los datos del trayecto."
                    ) from None
                if not math.isfinite(numeric_actual) or numeric_actual != expected_value:
                    raise DomainError(f"{key} no coincide con los datos del trayecto.")
        rows.append({
            "id": journey_id,
            "owner_id": owner,
            "revision": raw["revision"],
            **data,
            "created_at": _format_datetime(created),
            "updated_at": _format_datetime(updated),
        })
    return rows


def insert_manual_journey_archive_item(
    connection: sqlite3.Connection, item: Mapping[str, Any]
) -> None:
    """Insert a prevalidated archive item while a restore transaction is held."""
    data = {key: item[key] for key in _MANUAL_FIELDS}
    _insert_journey(
        connection,
        journey_id=item["id"],
        owner=item["owner_id"],
        data=data,
        created_at=item["created_at"],
        updated_at=item["updated_at"],
        revision=item["revision"],
    )


def _request_is_current(row: Mapping[str, Any], now: datetime) -> bool:
    try:
        created = _datetime(row["created_at"], "created_at")
        expires = _datetime(row["expires_at"], "expires_at")
    except (ManualJourneyValidationError, KeyError, TypeError):
        return False
    return (
        created <= now < expires
        and expires - created == timedelta(days=REQUEST_TTL_DAYS)
    )


def _request_link_is_valid(row: Mapping[str, Any]) -> bool:
    fingerprint = row["attempt_fingerprint"]
    journey_id = row["journey_id"]
    if fingerprint is None or journey_id is None:
        return fingerprint is None and journey_id is None
    return (
        isinstance(fingerprint, str)
        and re.fullmatch(r"[0-9a-f]{64}", fingerprint) is not None
        and isinstance(journey_id, str)
        and MAX_MANUAL_JOURNEY_ID_RE.fullmatch(journey_id) is not None
    )


class _JourneyTotalsAccumulator:
    """Constant-size exact sums for one report period or calendar month."""

    def __init__(self) -> None:
        self.count = 0
        self.known_distance_count = 0
        self.known_energy_count = 0
        self.paired_count = 0
        self.distance_sum = Decimal(0)
        self.energy_sum = Decimal(0)
        self.paired_distance_sum = Decimal(0)
        self.paired_energy_sum = Decimal(0)

    def add(self, distance_km: float | None, energy_kwh: float | None) -> None:
        distance = Decimal.from_float(distance_km) if distance_km is not None else None
        energy = Decimal.from_float(energy_kwh) if energy_kwh is not None else None
        self.count += 1
        if distance is not None:
            self.known_distance_count += 1
        if energy is not None:
            self.known_energy_count += 1
        if distance is not None and distance > 0 and energy is not None:
            self.paired_count += 1

        # Decimal.from_float preserves each accepted binary64 value exactly.
        # The explicit precision retains those expansions for the bounded
        # 100,000-row/50,000-km/5,000-kWh report, including tiny + large sums.
        with localcontext() as context:
            context.prec = _REPORT_DECIMAL_PRECISION
            if distance is not None:
                self.distance_sum += distance
            if energy is not None:
                self.energy_sum += energy
            if distance is not None and distance > 0 and energy is not None:
                self.paired_distance_sum += distance
                self.paired_energy_sum += energy

    def result(self) -> dict[str, Any]:
        paired_distance = float(self.paired_distance_sum)
        paired_energy = float(self.paired_energy_sum)
        return {
            "count": self.count,
            "distance_km": float(self.distance_sum),
            "energy_kwh": float(self.energy_sum),
            "known_distance_count": self.known_distance_count,
            "known_energy_count": self.known_energy_count,
            "paired_count": self.paired_count,
            "paired_distance_km": paired_distance,
            "paired_energy_kwh": paired_energy,
            "consumption_kwh_100km": (
                round(paired_energy / paired_distance * 100, 6)
                if paired_distance > 0 else None
            ),
        }


def _manual_journey_report(
    connection: sqlite3.Connection,
    owner: str,
    days: int,
    ended: datetime,
) -> dict[str, Any]:
    started = ended - timedelta(days=days)
    start_text, end_text = _format_datetime(started), _format_datetime(ended)
    rows = connection.execute(
        """SELECT * FROM manual_journeys
           WHERE owner_id = ? AND started_at >= ? AND started_at < ?
           ORDER BY started_at, id""",
        (owner, start_text, end_text),
    )
    return _manual_journey_report_from_items(
        (_journey_item(row) for row in rows), days, ended
    )


def _manual_journey_report_from_items(
    items: Iterable[Mapping[str, Any]],
    days: int,
    ended: datetime,
) -> dict[str, Any]:
    started = ended - timedelta(days=days)
    start_text, end_text = _format_datetime(started), _format_datetime(ended)
    month_keys: list[str] = []
    local_start = started.astimezone(_MONTH_ZONE)
    local_last = (ended - timedelta(microseconds=1)).astimezone(_MONTH_ZONE)
    year, month = local_start.year, local_start.month
    while (year, month) <= (local_last.year, local_last.month):
        month_keys.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)

    total = _JourneyTotalsAccumulator()
    grouped = {key: _JourneyTotalsAccumulator() for key in month_keys}
    for validated in items:
        month_key = _datetime(
            validated["started_at"], "started_at"
        ).astimezone(_MONTH_ZONE).strftime("%Y-%m")
        distance = validated["distance_km"]
        energy = validated["energy_kwh"]
        total.add(distance, energy)
        bucket = grouped.get(month_key)
        if bucket is None:
            bucket = grouped[month_key] = _JourneyTotalsAccumulator()
        bucket.add(distance, energy)
    return {
        "source": "manual",
        "coverage": "unknown",
        "period": {
            "started_at": start_text,
            "ended_at": end_text,
            "days": days,
            "timezone": "UTC",
        },
        "totals": total.result(),
        "months": [
            {"month_key": key, **grouped[key].result()}
            for key in month_keys
        ],
    }


class ManualJourneyStoreMixin:
    """SQLite operations for private, user-entered journey history."""

    def reserve_manual_journey_creation(self, owner_id: str) -> dict[str, str]:
        owner = _owner_id(owner_id)
        now_value = self._clock_now()
        now = _format_datetime(now_value)
        expires_at = _format_datetime(now_value + timedelta(days=REQUEST_TTL_DAYS))
        result: dict[str, str] | None = None
        capacity_reached = False
        with self._transaction() as connection:
            connection.execute(
                "DELETE FROM manual_journey_requests WHERE expires_at <= ?", (now,)
            )
            owner_count = int(connection.execute(
                "SELECT COUNT(*) FROM manual_journey_requests WHERE owner_id = ?", (owner,)
            ).fetchone()[0])
            global_count = int(connection.execute(
                "SELECT COUNT(*) FROM manual_journey_requests"
            ).fetchone()[0])
            if owner_count >= MAX_REQUESTS_PER_OWNER or global_count >= MAX_REQUESTS_GLOBAL:
                capacity_reached = True
            else:
                request_id = uuid.uuid4().hex
                connection.execute(
                    """INSERT INTO manual_journey_requests
                       (id, owner_id, created_at, expires_at)
                       VALUES (?, ?, ?, ?)""",
                    (request_id, owner, now, expires_at),
                )
                result = {"id": request_id, "expires_at": expires_at}
        if capacity_reached:
            _domain_error("Se alcanzó el límite de solicitudes de trayecto pendientes.", 429)
        if result is None:
            _domain_error("No se pudo reservar la solicitud de trayecto.", 500)
        return result

    def list_manual_journeys(
        self,
        owner_id: str,
        limit: int = 20,
        offset: int = 0,
        *,
        tag: str | None = None,
        search: str | None = None,
    ) -> dict[str, Any]:
        """Page owner rows; tags match exactly after NFC/casefold and search is a
        Unicode casefolded substring over origin, destination, notes, and tags.
        """
        owner = _owner_id(owner_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            _domain_error("limit debe estar entre 1 y 100.")
        if type(offset) is not int or not 0 <= offset <= 2_147_483_647:
            _domain_error("offset debe ser un entero entre 0 y 2147483647.")
        if tag is not None:
            try:
                normalized_tags = _tags([tag])
            except ManualJourneyValidationError as exc:
                _domain_error(str(exc), exc.status)
            normalized_tag = normalized_tags[0]
        else:
            normalized_tag = None
        normalized_search = _search_term(search)
        where = ["owner_id = ?"]
        parameters: list[Any] = [owner]
        if normalized_tag is not None:
            where.append(
                "EXISTS (SELECT 1 FROM json_each(manual_journeys.tags_json) AS journey_tag "
                "WHERE journey_tag.value = ?)"
            )
            parameters.append(normalized_tag)
        if normalized_search is not None:
            where.append(
                "(instr(pulse_casefold(from_label), ?) > 0 "
                "OR instr(pulse_casefold(to_label), ?) > 0 "
                "OR instr(pulse_casefold(notes), ?) > 0 "
                "OR EXISTS (SELECT 1 FROM json_each(manual_journeys.tags_json) AS journey_tag "
                "WHERE instr(journey_tag.value, ?) > 0))"
            )
            parameters.extend([normalized_search] * 4)
        where_sql = " AND ".join(where)
        with self._read_transaction() as connection:
            connection.create_function(
                "pulse_casefold", 1, _casefold_text, deterministic=True
            )
            total = int(connection.execute(
                f"SELECT COUNT(*) FROM manual_journeys WHERE {where_sql}", parameters
            ).fetchone()[0])
            rows = connection.execute(
                f"""SELECT * FROM manual_journeys WHERE {where_sql}
                    ORDER BY started_at DESC, id DESC LIMIT ? OFFSET ?""",
                (*parameters, limit, offset),
            ).fetchall()
            return {"items": [_journey_item(row) for row in rows], "total": total}

    def report_manual_journeys(self, owner_id: str, days: int = 30) -> dict[str, Any]:
        owner = _owner_id(owner_id)
        if type(days) is not int or days not in {30, 90, 365}:
            _domain_error("days debe ser 30, 90 o 365.")
        ended = self._clock_now()
        with self._read_transaction() as connection:
            return _manual_journey_report(connection, owner, days, ended)

    def journey_document_data(self, owner_id: str, days: int = 30) -> dict[str, Any]:
        """Return one owner-scoped report and at most 10,000 matching journey rows."""
        owner = _owner_id(owner_id)
        if type(days) is not int or days not in {30, 90, 365}:
            _domain_error("days debe ser 30, 90 o 365.")
        ended = self._clock_now()
        started = ended - timedelta(days=days)
        start_text, end_text = _format_datetime(started), _format_datetime(ended)
        with self._read_transaction() as connection:
            rows = connection.execute(
                """SELECT * FROM manual_journeys
                   WHERE owner_id = ? AND started_at >= ? AND started_at < ?
                   ORDER BY started_at, id LIMIT ?""",
                (
                    owner, start_text, end_text,
                    MAX_MANUAL_JOURNEY_DOCUMENT_ROWS + 1,
                ),
            ).fetchall()
            if len(rows) > MAX_MANUAL_JOURNEY_DOCUMENT_ROWS:
                _domain_error("El informe supera 10.000 trayectos.", 413)
            journeys = [_journey_item(row) for row in rows]
            return {
                "report": _manual_journey_report_from_items(journeys, days, ended),
                "journeys": journeys,
            }

    def add_manual_journey(
        self, owner_id: str, request_id: str, values: Mapping[str, Any]
    ) -> dict[str, Any]:
        owner = _owner_id(owner_id)
        if not valid_request_id(request_id):
            _domain_error("La solicitud de creación de trayecto ya no está disponible.", 409)
        try:
            now_value = self._clock_now()
            data = canonicalize_manual_journey(values, now=now_value)
        except ManualJourneyValidationError as exc:
            _domain_error(str(exc), exc.status)
        fingerprint = attempt_fingerprint(data)
        now = _format_datetime(now_value)
        created: dict[str, Any] | None = None
        capacity_reached = False
        with self._transaction() as connection:
            reservation = connection.execute(
                "SELECT * FROM manual_journey_requests WHERE id = ?", (request_id,)
            ).fetchone()
            if (
                reservation is None
                or reservation["owner_id"] != owner
                or not _request_is_current(reservation, now_value)
                or not _request_link_is_valid(reservation)
            ):
                _domain_error("La solicitud de creación de trayecto ya no está disponible.", 409)
            previous_fingerprint = reservation["attempt_fingerprint"]
            if previous_fingerprint is not None:
                # SQLite20 reservations hashed the same canonical intent without
                # annotations. Accept that exact legacy intent only when empty;
                # never recompute a reservation from a subsequently edited row.
                legacy_fingerprint = attempt_fingerprint({
                    key: value for key, value in data.items()
                    if key not in {"notes", "tags"}
                }) if data["notes"] is None and not data["tags"] else None
                if previous_fingerprint not in {fingerprint, legacy_fingerprint}:
                    _domain_error("La solicitud ya se utilizó con otros datos.", 409)
                row = connection.execute(
                    "SELECT * FROM manual_journeys WHERE id = ? AND owner_id = ?",
                    (reservation["journey_id"], owner),
                ).fetchone()
                if row is None:
                    _domain_error("El trayecto de esta solicitud ya no existe.", 409)
                return _journey_item(row)

            owner_count = int(connection.execute(
                "SELECT COUNT(*) FROM manual_journeys WHERE owner_id = ?", (owner,)
            ).fetchone()[0])
            if owner_count >= MAX_MANUAL_JOURNEYS_PER_OWNER:
                capacity_reached = True
            else:
                journey_id = uuid.uuid4().hex
                _insert_journey(
                    connection, journey_id=journey_id, owner=owner, data=data,
                    created_at=now, updated_at=now,
                )
                cursor = connection.execute(
                    """UPDATE manual_journey_requests
                       SET attempt_fingerprint = ?, journey_id = ?
                       WHERE id = ? AND owner_id = ?
                         AND attempt_fingerprint IS NULL AND journey_id IS NULL""",
                    (fingerprint, journey_id, request_id, owner),
                )
                if cursor.rowcount != 1:
                    _domain_error("La solicitud de creación de trayecto ya no está disponible.", 409)
                self._touch(connection)
                row = connection.execute(
                    "SELECT * FROM manual_journeys WHERE id = ?", (journey_id,)
                ).fetchone()
                created = _journey_item(row)
        if capacity_reached:
            _domain_error("Se alcanzó el límite de trayectos manuales.", 429)
        if created is None:
            _domain_error("No se pudo guardar el trayecto manual.", 500)
        return created

    def update_manual_journey(
        self, owner_id: str, journey_id: str, revision: int,
        values: Mapping[str, Any],
    ) -> dict[str, Any]:
        owner = _owner_id(owner_id)
        if not isinstance(journey_id, str) or MAX_MANUAL_JOURNEY_ID_RE.fullmatch(journey_id) is None:
            _domain_error("El identificador de trayecto no es válido.", 404)
        if type(revision) is not int or revision < 1:
            _domain_error("revision debe ser un entero positivo.")
        now_value = self._clock_now()
        try:
            data = canonicalize_manual_journey(values, now=now_value)
        except ManualJourneyValidationError as exc:
            _domain_error(str(exc), exc.status)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM manual_journeys WHERE id = ? AND owner_id = ?",
                (journey_id, owner),
            ).fetchone()
            if row is None:
                _domain_error("No se encontró el trayecto solicitado.", 404)
            current = _journey_item(row)
            # Clients predating annotations cannot express edits to them.
            # Explicit null/[] still clear values in the new editor.
            for field in ("notes", "tags"):
                if field not in values:
                    data[field] = current[field]
            if _same_journey_data(current, data):
                return current
            if revision != current["revision"]:
                _domain_error("El trayecto cambió desde que lo abriste.", 409)
            if current["revision"] >= MAX_MANUAL_JOURNEY_REVISION:
                _domain_error(
                    "El trayecto alcanzó la revisión máxima y no puede editarse.", 409
                )
            updated_at = _format_datetime(now_value)
            cursor = connection.execute(
                """UPDATE manual_journeys SET revision = revision + 1,
                   started_at = ?, ended_at = ?, from_label = ?, to_label = ?,
                   distance_km = ?, energy_kwh = ?, stops_json = ?, notes = ?,
                   tags_json = ?, updated_at = ?
                   WHERE id = ? AND owner_id = ? AND revision = ?""",
                (
                    data["started_at"], data["ended_at"], data["from_label"],
                    data["to_label"], data["distance_km"], data["energy_kwh"],
                    json.dumps(data["stops"], ensure_ascii=False, allow_nan=False,
                               separators=(",", ":")),
                    data["notes"], json.dumps(
                        data["tags"], ensure_ascii=False, allow_nan=False,
                        separators=(",", ":"),
                    ),
                    updated_at, journey_id, owner, revision,
                ),
            )
            if cursor.rowcount != 1:
                _domain_error("El trayecto cambió desde que lo abriste.", 409)
            self._touch(connection)
            updated = connection.execute(
                "SELECT * FROM manual_journeys WHERE id = ? AND owner_id = ?",
                (journey_id, owner),
            ).fetchone()
            return _journey_item(updated)

    def delete_manual_journey(self, owner_id: str, journey_id: str, revision: int) -> None:
        owner = _owner_id(owner_id)
        if not isinstance(journey_id, str) or MAX_MANUAL_JOURNEY_ID_RE.fullmatch(journey_id) is None:
            _domain_error("El identificador de trayecto no es válido.", 404)
        if type(revision) is not int or revision < 1:
            _domain_error("revision debe ser un entero positivo.")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT revision FROM manual_journeys WHERE id = ? AND owner_id = ?",
                (journey_id, owner),
            ).fetchone()
            if row is None:
                _domain_error("No se encontró el trayecto solicitado.", 404)
            if row["revision"] != revision:
                _domain_error("El trayecto cambió desde que lo abriste.", 409)
            connection.execute(
                "DELETE FROM manual_journeys WHERE id = ? AND owner_id = ?",
                (journey_id, owner),
            )
            self._touch(connection)

    def export_manual_journeys(
        self, connection: sqlite3.Connection, owner_id: str | None, *, byte_limit: int
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM manual_journeys"
        args: tuple[Any, ...] = ()
        if owner_id is not None:
            query += " WHERE owner_id = ?"
            args = (_owner_id(owner_id),)
        query += " ORDER BY owner_id, started_at, id"
        result: list[dict[str, Any]] = []
        encoded_size = 2  # Compact JSON array brackets.
        for row in connection.execute(query, args):
            item = _journey_item(row)
            item["owner_id"] = row["owner_id"]
            try:
                encoded_item_size = len(json.dumps(
                    item, allow_nan=False, separators=(",", ":"), ensure_ascii=False
                ).encode("utf-8"))
            except (TypeError, ValueError, UnicodeEncodeError):
                _domain_error("No se pudo validar el documento de exportación.", 500)
            next_size = encoded_size + encoded_item_size + (1 if result else 0)
            if next_size > byte_limit:
                _domain_error(
                    "La exportación supera el tamaño máximo admitido para una restauración.",
                    413,
                )
            encoded_size = next_size
            result.append(item)
        return result
