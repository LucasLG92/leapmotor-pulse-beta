"""Behavioral regressions for the opt-in, synthetic B10 session bootstrap."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import pytest

from pulse.providers.b10_bootstrap import _SessionReadClient, build_b10_provider
from pulse.providers.base import ProviderUnavailable


NOW = datetime(2026, 10, 9, 10, 0, tzinfo=timezone.utc)
VIN = "SYNTHETICB10VIN0001"
APP_CERT = (Path("/synthetic/app-cert.pem"), Path("/synthetic/app-key.pem"))


class FakeClock:
    def __init__(self, value=NOW):
        self.value = value

    def __call__(self):
        return self.value


class FakeMonotonic:
    def __init__(self, value=0.0):
        self.value = value

    def __call__(self):
        return self.value


class FakeSession:
    """Minimal explicit session contract with no certificate-file access."""

    def __init__(self, *, token, expires_at, refresh_token="synthetic-refresh",
                 user_id="synthetic-account", device_id="synthetic-device",
                 client_cert=APP_CERT):
        self.token = token
        self.expires_at = expires_at
        self.refresh_token = refresh_token
        self.user_id = user_id
        self.device_id = device_id
        self.client_cert = client_cert

    def renewable(self, now):
        return self.refresh_token is not None

    def ensure_valid(self, now):
        if self.expires_at is not None and now >= self.expires_at:
            raise RuntimeError("synthetic session expired")


class FakeSessionStore:
    def __init__(self):
        self.sessions = []

    def replace(self, session):
        self.sessions.append(session)


class FakeReader:
    def __init__(self, result="synthetic-read", error=None):
        self.result = result
        self.error = error
        self.calls = []

    def read(self, vin):
        self.calls.append(vin)
        if self.error:
            raise self.error
        return self.result


def managed_client(*, session=None, acquire=None, renew=None, reader=None,
                   clock=None, mono=None, refresh_before=120, retry_after=600):
    session = session or FakeSession(token="synthetic-access", expires_at=NOW + timedelta(hours=1))
    store = FakeSessionStore()
    reader = reader or FakeReader()
    acquired = []

    def acquire_once():
        acquired.append(True)
        if acquire:
            return acquire()
        return session

    client = _SessionReadClient(
        reader, store,
        acquire_session=acquire_once,
        renew_session=renew,
        session_type=FakeSession,
        clock=clock or FakeClock(),
        monotonic_clock=mono or FakeMonotonic(),
        refresh_before_seconds=refresh_before,
        retry_after_seconds=retry_after,
    )
    return client, reader, store, acquired


def test_session_read_client_construction_and_provider_status_do_not_authenticate_or_read():
    sdk_session = pytest.importorskip("leapmotor_cloud.session")

    class NoNetworkTransport:
        def __init__(self):
            self.calls = []

        def send(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            raise AssertionError("synthetic transport must not be reached during setup/status")

    transport = NoNetworkTransport()
    acquired = []
    provider = build_b10_provider(
        transport=transport,
        acquire_session=lambda: acquired.append(True),
        vehicle_id=VIN,
        center="https://appgateway.leapmotor-international.de",
        region="https://appgateway.leapmotor-international.de",
        clock=FakeClock(),
    )

    assert provider.connection_status()["status"] == "not_connected"
    assert acquired == []
    assert transport.calls == []
    assert sdk_session.SessionStore  # Factory imported the pinned SDK contract.


def test_acquisition_happens_once_and_failure_does_not_relogin_on_next_read():
    client, reader, store, acquired = managed_client(acquire=lambda: (_ for _ in ()).throw(
        RuntimeError("synthetic-password-and-token")
    ))

    with pytest.raises(ProviderUnavailable) as first:
        client.read(VIN)
    with pytest.raises(ProviderUnavailable) as second:
        client.read(VIN)

    assert first.value.code == "authentication_failed"
    assert second.value.code == "authentication_required"
    assert len(acquired) == 1
    assert reader.calls == []
    assert store.sessions == []
    assert "synthetic-password-and-token" not in str(first.value)


@pytest.mark.parametrize("expiry", [None, datetime(2026, 10, 9, 11, 0)])
def test_acquisition_rejects_unknown_or_naive_expiry_before_store_or_read(expiry):
    invalid = FakeSession(token="synthetic-invalid-expiry", expires_at=expiry)
    client, reader, store, acquired = managed_client(acquire=lambda: invalid)

    with pytest.raises(ProviderUnavailable) as first:
        client.read(VIN)
    with pytest.raises(ProviderUnavailable) as second:
        client.read(VIN)

    assert first.value.code == "authentication_failed"
    assert second.value.code == "authentication_required"
    assert len(acquired) == 1
    assert store.sessions == []
    assert reader.calls == []


def test_expiring_session_renews_proactively_and_preserves_account_device_and_certificate():
    old = FakeSession(token="synthetic-old", expires_at=NOW + timedelta(seconds=40))
    renewed = FakeSession(
        token="synthetic-new", expires_at=NOW + timedelta(hours=2),
        refresh_token="synthetic-next-refresh", user_id=old.user_id,
        device_id=old.device_id, client_cert=old.client_cert,
    )
    clock = FakeClock()
    renewal_calls = []
    client, reader, store, acquired = managed_client(
        session=old,
        renew=lambda current: renewal_calls.append(current) or renewed,
        clock=clock,
        refresh_before=60,
    )

    assert client.read(VIN) == "synthetic-read"

    assert renewal_calls == [old]
    assert store.sessions == [old, renewed]
    assert reader.calls == [VIN]
    assert acquired == [True]


def test_expired_but_renewable_session_is_refreshed_before_any_read():
    old = FakeSession(token="synthetic-expired-old", expires_at=NOW - timedelta(seconds=1))
    renewed = FakeSession(
        token="synthetic-current", expires_at=NOW + timedelta(hours=1),
        user_id=old.user_id, device_id=old.device_id, client_cert=old.client_cert,
    )
    calls = []
    client, reader, store, _acquired = managed_client(
        session=old, renew=lambda current: calls.append(current) or renewed,
        refresh_before=60,
    )

    assert client.read(VIN) == "synthetic-read"

    assert calls == [old]
    assert store.sessions == [old, renewed]
    assert reader.calls == [VIN]


def test_refresh_failure_uses_monotonic_cooldown_and_never_falls_back_to_login():
    old = FakeSession(token="synthetic-old", expires_at=NOW + timedelta(minutes=5))
    clock, mono = FakeClock(), FakeMonotonic()
    renewals = []
    acquisitions = []

    def acquire():
        acquisitions.append(True)
        return old

    def renew(_session):
        renewals.append(True)
        raise RuntimeError("synthetic-private-refresh-material")

    client, reader, _store, _ = managed_client(
        session=old, acquire=acquire, renew=renew, clock=clock, mono=mono,
        refresh_before=600, retry_after=60,
    )
    with pytest.raises(ProviderUnavailable) as first:
        client.read(VIN)
    with pytest.raises(ProviderUnavailable) as during_cooldown:
        client.read(VIN)

    assert first.value.code == "session_refresh_failed"
    assert during_cooldown.value.code == "session_refresh_cooldown"
    assert len(renewals) == 1
    assert len(acquisitions) == 1
    assert reader.calls == []
    assert "synthetic-private-refresh-material" not in str(first.value)

    mono.value += 61
    with pytest.raises(ProviderUnavailable) as after_cooldown:
        client.read(VIN)
    assert after_cooldown.value.code == "session_refresh_failed"
    assert len(renewals) == 2
    assert len(acquisitions) == 1


def test_wall_clock_rollback_cannot_bypass_failed_refresh_monotonic_cooldown():
    old = FakeSession(token="synthetic-old", expires_at=NOW + timedelta(minutes=5))
    clock, mono = FakeClock(), FakeMonotonic()
    renewals = []
    client, reader, _store, acquired = managed_client(
        session=old,
        renew=lambda _session: renewals.append(True) or (_ for _ in ()).throw(
            RuntimeError("synthetic-refresh-failure")
        ),
        clock=clock, mono=mono, refresh_before=600, retry_after=60,
    )

    with pytest.raises(ProviderUnavailable) as first:
        client.read(VIN)
    assert first.value.code == "session_refresh_failed"

    clock.value -= timedelta(hours=1)  # Wall clock now places the session outside the refresh window.
    mono.value = 30  # Monotonic time remains inside the failed-refresh cooldown.
    with pytest.raises(ProviderUnavailable) as during_cooldown:
        client.read(VIN)

    assert during_cooldown.value.code == "session_refresh_cooldown"
    assert len(renewals) == 1
    assert len(acquired) == 1
    assert reader.calls == []


def test_refresh_recovers_after_monotonic_cooldown_with_valid_continuous_session():
    old = FakeSession(token="synthetic-old", expires_at=NOW + timedelta(minutes=5))
    renewed = FakeSession(
        token="synthetic-recovered", expires_at=NOW + timedelta(hours=1),
        user_id=old.user_id, device_id=old.device_id, client_cert=old.client_cert,
    )
    mono = FakeMonotonic()
    calls = []

    def renew(session):
        calls.append(session)
        if len(calls) == 1:
            raise RuntimeError("synthetic-transient-refresh-failure")
        return renewed

    client, reader, store, acquired = managed_client(
        session=old, renew=renew, mono=mono, refresh_before=600, retry_after=60,
    )
    with pytest.raises(ProviderUnavailable) as first:
        client.read(VIN)
    assert first.value.code == "session_refresh_failed"
    assert reader.calls == []

    mono.value = 60
    assert client.read(VIN) == "synthetic-read"

    assert calls == [old, old]
    assert store.sessions == [old, renewed]
    assert reader.calls == [VIN]
    assert acquired == [True]


def test_short_renewed_token_does_not_trigger_a_refresh_storm_inside_cooldown():
    old = FakeSession(token="synthetic-old", expires_at=NOW + timedelta(seconds=30))
    short = FakeSession(
        token="synthetic-short", expires_at=NOW + timedelta(seconds=45),
        user_id=old.user_id, device_id=old.device_id, client_cert=old.client_cert,
    )
    calls = []
    client, reader, _store, _acquired = managed_client(
        session=old, renew=lambda current: calls.append(current) or short,
        refresh_before=60,
    )

    assert client.read(VIN) == "synthetic-read"
    assert client.read(VIN) == "synthetic-read"

    assert calls == [old]
    assert reader.calls == [VIN, VIN]


def test_expired_session_without_refresh_is_rejected_without_acquire_fallback():
    old = FakeSession(token="synthetic-expired", expires_at=NOW - timedelta(seconds=1), refresh_token=None)
    client, reader, store, acquired = managed_client(session=old)

    with pytest.raises(ProviderUnavailable) as error:
        client.read(VIN)

    assert error.value.code == "authentication_required"
    assert len(acquired) == 1
    assert store.sessions == [old]
    assert reader.calls == []


def test_session_without_usable_refresh_remains_readable_until_expiry_then_stops():
    sdk_session = pytest.importorskip("leapmotor_cloud.session")
    clock = FakeClock()
    expired_refresh = sdk_session.CloudSession(
        token="synthetic-account-token", user_id="synthetic-account",
        device_id="synthetic-device", key=b"s" * 32, client_cert=APP_CERT,
        expires_at=NOW + timedelta(seconds=30), refresh_token="synthetic-old-refresh",
        refresh_expires_at=NOW - timedelta(seconds=1),
    )
    reader, store, acquisitions, renewals = FakeReader(), FakeSessionStore(), [], []

    def acquire():
        acquisitions.append(True)
        return expired_refresh

    def forbidden_refresh(session):
        renewals.append(session)
        raise AssertionError("expired refresh token must not be sent")

    client = _SessionReadClient(
        reader, store, acquire_session=acquire, renew_session=forbidden_refresh,
        session_type=sdk_session.CloudSession, clock=clock,
        monotonic_clock=FakeMonotonic(), refresh_before_seconds=60,
        retry_after_seconds=600,
    )

    assert client.read(VIN) == "synthetic-read"
    clock.value += timedelta(seconds=30)
    with pytest.raises(ProviderUnavailable) as expired:
        client.read(VIN)

    assert expired.value.code == "authentication_required"
    assert renewals == []
    assert acquisitions == [True]
    assert store.sessions == [expired_refresh]
    assert reader.calls == [VIN]


def test_session_without_refresh_callback_is_readable_until_expiry_then_stops():
    session = FakeSession(token="synthetic-no-refresh-callback", expires_at=NOW + timedelta(seconds=30))
    clock = FakeClock()
    client, reader, store, acquired = managed_client(
        session=session, renew=None, clock=clock, refresh_before=60,
    )

    assert client.read(VIN) == "synthetic-read"
    clock.value += timedelta(seconds=30)
    with pytest.raises(ProviderUnavailable) as expired:
        client.read(VIN)

    assert expired.value.code == "authentication_required"
    assert acquired == [True]
    assert store.sessions == [session]
    assert reader.calls == [VIN]


def test_concurrent_reads_share_one_proactive_renewal():
    old = FakeSession(token="synthetic-old", expires_at=NOW + timedelta(seconds=30))
    renewed = FakeSession(
        token="synthetic-new", expires_at=NOW + timedelta(hours=1),
        user_id=old.user_id, device_id=old.device_id, client_cert=old.client_cert,
    )
    start = Barrier(3)
    calls = []
    client, reader, store, _acquired = managed_client(
        session=old,
        renew=lambda current: calls.append(current) or renewed,
        refresh_before=60,
    )

    def concurrent_read():
        start.wait(timeout=3)
        return client.read(VIN)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(concurrent_read)
        second = pool.submit(concurrent_read)
        start.wait(timeout=3)
        assert first.result(timeout=3) == "synthetic-read"
        assert second.result(timeout=3) == "synthetic-read"

    assert calls == [old]
    assert store.sessions == [old, renewed]
    assert reader.calls == [VIN, VIN]


def test_rejected_read_is_not_retried_or_converted_to_a_new_login():
    reader = FakeReader(error=RuntimeError("synthetic-cloud-rejection"))
    client, _reader, _store, acquired = managed_client(reader=reader)

    with pytest.raises(RuntimeError, match="synthetic-cloud-rejection"):
        client.read(VIN)

    assert reader.calls == [VIN]
    assert acquired == [True]


@pytest.mark.parametrize("changed_field", ["user_id", "device_id", "client_cert"])
def test_invalid_renewed_session_blocks_future_reads_and_redacts_material(changed_field):
    old = FakeSession(token="synthetic-old", expires_at=NOW + timedelta(seconds=30))
    fields = {"user_id": old.user_id, "device_id": old.device_id, "client_cert": old.client_cert}
    fields[changed_field] = {
        "user_id": "different-account",
        "device_id": "different-device",
        "client_cert": (Path("/synthetic/other-cert.pem"), Path("/synthetic/other-key.pem")),
    }[changed_field]
    changed_identity = FakeSession(token="synthetic-secret-token", expires_at=NOW + timedelta(hours=1),
                                   **fields)
    client, reader, _store, _acquired = managed_client(
        session=old, renew=lambda _session: changed_identity, refresh_before=60,
    )

    with pytest.raises(ProviderUnavailable) as first:
        client.read(VIN)
    with pytest.raises(ProviderUnavailable) as second:
        client.read(VIN)

    assert first.value.code == "session_refresh_invalid"
    assert second.value.code == "authentication_required"
    assert reader.calls == []
    assert "synthetic-secret-token" not in repr(client)
    assert "synthetic-secret-token" not in str(first.value)


def test_factory_uses_actual_sdk_read_client_with_synthetic_transport_responses():
    sdk_reads = pytest.importorskip("leapmotor_cloud.b10_reads")
    sdk_session = pytest.importorskip("leapmotor_cloud.session")
    sdk_transport = pytest.importorskip("leapmotor_cloud.transport")

    session = sdk_session.CloudSession(
        token="synthetic-access-token", user_id="synthetic-account",
        device_id="synthetic-device", key=b"s" * 32, client_cert=APP_CERT,
        expires_at=NOW + timedelta(hours=1), refresh_token="synthetic-refresh",
        refresh_expires_at=NOW + timedelta(days=1),
    )

    class SyntheticTransport:
        def __init__(self):
            self.requests = []

        def send(self, request, *, client_cert):
            self.requests.append((request, client_cert))
            if request.method == "POST":
                body = (b'{"code":0,"data":{"vin":"' + VIN.encode()
                        + b'","signalMap":{"100003":81,"1204":80,"1":"1791539940000"}}}')
            else:
                body = (b'{"code":0,"data":{"vin":"' + VIN.encode()
                        + b'","config":{"3":{"isEnable":"1","percent":80}}}}')
            return sdk_transport.Response(200, body)

    transport = SyntheticTransport()
    acquisitions = []
    provider = build_b10_provider(
        transport=transport,
        acquire_session=lambda: acquisitions.append(True) or session,
        vehicle_id=VIN,
        center="https://appgateway.leapmotor-international.de",
        region="https://appgateway.leapmotor-international.de",
        clock=FakeClock(),
    )
    assert provider.connection_status()["status"] == "not_connected"
    assert acquisitions == []
    assert transport.requests == []

    snapshot = provider.fetch_snapshot()

    assert isinstance(provider._read_client._reader, sdk_reads.B10ReadClient)
    assert snapshot["vehicle"]["battery_pct"] == 81
    assert len(acquisitions) == 1
    assert len(transport.requests) == 2
    assert all(cert == APP_CERT for _request, cert in transport.requests)
    assert all(request.url.startswith("https://appgateway.leapmotor-international.de/")
               for request, _cert in transport.requests)
