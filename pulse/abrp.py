"""Envío opt-in de observaciones recientes de batería a ABRP."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import partial
import http.client
import json
import math
import ssl
import threading
import time
from collections.abc import Mapping
from typing import Any, Callable
from urllib.parse import urlencode

from pulse.abrp_policy import (
    AbrpConfig,
    AbrpObservation,
    attempt_cooldown_active,
    inspect_observation,
    observation_attempt_key,
)
from pulse.domain import DomainError


ABRP_HOST = "api.iternio.com"
ABRP_PATH = "/1/tlm/bulk"
NETWORK_TIMEOUT_SECONDS = 8
MAX_RESPONSE_BYTES = 64 * 1024
_FIELDS = ["battery_pct", "observed_at"]


class AbrpTransportError(Exception):
    """Fallo externo intencionalmente genérico; no incluye URL ni cuerpo."""


class AbrpTransport:
    """Transporte stdlib de destino fijo.

    Interfaz inyectable para tests: `connect(timeout_seconds) -> session`,
    `send(session, api_key, user_token, *, utc_seconds, soc_percent,
    timeout_seconds, request_lock, authorize) -> bool`, y `close(session)`.
    `connect` completa TLS antes de retornar. `send` llama `authorize` bajo
    `request_lock` inmediatamente antes de despachar el POST; después de ese
    punto una revocación no puede retirar la petición en vuelo. Retorna True
    solo con HTTP 200 y JSON inequívoco `{"status":"ok"}`. Fakes deben
    preservar esta secuencia y cerrar la sesión al terminar `send`.
    """

    def connect(self, timeout_seconds: float = NETWORK_TIMEOUT_SECONDS) -> http.client.HTTPSConnection:
        connection = http.client.HTTPSConnection(
            ABRP_HOST,
            timeout=timeout_seconds,
            context=ssl.create_default_context(),
        )
        connection.set_debuglevel(0)
        try:
            connection.connect()
            if connection.sock is None:
                raise AbrpTransportError
            return connection
        except Exception:
            connection.close()
            raise AbrpTransportError("No se pudo conectar con ABRP.") from None

    def send(
        self,
        session: http.client.HTTPSConnection,
        api_key: str,
        user_token: str,
        *,
        utc_seconds: int,
        soc_percent: float,
        timeout_seconds: float,
        request_lock: threading.Lock,
        authorize: Callable[[], bool],
    ) -> bool:
        try:
            if (type(session) is not http.client.HTTPSConnection
                    or session.host != ABRP_HOST or session.port != 443
                    or session.sock is None or timeout_seconds <= 0):
                raise AbrpTransportError

            body = json.dumps(
                {
                    "data": [
                        {
                            "token": user_token,
                            "tlm_list": [{"utc": utc_seconds, "soc": soc_percent}],
                        }
                    ]
                },
                allow_nan=False,
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("ascii")
            path = ABRP_PATH + "?" + urlencode({"api_key": api_key})
            session.sock.settimeout(timeout_seconds)
            with request_lock:
                permitted = authorize()
            if not permitted:
                return False
            session.request(
                "POST",
                path,
                body=body,
                headers={
                    "Accept": "application/json",
                    "Connection": "close",
                    "Content-Type": "application/json",
                },
            )

            response = session.getresponse()
            if response.status != 200:
                raise AbrpTransportError
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise AbrpTransportError
            try:
                decoded = raw.decode("utf-8", errors="strict")
                result = json.loads(
                    decoded,
                    object_pairs_hook=_unique_object,
                    parse_constant=_reject_json_constant,
                )
            except (UnicodeDecodeError, ValueError, TypeError):
                raise AbrpTransportError from None
            if type(result) is not dict or result.get("status") != "ok":
                raise AbrpTransportError
            return True
        except AbrpTransportError:
            raise
        except Exception:
            raise AbrpTransportError("ABRP no confirmó el envío.") from None
        finally:
            self.close(session)

    @staticmethod
    def close(session: http.client.HTTPSConnection) -> None:
        try:
            session.close()
        except Exception:
            pass


class AbrpBridge:
    """Gestiona consentimiento, frescura, deduplicación y transporte ABRP."""

    def __init__(
        self,
        config: AbrpConfig,
        coordinator: object,
        transport: object | None = None,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
        on_status_change: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        if type(config) is not AbrpConfig:
            raise TypeError("AbrpBridge requiere AbrpConfig.")
        if not callable(getattr(coordinator, "call", None)):
            raise TypeError("AbrpBridge requiere un coordinator con call().")
        if transport is not None and any(
            not callable(getattr(transport, name, None))
            for name in ("connect", "send", "close")
        ):
            raise TypeError("El transporte ABRP debe implementar connect(), send() y close().")
        if clock is not None and not callable(clock):
            raise TypeError("clock debe ser invocable.")
        if monotonic is not None and not callable(monotonic):
            raise TypeError("monotonic debe ser invocable.")
        if on_status_change is not None and not callable(on_status_change):
            raise TypeError("on_status_change debe ser invocable.")

        self.config = config
        self.coordinator = coordinator
        self.transport = transport if transport is not None else AbrpTransport()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic or time.monotonic
        self._on_status_change = on_status_change
        self._executor: ThreadPoolExecutor | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._runner: asyncio.Task[None] | None = None
        self._wake_event: asyncio.Event | None = None
        self._poll_lock = asyncio.Lock()
        self._lifecycle_lock = threading.Lock()
        self._lifecycle_generation = 0
        self._candidate_id: str | None = None
        self._candidate_reason = "waiting_observation"
        self._send_permit: tuple[int, str, float] | None = None
        self._worker_future: asyncio.Future[Any] | None = None
        self._inflight = False
        self._started = False
        self._closed = False
        self._consent = False
        self._last_shared_at: str | None = None
        self._last_shared_key: str | None = None
        self._last_shared_snapshot_id: str | None = None
        self._last_shared_observed_at: datetime | None = None
        self._status = "disabled" if not config.enabled else "paused"
        self._reason_code = "disabled" if not config.enabled else "consent_required"

    def status(self) -> dict[str, Any]:
        self._expire_shared_status()
        return self._status_payload()

    def _status_payload(self) -> dict[str, Any]:
        return {
            "enabled": self.config.enabled,
            "consent": self._consent,
            "status": self._status,
            "reason_code": self._reason_code,
            "fields": list(_FIELDS),
            "max_age_seconds": self.config.max_age_seconds,
            "last_shared_at": self._last_shared_at,
            "validation": "not_qualified",
        }

    async def start(self) -> dict[str, Any]:
        if not self.config.enabled:
            self._set_status("disabled", "disabled")
            return self.status()
        if self._closed or self._started:
            return self.status()
        self._started = True
        self._loop = asyncio.get_running_loop()
        self._wake_event = asyncio.Event()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pulse-abrp")
        self._runner = self._loop.create_task(self._poll_loop(), name="pulse-abrp-poll")
        return self.status()

    async def close(self) -> dict[str, Any]:
        self.invalidate()
        with self._lifecycle_lock:
            self._closed = True
        runner = self._runner
        self._runner = None
        if runner is not None:
            runner.cancel()
            await asyncio.gather(runner, return_exceptions=True)
        executor = self._executor
        self._executor = None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
        self._set_status("disabled", "disabled")
        return self.status()

    async def poll_once(self) -> dict[str, Any]:
        if not self.config.enabled:
            self._set_status("disabled", "disabled")
            return self.status()
        if not self._started or self._closed or self._executor is None:
            return self.status()
        async with self._poll_lock:
            if self._closed:
                return self.status()
            if self._inflight:
                self._set_status("sharing", "sharing")
                return self.status()
            epoch = self.coordinator.import_epoch
            try:
                context = await self._read_context(epoch)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._set_status("error", "send_failed")
                return self.status()
            if context is None:
                self.invalidate()
                return self.status()
            if not self._valid_context(context):
                self._set_status("error", "send_failed")
                return self.status()
            if context["demo_enabled"]:
                self.invalidate()
                self._consent = context["consent"]
                self._set_status("paused", "demo_excluded")
                return self.status()
            self._consent = context["consent"]
            if not context["consent"]:
                self._set_status("paused", "consent_required")
                return self.status()

            candidate_id, candidate_reason, lifecycle_generation = self._candidate_state()
            snapshot = context["snapshot"]
            if candidate_id is None:
                self._set_status("waiting", candidate_reason)
                return self.status()
            if not isinstance(snapshot, Mapping) or snapshot.get("id") != candidate_id:
                self._drop_candidate(candidate_id, "waiting_observation")
                self._set_status("waiting", "waiting_observation")
                return self.status()

            now = self._clock_now()
            if now is None:
                self._drop_candidate(candidate_id, "observation_invalid")
                self._set_status("waiting", "observation_invalid")
                return self.status()
            observation, invalid_reason = inspect_observation(
                snapshot, now, self.config.max_age_seconds
            )
            if observation is None:
                self._drop_candidate(candidate_id, invalid_reason)
                self._set_status("waiting", invalid_reason)
                return self.status()

            key = observation_attempt_key(observation)
            if context["last_attempt_key"] == key:
                if self._last_shared_key == key:
                    self._consent = True
                    self._set_status("shared", "shared")
                    return self.status()
                self._set_status("paused", "already_attempted")
                return self.status()
            if attempt_cooldown_active(
                context["last_attempt_at"], now, self.config.send_interval_seconds
            ):
                self._set_status("paused", "already_attempted")
                return self.status()

            try:
                reserved = await self.coordinator.call(
                    "reserve_abrp",
                    self.config.scope,
                    context["generation"],
                    key,
                    observation.snapshot_id,
                    attempt_at=now,
                    send_interval_seconds=self.config.send_interval_seconds,
                    write=True,
                    expected_import_epoch=epoch,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                self._set_status("error", "send_failed")
                return self.status()
            if reserved is not True:
                await self._refresh_after_reject(epoch)
                return self.status()

            loop = asyncio.get_running_loop()
            network_deadline = loop.time() + NETWORK_TIMEOUT_SECONDS
            self._inflight = True
            self._set_status("sharing", "sharing")
            await self._transmit(
                observation,
                key,
                context["generation"],
                epoch,
                lifecycle_generation,
                network_deadline,
            )
            return self.status()

    def observe(self, snapshot: object) -> None:
        """Remember only the ID of the callback observation; never persist it here."""
        if not self.config.enabled:
            return
        now = self._clock_now()
        observation, reason = inspect_observation(snapshot, now, self.config.max_age_seconds) \
            if now is not None else (None, "observation_invalid")
        with self._lifecycle_lock:
            if self._closed:
                return
            previous_shared_id = self._last_shared_snapshot_id
            self._candidate_id = None if observation is None else observation.snapshot_id
            self._candidate_reason = "waiting_observation" if observation else reason
            self._send_permit = None
        if observation is None:
            self._set_status("waiting", reason)
        elif previous_shared_id is not None and previous_shared_id != observation.snapshot_id:
            if self._status == "shared":
                self._set_status("waiting", "waiting_observation")
        self._wake()

    def invalidate(self, *, clear_consent: bool = False) -> None:
        """Invalidate candidate and share status, optionally clearing RAM consent.

        Import handlers use ``clear_consent=True`` after a valid import has also
        revoked persisted consent. Demo transitions and invalid imports use the
        default to discard a candidate while preserving consent.
        """
        with self._lifecycle_lock:
            self._lifecycle_generation += 1
            self._candidate_id = None
            self._candidate_reason = "waiting_observation"
            self._send_permit = None
            self._last_shared_key = None
            self._last_shared_snapshot_id = None
            self._last_shared_observed_at = None
            self._last_shared_at = None
            if clear_consent:
                self._consent = False
        if self.config.enabled and not self._closed and not self._consent:
            self._set_status("paused", "consent_required")
        elif self.config.enabled and not self._closed:
            self._set_status("waiting", "waiting_observation")
        else:
            self._set_status("disabled", "disabled")
        self._wake()

    async def set_consent(self, enabled: bool) -> dict[str, Any]:
        if type(enabled) is not bool:
            raise ValueError("El consentimiento ABRP debe ser booleano.")
        if enabled and not self.config.enabled:
            raise DomainError("ABRP no está configurado.", 409)
        if enabled is False:
            self.invalidate(clear_consent=True)
        try:
            result = await self.coordinator.call(
                "set_abrp_consent", self.config.scope, enabled, write=True
            )
        except Exception:
            if enabled is False:
                self._set_status("error", "send_failed")
            raise
        if (not isinstance(result, Mapping) or result.get("consent") is not enabled
                or type(result.get("generation")) is not int):
            self._set_status("error", "send_failed")
            raise DomainError("No se pudo actualizar el consentimiento ABRP.", 503)
        self._consent = enabled and self.config.enabled
        if not self.config.enabled:
            self._set_status("disabled", "disabled")
        elif enabled:
            self._set_status("waiting", "waiting_observation")
        else:
            self._set_status("paused", "consent_required")
        self._wake()
        return self.status()

    async def _poll_loop(self) -> None:
        event = self._wake_event
        if event is None:
            return
        while not self._closed:
            event.clear()
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._set_status("error", "send_failed")
            if self._closed:
                return
            try:
                await asyncio.wait_for(event.wait(), timeout=self.config.poll_seconds)
            except TimeoutError:
                pass

    async def _read_context(self, epoch: int) -> Any:
        return await self.coordinator.call(
            "abrp_context", self.config.scope, expected_import_epoch=epoch
        )

    @staticmethod
    def _valid_context(context: Any) -> bool:
        return (
            isinstance(context, Mapping)
            and type(context.get("demo_enabled")) is bool
            and type(context.get("consent")) is bool
            and type(context.get("generation")) is int
            and context["generation"] >= 0
            and (context.get("last_attempt_key") is None
                 or isinstance(context.get("last_attempt_key"), str))
            and (context.get("last_attempt_at") is None
                 or isinstance(context.get("last_attempt_at"), str))
        )

    async def _refresh_after_reject(self, epoch: int) -> None:
        try:
            context = await self._read_context(epoch)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._set_status("error", "send_failed")
            return
        if context is None:
            self.invalidate()
        elif not self._valid_context(context):
            self.invalidate()
            self._set_status("error", "send_failed")
        elif context["demo_enabled"]:
            self.invalidate()
            self._consent = context["consent"]
            self._set_status("paused", "demo_excluded")
        elif not context["consent"]:
            self._consent = False
            self._set_status("paused", "consent_required")
        else:
            self._consent = True
            self._set_status("paused", "already_attempted")

    async def _transmit(
        self,
        observation: AbrpObservation,
        key: str,
        store_generation: int,
        import_epoch: int,
        lifecycle_generation: int,
        network_deadline: float,
    ) -> None:
        loop = asyncio.get_running_loop()
        executor = self._executor
        if executor is None:
            self._set_status("error", "send_failed")
            self._inflight = False
            return

        session: Any = None
        send_started = False
        try:
            remaining = network_deadline - loop.time()
            if remaining <= 0:
                self._set_status("error", "send_failed")
                self._inflight = False
                return
            connect_future = loop.run_in_executor(
                executor, self.transport.connect, min(remaining, NETWORK_TIMEOUT_SECONDS)
            )
            self._track_worker(connect_future)
            done, _ = await asyncio.wait({connect_future}, timeout=remaining)
            if connect_future not in done:
                connect_future.add_done_callback(self._close_late_session)
                self._set_status("error", "send_failed")
                return
            session = connect_future.result()

            if not await self._preflight(
                observation,
                key,
                store_generation,
                import_epoch,
                lifecycle_generation,
            ):
                self.transport.close(session)
                session = None
                return

            remaining = network_deadline - loop.time()
            if remaining <= 0:
                self._clear_permit()
                self.transport.close(session)
                session = None
                self._set_status("error", "send_failed")
                return

            send_call = partial(
                self.transport.send,
                session,
                self.config.api_key,
                self.config.user_token,
                utc_seconds=observation.utc_seconds,
                soc_percent=observation.soc_percent,
                timeout_seconds=remaining,
                request_lock=self._lifecycle_lock,
                authorize=lambda: self._authorize(lifecycle_generation, observation.snapshot_id),
            )
            send_future = loop.run_in_executor(executor, send_call)
            send_started = True
            session = None  # send() owns and closes it on every outcome.
            self._track_worker(send_future)
            done, _ = await asyncio.wait({send_future}, timeout=remaining)
            if send_future not in done:
                self._set_status("error", "send_failed")
                return
            acknowledged = send_future.result()
            if acknowledged is not True:
                await self._refresh_after_reject(import_epoch)
                return

            current = await self._read_context(import_epoch)
            with self._lifecycle_lock:
                lifecycle_current = (
                    not self._closed
                    and self._lifecycle_generation == lifecycle_generation
                )
            if (current is None or not self._valid_context(current)
                    or current["demo_enabled"] or not current["consent"]
                    or current["generation"] != store_generation
                    or current["last_attempt_key"] != key or not lifecycle_current
                    or import_epoch != self.coordinator.import_epoch):
                await self._refresh_after_reject(import_epoch)
                return

            shared_at = self._clock_now()
            self._last_shared_at = shared_at.isoformat() if shared_at is not None else None
            if (shared_at is None or observation.observed_at > shared_at
                    or (shared_at - observation.observed_at).total_seconds()
                    > self.config.max_age_seconds):
                self._last_shared_key = None
                self._last_shared_snapshot_id = None
                self._last_shared_observed_at = None
                self._set_status(
                    "waiting",
                    "observation_stale" if shared_at is not None else "observation_invalid",
                )
                return
            self._last_shared_key = key
            self._last_shared_snapshot_id = observation.snapshot_id
            self._last_shared_observed_at = observation.observed_at
            self._consent = True
            self._set_status("shared", "shared")
        except asyncio.CancelledError:
            raise
        except Exception:
            # Exception text may include request details; never retain or expose it.
            self._clear_permit()
            self._set_status("error", "send_failed")
        finally:
            if session is not None and not send_started:
                self.transport.close(session)
            if send_started:
                # The worker future owns the latch until its callback observes completion.
                pass
            elif self._worker_future is None or self._worker_future.done():
                self._inflight = False

    async def _preflight(
        self,
        observation: AbrpObservation,
        key: str,
        store_generation: int,
        import_epoch: int,
        lifecycle_generation: int,
    ) -> bool:
        with self._lifecycle_lock:
            still_current = (
                not self._closed
                and self._lifecycle_generation == lifecycle_generation
                and self._candidate_id == observation.snapshot_id
            )
        if not still_current:
            self._set_status("waiting", "waiting_observation")
            return False

        try:
            context = await self._read_context(import_epoch)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._set_status("error", "send_failed")
            return False
        if context is None:
            self.invalidate()
            return False
        if not self._valid_context(context):
            self._set_status("error", "send_failed")
            return False
        self._consent = context["consent"]
        if context["demo_enabled"]:
            self.invalidate()
            self._set_status("paused", "demo_excluded")
            return False
        if not context["consent"] or context["generation"] != store_generation:
            self._set_status("paused", "consent_required")
            return False
        snapshot = context["snapshot"]
        if not isinstance(snapshot, Mapping) or snapshot.get("id") != observation.snapshot_id:
            self._drop_candidate(observation.snapshot_id, "waiting_observation")
            self._set_status("waiting", "waiting_observation")
            return False
        now = self._clock_now()
        latest, reason = inspect_observation(snapshot, now, self.config.max_age_seconds) \
            if now is not None else (None, "observation_invalid")
        if latest is None or latest.snapshot_id != observation.snapshot_id:
            self._drop_candidate(observation.snapshot_id, reason)
            self._set_status("waiting", reason)
            return False
        if (context["last_attempt_key"] != key
                or import_epoch != self.coordinator.import_epoch):
            self._set_status("paused", "already_attempted")
            return False

        mono = self._monotonic()
        age = max(0.0, (now - latest.observed_at).total_seconds())
        expires_at = mono + max(0.0, self.config.max_age_seconds - age)
        with self._lifecycle_lock:
            permission_still_current = (
                not self._closed
                and self._lifecycle_generation == lifecycle_generation
                and self._candidate_id == observation.snapshot_id
            )
            if permission_still_current:
                self._send_permit = (lifecycle_generation, observation.snapshot_id, expires_at)
        if not permission_still_current:
            self._set_status("waiting", "waiting_observation")
            return False
        return True

    def _authorize(self, lifecycle_generation: int, snapshot_id: str) -> bool:
        """Thread-side guard. Called under `_request_lock`; performs no database I/O."""
        permit = self._send_permit
        if permit is None:
            return False
        generation, permitted_id, expires_at = permit
        current = self._monotonic()
        if (self._closed or generation != lifecycle_generation
                or permitted_id != snapshot_id
                or self._lifecycle_generation != lifecycle_generation
                or self._candidate_id != snapshot_id
                or not math.isfinite(current) or current > expires_at):
            self._send_permit = None
            return False
        self._send_permit = None
        return True

    def _track_worker(self, future: asyncio.Future[Any]) -> None:
        self._worker_future = future
        self._inflight = True
        future.add_done_callback(self._worker_done)

    def _worker_done(self, future: asyncio.Future[Any]) -> None:
        if not future.cancelled():
            try:
                future.exception()
            except Exception:
                pass
        if self._worker_future is future:
            self._worker_future = None
            self._inflight = False

    def _close_late_session(self, future: asyncio.Future[Any]) -> None:
        if future.cancelled():
            return
        try:
            session = future.result()
        except Exception:
            return
        self.transport.close(session)

    def _candidate_state(self) -> tuple[str | None, str, int]:
        with self._lifecycle_lock:
            return self._candidate_id, self._candidate_reason, self._lifecycle_generation

    def _drop_candidate(self, expected_id: str, reason: str) -> None:
        with self._lifecycle_lock:
            if self._candidate_id == expected_id:
                self._candidate_id = None
                self._candidate_reason = reason
                self._send_permit = None

    def _clear_permit(self) -> None:
        with self._lifecycle_lock:
            self._send_permit = None

    def _clock_now(self) -> datetime | None:
        try:
            value = self._clock()
            if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
                return None
            return value.astimezone(timezone.utc)
        except Exception:
            return None

    def _set_status(self, status: str, reason: str) -> None:
        if self._closed and status != "disabled":
            return
        if self._status == status and self._reason_code == reason:
            return
        self._status = status
        self._reason_code = reason
        callback = self._on_status_change
        if callback is not None:
            try:
                callback(self._status_payload())
            except Exception:
                pass

    def _expire_shared_status(self) -> None:
        if self._status != "shared" or self._last_shared_observed_at is None:
            return
        now = self._clock_now()
        observed = self._last_shared_observed_at
        if (now is not None and observed <= now
                and (now - observed).total_seconds() <= self.config.max_age_seconds):
            return
        self._last_shared_key = None
        self._last_shared_snapshot_id = None
        self._last_shared_observed_at = None
        self._status = "waiting"
        self._reason_code = (
            "observation_stale"
            if now is not None and observed <= now
            else "observation_invalid"
        )
        callback = self._on_status_change
        if callback is not None:
            try:
                callback(self._status_payload())
            except Exception:
                pass

    def _wake(self) -> None:
        loop = self._loop
        event = self._wake_event
        if loop is not None and event is not None and not loop.is_closed():
            loop.call_soon_threadsafe(event.set)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError


__all__ = ["AbrpBridge", "AbrpTransport", "AbrpTransportError"]
