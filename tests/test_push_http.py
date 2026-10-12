"""BFF de avisos con HTTP/SQLite reales y transporte sin red ni permisos."""

import asyncio
from datetime import datetime, timedelta, timezone
from functools import partial
import hashlib
import json
import os
import sqlite3
import threading
import time

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
import http_ece
import pytest

from pulse.app import create_app
from pulse.backup_alerts import BackupAlertsConfig
from pulse.config import Config
from pulse.collector import Collector
from pulse.domain import DomainError, Store
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


def selected(*, enabled=True, backup_alerts=None):
    private = ec.generate_private_key(ec.SECP256R1())
    push = PushConfig(enabled=True,
        vapid_private_key=encode_url64(private.private_numbers().private_value.to_bytes(32, "big")),
        storage_key=Fernet.generate_key().decode(), contact="mailto:operator@example.org") if enabled else PushConfig()
    return Config(auth_mode="oidc", public_url="https://pulse.example.org", oidc_issuer="https://id.example.org",
        oidc_client_id="pulse-web", oidc_client_secret="test-only-credential", allowed_users=("fixture",),
        hosts=("pulse.example.org",), push=push,
        backup_alerts=backup_alerts or BackupAlertsConfig())


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


def normalized_backup_reports(now, *, status="healthy", sequence=0, journal="c" * 32, events=()):
    result = []
    for profile in ("sqlite", "host"):
        checked = now.isoformat().replace("+00:00", "Z") if status != "unavailable" else None
        current_journal = journal if status != "unavailable" else None
        current_sequence = sequence if profile == "sqlite" and status != "unavailable" else 0
        current_events = list(events) if profile == "sqlite" and status != "unavailable" else []
        reason = "missing" if status == "unavailable" else None
        fingerprint = hashlib.sha256(f"{profile}:{status}:{current_sequence}:{reason}".encode()).hexdigest()
        result.append({"profile": profile, "status": status, "checked_at": checked,
            "journal_id": current_journal, "sequence": current_sequence, "events": current_events,
            "reason_code": reason, "fingerprint": fingerprint})
    return tuple(result)


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


def test_disabled_server_does_not_start_transport_or_register(tmp_path, monkeypatch):
    transport = Transport()
    monkeypatch.setattr("pulse.push_service.read_monitor_reports",
        lambda *args: (_ for _ in ()).throw(AssertionError("disabled reader must not run")))
    app = create_app(tmp_path / "pulse.sqlite3", config=selected(enabled=False), push_transport=transport)
    with TestClient(app, base_url="https://pulse.example.org") as web:
        attach(web, app)
        assert app.state.push._backup_task is None
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


def test_backup_alert_consent_is_confirmed_owner_scoped_and_opt_in(tmp_path, monkeypatch):
    transport = Transport()
    backup = BackupAlertsConfig(enabled=True, state_directory=str(tmp_path / "monitor"),
        owner_id="fixture-first", poll_seconds=30)
    monkeypatch.setattr("pulse.push_service.read_monitor_reports",
        lambda config, now: normalized_backup_reports(now))
    config = selected(backup_alerts=backup)
    app = create_app(tmp_path / "pulse.sqlite3", config=config, push_transport=transport)
    with TestClient(app, base_url=config.public_url) as web:
        other = TestClient(app, base_url=config.public_url)
        attach(web, app)
        attach(other, app, "fixture-second")
        assert web.get("/api/push/capabilities").json()["backup_alerts_available"] is True
        assert other.get("/api/push/capabilities").json()["backup_alerts_available"] is False
        device = register(web)
        assert device["backup_alerts_enabled"] is False
        path = f'/api/push/subscriptions/{device["id"]}/backup-alerts'
        assert web.post(path, json={"enabled": True, "confirmed": False}).status_code == 400

        other_device = register(other, payload("https://fcm.googleapis.com/wp/other-device")[0])
        foreign = other.post(f'/api/push/subscriptions/{other_device["id"]}/backup-alerts',
            json={"enabled": True, "confirmed": True})
        assert foreign.status_code == 503

        enabled = web.post(path, json={"enabled": True, "confirmed": True})
        assert enabled.status_code == 200 and enabled.json()["backup_alerts_enabled"] is True
        stored_row = stored(web, "get_push_subscription", "fixture-first", device["id"])
        assert stored_row["backup_alerts_enabled"] == 1 and stored_row["backup_alerts_consented_at"]
        disabled = web.post(path, json={"enabled": False, "confirmed": False})
        assert disabled.status_code == 200 and disabled.json()["backup_alerts_enabled"] is False
        stored_row = stored(web, "get_push_subscription", "fixture-first", device["id"])
        assert stored_row["backup_alerts_enabled"] == 0 and stored_row["backup_alerts_consented_at"] is None
        assert transport.calls == []
        other.close()


def test_start_activates_new_backup_scope_before_first_blocked_read(tmp_path, monkeypatch):
    transport = Transport()
    db_path = tmp_path / "pulse.sqlite3"
    endpoint = "https://fcm.googleapis.com/wp/startup-scope-race"
    previous_scope = hashlib.sha256(b"previous-backup-scope").hexdigest()
    store = Store(db_path)
    store.ensure_backup_alert_scope(previous_scope)
    old_row = store.upsert_push_subscription(
        "fixture-first", hashlib.sha256(endpoint.encode()).hexdigest(),
        hashlib.sha256(b"previous-session").hexdigest(), "Old device",
        "gAAAA-old-synthetic-ciphertext",
        (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
    )
    old_row = store.set_backup_alert_consent(
        "fixture-first", old_row["id"], True,
        datetime.now(timezone.utc).isoformat(), expected_revision=old_row["revision"],
    )
    assert old_row["backup_alerts_enabled"] == 1

    backup = BackupAlertsConfig(enabled=True, state_directory=str(tmp_path / "monitor"),
        owner_id="fixture-first", poll_seconds=30)
    read_started, allow_read = threading.Event(), threading.Event()

    def reader(config, now):
        read_started.set()
        if not allow_read.wait(3):
            raise TimeoutError("test-only-reader-gate")
        return normalized_backup_reports(now, status="unavailable")

    monkeypatch.setattr("pulse.push_service.read_monitor_reports", reader)
    config = selected(backup_alerts=backup)
    app = create_app(db_path, config=config, push_transport=transport)
    try:
        with TestClient(app, base_url=config.public_url) as web:
            attach(web, app)
            assert read_started.wait(2)
            old_after_start = stored(web, "get_push_subscription", "fixture-first", old_row["id"])
            assert old_after_start["backup_alerts_enabled"] == 0

            rebound = register(web, payload(endpoint)[0])
            assert rebound["id"] == old_row["id"] and rebound["backup_alerts_enabled"] is False
            consent_path = f'/api/push/subscriptions/{rebound["id"]}/backup-alerts'
            enabled = web.post(consent_path, json={"enabled": True, "confirmed": True})
            assert enabled.status_code == 200 and enabled.json()["backup_alerts_enabled"] is True

            allow_read.set()

            async def wait_for_send():
                async with asyncio.timeout(2):
                    while not transport.calls:
                        await asyncio.sleep(0.01)

            web.portal.call(wait_for_send)
            current = stored(web, "get_push_subscription", "fixture-first", rebound["id"])
            assert current["backup_alerts_enabled"] == 1
            assert current["backup_alerts_consented_at"]
            assert len(transport.calls) == 1
    finally:
        allow_read.set()


def test_restore_during_backup_encryption_marks_attempt_uncertain_without_replay(tmp_path, monkeypatch):
    transport = Transport()
    backup = BackupAlertsConfig(enabled=True, state_directory=str(tmp_path / "monitor"),
        owner_id="fixture-first", poll_seconds=30)
    monkeypatch.setattr("pulse.push_service.read_monitor_reports",
        lambda config, now: normalized_backup_reports(now))
    config = selected(backup_alerts=backup)
    app = create_app(tmp_path / "pulse.sqlite3", config=config, push_transport=transport)
    with TestClient(app, base_url=config.public_url) as web:
        attach(web, app)
        device = register(web)
        consent_path = f'/api/push/subscriptions/{device["id"]}/backup-alerts'
        assert web.post(consent_path, json={"enabled": True, "confirmed": True}).status_code == 200
        missing = normalized_backup_reports(datetime.now(timezone.utc), status="unavailable")
        web.portal.call(partial(app.state.coordinator.call, "observe_backup_reports", backup.scope,
            missing, write=True))
        event = web.portal.call(partial(app.state.coordinator.call,
            "list_backup_alert_candidates", backup.scope, "fixture-first"))[0]
        row = stored(web, "get_push_subscription", "fixture-first", device["id"])
        import_epoch = app.state.coordinator.import_epoch
        reached, release = threading.Event(), threading.Event()
        original = app.state.push.crypto.request

        def delayed(*args):
            reached.set()
            if not release.wait(3):
                raise TimeoutError("test-only-encryption-gate")
            return original(*args)

        app.state.push.crypto.request = delayed

        async def scenario():
            task = asyncio.create_task(app.state.push._send_backup_event(
                row, event, backup.scope, expected_import_epoch=import_epoch,
            ))
            assert await asyncio.to_thread(reached.wait, 2)
            archive = await app.state.coordinator.call("export_data", owner_id="fixture-first")
            await app.state.coordinator.restore(archive, owner_id="fixture-first")
            assert app.state.coordinator.import_epoch == import_epoch + 1
            release.set()
            with pytest.raises(DomainError):
                await asyncio.wait_for(task, 2)

        try:
            web.portal.call(scenario)
        finally:
            release.set()

        assert transport.calls == []
        current = stored(web, "get_push_subscription", "fixture-first", device["id"])
        assert current["backup_alerts_enabled"] == 1
        assert current["backup_alerts_consented_at"] == row["backup_alerts_consented_at"]
        assert current["last_result"] == "uncertain"
        with sqlite3.connect(app.state.coordinator.path) as db:
            assert db.execute("SELECT status FROM backup_alert_deliveries").fetchall() == [("uncertain",)]
        web.portal.call(partial(app.state.push._send_backup_event, current, event, backup.scope,
            expected_import_epoch=app.state.coordinator.import_epoch))
        assert transport.calls == []


def test_restore_before_backup_reservation_discards_stale_candidate_without_write(tmp_path, monkeypatch):
    transport = Transport()
    backup = BackupAlertsConfig(enabled=True, state_directory=str(tmp_path / "monitor"),
        owner_id="fixture-first", poll_seconds=30)
    monkeypatch.setattr("pulse.push_service.read_monitor_reports",
        lambda config, now: normalized_backup_reports(now))
    config = selected(backup_alerts=backup)
    app = create_app(tmp_path / "pulse.sqlite3", config=config, push_transport=transport)
    with TestClient(app, base_url=config.public_url) as web:
        attach(web, app)
        device = register(web)
        consent_path = f'/api/push/subscriptions/{device["id"]}/backup-alerts'
        assert web.post(consent_path, json={"enabled": True, "confirmed": True}).status_code == 200
        missing = normalized_backup_reports(datetime.now(timezone.utc), status="unavailable")
        web.portal.call(partial(app.state.coordinator.call, "observe_backup_reports", backup.scope,
            missing, write=True))
        event = web.portal.call(partial(app.state.coordinator.call,
            "list_backup_alert_candidates", backup.scope, "fixture-first"))[0]
        row = stored(web, "get_push_subscription", "fixture-first", device["id"])
        import_epoch = app.state.coordinator.import_epoch
        original_call = app.state.coordinator.call
        reservation_waiting, release_reservation = threading.Event(), asyncio.Event()

        async def gated_call(method, *args, **kwargs):
            if method == "reserve_backup_alert_delivery":
                reservation_waiting.set()
                await release_reservation.wait()
            return await original_call(method, *args, **kwargs)

        async def scenario():
            app.state.coordinator.call = gated_call
            task = asyncio.create_task(app.state.push._send_backup_event(
                row, event, backup.scope, expected_import_epoch=import_epoch,
            ))
            assert await asyncio.to_thread(reservation_waiting.wait, 2)
            archive = await original_call("export_data", owner_id="fixture-first")
            await app.state.coordinator.restore(archive, owner_id="fixture-first")
            release_reservation.set()
            await asyncio.wait_for(task, 2)

        try:
            web.portal.call(scenario)
        finally:
            release_reservation.set()
            app.state.coordinator.call = original_call

        current = stored(web, "get_push_subscription", "fixture-first", device["id"])
        assert current["backup_alerts_enabled"] == 1
        assert current["backup_alerts_consented_at"] == row["backup_alerts_consented_at"]
        assert current["last_result"] == "never"
        with sqlite3.connect(app.state.coordinator.path) as db:
            assert db.execute("SELECT COUNT(*) FROM backup_alert_deliveries").fetchone()[0] == 0
        assert transport.calls == []


def test_restore_after_backup_request_emission_finishes_uncertain_without_replay(tmp_path, monkeypatch):
    transport = Transport()
    backup = BackupAlertsConfig(enabled=True, state_directory=str(tmp_path / "monitor"),
        owner_id="fixture-first", poll_seconds=30)
    monkeypatch.setattr("pulse.push_service.read_monitor_reports",
        lambda config, now: normalized_backup_reports(now))
    config = selected(backup_alerts=backup)
    app = create_app(tmp_path / "pulse.sqlite3", config=config, push_transport=transport)
    with TestClient(app, base_url=config.public_url) as web:
        attach(web, app)
        device = register(web)
        consent_path = f'/api/push/subscriptions/{device["id"]}/backup-alerts'
        assert web.post(consent_path, json={"enabled": True, "confirmed": True}).status_code == 200
        missing = normalized_backup_reports(datetime.now(timezone.utc), status="unavailable")
        web.portal.call(partial(app.state.coordinator.call, "observe_backup_reports", backup.scope,
            missing, write=True))
        event = web.portal.call(partial(app.state.coordinator.call,
            "list_backup_alert_candidates", backup.scope, "fixture-first"))[0]
        row = stored(web, "get_push_subscription", "fixture-first", device["id"])
        import_epoch = app.state.coordinator.import_epoch
        request_emitted, release_response = asyncio.Event(), asyncio.Event()

        async def ambiguous_send(endpoint, headers, body):
            transport.calls.append((endpoint, headers, body))
            request_emitted.set()
            await release_response.wait()
            raise TimeoutError("test-only-ambiguous-request")

        transport.send = ambiguous_send

        async def scenario():
            task = asyncio.create_task(app.state.push._send_backup_event(
                row, event, backup.scope, expected_import_epoch=import_epoch,
            ))
            await asyncio.wait_for(request_emitted.wait(), 2)
            archive = await app.state.coordinator.call("export_data", owner_id="fixture-first")
            await app.state.coordinator.restore(archive, owner_id="fixture-first")
            release_response.set()
            with pytest.raises(DomainError):
                await asyncio.wait_for(task, 2)

        try:
            web.portal.call(scenario)
        finally:
            release_response.set()

        current = stored(web, "get_push_subscription", "fixture-first", device["id"])
        assert current["backup_alerts_enabled"] == 1
        assert current["backup_alerts_consented_at"] == row["backup_alerts_consented_at"]
        assert current["last_result"] == "uncertain"
        with sqlite3.connect(app.state.coordinator.path) as db:
            assert db.execute("SELECT status FROM backup_alert_deliveries").fetchall() == [("uncertain",)]
        web.portal.call(partial(app.state.push._send_backup_event, current, event, backup.scope,
            expected_import_epoch=app.state.coordinator.import_epoch))
        assert len(transport.calls) == 1


def test_backup_alert_sends_only_fixed_encrypted_payload_and_deduplicates(tmp_path, monkeypatch):
    transport = Transport()
    backup = BackupAlertsConfig(enabled=True, state_directory=str(tmp_path / "monitor"),
        owner_id="fixture-first", poll_seconds=30)
    monkeypatch.setattr("pulse.push_service.read_monitor_reports",
        lambda config, now: normalized_backup_reports(now))
    config = selected(backup_alerts=backup)
    app = create_app(tmp_path / "pulse.sqlite3", config=config, push_transport=transport)
    with TestClient(app, base_url=config.public_url) as web:
        attach(web, app)
        data, recipient, auth = payload()
        device = register(web, data)
        path = f'/api/push/subscriptions/{device["id"]}/backup-alerts'
        assert web.post(path, json={"enabled": True, "confirmed": True}).status_code == 200
        missing = normalized_backup_reports(datetime.now(timezone.utc), status="unavailable")
        web.portal.call(partial(app.state.coordinator.call, "observe_backup_reports", backup.scope,
            missing, write=True))
        event = web.portal.call(partial(app.state.coordinator.call,
            "list_backup_alert_candidates", backup.scope, "fixture-first"))[0]
        row = stored(web, "get_push_subscription", "fixture-first", device["id"])
        web.portal.call(partial(app.state.push._send_backup_event, row, event, backup.scope))
        assert len(transport.calls) == 1
        body = http_ece.decrypt(transport.calls[0][2], private_key=recipient, auth_secret=auth,
            version="aes128gcm")
        assert json.loads(body) == {"version": 1, "kind": "backup_alert"}
        assert transport.calls[0][1]["Topic"] == "pulse-backup-alert"
        assert b"monitor" not in body and b"fixture-first" not in body
        web.portal.call(partial(app.state.push._send_backup_event, row, event, backup.scope))
        assert len(transport.calls) == 1


def test_backup_alert_cannot_survive_consent_change_during_encryption(tmp_path, monkeypatch):
    transport = Transport()
    backup = BackupAlertsConfig(enabled=True, state_directory=str(tmp_path / "monitor"),
        owner_id="fixture-first", poll_seconds=30)
    monkeypatch.setattr("pulse.push_service.read_monitor_reports",
        lambda config, now: normalized_backup_reports(now))
    config = selected(backup_alerts=backup)
    app = create_app(tmp_path / "pulse.sqlite3", config=config, push_transport=transport)
    with TestClient(app, base_url=config.public_url) as web:
        attach(web, app)
        device = register(web)
        path = f'/api/push/subscriptions/{device["id"]}/backup-alerts'
        assert web.post(path, json={"enabled": True, "confirmed": True}).status_code == 200
        missing = normalized_backup_reports(datetime.now(timezone.utc), status="unavailable")
        web.portal.call(partial(app.state.coordinator.call, "observe_backup_reports", backup.scope,
            missing, write=True))
        event = web.portal.call(partial(app.state.coordinator.call,
            "list_backup_alert_candidates", backup.scope, "fixture-first"))[0]
        row = stored(web, "get_push_subscription", "fixture-first", device["id"])
        reached, release = threading.Event(), threading.Event()
        original = app.state.push.crypto.request

        def delayed(*args):
            reached.set()
            if not release.wait(3):
                raise TimeoutError("test-only-encryption-gate")
            return original(*args)

        app.state.push.crypto.request = delayed

        async def scenario():
            task = asyncio.create_task(app.state.push._send_backup_event(row, event, backup.scope))
            assert await asyncio.to_thread(reached.wait, 2)
            await app.state.push.set_backup_alerts("fixture-first", device["id"],
                enabled=False, confirmed=False)
            release.set()
            with pytest.raises(DomainError):
                await asyncio.wait_for(task, 2)

        try:
            web.portal.call(scenario)
        finally:
            release.set()
        assert transport.calls == []
        with sqlite3.connect(app.state.coordinator.path) as db:
            assert db.execute("SELECT status FROM backup_alert_deliveries").fetchone()[0] == "uncertain"


def test_stopping_backup_consumer_keeps_reserved_attempt_pending_without_send(tmp_path, monkeypatch):
    transport = Transport()
    backup = BackupAlertsConfig(enabled=True, state_directory=str(tmp_path / "monitor"),
        owner_id="fixture-first", poll_seconds=30)
    read_started, allow_read = threading.Event(), threading.Event()
    reports_box = {"value": normalized_backup_reports(datetime.now(timezone.utc))}

    def reader(config, now):
        read_started.set()
        if not allow_read.wait(3):
            raise TimeoutError("test-only-reader-gate")
        return reports_box["value"]

    monkeypatch.setattr("pulse.push_service.read_monitor_reports", reader)
    config = selected(backup_alerts=backup)
    app = create_app(tmp_path / "pulse.sqlite3", config=config, push_transport=transport)
    with TestClient(app, base_url=config.public_url) as web:
        attach(web, app)
        assert read_started.wait(2)
        device = register(web)
        path = f'/api/push/subscriptions/{device["id"]}/backup-alerts'
        assert web.post(path, json={"enabled": True, "confirmed": True}).status_code == 200
        reports_box["value"] = normalized_backup_reports(datetime.now(timezone.utc), status="unavailable")

        encrypted_started, finish_crypto = threading.Event(), threading.Event()
        original = app.state.push.crypto.request

        def delayed_crypto(*args):
            encrypted_started.set()
            if not finish_crypto.wait(3):
                raise TimeoutError("test-only-crypto-gate")
            return original(*args)

        app.state.push.crypto.request = delayed_crypto
        allow_read.set()
        assert encrypted_started.wait(2)
        background_task = app.state.push._backup_task

        async def stop_consumer():
            background_task.cancel()
            await asyncio.gather(background_task, return_exceptions=True)

        web.portal.call(stop_consumer)
        finish_crypto.set()
        assert transport.calls == []
        with sqlite3.connect(app.state.coordinator.path) as db:
            assert db.execute("SELECT status FROM backup_alert_deliveries").fetchall() == [("pending",)]


def test_new_session_and_rebind_do_not_inherit_backup_consent(tmp_path, monkeypatch):
    transport = Transport()
    backup = BackupAlertsConfig(enabled=True, state_directory=str(tmp_path / "monitor"),
        owner_id="fixture-first", poll_seconds=30)
    monkeypatch.setattr("pulse.push_service.read_monitor_reports",
        lambda config, now: normalized_backup_reports(now))
    config = selected(backup_alerts=backup)
    app = create_app(tmp_path / "pulse.sqlite3", config=config, push_transport=transport)
    with TestClient(app, base_url=config.public_url) as web:
        attach(web, app)
        data = payload()[0]
        device = register(web, data)
        path = f'/api/push/subscriptions/{device["id"]}/backup-alerts'
        assert web.post(path, json={"enabled": True, "confirmed": True}).status_code == 200
        assert web.post("/api/auth/logout", json={}).status_code == 200
        attach(web, app)
        assert web.post(path, json={"enabled": True, "confirmed": True}).status_code == 409
        rebound = register(web, data)
        assert rebound["session_active"] is True and rebound["backup_alerts_enabled"] is False
        assert transport.calls == []


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
