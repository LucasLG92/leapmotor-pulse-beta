from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import threading
from urllib.parse import parse_qs, urlsplit

import pytest

import pulse.abrp as abrp_module
from pulse.abrp import AbrpBridge, AbrpTransport, AbrpTransportError
from pulse.abrp_policy import AbrpConfig
from pulse.domain import DomainError


UTC = timezone.utc
NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


def snapshot(**updates):
    value = {
        "id": "b" * 32,
        "source": "MATE-API B10ReadClient",
        "observed_at": NOW.isoformat(),
        "vehicle": {"battery_pct": 72.5},
        "field_sources": {"battery_pct": "precise_soc_percent"},
    }
    value.update(updates)
    return value


class FixedClock:
    def __init__(self, value=NOW):
        self.value = value

    def __call__(self):
        return self.value


class FakeCoordinator:
    def __init__(self):
        self.import_epoch = 0
        self.demo = False
        self.snapshot = None
        self.scope = None
        self.consent = False
        self.generation = 0
        self.last_attempt_key = None
        self.last_attempt_at = None
        self.reserve_count = 0
        self.calls = []
        self.block_preflight = False
        self.preflight_started = None
        self.preflight_gate = None

    async def call(self, method, *args, write=False, expected_import_epoch=None, **kwargs):
        self.calls.append((method, args, write, kwargs))
        if expected_import_epoch is not None and expected_import_epoch != self.import_epoch:
            return None
        if method == "abrp_context":
            if (self.block_preflight and self.reserve_count and self.preflight_started is not None
                    and not self.preflight_started.is_set()):
                self.preflight_started.set()
                await self.preflight_gate.wait()
            same_scope = args[0] == self.scope
            return {
                "demo_enabled": self.demo,
                "snapshot": None if self.demo else self.snapshot,
                "consent": bool(same_scope and self.consent),
                "generation": self.generation,
                "last_attempt_key": self.last_attempt_key if same_scope else None,
                "last_attempt_at": self.last_attempt_at if same_scope else None,
            }
        if method == "set_abrp_consent":
            scope, enabled = args
            if enabled and self.demo:
                raise DomainError("Demo excluida.", 409)
            self.generation += 1
            if scope != self.scope:
                self.last_attempt_key = None
                self.last_attempt_at = None
            self.scope = scope
            self.consent = enabled
            return {"consent": enabled, "generation": self.generation}
        if method == "reserve_abrp":
            scope, generation, key, snapshot_id = args
            attempt_at = kwargs["attempt_at"]
            interval = kwargs["send_interval_seconds"]
            if (self.demo or not self.consent or scope != self.scope
                    or generation != self.generation or self.snapshot is None
                    or self.snapshot["id"] != snapshot_id or self.last_attempt_key == key):
                return False
            if self.last_attempt_at is not None:
                previous = datetime.fromisoformat(self.last_attempt_at)
                if (attempt_at - previous).total_seconds() < interval:
                    return False
            self.last_attempt_key = key
            self.last_attempt_at = attempt_at.astimezone(UTC).isoformat()
            self.reserve_count += 1
            return True
        raise AssertionError(f"Unexpected Coordinator method: {method}")


class FakeSession:
    def __init__(self):
        self.closed = False


class FakeTransport:
    """Synthetic transport with the same TLS/revalidation/send boundary."""

    def __init__(self, *, block_send=False, ack=True):
        self.block_send = block_send
        self.ack = ack
        self.connected = threading.Event()
        self.send_started = threading.Event()
        self.release_send = threading.Event()
        self.finished = threading.Event()
        self.sessions = []
        self.calls = []
        self.payload = None

    def connect(self, timeout_seconds):
        session = FakeSession()
        self.sessions.append(session)
        self.connected.set()
        return session

    def send(
        self,
        session,
        api_key,
        user_token,
        *,
        utc_seconds,
        soc_percent,
        timeout_seconds,
        request_lock,
        authorize,
    ):
        try:
            with request_lock:
                if not authorize():
                    return False
                self.calls.append((api_key, user_token, timeout_seconds))
                self.payload = {"utc": utc_seconds, "soc": soc_percent}
                self.send_started.set()
            if self.block_send and not self.release_send.wait(2):
                raise TimeoutError
            return self.ack
        finally:
            self.close(session)
            self.finished.set()

    @staticmethod
    def close(session):
        session.closed = True


def run(coroutine):
    return asyncio.run(coroutine)


async def start_without_runner(bridge):
    await bridge.start()
    # Let start's initial local poll finish, then use poll_once deterministically.
    await wait_until(lambda: any(call[0] == "abrp_context" for call in bridge.coordinator.calls))
    await asyncio.sleep(0.01)
    if bridge._runner is not None:
        bridge._runner.cancel()
        await asyncio.gather(bridge._runner, return_exceptions=True)
        bridge._runner = None


def make_bridge(*, coordinator=None, transport=None, clock=None, config=None):
    coordinator = coordinator or FakeCoordinator()
    transport = transport or FakeTransport()
    config = config or AbrpConfig(
        enabled=True, api_key="synthetic-key", user_token="synthetic-token"
    )
    return AbrpBridge(config, coordinator, transport=transport, clock=clock or FixedClock()), coordinator, transport


async def wait_thread_event(event):
    assert await asyncio.wait_for(asyncio.to_thread(event.wait, 1), timeout=1.5)


async def wait_until(predicate):
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0.005)
    assert predicate()


def test_disabled_bridge_does_not_touch_coordinator_or_transport():
    async def scenario():
        coordinator = FakeCoordinator()
        transport = FakeTransport()
        bridge = AbrpBridge(AbrpConfig(), coordinator, transport=transport, clock=FixedClock())
        try:
            status = await bridge.start()
            assert status == {
                "enabled": False,
                "consent": False,
                "status": "disabled",
                "reason_code": "disabled",
                "fields": ["battery_pct", "observed_at"],
                "max_age_seconds": 300,
                "last_shared_at": None,
                "validation": "not_qualified",
            }
            await bridge.poll_once()
            assert coordinator.calls == []
            assert transport.sessions == []
            assert await bridge.close() == status
        finally:
            await bridge.close()

    run(scenario())


def test_only_a_new_live_callback_and_explicit_consent_can_share_minimal_soc():
    async def scenario():
        bridge, coordinator, transport = make_bridge()
        try:
            await start_without_runner(bridge)
            coordinator.snapshot = snapshot()
            assert transport.calls == []
            await bridge.set_consent(True)
            bridge.observe(coordinator.snapshot)
            result = await bridge.poll_once()
            assert result["status"] == "shared"
            assert result["reason_code"] == "shared"
            assert result["validation"] == "not_qualified"
            assert result["fields"] == ["battery_pct", "observed_at"]
            assert transport.calls[0][:2] == ("synthetic-key", "synthetic-token")
            assert 0 < transport.calls[0][2] <= 8
            assert transport.payload == {"utc": int(NOW.timestamp()), "soc": 72.5}
            assert len(coordinator.calls) >= 3
            assert coordinator.reserve_count == 1
            assert set(bridge.status()) == {
                "enabled", "consent", "status", "reason_code", "fields",
                "max_age_seconds", "last_shared_at", "validation",
            }

            # A second poll of the same acknowledged value remains shared and is deduplicated.
            assert (await bridge.poll_once())["reason_code"] == "shared"
            assert len(transport.calls) == 1
        finally:
            transport.release_send.set()
            await bridge.close()

    run(scenario())


def test_shared_status_expires_by_observation_time_and_invalidation_clears_it():
    async def scenario():
        clock = FixedClock()
        bridge, coordinator, transport = make_bridge(clock=clock)
        try:
            await start_without_runner(bridge)
            await bridge.set_consent(True)
            coordinator.snapshot = snapshot()
            bridge.observe(coordinator.snapshot)
            await bridge.poll_once()
            assert bridge.status()["status"] == "shared"
            call_count = len(coordinator.calls)

            clock.value += timedelta(seconds=301)
            expired = bridge.status()
            assert expired["status"] == "waiting"
            assert expired["reason_code"] == "observation_stale"
            assert expired["last_shared_at"] is not None
            assert len(coordinator.calls) == call_count  # status freshness is RAM-only

            bridge.invalidate()
            invalidated = bridge.status()
            assert invalidated["reason_code"] == "waiting_observation"
            assert invalidated["last_shared_at"] is None
            assert invalidated["consent"] is True

            bridge.invalidate(clear_consent=True)
            revoked = bridge.status()
            assert revoked["status"] == "paused"
            assert revoked["reason_code"] == "consent_required"
            assert revoked["consent"] is False
        finally:
            await bridge.close()

    run(scenario())


def test_restart_waits_for_a_new_callback_even_when_store_has_a_recent_snapshot():
    async def scenario():
        config = AbrpConfig(enabled=True, api_key="synthetic-key", user_token="synthetic-token")
        coordinator = FakeCoordinator()
        coordinator.scope = config.scope
        coordinator.consent = True
        coordinator.generation = 1
        coordinator.snapshot = snapshot()
        transport = FakeTransport()
        bridge = AbrpBridge(config, coordinator, transport=transport, clock=FixedClock())
        try:
            await start_without_runner(bridge)
            result = await bridge.poll_once()
            assert result["status"] == "waiting"
            assert result["reason_code"] == "waiting_observation"
            assert transport.calls == []
            assert coordinator.reserve_count == 0
        finally:
            await bridge.close()

    run(scenario())


def test_demo_stale_and_invalid_callback_are_excluded():
    async def scenario():
        bridge, coordinator, transport = make_bridge()
        try:
            coordinator.demo = True
            coordinator.scope = bridge.config.scope
            coordinator.consent = True
            coordinator.generation = 1
            coordinator.snapshot = snapshot()
            await start_without_runner(bridge)
            assert (await bridge.poll_once())["reason_code"] == "demo_excluded"
            assert transport.calls == []

            coordinator.demo = False
            coordinator.consent = True
            coordinator.generation += 1
            stale = snapshot(observed_at=(NOW - timedelta(seconds=301)).isoformat())
            coordinator.snapshot = stale
            bridge.observe(stale)
            assert (await bridge.poll_once())["reason_code"] == "observation_stale"

            invalid = snapshot(source="configured_provider")
            coordinator.snapshot = invalid
            bridge.observe(invalid)
            assert (await bridge.poll_once())["reason_code"] == "observation_invalid"
            assert coordinator.reserve_count == 0
            assert transport.calls == []
        finally:
            await bridge.close()

    run(scenario())


def test_persisted_cooldown_blocks_a_distinct_candidate_before_reserving():
    async def scenario():
        config = AbrpConfig(enabled=True, api_key="synthetic-key", user_token="synthetic-token")
        coordinator = FakeCoordinator()
        coordinator.scope = config.scope
        coordinator.consent = True
        coordinator.generation = 1
        coordinator.last_attempt_key = "c" * 64
        coordinator.last_attempt_at = (NOW - timedelta(seconds=299)).isoformat()
        coordinator.snapshot = snapshot()
        transport = FakeTransport()
        bridge = AbrpBridge(config, coordinator, transport=transport, clock=FixedClock())
        try:
            await start_without_runner(bridge)
            bridge.observe(coordinator.snapshot)
            result = await bridge.poll_once()
            assert result["status"] == "paused"
            assert result["reason_code"] == "already_attempted"
            assert coordinator.reserve_count == 0
            assert transport.calls == []
        finally:
            await bridge.close()

    run(scenario())


def test_revocation_after_tls_connect_prevents_the_post():
    async def scenario():
        bridge, coordinator, transport = make_bridge()
        try:
            await start_without_runner(bridge)
            await bridge.set_consent(True)
            coordinator.snapshot = snapshot()
            coordinator.block_preflight = True
            coordinator.preflight_started = asyncio.Event()
            coordinator.preflight_gate = asyncio.Event()
            bridge.observe(coordinator.snapshot)
            attempt = asyncio.create_task(bridge.poll_once())
            await asyncio.wait_for(coordinator.preflight_started.wait(), timeout=1)

            # set_consent invalidates the RAM latch before its Coordinator write.
            await bridge.set_consent(False)
            coordinator.preflight_gate.set()
            result = await attempt
            assert result["reason_code"] == "consent_required"
            assert transport.calls == []
            assert transport.sessions[0].closed is True
        finally:
            coordinator.preflight_gate.set() if coordinator.preflight_gate is not None else None
            await bridge.close()

    run(scenario())


def test_import_during_an_already_started_request_cannot_be_reported_shared():
    async def scenario():
        bridge, coordinator, transport = make_bridge(transport=FakeTransport(block_send=True))
        try:
            await start_without_runner(bridge)
            await bridge.set_consent(True)
            coordinator.snapshot = snapshot()
            bridge.observe(coordinator.snapshot)
            attempt = asyncio.create_task(bridge.poll_once())
            await wait_thread_event(transport.send_started)

            # A valid import advances the database epoch and invalidates the RAM latch.
            coordinator.import_epoch += 1
            bridge.invalidate(clear_consent=True)
            transport.release_send.set()
            result = await attempt
            assert result["status"] == "paused"
            assert result["reason_code"] == "consent_required"
            assert result["consent"] is False
            assert result["last_shared_at"] is None
            assert transport.sessions[0].closed is True
        finally:
            transport.release_send.set()
            await bridge.close()

    run(scenario())


def test_revocation_during_an_already_started_request_never_reports_shared():
    async def scenario():
        bridge, coordinator, transport = make_bridge(transport=FakeTransport(block_send=True))
        try:
            await start_without_runner(bridge)
            await bridge.set_consent(True)
            coordinator.snapshot = snapshot()
            bridge.observe(coordinator.snapshot)
            attempt = asyncio.create_task(bridge.poll_once())
            await wait_thread_event(transport.send_started)

            await asyncio.wait_for(bridge.set_consent(False), timeout=0.1)
            assert bridge.status()["reason_code"] == "consent_required"
            transport.release_send.set()
            result = await attempt
            assert result["status"] == "paused"
            assert result["reason_code"] == "consent_required"
            assert result["last_shared_at"] is None
            assert transport.sessions[0].closed is True
        finally:
            transport.release_send.set()
            await bridge.close()

    run(scenario())


def test_timeout_keeps_single_worker_latched_and_deduplicates_the_attempt(monkeypatch):
    async def scenario():
        monkeypatch.setattr(abrp_module, "NETWORK_TIMEOUT_SECONDS", 0.03)
        bridge, coordinator, transport = make_bridge(transport=FakeTransport(block_send=True))
        try:
            await start_without_runner(bridge)
            await bridge.set_consent(True)
            coordinator.snapshot = snapshot()
            bridge.observe(coordinator.snapshot)
            first = await bridge.poll_once()
            assert first["status"] == "error"
            assert first["reason_code"] == "send_failed"

            second = await bridge.poll_once()
            assert second["status"] == "sharing"
            assert transport.calls and len(transport.calls) == 1
            assert len(transport.sessions) == 1

            transport.release_send.set()
            await wait_thread_event(transport.finished)
            await wait_until(lambda: bridge._inflight is False)
            after = await bridge.poll_once()
            assert after["reason_code"] == "already_attempted"
            assert len(transport.calls) == 1
        finally:
            transport.release_send.set()
            await bridge.close()

    run(scenario())


class FakeSocket:
    def __init__(self):
        self.timeouts = []

    def settimeout(self, value):
        self.timeouts.append(value)


class FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self.body = body

    def read(self, limit):
        return self.body


class FakeHTTPSConnection:
    instances = []
    response_status = 200
    response_body = b'{"status":"ok"}'
    request_started = None
    request_release = None

    def __init__(self, host, *, timeout, context):
        self.host = host
        self.port = 443
        self.timeout = timeout
        self.context = context
        self.debuglevel = None
        self.sock = FakeSocket()
        self.request_args = None
        self.closed = False
        self.instances.append(self)

    def set_debuglevel(self, value):
        self.debuglevel = value

    def connect(self):
        return None

    def request(self, method, path, *, body, headers):
        self.request_args = (method, path, body, headers)
        if self.request_started is not None:
            self.request_started.set()
        if self.request_release is not None:
            if not self.request_release.wait(1):
                raise TimeoutError

    def getresponse(self):
        return FakeResponse(self.response_status, self.response_body)

    def close(self):
        self.closed = True
        self.sock = None


def test_stdlib_transport_posts_only_contract_fields_and_requires_exact_ack(monkeypatch):
    FakeHTTPSConnection.instances.clear()
    FakeHTTPSConnection.response_status = 200
    FakeHTTPSConnection.response_body = b'{"status":"ok"}'
    monkeypatch.setattr(abrp_module.http.client, "HTTPSConnection", FakeHTTPSConnection)
    transport = AbrpTransport()
    session = transport.connect(8)
    permitted = []
    result = transport.send(
        session,
        "key/with?symbols",
        "synthetic-user-token",
        utc_seconds=int(NOW.timestamp()),
        soc_percent=72.5,
        timeout_seconds=7,
        request_lock=threading.Lock(),
        authorize=lambda: permitted.append(True) or True,
    )
    connection = FakeHTTPSConnection.instances[-1]
    method, path, body, headers = connection.request_args
    payload = json.loads(body)
    assert result is True
    assert connection.host == "api.iternio.com"
    assert connection.debuglevel == 0
    assert method == "POST"
    assert urlsplit(path).path == "/1/tlm/bulk"
    assert parse_qs(urlsplit(path).query) == {"api_key": ["key/with?symbols"]}
    assert payload == {
        "data": [
            {
                "token": "synthetic-user-token",
                "tlm_list": [{"utc": int(NOW.timestamp()), "soc": 72.5}],
            }
        ]
    }
    assert set(payload["data"][0]) == {"token", "tlm_list"}
    assert headers["Content-Type"] == "application/json"
    assert "api_key" not in headers
    assert permitted == [True]
    assert connection.closed is True


def test_transport_rejects_redirects_malformed_duplicate_and_oversized_ack(monkeypatch):
    monkeypatch.setattr(abrp_module.http.client, "HTTPSConnection", FakeHTTPSConnection)
    transport = AbrpTransport()
    cases = [
        (302, b'{"status":"ok"}'),
        (200, b"not-json"),
        (200, b'{"status":"ok","status":"ok"}'),
        (200, b"{" + b" " * (64 * 1024) + b"}"),
        (200, b'{"status":"error"}'),
    ]
    for status, body in cases:
        FakeHTTPSConnection.response_status = status
        FakeHTTPSConnection.response_body = body
        session = transport.connect(8)
        with pytest.raises(AbrpTransportError):
            transport.send(
                session,
                "synthetic-key",
                "synthetic-token",
                utc_seconds=int(NOW.timestamp()),
                soc_percent=72.5,
                timeout_seconds=7,
                request_lock=threading.Lock(),
                authorize=lambda: True,
            )
        assert session.closed is True


def test_transport_closes_session_without_post_when_permission_latch_is_clear(monkeypatch):
    monkeypatch.setattr(abrp_module.http.client, "HTTPSConnection", FakeHTTPSConnection)
    transport = AbrpTransport()
    session = transport.connect(8)
    result = transport.send(
        session,
        "synthetic-key",
        "synthetic-token",
        utc_seconds=int(NOW.timestamp()),
        soc_percent=72.5,
        timeout_seconds=7,
        request_lock=threading.Lock(),
        authorize=lambda: False,
    )
    assert result is False
    assert session.request_args is None
    assert session.closed is True


def test_transport_releases_lifecycle_lock_before_blocking_request(monkeypatch):
    monkeypatch.setattr(abrp_module.http.client, "HTTPSConnection", FakeHTTPSConnection)
    FakeHTTPSConnection.response_status = 200
    FakeHTTPSConnection.response_body = b'{"status":"ok"}'
    FakeHTTPSConnection.request_started = threading.Event()
    FakeHTTPSConnection.request_release = threading.Event()
    transport = AbrpTransport()
    session = transport.connect(8)
    request_lock = threading.Lock()
    outcomes = []

    def send():
        outcomes.append(transport.send(
            session,
            "synthetic-key",
            "synthetic-token",
            utc_seconds=int(NOW.timestamp()),
            soc_percent=72.5,
            timeout_seconds=7,
            request_lock=request_lock,
            authorize=lambda: True,
        ))

    worker = threading.Thread(target=send)
    worker.start()
    try:
        assert FakeHTTPSConnection.request_started.wait(1)
        # Lifecycle invalidation can acquire the gate while the remote side is slow.
        assert request_lock.acquire(timeout=0.1)
        request_lock.release()
    finally:
        FakeHTTPSConnection.request_release.set()
        worker.join(timeout=1)
        FakeHTTPSConnection.request_started = None
        FakeHTTPSConnection.request_release = None
    assert not worker.is_alive()
    assert outcomes == [True]
