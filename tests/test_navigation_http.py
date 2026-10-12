"""Authenticated navigation contract with original synthetic public data only."""

import base64
import struct
import zlib

import httpx2
import pytest
from fastapi.testclient import TestClient

from pulse.app import create_app
from pulse.config import Config
from pulse.navigation_config import NavigationConfig


def png():
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    rows = (b"\0" + b"\x80\xa0\x90\xff" * 256) * 256
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 256, 256, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def application(tmp_path, *, enabled=True):
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.path == "/search":
            return httpx2.Response(200, json=[{"osm_type": "node", "osm_id": 1,
                "name": "Plaza de prueba", "lat": "40.41678", "lon": "-3.70379"}])
        if request.url.path == "/api/interpreter":
            return httpx2.Response(200, json={"elements": [{"type": "node", "id": 2,
                "lat": 40.417, "lon": -3.704, "tags": {"amenity": "charging_station",
                "name": "Cargador de prueba", "socket:type2": "2", "maxpower": "22 kW"}}]})
        return httpx2.Response(200, content=png(), headers={"Content-Type": "image/png", "Cache-Control": "max-age=604800"})
    config = Config(hosts=("testserver",), navigation=NavigationConfig(
        enabled=enabled, contact="https://pulse.example.invalid/contact" if enabled else ""))
    # The HTTP contract must work in a clean checkout before the web build.
    static = tmp_path / "static"
    static.mkdir(exist_ok=True)
    (static / "index.html").write_text("<!doctype html><title>Pulse navigation fixture</title>")
    return create_app(tmp_path / "pulse.sqlite3", config=config, static_dir=static,
                      navigation_transport=httpx2.MockTransport(handler)), calls


def auth(client):
    return {"X-Pulse-CSRF": client.get("/api/session").json()["csrf_token"]}


def test_opening_navigation_and_state_never_contacts_providers(tmp_path):
    app, calls = application(tmp_path)
    with TestClient(app) as client:
        # Local mode explicitly trusts only loopback; a proxy cannot inherit it.
        assert client.get("/api/navigation/status", headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 403
        auth(client)
        response = client.get("/api/navigation/status")
        assert response.status_code == 200
        assert response.json() == {"enabled": True, "reason": "ready", "source": "OpenStreetMap",
                                   "attribution": "© OpenStreetMap contributors"}
        assert client.get("/api/state").status_code == 200
        assert not calls and not (tmp_path / "map-cache").exists()


def test_navigation_can_open_directly_without_provider_io(tmp_path):
    app, calls = application(tmp_path)
    with TestClient(app) as client:
        response = client.get("/navigation")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert client.head("/navigation").status_code == 200
        assert client.get("/navigation/unexpected").status_code == 404
        assert not calls and not (tmp_path / "map-cache").exists()


def test_oidc_anonymous_navigation_is_closed_before_provider_io(tmp_path):
    # Explicit synthetic configuration; no discovery or login is invoked.
    config = Config(auth_mode="oidc", public_url="https://pulse.example.invalid",
        oidc_issuer="https://identity.example.invalid", oidc_client_id="test",
        oidc_client_secret="synthetic-secret", allowed_users=("lucas",), hosts=("pulse.example.invalid",),
        navigation=NavigationConfig(enabled=True, contact="https://pulse.example.invalid/contact"))
    def forbidden(_request):
        raise AssertionError("Anonymous requests must never reach the provider")
    app = create_app(tmp_path / "anonymous.sqlite3", config=config, navigation_transport=httpx2.MockTransport(forbidden))
    with TestClient(app, base_url=config.public_url) as client:
        assert client.get("/api/navigation/status").status_code == 401
        assert client.post("/api/navigation/places", json={"query": "Madrid", "consent": True}).status_code == 401
        assert client.get("/api/session").json()["authenticated"] is False
        assert client.get("/api/navigation/status").status_code == 401
        assert not (tmp_path / "map-cache").exists()


def test_disabled_and_no_consent_never_network_or_create_map_cache(tmp_path):
    app, calls = application(tmp_path, enabled=False)
    with TestClient(app) as client:
        headers = auth(client)
        assert client.get("/api/navigation/status").json()["reason"] == "disabled"
        for path, body in [("places", {"query": "Madrid"}), ("chargers", {"latitude": 40, "longitude": -3}),
                           ("tiles", {"z": 1, "x": 0, "y": 0})]:
            response = client.post("/api/navigation/" + path, json={**body, "consent": True}, headers=headers)
            assert response.status_code == 503
        assert not calls and not (tmp_path / "map-cache").exists()
    app, calls = application(tmp_path)
    with TestClient(app) as client:
        headers = auth(client)
        for path, body in [("places", {"query": "Madrid"}), ("chargers", {"latitude": 40, "longitude": -3}),
                           ("tiles", {"z": 1, "x": 0, "y": 0})]:
            assert client.post("/api/navigation/" + path, json={**body, "consent": False}, headers=headers).status_code == 400
        assert not calls and not (tmp_path / "map-cache").exists()


@pytest.mark.parametrize("path,body", [
    ("places", {"query": "Madrid"}),
    ("places", {"query": "Ma", "consent": True}),
    ("places", {"query": "Madrid", "consent": "true"}),
    ("places", {"query": "Madrid", "consent": True, "url": "https://attacker.invalid/"}),
    ("chargers", {"latitude": True, "longitude": -3, "consent": True}),
    ("chargers", {"latitude": 91, "longitude": -3, "consent": True}),
    ("chargers", {"latitude": "40", "longitude": -3, "consent": True}),
    ("tiles", {"z": True, "x": 0, "y": 0, "consent": True}),
    ("tiles", {"z": 19, "x": 0, "y": 0, "consent": True}),
    ("tiles", {"z": 0, "x": 1, "y": 0, "consent": True}),
])
def test_invalid_navigation_payloads_do_not_escape_or_make_requests(tmp_path, path, body):
    app, calls = application(tmp_path)
    with TestClient(app) as client:
        response = client.post("/api/navigation/" + path, json=body, headers=auth(client))
        assert response.status_code in {400, 422}
        assert "attacker" not in response.text
        assert response.headers["cache-control"] == "no-store"
        assert not calls


def test_csrf_origin_size_and_private_response_boundaries(tmp_path):
    app, calls = application(tmp_path)
    with TestClient(app) as client:
        headers = auth(client)
        body = {"query": "Madrid", "consent": True}
        assert client.post("/api/navigation/places", json=body).status_code == 403
        assert client.post("/api/navigation/places", json=body, headers={**headers, "Origin": "https://attacker.invalid"}).status_code == 403
        assert client.post("/api/navigation/places", content=b" " * 4097, headers={**headers, "Content-Type": "application/json"}).status_code == 413
        assert not calls
        response = client.post("/api/navigation/places", json=body, headers={**headers, "Accept-Encoding": "gzip"})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store" and "content-encoding" not in response.headers
        assert response.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"
        assert "connect-src 'self'" in response.headers["content-security-policy"]
        assert response.json()["places"][0]["name"] == "Plaza de prueba"
        repeated = client.post("/api/navigation/places", json=body, headers=headers)
        assert repeated.json()["cached"] is True and len(calls) == 1
        document = client.get("/api/export").json()
        assert "Madrid" not in str(document) and "Plaza de prueba" not in str(document)


def test_chargers_return_static_public_data_without_location_history(tmp_path):
    app, calls = application(tmp_path)
    with TestClient(app) as client:
        headers = auth(client)
        response = client.post("/api/navigation/chargers", json={"latitude": 40.41678, "longitude": -3.70379, "consent": True}, headers=headers)
        assert response.status_code == 200, response.text
        station = response.json()["stations"][0]
        assert station["name"] == "Cargador de prueba" and station["power_kw"] == 22
        assert "available" not in station and "route" not in station
        assert station["distance_m"] < 100
        assert len(calls) == 1 and not (tmp_path / "map-cache").exists()


@pytest.mark.parametrize("referer,expected", [
    ("http://testserver/navigation?private=hidden", "http://testserver"),
    ("https://attacker.invalid/navigation", None),
    ("http://user:password@testserver/navigation", None),
    ("http://[invalid/", None),
])
def test_tiles_forward_only_same_origin_referer_and_cache_privately(tmp_path, referer, expected):
    app, calls = application(tmp_path)
    with TestClient(app) as client:
        headers = {**auth(client), "Referer": referer}
        response = client.post("/api/navigation/tiles", json={"z": 1, "x": 0, "y": 0, "consent": True}, headers=headers)
        assert response.status_code == 200, response.text
        assert base64.b64decode(response.json()["data_url"].split(",", 1)[1]) == png()
        assert calls[0].url.path == "/1/0/0.png"
        assert calls[0].headers.get("referer") == expected
        assert "private" not in str(calls[0].headers)
        files = list((tmp_path / "map-cache" / "navigation-tiles").glob("*.tile"))
        assert len(files) == 1 and files[0].stat().st_mode & 0o777 == 0o600
        repeated = client.post("/api/navigation/tiles", json={"z": 1, "x": 0, "y": 0, "consent": True}, headers=headers)
        assert repeated.json()["cached"] is True and len(calls) == 1
