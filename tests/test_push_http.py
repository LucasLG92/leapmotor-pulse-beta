"""BFF de avisos con HTTP/SQLite reales y transporte sin red ni permisos."""

import asyncio
from datetime import datetime, timedelta, timezone
from functools import partial
import json
import os
import sqlite3
import time

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
import http_ece
import pytest

from pulse.app import create_app
from pulse.config import Config
from pulse.collector import Collector
from pulse.domain import DomainError
from pulse.push_policy import PushConfig, encode_url64


class Transport:
    def __init__(self, status=201):
        self.status, self.calls, self.starts, self.closes = status, [], 0, 0
        self.error = None

    async def start(self):
        self.starts += 1

    async def send(self, endpoint, headers, body):
        self.calls.append((endpoint, headers, body))
        if self.error:
            raise self.error
        return self.status

    async def close(self):
        self.closes += 1


def selected(*, enabled=True):
    private = ec.generate_private_key(ec.SECP256R1())
    push = PushConfig(enabled=True,
        vapid_private_key=encode_url64(private.private_numbers().private_value.to_bytes(32, "big")),
        storage_key=Fernet.generate_key().decode(), contact="mailto:operator@example.org") if enabled else PushConfig()
    return Config(auth_mode="oidc", public_url="https://pulse.example.org", oidc_issuer="https://id.example.org",
        oidc_client_id="pulse-web", oidc_client_secret="test-only-credential", allowed_users=("fixture",),
        hosts=("pulse.example.org",), push=push)


def attach(client, app, owner="fixture-first"):
    sid, record = app.state.sessions.create({"id": owner, "name": "Fixture"})
    client.cookies.set(app.state.config.cookie_name, sid, domain="pulse.example.org", path="/")
    client.headers["X-Pulse-CSRF"] = record.csrf
    return sid, record


def payload(endpoint="https://fcm.googleapis.com/wp/test-only-endpoint", **changes):
    private = ec.generate_private_key(ec.SECP256R1())
    auth = os.urandom(16)
    data = {"name": "Móvil de pruebas", "subscription": {"endpoint": endpoint, "expirationTime": None,
        "keys": {"p256dh": encode_url64(private.public_key().public_bytes(serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint)), "auth": encode_url64(auth)}}}
    data.update(changes)
    return data, private, auth


@pytest.fixture
def clients(tmp_path):
    transport = Transport()
    app = create_app(tmp_path / "pulse.sqlite3", config=selected(), push_transport=transport)
    with TestClient(app, base_url="https://pulse.example.org") as web:
        other = TestClient(app, base_url="https://pulse.example.org")
        attach(web, app)
        attach(other, app, "fixture-second")
        yield web, other, app, transport
        other.close()
    assert transport.starts == transport.closes == 1


def stored(web, method, *args, **kwargs):
    return web.portal.call(partial(web.app.state.coordinator.call, method, *args, **kwargs))


def register(web, data=None):
    response = web.post("/api/push/subscriptions", json=data or payload()[0])
    assert response.status_code == 200
    return response.json()


def test_register_test_export_import_and_owner_revocation(clients):
    web, other, app, transport = clients
    data, recipient, auth = payload()
    capabilities = web.get("/api/push/capabilities")
    assert capabilities.status_code == 200 and capabilities.headers["cache-control"] == "no-store"
    assert capabilities.json()["enabled"] and capabilities.json()["privacy_mode"] == "generic"
    device = register(web, data)
    assert device["state"] == "active" and device["last_result"] == "never"
    assert 7.99 * 3600 < (datetime.fromisoformat(device["expires_at"]) - datetime.now(timezone.utc)).total_seconds() <= 8 * 3600
    response = web.post(f'/api/push/subscriptions/{device["id"]}/test', json={"confirmed": True})
    assert response.status_code == 200 and response.json()["status"] == "accepted"
    assert len(transport.calls) == 1
    assert json.loads(http_ece.decrypt(transport.calls[0][2], private_key=recipient, auth_secret=auth,
        version="aes128gcm")) == {"version": 1, "kind": "test"}
    current = web.get("/api/push/subscriptions").json()["items"][0]
    assert current["last_result"] == "accepted" and current["last_accepted_at"]
    assert other.get("/api/push/subscriptions").json()["items"] == []
    assert other.request("DELETE", f'/api/push/subscriptions/{device["id"]}', json={}).status_code == 404
    archive = web.get("/api/export").json()
    public_text = json.dumps([capabilities.json(), device, current, archive])
    for hidden in (data["subscription"]["endpoint"], data["subscription"]["keys"]["p256dh"],
                   data["subscription"]["keys"]["auth"], "ciphertext", "session_hash", "owner_id"):
        assert hidden not in public_text
    internal = stored(web, "get_push_subscription", "fixture-first", device["id"])
    assert data["subscription"]["endpoint"] not in internal["ciphertext"]
    assert web.post("/api/import", json={"confirmed": True, "data": archive}).status_code == 200
    assert web.get("/api/push/subscriptions").json()["items"][0]["id"] == device["id"]
    assert web.request("DELETE", f'/api/push/subscriptions/{device["id"]}', json={}).status_code == 200
    assert web.get("/api/push/subscriptions").json()["items"] == []


def test_csrf_body_confirmation_and_subscription_validation(clients):
    web, _, _, transport = clients
    data = payload()[0]
    assert web.post("/api/push/subscriptions", json=data, headers={"X-Pulse-CSRF": "invalid"}).status_code == 403
    assert web.post("/api/push/subscriptions", json=data, headers={"Origin": "https://evil.example"}).status_code == 403
    assert web.post("/api/push/subscriptions", content=b"x" * 4097, headers={"Content-Type": "application/json"}).status_code == 413
    bad = payload("https://127.0.0.1/private-sentinel")[0]
    response = web.post("/api/push/subscriptions", json=bad)
    assert response.status_code == 422 and "private-sentinel" not in response.text
    assert web.post("/api/push/subscriptions", json=data | {"owner_id": "fixture-second"}).status_code == 422
    data["subscription"]["expirationTime"] = (time.time() - 5) * 1000
    assert web.post("/api/push/subscriptions", json=data).status_code == 410
    device = register(web)
    assert web.post(f'/api/push/subscriptions/{device["id"]}/test', json={"confirmed": False}).status_code == 400
    assert transport.calls == []


def test_logout_and_new_session_need_explicit_reactivation(clients):
    web, _, app, transport = clients
    data = payload()[0]
    device = register(web, data)
    assert web.post("/api/auth/logout", json={}).status_code == 200
    assert web.get("/api/push/subscriptions").status_code == 401
    attach(web, app)
    inactive = web.get("/api/push/subscriptions").json()["items"][0]
    assert inactive["state"] == "session_required" and not inactive["session_active"]
    assert web.post(f'/api/push/subscriptions/{device["id"]}/test', json={"confirmed": True}).status_code == 409
    assert transport.calls == []
    assert register(web, data)["session_active"]
    assert web.post(f'/api/push/subscriptions/{device["id"]}/test', json={"confirmed": True}).status_code == 200


@pytest.mark.parametrize("status,result,response_code", [(201, "accepted", 200), (429, "failed", 503), (503, "failed", 503)])
def test_cooldown_and_service_outcome_do_not_claim_delivery(clients, status, result, response_code):
    web, _, _, transport = clients
    transport.status = status
    device = register(web)
    path = f'/api/push/subscriptions/{device["id"]}/test'
    assert web.post(path, json={"confirmed": True}).status_code == response_code
    assert web.post(path, json={"confirmed": True}).status_code == 429
    assert len(transport.calls) == 1
    row = web.get("/api/push/subscriptions").json()["items"][0]
    assert row["last_result"] == result
    assert bool(row["last_accepted_at"]) == (result == "accepted")


def test_network_uncertainty_is_persisted_without_automatic_retry(clients):
    web, _, _, transport = clients
    transport.error = TimeoutError("synthetic-private-provider-message")
    device = register(web)
    response = web.post(f'/api/push/subscriptions/{device["id"]}/test', json={"confirmed": True})
    assert response.status_code == 503 and "synthetic-private" not in response.text
    assert web.get("/api/push/subscriptions").json()["items"][0]["last_result"] == "uncertain"
    assert len(transport.calls) == 1


@pytest.mark.parametrize("status", [404, 410])
def test_expired_service_endpoint_revoked(clients, status):
    web, _, _, transport = clients
    transport.status = status
    device = register(web)
    assert web.post(f'/api/push/subscriptions/{device["id"]}/test', json={"confirmed": True}).status_code == 410
    assert web.get("/api/push/subscriptions").json()["items"] == []


def test_disabled_server_does_not_start_transport_or_register(tmp_path):
    transport = Transport()
    app = create_app(tmp_path / "pulse.sqlite3", config=selected(enabled=False), push_transport=transport)
    with TestClient(app, base_url="https://pulse.example.org") as web:
        attach(web, app)
        assert not web.get("/api/push/capabilities").json()["enabled"]
        assert web.post("/api/push/subscriptions", json=payload()[0]).status_code == 503
        assert web.get("/api/push/subscriptions").json()["items"] == []
    assert transport.starts == transport.closes == 0 and transport.calls == []


@pytest.mark.parametrize("invalidation", ["logout", "expiry", "revoke", "rebind"])
def test_final_send_gate_after_demo_read_blocks_invalidated_permission(clients, invalidation):
    web, _, app, transport = clients
    data = payload()[0]
    device = register(web, data)
    row = stored(web, "get_push_subscription", "fixture-first", device["id"])

    async def scenario():
        original = app.state.coordinator.call
        reached, release = asyncio.Event(), asyncio.Event()

        async def gated(method, *args, **kwargs):
            if method == "push_demo_enabled":
                reached.set()
                await release.wait()
            return await original(method, *args, **kwargs)

        app.state.coordinator.call = gated
        task = asyncio.create_task(app.state.push._send(row, "charging_observation"))
        try:
            await asyncio.wait_for(reached.wait(), 2)
            if invalidation in {"logout", "expiry"}:
                for sid, record in tuple(app.state.sessions.records.items()):
                    if record.user and record.user["id"] == "fixture-first":
                        if invalidation == "logout":
                            app.state.sessions.delete(sid)
                        else:
                            record.created -= app.state.config.session_ttl + 1
            elif invalidation == "revoke":
                await original("delete_push_subscription", "fixture-first", device["id"], write=True)
            else:
                await original("upsert_push_subscription", row["owner_id"], row["endpoint_hash"], row["session_hash"],
                    "Revinculado", row["ciphertext"], row["expires_at"], write=True)
            release.set()
            with pytest.raises(DomainError):
                await asyncio.wait_for(task, 2)
            assert transport.calls == []
        finally:
            release.set()
            app.state.coordinator.call = original
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    web.portal.call(scenario)


def test_old_service_failure_cannot_delete_a_rebound_device(clients):
    web, _, app, transport = clients
    device = register(web)
    row = stored(web, "get_push_subscription", "fixture-first", device["id"])

    async def scenario():
        reached, release = asyncio.Event(), asyncio.Event()
        async def delayed(*args):
            reached.set()
            await release.wait()
            return 410
        transport.send = delayed
        task = asyncio.create_task(app.state.push._send(row, "test"))
        await asyncio.wait_for(reached.wait(), 2)
        rebound = await app.state.coordinator.call("upsert_push_subscription", row["owner_id"], row["endpoint_hash"],
            row["session_hash"], "Renovado", row["ciphertext"], row["expires_at"], write=True)
        release.set()
        with pytest.raises(DomainError):
            await asyncio.wait_for(task, 2)
        current = await app.state.coordinator.call("get_push_subscription", row["owner_id"], row["id"])
        assert current["revision"] == rebound["revision"] == row["revision"] + 1
        assert current["name"] == "Renovado" and current["last_result"] == "uncertain"

    web.portal.call(scenario)


def test_restart_never_renews_an_existing_session_permission(tmp_path):
    config = selected()
    path = tmp_path / "pulse.sqlite3"
    first_transport = Transport()
    with TestClient(create_app(path, config=config, push_transport=first_transport), base_url=config.public_url) as web:
        attach(web, web.app)
        device = register(web)
    second_transport = Transport()
    with TestClient(create_app(path, config=config, push_transport=second_transport), base_url=config.public_url) as web:
        attach(web, web.app)
        row = web.get("/api/push/subscriptions").json()["items"][0]
        assert row["id"] == device["id"] and row["state"] == "session_required"
        assert web.post(f'/api/push/subscriptions/{device["id"]}/test', json={"confirmed": True}).status_code == 409
    assert first_transport.calls == second_transport.calls == []


def observation(at, charging):
    return {"source": "MATE-API B10ReadClient", "observed_at": at.isoformat(),
        "provider_received_at": at.isoformat(), "vehicle": {"battery_pct": 75, "range_km": 300,
        "odometer_km": 1000, "locked": True, "climate_on": False, "charging": charging},
        "field_sources": {"battery_pct": "precise_soc_percent", "range_km": "expected_range_km",
                         "odometer_km": "odometer_km", "locked": None,
                         "climate_on": None, "charging": None}}


def test_collector_persistence_is_the_only_automatic_push_trigger(clients):
    web, _, app, transport = clients
    data, recipient, auth = payload()
    register(web, data)

    async def scenario():
        now = datetime.now(timezone.utc)
        class Provider:
            current = observation(now - timedelta(seconds=2), False)
            def fetch_snapshot(self):
                return self.current
        provider = Provider()
        collector = Collector(provider, app.state.coordinator, interval_seconds=0.001, on_stored=app.state.push.observe)
        async def wait_for(predicate):
            async with asyncio.timeout(2):
                while not predicate():
                    await asyncio.sleep(0.001)
        try:
            # Escrituras de telemetría/import/revisiones ajenas no activan el hook.
            await app.state.coordinator.call("record_snapshot", observation(now - timedelta(seconds=3), True), write=True)
            await asyncio.sleep(0.01)
            assert transport.calls == [] and app.state.push._detector.previous is None
            assert (await collector.request_refresh())["status"] == "stored"
            await wait_for(lambda: app.state.push._detector.previous is not None)
            assert transport.calls == []
            provider.current = observation(now - timedelta(seconds=1), True)
            assert (await collector.request_refresh())["status"] == "stored"
            await wait_for(lambda: len(transport.calls) == 1)
            content = http_ece.decrypt(transport.calls[0][2], private_key=recipient, auth_secret=auth, version="aes128gcm")
            assert json.loads(content) == {"version": 1, "kind": "charging_observation"}
            assert collector.status()["success_count"] == 2
            await app.state.coordinator.call("set_demo", True, write=True)
            provider.current = observation(now, False)
            await collector.request_refresh()
            await wait_for(lambda: app.state.push._detector.previous is None)
            assert len(transport.calls) == 1
        finally:
            await collector.close()

    web.portal.call(scenario)


def test_observer_failure_does_not_erase_successful_collection(tmp_path):
    from pulse.coordinator import Coordinator
    async def scenario():
        coordinator = Coordinator(tmp_path / "pulse.sqlite3")
        await coordinator.start()
        class Provider:
            def fetch_snapshot(self):
                return observation(datetime.now(timezone.utc), True)
        def fail_observer(saved):
            assert saved["id"]
            raise RuntimeError("test-only-observer-failure")
        collector = Collector(Provider(), coordinator, on_stored=fail_observer)
        try:
            result = await collector.request_refresh()
            assert result["status"] == "stored"
            assert collector.status()["success_count"] == 1
            assert collector.status()["error_count"] == 0
            assert collector.status()["observer_error_count"] == 1
            assert (await coordinator.call("telemetry_latest"))["vehicle"]["charging"] is True
        finally:
            await collector.close()
            await coordinator.close()
    asyncio.run(scenario())
