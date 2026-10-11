import asyncio
from datetime import datetime, timedelta, timezone
import json
import socket
import threading
from unittest.mock import Mock

import pytest

import pulse.mqtt_bridge as mqtt_bridge_module
from pulse.mqtt_bridge import MqttBridge, _PahoTransport
from pulse.mqtt_policy import MqttConfig


BASE = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


class ManualClock:
    def __init__(self, value=BASE):
        self.value = value

    def __call__(self):
        return self.value


class FakeCoordinator:
    def __init__(self, context):
        self.context = context
        self.calls = []

    async def call(self, method):
        self.calls.append(method)
        assert method == "mqtt_context"
        return self.context


class FakeTransport:
    """Faithful ack boundary: only successful publish() returns count as delivery."""

    def __init__(self, *, fail_once=(), pause_topic=None, close_result=None, join_gate=None):
        self.fail_once = set(fail_once)
        self.failed_topics = set()
        self.pause_topic = pause_topic
        self.close_result = close_result
        self.join_gate = join_gate
        self.wait_closed_calls = 0
        self.pause_started = asyncio.Event()
        self.release_publish = asyncio.Event()
        self.messages = []
        self.attempts = []
        self.subscriptions = []
        self.close_calls = []
        self.starts = 0
        self.callback_sets = []
        self.on_connect = None
        self.on_disconnect = None
        self.on_message = None

    async def start(self, on_connect, on_disconnect, on_message):
        self.starts += 1
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self.on_message = on_message
        self.callback_sets.append((on_connect, on_disconnect, on_message))
        on_connect(True)

    async def subscribe(self, topic, *, qos, timeout):
        self.subscriptions.append((topic, qos, timeout))
        return True

    async def publish(self, topic, payload, *, qos, retain, timeout):
        message = {"topic": topic, "payload": payload, "qos": qos, "retain": retain}
        self.attempts.append(message)
        if topic == self.pause_topic and not self.pause_started.is_set():
            self.pause_started.set()
            await self.release_publish.wait()
        if topic in self.fail_once and topic not in self.failed_topics:
            self.failed_topics.add(topic)
            return False
        self.messages.append(message)
        return True

    async def close(self, *, abort=False):
        self.close_calls.append({"abort": abort})
        return self.close_result

    async def wait_closed(self):
        self.wait_closed_calls += 1
        if self.join_gate is not None:
            await self.join_gate.wait()
            return True
        return self.close_result is not False

    def birth(self, topic="homeassistant/status", payload=b"online"):
        self.on_message(topic, payload)

    def disconnect(self):
        self.on_disconnect()


def config(*, enabled=True, fields=("battery_pct", "charging"), max_age=900, poll=1, tls=False):
    return MqttConfig(
        enabled=enabled,
        host="127.0.0.1",
        port=1883,
        tls=tls,
        instance="garage",
        discovery_prefix="homeassistant",
        fields=tuple(fields),
        max_age_seconds=max_age,
        poll_seconds=poll,
    )


def test_paho_transport_rejects_sslkeylogfile_before_client_tls_or_socket(monkeypatch, tmp_path):
    import paho.mqtt.client as paho_client

    keylog = tmp_path / "tls-keylog.log"
    monkeypatch.setenv("SSLKEYLOGFILE", str(keylog))
    make_client = Mock()
    create_context = Mock()
    create_socket = Mock()
    monkeypatch.setattr(paho_client, "Client", make_client)
    monkeypatch.setattr(mqtt_bridge_module.ssl, "create_default_context", create_context)
    monkeypatch.setattr(mqtt_bridge_module.socket, "create_connection", create_socket)

    transport = _PahoTransport(config(tls=True))
    with pytest.raises(RuntimeError, match="mqtt_tls_key_logging_unsupported"):
        asyncio.run(transport.start(lambda _accepted: None, lambda: None, lambda _topic, _payload: None))

    make_client.assert_not_called()
    create_context.assert_not_called()
    create_socket.assert_not_called()
    assert transport._client is None
    assert transport._network_started is False
    assert not keylog.exists()


def context(*, observed_at=None, battery=74.5, charging=False, demo=False):
    return {
        "demo_enabled": demo,
        "snapshot": {
            "observed_at": (observed_at or BASE).isoformat().replace("+00:00", "Z"),
            "vehicle": {"battery_pct": battery, "range_km": 321, "charging": charging},
        },
    }


async def wait_until(predicate, timeout=2.5):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() >= deadline:
            raise AssertionError("El estado esperado no llegó dentro del límite.")
        await asyncio.sleep(0.005)


def topic_messages(transport, suffix):
    return [message for message in transport.messages if message["topic"].endswith(suffix)]


def test_disabled_is_read_only_and_never_starts_injected_transport():
    asyncio.run(_test_disabled_is_read_only_and_never_starts_injected_transport())


async def _test_disabled_is_read_only_and_never_starts_injected_transport():
    coordinator = FakeCoordinator(context())
    transport = FakeTransport()
    bridge = MqttBridge(config(enabled=False, fields=("battery_pct",)), coordinator, transport=transport)

    assert await bridge.start() == {
        "enabled": False,
        "status": "disabled",
        "data_status": "no_data",
        "fields": ["battery_pct"],
        "max_age_seconds": 900,
        "observed_at": None,
        "last_published_at": None,
        "reason_code": "disabled",
    }
    await bridge.close()

    assert coordinator.calls == []
    assert transport.starts == 0
    assert transport.close_calls == []


def test_startup_publishes_only_selected_fields_and_poll_deduplicates():
    asyncio.run(_test_startup_publishes_only_selected_fields_and_poll_deduplicates())


async def _test_startup_publishes_only_selected_fields_and_poll_deduplicates():
    coordinator = FakeCoordinator(context())
    transport = FakeTransport()
    bridge = MqttBridge(config(), coordinator, transport=transport, clock=ManualClock())
    await bridge.start()
    await wait_until(lambda: bridge.status()["status"] == "connected")

    state_messages = [m for m in transport.messages if "/state/" in m["topic"]]
    assert {m["topic"] for m in state_messages} == {
        "pulse/garage/state/battery_pct",
        "pulse/garage/state/charging",
    }
    assert all(m["qos"] == 1 and not m["retain"] for m in state_messages)
    decoded = {m["topic"].rsplit("/", 1)[-1]: json.loads(m["payload"]) for m in state_messages}
    assert decoded["battery_pct"] == {
        "value": 74.5,
        "observed_at": "2026-10-08T12:00:00Z",
        "source": "telemetry",
        "physical_freshness": "unknown",
    }
    assert decoded["charging"]["value"] == "not_charging"
    assert not any("VIN" in m["payload"] or "garage-user" in m["payload"] for m in state_messages)
    assert any(m["topic"].endswith("/range_km/config") and m["payload"] == "" and m["retain"] for m in transport.messages)
    assert any(m["topic"].endswith("/availability/bridge") and m["payload"] == "online" and m["retain"] for m in transport.messages)
    assert transport.subscriptions == [("homeassistant/status", 1, 5.0)]
    assert bridge.status()["data_status"] == "published"
    assert bridge.status()["last_published_at"] == "2026-10-08T12:00:00Z"
    assert coordinator.calls == ["mqtt_context"]

    initial_states = len(state_messages)
    initial_calls = len(coordinator.calls)
    await asyncio.sleep(1.1)
    assert len([m for m in transport.messages if "/state/" in m["topic"]]) == initial_states
    assert len(coordinator.calls) > initial_calls

    await bridge.close()
    assert transport.close_calls[-1] == {"abort": False}
    assert not any("host" in key.lower() for key in bridge.status())


def test_future_and_expired_observations_publish_only_field_unavailability():
    asyncio.run(_test_future_and_expired_observations_publish_only_field_unavailability())


async def _test_future_and_expired_observations_publish_only_field_unavailability():
    clock = ManualClock(BASE)
    coordinator = FakeCoordinator(context(observed_at=BASE + timedelta(seconds=1)))
    transport = FakeTransport()
    bridge = MqttBridge(config(max_age=30), coordinator, transport=transport, clock=clock)
    await bridge.start()
    await wait_until(lambda: bridge.status()["status"] == "connected")

    assert bridge.status()["reason_code"] == "observation_unavailable"
    assert not any("/state/" in m["topic"] for m in transport.messages)
    assert {m["payload"] for m in transport.messages if "/availability/" in m["topic"] and "/bridge" not in m["topic"]} == {"offline"}

    coordinator.context = context(observed_at=BASE)
    transport.birth()
    await wait_until(lambda: any("/state/battery_pct" in m["topic"] for m in transport.messages))
    clock.value = BASE + timedelta(seconds=31)
    transport.birth()
    await wait_until(
        lambda: bridge.status()["data_status"] == "unavailable"
        and any(m["topic"].endswith("/availability/battery_pct") and m["payload"] == "offline" for m in transport.messages)
    )
    assert len([m for m in transport.messages if m["topic"].endswith("/state/battery_pct")]) == 1
    assert all(not m["retain"] for m in transport.messages if "/state/" in m["topic"])
    await bridge.close()


def test_home_assistant_birth_reannounces_discovery_and_fresh_state():
    asyncio.run(_test_home_assistant_birth_reannounces_discovery_and_fresh_state())


async def _test_home_assistant_birth_reannounces_discovery_and_fresh_state():
    coordinator = FakeCoordinator(context())
    transport = FakeTransport()
    bridge = MqttBridge(config(), coordinator, transport=transport, clock=ManualClock())
    await bridge.start()
    await wait_until(lambda: bridge.status()["status"] == "connected")
    initial_discovery = len([m for m in transport.messages if m["topic"].endswith("/config")])
    initial_state = len([m for m in transport.messages if "/state/" in m["topic"]])

    transport.birth(payload=b"offline")
    transport.birth(topic="some/other/status")
    await asyncio.sleep(0.05)
    assert len([m for m in transport.messages if m["topic"].endswith("/config")]) == initial_discovery

    transport.birth()
    await wait_until(
        lambda: len([m for m in transport.messages if m["topic"].endswith("/config")]) > initial_discovery
        and len([m for m in transport.messages if "/state/" in m["topic"]]) > initial_state
    )
    discovery = [m for m in transport.messages if m["topic"].endswith("/config")]
    assert all(m["retain"] and m["qos"] == 1 for m in discovery)
    assert json.loads(discovery[-1]["payload"])["expire_after"] == 890
    assert coordinator.calls == ["mqtt_context", "mqtt_context"]
    await bridge.close()


def test_partial_publish_failure_discards_session_then_reannounces_after_reconnect():
    asyncio.run(_test_partial_publish_failure_discards_session_then_reannounces_after_reconnect())


async def _test_partial_publish_failure_discards_session_then_reannounces_after_reconnect():
    coordinator = FakeCoordinator(context())
    transport = FakeTransport(fail_once=("pulse/garage/state/battery_pct",))
    statuses = []
    bridge = MqttBridge(
        config(fields=("battery_pct",)),
        coordinator,
        transport=transport,
        clock=ManualClock(),
        on_status_change=statuses.append,
    )
    await bridge.start()
    await wait_until(lambda: transport.starts >= 2 and bridge.status()["status"] == "connected")

    attempted = [m for m in transport.attempts if m["topic"].endswith("/state/battery_pct")]
    acknowledged = [m for m in transport.messages if m["topic"].endswith("/state/battery_pct")]
    assert len(attempted) == 2
    assert len(acknowledged) == 1
    assert any(status["reason_code"] == "publish_failed" for status in statuses)
    assert transport.close_calls[0] == {"abort": True}
    assert bridge.status()["last_published_at"] == "2026-10-08T12:00:00Z"
    await bridge.close()


def test_bridge_does_not_retry_when_transport_cannot_confirm_thread_join():
    asyncio.run(_test_bridge_does_not_retry_when_transport_cannot_confirm_thread_join())


async def _test_bridge_does_not_retry_when_transport_cannot_confirm_thread_join():
    coordinator = FakeCoordinator(context())
    transport = FakeTransport(
        fail_once=("pulse/garage/state/battery_pct",),
        close_result=False,
    )
    bridge = MqttBridge(
        config(fields=("battery_pct",)),
        coordinator,
        transport=transport,
        clock=ManualClock(),
    )
    await bridge.start()
    await wait_until(lambda: bridge.status()["reason_code"] == "connection_failed")
    assert transport.close_calls == [{"abort": True}]
    assert transport.starts == 1

    await asyncio.sleep(1.1)
    assert transport.starts == 1
    assert coordinator.calls == ["mqtt_context"]
    await bridge.close()


def test_supervisor_retries_only_after_existing_stopper_joins():
    asyncio.run(_test_supervisor_retries_only_after_existing_stopper_joins())


async def _test_supervisor_retries_only_after_existing_stopper_joins():
    coordinator = FakeCoordinator(context())
    join_gate = asyncio.Event()
    transport = FakeTransport(
        fail_once=("pulse/garage/state/battery_pct",),
        close_result=False,
        join_gate=join_gate,
    )
    bridge = MqttBridge(
        config(fields=("battery_pct",)),
        coordinator,
        transport=transport,
        clock=ManualClock(),
    )
    await bridge.start()
    await wait_until(lambda: transport.wait_closed_calls == 1)
    await asyncio.sleep(1.1)
    assert transport.starts == 1

    join_gate.set()
    await wait_until(lambda: transport.starts == 2 and bridge.status()["status"] == "connected")
    await bridge.close()


def test_callbacks_from_discarded_generation_cannot_change_new_session():
    asyncio.run(_test_callbacks_from_discarded_generation_cannot_change_new_session())


async def _test_callbacks_from_discarded_generation_cannot_change_new_session():
    coordinator = FakeCoordinator(context())
    transport = FakeTransport(fail_once=("pulse/garage/state/battery_pct",))
    bridge = MqttBridge(
        config(fields=("battery_pct",)), coordinator, transport=transport, clock=ManualClock()
    )
    await bridge.start()
    await wait_until(lambda: transport.starts >= 2 and bridge.status()["status"] == "connected")
    old_connect, old_disconnect, old_message = transport.callback_sets[0]
    current_starts = transport.starts
    current_discovery = len([m for m in transport.messages if m["topic"].endswith("/config")])

    old_connect(False)
    old_disconnect()
    old_message("homeassistant/status", b"online")
    await asyncio.sleep(0.05)

    assert transport.starts == current_starts
    assert bridge.status()["status"] == "connected"
    assert len([m for m in transport.messages if m["topic"].endswith("/config")]) == current_discovery
    await bridge.close()


def test_disconnect_while_publish_is_pending_never_claims_a_puback():
    asyncio.run(_test_disconnect_while_publish_is_pending_never_claims_a_puback())


async def _test_disconnect_while_publish_is_pending_never_claims_a_puback():
    coordinator = FakeCoordinator(context())
    transport = FakeTransport(pause_topic="pulse/garage/state/battery_pct")
    bridge = MqttBridge(
        config(fields=("battery_pct",)),
        coordinator,
        transport=transport,
        clock=ManualClock(),
    )
    await bridge.start()
    await asyncio.wait_for(transport.pause_started.wait(), timeout=2.0)
    transport.disconnect()
    await wait_until(lambda: bridge.status()["reason_code"] == "connection_failed")

    close_task = asyncio.create_task(bridge.close())
    transport.release_publish.set()
    await asyncio.wait_for(close_task, timeout=2.0)

    assert bridge.status()["last_published_at"] is None
    assert bridge.status()["data_status"] == "no_data"
    # Even a late transport result cannot override the generation invalidated by
    # disconnect, so bridge status never claims this publication was delivered.
    assert any(m["topic"].endswith("/state/battery_pct") for m in transport.messages)
    assert bridge.status()["reason_code"] == "connection_failed"
    assert transport.close_calls[-1] == {"abort": True}
    starts = transport.starts
    await asyncio.sleep(1.1)
    assert transport.starts == starts


def test_status_is_fixed_and_redacts_broker_identity_and_credentials():
    asyncio.run(_test_status_is_fixed_and_redacts_broker_identity_and_credentials())


async def _test_status_is_fixed_and_redacts_broker_identity_and_credentials():
    secret_config = MqttConfig(
        enabled=True,
        host="broker-private.example",
        port=8883,
        username="private-user",
        password="private-password",
        tls=True,
        instance="garage",
        fields=("battery_pct",),
    )
    bridge = MqttBridge(secret_config, FakeCoordinator(context()), transport=FakeTransport())

    status = bridge.status()
    assert set(status) == {
        "enabled", "status", "data_status", "fields", "max_age_seconds",
        "observed_at", "last_published_at", "reason_code",
    }
    assert "broker-private.example" not in repr(status)
    assert "private-user" not in repr(status)
    assert "private-password" not in repr(status)
    await bridge.close()


def test_paho_v2_reason_codes_use_ack_metadata_without_integer_coercion():
    asyncio.run(_test_paho_v2_reason_codes_use_ack_metadata_without_integer_coercion())


async def _test_paho_v2_reason_codes_use_ack_metadata_without_integer_coercion():
    from paho.mqtt.packettypes import PacketTypes
    from paho.mqtt.reasoncodes import ReasonCode

    transport = _PahoTransport(config())
    loop = asyncio.get_running_loop()
    transport._loop = loop

    granted = ReasonCode(PacketTypes.SUBACK, identifier=1)
    rejected = ReasonCode(PacketTypes.SUBACK, identifier=128)
    assert granted.value == 1
    assert not hasattr(granted, "__int__")

    accepted_suback = loop.create_future()
    transport._subacks[41] = accepted_suback
    transport._handle_subscribe(None, None, 41, [granted], None)
    await asyncio.sleep(0)
    assert await accepted_suback is True

    rejected_suback = loop.create_future()
    transport._subacks[42] = rejected_suback
    transport._handle_subscribe(None, None, 42, [rejected], None)
    await asyncio.sleep(0)
    assert await rejected_suback is False

    accepted_puback = loop.create_future()
    transport._pubacks[43] = accepted_puback
    transport._handle_publish(
        None, None, 43, ReasonCode(PacketTypes.PUBACK, identifier=0), None
    )
    await asyncio.sleep(0)
    assert await accepted_puback is True

    rejected_puback = loop.create_future()
    transport._pubacks[44] = rejected_puback
    transport._handle_publish(
        None, None, 44, ReasonCode(PacketTypes.PUBACK, identifier=128), None
    )
    await asyncio.sleep(0)
    assert await rejected_puback is False


def test_stalled_paho_tls_handshake_is_joined_before_close_returns():
    asyncio.run(_test_stalled_paho_tls_handshake_is_joined_before_close_returns())


async def _test_stalled_paho_tls_handshake_is_joined_before_close_returns():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(2.0)
    accepted = threading.Event()
    peer_closed = threading.Event()
    connections = []

    def stall_tls_handshake():
        connection = None
        try:
            connection, _address = listener.accept()
            connection.settimeout(None)
            connections.append(connection)
            accepted.set()
            while True:
                if not connection.recv(16_384):
                    peer_closed.set()
                    break
        except OSError:
            pass
        finally:
            if connection is not None:
                connection.close()

    server = threading.Thread(target=stall_tls_handshake, name="pulse-test-stalled-tls", daemon=True)
    server.start()
    broker_config = MqttConfig(
        enabled=True,
        host="127.0.0.1",
        port=listener.getsockname()[1],
        tls=True,
        instance="stalled-tls",
        fields=("battery_pct",),
    )
    transport = _PahoTransport(broker_config)
    try:
        await transport.start(lambda _accepted: None, lambda: None, lambda _topic, _payload: None)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 2.0
        while not accepted.is_set() and loop.time() < deadline:
            await asyncio.sleep(0.01)
        assert accepted.is_set(), "Paho no llegó al peer TLS local."

        started = loop.time()
        joined = await transport.close(abort=True)
        elapsed = loop.time() - started
        assert joined is True
        assert elapsed <= 6.5

        deadline = loop.time() + 1.0
        while not peer_closed.is_set() and loop.time() < deadline:
            await asyncio.sleep(0.01)
        assert peer_closed.is_set(), "El socket TLS tardío no se cerró al unirse el loop de Paho."
    finally:
        listener.close()
        for connection in connections:
            connection.close()
        server.join(timeout=1.0)
