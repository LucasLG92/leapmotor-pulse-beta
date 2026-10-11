"""Opt-in composition of the pinned SDK transport for B10 reads and sessions."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

from pulse.providers.base import ProviderUnavailable


class _B10Transport:
    __slots__ = ("_transport", "_request_type", "_hosts", "_routes", "_validate_url")

    def __init__(self, transport, request_type, hosts, routes, validate_url):
        self._transport, self._request_type = transport, request_type
        self._hosts, self._routes, self._validate_url = hosts, routes, validate_url

    def __repr__(self):
        return "PulseB10Transport(<explicit TLS configuration>)"

    def send(self, request, *, client_cert):
        # The SDK's create_default_context would otherwise honour SSLKEYLOGFILE.
        # Check presence only: never read, log or modify its value.
        if "SSLKEYLOGFILE" in os.environ:
            raise ProviderUnavailable(
                "transport_key_logging_enabled", "La configuración TLS requiere revisión."
            )
        try:
            if type(request) is not self._request_type:
                raise ValueError
            self._validate_url(request.url, self._hosts)
            parts = urlsplit(request.url)
            if (request.method, parts.hostname, parts.path) not in self._routes:
                raise ValueError
            if request.method == "POST" and "?" in request.url:
                raise ValueError
        except Exception:
            raise ProviderUnavailable(
                "transport_request_rejected", "Solicitud no admitida por el transporte B10."
            ) from None
        # The SDK enforces verified TLS, no redirects/proxies/cookies/retries and
        # bounded response bytes. No certificate is opened by this composition.
        return self._transport.send(request, client_cert=client_cert)


def build_b10_transport(*, ca_file: Path, center: str, region: str):
    """Prepare an explicit transport without files, login or network activity.

    Certificate files are consumed by the SDK only during an authorized send.
    Its 15-second timeout is a socket-operation timeout, not a total deadline.
    This factory never configures the running app or enables physical commands.
    """
    if (not isinstance(ca_file, Path) or not ca_file.is_absolute()
            or ".." in ca_file.parts or "\x00" in str(ca_file)):
        raise ValueError("Se requiere una ruta absoluta de CA explícita.")
    try:
        from leapmotor_cloud.authentication import LOGIN_PATH, REFRESH_PATH
        from leapmotor_cloud.b10_reads import CONFIG, HOSTS, SIGNALS
        from leapmotor_cloud.cloud import GLOBAL_ORIGIN
        from leapmotor_cloud.transport import Request, UrllibTransport, validate_url
    except ImportError:
        raise ValueError("El transporte B10 requiere el extra SDK fijado de Pulse.") from None
    try:
        center = validate_url(center, HOSTS, origin=True)
        region = validate_url(region, HOSTS, origin=True)
        login_host = urlsplit(GLOBAL_ORIGIN).hostname
        hosts = frozenset({urlsplit(center).hostname, urlsplit(region).hostname, login_host})
        transport = UrllibTransport(ca_file, hosts, timeout=15, max_response_bytes=1024 * 1024)
    except Exception:
        raise ValueError("Los orígenes B10 no están admitidos por el SDK fijado.") from None
    routes = frozenset({
        ("POST", login_host, LOGIN_PATH), ("POST", login_host, REFRESH_PATH),
        ("POST", urlsplit(region).hostname, SIGNALS),
        ("GET", urlsplit(center).hostname, CONFIG),
    })
    return _B10Transport(transport, Request, hosts, routes, validate_url)
