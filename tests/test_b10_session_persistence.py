"""Synthetic end-to-end session persistence, recovery and shutdown regressions."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from threading import Event

import pytest

# This sandbox maps its public filesystem roots to overflow UID65534. Model the
# root-owned Linux deployment roots; final-directory/file checks remain real.
from test_session_vault import normalize_user_namespace_root_owners

from pulse.providers.base import ProviderUnavailable
from pulse.providers.b10_bootstrap import ManagedB10Provider, _SessionReadClient, build_b10_provider
from pulse.providers.session_codec import decode_session, encode_session
from pulse.providers.session_vault import EncryptedSessionVault, VaultUnavailable


NOW = datetime(2026, 10, 9, 14, tzinfo=timezone.utc)
CONTEXT = "c" * 64
VIN = "SYNTHETICV41B10"
KEY = b"v" * 32  # Fictitious fixture key, never operational material.
CERT = (Path("/synthetic/cert.pem"), Path("/synthetic/key.pem"))


@dataclass(frozen=True)
class Session:
    token: str = "synthetic-access"
    user_id: str = "synthetic-account"
    device_id: str = "synthetic-installation"
    key: bytes = b"s" * 32
    client_cert: tuple = CERT
    expires_at: datetime = NOW + timedelta(hours=1)
    refresh_token: str | None = "synthetic-refresh"
    refresh_expires_at: datetime | None = NOW + timedelta(days=1)

    def renewable(self, now):
        return bool(self.refresh_token) and (self.refresh_expires_at is None or now < self.refresh_expires_at)

    def ensure_valid(self, now):
        if now >= self.expires_at:
            raise ValueError("Synthetic expiry")


class Clock:
    value = NOW

    def __call__(self):
        return self.value


class Store:
    current = None

    def replace(self, session):
        self.current = session


class Reader:
    def __init__(self, store):
        self.store, self.calls = store, []

    def read(self, vin):
        self.calls.append(vin)
        return self.store.current.token


def client(directory, *, acquire=lambda: Session(), renew=None, clock=None, context=CONTEXT):
    vault = EncryptedSessionVault(directory, KEY, "synthetic.account.installation")
    store = Store()
    reader = Reader(store)
    managed = _SessionReadClient(
        reader, store, acquire_session=acquire, renew_session=renew,
        session_type=Session, clock=clock or Clock(),
        session_vault=vault, session_context=context,
    )
    return managed, vault, store, reader


def test_restart_recovers_session_without_another_acquisition(tmp_path):
    acquired = []
    first, _, _, _ = client(tmp_path / "vault", acquire=lambda: acquired.append(True) or Session())
    assert first.read(VIN) == "synthetic-access"
    first.close()

    second, _, _, _ = client(tmp_path / "vault", acquire=lambda: acquired.append(True) or Session())
    try:
        assert second.read(VIN) == "synthetic-access"
        assert acquired == [True]
        assert b"synthetic-access" not in (tmp_path / "vault/session.bin").read_bytes()
    finally:
        second.close()


def test_renewed_session_is_the_one_recovered_after_restart(tmp_path):
    clock = Clock()
    renewed = Session(token="synthetic-renewed", expires_at=NOW + timedelta(hours=2))
    renewals = []
    first, _, _, _ = client(tmp_path / "vault", clock=clock,
                            renew=lambda old: renewals.append(old.token) or renewed)
    assert first.read(VIN) == "synthetic-access"
    clock.value = NOW + timedelta(minutes=59)
    assert first.read(VIN) == "synthetic-renewed"
    first.close()

    def forbidden_login():
        raise AssertionError("Restoration must not perform login")

    second, _, _, _ = client(tmp_path / "vault", clock=clock, acquire=forbidden_login)
    try:
        assert second.read(VIN) == "synthetic-renewed"
        assert renewals == ["synthetic-access"]
    finally:
        second.close()


def test_corrupt_persistence_never_triggers_login_fallback(tmp_path):
    first, _, _, _ = client(tmp_path / "vault")
    first.read(VIN)
    first.close()
    blob = tmp_path / "vault/session.bin"
    data = bytearray(blob.read_bytes())
    data[-1] ^= 1
    blob.write_bytes(data)
    acquired = []
    second, _, store, reader = client(tmp_path / "vault", acquire=lambda: acquired.append(True) or Session())
    try:
        with pytest.raises(ProviderUnavailable) as failure:
            second.read(VIN)
        assert failure.value.code == "session_storage_failed"
        assert acquired == [] and store.current is None and reader.calls == []
    finally:
        second.close()


def test_different_vehicle_context_is_rejected_before_login_or_read(tmp_path):
    first, _, _, _ = client(tmp_path / "vault")
    first.read(VIN)
    first.close()
    acquired = []
    second, _, _, reader = client(tmp_path / "vault", context="d" * 64,
                                  acquire=lambda: acquired.append(True) or Session())
    try:
        with pytest.raises(ProviderUnavailable) as failure:
            second.read(VIN)
        assert failure.value.code == "session_storage_failed"
        assert acquired == [] and reader.calls == []
    finally:
        second.close()


def test_failed_initial_persistence_does_not_activate_session_or_repeat_login(tmp_path, monkeypatch):
    acquired = []
    managed, vault, store, reader = client(tmp_path / "vault", acquire=lambda: acquired.append(True) or Session())

    save = vault.save

    def write_failure(data):
        if json.loads(data)["state"] == "ready":
            raise OSError("synthetic-private-token-and-path")
        save(data)

    monkeypatch.setattr(vault, "save", write_failure)
    try:
        with pytest.raises(ProviderUnavailable) as failure:
            managed.read(VIN)
        with pytest.raises(ProviderUnavailable):
            managed.read(VIN)
        assert failure.value.code == "session_storage_failed"
        assert "synthetic-private-token-and-path" not in str(failure.value)
        assert acquired == [True] and store.current is None and reader.calls == []
    finally:
        managed.close()
    second, _, _, _ = client(tmp_path / "vault", acquire=lambda: acquired.append(True) or Session())
    try:
        with pytest.raises(ProviderUnavailable):
            second.read(VIN)
        assert acquired == [True]
    finally:
        second.close()


def test_failed_renewal_persistence_keeps_previous_store_and_blocks_reads(tmp_path, monkeypatch):
    clock, renewals = Clock(), []
    renewed = Session(token="synthetic-renewed", expires_at=NOW + timedelta(hours=2))
    managed, vault, store, reader = client(tmp_path / "vault", clock=clock,
        renew=lambda old: renewals.append(old.token) or renewed)
    managed.read(VIN)
    previous = (tmp_path / "vault/session.bin").read_bytes()
    clock.value = NOW + timedelta(minutes=59)
    save = vault.save

    def failed_ready(data):
        if json.loads(data)["state"] == "ready":
            raise OSError("synthetic-failed-write")
        save(data)

    monkeypatch.setattr(vault, "save", failed_ready)
    try:
        with pytest.raises(ProviderUnavailable) as failure:
            managed.read(VIN)
        with pytest.raises(ProviderUnavailable):
            managed.read(VIN)
        assert failure.value.code == "session_storage_failed"
        assert store.current.token == "synthetic-access" and reader.calls == [VIN]
        assert renewals == ["synthetic-access"]
        assert (tmp_path / "vault/session.bin").read_bytes() != previous
        with pytest.raises(ValueError):
            decode_session(vault.load(), CONTEXT, Session)
    finally:
        managed.close()
    second, _, _, _ = client(tmp_path / "vault")
    try:
        with pytest.raises(ProviderUnavailable) as restored:
            second.read(VIN)
        assert restored.value.code == "session_storage_failed"
    finally:
        second.close()


def test_failed_authentication_stays_pending_across_restart(tmp_path):
    acquired = []

    def failed_login():
        acquired.append(True)
        raise RuntimeError("Synthetic login outcome unknown")

    for _ in range(2):
        managed, _, _, reader = client(tmp_path / "vault", acquire=failed_login)
        try:
            with pytest.raises(ProviderUnavailable):
                managed.read(VIN)
            assert reader.calls == []
        finally:
            managed.close()
    assert acquired == [True]


def test_failed_refresh_stays_pending_across_restart_without_resending(tmp_path):
    attempts = []

    def failed_refresh(_old):
        attempts.append(True)
        raise RuntimeError("Synthetic refresh outcome unknown")

    for _ in range(2):
        managed, _, _, reader = client(tmp_path / "vault", acquire=lambda: Session(expires_at=NOW),
                                      renew=failed_refresh)
        try:
            with pytest.raises(ProviderUnavailable) as failure:
                managed.read(VIN)
            with pytest.raises(ProviderUnavailable):
                managed.read(VIN)
            assert failure.value.code in {"session_refresh_uncertain", "session_storage_failed"}
            assert reader.calls == []
        finally:
            managed.close()
    assert attempts == [True]


def test_failed_pending_checkpoint_does_not_start_authentication(tmp_path, monkeypatch):
    acquired = []
    managed, vault, _, reader = client(tmp_path / "vault", acquire=lambda: acquired.append(True) or Session())
    monkeypatch.setattr(vault, "save", lambda _data: (_ for _ in ()).throw(OSError("synthetic-marker-failure")))
    try:
        with pytest.raises(ProviderUnavailable) as failure:
            managed.read(VIN)
        assert failure.value.code == "session_storage_failed"
        assert acquired == [] and reader.calls == []
    finally:
        managed.close()


def test_close_during_load_does_not_start_acquisition(tmp_path, monkeypatch):
    entered, release, acquired = Event(), Event(), []
    managed, vault, _, reader = client(tmp_path / "vault", acquire=lambda: acquired.append(True) or Session())
    load = vault.load

    def paused_load():
        payload = load()
        entered.set()
        assert release.wait(3)
        return payload

    monkeypatch.setattr(vault, "load", paused_load)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(managed.read, VIN)
        assert entered.wait(3)
        try:
            managed.close()
        finally:
            release.set()
        with pytest.raises(ProviderUnavailable) as failure:
            future.result(timeout=3)
        assert failure.value.code == "session_closed"
    assert acquired == [] and reader.calls == []


def test_close_inside_acquisition_keeps_lease_until_callback_result_persisted(tmp_path):
    other = EncryptedSessionVault(tmp_path / "vault", KEY, "synthetic.account.installation")
    renewals = []

    def closing_login():
        managed.close()  # Reentrant close in the same worker thread.
        with pytest.raises(VaultUnavailable):
            other.load()
        return Session(expires_at=NOW)

    managed, _, _, reader = client(tmp_path / "vault", acquire=closing_login,
                                   renew=lambda old: renewals.append(old) or Session())
    try:
        with pytest.raises(ProviderUnavailable) as failure:
            managed.read(VIN)
        assert failure.value.code == "session_closed"
        assert renewals == [] and reader.calls == []
        assert decode_session(other.load(), CONTEXT, Session).token == "synthetic-access"
    finally:
        managed.close()
        other.close()


def test_close_during_renewal_persists_rotated_session_then_releases_lease(tmp_path):
    entered, release = Event(), Event()
    renewed = Session(token="synthetic-renewed", expires_at=NOW + timedelta(hours=2))

    def renew(_old):
        entered.set()
        assert release.wait(3)
        return renewed

    managed, _, _, reader = client(tmp_path / "vault", acquire=lambda: Session(expires_at=NOW), renew=renew)
    other = EncryptedSessionVault(tmp_path / "vault", KEY, "synthetic.account.installation")
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(managed.read, VIN)
        assert entered.wait(3)
        try:
            managed.close()  # Returns while the refresh callback is still active.
            with pytest.raises(VaultUnavailable):
                other.load()
        finally:
            release.set()
        with pytest.raises(ProviderUnavailable) as failure:
            future.result(timeout=3)
        assert failure.value.code == "session_closed"
    try:
        assert decode_session(other.load(), CONTEXT, Session).token == "synthetic-renewed"
        assert reader.calls == []
        with pytest.raises(ProviderUnavailable):
            managed.read(VIN)
    finally:
        other.close()


def test_app_shutdown_closes_the_managed_provider_and_releases_lease(tmp_path):
    from pulse.app import create_app
    from pulse.config import Config

    managed, _, _, _ = client(tmp_path / "vault")
    managed.read(VIN)
    provider = ManagedB10Provider(managed, VIN)
    app = create_app(db_path=tmp_path / "pulse.sqlite3", config=Config(), provider=provider)

    async def no_background_collection():
        pass

    app.state.collector.start = no_background_collection

    async def lifespan():
        async with app.router.lifespan_context(app):
            pass

    asyncio.run(lifespan())
    other = EncryptedSessionVault(tmp_path / "vault", KEY, "synthetic.account.installation")
    try:
        assert decode_session(other.load(), CONTEXT, Session).token == "synthetic-access"
        with pytest.raises(ProviderUnavailable):
            managed.read(VIN)
    finally:
        other.close()


@pytest.mark.parametrize("field,value", [
    ("version", True), ("version", 2), ("context", "other-context"),
    ("key", "not-base64"), ("expires_at", "2026-10-09T14:00:00"),
    ("client_cert", ["relative.pem", "/synthetic/key.pem"]),
    ("client_cert", ["/synthetic/../cert.pem", "/synthetic/key.pem"]),
])
def test_codec_rejects_invalid_version_scope_dates_keys_and_paths(field, value):
    data = json.loads(encode_session(Session(), CONTEXT))
    data[field] = value
    with pytest.raises(ValueError):
        decode_session(json.dumps(data).encode(), CONTEXT, Session)


def test_codec_rejects_duplicate_unknown_fields_and_oversized_payloads():
    data = encode_session(Session(), CONTEXT)
    duplicate = data[:-1] + b',"version":1}'
    unknown = data[:-1] + b',"unexpected":true}'
    for payload in (duplicate, unknown, b" " * (128 * 1024 + 1)):
        with pytest.raises(ValueError):
            decode_session(payload, CONTEXT, Session)


def test_factory_real_sdk_recovers_without_login_and_prevents_shared_vault(tmp_path):
    sdk_session = pytest.importorskip("leapmotor_cloud.session")
    sdk_transport = pytest.importorskip("leapmotor_cloud.transport")
    session = sdk_session.CloudSession(**Session().__dict__)
    acquisitions = []

    class SyntheticTransport:
        def send(self, request, *, client_cert):
            data = ({"vin": VIN, "signalMap": {"100003": 81}} if request.method == "POST" else
                    {"vin": VIN, "config": {"3": {"isEnable": "1", "percent": 80}}})
            return sdk_transport.Response(200, json.dumps({"code": 0, "data": data}).encode())

    def provider(vault):
        return build_b10_provider(
            transport=SyntheticTransport(), acquire_session=lambda: acquisitions.append(True) or session,
            vehicle_id=VIN, center="https://appgateway.leapmotor-international.de",
            region="https://appgateway.leapmotor-international.de", clock=Clock(), session_vault=vault,
        )

    vault = EncryptedSessionVault(tmp_path / "vault", KEY, "synthetic.account.installation")
    first = provider(vault)
    assert not (tmp_path / "vault").exists() and acquisitions == []
    with pytest.raises(ValueError):
        provider(vault)
    assert first.fetch_snapshot()["vehicle"]["battery_pct"] == 81
    first.close()
    second = provider(EncryptedSessionVault(tmp_path / "vault", KEY, "synthetic.account.installation"))
    try:
        assert second.fetch_snapshot()["vehicle"]["battery_pct"] == 81
        assert acquisitions == [True]
        assert second.command_status("unlock")["enabled"] is False
    finally:
        second.close()
