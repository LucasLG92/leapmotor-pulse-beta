"""MQTT read-only bridge to Home Assistant, disabled unless explicitly configured."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone
import json
import socket
import ssl
import threading
from typing import Any

from pulse.mqtt_policy import (
    MqttConfig,
    discovery_messages,
    discovery_tombstones,
    prepare_publication,
)


_CONNECT_TIMEOUT_SECONDS = 10.0
_PAHO_SOCKET_TIMEOUT_SECONDS = 5.0
_PAHO_NETWORK_STOP_TIMEOUT_SECONDS = 6.0
_TRANSPORT_CLOSE_TIMEOUT_SECONDS = 7.0
_SUBACK_TIMEOUT_SECONDS = 5.0
_PUBLISH_TIMEOUT_SECONDS = 5.0
_SHUTDOWN_PUBLISH_TIMEOUT_SECONDS = 1.0
_SHUTDOWN_WAIT_SECONDS = 8.0
_MIN_RECONNECT_SECONDS = 1.0
_MAX_RECONNECT_SECONDS = 60.0
_MAX_INFLIGHT = 5
_EXPIRY_ACK_MARGIN_SECONDS = 10


class _SessionLost(Exception):
    """The MQTT connection changed while an operation was awaiting its ack."""


class _AckFailure(Exception):
    """A bounded QoS1 operation did not receive its positive broker ack."""

    def __init__(self, reason_code: str = "publish_failed") -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class _PahoTransport:
    """One disposable Paho session. A new instance is used after every failure."""

    def __init__(self, config: MqttConfig) -> None:
        self._config = config
        self._client: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._on_connect: Callable[[bool], None] | None = None
        self._on_disconnect: Callable[[], None] | None = None
        self._on_message: Callable[[str, bytes], None] | None = None
        self._pubacks: dict[int, asyncio.Future[bool]] = {}
        self._subacks: dict[int, asyncio.Future[bool]] = {}
        self._closing = False
        self._network_started = False
        self._stop_started = False
        self._stop_done = threading.Event()
        self._stop_succeeded = False
        self._stopping_client: Any = None
        self._stop_lock = threading.Lock()

    async def start(
        self,
        on_connect: Callable[[bool], None],
        on_disconnect: Callable[[], None],
        on_message: Callable[[str, bytes], None],
    ) -> None:
        """Create a clean client and start its Paho network thread."""
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            raise RuntimeError("mqtt_dependency_unavailable") from None

        self._loop = asyncio.get_running_loop()
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._on_message = on_message
        self._closing = False
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=self._config.client_id,
            clean_session=True,
            protocol=mqtt.MQTTv311,
            reconnect_on_failure=False,
        )
        self._client = client
        client.max_queued_messages_set(20)
        client.max_inflight_messages_set(_MAX_INFLIGHT)
        client.reconnect_delay_set(min_delay=1, max_delay=60)
        # Paho applies connect_timeout to TCP connect; TLS handshake uses the
        # keepalive passed to connect_async() below.
        client.connect_timeout = _PAHO_SOCKET_TIMEOUT_SECONDS
        client.will_set(
            self._config.bridge_availability_topic,
            payload="offline",
            qos=1,
            retain=True,
        )
        if self._config.username:
            client.username_pw_set(self._config.username, self._config.password or None)
        if self._config.tls:
            tls_context = ssl.create_default_context(cafile=self._config.ca_file or None)
            tls_context.check_hostname = True
            tls_context.verify_mode = ssl.CERT_REQUIRED
            client.tls_set_context(tls_context)

        client.on_connect = self._handle_connect
        client.on_connect_fail = self._handle_connect_fail
        client.on_disconnect = self._handle_disconnect
        client.on_message = self._handle_message
        client.on_publish = self._handle_publish
        client.on_subscribe = self._handle_subscribe

        # Paho uses keepalive as the timeout on the TLS socket while it performs
        # its synchronous handshake inside _create_socket(). Keep it bounded so
        # loop_stop() can join that network thread after a stalled TLS peer.
        client.connect_async(
            self._config.host,
            self._config.port,
            keepalive=int(_PAHO_SOCKET_TIMEOUT_SECONDS),
        )
        result = client.loop_start()
        if result != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError("mqtt_network_thread_start_failed")
        self._network_started = True

    async def subscribe(self, topic: str, *, qos: int, timeout: float) -> bool:
        client, loop = self._require_session()
        result, mid = client.subscribe(topic, qos=qos)
        if result != 0:
            return False
        future = loop.create_future()
        self._subacks[mid] = future
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._subacks.pop(mid, None)

    async def publish(
        self,
        topic: str,
        payload: str,
        *,
        qos: int,
        retain: bool,
        timeout: float,
    ) -> bool:
        client, loop = self._require_session()
        info = client.publish(topic, payload, qos=qos, retain=retain)
        if info.rc != 0:
            return False
        future = loop.create_future()
        self._pubacks[info.mid] = future
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pubacks.pop(info.mid, None)

    async def close(self, *, abort: bool = False) -> bool:
        """Stop the Paho thread and report only after its join has completed.

        Paho's public ``socket()`` can be None while TLS is handshaking. The
        single daemon stopper calls the public, blocking ``loop_stop()`` and
        closes any socket that became visible after the handshake. If the OS is
        stuck in DNS resolution, this method returns false after a finite wait;
        the bridge must not create a replacement client until that stopper joins.
        """
        self._closing = True
        loop = self._loop
        client = self._client or self._stopping_client
        if client is None:
            return True

        if abort:
            self._close_public_socket(client, shutdown=True)
        elif self._client is not None:
            try:
                # Preserve the graceful DISCONNECT after the bridge has acked
                # its offline availability messages.
                client.disconnect()
            except Exception:
                pass

        self._client = None
        self._stopping_client = client
        with self._stop_lock:
            if not self._stop_started:
                self._stop_started = True
                if not self._network_started:
                    self._close_public_socket(client, shutdown=abort)
                    self._stop_succeeded = True
                    self._stop_done.set()
                else:
                    stopper = threading.Thread(
                        target=self._finish_network_stop,
                        args=(client,),
                        name="pulse-mqtt-paho-stop",
                        daemon=True,
                    )
                    stopper.start()

        if loop is not None:
            self._fail_pending()
        deadline = asyncio.get_running_loop().time() + _PAHO_NETWORK_STOP_TIMEOUT_SECONDS
        while not self._stop_done.is_set() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.02)
        return self._stop_done.is_set() and self._stop_succeeded

    async def wait_closed(self) -> bool:
        """Wait for the existing stopper; never create another join worker."""
        while not self._stop_done.is_set():
            await asyncio.sleep(0.1)
        return self._stop_succeeded

    def _finish_network_stop(self, client: Any) -> None:
        succeeded = False
        try:
            import paho.mqtt.client as mqtt

            result = client.loop_stop()
            # MQTT_ERR_INVAL means Paho had already stopped its worker. Both
            # success and that already-stopped result prove there is no overlap.
            succeeded = result in (mqtt.MQTT_ERR_SUCCESS, mqtt.MQTT_ERR_INVAL)
        except Exception:
            succeeded = False
        finally:
            # A TLS handshake may have completed between the earlier public
            # socket() check and loop_stop() returning. Close it only after Paho
            # has joined its network thread, so it cannot race with that thread.
            if succeeded:
                self._close_public_socket(client, shutdown=False)
            self._stop_succeeded = succeeded
            self._stop_done.set()

    @staticmethod
    def _close_public_socket(client: Any, *, shutdown: bool) -> None:
        try:
            sock = client.socket()
        except Exception:
            return
        if sock is None:
            return
        if shutdown:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        try:
            sock.close()
        except Exception:
            pass

    def _require_session(self) -> tuple[Any, asyncio.AbstractEventLoop]:
        if self._client is None or self._loop is None or self._closing:
            raise RuntimeError("mqtt_session_unavailable")
        return self._client, self._loop

    def _dispatch(self, callback: Callable[..., None] | None, *args: Any) -> None:
        loop = self._loop
        if callback is None or loop is None or self._closing or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(callback, *args)
        except RuntimeError:
            return

    @staticmethod
    def _reason_ok(reason_code: Any) -> bool:
        failure = getattr(reason_code, "is_failure", None)
        if failure is not None:
            return not bool(failure)
        try:
            return int(reason_code) < 128
        except (TypeError, ValueError):
            return False

    def _handle_connect(self, _client: Any, _userdata: Any, _flags: Any, reason_code: Any, _properties: Any) -> None:
        self._dispatch(self._on_connect, self._reason_ok(reason_code))

    def _handle_connect_fail(self, _client: Any, _userdata: Any) -> None:
        # Paho calls this for socket/TLS failures before a CONNACK exists.
        self._dispatch(self._on_connect, False)

    def _handle_disconnect(
        self, _client: Any, _userdata: Any, _flags: Any, _reason_code: Any, _properties: Any
    ) -> None:
        loop = self._loop
        if loop is not None and not self._closing:
            try:
                loop.call_soon_threadsafe(self._fail_pending)
            except RuntimeError:
                pass
            self._dispatch(self._on_disconnect)

    def _handle_message(self, _client: Any, _userdata: Any, message: Any) -> None:
        self._dispatch(self._on_message, message.topic, bytes(message.payload))

    def _handle_publish(
        self, _client: Any, _userdata: Any, mid: int, reason_code: Any, _properties: Any
    ) -> None:
        self._dispatch(self._resolve_ack, self._pubacks, mid, self._reason_ok(reason_code))

    def _handle_subscribe(
        self, _client: Any, _userdata: Any, mid: int, reason_codes: Any, _properties: Any
    ) -> None:
        accepted = False
        if isinstance(reason_codes, (list, tuple)) and len(reason_codes) == 1:
            reason_code = reason_codes[0]
            granted_qos = getattr(reason_code, "value", None)
            accepted = (
                self._reason_ok(reason_code)
                and type(granted_qos) is int
                and granted_qos == 1
            )
        self._dispatch(self._resolve_ack, self._subacks, mid, accepted)

    @staticmethod
    def _resolve_ack(pending: dict[int, asyncio.Future[bool]], mid: int, accepted: bool) -> None:
        future = pending.get(mid)
        if future is not None and not future.done():
            future.set_result(accepted)

    def _fail_pending(self) -> None:
        for pending in (self._pubacks, self._subacks):
            for future in tuple(pending.values()):
                if not future.done():
                    future.set_result(False)
        self._pubacks.clear()
        self._subacks.clear()


class MqttBridge:
    """Poll only the local normalized store and publish selected read-only fields."""

    def __init__(
        self,
        config: MqttConfig,
        coordinator: object,
        *,
        transport: object | None = None,
        clock: Callable[[], datetime] | None = None,
        on_status_change: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        if type(config) is not MqttConfig:
            raise TypeError("MqttBridge requiere MqttConfig.")
        if not callable(getattr(coordinator, "call", None)):
            raise TypeError("MqttBridge requiere un coordinator con call().")
        if transport is not None and not callable(getattr(transport, "start", None)):
            raise TypeError("transport debe implementar start().")
        if clock is not None and not callable(clock):
            raise TypeError("clock debe ser invocable.")
        if on_status_change is not None and not callable(on_status_change):
            raise TypeError("on_status_change debe ser invocable.")

        self.config = config
        self.coordinator = coordinator
        self._transport_template = transport
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._on_status_change = on_status_change
        self._loop: asyncio.AbstractEventLoop | None = None
        self._runner: asyncio.Task[None] | None = None
        self._transport: Any = None
        self._session_lease: object | None = None
        self._started = False
        self._closing = False
        self._closed = False
        self._transport_connected = False
        self._generation = 0
        self._connection_event: asyncio.Event | None = None
        self._wake_event: asyncio.Event | None = None
        self._stop_event: asyncio.Event | None = None
        self._announce_requested = False
        self._field_online: dict[str, bool | None] = {name: None for name in config.fields}
        self._field_signatures: dict[str, tuple[Any, ...]] = {}
        self._last_published_at: str | None = None
        self._status_value: dict[str, Any] = {
            "enabled": config.enabled,
            "status": "connecting" if config.enabled else "disabled",
            "data_status": "no_data",
            "fields": list(config.fields),
            "max_age_seconds": config.max_age_seconds,
            "observed_at": None,
            "last_published_at": None,
            "reason_code": "connecting" if config.enabled else "disabled",
        }

    def status(self) -> dict[str, Any]:
        """Return the fixed-shape operational state without broker identity or secrets."""
        return {
            **self._status_value,
            "fields": list(self._status_value["fields"]),
        }

    async def start(self) -> dict[str, Any]:
        if self._closed or self._closing:
            return self.status()
        if not self.config.enabled:
            return self.status()
        if self._started:
            return self.status()
        self._started = True
        self._loop = asyncio.get_running_loop()
        self._connection_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._stop_event = asyncio.Event()
        self._runner = asyncio.create_task(self._run(), name="pulse-mqtt-bridge")
        return self.status()

    async def close(self) -> dict[str, Any]:
        if self._closed or self._closing:
            return self.status()
        self._closing = True
        if self._stop_event is not None:
            self._stop_event.set()
        if self._connection_event is not None:
            self._connection_event.set()
        if self._wake_event is not None:
            self._wake_event.set()
        runner = self._runner
        if runner is not None and not runner.done():
            done, _pending = await asyncio.wait({runner}, timeout=_SHUTDOWN_WAIT_SECONDS)
            if not done:
                if self._transport is not None:
                    stopped = await self._close_transport(abort=True, wait_for_join=False)
                    if not stopped:
                        self._set_status(status="error", reason_code="connection_failed")
                runner.cancel()
                done, _pending = await asyncio.wait({runner}, timeout=1.0)
                if not done:
                    self._set_status(status="error", reason_code="connection_failed")
        elif self._transport is not None:
            stopped = await self._close_transport(abort=True)
            if not stopped:
                self._set_status(status="error", reason_code="connection_failed")
        self._closed = True
        self._closing = False
        return self.status()

    async def _run(self) -> None:
        retry_delay = _MIN_RECONNECT_SECONDS
        try:
            while not self._closing and not self._closed:
                self._transport = self._new_transport()
                lease = object()
                self._session_lease = lease
                self._transport_connected = False
                self._field_online = {name: None for name in self.config.fields}
                self._field_signatures.clear()
                if self._connection_event is None:
                    return
                self._connection_event.clear()
                self._set_status(status="connecting", reason_code="connecting")
                try:
                    await self._transport.start(
                        lambda accepted, lease=lease: self._transport_on_connect(lease, accepted),
                        lambda lease=lease: self._transport_on_disconnect(lease),
                        lambda topic, payload, lease=lease: self._transport_on_message(lease, topic, payload),
                    )
                    try:
                        await asyncio.wait_for(
                            self._connection_event.wait(), timeout=_CONNECT_TIMEOUT_SECONDS
                        )
                    except asyncio.TimeoutError:
                        raise _AckFailure("connection_failed") from None
                    if not self._transport_connected:
                        raise _AckFailure("connection_failed") from None
                    generation = self._generation
                    subscribed = await self._transport.subscribe(
                        self.config.birth_topic,
                        qos=1,
                        timeout=_SUBACK_TIMEOUT_SECONDS,
                    )
                    self._require_live(generation)
                    if subscribed is not True:
                        raise _AckFailure("connection_failed") from None
                    retry_delay = _MIN_RECONNECT_SECONDS
                    self._announce_requested = True
                    while self._transport_connected and not self._closing and not self._closed:
                        if self._announce_requested:
                            self._announce_requested = False
                            await self._publish_cycle(generation, announce=True)
                            continue
                        assert self._wake_event is not None
                        self._wake_event.clear()
                        try:
                            await asyncio.wait_for(
                                self._wake_event.wait(), timeout=self.config.poll_seconds
                            )
                        except asyncio.TimeoutError:
                            pass
                        if self._closing or self._closed or not self._transport_connected:
                            break
                        if self._announce_requested:
                            continue
                        await self._publish_cycle(generation, announce=False)
                except _SessionLost:
                    pass
                except _AckFailure as exc:
                    self._set_status(status="error", reason_code=exc.reason_code)
                    self._transport_connected = False
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self._set_status(status="error", reason_code="connection_failed")
                    self._transport_connected = False

                if self._closing or self._closed:
                    await self._shutdown_session()
                    self._closed = True
                    return
                stopped = await self._close_transport(abort=True)
                self._transport = None
                if not stopped:
                    # A Paho thread can be stuck in an OS DNS lookup, which has
                    # no public cancellation API. Do not overlap it with a new
                    # client or accumulate more network threads.
                    self._set_status(status="error", reason_code="connection_failed")
                    self._closed = True
                    return
                if await self._wait_retry(retry_delay):
                    return
                retry_delay = min(retry_delay * 2, _MAX_RECONNECT_SECONDS)
        finally:
            if self._transport is not None:
                await self._close_transport(abort=True)
            self._transport = None

    async def _publish_cycle(self, generation: int, *, announce: bool) -> None:
        try:
            context = await self.coordinator.call("mqtt_context")
        except asyncio.CancelledError:
            raise
        except Exception:
            self._require_live(generation)
            self._set_status(
                status="connected",
                data_status="unavailable",
                reason_code="observation_unavailable",
            )
            return
        self._require_live(generation)
        if announce:
            await self._publish_tombstones(generation)
        self._require_live(generation)

        for field_name in self.config.fields:
            self._require_live(generation)
            publication = self._prepare(context)
            values = publication["values"]
            if field_name not in values:
                if announce:
                    await self._publish_discovery_field(field_name, context, generation)
                if announce or self._field_online.get(field_name) is not False:
                    await self._send(
                        self._availability_topic(field_name),
                        "offline",
                        qos=1,
                        retain=False,
                        generation=generation,
                    )
                    self._field_online[field_name] = False
                    self._field_signatures.pop(field_name, None)
                continue

            signature = self._field_signature(field_name, values[field_name], publication)
            should_publish = (
                announce
                or signature != self._field_signatures.get(field_name)
                or self._field_online.get(field_name) is not True
            )
            if should_publish:
                # The retained entity config carries only the remaining validity
                # window; renew it beside every changed state, never on heartbeat.
                await self._publish_discovery_field(field_name, context, generation)
                publication = self._prepare(context)
                values = publication["values"]
                if field_name not in values:
                    if announce or self._field_online.get(field_name) is not False:
                        await self._send(
                            self._availability_topic(field_name),
                            "offline",
                            qos=1,
                            retain=False,
                            generation=generation,
                        )
                        self._field_online[field_name] = False
                        self._field_signatures.pop(field_name, None)
                    continue
                signature = self._field_signature(field_name, values[field_name], publication)
                payload = {
                    "value": self._state_value(field_name, values[field_name]),
                    "observed_at": publication["observed_at"],
                    "source": "telemetry",
                    "physical_freshness": "unknown",
                }
                encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                await self._send(
                    f"{self.config.topic_prefix}/state/{field_name}",
                    encoded,
                    qos=1,
                    retain=False,
                    generation=generation,
                )
                self._field_signatures[field_name] = signature
                self._last_published_at = self._iso_now()
                after_ack = self._prepare(context)
                if field_name not in after_ack["values"]:
                    await self._send(
                        self._availability_topic(field_name),
                        "offline",
                        qos=1,
                        retain=False,
                        generation=generation,
                    )
                    self._field_online[field_name] = False
                    self._field_signatures.pop(field_name, None)
                else:
                    await self._send(
                        self._availability_topic(field_name),
                        "online",
                        qos=1,
                        retain=False,
                        generation=generation,
                    )
                    self._field_online[field_name] = True
                    after_availability = self._prepare(context)
                    if field_name not in after_availability["values"]:
                        await self._send(
                            self._availability_topic(field_name),
                            "offline",
                            qos=1,
                            retain=False,
                            generation=generation,
                        )
                        self._field_online[field_name] = False
                        self._field_signatures.pop(field_name, None)

        publication, any_value = await self._expire_stale_fields(context, generation)
        if announce:
            await self._send(
                self.config.bridge_availability_topic,
                "online",
                qos=1,
                retain=True,
                generation=generation,
            )
            publication, any_value = await self._expire_stale_fields(context, generation)
        self._require_live(generation)
        data_status = "published" if any_value else self._status_data_status(publication)
        reason_code = "published" if any_value else self._status_reason(publication)
        self._set_status(
            status="connected",
            data_status=data_status,
            observed_at=publication["observed_at"],
            reason_code=reason_code,
        )

    async def _publish_tombstones(self, generation: int) -> None:
        for topic, payload in discovery_tombstones(self.config):
            await self._send(topic, payload, qos=1, retain=True, generation=generation)

    async def _expire_stale_fields(
        self, context: dict[str, Any], generation: int
    ) -> tuple[dict[str, Any], bool]:
        publication = self._prepare(context)
        for field_name in self.config.fields:
            self._require_live(generation)
            if field_name not in publication["values"] and self._field_online.get(field_name) is True:
                await self._send(
                    self._availability_topic(field_name),
                    "offline",
                    qos=1,
                    retain=False,
                    generation=generation,
                )
                self._field_online[field_name] = False
                self._field_signatures.pop(field_name, None)
                publication = self._prepare(context)
        publication = self._prepare(context)
        any_value = any(
            self._field_online.get(field_name) is True
            and field_name in publication["values"]
            for field_name in self.config.fields
        )
        return publication, any_value

    async def _publish_discovery_field(
        self, field_name: str, context: dict[str, Any], generation: int
    ) -> None:
        current = self._prepare(context)
        remaining = (
            current["remaining_seconds"] - _EXPIRY_ACK_MARGIN_SECONDS
            if current["data_status"] == "ready"
            else self.config.max_age_seconds
        )
        suffix = f"/{field_name}/config"
        topic, payload = next(
            ((topic, body) for topic, body in discovery_messages(self.config, max(1, remaining))
             if topic.endswith(suffix)),
            (None, None),
        )
        if topic is not None and payload is not None:
            await self._send(
                topic,
                payload,
                qos=1,
                retain=True,
                generation=generation,
            )

    async def _send(
        self,
        topic: str,
        payload: str,
        *,
        qos: int,
        retain: bool,
        generation: int,
        timeout: float = _PUBLISH_TIMEOUT_SECONDS,
        allow_closing: bool = False,
    ) -> None:
        self._require_live(generation, allow_closing=allow_closing)
        if self._transport is None:
            raise _SessionLost
        try:
            accepted = await self._transport.publish(
                topic,
                payload,
                qos=qos,
                retain=retain,
                timeout=timeout,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            self._require_live(generation, allow_closing=allow_closing)
            raise _AckFailure from None
        self._require_live(generation, allow_closing=allow_closing)
        if accepted is not True:
            raise _AckFailure

    async def _shutdown_session(self) -> None:
        transport = self._transport
        if transport is None:
            return
        abort = not self._transport_connected
        if not abort:
            generation = self._generation
            try:
                for field_name in self.config.fields:
                    await self._send(
                        self._availability_topic(field_name),
                        "offline",
                        qos=1,
                        retain=False,
                        generation=generation,
                        timeout=_SHUTDOWN_PUBLISH_TIMEOUT_SECONDS,
                        allow_closing=True,
                    )
                await self._send(
                    self.config.bridge_availability_topic,
                    "offline",
                    qos=1,
                    retain=True,
                    generation=generation,
                    timeout=_SHUTDOWN_PUBLISH_TIMEOUT_SECONDS,
                    allow_closing=True,
                )
            except Exception:
                abort = True
        stopped = await self._close_transport(abort=abort)
        self._transport = None
        if not stopped:
            self._set_status(status="error", reason_code="connection_failed")

    async def _close_transport(self, *, abort: bool, wait_for_join: bool = True) -> bool:
        transport = self._transport
        if transport is None:
            return True
        self._transport = None
        self._session_lease = None
        self._generation += 1
        self._transport_connected = False
        try:
            result = await asyncio.wait_for(
                transport.close(abort=abort),
                timeout=_TRANSPORT_CLOSE_TIMEOUT_SECONDS,
            )
            if result is False and wait_for_join:
                # The default Paho transport has one existing stopper thread.
                # Wait for its join without creating another executor or join
                # thread; close can still cancel this coroutine at its bounded
                # shutdown deadline.
                wait_closed = getattr(transport, "wait_closed", None)
                if callable(wait_closed):
                    result = await wait_closed()
            return result is not False
        except Exception:
            return False

    async def _wait_retry(self, delay: float) -> bool:
        if self._stop_event is None:
            return True
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
            return True
        except asyncio.TimeoutError:
            return self._closed

    def _new_transport(self) -> Any:
        if self._transport_template is None:
            return _PahoTransport(self.config)
        return self._transport_template

    def _transport_on_connect(self, lease: object, accepted: bool) -> None:
        if lease is not self._session_lease or self._closed or self._closing:
            return
        loop = self._loop
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(self._handle_connect, lease, accepted)
            except RuntimeError:
                pass

    def _transport_on_disconnect(self, lease: object) -> None:
        if lease is not self._session_lease or self._closed or self._closing:
            return
        loop = self._loop
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(self._handle_disconnect, lease)
            except RuntimeError:
                pass

    def _transport_on_message(self, lease: object, topic: str, payload: bytes) -> None:
        if lease is not self._session_lease or self._closed or self._closing:
            return
        loop = self._loop
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(self._handle_message, lease, topic, payload)
            except RuntimeError:
                pass

    def _handle_connect(self, lease: object, accepted: bool) -> None:
        if lease is not self._session_lease or self._closed or self._closing:
            return
        self._generation += 1
        self._transport_connected = accepted
        if self._connection_event is not None:
            self._connection_event.set()
        if accepted:
            self._set_status(status="connecting", reason_code="connecting")
        else:
            self._set_status(status="error", reason_code="connection_failed")
        if self._wake_event is not None:
            self._wake_event.set()

    def _handle_disconnect(self, lease: object) -> None:
        if lease is not self._session_lease or self._closed or self._closing:
            return
        self._generation += 1
        self._transport_connected = False
        self._set_status(status="error", reason_code="connection_failed")
        if self._connection_event is not None:
            self._connection_event.set()
        if self._wake_event is not None:
            self._wake_event.set()

    def _handle_message(self, lease: object, topic: str, payload: bytes) -> None:
        if (
            lease is not self._session_lease
            or self._closed
            or self._closing
            or not self._transport_connected
            or topic != self.config.birth_topic
        ):
            return
        if not isinstance(payload, bytes) or len(payload) > 64 or payload.strip() != b"online":
            return
        # A single bool/event coalesces any burst of HA birth messages.
        self._announce_requested = True
        if self._wake_event is not None:
            self._wake_event.set()

    def _prepare(self, context: dict[str, Any]) -> dict[str, Any]:
        now = self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("clock_must_return_aware_datetime")
        now_utc = now.astimezone(timezone.utc)
        publication = prepare_publication(self.config, context, now_utc)
        if (
            publication["data_status"] == "ready"
            and publication["remaining_seconds"] <= _EXPIRY_ACK_MARGIN_SECONDS
        ):
            return {
                **publication,
                "data_status": "unavailable",
                "values": {},
                "source": "unavailable",
            }
        return publication

    @staticmethod
    def _field_signature(field_name: str, value: Any, publication: dict[str, Any]) -> tuple[Any, ...]:
        return (field_name, value, publication["observed_at"], publication["source"])

    @staticmethod
    def _state_value(field_name: str, value: Any) -> Any:
        if field_name == "charging":
            return "charging" if value else "not_charging"
        return value

    def _availability_topic(self, field_name: str) -> str:
        return f"{self.config.topic_prefix}/availability/{field_name}"

    def _require_live(self, generation: int, *, allow_closing: bool = False) -> None:
        if (
            ((self._closed or self._closing) and not allow_closing)
            or not self._transport_connected
            or generation != self._generation
        ):
            raise _SessionLost

    def _iso_now(self) -> str:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            return None  # type: ignore[return-value]
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _status_data_status(publication: dict[str, Any]) -> str:
        return {
            "no_data": "no_data",
            "demo": "demo",
            "unavailable": "unavailable",
        }.get(publication["data_status"], "unavailable")

    @staticmethod
    def _status_reason(publication: dict[str, Any]) -> str:
        return {
            "no_data": "waiting_observation",
            "demo": "demo_excluded",
            "unavailable": "observation_unavailable",
        }.get(publication["data_status"], "observation_unavailable")

    def _set_status(self, **changes: Any) -> None:
        if self._closed and changes.get("status") != self._status_value.get("status"):
            return
        updated = {
            **self._status_value,
            **changes,
            "last_published_at": self._last_published_at,
        }
        if updated == self._status_value:
            return
        self._status_value = updated
        callback = self._on_status_change
        if callback is not None:
            try:
                callback(self.status())
            except Exception:
                # User callbacks are observational; their details may contain secrets.
                pass
