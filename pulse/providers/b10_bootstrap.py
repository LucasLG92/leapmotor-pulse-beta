"""Explicit B10 assembly and in-memory session renewal for passive reads.

The operator supplies authorized authentication callbacks and a TLS transport.
Ordinary Pulse startup never imports the optional SDK or calls this factory.
No credentials, certificate files or persistent session store are discovered here.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from threading import Event, RLock
from time import monotonic

from pulse.providers.base import ProviderUnavailable
from pulse.providers.mate_api import MateApiB10Provider
from pulse.providers.session_codec import decode_session, encode_pending, encode_session


def _utc_now():
    return datetime.now(timezone.utc)


def _limit(value, minimum, maximum):
    if type(value) not in (int, float) or not minimum <= value <= maximum or not math.isfinite(value):
        raise ValueError("Límites de renovación de sesión no válidos.")
    return float(value)


def _unavailable(code):
    # Callback errors can contain private authentication material.
    return ProviderUnavailable(code, "La sesión de lectura no está disponible.")


class _SessionReadClient:
    """Serialize acquisition, renewal and reads for one privately owned SDK store."""

    def __init__(self, reader, sessions, *, acquire_session, renew_session,
                 session_type, clock, monotonic_clock=monotonic,
                 refresh_before_seconds=120, retry_after_seconds=600,
                 session_vault=None, session_context=None):
        if (not callable(getattr(reader, "read", None))
                or not callable(getattr(sessions, "replace", None))
                or not isinstance(session_type, type)
                or not all(callable(value) for value in (acquire_session, clock, monotonic_clock))
                or renew_session is not None and not callable(renew_session)):
            raise ValueError("El bootstrap requiere dependencias explícitas válidas.")
        if session_vault is not None and (
                not isinstance(session_context, str) or len(session_context) != 64
                or any(not callable(getattr(session_vault, method, None))
                       for method in ("load", "save", "close"))):
            raise ValueError("El almacén de sesión debe ser explícito y estar vinculado.")
        self._refresh_before = _limit(refresh_before_seconds, 0, 900)
        self._retry_after = _limit(retry_after_seconds, 60, 3600)
        self._reader, self._sessions = reader, sessions
        self._acquire, self._renew, self._session_type = acquire_session, renew_session, session_type
        self._clock, self._monotonic = clock, monotonic_clock
        self._lock = RLock()
        self._closed = Event()
        self._active_reads = 0
        self._vault, self._context = session_vault, session_context
        self._session = None
        self._acquisition_attempted = False
        self._renewal_blocked = False
        self._renewal_failed = False
        self._renew_after = None

    def __repr__(self):
        return "B10SessionReadClient(<private dependencies>)"

    def _now(self):
        now = self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise _unavailable("session_clock_invalid")
        return now.astimezone(timezone.utc)

    def _tick(self):
        value = self._monotonic()
        if type(value) not in (int, float) or not math.isfinite(value):
            raise _unavailable("session_clock_invalid")
        return value

    def _check_type(self, session):
        if type(session) is not self._session_type:
            raise ValueError("Invalid session type")
        expiry = session.expires_at
        if not isinstance(expiry, datetime) or expiry.tzinfo is None or expiry.utcoffset() is None:
            raise ValueError("Session expiry must be known")

    def _acquire_once(self):
        if self._acquisition_attempted:
            raise _unavailable("authentication_required")
        self._acquisition_attempted = True
        payload = None
        if self._vault is not None:
            try:
                payload = self._vault.load()
                if payload is not None:
                    session = decode_session(payload, self._context, self._session_type)
                    self._check_type(session)
            except Exception:
                raise _unavailable("session_storage_failed") from None
        if payload is None:
            self._raise_if_closed()
            self._persist_pending("acquisition_pending")
            self._raise_if_closed()
        try:
            if payload is None:
                session = self._acquire()
            self._check_type(session)
        except Exception:
            raise _unavailable("authentication_failed") from None

        if payload is None:
            self._persist(session)
        try:
            self._sessions.replace(session)
            self._session = session
        except Exception:
            raise _unavailable("authentication_failed") from None

    def _persist_pending(self, state):
        if self._vault is not None:
            try:
                self._vault.save(encode_pending(self._context, state))
            except Exception:
                self._renewal_blocked = True
                raise _unavailable("session_storage_failed") from None

    def _persist(self, session):
        if self._vault is not None:
            try:
                self._vault.save(encode_session(session, self._context))
            except Exception:
                self._renewal_blocked = True
                raise _unavailable("session_storage_failed") from None

    def _ensure_session(self):
        now = self._now()
        if self._session is None:
            self._acquire_once()
            now = self._now()
        self._raise_if_closed()
        session = self._session
        if self._renewal_blocked:
            raise _unavailable("authentication_required")
        # A wall-clock correction must not bypass a failed refresh's cooldown.
        if (self._renewal_failed and self._renew_after is not None
                and self._tick() < self._renew_after):
            raise _unavailable("session_refresh_cooldown")
        expiry = session.expires_at
        if (expiry - now).total_seconds() <= self._refresh_before:
            if self._renew is None or not session.renewable(now):
                # An otherwise valid token remains usable until its stated expiry.
                if now >= expiry:
                    raise _unavailable("authentication_required")
            else:
                tick = self._tick()
                if self._renew_after is not None and tick < self._renew_after:
                    if self._renewal_failed or now >= expiry:
                        raise _unavailable("session_refresh_cooldown")
                else:
                    self._raise_if_closed()
                    self._persist_pending("renewal_pending")
                    self._raise_if_closed()
                    self._renew_after = tick + self._retry_after
                    self._renewal_failed = True
                    try:
                        renewed = self._renew(session)
                    except Exception:
                        if self._vault is not None:
                            self._renewal_blocked = True
                            raise _unavailable("session_refresh_uncertain") from None
                        raise _unavailable("session_refresh_failed") from None
                    try:
                        self._check_type(renewed)
                        finished = self._now()
                        renewed.ensure_valid(finished)
                        if any(getattr(renewed, field) != getattr(session, field)
                               for field in ("user_id", "device_id", "client_cert")):
                            raise ValueError("Session continuity changed")
                    except Exception:
                        # A response with invalid material is not a network retry case.
                        self._renewal_blocked = True
                        raise _unavailable("session_refresh_invalid") from None
                    self._persist(renewed)
                    try:
                        self._sessions.replace(renewed)
                    except Exception:
                        self._renewal_blocked = True
                        raise _unavailable("session_refresh_invalid") from None
                    self._session = session = renewed
                    self._renewal_failed = False
        try:
            session.ensure_valid(self._now())
        except Exception:
            raise _unavailable("authentication_required") from None

    def read(self, vin):
        with self._lock:
            self._raise_if_closed()
            self._active_reads += 1
            try:
                try:
                    self._ensure_session()
                except ProviderUnavailable:
                    raise
                except Exception:
                    raise _unavailable("session_invalid") from None
                self._raise_if_closed()
                # Never retry a rejected/ambiguous read or turn it into another login.
                return self._reader.read(vin)
            finally:
                self._active_reads -= 1
                if self._closed.is_set() and self._active_reads == 0:
                    self._close_vault()

    def _raise_if_closed(self):
        if self._closed.is_set():
            raise _unavailable("session_closed")

    def _close_vault(self):
        if self._vault is not None:
            try:
                self._vault.close()
            except Exception:
                raise _unavailable("session_storage_failed") from None

    def close(self):
        # Do not block the event loop on an active SDK call. Its finally releases
        # the lease after its last persistence/transport operation has finished.
        self._closed.set()
        if self._lock.acquire(blocking=False):
            try:
                if self._active_reads == 0:
                    self._close_vault()
            finally:
                self._lock.release()


class ManagedB10Provider(MateApiB10Provider):
    def close(self):
        self._read_client.close()


def build_b10_provider(*, transport, acquire_session, renew_session=None,
                       vehicle_id, center, region, clock=_utc_now,
                       refresh_before_seconds=120, retry_after_seconds=600,
                       session_vault=None):
    """Build a passive provider without authenticating or touching private files.

    ``acquire_session`` returns the pinned SDK's CloudSession on the first read.
    It can invoke a separately authorized LoginClient or restore a session from an
    operator-owned store. ``renew_session(session)`` can invoke LoginClient.refresh.
    Pulse owns a new SessionStore and never falls back from renewal to login.
    Pass the result to ``create_app(provider=...)``; no global provider is mutated.
    """
    if not callable(getattr(transport, "send", None)):
        raise ValueError("Se requiere un transporte TLS explícito con send().")
    if session_vault is not None:
        from pulse.providers.session_vault import EncryptedSessionVault
        if not isinstance(session_vault, EncryptedSessionVault):
            raise ValueError("El bootstrap requiere un almacén cifrado explícito.")
    try:
        from leapmotor_cloud.b10_reads import B10ReadClient
        from leapmotor_cloud.session import CloudSession, SessionStore
    except ImportError:
        raise ValueError("El bootstrap B10 requiere el extra SDK fijado de Pulse.") from None

    sessions = SessionStore()
    reader = B10ReadClient(transport, sessions, center=center, region=region, clock=clock)
    context = hashlib.sha256(json.dumps([vehicle_id, reader.center, reader.region],
                                      separators=(",", ":")).encode()).hexdigest()
    managed = _SessionReadClient(
        reader, sessions, acquire_session=acquire_session, renew_session=renew_session,
        session_type=CloudSession, clock=clock,
        refresh_before_seconds=refresh_before_seconds, retry_after_seconds=retry_after_seconds,
        session_vault=session_vault, session_context=context,
    )
    provider = ManagedB10Provider(managed, vehicle_id)
    if session_vault is not None:
        try:
            session_vault.reserve()
        except Exception:
            raise ValueError("El almacén cifrado ya pertenece a otra conexión.") from None
    return provider
