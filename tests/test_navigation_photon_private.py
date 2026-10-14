"""Private Photon adapter checks using only synthetic GeoJSON and transports."""

import asyncio
import ssl

import httpx2
import pytest

from pulse.contracts import NavigationSearch, NavigationStatus
from pulse.domain import DomainError
from pulse.navigation import Navigation, _photon_places
from pulse.navigation_config import NavigationConfig


def _private_config(**changes):
    values = {
        "enabled": True,
        "contact": "https://pulse.example.invalid/contact",
        "places_provider": "photon_private",
        "places_origin": "https://10.107.0.8:2322",
        "geocoder_ca_file": "/etc/pulse/geocoder/ca.pem",
        "geocoder_cert_file": "/etc/pulse/geocoder/client.pem",
        "geocoder_key_file": "/etc/pulse/geocoder/client-key.pem",
    }
    values.update(changes)
    return NavigationConfig(**values)


def _feature(osm_type="N", osm_id=101, *, coordinates=(-3.70379, 40.41678), properties=None):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": list(coordinates)},
        "properties": {"osm_type": osm_type, "osm_id": osm_id, "name": "Madrid", **(properties or {})},
    }


def test_private_config_requires_literal_private_https_origin_and_absolute_mtls_paths():
    for origin in (
        "https://photon.example.invalid:2322",
        "https://8.8.8.8:2322",
        "https://169.254.10.2:2322",
        "https://224.0.0.1:2322",
        "https://240.0.0.1:2322",
        "https://100.64.0.1:2322",
        "https://[fe80::1]:2322",
        "https://[ff02::1]:2322",
        "https://[::ffff:192.168.1.1]:2322",
        "https://user@10.0.0.1:2322",
        "https://10.0.0.1:2322/api",
        "https://10.0.0.1:2322?query=private",
        "https://10.0.0.1:2322?",
        "https://10.0.0.1:2322#",
        "http://10.0.0.1:2322",
    ):
        with pytest.raises(ValueError):
            _private_config(places_origin=origin)

    with pytest.raises(ValueError):
        _private_config(geocoder_key_file="relative/client-key.pem")
    for field_name in ("geocoder_ca_file", "geocoder_cert_file", "geocoder_key_file"):
        with pytest.raises(ValueError):
            _private_config(**{field_name: ""})


def test_private_config_accepts_loopback_and_ula_and_reads_env_selection(monkeypatch):
    for origin in ("https://127.0.0.1:2322", "https://[::1]:2322", "https://[fd12::8]:2322"):
        assert _private_config(places_origin=origin).places_origin == origin

    values = {
        "PULSE_NAVIGATION_ENABLED": "true",
        "PULSE_NAVIGATION_CONTACT": "https://pulse.example.invalid/contact",
        "PULSE_NAVIGATION_TILES_ORIGIN": "https://tile.openstreetmap.org",
        "PULSE_NAVIGATION_PLACES_PROVIDER": "photon_private",
        "PULSE_NAVIGATION_PLACES_ORIGIN": "https://10.107.0.8:2322",
        "PULSE_NAVIGATION_CHARGERS_ORIGIN": "https://overpass-api.de",
        "PULSE_NAVIGATION_GEOCODER_CA_FILE": "/etc/pulse/ca.pem",
        "PULSE_NAVIGATION_GEOCODER_CERT_FILE": "/etc/pulse/client.pem",
        "PULSE_NAVIGATION_GEOCODER_KEY_FILE": "/etc/pulse/client-key.pem",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    config = NavigationConfig.from_env()
    assert config.places_provider == "photon_private"
    assert config.places_origin == "https://10.107.0.8:2322"
    assert config.geocoder_ca_file == "/etc/pulse/ca.pem"


def test_photon_parser_uses_point_lon_lat_and_safe_address_fallback():
    payload = {
        "type": "FeatureCollection",
        "features": [
            _feature("N", 101),
            _feature("W", 202, properties={"name": None, "street": "Calle Mayor",
                "housenumber": "1", "postcode": "28013", "city": "Madrid"}),
            _feature("R", 303, coordinates=(181, 40)),
            _feature("N", 404, coordinates=(float("inf"), 40)),
            _feature("N", 405, coordinates=(True, 40)),
            _feature("N", 406, properties={"name": "<script>alert(1)</script>"}),
            _feature("N", 407, properties={"name": "https://attacker.invalid"}),
            _feature("N", 101, properties={"name": "Duplicate"}),
            _feature("R", 409, properties={"name": None, "street": "<script>bad</script>", "city": "Valencia"}),
            _feature("R", 410, properties={"name": None, "city": "C" * 81}),
            {"type": "Feature", "geometry": {"type": "LineString", "coordinates": []},
             "properties": {"osm_type": "N", "osm_id": 408, "name": "No point"}},
        ],
    }

    assert _photon_places(payload) == [
        {"id": "node/101", "name": "Madrid", "latitude": 40.41678, "longitude": -3.70379},
        {"id": "way/202", "name": "Calle Mayor, 1, 28013, Madrid",
         "latitude": 40.41678, "longitude": -3.70379},
        {"id": "relation/409", "name": "Valencia", "latitude": 40.41678, "longitude": -3.70379},
    ]


@pytest.mark.parametrize("payload", [None, [], {"type": "Feature"},
    {"type": "FeatureCollection", "features": {}}])
def test_private_photon_rejects_malformed_top_level_response_without_public_fallback(tmp_path, monkeypatch, payload):
    requests = []
    monkeypatch.setattr("pulse.navigation._build_photon_ssl_context", lambda _config: ssl.create_default_context())

    async def handler(request):
        requests.append(request)
        return httpx2.Response(200, json=payload)

    navigation = Navigation(_private_config(), tmp_path / "cache", transport=httpx2.MockTransport(handler))

    async def run():
        with pytest.raises(DomainError) as error:
            await navigation.search("Madrid", True)
        assert error.value.status == 502
        await navigation.close()

    asyncio.run(run())
    assert len(requests) == 1
    assert requests[0].url.host == "10.107.0.8"
    assert requests[0].url.path == "/api"


def test_private_photon_rejects_oversized_body_without_public_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr("pulse.navigation._build_photon_ssl_context", lambda _config: ssl.create_default_context())
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx2.Response(200, content=b'{"type":"FeatureCollection","features":[]}' + b" " * (256 * 1024),
            headers={"content-type": "application/json"})

    navigation = Navigation(_private_config(), tmp_path / "cache", transport=httpx2.MockTransport(handler))

    async def run():
        with pytest.raises(DomainError) as error:
            await navigation.search("Madrid", True)
        assert error.value.status == 502
        await navigation.close()

    asyncio.run(run())
    assert len(requests) == 1 and requests[0].url.host == "10.107.0.8"


def test_photon_request_uses_private_endpoint_caches_in_ram_and_never_calls_nominatim(tmp_path, monkeypatch):
    requests = []
    ssl_context = ssl.create_default_context()
    contexts = []

    def tls_factory(config):
        contexts.append(config)
        return ssl_context

    monkeypatch.setattr("pulse.navigation._build_photon_ssl_context", tls_factory)

    async def handler(request):
        requests.append(request)
        return httpx2.Response(200, json={"type": "FeatureCollection", "features": [_feature()]})

    navigation = Navigation(_private_config(), tmp_path / "cache", transport=httpx2.MockTransport(handler))

    async def run():
        status = NavigationStatus.model_validate(navigation.status()).model_dump()
        assert status["places_provider"] == "photon_private"
        assert status["private_place_search"] is True
        first = await navigation.search("  Madrid   centro ", True)
        second = await navigation.search("Madrid centro", True)
        await navigation.close()
        return first, second

    first, second = asyncio.run(run())
    assert len(requests) == 1
    assert requests[0].url.scheme == "https"
    assert requests[0].url.host == "10.107.0.8"
    assert requests[0].url.path == "/api"
    assert dict(requests[0].url.params) == {"q": "Madrid centro", "limit": "5", "lang": "es"}
    assert first["source"] == "OpenStreetMap Photon" and first["cached"] is False
    assert NavigationSearch.model_validate(first).source == "OpenStreetMap Photon"
    assert second["cached"] is True
    assert first["places"][0]["id"] == "node/101"
    assert contexts == [navigation.config]


def test_private_failure_and_cancellation_do_not_fallback_to_nominatim(tmp_path, monkeypatch):
    monkeypatch.setattr("pulse.navigation._build_photon_ssl_context", lambda _config: ssl.create_default_context())
    requests = []

    async def failure(request):
        requests.append(request)
        return httpx2.Response(503, text="synthetic internal error")

    navigation = Navigation(_private_config(), tmp_path / "failure", transport=httpx2.MockTransport(failure))

    async def run_failure():
        with pytest.raises(DomainError) as error:
            await navigation.search("Madrid", True)
        assert error.value.status == 502
        assert "synthetic internal error" not in str(error.value)
        await navigation.close()

    asyncio.run(run_failure())
    assert len(requests) == 1 and requests[0].url.host == "10.107.0.8"

    entered = asyncio.Event()
    cancelled_requests = []

    async def slow(request):
        cancelled_requests.append(request)
        entered.set()
        await asyncio.sleep(1)
        return httpx2.Response(200, json={"type": "FeatureCollection", "features": []})

    cancelled = Navigation(_private_config(), tmp_path / "cancel", transport=httpx2.MockTransport(slow))

    async def run_cancelled():
        task = asyncio.create_task(cancelled.search("Barcelona", True))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await cancelled.close()

    asyncio.run(run_cancelled())
    assert len(cancelled_requests) == 1 and cancelled_requests[0].url.host == "10.107.0.8"


def test_private_mtls_context_is_not_used_by_public_charger_client(tmp_path, monkeypatch):
    private_context = ssl.create_default_context()
    monkeypatch.setattr("pulse.navigation._build_photon_ssl_context", lambda _config: private_context)
    real_client = httpx2.AsyncClient
    client_kwargs = []

    def client_spy(*args, **kwargs):
        client_kwargs.append(kwargs)
        return real_client(*args, **kwargs)

    monkeypatch.setattr("pulse.navigation.httpx2.AsyncClient", client_spy)
    requests = []

    async def handler(request):
        requests.append(request)
        if request.url.path == "/api":
            return httpx2.Response(200, json={"type": "FeatureCollection", "features": [_feature()]})
        return httpx2.Response(200, json={"elements": []})

    navigation = Navigation(_private_config(), tmp_path / "cache", transport=httpx2.MockTransport(handler))

    async def run():
        await navigation.search("Madrid", True)
        await navigation.chargers(40.4, -3.7, True)
        await navigation.close()

    asyncio.run(run())
    assert client_kwargs[0]["verify"] is private_context
    assert client_kwargs[1]["verify"] is True
    assert all(kwargs["trust_env"] is False and kwargs["follow_redirects"] is False for kwargs in client_kwargs)
    assert [request.url.path for request in requests] == ["/api", "/api/interpreter"]

