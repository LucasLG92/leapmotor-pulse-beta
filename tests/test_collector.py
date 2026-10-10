"""Deterministic tests for the explicit read-only collector; no network is used."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import math
import threading
import time

from pulse.collector import Collector, quality


UTC = timezone.utc
NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


def snapshot(**updates):
    value = {
        "source": "MATE-API B10ReadClient",
        "observed_at": NOW.isoformat(),
        "provider_received_at": NOW.isoformat(),
        "vehicle": {
            "model": "must be dropped",
            "battery_pct": 72.5,
            "range_km": 320,
            "odometer_km": 1250,
            "locked": True,
            "climate_on": False,
            "charging": False,
        },
        "field_sources": {
            "battery_pct": "precise_soc_percent",
            "range_km": "expected_range_km",
            "odometer_km": "odometer_km",
        },
        "freshness": "fresh",
        "capabilities": {"availability": "available"},
    }
    value.update(updates)
    return value


class FixedClock:
    def __init__(self, now=NOW):
        self.value = now

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


class FakeProvider:
    def __init__(self, result=None, *, error=None, started=None, release=None, finished=None):
        self.result = result if result is not None else snapshot()
        self.error = error
        self.started = started
        self.release = release
        self.finished = finished
        self.calls = 0
        self._lock = threading.Lock()

    def fetch_snapshot(self):
        with self._lock:
            self.calls += 1
        if self.started is not None:
            self.started.set()
        try:
            if self.release is not None and not self.release.wait(3):
                raise RuntimeError("synthetic worker gate expired")
            if self.error is not None:
                raise self.error
            return self.result
        finally:
            if self.finished is not None:
                self.finished.set()


class FakeCoordinator:
    def __init__(self):
        self.calls = []
        self.import_epoch = 0

    async def call(self, method, *args, write=False, **kwargs):
        self.calls.append((method, args, write, kwargs))
        return {"id": "test-only"}


def run(coroutine):
    return asyncio.run(coroutine)


async def wait_thread_event(event):
    assert await asyncio.wait_for(asyncio.to_thread(event.wait, 1), timeout=1.5)


def test_construction_does_not_start_reads_or_tasks():
    provider = FakeProvider()
    collector = Collector(provider, FakeCoordinator(), clock=FixedClock())

    assert provider.calls == 0
    assert collector.status()["read_count"] == 0
    assert collector.status()["in_flight"] is False
    run(collector.close())


def test_restore_during_provider_read_discards_snapshot_and_notification(tmp_path):
    async def scenario():
        from pulse.coordinator import Coordinator

        started, release = threading.Event(), threading.Event()
        coordinator = Coordinator(tmp_path / "pulse.sqlite3")
        await coordinator.start()
        coordinator.store._clock = lambda: NOW
        archive = await coordinator.call("export_data")
        notifications = []
        provider = FakeProvider(started=started, release=release)
        collector = Collector(provider, coordinator, clock=FixedClock(), on_stored=notifications.append)
        request = asyncio.create_task(collector.request_refresh())
        try:
            await wait_thread_event(started)
            await coordinator.restore(archive)
            release.set()
            result = await request
            assert result == {"status": "discarded", "error_code": "storage_replaced"}
            assert await coordinator.call("telemetry_latest") is None
            assert (await coordinator.call("observed_events"))["total"] == 0
            assert notifications == []
            assert collector.status()["success_count"] == 0
            assert collector.status()["error_count"] == 0
        finally:
            release.set()
            await collector.close()
            await asyncio.gather(request, return_exceptions=True)
            await coordinator.close()

    run(scenario())


def test_restore_after_store_result_suppresses_observer():
    class ReplacedCoordinator(FakeCoordinator):
        async def call(self, *args, **kwargs):
            result = await super().call(*args, **kwargs)
            self.import_epoch += 1
            return result

    async def scenario():
        notifications = []
        collector = Collector(FakeProvider(), ReplacedCoordinator(), clock=FixedClock(),
                              on_stored=notifications.append)
        try:
            assert await collector.request_refresh() == {"status": "discarded", "error_code": "storage_replaced"}
            assert notifications == []
        finally:
            await collector.close()

    run(scenario())

def test_concurrent_refreshes_share_one_future_and_store_once():
    async def scenario():
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        provider = FakeProvider(started=started, release=release, finished=finished)
        coordinator = FakeCoordinator()
        collector = Collector(provider, coordinator, clock=FixedClock())
        requests = []
        try:
            first = asyncio.create_task(collector.request_refresh())
            requests.append(first)
            await wait_thread_event(started)
            second = asyncio.create_task(collector.request_refresh())
            requests.append(second)
            await asyncio.sleep(0)
            assert provider.calls == 1
            release.set()
            result_first, result_second = await asyncio.gather(first, second)

            assert result_first is result_second
            assert result_first["status"] == "stored"
            assert provider.calls == 1
            assert len(coordinator.calls) == 1
            assert collector.status()["read_count"] == 1
            assert collector.status()["success_count"] == 1
        finally:
            release.set()
            await collector.close()
            await asyncio.gather(*requests, return_exceptions=True)
            await wait_thread_event(finished)

    run(scenario())


def test_timeout_keeps_worker_guard_until_blocked_read_finishes():
    async def scenario():
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        provider = FakeProvider(started=started, release=release, finished=finished)
        coordinator = FakeCoordinator()
        clock = FixedClock()
        collector = Collector(
            provider,
            coordinator,
            interval_seconds=1,
            read_timeout_seconds=0.02,
            clock=clock,
        )
        try:
            first_result = asyncio.create_task(collector.request_refresh())
            await wait_thread_event(started)
            timed_out = await first_result
            blocked = await collector.request_refresh()

            assert timed_out == {"status": "error", "error_code": "read_timeout"}
            assert blocked["status"] == "busy"
            assert provider.calls == 1
            assert coordinator.calls == []
            assert collector.status()["in_flight"] is True

            release.set()
            await wait_thread_event(finished)
            deadline = time.monotonic() + 1
            while collector.status()["in_flight"] and time.monotonic() < deadline:
                await asyncio.sleep(0.005)
            assert collector.status()["in_flight"] is False

            # The response that arrived after the timeout is discarded. A retry is
            # allowed only after both worker completion and the bounded backoff.
            clock.advance(2)
            retried = await collector.request_refresh()
            assert retried["status"] == "stored"
            assert provider.calls == 2
            assert len(coordinator.calls) == 1
        finally:
            release.set()
            await collector.close()
            await wait_thread_event(finished)

    run(scenario())


def test_shutdown_is_bounded_when_provider_thread_is_stuck():
    async def scenario():
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        provider = FakeProvider(started=started, release=release, finished=finished)
        collector = Collector(
            provider,
            FakeCoordinator(),
            read_timeout_seconds=30,
            clock=FixedClock(),
        )
        request = asyncio.create_task(collector.request_refresh())
        try:
            await wait_thread_event(started)
            before = time.monotonic()
            closed = await collector.close()
            elapsed = time.monotonic() - before

            assert closed["status"] == "closed"
            assert elapsed < 1.25
            assert collector.status()["closed"] is True
        finally:
            release.set()
            await collector.close()
            await wait_thread_event(finished)
            await asyncio.gather(request, return_exceptions=True)

    run(scenario())


def test_quality_requires_observation_and_keeps_physical_freshness_unknown():
    empty = quality(None, NOW)
    assert empty == {
        "freshness": "unknown",
        "temporal_status": "unknown",
        "age_seconds": None,
        "observed_at": None,
        "reason_code": "no_snapshot",
    }

    missing = quality({"provider_received_at": NOW.isoformat()}, NOW)
    assert missing == {
        "freshness": "unknown",
        "temporal_status": "unknown",
        "age_seconds": None,
        "observed_at": None,
        "reason_code": "observed_at_missing",
    }

    recent = quality({"observed_at": (NOW + timedelta(seconds=30)).isoformat()}, NOW)
    assert recent["temporal_status"] == "fresh"
    assert recent["age_seconds"] == 0
    assert recent["freshness"] == "unknown"


def test_quality_marks_far_future_stale_and_expired_observations():
    future = quality({"observed_at": (NOW + timedelta(seconds=61)).isoformat()}, NOW)
    stale = quality({"observed_at": (NOW - timedelta(seconds=301)).isoformat()}, NOW)
    expired = quality({"observed_at": (NOW - timedelta(seconds=7201)).isoformat()}, NOW)

    assert future["temporal_status"] == "unknown"
    assert future["age_seconds"] is None
    assert future["reason_code"] == "observed_at_future"
    assert stale["temporal_status"] == "stale"
    assert stale["age_seconds"] == 301
    assert expired["temporal_status"] == "unavailable"
    assert expired["age_seconds"] == 7201
    assert all(item["freshness"] == "unknown" for item in (future, stale, expired))


def test_invalid_observation_and_values_are_not_replaced_with_zero():
    async def scenario():
        raw = snapshot(
            observed_at="not a timestamp",
            provider_received_at=NOW.isoformat(),
        )
        raw["vehicle"].update(
            battery_pct=math.nan,
            range_km="320",
            odometer_km=1250,
            locked=1,
        )
        provider = FakeProvider(result=raw)
        coordinator = FakeCoordinator()
        collector = Collector(provider, coordinator, clock=FixedClock())
        result = await collector.request_refresh()

        assert result["status"] == "stored"
        method, args, write, kwargs = coordinator.calls[0]
        persisted = args[0]
        assert method == "record_snapshot"
        assert write is True and kwargs == {"expected_import_epoch": 0}
        assert set(persisted) == {
            "source",
            "observed_at",
            "provider_received_at",
            "vehicle",
            "field_sources",
        }
        assert persisted["observed_at"] is None
        assert persisted["provider_received_at"] == NOW.isoformat()
        assert persisted["vehicle"] == {
            "battery_pct": None,
            "range_km": None,
            "odometer_km": 1250.0,
            "locked": None,
            "climate_on": False,
            "charging": False,
        }
        assert persisted["field_sources"] == {
            "battery_pct": None,
            "range_km": None,
            "odometer_km": "odometer_km",
            "locked": None,
            "climate_on": None,
            "charging": None,
        }
        assert "model" not in persisted["vehicle"]
        assert "freshness" not in persisted
        assert "capabilities" not in persisted
        assert "server_received_at" not in persisted
        await collector.close()

    run(scenario())


def test_provider_exception_text_never_enters_status():
    async def scenario():
        provider = FakeProvider(error=RuntimeError("private token must not escape"))
        collector = Collector(provider, FakeCoordinator(), clock=FixedClock())
        result = await collector.request_refresh()

        assert result == {"status": "error", "error_code": "provider_unavailable"}
        assert collector.status()["last_error_code"] == "provider_unavailable"
        assert "private token" not in repr(collector.status())
        assert set(collector.status()) == {
            "running",
            "closed",
            "in_flight",
            "read_count",
            "success_count",
            "error_count",
            "observer_error_count",
            "last_attempt_at",
            "next_attempt_at",
            "last_error_code",
        }
        await collector.close()

    run(scenario())
