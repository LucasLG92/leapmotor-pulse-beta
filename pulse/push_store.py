"""Private persistence for encrypted Web Push subscriptions.

This mixin intentionally stores only endpoint/session hashes and Fernet text.
It does not receive or interpret endpoint, auth, p256dh, or encryption keys.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
import uuid
from typing import Any

from pulse.widget_profiles import WidgetProfileValidationError, validate_owner_id


MAX_PUSH_SUBSCRIPTIONS_PER_OWNER = 8
MAX_PUSH_SUBSCRIPTIONS_GLOBAL = 256
MAX_PUSH_NAME_LENGTH = 60
MAX_PUSH_CIPHERTEXT_LENGTH = 8192
MAX_PUSH_TTL = timedelta(hours=24)
_FINISH_RESULTS = frozenset({"accepted", "failed", "uncertain"})
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
_FERNET_TEXT_RE = re.compile(r"gAAAA[A-Za-z0-9_-]+={0,2}\Z")
_INVALID = "Los datos de la suscripción Push no son válidos."
_MISSING = "No se encontró la suscripción Push."
_CONFLICT = "La suscripción Push no está disponible."


class PushStoreMixin:
    """Owner-scoped CRUD and attempt reservation on the existing Store."""

    def upsert_push_subscription(
        self,
        owner_id: Any,
        endpoint_hash: Any,
        session_hash: Any,
        name: Any,
        ciphertext: Any,
        expires_at: Any,
    ) -> dict[str, Any]:
        """Create or rebind the globally unique endpoint for one owner."""
        owner = _owner(owner_id)
        endpoint = _hash(endpoint_hash)
        session = _hash(session_hash)
        clean_name = _name(name)
        encrypted = _ciphertext(ciphertext)
        expiry = _datetime(expires_at)
        now = self._clock_now()
        if expiry <= now or expiry > now + MAX_PUSH_TTL:
            raise _error(_INVALID)
        now_text = _format_datetime(now)
        expiry_text = _format_datetime(expiry)

        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM push_subscriptions WHERE endpoint_hash = ?", (endpoint,)
            ).fetchone()
            if existing is not None:
                if existing["owner_id"] != owner:
                    raise _error(_CONFLICT, 409)
                # A pending attempt from the old revision becomes ambiguous on rebind.
                connection.execute(
                    """UPDATE backup_alert_deliveries SET status = 'uncertain', updated_at = ?
                       WHERE subscription_id = ? AND revision = ? AND status = 'pending'""",
                    (now_text, existing["id"], existing["revision"]),
                )
                connection.execute(
                    """UPDATE push_subscriptions
                       SET session_hash = ?, name = ?, ciphertext = ?, expires_at = ?,
                           updated_at = ?, revision = revision + 1,
                           backup_alerts_enabled = 0,
                           backup_alerts_consented_at = NULL,
                           last_result = CASE WHEN last_result = 'pending'
                                              THEN 'uncertain' ELSE last_result END
                       WHERE id = ? AND revision = ?""",
                    (
                        session,
                        clean_name,
                        encrypted,
                        expiry_text,
                        now_text,
                        existing["id"],
                        existing["revision"],
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM push_subscriptions WHERE id = ?", (existing["id"],)
                ).fetchone()
                return dict(row)

            owner_count = connection.execute(
                "SELECT COUNT(*) FROM push_subscriptions WHERE owner_id = ?", (owner,)
            ).fetchone()[0]
            global_count = connection.execute(
                "SELECT COUNT(*) FROM push_subscriptions"
            ).fetchone()[0]
            if (
                owner_count >= MAX_PUSH_SUBSCRIPTIONS_PER_OWNER
                or global_count >= MAX_PUSH_SUBSCRIPTIONS_GLOBAL
            ):
                raise _error(_CONFLICT, 409)

            item_id = uuid.uuid4().hex
            connection.execute(
                """INSERT INTO push_subscriptions
                   (id, owner_id, endpoint_hash, session_hash, name, ciphertext,
                    created_at, updated_at, expires_at, revision, last_attempt_at,
                    last_accepted_at, last_result)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, NULL, NULL, 'never')""",
                (
                    item_id,
                    owner,
                    endpoint,
                    session,
                    clean_name,
                    encrypted,
                    now_text,
                    now_text,
                    expiry_text,
                ),
            )
            row = connection.execute(
                "SELECT * FROM push_subscriptions WHERE id = ?", (item_id,)
            ).fetchone()
            return dict(row)

    def list_push_subscriptions(self, owner_id: Any = None) -> list[dict[str, Any]]:
        if owner_id is None:
            query = "SELECT * FROM push_subscriptions ORDER BY owner_id, created_at, id"
            args: tuple[Any, ...] = ()
        else:
            owner = _owner(owner_id)
            query = (
                "SELECT * FROM push_subscriptions WHERE owner_id = ? "
                "ORDER BY created_at, id"
            )
            args = (owner,)
        with self._read_transaction() as connection:
            return [dict(row) for row in connection.execute(query, args).fetchall()]

    def get_push_subscription(self, owner_id: Any, item_id: Any) -> dict[str, Any]:
        owner = _owner(owner_id)
        item = _item_id(item_id)
        with self._read_transaction() as connection:
            row = connection.execute(
                "SELECT * FROM push_subscriptions WHERE owner_id = ? AND id = ?",
                (owner, item),
            ).fetchone()
            if row is None:
                raise _error(_MISSING, 404)
            return dict(row)

    def delete_push_subscription(
        self,
        owner_id: Any,
        item_id: Any,
        *,
        expected_revision: Any = None,
    ) -> dict[str, bool]:
        owner = _owner(owner_id)
        item = _item_id(item_id)
        if expected_revision is not None and (
            type(expected_revision) is not int or expected_revision < 1
        ):
            raise _error(_INVALID)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT revision FROM push_subscriptions WHERE owner_id = ? AND id = ?",
                (owner, item),
            ).fetchone()
            if row is None:
                if expected_revision is not None:
                    return {"ok": False}
                raise _error(_MISSING, 404)
            if expected_revision is not None and row["revision"] != expected_revision:
                return {"ok": False}
            now_text = _format_datetime(self._clock_now())
            connection.execute(
                """UPDATE backup_alert_deliveries SET status = 'uncertain', updated_at = ?
                   WHERE subscription_id = ? AND revision = ? AND status = 'pending'""",
                (now_text, item, row["revision"]),
            )
            if expected_revision is None:
                cursor = connection.execute(
                    "DELETE FROM push_subscriptions WHERE owner_id = ? AND id = ?",
                    (owner, item),
                )
            else:
                cursor = connection.execute(
                    """DELETE FROM push_subscriptions
                       WHERE owner_id = ? AND id = ? AND revision = ?""",
                    (owner, item, expected_revision),
                )
            if cursor.rowcount != 1:
                if expected_revision is not None:
                    return {"ok": False}
                raise _error(_MISSING, 404)
        return {"ok": True}

    def set_backup_alert_consent(
        self,
        owner_id: Any,
        item_id: Any,
        enabled: Any,
        consented_at: Any,
        *,
        expected_revision: Any = None,
    ) -> dict[str, Any]:
        owner = _owner(owner_id)
        item = _item_id(item_id)
        if type(enabled) is not bool:
            raise _error(_INVALID)
        if expected_revision is not None and (
            type(expected_revision) is not int or expected_revision < 1
        ):
            raise _error(_INVALID)
        consent = _format_datetime(_datetime(consented_at)) if enabled else None
        now_text = _format_datetime(self._clock_now())
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM push_subscriptions WHERE owner_id = ? AND id = ?",
                (owner, item),
            ).fetchone()
            if row is None:
                raise _error(_MISSING, 404)
            if expected_revision is not None and row["revision"] != expected_revision:
                raise _error(_CONFLICT, 409)
            currently_enabled = bool(row["backup_alerts_enabled"])
            if currently_enabled == enabled and (not enabled or row["backup_alerts_consented_at"]):
                return dict(row)
            connection.execute(
                """UPDATE backup_alert_deliveries SET status = 'uncertain', updated_at = ?
                   WHERE subscription_id = ? AND status = 'pending'""",
                (now_text, item),
            )
            cursor = connection.execute(
                """UPDATE push_subscriptions
                   SET backup_alerts_enabled = ?, backup_alerts_consented_at = ?,
                       revision = revision + 1,
                       last_result = CASE WHEN last_result = 'pending'
                                          THEN 'uncertain' ELSE last_result END,
                       updated_at = ?
                   WHERE owner_id = ? AND id = ? AND revision = ?""",
                (int(enabled), consent, now_text, owner, item, row["revision"]),
            )
            if cursor.rowcount != 1:
                raise _error(_CONFLICT, 409)
            updated = connection.execute(
                "SELECT * FROM push_subscriptions WHERE owner_id = ? AND id = ?",
                (owner, item),
            ).fetchone()
            return dict(updated)

    def reserve_push_attempt(
        self,
        owner_id: Any,
        item_id: Any,
        revision: Any,
        min_interval_seconds: Any = 60,
    ) -> dict[str, Any]:
        """Persist an attempt before delivery; pending survives process restarts."""
        owner = _owner(owner_id)
        item = _item_id(item_id)
        if type(revision) is not int or revision < 1:
            raise _error(_INVALID)
        if (
            type(min_interval_seconds) is not int
            or not 0 <= min_interval_seconds <= 86_400
        ):
            raise _error(_INVALID)
        now = self._clock_now()
        now_text = _format_datetime(now)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM push_subscriptions WHERE owner_id = ? AND id = ?",
                (owner, item),
            ).fetchone()
            if row is None:
                raise _error(_MISSING, 404)
            if row["revision"] != revision:
                raise _error(_CONFLICT, 409)
            if _stored_datetime(row["expires_at"]) <= now:
                raise _error(_CONFLICT, 409)
            # With no attempt token in finish(), pending cannot safely expire by itself.
            # A finish or an explicit rebind is required before another reservation.
            if row["last_result"] == "pending":
                raise _error(_CONFLICT, 409)
            if row["last_attempt_at"] is not None:
                last_attempt = _stored_datetime(row["last_attempt_at"])
                if now < last_attempt + timedelta(seconds=min_interval_seconds):
                    raise _error(_CONFLICT, 409)
            connection.execute(
                """UPDATE push_subscriptions
                   SET last_attempt_at = ?, last_result = 'pending', updated_at = ?
                   WHERE id = ? AND owner_id = ? AND revision = ?""",
                (now_text, now_text, item, owner, revision),
            )
            updated = connection.execute(
                "SELECT * FROM push_subscriptions WHERE id = ?", (item,)
            ).fetchone()
            return dict(updated)

    def finish_push_attempt(
        self,
        owner_id: Any,
        item_id: Any,
        revision: Any,
        result: Any,
    ) -> bool:
        owner = _owner(owner_id)
        item = _item_id(item_id)
        if (
            type(revision) is not int
            or revision < 1
            or not isinstance(result, str)
            or result not in _FINISH_RESULTS
        ):
            raise _error(_INVALID)
        now_text = _format_datetime(self._clock_now())
        with self._transaction() as connection:
            if result == "accepted":
                cursor = connection.execute(
                    """UPDATE push_subscriptions
                       SET last_result = 'accepted', last_accepted_at = ?, updated_at = ?
                       WHERE owner_id = ? AND id = ? AND revision = ?
                         AND last_result = 'pending'""",
                    (now_text, now_text, owner, item, revision),
                )
            else:
                cursor = connection.execute(
                    """UPDATE push_subscriptions
                       SET last_result = ?, updated_at = ?
                       WHERE owner_id = ? AND id = ? AND revision = ?
                         AND last_result = 'pending'""",
                    (result, now_text, owner, item, revision),
                )
            return cursor.rowcount == 1


def _error(message: str, status: int = 400) -> Exception:
    # Deferred import keeps the mixin independent of domain.Store initialization.
    from pulse.domain import DomainError

    return DomainError(message, status)


def _owner(value: Any) -> str:
    try:
        return validate_owner_id(value)
    except WidgetProfileValidationError:
        raise _error(_INVALID) from None


def _hash(value: Any) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise _error(_INVALID)
    return value


def _name(value: Any) -> str:
    if not isinstance(value, str):
        raise _error(_INVALID)
    clean = value.strip()
    try:
        clean.encode("utf-8")
    except UnicodeEncodeError:
        raise _error(_INVALID) from None
    if not 1 <= len(clean) <= MAX_PUSH_NAME_LENGTH:
        raise _error(_INVALID)
    if any(ord(character) < 32 or ord(character) == 127 for character in clean):
        raise _error(_INVALID)
    return clean


def _ciphertext(value: Any) -> str:
    if (
        not isinstance(value, str)
        or len(value) > MAX_PUSH_CIPHERTEXT_LENGTH
        or _FERNET_TEXT_RE.fullmatch(value) is None
    ):
        raise _error(_INVALID)
    return value


def _item_id(value: Any) -> str:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        raise _error(_INVALID)
    return value


def _datetime(value: Any) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise _error(_INVALID)
    try:
        parsed = datetime.fromisoformat(
            value[:-1] + "+00:00" if value.endswith("Z") else value
        )
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise _error(_INVALID)
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise _error(_INVALID)


def _stored_datetime(value: str) -> datetime:
    try:
        return _datetime(value)
    except Exception:
        raise _error("No se pudo acceder al almacenamiento Pulse.", 500) from None


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )
