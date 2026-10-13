"""Owner-scoped maintenance plans with explicit date and odometer coverage."""

from __future__ import annotations

from collections.abc import Mapping
import calendar
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import re
import sqlite3
from typing import Any
import uuid

from pulse.widget_profiles import WidgetProfileValidationError, validate_owner_id


MAX_MAINTENANCE_PLANS_PER_OWNER = 1_000
MAX_MAINTENANCE_PLANS_GLOBAL = 10_000
MAX_MAINTENANCE_COMPLETIONS_PER_PLAN = 100
MAX_MAINTENANCE_COMPLETIONS_GLOBAL = 10_000
# Tombstones preserve idempotency across archive replacement. These finite
# ceilings intentionally stop repeated replacements from growing forever.
MAX_INVALIDATED_MAINTENANCE_PLAN_IDS_PER_OWNER = 10_000
MAX_INVALIDATED_MAINTENANCE_PLAN_IDS_GLOBAL = 100_000
MAX_MAINTENANCE_PLAN_REVISION = 2_147_483_647
MAX_MAINTENANCE_TITLE_LENGTH = 120
MAX_MAINTENANCE_NOTES_LENGTH = 2_000
MAX_MAINTENANCE_COST_EUR = 100_000
_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
_PLAN_FIELDS = frozenset({
    "title", "interval_months", "interval_km", "last_date",
    "last_odometer_km", "notes",
})
_ARCHIVE_FIELDS = frozenset({
    "id", "owner_id", "revision", "source", *_PLAN_FIELDS,
    "created_at", "updated_at", "deleted_at", "history",
})
_HISTORY_FIELDS = frozenset({
    "id", "performed_date", "odometer_km", "cost_eur", "recorded_at",
})


class MaintenancePlanValidationError(ValueError):
    """A maintenance plan or its archive representation is invalid."""


def _domain_error(message: str, status: int = 400) -> None:
    # DomainError imports this module through its Store mixin.
    from pulse.domain import DomainError

    raise DomainError(message, status)


def _owner_id(value: Any) -> str:
    try:
        return validate_owner_id(value)
    except WidgetProfileValidationError as exc:
        _domain_error(str(exc))
    raise AssertionError("unreachable")


def _date(value: Any, name: str, *, nullable: bool = True) -> date | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or len(value) != 10:
        raise MaintenancePlanValidationError(f"{name} debe ser una fecha ISO YYYY-MM-DD.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise MaintenancePlanValidationError(
            f"{name} debe ser una fecha ISO YYYY-MM-DD válida."
        ) from None
    if parsed.isoformat() != value:
        raise MaintenancePlanValidationError(f"{name} debe usar el formato YYYY-MM-DD.")
    return parsed


def _format_datetime(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise MaintenancePlanValidationError("El reloj debe devolver una fecha con zona horaria.")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _datetime(value: Any, name: str) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise MaintenancePlanValidationError(f"{name} debe ser una fecha ISO con zona horaria.")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        raise MaintenancePlanValidationError(
            f"{name} debe ser una fecha ISO con zona horaria."
        ) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MaintenancePlanValidationError(f"{name} debe incluir una zona horaria.")
    try:
        return parsed.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        raise MaintenancePlanValidationError(f"{name} queda fuera del rango admitido.") from None


def _bounded_int(value: Any, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise MaintenancePlanValidationError(
            f"{name} debe ser un entero entre {minimum} y {maximum}."
        )
    return value


def _nullable_int(value: Any, name: str, maximum: int) -> int | None:
    if value is None:
        return None
    return _bounded_int(value, name, 0, maximum)


def _text(value: Any, name: str, maximum: int, *, required: bool) -> str:
    if not isinstance(value, str):
        raise MaintenancePlanValidationError(f"{name} debe ser texto.")
    normalized = value.strip() if required else value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if required and not normalized:
        raise MaintenancePlanValidationError(f"{name} no puede estar vacío.")
    if len(normalized) > maximum:
        raise MaintenancePlanValidationError(f"{name} supera {maximum} caracteres.")
    try:
        normalized.encode("utf-8")
    except UnicodeEncodeError:
        raise MaintenancePlanValidationError(f"{name} contiene texto no válido.") from None
    if any(
        (ord(character) < 32 and character not in "\n\t") or ord(character) == 127
        for character in normalized
    ):
        raise MaintenancePlanValidationError(f"{name} contiene caracteres no permitidos.")
    return normalized


def _now_date(now: datetime | None) -> date | None:
    if now is None:
        return None
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise MaintenancePlanValidationError("now debe incluir una zona horaria.")
    return now.astimezone(timezone.utc).date()


def validate_maintenance_plan_values(
    value: Any, *, now: datetime | None = None
) -> dict[str, Any]:
    """Validate and normalize the writable fields of a maintenance plan."""
    if not isinstance(value, Mapping) or set(value) != _PLAN_FIELDS:
        raise MaintenancePlanValidationError(
            "El plan debe incluir todos sus campos editables y no admitir campos adicionales."
        )
    title = _text(value["title"], "title", MAX_MAINTENANCE_TITLE_LENGTH, required=True)
    notes = _text(value["notes"], "notes", MAX_MAINTENANCE_NOTES_LENGTH, required=False)
    months = value["interval_months"]
    if months is not None:
        months = _bounded_int(months, "interval_months", 1, 120)
    kilometres = value["interval_km"]
    if kilometres is not None:
        kilometres = _bounded_int(kilometres, "interval_km", 1, 200_000)
    if months is None and kilometres is None:
        raise MaintenancePlanValidationError(
            "El plan debe definir un intervalo por meses, kilómetros o ambos."
        )
    last_date = _date(value["last_date"], "last_date")
    last_odometer = _nullable_int(
        value["last_odometer_km"], "last_odometer_km", 2_000_000
    )
    if kilometres is not None and last_odometer is None:
        raise MaintenancePlanValidationError(
            "Un intervalo por kilómetros requiere last_odometer_km como base manual."
        )
    today = _now_date(now)
    if today is not None and last_date is not None and last_date > today:
        raise MaintenancePlanValidationError("last_date no puede estar en el futuro.")
    normalized = {
        "title": title,
        "interval_months": months,
        "interval_km": kilometres,
        "last_date": last_date.isoformat() if last_date is not None else None,
        "last_odometer_km": last_odometer,
        "notes": notes,
    }
    _due_values(normalized)
    return normalized


def attempt_fingerprint(data: Mapping[str, Any]) -> str:
    """Hash canonical creation intent; this value is private operational state."""
    canonical = json.dumps(
        dict(data), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _cost(value: Any, name: str = "cost_eur") -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MaintenancePlanValidationError(f"{name} debe ser un número finito o null.")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise MaintenancePlanValidationError(f"{name} está fuera del rango permitido.") from None
    if not math.isfinite(result) or not 0 <= result <= MAX_MAINTENANCE_COST_EUR:
        raise MaintenancePlanValidationError(
            f"{name} debe estar entre 0 y {MAX_MAINTENANCE_COST_EUR} EUR."
        )
    return 0.0 if result == 0 else result


def _add_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 + months
    year, month0 = divmod(month_index, 12)
    if not 1 <= year <= 9999:
        raise MaintenancePlanValidationError("due_date queda fuera del rango de fechas admitido.")
    month = month0 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _due_values(data: Mapping[str, Any]) -> tuple[str | None, int | None]:
    due_date = None
    if data["interval_months"] is not None and data["last_date"] is not None:
        due_date = _add_months(
            date.fromisoformat(data["last_date"]), data["interval_months"]
        ).isoformat()
    due_odometer = None
    if data["interval_km"] is not None and data["last_odometer_km"] is not None:
        due_odometer = data["last_odometer_km"] + data["interval_km"]
    return due_date, due_odometer


def _status(
    data: Mapping[str, Any], *, today: date, odometer_km: int | None
) -> tuple[str, list[str], dict[str, str]]:
    due_date, due_odometer = _due_values(data)
    date_state = "not_configured"
    if data["interval_months"] is not None:
        date_state = (
            "unknown" if due_date is None
            else "due" if date.fromisoformat(due_date) <= today
            else "upcoming"
        )
    km_state = "not_configured"
    if data["interval_km"] is not None:
        km_state = (
            "unknown" if odometer_km is None
            else "due" if odometer_km >= due_odometer
            else "upcoming"
        )
    reasons: list[str] = []
    if date_state == "due":
        reasons.append("date_due")
    elif date_state == "unknown":
        reasons.append("date_unknown")
    if km_state == "due":
        reasons.append("km_due")
    elif km_state == "unknown":
        reasons.append("km_unknown")
    if "due" in (date_state, km_state):
        state = "due"
    elif "unknown" in (date_state, km_state):
        state = "unknown"
    else:
        state = "upcoming"
    return state, reasons, {"date": date_state, "km": km_state}


def _valid_id(value: Any, name: str = "id") -> str:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        raise MaintenancePlanValidationError(f"{name} no es válido.")
    return value


def _canonical_history(raw: Any, *, now: datetime) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or set(raw) != _HISTORY_FIELDS:
        raise MaintenancePlanValidationError(
            "Cada evento de mantenimiento debe incluir exactamente sus campos persistentes."
        )
    history_id = _valid_id(raw["id"], "history.id")
    performed = _date(raw["performed_date"], "performed_date", nullable=False)
    if performed is None or performed > now.astimezone(timezone.utc).date():
        raise MaintenancePlanValidationError("performed_date no puede estar en el futuro.")
    odometer = _nullable_int(raw["odometer_km"], "odometer_km", 2_000_000)
    cost = _cost(raw["cost_eur"])
    recorded_at = _datetime(raw["recorded_at"], "recorded_at")
    if recorded_at > now.astimezone(timezone.utc) + timedelta(minutes=5):
        raise MaintenancePlanValidationError("recorded_at no puede estar en el futuro.")
    return {
        "id": history_id,
        "performed_date": performed.isoformat(),
        "odometer_km": odometer,
        "cost_eur": cost,
        "recorded_at": _format_datetime(recorded_at),
    }


def _history_item(row: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and expose one persisted completion without deriving extra totals."""
    try:
        history_id = _valid_id(row["id"], "history.id")
        performed = _date(row["performed_date"], "performed_date", nullable=False)
        odometer = _nullable_int(row["odometer_km"], "odometer_km", 2_000_000)
        cost = _cost(row["cost_eur"])
        recorded_at = _datetime(row["recorded_at"], "recorded_at")
        return {
            "id": history_id,
            "performed_date": performed.isoformat() if performed is not None else None,
            "odometer_km": odometer,
            "cost_eur": cost,
            "recorded_at": _format_datetime(recorded_at),
        }
    except (MaintenancePlanValidationError, KeyError, TypeError, ValueError):
        _domain_error("El historial de mantenimiento contiene un registro no válido.", 500)
    raise AssertionError("unreachable")


def _persistent_plan_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return only persisted fields; status and due values are context-dependent."""
    try:
        plan_id = _valid_id(row["id"])
        revision = _bounded_int(
            row["revision"], "revision", 1, MAX_MAINTENANCE_PLAN_REVISION
        )
        if row["source"] != "manual":
            raise MaintenancePlanValidationError("source debe ser manual.")
        data = validate_maintenance_plan_values({field: row[field] for field in _PLAN_FIELDS})
        _due_values(data)
        created = _datetime(row["created_at"], "created_at")
        updated = _datetime(row["updated_at"], "updated_at")
        deleted = None if row["deleted_at"] is None else _datetime(row["deleted_at"], "deleted_at")
        if updated < created or (deleted is not None and deleted < updated):
            raise MaintenancePlanValidationError("Las fechas del plan no mantienen un orden válido.")
        return {
            "id": plan_id,
            "revision": revision,
            "source": "manual",
            **data,
            "created_at": _format_datetime(created),
            "updated_at": _format_datetime(updated),
            "deleted_at": _format_datetime(deleted) if deleted is not None else None,
        }
    except (MaintenancePlanValidationError, KeyError, TypeError, ValueError):
        _domain_error("El almacenamiento contiene un plan de mantenimiento no válido.", 500)
    raise AssertionError("unreachable")


def _maintenance_plan_item(
    row: Mapping[str, Any],
    history: list[dict[str, Any]],
    *,
    today: date,
    odometer_km: int | None,
) -> dict[str, Any]:
    """Build a context-aware plan response with explicit unknown coverage."""
    persistent = _persistent_plan_row(row)
    state, reasons, coverage = _status(
        persistent, today=today, odometer_km=odometer_km
    )
    due_date, due_odometer = _due_values(persistent)
    return {
        **persistent,
        "due_date": due_date,
        "due_odometer_km": due_odometer,
        "status": state,
        "due_reasons": reasons,
        "coverage": coverage,
        "history": history,
    }


def create_maintenance_plan_schema(connection: sqlite3.Connection) -> None:
    """Create the owner-scoped plan and append-only completion tables."""
    connection.execute(
        """CREATE TABLE maintenance_plans (
            id TEXT PRIMARY KEY
                CHECK (length(id) = 32 AND id NOT GLOB '*[^0-9a-f]*'),
            owner_id TEXT NOT NULL CHECK (length(owner_id) BETWEEN 1 AND 255),
            revision INTEGER NOT NULL CHECK (revision BETWEEN 1 AND 2147483647),
            source TEXT NOT NULL CHECK (source = 'manual'),
            title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 120),
            interval_months INTEGER CHECK (interval_months IS NULL OR interval_months BETWEEN 1 AND 120),
            interval_km INTEGER CHECK (interval_km IS NULL OR interval_km BETWEEN 1 AND 200000),
            last_date TEXT,
            last_odometer_km INTEGER CHECK (
                last_odometer_km IS NULL OR last_odometer_km BETWEEN 0 AND 2000000
            ),
            notes TEXT NOT NULL CHECK (length(notes) <= 2000),
            creation_fingerprint TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            deleted_at TEXT,
            UNIQUE (id, owner_id),
            CHECK (interval_months IS NOT NULL OR interval_km IS NOT NULL),
            CHECK (interval_km IS NULL OR last_odometer_km IS NOT NULL)
        )"""
    )
    connection.execute(
        "CREATE INDEX idx_maintenance_plans_owner_updated "
        "ON maintenance_plans(owner_id, deleted_at, updated_at DESC, id)"
    )
    connection.execute(
        """CREATE TABLE maintenance_completions (
            id TEXT PRIMARY KEY
                CHECK (length(id) = 32 AND id NOT GLOB '*[^0-9a-f]*'),
            plan_id TEXT NOT NULL,
            owner_id TEXT NOT NULL CHECK (length(owner_id) BETWEEN 1 AND 255),
            performed_date TEXT NOT NULL,
            odometer_km INTEGER CHECK (odometer_km IS NULL OR odometer_km BETWEEN 0 AND 2000000),
            cost_eur REAL CHECK (cost_eur IS NULL OR cost_eur BETWEEN 0 AND 100000),
            recorded_at TEXT NOT NULL,
            FOREIGN KEY (plan_id, owner_id)
                REFERENCES maintenance_plans(id, owner_id) ON DELETE RESTRICT
        )"""
    )
    connection.execute(
        "CREATE INDEX idx_maintenance_completions_plan_date "
        "ON maintenance_completions(owner_id, plan_id, performed_date, id)"
    )
    connection.execute(
        """CREATE TABLE maintenance_plan_request_tombstones (
            plan_id TEXT PRIMARY KEY
                CHECK (length(plan_id) = 32 AND plan_id NOT GLOB '*[^0-9a-f]*'),
            owner_id TEXT NOT NULL CHECK (length(owner_id) BETWEEN 1 AND 255)
        )"""
    )
    connection.execute(
        "CREATE INDEX idx_maintenance_plan_tombstones_owner "
        "ON maintenance_plan_request_tombstones(owner_id, plan_id)"
    )


def validate_maintenance_plan_archive(raw: Any, now: datetime) -> list[dict[str, Any]]:
    """Validate persistent plan fields and full completion history from an archive."""
    from pulse.domain import DomainError

    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise DomainError("El reloj de Store debe devolver una fecha con zona horaria.")
    if not isinstance(raw, list) or len(raw) > MAX_MAINTENANCE_PLANS_GLOBAL:
        raise DomainError("maintenance_plans debe ser una lista dentro del límite global permitido.")
    normalized: list[dict[str, Any]] = []
    plan_ids: set[str] = set()
    history_ids: set[str] = set()
    owner_counts: dict[str, int] = {}
    total_history = 0
    for item in raw:
        if not isinstance(item, Mapping) or set(item) != _ARCHIVE_FIELDS:
            raise DomainError("Cada plan requiere exactamente los campos persistentes del archivo.")
        try:
            plan_id = _valid_id(item["id"])
            if plan_id in plan_ids:
                raise MaintenancePlanValidationError("maintenance_plans contiene ids duplicados.")
            plan_ids.add(plan_id)
            owner = validate_owner_id(item["owner_id"])
            owner_counts[owner] = owner_counts.get(owner, 0) + 1
            if owner_counts[owner] > MAX_MAINTENANCE_PLANS_PER_OWNER:
                raise MaintenancePlanValidationError(
                    "Un propietario supera el límite de 1000 planes de mantenimiento."
                )
            revision = _bounded_int(
                item["revision"], "revision", 1, MAX_MAINTENANCE_PLAN_REVISION
            )
            if item["source"] != "manual":
                raise MaintenancePlanValidationError("source debe ser manual.")
            data = validate_maintenance_plan_values(
                {field: item[field] for field in _PLAN_FIELDS}, now=now
            )
            created = _datetime(item["created_at"], "created_at")
            updated = _datetime(item["updated_at"], "updated_at")
            deleted = None if item["deleted_at"] is None else _datetime(item["deleted_at"], "deleted_at")
            future_limit = now.astimezone(timezone.utc) + timedelta(minutes=5)
            if created > future_limit or updated > future_limit or (deleted is not None and deleted > future_limit):
                raise MaintenancePlanValidationError("Las fechas de registro no pueden ser futuras.")
            if updated < created or (deleted is not None and deleted < updated):
                raise MaintenancePlanValidationError("Las fechas del plan no mantienen un orden válido.")
            events_raw = item["history"]
            if not isinstance(events_raw, list) or len(events_raw) > MAX_MAINTENANCE_COMPLETIONS_PER_PLAN:
                raise MaintenancePlanValidationError(
                    "history supera el límite de 100 eventos por plan."
                )
            history: list[dict[str, Any]] = []
            for event_raw in events_raw:
                event = _canonical_history(event_raw, now=now)
                if event["id"] in history_ids:
                    raise MaintenancePlanValidationError("history contiene ids duplicados.")
                history_ids.add(event["id"])
                recorded = _datetime(event["recorded_at"], "history.recorded_at")
                if recorded < created or recorded > updated:
                    raise MaintenancePlanValidationError(
                        "history.recorded_at debe quedar entre created_at y updated_at."
                    )
                history.append(event)
            total_history += len(history)
            if total_history > MAX_MAINTENANCE_COMPLETIONS_GLOBAL:
                raise MaintenancePlanValidationError(
                    "maintenance_plans supera el límite global de eventos de mantenimiento."
                )
            normalized.append({
                "id": plan_id,
                "owner_id": owner,
                "revision": revision,
                "source": "manual",
                **data,
                "created_at": _format_datetime(created),
                "updated_at": _format_datetime(updated),
                "deleted_at": _format_datetime(deleted) if deleted is not None else None,
                "history": history,
            })
        except (MaintenancePlanValidationError, WidgetProfileValidationError) as exc:
            raise DomainError(str(exc)) from None
    return normalized


def replace_maintenance_plans(
    connection: sqlite3.Connection,
    owner: str | None,
    rows: list[dict[str, Any]],
    *,
    validate_only: bool = False,
) -> None:
    """Validate a replacement or apply it inside the caller's restore transaction."""
    if type(validate_only) is not bool:
        _domain_error("validate_only debe ser booleano.")
    if owner is not None:
        owner = _owner_id(owner)
    if not isinstance(rows, list) or len(rows) > MAX_MAINTENANCE_PLANS_GLOBAL:
        _domain_error("maintenance_plans supera el límite global permitido.")
    owners: dict[str, int] = {}
    plan_ids: set[str] = set()
    plan_owners: dict[str, str] = {}
    history_ids: set[str] = set()
    history_count = 0
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != _ARCHIVE_FIELDS:
            _domain_error("Cada plan requiere exactamente los campos persistentes del archivo.")
        row_owner = _owner_id(row["owner_id"])
        if owner is not None and row_owner != owner:
            _domain_error("La importación contiene planes de otro usuario.", 409)
        owners[row_owner] = owners.get(row_owner, 0) + 1
        if owners[row_owner] > MAX_MAINTENANCE_PLANS_PER_OWNER:
            _domain_error("Un propietario supera el límite de 1000 planes.")
        plan_id = _valid_id(row["id"])
        if plan_id in plan_ids:
            _domain_error("maintenance_plans contiene ids duplicados.")
        plan_ids.add(plan_id)
        plan_owners[plan_id] = row_owner
        events = row["history"]
        if not isinstance(events, list) or len(events) > MAX_MAINTENANCE_COMPLETIONS_PER_PLAN:
            _domain_error("history supera el límite de 100 eventos por plan.")
        history_count += len(events)
        if history_count > MAX_MAINTENANCE_COMPLETIONS_GLOBAL:
            _domain_error("maintenance_plans supera el límite global de eventos.")
        for event in events:
            if not isinstance(event, Mapping) or set(event) != _HISTORY_FIELDS:
                _domain_error("Cada evento de mantenimiento requiere los campos persistentes.")
            event_id = _valid_id(event["id"], "history.id")
            if event_id in history_ids:
                _domain_error("history contiene ids duplicados.")
            history_ids.add(event_id)
    if owner is not None and any(row["owner_id"] != owner for row in rows):
        _domain_error("La importación contiene planes de otro usuario.", 409)
    current_plans_query = "SELECT id, owner_id FROM maintenance_plans"
    current_plans_args: tuple[Any, ...] = ()
    if owner is not None:
        current_plans_query += " WHERE owner_id = ?"
        current_plans_args = (owner,)
    invalidated_rows = connection.execute(
        current_plans_query, current_plans_args
    ).fetchall()
    invalidated_owners = {row["id"]: row["owner_id"] for row in invalidated_rows}

    # A plan ID is globally unique and remains tied to its original owner even
    # after archive replacement. This also blocks ownership transfer through a
    # full restore, which previously removed the evidence before checking it.
    check_ids = list(plan_ids | set(invalidated_owners))
    tombstone_owners: dict[str, str] = {}
    for start in range(0, len(check_ids), 500):
        chunk = check_ids[start:start + 500]
        placeholders = ",".join("?" for _ in chunk)
        current_rows = connection.execute(
            f"SELECT id, owner_id FROM maintenance_plans WHERE id IN ({placeholders})",
            chunk,
        ).fetchall()
        for current in current_rows:
            incoming_owner = plan_owners.get(current["id"])
            if incoming_owner is not None and current["owner_id"] != incoming_owner:
                _domain_error(
                    "La importación contiene un identificador de plan usado por otro usuario.",
                    409,
                )
        tombstones = connection.execute(
            f"""SELECT plan_id, owner_id FROM maintenance_plan_request_tombstones
                WHERE plan_id IN ({placeholders})""",
            chunk,
        ).fetchall()
        for tombstone in tombstones:
            plan_id = tombstone["plan_id"]
            tombstone_owners[plan_id] = tombstone["owner_id"]
            expected_owner = plan_owners.get(plan_id, invalidated_owners.get(plan_id))
            if expected_owner is not None and tombstone["owner_id"] != expected_owner:
                _domain_error(
                    "La importación contiene un identificador de plan usado por otro usuario.",
                    409,
                )

    pending_tombstones = [
        (plan_id, row_owner)
        for plan_id, row_owner in invalidated_owners.items()
        if plan_id not in tombstone_owners
    ]
    if pending_tombstones:
        existing_tombstones = int(connection.execute(
            "SELECT COUNT(*) FROM maintenance_plan_request_tombstones"
        ).fetchone()[0])
        if existing_tombstones + len(pending_tombstones) > MAX_INVALIDATED_MAINTENANCE_PLAN_IDS_GLOBAL:
            _domain_error("Se alcanzó el límite global de intentos de mantenimiento invalidados.", 429)
        additions_by_owner: dict[str, int] = {}
        for _, row_owner in pending_tombstones:
            additions_by_owner[row_owner] = additions_by_owner.get(row_owner, 0) + 1
        for row_owner, addition in additions_by_owner.items():
            existing_owner_tombstones = int(connection.execute(
                """SELECT COUNT(*) FROM maintenance_plan_request_tombstones
                   WHERE owner_id = ?""",
                (row_owner,),
            ).fetchone()[0])
            if existing_owner_tombstones + addition > MAX_INVALIDATED_MAINTENANCE_PLAN_IDS_PER_OWNER:
                _domain_error(
                    "Se alcanzó el límite de intentos de mantenimiento invalidados para este propietario.",
                    429,
                )
    if owner is not None:
        if history_ids:
            ids = list(history_ids)
            for start in range(0, len(ids), 500):
                chunk = ids[start:start + 500]
                placeholders = ",".join("?" for _ in chunk)
                conflict = connection.execute(
                    f"""SELECT id FROM maintenance_completions
                        WHERE owner_id != ? AND id IN ({placeholders}) LIMIT 1""",
                    (owner, *chunk),
                ).fetchone()
                if conflict is not None:
                    _domain_error(
                        "La importación contiene un identificador de historial usado por otro usuario.",
                        409,
                    )
        existing_global = int(connection.execute(
            "SELECT COUNT(*) FROM maintenance_plans"
        ).fetchone()[0])
        existing_owner = int(connection.execute(
            "SELECT COUNT(*) FROM maintenance_plans WHERE owner_id = ?", (owner,)
        ).fetchone()[0])
        if existing_global - existing_owner + len(rows) > MAX_MAINTENANCE_PLANS_GLOBAL:
            _domain_error("Se alcanzó el límite global de planes de mantenimiento.", 429)
        external_history = int(connection.execute(
            "SELECT COUNT(*) FROM maintenance_completions WHERE owner_id != ?", (owner,)
        ).fetchone()[0])
        if external_history + history_count > MAX_MAINTENANCE_COMPLETIONS_GLOBAL:
            _domain_error("Se alcanzó el límite global de eventos de mantenimiento.", 429)
    else:
        if len(rows) > MAX_MAINTENANCE_PLANS_GLOBAL:
            _domain_error("Se alcanzó el límite global de planes de mantenimiento.", 429)

    if validate_only:
        return

    # Write invalidations and replace rows in the caller's restore transaction.
    # If any later insert fails, the caller rolls all three tables back together.
    if pending_tombstones:
        connection.executemany(
            """INSERT INTO maintenance_plan_request_tombstones (plan_id, owner_id)
               VALUES (?, ?)""",
            pending_tombstones,
        )
    if owner is not None:
        connection.execute("DELETE FROM maintenance_completions WHERE owner_id = ?", (owner,))
        connection.execute("DELETE FROM maintenance_plans WHERE owner_id = ?", (owner,))
    else:
        connection.execute("DELETE FROM maintenance_completions")
        connection.execute("DELETE FROM maintenance_plans")
    for row in rows:
        connection.execute(
            """INSERT INTO maintenance_plans
               (id, owner_id, revision, source, title, interval_months, interval_km,
                last_date, last_odometer_km, notes, created_at, updated_at, deleted_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["id"], row["owner_id"], row["revision"], row["source"],
                row["title"], row["interval_months"], row["interval_km"],
                row["last_date"], row["last_odometer_km"], row["notes"],
                row["created_at"], row["updated_at"], row["deleted_at"],
            ),
        )
        for event in row["history"]:
            connection.execute(
                """INSERT INTO maintenance_completions
                   (id, plan_id, owner_id, performed_date, odometer_km, cost_eur, recorded_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    event["id"], row["id"], row["owner_id"], event["performed_date"],
                    event["odometer_km"], event["cost_eur"], event["recorded_at"],
                ),
            )


class MaintenancePlanStoreMixin:
    """SQLite operations for manually maintained service plans and completion history."""

    def list_maintenance_plans(
        self,
        owner_id: str,
        limit: int = 50,
        offset: int = 0,
        odometer_km: int | None = None,
    ) -> dict[str, Any]:
        owner = _owner_id(owner_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            _domain_error("limit debe estar entre 1 y 100.")
        if type(offset) is not int or not 0 <= offset <= 1_000_000:
            _domain_error("offset debe estar entre 0 y 1000000.")
        try:
            odometer = _nullable_int(odometer_km, "odometer_km", 2_000_000)
        except MaintenancePlanValidationError as exc:
            _domain_error(str(exc))
        today = self._clock_now().astimezone(timezone.utc).date()
        with self._read_transaction() as connection:
            rows = connection.execute(
                """SELECT * FROM maintenance_plans
                   WHERE owner_id = ? AND deleted_at IS NULL
                   ORDER BY title COLLATE NOCASE, id LIMIT ? OFFSET ?""",
                (owner, limit, offset),
            ).fetchall()
            total = int(connection.execute(
                "SELECT COUNT(*) FROM maintenance_plans WHERE owner_id = ? AND deleted_at IS NULL",
                (owner,),
            ).fetchone()[0])
            grouped: dict[str, list[dict[str, Any]]] = {}
            plan_ids = [row["id"] for row in rows]
            if plan_ids:
                placeholders = ",".join("?" for _ in plan_ids)
                histories = connection.execute(
                    f"""SELECT * FROM maintenance_completions
                        WHERE owner_id = ? AND plan_id IN ({placeholders})
                        ORDER BY plan_id, rowid""",
                    (owner, *plan_ids),
                ).fetchall()
                for event in histories:
                    grouped.setdefault(event["plan_id"], []).append(_history_item(event))
            items = [
                _maintenance_plan_item(
                    row, grouped.get(row["id"], []), today=today, odometer_km=odometer
                )
                for row in rows
            ]
            return {"items": items, "total": total}

    def create_maintenance_plan(
        self,
        owner_id: str,
        values: Mapping[str, Any],
        request_id: str | None = None,
    ) -> dict[str, Any]:
        owner = _owner_id(owner_id)
        if request_id is None:
            plan_id = uuid.uuid4().hex
        else:
            try:
                plan_id = _valid_id(request_id, "request_id")
            except MaintenancePlanValidationError as exc:
                _domain_error(str(exc))
        now_value = self._clock_now()
        try:
            data = validate_maintenance_plan_values(values, now=now_value)
        except MaintenancePlanValidationError as exc:
            _domain_error(str(exc))
        fingerprint = attempt_fingerprint(data)
        now = _format_datetime(now_value)
        created: dict[str, Any] | None = None
        capacity_reached = False
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM maintenance_plans WHERE id = ?", (plan_id,)
            ).fetchone()
            tombstone = connection.execute(
                """SELECT owner_id FROM maintenance_plan_request_tombstones
                   WHERE plan_id = ?""",
                (plan_id,),
            ).fetchone()
            if existing is not None and existing["owner_id"] != owner:
                _domain_error("No se encontró el plan de mantenimiento solicitado.", 404)
            if tombstone is not None:
                if tombstone["owner_id"] != owner:
                    _domain_error("No se encontró el plan de mantenimiento solicitado.", 404)
                _domain_error(
                    "El intento de creación fue invalidado por una restauración anterior.", 409
                )
            if existing is not None:
                if existing["deleted_at"] is not None:
                    _domain_error("El intento de creación corresponde a un plan archivado.", 409)
                if existing["creation_fingerprint"] is None:
                    _domain_error(
                        "No se puede confirmar este intento con el plan restaurado. Revisa la lista antes de crear otro.",
                        409,
                    )
                if existing["creation_fingerprint"] != fingerprint:
                    _domain_error(
                        "request_id ya se utilizó con otros datos de creación.", 409
                    )
                history_rows = connection.execute(
                    """SELECT * FROM maintenance_completions
                       WHERE owner_id = ? AND plan_id = ? ORDER BY rowid""",
                    (owner, plan_id),
                ).fetchall()
                return _maintenance_plan_item(
                    existing, [_history_item(event) for event in history_rows],
                    today=now_value.astimezone(timezone.utc).date(), odometer_km=None,
                )
            owner_count = int(connection.execute(
                "SELECT COUNT(*) FROM maintenance_plans WHERE owner_id = ?", (owner,)
            ).fetchone()[0])
            global_count = int(connection.execute(
                "SELECT COUNT(*) FROM maintenance_plans"
            ).fetchone()[0])
            if owner_count >= MAX_MAINTENANCE_PLANS_PER_OWNER or global_count >= MAX_MAINTENANCE_PLANS_GLOBAL:
                capacity_reached = True
            else:
                connection.execute(
                    """INSERT INTO maintenance_plans
                       (id, owner_id, revision, source, title, interval_months, interval_km,
                        last_date, last_odometer_km, notes, creation_fingerprint,
                        created_at, updated_at)
                       VALUES (?, ?, 1, 'manual', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        plan_id, owner, data["title"], data["interval_months"],
                        data["interval_km"], data["last_date"], data["last_odometer_km"],
                        data["notes"], fingerprint, now, now,
                    ),
                )
                self._touch(connection)
                row = connection.execute(
                    "SELECT * FROM maintenance_plans WHERE id = ? AND owner_id = ?",
                    (plan_id, owner),
                ).fetchone()
                created = _maintenance_plan_item(
                    row, [], today=now_value.astimezone(timezone.utc).date(), odometer_km=None
                )
        if capacity_reached:
            _domain_error("Se alcanzó el límite de planes de mantenimiento.", 429)
        if created is None:
            _domain_error("No se pudo guardar el plan de mantenimiento.", 500)
        return created

    def update_maintenance_plan(
        self, owner_id: str, plan_id: str, revision: int, values: Mapping[str, Any]
    ) -> dict[str, Any]:
        owner = _owner_id(owner_id)
        if not isinstance(plan_id, str) or _ID_RE.fullmatch(plan_id) is None:
            _domain_error("El identificador de plan no es válido.", 404)
        if type(revision) is not int or not 1 <= revision <= MAX_MAINTENANCE_PLAN_REVISION:
            _domain_error("revision debe ser un entero positivo válido.")
        now_value = self._clock_now()
        try:
            data = validate_maintenance_plan_values(values, now=now_value)
        except MaintenancePlanValidationError as exc:
            _domain_error(str(exc))
        with self._transaction() as connection:
            row = connection.execute(
                """SELECT * FROM maintenance_plans
                   WHERE id = ? AND owner_id = ? AND deleted_at IS NULL""",
                (plan_id, owner),
            ).fetchone()
            if row is None:
                _domain_error("No se encontró el plan de mantenimiento solicitado.", 404)
            if row["revision"] != revision:
                _domain_error("El plan cambió desde que lo abriste.", 409)
            if revision >= MAX_MAINTENANCE_PLAN_REVISION:
                _domain_error("El plan alcanzó la revisión máxima y no puede editarse.", 409)
            updated_at = _format_datetime(now_value)
            cursor = connection.execute(
                """UPDATE maintenance_plans SET revision = revision + 1,
                   title = ?, interval_months = ?, interval_km = ?, last_date = ?,
                   last_odometer_km = ?, notes = ?, updated_at = ?
                   WHERE id = ? AND owner_id = ? AND revision = ? AND deleted_at IS NULL""",
                (
                    data["title"], data["interval_months"], data["interval_km"],
                    data["last_date"], data["last_odometer_km"], data["notes"],
                    updated_at, plan_id, owner, revision,
                ),
            )
            if cursor.rowcount != 1:
                _domain_error("El plan cambió desde que lo abriste.", 409)
            self._touch(connection)
            updated = connection.execute(
                "SELECT * FROM maintenance_plans WHERE id = ? AND owner_id = ?",
                (plan_id, owner),
            ).fetchone()
            history_rows = connection.execute(
                """SELECT * FROM maintenance_completions
                   WHERE owner_id = ? AND plan_id = ? ORDER BY rowid""",
                (owner, plan_id),
            ).fetchall()
            return _maintenance_plan_item(
                updated, [_history_item(event) for event in history_rows],
                today=now_value.astimezone(timezone.utc).date(), odometer_km=None,
            )

    def delete_maintenance_plan(self, owner_id: str, plan_id: str, revision: int) -> dict[str, bool]:
        owner = _owner_id(owner_id)
        if not isinstance(plan_id, str) or _ID_RE.fullmatch(plan_id) is None:
            _domain_error("El identificador de plan no es válido.", 404)
        if type(revision) is not int or not 1 <= revision <= MAX_MAINTENANCE_PLAN_REVISION:
            _domain_error("revision debe ser un entero positivo válido.")
        now = _format_datetime(self._clock_now())
        with self._transaction() as connection:
            row = connection.execute(
                """SELECT revision FROM maintenance_plans
                   WHERE id = ? AND owner_id = ? AND deleted_at IS NULL""",
                (plan_id, owner),
            ).fetchone()
            if row is None:
                _domain_error("No se encontró el plan de mantenimiento solicitado.", 404)
            if row["revision"] != revision:
                _domain_error("El plan cambió desde que lo abriste.", 409)
            if revision >= MAX_MAINTENANCE_PLAN_REVISION:
                _domain_error("El plan alcanzó la revisión máxima y no puede borrarse.", 409)
            cursor = connection.execute(
                """UPDATE maintenance_plans SET revision = revision + 1,
                   updated_at = ?, deleted_at = ?
                   WHERE id = ? AND owner_id = ? AND revision = ? AND deleted_at IS NULL""",
                (now, now, plan_id, owner, revision),
            )
            if cursor.rowcount != 1:
                _domain_error("El plan cambió desde que lo abriste.", 409)
            self._touch(connection)
        return {"ok": True}

    def complete_maintenance_plan(
        self,
        owner_id: str,
        plan_id: str,
        revision: int,
        performed_date: str,
        odometer_km: int | None,
        cost_eur: float | None,
    ) -> dict[str, Any]:
        owner = _owner_id(owner_id)
        if not isinstance(plan_id, str) or _ID_RE.fullmatch(plan_id) is None:
            _domain_error("El identificador de plan no es válido.", 404)
        if type(revision) is not int or not 1 <= revision <= MAX_MAINTENANCE_PLAN_REVISION:
            _domain_error("revision debe ser un entero positivo válido.")
        now_value = self._clock_now()
        try:
            performed = _date(performed_date, "performed_date", nullable=False)
            if performed is None or performed > now_value.astimezone(timezone.utc).date():
                raise MaintenancePlanValidationError("performed_date no puede estar en el futuro.")
            odometer = _nullable_int(odometer_km, "odometer_km", 2_000_000)
            cost = _cost(cost_eur)
        except MaintenancePlanValidationError as exc:
            _domain_error(str(exc))
        now = _format_datetime(now_value)
        with self._transaction() as connection:
            row = connection.execute(
                """SELECT * FROM maintenance_plans
                   WHERE id = ? AND owner_id = ? AND deleted_at IS NULL""",
                (plan_id, owner),
            ).fetchone()
            if row is None:
                _domain_error("No se encontró el plan de mantenimiento solicitado.", 404)
            if row["revision"] != revision:
                _domain_error("El plan cambió desde que lo abriste.", 409)
            if revision >= MAX_MAINTENANCE_PLAN_REVISION:
                _domain_error("El plan alcanzó la revisión máxima y no puede completarse.", 409)
            if row["interval_km"] is not None and odometer is None:
                _domain_error(
                    "Completar un plan por kilómetros requiere indicar el odómetro manual.",
                )
            if row["last_odometer_km"] is not None and odometer is not None and odometer < row["last_odometer_km"]:
                _domain_error("El odómetro no puede disminuir al completar un plan.")
            if row["last_date"] is not None and performed.isoformat() < row["last_date"]:
                _domain_error("performed_date no puede preceder a la última base del plan.")
            plan_history_count = int(connection.execute(
                "SELECT COUNT(*) FROM maintenance_completions WHERE plan_id = ? AND owner_id = ?",
                (plan_id, owner),
            ).fetchone()[0])
            global_history_count = int(connection.execute(
                "SELECT COUNT(*) FROM maintenance_completions"
            ).fetchone()[0])
            if (
                plan_history_count >= MAX_MAINTENANCE_COMPLETIONS_PER_PLAN
                or global_history_count >= MAX_MAINTENANCE_COMPLETIONS_GLOBAL
            ):
                _domain_error("Se alcanzó el límite de eventos de mantenimiento.", 429)
            completion_id = uuid.uuid4().hex
            new_odometer_base = (
                odometer if odometer is not None else row["last_odometer_km"]
            )
            cursor = connection.execute(
                """UPDATE maintenance_plans SET revision = revision + 1,
                   last_date = ?, last_odometer_km = ?, updated_at = ?
                   WHERE id = ? AND owner_id = ? AND revision = ? AND deleted_at IS NULL""",
                (performed.isoformat(), new_odometer_base, now, plan_id, owner, revision),
            )
            if cursor.rowcount != 1:
                _domain_error("El plan cambió desde que lo abriste.", 409)
            connection.execute(
                """INSERT INTO maintenance_completions
                   (id, plan_id, owner_id, performed_date, odometer_km, cost_eur, recorded_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (completion_id, plan_id, owner, performed.isoformat(), odometer, cost, now),
            )
            self._touch(connection)
            updated = connection.execute(
                "SELECT * FROM maintenance_plans WHERE id = ? AND owner_id = ?",
                (plan_id, owner),
            ).fetchone()
            history_rows = connection.execute(
                """SELECT * FROM maintenance_completions
                   WHERE owner_id = ? AND plan_id = ? ORDER BY rowid""",
                (owner, plan_id),
            ).fetchall()
            return _maintenance_plan_item(
                updated, [_history_item(event) for event in history_rows],
                today=now_value.astimezone(timezone.utc).date(), odometer_km=None,
            )

    def export_maintenance_plans(
        self, connection: sqlite3.Connection, owner_id: str | None
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM maintenance_plans"
        args: tuple[Any, ...] = ()
        if owner_id is not None:
            owner_id = _owner_id(owner_id)
            query += " WHERE owner_id = ?"
            args = (owner_id,)
        query += " ORDER BY owner_id, created_at, id"
        plans = connection.execute(query, args).fetchall()
        if owner_id is None and plans:
            _domain_error(
                "La exportación global de planes requiere un propietario autenticado.", 403
            )
        history_count_query = "SELECT COUNT(*) FROM maintenance_completions"
        history_count_args: tuple[Any, ...] = ()
        if owner_id is not None:
            history_count_query += " WHERE owner_id = ?"
            history_count_args = (owner_id,)
        history_count = int(connection.execute(
            history_count_query, history_count_args
        ).fetchone()[0])
        if owner_id is None and history_count:
            _domain_error(
                "La exportación global del historial requiere un propietario autenticado.", 403
            )
        if len(plans) > MAX_MAINTENANCE_PLANS_PER_OWNER and owner_id is not None:
            _domain_error("El archivo supera el límite de 1000 planes por propietario.", 500)
        if history_count > MAX_MAINTENANCE_COMPLETIONS_GLOBAL:
            _domain_error("El historial supera el límite de eventos de mantenimiento.", 500)
        grouped: dict[str, list[dict[str, Any]]] = {}
        plan_ids = [row["id"] for row in plans]
        for start in range(0, len(plan_ids), 500):
            chunk = plan_ids[start:start + 500]
            placeholders = ",".join("?" for _ in chunk)
            events = connection.execute(
                f"""SELECT * FROM maintenance_completions
                    WHERE owner_id = ? AND plan_id IN ({placeholders})
                    ORDER BY plan_id, rowid""",
                (owner_id, *chunk),
            ).fetchall()
            for event in events:
                grouped.setdefault(event["plan_id"], []).append(_history_item(event))
        result: list[dict[str, Any]] = []
        for row in plans:
            item = _persistent_plan_row(row)
            item["owner_id"] = row["owner_id"]
            item["history"] = grouped.get(row["id"], [])
            result.append(item)
        return result

    def replace_maintenance_plans(
        self,
        connection: sqlite3.Connection,
        owner: str | None,
        rows: list[dict[str, Any]],
    ) -> None:
        """Replace plans inside the caller's already-open restore transaction."""
        replace_maintenance_plans(connection, owner, rows)

    def validate_maintenance_plan_replacement(
        self,
        connection: sqlite3.Connection,
        owner: str | None,
        rows: list[dict[str, Any]],
    ) -> None:
        """Run all restore preflight checks without changing the database."""
        replace_maintenance_plans(connection, owner, rows, validate_only=True)
