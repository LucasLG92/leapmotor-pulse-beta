"""B10 transport policy with the pinned SDK and wholly synthetic wire/TLS."""

import builtins
from datetime import datetime, timedelta, timezone
from pathlib import Path
import ssl
import urllib.error

import pytest

from pulse.providers import build_b10_transport
from pulse.providers.base import ProviderUnavailable


ORIGIN = "https://appgateway.leapmotor-international.de"
CERT = (Path("/synthetic/client.pem"), Path("/synthetic/client.key"))


def sdk():
    return pytest.importorskip("leapmotor_cloud.transport")


def transport():
    sdk()
    return build_b10_transport(ca_file=Path("/synthetic/ca.pem"), center=ORIGIN, region=ORIGIN)


def wire(monkeypatch, *, body=b"{}", status=200, error=None):
    module = sdk()
    calls = []

    class Context:
        def load_cert_chain(self, cert, key):
            calls.append(("certificate", cert, key))

    class Stream:
        def __init__(self, payload): self.payload = payload
        def __enter__(self): return self
        def __exit__(self, *_): calls.append(("closed",))
        def getcode(self): return status
        def read(self, limit):
            calls.append(("read", limit))
            return self.payload[:limit]

    class Opener:
        def open(self, request, *, timeout):
            calls.append(("open", request.full_url, timeout))
            if error is not None:
                raise error
            return Stream(body(request) if callable(body) else body)

    def context(ca):
        calls.append(("context", str(ca)))
        return Context()

    def opener(*handlers):
        calls.append(("handlers", handlers))
        return Opener()

    monkeypatch.delenv("SSLKEYLOGFILE", raising=False)
    monkeypatch.setattr(module, "tls_context", context)
    monkeypatch.setattr(module.urllib.request, "build_opener", opener)
    return calls


def test_base_import_and_missing_extra_have_no_sdk_side_effect(monkeypatch):
    original = builtins.__import__

    def without_sdk(name, *args, **kwargs):
        if name.startswith("leapmotor_cloud"):
            raise ImportError("synthetic import unavailable")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_sdk)
    with pytest.raises(ValueError, match="extra SDK fijado"):
        build_b10_transport(ca_file=Path("/synthetic/ca.pem"), center=ORIGIN, region=ORIGIN)


@pytest.mark.parametrize("ca", [None, "/synthetic/ca.pem", Path("relative.pem"),
                                Path("/synthetic/../ca.pem"), Path("/synthetic/ca\x00.pem")])
def test_invalid_ca_rejected_without_sdk_or_file_access(ca):
    with pytest.raises(ValueError, match="ruta absoluta"):
        build_b10_transport(ca_file=ca, center=ORIGIN, region=ORIGIN)


def test_construction_does_not_create_tls_or_read_files(monkeypatch):
    module = sdk()
    def forbidden(*_, **__): raise AssertionError("construction touched TLS or files")
    monkeypatch.setattr(module, "tls_context", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    value = transport()
    assert "synthetic" not in repr(value)
    assert "ca.pem" not in repr(value)


@pytest.mark.parametrize("origin", ["http://appgateway.leapmotor-international.de",
    "https://unapproved.invalid", ORIGIN+"/path", ORIGIN+"?secret=synthetic",
    "https://user:synthetic@appgateway.leapmotor-international.de", ORIGIN+":8443"])
def test_origin_is_not_a_caller_expandable_allowlist(origin):
    sdk()
    with pytest.raises(ValueError, match="orígenes B10") as caught:
        build_b10_transport(ca_file=Path("/synthetic/ca.pem"), center=origin, region=ORIGIN)
    assert "synthetic" not in str(caught.value)


@pytest.mark.parametrize("method,path", [
    ("POST", "/base/base-user/account/v1/login"),
    ("POST", "/base/base-user/token/v1/refresh"),
    ("POST", "/app/app-signal-service/signal/info/query"),
    ("GET", "/carownerservice/v3/api/vehicleinfo/commonConfig?vin=SYNTHETICB10VIN0001"),
])
def test_expected_sdk_routes_use_one_bounded_send(monkeypatch, method, path):
    calls = wire(monkeypatch)
    module = sdk()
    value = transport()
    from leapmotor_cloud.cloud import GLOBAL_ORIGIN
    origin = GLOBAL_ORIGIN if path.startswith("/base/") else ORIGIN
    response = value.send(module.Request(method, origin+path, {}), client_cert=CERT)
    assert response.status == 200 and response.body == b"{}"
    assert len([call for call in calls if call[0] == "open"]) == 1
    assert ("open", origin+path, 15) in calls
    assert ("read", 1024*1024+1) in calls and ("closed",) in calls
    handlers = next(call[1] for call in calls if call[0] == "handlers")
    proxy = next(h for h in handlers if isinstance(h, module.urllib.request.ProxyHandler))
    assert proxy.proxies == {}
    redirect = next(h for h in handlers if isinstance(h, module._NoRedirect))
    with pytest.raises(module.TransportError, match="redirect_refused"):
        redirect.redirect_request(None, None, 302, "synthetic", {}, "https://unapproved.invalid")


@pytest.mark.parametrize("method,path", [
    ("POST", "/app/app-car-service/vehicle/command"),
    ("GET", "/base/base-user/account/v1/login"),
    ("POST", "/app/app-signal-service/signal/info/query?unexpected=synthetic"),
    ("POST", "/app/app-signal-service/signal/info/%71uery"),
    ("POST", "/app/../app/app-signal-service/signal/info/query"),
])
def test_commands_unknown_routes_and_method_variants_fail_before_tls(monkeypatch, method, path):
    calls = wire(monkeypatch)
    with pytest.raises(ProviderUnavailable) as caught:
        transport().send(sdk().Request(method, ORIGIN+path, {}), client_cert=CERT)
    assert caught.value.code == "transport_request_rejected"
    assert calls == []
    assert "synthetic" not in str(caught.value)


def test_forbidden_destination_fails_before_tls(monkeypatch):
    calls = wire(monkeypatch)
    with pytest.raises(ProviderUnavailable):
        transport().send(sdk().Request("POST", "https://unapproved.invalid/base/base-user/account/v1/login", {}), client_cert=CERT)
    assert calls == []


def test_valid_route_on_wrong_admitted_host_is_still_rejected(monkeypatch):
    calls = wire(monkeypatch)
    with pytest.raises(ProviderUnavailable):
        transport().send(sdk().Request("POST", ORIGIN+"/base/base-user/account/v1/login", {}), client_cert=CERT)
    assert calls == []


def test_composition_serves_actual_b10_client_without_authentication_or_files(monkeypatch):
    from pulse.providers import build_b10_provider
    session_module = pytest.importorskip("leapmotor_cloud.session")
    now = datetime(2026, 10, 11, 6, 0, tzinfo=timezone.utc)
    vin = "SYNTHETICB10VIN0001"
    session = session_module.CloudSession(
        token="synthetic-access", user_id="synthetic-account", device_id="synthetic-device",
        key=b"s"*32, client_cert=CERT, expires_at=now+timedelta(hours=1),
    )
    def payload(request):
        if request.get_method() == "POST":
            return (b'{"code":0,"data":{"vin":"SYNTHETICB10VIN0001",'
                    b'"signalMap":{"100003":81,"1204":80,"1":"1791698400000"}}}')
        return (b'{"code":0,"data":{"vin":"SYNTHETICB10VIN0001",'
                b'"config":{"3":{"isEnable":"1","percent":80}}}}')
    calls = wire(monkeypatch, body=payload)
    acquisitions = []
    provider = build_b10_provider(
        transport=transport(), acquire_session=lambda: acquisitions.append(True) or session,
        vehicle_id=vin, center=ORIGIN, region=ORIGIN, clock=lambda: now,
    )
    assert calls == [] and acquisitions == []
    snapshot = provider.fetch_snapshot()
    assert snapshot["vehicle"]["battery_pct"] == 81
    assert acquisitions == [True]
    assert len([c for c in calls if c[0] == "open"]) == 2
    assert provider.command_status("unlock")["enabled"] is False


def test_key_logging_is_refused_at_send_even_if_enabled_after_construction(monkeypatch):
    calls = wire(monkeypatch)
    value = transport()
    monkeypatch.setenv("SSLKEYLOGFILE", "/synthetic/never-created-keylog")
    with pytest.raises(ProviderUnavailable) as caught:
        value.send(sdk().Request("POST", ORIGIN+"/base/base-user/account/v1/login", {}), client_cert=CERT)
    assert caught.value.code == "transport_key_logging_enabled"
    assert calls == [] and "synthetic" not in str(caught.value)


@pytest.mark.parametrize("header", ["Host", "Cookie", "Proxy-Authorization"])
def test_sensitive_ambient_headers_refused_before_tls(monkeypatch, header):
    calls = wire(monkeypatch)
    with pytest.raises(sdk().ValidationError):
        transport().send(sdk().Request("POST", ORIGIN+"/app/app-signal-service/signal/info/query", {header:"synthetic"}), client_cert=CERT)
    assert calls == []


@pytest.mark.parametrize("status,body,reason", [
    (302,b"{}","redirect_refused"), (200,b"x"*(1024*1024+1),"response_too_large"),
])
def test_response_limits_close_stream_without_retry(monkeypatch,status,body,reason):
    calls = wire(monkeypatch,status=status,body=body)
    with pytest.raises(sdk().TransportError,match=reason):
        transport().send(sdk().Request("POST",ORIGIN+"/app/app-signal-service/signal/info/query",{}),client_cert=CERT)
    assert len([c for c in calls if c[0]=="open"]) == 1
    assert ("closed",) in calls


@pytest.mark.parametrize("error,reason", [
    (TimeoutError("synthetic-sensitive-detail"),"timeout"),
    (ssl.SSLCertVerificationError("synthetic-sensitive-detail"),"certificate_rejected"),
])
def test_timeout_and_certificate_errors_are_sanitized_without_retry(monkeypatch,error,reason):
    calls = wire(monkeypatch,error=error)
    with pytest.raises(sdk().TransportError,match=reason) as caught:
        transport().send(sdk().Request("POST",ORIGIN+"/app/app-signal-service/signal/info/query",{}),client_cert=CERT)
    assert len([c for c in calls if c[0]=="open"]) == 1
    assert "synthetic-sensitive-detail" not in str(caught.value)
