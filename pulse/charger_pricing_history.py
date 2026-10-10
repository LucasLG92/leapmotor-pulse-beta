"""Bounded audit history for PVPC quotes attached to closed evcc sessions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re
import sqlite3
from typing import Any, Mapping
import uuid

from pulse.charger_store import (
    FUTURE_TOLERANCE_SECONDS,
    ChargerDataError,
    _parse_utc,
    _session_from_row,
    validate_charger_archive,
    validate_scope,
    validate_session,
)


MAX_CHARGER_PRICING_HISTORY = 2_048
_HISTORY_SESSION_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
_HISTORY_EVENT_FIELDS = frozenset({
    "id", "scope", "kind", "origin", "recorded_at", "session", "quote",
    "corrected_session",
})
_HISTORY_KINDS = frozenset({"priced", "baseline", "invalidated"})
_HISTORY_ORIGINS = frozenset({"pulse", "migration", "archive"})
_STABLE_SESSION_FIELDS = ("id", "upstream_id", "loadpoint", "started_at")


def validate_charger_pricing_history_archive(
    value: Any, charger: Any
) -> list[dict[str, Any]]:
    """Validate bounded history and its stable references to current sessions."""
    if not isinstance(value, list) or len(value) > MAX_CHARGER_PRICING_HISTORY:
        raise ChargerDataError("charger_pricing_history supera el límite de 2048 eventos.")
    normalized_charger = validate_charger_archive(charger)
    references = _session_references(normalized_charger)
    seen_ids: set[str] = set()
    events: list[dict[str, Any]] = []
    for raw in value:
        event = _validate_history_event(raw, references=references)
        if event["id"] in seen_ids:
            raise ChargerDataError("charger_pricing_history contiene ids duplicados.")
        seen_ids.add(event["id"])
        events.append(event)
    return events


def validate_charger_pricing_history_future_dates(
    events: Any, now: datetime
) -> None:
    """Reject history timestamps more than 60 seconds beyond Store's clock."""
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ChargerDataError("El reloj de Store debe devolver una fecha con zona horaria.")
    if not isinstance(events, list) or len(events) > MAX_CHARGER_PRICING_HISTORY:
        raise ChargerDataError("charger_pricing_history supera el límite de 2048 eventos.")
    limit = now.astimezone(timezone.utc) + timedelta(seconds=FUTURE_TOLERANCE_SECONDS)
    for raw in events:
        event = _validate_history_event(raw)
        timestamps = [event["recorded_at"]]
        for snapshot in (event["session"], event["corrected_session"]):
            if snapshot is not None:
                timestamps.extend(
                    snapshot[field] for field in ("started_at", "ended_at", "received_at")
                )
        pricing = event["quote"]["pricing"]
        timestamps.append(pricing["calculated_at"])
        # price_fetched_at is the persisted quote's PVPC download timestamp.
        timestamps.extend(interval["price_fetched_at"] for interval in pricing["intervals"])
        for timestamp in timestamps:
            if _parse_utc(timestamp) > limit:
                raise ChargerDataError(
                    "charger_pricing_history contiene una fecha que supera en más de "
                    "60 segundos el reloj de Store."
                )


class ChargerPricingHistoryStoreMixin:
    """SQLite history methods composed into the domain Store."""

    def _migrate_v12_to_v13(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """CREATE TABLE charger_pricing_history (
                ordinal INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT NOT NULL UNIQUE CHECK (
                    length(id) = 32 AND id NOT GLOB '*[^0-9a-f]*'
                ),
                session_id TEXT NOT NULL
                    REFERENCES charger_sessions(id) ON DELETE CASCADE,
                event_json TEXT NOT NULL CHECK (json_valid(event_json))
            )"""
        )
        connection.execute(
            "CREATE INDEX idx_charger_pricing_history_session_ordinal "
            "ON charger_pricing_history(session_id, ordinal DESC)"
        )
        self._baseline_charger_pricing_history(connection, origin="migration")

    def _append_charger_pricing_event(
        self,
        connection: sqlite3.Connection,
        kind: str,
        scope: Any,
        session: Any,
        quote: Any,
        corrected_session: Any = None,
        origin: str = "pulse",
    ) -> dict[str, Any]:
        """Validate and append one event inside the caller's transaction."""
        event = _make_history_event(
            kind=kind,
            scope=scope,
            session=session,
            quote=quote,
            corrected_session=corrected_session,
            origin=origin,
            recorded_at=self._now(),
        )
        current = connection.execute(
            "SELECT * FROM charger_sessions WHERE id = ?", (event["session"]["id"],)
        ).fetchone()
        if current is None or current["scope"] != event["scope"]:
            raise _history_store_error("La sesión del evento ya no está disponible.", 409)
        current_session = _session_from_row(current)
        if not _same_stable_identity(event["session"], current_session):
            raise _history_store_error("La identidad de sesión del evento no coincide.", 409)
        if (
            current_session["ended_at"] != event["session"]["ended_at"]
            or current_session["kwh"] != event["session"]["kwh"]
        ):
            raise _history_store_error("La sesión cambió antes de registrar el evento.", 409)
        event_json = _encode_event(event)
        connection.execute(
            "INSERT INTO charger_pricing_history (id, session_id, event_json) VALUES (?, ?, ?)",
            (event["id"], event["session"]["id"], event_json),
        )
        connection.execute(
            """DELETE FROM charger_pricing_history WHERE ordinal IN (
                   SELECT ordinal FROM charger_pricing_history
                   ORDER BY ordinal DESC LIMIT -1 OFFSET ?
               )""",
            (MAX_CHARGER_PRICING_HISTORY,),
        )
        return event

    def _baseline_charger_pricing_history(
        self, connection: sqlite3.Connection, origin: str = "migration"
    ) -> None:
        if origin not in ("migration", "archive"):
            raise _history_store_error("La procedencia del baseline no es válida.")
        from pulse.charger_pricing_store import _verified_stored_quote

        rows = connection.execute(
            """SELECT sessions.*, pricing.quote_json
               FROM charger_pricing AS pricing
               JOIN charger_sessions AS sessions ON sessions.id = pricing.session_id
               ORDER BY pricing.session_id"""
        ).fetchall()
        for row in rows:
            session = _session_from_row(row)
            quote = _verified_stored_quote(row["quote_json"], session)
            self._append_charger_pricing_event(
                connection,
                "baseline",
                row["scope"],
                session,
                quote,
                origin=origin,
            )

    def _record_charger_pricing_correction(
        self,
        connection: sqlite3.Connection,
        scope: Any,
        current_row: sqlite3.Row,
        new_session: Any,
    ) -> bool:
        """Preserve the quote before the existing correction trigger removes it."""
        normalized_scope = _validated_history_scope(scope)
        try:
            previous = validate_session(_session_from_row(current_row), normalized_scope)
            corrected = validate_session(new_session, normalized_scope)
        except ChargerDataError as error:
            raise _history_store_error(str(error)) from None
        if current_row["scope"] != normalized_scope or not _same_stable_identity(
            previous, corrected
        ):
            raise _history_store_error("La corrección no coincide con la identidad de sesión.")
        if (
            corrected["received_at"] <= previous["received_at"]
            or (
                corrected["ended_at"] == previous["ended_at"]
                and corrected["kwh"] == previous["kwh"]
            )
        ):
            return False
        quote_row = connection.execute(
            "SELECT quote_json FROM charger_pricing WHERE session_id = ?",
            (previous["id"],),
        ).fetchone()
        if quote_row is None:
            return False
        from pulse.charger_pricing_store import _verified_stored_quote

        quote = _verified_stored_quote(quote_row["quote_json"], previous)
        self._append_charger_pricing_event(
            connection,
            "invalidated",
            normalized_scope,
            previous,
            quote,
            corrected_session=corrected,
            origin="pulse",
        )
        return True

    def charger_pricing_history(
        self, scope: Any, session_id: Any, limit: Any = 10, offset: Any = 0
    ) -> dict[str, Any]:
        normalized_scope = _validated_history_scope(scope)
        if not isinstance(session_id, str) or not _HISTORY_SESSION_ID_RE.fullmatch(session_id):
            raise _history_store_error("id de sesión no válido.")
        page_limit = _history_bounded_int(limit, "limit", 1, 20)
        page_offset = _history_bounded_int(offset, "offset", 0, MAX_CHARGER_PRICING_HISTORY)
        with self._read_transaction() as connection:
            current = connection.execute(
                "SELECT * FROM charger_sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if current is None or current["scope"] != normalized_scope:
                raise _history_store_error("La sesión no existe.", 404)
            current_session = _session_from_row(current)
            references = {session_id: (normalized_scope, current_session)}
            total = int(connection.execute(
                "SELECT COUNT(*) FROM charger_pricing_history WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0])
            rows = connection.execute(
                """SELECT id, session_id, event_json FROM charger_pricing_history
                   WHERE session_id = ? ORDER BY ordinal DESC LIMIT ? OFFSET ?""",
                (session_id, page_limit, page_offset),
            ).fetchall()
            items = []
            for row in rows:
                event = _read_stored_history_event(row, references)
                items.append(_history_api_item(event))
            return {
                "session_id": session_id,
                "items": items,
                "total": total,
                "limit": page_limit,
                "offset": page_offset,
                "retention_limit": MAX_CHARGER_PRICING_HISTORY,
            }

    def _export_charger_pricing_history(
        self, connection: sqlite3.Connection
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            """SELECT ordinal, id, session_id, event_json FROM charger_pricing_history
               ORDER BY ordinal ASC"""
        ).fetchall()
        if len(rows) > MAX_CHARGER_PRICING_HISTORY:
            raise _history_store_error("El historial supera el límite de 2048 eventos.", 500)
        raw_events: list[dict[str, Any]] = []
        for row in rows:
            event = _decode_event(row["event_json"])
            if event.get("id") != row["id"] or not isinstance(event.get("session"), Mapping) \
                    or event["session"].get("id") != row["session_id"]:
                raise _history_store_error("El ledger contiene referencias inconsistentes.", 500)
            raw_events.append(event)
        try:
            return validate_charger_pricing_history_archive(
                raw_events, self._export_charger(connection)
            )
        except ChargerDataError:
            raise _history_store_error("El ledger contiene un evento no válido.", 500) from None

    def _replace_charger_pricing_history_archive(
        self, connection: sqlite3.Connection, events: list[dict[str, Any]]
    ) -> None:
        try:
            normalized_events = validate_charger_pricing_history_archive(
                events, self._export_charger(connection)
            )
        except ChargerDataError as error:
            raise _history_store_error(str(error)) from None
        connection.execute("DELETE FROM charger_pricing_history")
        for event in normalized_events:
            connection.execute(
                """INSERT INTO charger_pricing_history (id, session_id, event_json)
                   VALUES (?, ?, ?)""",
                (event["id"], event["session"]["id"], _encode_event(event)),
            )


def _validate_history_event(
    raw: Any,
    *,
    references: Mapping[str, tuple[str, Mapping[str, Any]]] | None = None,
) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or set(raw) != _HISTORY_EVENT_FIELDS:
        raise ChargerDataError("Cada evento requiere los campos exactos de historial.")
    event_id = raw["id"]
    if not isinstance(event_id, str) or not _HISTORY_SESSION_ID_RE.fullmatch(event_id):
        raise ChargerDataError("El id del evento debe ser hexadecimal de 32 caracteres.")
    try:
        scope = validate_scope(raw["scope"])
    except ChargerDataError as error:
        raise ChargerDataError(f"scope del historial no válido: {error}") from None
    kind = raw["kind"]
    origin = raw["origin"]
    if (
        not isinstance(kind, str)
        or not isinstance(origin, str)
        or kind not in _HISTORY_KINDS
        or origin not in _HISTORY_ORIGINS
    ):
        raise ChargerDataError("kind u origin del historial no válido.")
    if (kind == "priced" and origin != "pulse") or (
        kind == "baseline" and origin not in ("migration", "archive")
    ) or (kind == "invalidated" and origin != "pulse"):
        raise ChargerDataError("kind y origin del historial no son compatibles.")
    recorded_at = _history_datetime(raw["recorded_at"], "recorded_at")
    try:
        session = validate_session(raw["session"], scope)
    except ChargerDataError as error:
        raise ChargerDataError(f"La sesión histórica no es válida: {error}") from None
    quote = _history_quote(raw["quote"], session)
    corrected_raw = raw["corrected_session"]
    corrected: dict[str, Any] | None
    if kind == "invalidated":
        try:
            corrected = validate_session(corrected_raw, scope)
        except ChargerDataError as error:
            raise ChargerDataError(f"La sesión corregida no es válida: {error}") from None
        if not _same_stable_identity(session, corrected):
            raise ChargerDataError("La sesión corregida cambia la identidad estable.")
        if (
            corrected["ended_at"] == session["ended_at"]
            and corrected["kwh"] == session["kwh"]
        ):
            raise ChargerDataError("Una invalidación debe cambiar ended_at o kwh.")
        if corrected["received_at"] <= session["received_at"]:
            raise ChargerDataError("La corrección debe tener received_at posterior.")
    else:
        if corrected_raw is not None:
            raise ChargerDataError("Solo invalidated puede incluir corrected_session.")
        corrected = None
    if references is not None:
        reference = references.get(session["id"])
        if reference is None:
            raise ChargerDataError("El evento referencia una sesión ausente de charger.")
        reference_scope, current_session = reference
        if reference_scope != scope or not _same_stable_identity(session, current_session):
            raise ChargerDataError("El evento no coincide con la identidad estable de charger.")
        if corrected is not None and not _same_stable_identity(corrected, current_session):
            raise ChargerDataError("La corrección no coincide con la identidad estable de charger.")
    return {
        "id": event_id,
        "scope": scope,
        "kind": kind,
        "origin": origin,
        "recorded_at": recorded_at,
        "session": session,
        "quote": quote,
        "corrected_session": corrected,
    }


def _make_history_event(
    *, kind: str, scope: Any, session: Any, quote: Any,
    corrected_session: Any, origin: str, recorded_at: Any,
) -> dict[str, Any]:
    raw = {
        "id": uuid.uuid4().hex,
        "scope": scope,
        "kind": kind,
        "origin": origin,
        "recorded_at": recorded_at,
        "session": session,
        "quote": quote,
        "corrected_session": corrected_session,
    }
    return _validate_history_event(raw)


def _session_references(
    charger: list[dict[str, Any]],
) -> dict[str, tuple[str, Mapping[str, Any]]]:
    return {
        session["id"]: (scope_row["scope"], session)
        for scope_row in charger
        for session in scope_row["sessions"]
    }


def _history_quote(value: Any, session: Mapping[str, Any]) -> dict[str, Any]:
    try:
        from pulse.charger_pricing_store import _validate_quote
        return _validate_quote(value, session)
    except Exception as error:
        from pulse.domain import DomainError

        if isinstance(
            error,
            (DomainError, TypeError, ValueError, KeyError, AttributeError, RecursionError),
        ):
            raise ChargerDataError("La cotización PVPC del historial no es válida.") from None
        raise


def _history_datetime(value: Any, name: str) -> str:
    from pulse.charger_store import _datetime

    return _datetime(value, name)


def _same_stable_identity(
    left: Mapping[str, Any], right: Mapping[str, Any]
) -> bool:
    return all(left[field] == right[field] for field in _STABLE_SESSION_FIELDS)


def _decode_event(value: str) -> dict[str, Any]:
    try:
        decoded = json.loads(
            value,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_unique_object_pairs,
        )
    except (TypeError, ValueError, json.JSONDecodeError, RecursionError):
        raise _history_store_error("El ledger contiene JSON no válido.", 500) from None
    if not isinstance(decoded, dict):
        raise _history_store_error("El ledger contiene un evento no válido.", 500)
    return decoded


def _read_stored_history_event(
    row: sqlite3.Row,
    references: Mapping[str, tuple[str, Mapping[str, Any]]],
) -> dict[str, Any]:
    raw = _decode_event(row["event_json"])
    if raw.get("id") != row["id"] or not isinstance(raw.get("session"), Mapping) \
            or raw["session"].get("id") != row["session_id"]:
        raise _history_store_error("El ledger contiene referencias inconsistentes.", 500)
    try:
        return _validate_history_event(raw, references=references)
    except ChargerDataError:
        raise _history_store_error("El ledger contiene un evento no válido.", 500) from None


def _history_api_item(event: Mapping[str, Any]) -> dict[str, Any]:
    session = event["session"]
    corrected = event["corrected_session"]
    return {
        "id": event["id"],
        "kind": event["kind"],
        "recorded_at": event["recorded_at"],
        "session": {
            "started_at": session["started_at"],
            "ended_at": session["ended_at"],
            "kwh": session["kwh"],
        },
        "quote": event["quote"],
        "corrected_session": None if corrected is None else {
            "started_at": corrected["started_at"],
            "ended_at": corrected["ended_at"],
            "kwh": corrected["kwh"],
        },
    }


def _encode_event(event: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            event,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError, UnicodeEncodeError):
        raise _history_store_error("El evento no se pudo guardar.") from None


def _validated_history_scope(scope: Any) -> str:
    try:
        return validate_scope(scope)
    except ChargerDataError as error:
        raise _history_store_error(str(error)) from None


def _history_bounded_int(value: Any, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise _history_store_error(f"{name} está fuera de rango.")
    return value


def _reject_json_constant(value: str) -> Any:
    raise ValueError(f"Constante JSON no admitida: {value}")


def _unique_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Campo JSON duplicado.")
        result[key] = value
    return result


def _history_store_error(message: str, status: int = 400) -> Exception:
    from pulse.domain import DomainError

    return DomainError(message, status)
