"""Explicit, read-only telemetry collection outside the SQLite executor."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import math
from inspect import iscoroutinefunction
from typing import Any, Callable


_UTC = timezone.utc
_MAX_SHUTDOWN_WAIT_SECONDS = 1.0
_MAX_BACKOFF_SECONDS = 60 * 60
_MAX_CLOCK_SKEW_SECONDS = 60

_NUMERIC_FIELDS: dict[str, tuple[float, float, frozenset[str]]] = {
    "battery_pct": (0, 100, frozenset({"precise_soc_percent", "soc_percent"})),
    "range_km": (0, 5000, frozenset({"expected_range_km"})),
    "odometer_km": (0, 10_000_000, frozenset({"odometer_km"})),
    "interior_temperature_c": (-100, 100, frozenset({"interior_temperature_c"})),
    "battery_min_temperature_c": (-100, 150, frozenset({"battery_min_temperature_c"})),
    "front_left_pressure_kpa": (0, 1000, frozenset({"front_left_pressure_kpa"})),
    "front_right_pressure_kpa": (0, 1000, frozenset({"front_right_pressure_kpa"})),
    "rear_left_pressure_kpa": (0, 1000, frozenset({"rear_left_pressure_kpa"})),
    "rear_right_pressure_kpa": (0, 1000, frozenset({"rear_right_pressure_kpa"})),
}
_BOOLEAN_FIELDS = ("locked", "climate_on", "charging")
_SECURITY_CODE_FIELDS = ("vehicle_security_code", "sentry_code")
_REASON_CODES = frozenset(
    {
        "no_snapshot",
        "observed_at_missing",
        "observed_at_invalid",
        "observed_at_future",
        "observation_recent",
        "observation_stale",
        "observation_expired",
    }
)


class _InvalidSnapshot(ValueError):
    """Internal marker; details never leave the collector."""


def _utc_now() -> datetime:
    return datetime.now(_UTC)


def _parse_aware_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and len(value) <= 64:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(_UTC)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(_UTC).isoformat() if value is not None else None


def _seconds(value: object, name: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{name} debe ser un número positivo.")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} debe ser un número finito.") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{name} debe ser un número positivo.")
    return number


def quality(
    snapshot: Mapping[str, Any] | None,
    now: datetime,
    fresh_seconds: float = 300,
    max_age_seconds: float = 7200,
) -> dict[str, Any]:
    """Return time-based status separately from unqualified physical freshness.

    `freshness` intentionally remains `unknown`: a timestamp can describe age,
    but cannot prove that the source's vehicle signal is physically current.
    `provider_received_at` is never used as an observation timestamp.
    """
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now debe incluir zona horaria.")
    fresh_window = _seconds(fresh_seconds, "fresh_seconds")
    max_age = _seconds(max_age_seconds, "max_age_seconds")
    if max_age < fresh_window:
        raise ValueError("max_age_seconds debe ser mayor o igual a fresh_seconds.")

    if snapshot is None:
        return {
            "freshness": "unknown",
            "temporal_status": "unknown",
            "age_seconds": None,
            "observed_at": None,
            "reason_code": "no_snapshot",
        }
    if not isinstance(snapshot, Mapping):
        raise ValueError("snapshot debe ser un mapping o None.")

    raw_observed_at = snapshot.get("observed_at")
    observed_at = _parse_aware_datetime(raw_observed_at)
    if raw_observed_at is None:
        temporal_status = "unknown"
        reason_code = "observed_at_missing"
        age_seconds = None
    elif observed_at is None:
        temporal_status = "unknown"
        reason_code = "observed_at_invalid"
        age_seconds = None
    else:
        now_utc = now.astimezone(_UTC)
        ahead = (observed_at - now_utc).total_seconds()
        if ahead > _MAX_CLOCK_SKEW_SECONDS:
            temporal_status = "unknown"
            reason_code = "observed_at_future"
            age_seconds = None
        else:
            age = max(0.0, (now_utc - observed_at).total_seconds())
            age_seconds = round(age, 3)
            if age > max_age:
                temporal_status = "unavailable"
                reason_code = "observation_expired"
            elif age > fresh_window:
                temporal_status = "stale"
                reason_code = "observation_stale"
            else:
                temporal_status = "fresh"
                reason_code = "observation_recent"

    assert reason_code in _REASON_CODES
    return {
        "freshness": "unknown",
        "temporal_status": temporal_status,
        "age_seconds": age_seconds,
        "observed_at": _iso(observed_at),
        "reason_code": reason_code,
    }


class Collector:
    """Periodically request a passive provider read and persist its safe subset.

    Construction never reads the provider or starts tasks. A caller must inject
    a provider and explicitly call `start()` or `request_refresh()`.
    """

    def __init__(
        self,
        provider: object,
        coordinator: object,
        interval_seconds: float = 300,
        clock: Callable[[], datetime] = _utc_now,
        read_timeout_seconds: float = 20,
        on_stored: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        if provider is None or not callable(getattr(provider, "fetch_snapshot", None)):
            raise TypeError("Collector requiere un proveedor explícito con fetch_snapshot().")
        if coordinator is None or not callable(getattr(coordinator, "call", None)):
            raise TypeError("Collector requiere un coordinator con call().")
        if not callable(clock):
            raise TypeError("clock debe ser invocable.")
        if on_stored is not None and (not callable(on_stored) or iscoroutinefunction(on_stored)):
            raise TypeError("on_stored debe ser un callback síncrono.")

        self.interval_seconds = _seconds(interval_seconds, "interval_seconds")
        self.read_timeout_seconds = _seconds(read_timeout_seconds, "read_timeout_seconds")
        self.provider = provider
        self.coordinator = coordinator
        self.clock = clock
        self.on_stored = on_stored
        self._observer_errors = 0

        # Deliberately distinct from Coordinator.executor (SQLite owner).
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pulse-collector")
        self._guard = asyncio.Lock()
        self._stop_event: asyncio.Event | None = None
        self._periodic_task: asyncio.Task[None] | None = None
        self._refresh_task: asyncio.Task[dict[str, Any]] | None = None
        self._worker_future: asyncio.Future[Any] | None = None
        self._closed = False
        self._started = False

        self._read_count = 0
        self._success_count = 0
        self._error_count = 0
        self._consecutive_errors = 0
        self._last_attempt_at: datetime | None = None
        self._next_attempt_at: datetime | None = None
        self._last_error_code: str | None = None

    def status(self) -> dict[str, Any]:
        """Sanitized operational counters only; never includes provider data."""
        refresh_pending = self._refresh_task is not None and not self._refresh_task.done()
        worker_pending = self._worker_future is not None and not self._worker_future.done()
        return {
            "running": self._started and not self._closed,
            "closed": self._closed,
            "in_flight": refresh_pending or worker_pending,
            "read_count": self._read_count,
            "success_count": self._success_count,
            "error_count": self._error_count,
            "observer_error_count": self._observer_errors,
            "last_attempt_at": _iso(self._last_attempt_at),
            "next_attempt_at": _iso(self._next_attempt_at),
            "last_error_code": self._last_error_code,
        }

    async def start(self) -> dict[str, Any]:
        """Start the explicit periodic loop; the first read is asynchronous."""
        async with self._guard:
            if self._closed:
                return self._result("closed")
            if self._periodic_task is not None and not self._periodic_task.done():
                return self._result("running")
            self._started = True
            self._stop_event = asyncio.Event()
            self._periodic_task = asyncio.create_task(
                self._periodic_loop(), name="pulse-telemetry-collector"
            )
        return self._result("started")

    async def close(self) -> dict[str, Any]:
        """Stop scheduling and return promptly, even if a provider thread stalls."""
        async with self._guard:
            if self._closed:
                return self._result("closed")
            self._closed = True
            self._started = False
            if self._stop_event is not None:
                self._stop_event.set()
            tasks = [
                task
                for task in (self._periodic_task, self._refresh_task)
                if task is not None and not task.done()
            ]
            for task in tasks:
                task.cancel()

        if tasks:
            await asyncio.wait(tasks, timeout=_MAX_SHUTDOWN_WAIT_SECONDS)
        # A Python thread cannot be forcibly stopped. Do not block shutdown on
        # it; the in-flight guard remains effective until its future completes.
        self._executor.shutdown(wait=False, cancel_futures=True)
        return self._result("closed")

    async def request_refresh(self) -> dict[str, Any]:
        """Request one read; simultaneous callers await the same refresh task."""
        async with self._guard:
            if self._closed:
                return self._result("closed")

            task = self._refresh_task
            if task is not None and not task.done():
                refresh_task = task
            else:
                worker = self._worker_future
                if worker is not None and not worker.done():
                    return self._result("busy")

                now = self._clock_now()
                if self._next_attempt_at is not None and now < self._next_attempt_at:
                    return self._result("backoff")

                refresh_task = asyncio.create_task(self._refresh_once(), name="pulse-refresh-once")
                self._refresh_task = refresh_task
                refresh_task.add_done_callback(self._clear_refresh_task)

        try:
            return await asyncio.shield(refresh_task)
        except asyncio.CancelledError:
            if self._closed:
                return self._result("closed")
            raise

    async def _periodic_loop(self) -> None:
        while not self._closed:
            now = self._clock_now()
            due_at = self._next_attempt_at
            if due_at is not None:
                delay = max(0.0, (due_at - now).total_seconds())
                if delay > 0:
                    stop_event = self._stop_event
                    if stop_event is None:
                        return
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=delay)
                    except TimeoutError:
                        pass
                    if self._closed or stop_event.is_set():
                        return
                    continue

            if self._worker_future is not None and not self._worker_future.done():
                # The timed-out executor call still owns the single worker.
                # Defer another opportunity without incrementing error metrics.
                self._next_attempt_at = now + timedelta(seconds=self.interval_seconds)
                continue

            result = await self.request_refresh()
            if self._closed:
                return
            if result["status"] == "busy":
                self._next_attempt_at = self._clock_now() + timedelta(
                    seconds=self.interval_seconds
                )

    async def _refresh_once(self) -> dict[str, Any]:
        try:
            attempted_at = self._clock_now()
        except Exception:
            self._record_error("clock_invalid", self._last_attempt_at or _utc_now())
            return self._result("error", "clock_invalid")

        self._last_attempt_at = attempted_at
        self._read_count += 1
        import_epoch = self.coordinator.import_epoch
        loop = asyncio.get_running_loop()
        try:
            worker_future = loop.run_in_executor(self._executor, self.provider.fetch_snapshot)
        except Exception:
            self._record_error("read_failed", attempted_at)
            return self._result("error", "read_failed")

        self._worker_future = worker_future
        worker_future.add_done_callback(self._worker_done)
        try:
            done, _pending = await asyncio.wait(
                {worker_future}, timeout=self.read_timeout_seconds
            )
        except asyncio.CancelledError:
            # asyncio.wait doesn't cancel the underlying executor future.
            raise

        if worker_future not in done:
            self._record_error("read_timeout", attempted_at)
            return self._result("error", "read_timeout")

        try:
            raw_snapshot = worker_future.result()
        except Exception:
            self._record_error("provider_unavailable", attempted_at)
            return self._result("error", "provider_unavailable")

        try:
            record = _normalize_snapshot(raw_snapshot)
            stored = await self.coordinator.call(
                "record_snapshot", record, write=True, expected_import_epoch=import_epoch)
            completed_at = self._clock_now()
        except _InvalidSnapshot:
            self._record_error("invalid_snapshot", attempted_at)
            return self._result("error", "invalid_snapshot")
        except asyncio.CancelledError:
            raise
        except Exception:
            self._record_error("store_failed", attempted_at)
            return self._result("error", "store_failed")

        if stored is None or import_epoch != self.coordinator.import_epoch:
            # An import replaced the database while this provider read was in
            # flight. Retry on the normal cadence without persisting/notifying.
            self._next_attempt_at = completed_at + timedelta(seconds=self.interval_seconds)
            return self._result("discarded", "storage_replaced")

        self._success_count += 1
        self._consecutive_errors = 0
        self._last_error_code = None
        self._next_attempt_at = completed_at + timedelta(seconds=self.interval_seconds)
        if self.on_stored is not None:
            try:
                self.on_stored(stored)
            except Exception:
                self._observer_errors += 1
        return self._result("stored")

    def _worker_done(self, future: asyncio.Future[Any]) -> None:
        # Retrieve late exceptions after a caller timed out, without exposing
        # their text or triggering an unhandled-future warning.
        if not future.cancelled():
            try:
                future.exception()
            except Exception:
                pass

    def _clear_refresh_task(self, task: asyncio.Task[dict[str, Any]]) -> None:
        if self._refresh_task is task:
            self._refresh_task = None

    def _clock_now(self) -> datetime:
        value = self.clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return an aware datetime")
        return value.astimezone(_UTC)

    def _record_error(self, error_code: str, attempted_at: datetime) -> None:
        self._error_count += 1
        self._consecutive_errors += 1
        self._last_error_code = error_code
        try:
            now = self._clock_now()
        except Exception:
            now = attempted_at
        exponent = min(self._consecutive_errors - 1, 16)
        backoff = min(
            max(self.interval_seconds, _MAX_BACKOFF_SECONDS),
            self.interval_seconds * (2**exponent),
        )
        self._next_attempt_at = now + timedelta(seconds=backoff)

    def _result(self, status: str, error_code: str | None = None) -> dict[str, Any]:
        return {"status": status, "error_code": error_code}


def _normalize_snapshot(raw_snapshot: object) -> dict[str, Any]:
    if not isinstance(raw_snapshot, Mapping):
        raise _InvalidSnapshot
    raw_vehicle = raw_snapshot.get("vehicle")
    if not isinstance(raw_vehicle, Mapping):
        raise _InvalidSnapshot

    raw_sources = raw_snapshot.get("field_sources")
    if not isinstance(raw_sources, Mapping):
        raw_sources = {}

    vehicle: dict[str, Any] = {}
    field_sources: dict[str, str | None] = {}
    for field, (minimum, maximum, allowed_sources) in _NUMERIC_FIELDS.items():
        value = raw_vehicle.get(field)
        number: float | None = None
        if type(value) in (int, float):
            try:
                candidate = float(value)
                if math.isfinite(candidate) and minimum <= candidate <= maximum:
                    number = candidate
            except (OverflowError, ValueError):
                pass
        vehicle[field] = number
        source = raw_sources.get(field)
        field_sources[field] = (
            source if number is not None and isinstance(source, str) and source in allowed_sources
            else None
        )

    for field in _SECURITY_CODE_FIELDS:
        value = raw_vehicle.get(field)
        source = raw_sources.get(field)
        normalized: int | None = None
        if type(value) in (int, float):
            try:
                candidate = float(value)
                if math.isfinite(candidate) and candidate.is_integer() and 0 <= candidate <= 255:
                    normalized = int(candidate)
            except (OverflowError, ValueError):
                pass
        if normalized is not None and type(source) is str and source == field:
            vehicle[field] = normalized
            field_sources[field] = source
        else:
            vehicle[field] = None
            field_sources[field] = None

    for field in _BOOLEAN_FIELDS:
        value = raw_vehicle.get(field)
        vehicle[field] = value if type(value) is bool else None
        field_sources[field] = None

    observed_at = _iso(_parse_aware_datetime(raw_snapshot.get("observed_at")))
    provider_received_at = _iso(
        _parse_aware_datetime(raw_snapshot.get("provider_received_at"))
    )
    source = raw_snapshot.get("source")
    safe_source = (
        "MATE-API B10ReadClient"
        if source == "MATE-API B10ReadClient"
        else "configured_provider"
    )
    return {
        "source": safe_source,
        "observed_at": observed_at,
        "provider_received_at": provider_received_at,
        "vehicle": vehicle,
        "field_sources": field_sources,
    }


__all__ = ["Collector", "quality"]
