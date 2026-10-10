"""Persistent PVPC quotes for normalized, closed evcc sessions."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Mapping

from pulse.charger_store import (
    MAX_CHARGER_PAGE_SIZE,
    MAX_CHARGER_SESSIONS,
    ChargerDataError,
    _session_from_row,
    validate_scope,
    validate_session,
)


MAX_CHARGER_PRICING_QUOTES = MAX_CHARGER_SESSIONS
_SESSION_ID_LENGTH = 32
_MAX_PVPC_SECONDS = 72 * 60 * 60


def validate_charger_pricing_archive(
    value: Any, charger: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Validate quote references and recompute each quote against its session."""
    if not isinstance(value, list) or len(value) > MAX_CHARGER_PRICING_QUOTES:
        raise ChargerDataError("charger_pricing debe contener como máximo 1000 snapshots.")

    sessions = {
        session["id"]: session
        for scope_row in charger
        for session in scope_row["sessions"]
    }
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, Mapping) or set(raw) != {"session_id", "quote"}:
            raise ChargerDataError("Cada snapshot charger_pricing requiere session_id y quote.")
        session_id = raw["session_id"]
        if (
            not isinstance(session_id, str)
            or len(session_id) != _SESSION_ID_LENGTH
            or any(character not in "0123456789abcdef" for character in session_id)
        ):
            raise ChargerDataError("charger_pricing contiene un id de sesión no válido.")
        if session_id in seen:
            raise ChargerDataError("charger_pricing contiene ids de sesión duplicados.")
        seen.add(session_id)
        session = sessions.get(session_id)
        if session is None:
            raise ChargerDataError("charger_pricing referencia una sesión ausente de charger.")
        result.append({"session_id": session_id, "quote": _validate_quote(raw["quote"], session)})
    return result


class ChargerPricingStoreMixin:
    """Store methods for one immutable quote per current closed charger session."""

    def _migrate_v8_to_v9(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """CREATE TABLE charger_pricing (
                session_id TEXT PRIMARY KEY
                    REFERENCES charger_sessions(id) ON DELETE CASCADE,
                quote_json TEXT NOT NULL CHECK (json_valid(quote_json))
            )"""
        )
        connection.execute(
            """CREATE TRIGGER invalidate_charger_pricing_on_session_change
               AFTER UPDATE OF ended_at, kwh ON charger_sessions
               WHEN OLD.ended_at IS NOT NEW.ended_at OR OLD.kwh IS NOT NEW.kwh
               BEGIN
                   DELETE FROM charger_pricing WHERE session_id = OLD.id;
               END"""
        )

    def _export_charger_pricing(self, connection: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = connection.execute(
            """SELECT pricing.session_id, pricing.quote_json, sessions.*
               FROM charger_pricing AS pricing
               JOIN charger_sessions AS sessions ON sessions.id = pricing.session_id
               ORDER BY pricing.session_id"""
        ).fetchall()
        if len(rows) > MAX_CHARGER_PRICING_QUOTES:
            raise _store_error("El ledger supera el límite de 1000 snapshots.", 500)
        return [
            {
                "session_id": row["session_id"],
                "quote": _verified_stored_quote(row["quote_json"], _session_from_row(row)),
            }
            for row in rows
        ]

    def _replace_charger_pricing_archive(
        self, connection: sqlite3.Connection, quotes: list[dict[str, Any]]
    ) -> None:
        for item in quotes:
            quote_json = json.dumps(
                item["quote"],
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            connection.execute(
                "INSERT INTO charger_pricing (session_id, quote_json) VALUES (?, ?)",
                (item["session_id"], quote_json),
            )

    def charger_pricing_candidates(
        self, scope: Any, limit: Any = MAX_CHARGER_SESSIONS
    ) -> list[dict[str, Any]]:
        normalized_scope = _validated_scope(scope)
        page_limit = _bounded_int(limit, "limit", 1, MAX_CHARGER_SESSIONS)
        with self._read_transaction() as connection:
            rows = connection.execute(
                """SELECT sessions.* FROM charger_sessions AS sessions
                   LEFT JOIN charger_pricing AS pricing ON pricing.session_id = sessions.id
                   WHERE sessions.scope = ? AND pricing.session_id IS NULL
                   ORDER BY sessions.ended_at DESC, sessions.id DESC LIMIT ?""",
                (normalized_scope, page_limit),
            ).fetchall()
            return [_session_from_row(row) for row in rows]

    def save_charger_pricing(
        self, scope: Any, session: Any, quote: Any
    ) -> dict[str, bool]:
        normalized_scope = _validated_scope(scope)
        try:
            candidate = validate_session(session, normalized_scope)
        except ChargerDataError as error:
            raise _store_error(str(error)) from None

        normalized_quote = _validate_quote(quote, candidate)
        try:
            quote_json = json.dumps(
                normalized_quote,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        except (TypeError, ValueError, UnicodeEncodeError):
            raise _store_error("El snapshot PVPC no se pudo guardar.") from None

        with self._transaction() as connection:
            current = connection.execute(
                "SELECT * FROM charger_sessions WHERE id = ?", (candidate["id"],)
            ).fetchone()
            if current is None:
                return {"changed": False}
            existing = connection.execute(
                "SELECT 1 FROM charger_pricing WHERE session_id = ?", (candidate["id"],)
            ).fetchone()
            if existing is not None:
                return {"changed": False}
            pricing_method = connection.execute(
                "SELECT pricing_method FROM settings WHERE id = 1"
            ).fetchone()[0]
            if pricing_method != "pvpc" or not _same_pricing_inputs(
                current, normalized_scope, candidate
            ):
                return {"changed": False}
            inserted = connection.execute(
                """INSERT INTO charger_pricing (session_id, quote_json)
                   VALUES (?, ?) ON CONFLICT(session_id) DO NOTHING""",
                (candidate["id"], quote_json),
            )
            if inserted.rowcount != 1:
                return {"changed": False}
            # Record only a real quote insert, with the canonical row values that
            # passed the current-input check, in the same SQLite transaction.
            self._append_charger_pricing_event(
                connection,
                "priced",
                normalized_scope,
                _session_from_row(current),
                normalized_quote,
                origin="pulse",
            )
            return {"changed": True}

    def charger_temporal_pricing_candidates(
        self, scope: Any, limit: Any = 4, after_session_id: Any = None
    ) -> list[dict[str, Any]]:
        """Closed sessions and the quote a configured energy reader may revise."""
        normalized_scope = _validated_scope(scope)
        page_limit = _bounded_int(limit, "limit", 1, 4)
        if after_session_id is not None and (
            not isinstance(after_session_id, str) or len(after_session_id) != 32
            or any(character not in "0123456789abcdef" for character in after_session_id)
        ):
            raise _store_error("El cursor de sesión no es válido.")
        with self._read_transaction() as connection:
            cursor = connection.execute(
                "SELECT ended_at, id FROM charger_sessions WHERE scope = ? AND id = ?",
                (normalized_scope, after_session_id),
            ).fetchone() if after_session_id is not None else None
            tail = " AND (sessions.ended_at < ? OR (sessions.ended_at = ? AND sessions.id < ?))" if cursor else ""
            params = (normalized_scope, cursor[0], cursor[0], cursor[1], page_limit) if cursor else (normalized_scope, page_limit)
            rows = connection.execute(
                """SELECT sessions.*, pricing.quote_json FROM charger_sessions AS sessions
                   LEFT JOIN charger_pricing AS pricing ON pricing.session_id = sessions.id
                   WHERE sessions.scope = ?""" + tail +
                " ORDER BY sessions.ended_at DESC, sessions.id DESC LIMIT ?", params,
            ).fetchall()
            if cursor and len(rows) < page_limit:
                rows.extend(connection.execute(
                    """SELECT sessions.*, pricing.quote_json FROM charger_sessions AS sessions
                       LEFT JOIN charger_pricing AS pricing ON pricing.session_id = sessions.id
                       WHERE sessions.scope = ? AND
                         (sessions.ended_at > ? OR (sessions.ended_at = ? AND sessions.id >= ?))
                       ORDER BY sessions.ended_at DESC, sessions.id DESC LIMIT ?""",
                    (normalized_scope, cursor[0], cursor[0], cursor[1], page_limit - len(rows)),
                ).fetchall())
            return [{"session": _session_from_row(row),
                     "quote": (_verified_stored_quote(row["quote_json"], _session_from_row(row))
                               if row["quote_json"] is not None else None)} for row in rows]

    def save_charger_temporal_pricing(
        self, scope: Any, session: Any, quote: Any, expected_quote: Any
    ) -> dict[str, bool]:
        """Compare and replace a quote with its audit event in one transaction."""
        normalized_scope = _validated_scope(scope)
        try:
            candidate = validate_session(session, normalized_scope)
        except ChargerDataError as error:
            raise _store_error(str(error)) from None
        normalized_quote = _validate_quote(quote, candidate)
        if normalized_quote["pricing"]["accuracy"] != "estimated_intervals":
            raise _store_error("La revisión requiere una distribución de energía por intervalos.")
        expected = _validate_quote(expected_quote, candidate) if expected_quote is not None else None
        quote_json = json.dumps(normalized_quote, allow_nan=False, ensure_ascii=False,
                                separators=(",", ":"))
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM charger_sessions WHERE id = ?",
                                     (candidate["id"],)).fetchone()
            if row is None or not _same_pricing_inputs(row, normalized_scope, candidate):
                return {"changed": False}
            if connection.execute("SELECT pricing_method FROM settings WHERE id = 1").fetchone()[0] != "pvpc":
                return {"changed": False}
            stored = connection.execute("SELECT quote_json FROM charger_pricing WHERE session_id = ?",
                                        (candidate["id"],)).fetchone()
            current = _verified_stored_quote(stored[0], candidate) if stored is not None else None
            if current != expected or current == normalized_quote:
                return {"changed": False}
            # The original priced/baseline event already preserves the old quote.
            connection.execute(
                """INSERT INTO charger_pricing (session_id, quote_json) VALUES (?, ?)
                   ON CONFLICT(session_id) DO UPDATE SET quote_json = excluded.quote_json""",
                (candidate["id"], quote_json),
            )
            self._append_charger_pricing_event(connection, "priced", normalized_scope,
                                               _session_from_row(row), normalized_quote, origin="pulse")
            return {"changed": True}

    def priced_charger_sessions(
        self, scope: Any, limit: Any = 20, offset: Any = 0
    ) -> dict[str, Any]:
        normalized_scope = _validated_scope(scope)
        page_limit = _bounded_int(limit, "limit", 1, MAX_CHARGER_PAGE_SIZE)
        page_offset = _bounded_int(offset, "offset", 0, 2_147_483_647)
        with self._read_transaction() as connection:
            pricing_enabled = connection.execute(
                "SELECT pricing_method FROM settings WHERE id = 1"
            ).fetchone()[0] == "pvpc"
            total = int(connection.execute(
                "SELECT COUNT(*) FROM charger_sessions WHERE scope = ?", (normalized_scope,)
            ).fetchone()[0])
            rows = connection.execute(
                """SELECT sessions.*, pricing.quote_json
                   FROM charger_sessions AS sessions
                   LEFT JOIN charger_pricing AS pricing ON pricing.session_id = sessions.id
                   WHERE sessions.scope = ?
                   ORDER BY sessions.ended_at DESC, sessions.id DESC LIMIT ? OFFSET ?""",
                (normalized_scope, page_limit, page_offset),
            ).fetchall()
            items: list[dict[str, Any]] = []
            for row in rows:
                session = _session_from_row(row)
                quote_json = row["quote_json"]
                if quote_json is not None:
                    quote = _verified_stored_quote(quote_json, session)
                    status = "ready"
                else:
                    quote = None
                    if not pricing_enabled:
                        status = "disabled"
                    elif _is_pricing_eligible(session):
                        status = "pending"
                    else:
                        status = "ineligible"
                items.append({**session, "pvpc": quote, "pricing_status": status})
            return {
                "items": items,
                "total": total,
                "limit": page_limit,
                "offset": page_offset,
            }


def _validate_quote(quote: Any, session: Mapping[str, Any]) -> dict[str, Any]:
    # Runtime import keeps pulse.domain -> this mixin free of an import cycle.
    from pulse.domain import _validate_pvpc_quote

    cost, tariff, pricing = _validate_pvpc_quote(
        quote, session["started_at"], session["ended_at"], session["kwh"], allow_temporal=True
    )
    if pricing["accuracy"] == "estimated_intervals":
        distribution = pricing["energy_distribution"]
        if distribution["session_id"] != session["id"]:
            raise _store_error("La energía por intervalos pertenece a otra sesión.")
        try:
            validate_session(session, distribution["scope"])
        except ChargerDataError:
            raise _store_error("La energía por intervalos pertenece a otro ámbito.") from None
    return {"cost_eur": cost, "tariff_eur_kwh": tariff, "pricing": pricing}


def _verified_stored_quote(value: str, session: Mapping[str, Any]) -> dict[str, Any]:
    try:
        raw = json.loads(
            value,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_unique_object_pairs,
        )
        return _validate_quote(raw, session)
    except Exception as error:
        from pulse.domain import DomainError

        if isinstance(error, DomainError):
            raise _store_error("El ledger contiene un snapshot PVPC no válido.", 500) from None
        if isinstance(error, (TypeError, ValueError, json.JSONDecodeError, RecursionError)):
            raise _store_error("El ledger contiene un snapshot PVPC no válido.", 500) from None
        raise


def _same_pricing_inputs(
    current: sqlite3.Row, scope: str, candidate: Mapping[str, Any]
) -> bool:
    return (
        current["scope"] == scope
        and int(current["upstream_id"]) == candidate["upstream_id"]
        and current["loadpoint"] == candidate["loadpoint"]
        and current["started_at"] == candidate["started_at"]
        and current["ended_at"] == candidate["ended_at"]
        and float(current["kwh"]) == candidate["kwh"]
    )


def _is_pricing_eligible(session: Mapping[str, Any]) -> bool:
    if session["kwh"] <= 0:
        return False
    started = datetime.fromisoformat(session["started_at"].replace("Z", "+00:00"))
    ended = datetime.fromisoformat(session["ended_at"].replace("Z", "+00:00"))
    duration = (ended.astimezone(timezone.utc) - started.astimezone(timezone.utc)).total_seconds()
    return 0 < duration <= _MAX_PVPC_SECONDS


def _validated_scope(scope: Any) -> str:
    try:
        return validate_scope(scope)
    except ChargerDataError as error:
        raise _store_error(str(error)) from None


def _bounded_int(value: Any, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise _store_error(f"{name} está fuera de rango.")
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


def _store_error(message: str, status: int = 400) -> Exception:
    from pulse.domain import DomainError

    return DomainError(message, status)
