"""Synthetic, consent-gated navigation service tests; no real public services."""

import asyncio
import base64
import json
import struct
import zlib
from urllib.parse import parse_qs

import httpx2
import pytest

from pulse.domain import DomainError
from pulse.navigation import Navigation, _png_256
from pulse.navigation_config import NavigationConfig


def _enabled_config(**changes):
    return NavigationConfig(enabled=True, contact="https://example.invalid/contact", **changes)


def _chunk(kind, data):
    payload = kind + data
    return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)


def _png(width=256, height=256):
    rows = b"".join(b"\0" + b"\0" * width * 4 for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(rows)) + _chunk(b"IEND", b""))


def _place(osm_id, *, name="Madrid", lat="40.41678", lon="-3.70379", osm_type="node"):
    return {"osm_type": osm_type, "osm_id": osm_id, "name": name, "lat": lat, "lon": lon}


def test_navigation_config_defaults_closed_and_rejects_unsafe_destinations():
    config = NavigationConfig()
    assert config.enabled is False
    assert config.contact == ""
    assert config.tiles_origin == "https://tile.openstreetmap.org"

    with pytest.raises(ValueError):
        NavigationConfig(enabled=True)
    with pytest.raises(ValueError):
        NavigationConfig(tiles_origin="http://tiles.example.invalid")
    with pytest.raises(ValueError):
        NavigationConfig(places_origin="https://user:pass@example.invalid")
    with pytest.raises(ValueError):
        NavigationConfig(chargers_origin="https://example.invalid/extra")
    with pytest.raises(ValueError):
        NavigationConfig(contact="https://example.invalid/path?q=private")
    with pytest.raises(ValueError):
        NavigationConfig(contact="not-an-email")


def test_status_and_disabled_consent_paths_do_not_create_files_or_network(tmp_path):
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx2.Response(200, json=[])

    path = tmp_path / "map-cache"
    navigation = Navigation(NavigationConfig(), path, transport=httpx2.MockTransport(handler))

    async def run():
        assert navigation.status() == {
            "enabled": False,
            "source": "OpenStreetMap",
            "reason": "disabled",
            "attribution": "© OpenStreetMap contributors",
        }
        with pytest.raises(DomainError) as disabled:
            await navigation.search("Madrid", True)
        assert disabled.value.status == 503
        await navigation.close()

    asyncio.run(run())
    assert requests == []
    assert not path.exists()


def test_consent_false_rejects_before_client_or_cache_creation(tmp_path):
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx2.Response(200, json=[])

    path = tmp_path / "map-cache"
    navigation = Navigation(_enabled_config(), path, transport=httpx2.MockTransport(handler))

    async def run():
        with pytest.raises(DomainError) as error:
            await navigation.search("Madrid", False)
        assert error.value.status == 400
        with pytest.raises(DomainError) as bool_error:
            await navigation.chargers(40.0, 0.0, False)
        assert bool_error.value.status == 400
        await navigation.close()

    asyncio.run(run())
    assert requests == []
    assert not path.exists()


def test_search_uses_fixed_nominatim_request_validates_rows_and_caches_in_ram(tmp_path):
    requests = []
    payload = [
        _place(101, name="Madrid centro"),
        _place(102, name="<script>alert(1)</script>"),
        _place(103, name="https://example.invalid"),
        _place(104, name="Madrid norte", lat="91"),
        *[_place(200 + index, name=f"Lugar {index}") for index in range(6)],
    ]

    async def handler(request):
        requests.append(request)
        return httpx2.Response(200, json=payload)

    navigation = Navigation(_enabled_config(), tmp_path / "map-cache", transport=httpx2.MockTransport(handler))

    async def run():
        first = await navigation.search("  Madrid   centro ", True)
        second = await navigation.search("Madrid centro", True)
        await navigation.close()
        return first, second

    first, second = asyncio.run(run())
    assert len(requests) == 1
    assert requests[0].url.scheme == "https"
    assert requests[0].url.host == "nominatim.openstreetmap.org"
    assert requests[0].url.path == "/search"
    assert dict(requests[0].url.params) == {
        "format": "jsonv2", "q": "Madrid centro", "limit": "5", "addressdetails": "0"
    }
    assert requests[0].headers["accept-language"] == "es"
    assert "Pulse" in requests[0].headers["user-agent"]
    assert "example.invalid/contact" in requests[0].headers["user-agent"]
    assert first["source"] == "OpenStreetMap Nominatim"
    assert first["cached"] is False
    assert second["cached"] is True
    assert first["fetched_at"].endswith("Z")
    assert len(first["places"]) == 5
    assert first["places"][0] == {
        "id": "node/101", "name": "Madrid centro", "latitude": 40.41678, "longitude": -3.70379
    }
    assert all("<" not in place["name"] and "http" not in place["name"] for place in first["places"])


def test_search_rate_limit_is_immediate_and_does_not_queue(tmp_path):
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx2.Response(200, json=[_place(len(requests), name="Lugar")])

    navigation = Navigation(_enabled_config(), tmp_path / "map-cache", transport=httpx2.MockTransport(handler))

    async def run():
        await navigation.search("Madrid", True)
        with pytest.raises(DomainError) as error:
            await navigation.search("Barcelona", True)
        assert error.value.status == 429
        await navigation.close()

    asyncio.run(run())
    assert len(requests) == 1


def test_search_redacts_upstream_errors_and_rejects_oversized_body(tmp_path):
    async def failing(_request):
        return httpx2.Response(503, text="synthetic-private-response")

    first = Navigation(_enabled_config(), tmp_path / "first", transport=httpx2.MockTransport(failing))

    async def run_failure():
        with pytest.raises(DomainError) as error:
            await first.search("Madrid", True)
        assert error.value.status == 502
        assert "synthetic-private-response" not in str(error.value)
        await first.close()

    asyncio.run(run_failure())

    async def oversized(_request):
        return httpx2.Response(200, content=b"{" + b" " * (256 * 1024), headers={"content-type": "application/json"})

    second = Navigation(_enabled_config(), tmp_path / "second", transport=httpx2.MockTransport(oversized))

    async def run_oversized():
        with pytest.raises(DomainError) as error:
            await second.search("Madrid", True)
        assert error.value.status == 502
        await second.close()

    asyncio.run(run_oversized())


def test_search_timeout_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr("pulse.navigation.PLACES_TIMEOUT_SECONDS", 0.01)

    async def slow(_request):
        await asyncio.sleep(0.05)
        return httpx2.Response(200, json=[])

    navigation = Navigation(_enabled_config(), tmp_path / "map-cache", transport=httpx2.MockTransport(slow))

    async def run():
        with pytest.raises(DomainError) as error:
            await navigation.search("Madrid", True)
        assert error.value.status == 504
        await navigation.close()

    asyncio.run(run())


def test_search_discards_unhashable_types_and_overflowing_coordinates(tmp_path):
    payload = [
        {"osm_type": [], "osm_id": 1, "name": "Fila inválida", "lat": "40", "lon": "-3"},
        {"osm_type": {"node": True}, "osm_id": 2, "name": "Fila inválida", "lat": "40", "lon": "-3"},
        _place(3, name="Coordenada enorme", lat=10**400),
        _place(4, name="Madrid válido"),
    ]

    async def handler(_request):
        return httpx2.Response(200, json=payload)

    navigation = Navigation(_enabled_config(), tmp_path / "map-cache", transport=httpx2.MockTransport(handler))

    async def run():
        result = await navigation.search("Madrid", True)
        await navigation.close()
        return result

    result = asyncio.run(run())
    assert result["places"] == [{
        "id": "node/4", "name": "Madrid válido", "latitude": 40.41678, "longitude": -3.70379
    }]


def test_chargers_posts_fixed_overpass_query_and_normalizes_public_fields(tmp_path):
    requests = []
    payload = {
        "elements": [
            {"type": "node", "id": 41, "lat": 40.41678, "lon": -3.70379,
             "tags": {"amenity": "charging_station", "name": "Punto Centro", "operator": "Red pública",
                      "maxpower": "50 kW", "socket:type2": "2", "addr:street": "Calle Sol"}},
            {"type": "way", "id": 42, "center": {"lat": 40.42678, "lon": -3.70379},
             "tags": {"amenity": "charging_station", "name": "Punto Norte", "socket:ccs": "yes",
                      "socket:ccs:output": "75kW"}},
            {"type": "relation", "id": 43, "center": {"lat": 40.60, "lon": -3.70},
             "tags": {"amenity": "charging_station", "name": "Fuera del radio"}},
            {"type": "node", "id": 44, "lat": 40.41679, "lon": -3.70379,
             "tags": {"amenity": "charging_station", "name": "<img src=x>"}},
        ]
    }

    async def handler(request):
        requests.append(request)
        return httpx2.Response(200, json=payload)

    navigation = Navigation(_enabled_config(), tmp_path / "map-cache", transport=httpx2.MockTransport(handler))

    async def run():
        first = await navigation.chargers(40.416784, -3.703794, True)
        second = await navigation.chargers(40.416784, -3.703794, True)
        await navigation.close()
        return first, second

    first, second = asyncio.run(run())
    assert len(requests) == 1
    request = requests[0]
    assert request.url.scheme == "https"
    assert request.url.host == "overpass-api.de"
    assert request.url.path == "/api/interpreter"
    assert request.method == "POST"
    query = parse_qs(request.content.decode("ascii"))["data"][0]
    assert query.startswith("[out:json][timeout:8][maxsize:524288];")
    assert query.count('[' + '"amenity"="charging_station"' + ']') == 3
    assert query.count("around:5000,40.41678,-3.70379") == 3
    assert "node[" in query and "way[" in query and "relation[" in query and "out center tags" in query
    assert first["source"] == "OpenStreetMap Overpass"
    assert first["cached"] is False and second["cached"] is True
    assert first["truncated"] is False
    assert [station["id"] for station in first["stations"]] == ["node/41", "way/42"]
    assert first["stations"][0]["connectors"] == ["type2"]
    assert first["stations"][0]["power_kw"] == 50.0
    assert first["stations"][0]["address"] == "Calle Sol"
    assert first["stations"][0]["distance_m"] == 0
    assert first["stations"][1]["operator"] is None
    assert first["stations"][1]["power_kw"] == 75.0


def test_charger_list_is_capped_and_marks_truncation(tmp_path):
    rows = [
        {"type": "node", "id": index + 1, "lat": 40.0, "lon": -3.0,
         "tags": {"amenity": "charging_station", "name": f"Punto {index}"}}
        for index in range(61)
    ]

    async def handler(_request):
        return httpx2.Response(200, json={"elements": rows})

    navigation = Navigation(_enabled_config(), tmp_path / "map-cache", transport=httpx2.MockTransport(handler))

    async def run():
        result = await navigation.chargers(40, -3, True)
        await navigation.close()
        return result

    result = asyncio.run(run())
    assert len(result["stations"]) == 60
    assert result["truncated"] is True


def test_chargers_rejects_boolean_and_out_of_range_coordinates_before_network(tmp_path):
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx2.Response(200, json={"elements": []})

    navigation = Navigation(_enabled_config(), tmp_path / "map-cache", transport=httpx2.MockTransport(handler))

    async def run():
        for latitude, longitude in ((True, 0), (91, 0), (0, float("nan")), (0, 181), (10**400, 0)):
            with pytest.raises(DomainError) as error:
                await navigation.chargers(latitude, longitude, True)
            assert error.value.status == 400
        await navigation.close()

    asyncio.run(run())
    assert requests == []


def test_chargers_discards_unhashable_types_and_overflowing_coordinates(tmp_path):
    payload = {"elements": [
        {"type": [], "id": 1, "lat": 40, "lon": -3,
         "tags": {"amenity": "charging_station", "name": "Fila inválida"}},
        {"type": {"node": True}, "id": 2, "lat": 40, "lon": -3,
         "tags": {"amenity": "charging_station", "name": "Fila inválida"}},
        {"type": "node", "id": 3, "lat": 10**400, "lon": -3,
         "tags": {"amenity": "charging_station", "name": "Coordenada enorme"}},
        {"type": "node", "id": 4, "lat": 40.0, "lon": -3.0,
         "tags": {"amenity": "charging_station", "name": "Punto válido"}},
    ]}

    async def handler(_request):
        return httpx2.Response(200, json=payload)

    navigation = Navigation(_enabled_config(), tmp_path / "map-cache", transport=httpx2.MockTransport(handler))

    async def run():
        result = await navigation.chargers(40.0, -3.0, True)
        await navigation.close()
        return result

    result = asyncio.run(run())
    assert [station["name"] for station in result["stations"]] == ["Punto válido"]


def test_overpass_response_is_bounded_at_512kib(tmp_path):
    async def oversized(_request):
        return httpx2.Response(200, content=b'{"elements":[]}' + b" " * (512 * 1024),
                               headers={"content-type": "application/json"})

    navigation = Navigation(_enabled_config(), tmp_path / "map-cache", transport=httpx2.MockTransport(oversized))

    async def run():
        with pytest.raises(DomainError) as error:
            await navigation.chargers(40.0, -3.0, True)
        assert error.value.status == 502
        await navigation.close()

    asyncio.run(run())


def test_tile_fetches_contactable_png_preserves_only_referer_origin_and_caches(tmp_path):
    requests = []
    png = _png()

    async def handler(request):
        requests.append(request)
        return httpx2.Response(200, content=png, headers={
            "content-type": "image/png", "cache-control": "public, max-age=3600", "etag": '"tile-v1"'
        })

    navigation = Navigation(_enabled_config(), tmp_path / "map-cache", transport=httpx2.MockTransport(handler))

    async def run():
        first = await navigation.tile(4, 3, 5, True, "https://pulse.example/path/page?token=private")
        second = await navigation.tile(4, 3, 5, True)
        await navigation.close()
        return first, second

    first, second = asyncio.run(run())
    assert len(requests) == 1
    assert requests[0].url.path == "/4/3/5.png"
    assert requests[0].url.host == "tile.openstreetmap.org"
    assert requests[0].headers["user-agent"].startswith("Leapmotor Pulse/")
    assert requests[0].headers["referer"] == "https://pulse.example"
    assert first["cached"] is False and second["cached"] is True
    assert first["data_url"] == "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    cache_path = tmp_path / "map-cache" / "navigation-tiles"
    tile_files = list(cache_path.glob("*.tile"))
    assert len(tile_files) == 1
    assert len(tile_files[0].stem) == 64
    assert tile_files[0].stat().st_mode & 0o777 == 0o600
    assert cache_path.stat().st_mode & 0o777 == 0o700


def test_tile_revalidates_expired_etag_with_304_and_reuses_body(tmp_path, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("pulse.navigation.time.time", lambda: now[0])
    requests = []
    png = _png()

    async def handler(request):
        requests.append(request)
        if request.headers.get("if-none-match") == '"tile-v1"':
            return httpx2.Response(304, headers={"cache-control": "max-age=20", "etag": '"tile-v1"'})
        return httpx2.Response(200, content=png, headers={
            "content-type": "image/png", "cache-control": "max-age=10", "etag": '"tile-v1"'
        })

    navigation = Navigation(_enabled_config(), tmp_path / "map-cache", transport=httpx2.MockTransport(handler))

    async def run():
        first = await navigation.tile(2, 1, 1, True)
        now[0] = 1020.0
        refreshed = await navigation.tile(2, 1, 1, True)
        now[0] = 1030.0
        cached = await navigation.tile(2, 1, 1, True)
        await navigation.close()
        return first, refreshed, cached

    first, refreshed, cached = asyncio.run(run())
    assert len(requests) == 2
    assert requests[1].headers["if-none-match"] == '"tile-v1"'
    assert first["cached"] is False
    assert refreshed == {"data_url": first["data_url"], "cached": True}
    assert cached["cached"] is True


def test_tile_304_etag_only_preserves_original_max_age(tmp_path, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("pulse.navigation.time.time", lambda: now[0])
    requests = []
    png = _png()

    async def handler(request):
        requests.append(request)
        if request.headers.get("if-none-match") == '"tile-v1"':
            return httpx2.Response(304, headers={"etag": '"tile-v1"'})
        return httpx2.Response(200, content=png, headers={
            "content-type": "image/png", "cache-control": "max-age=60", "etag": '"tile-v1"'
        })

    navigation = Navigation(_enabled_config(), tmp_path / "map-cache", transport=httpx2.MockTransport(handler))

    async def run():
        await navigation.tile(1, 0, 0, True)
        now[0] = 1061.0
        refreshed = await navigation.tile(1, 0, 0, True)
        now[0] = 1122.0
        stale_again = await navigation.tile(1, 0, 0, True)
        await navigation.close()
        return refreshed, stale_again

    refreshed, stale_again = asyncio.run(run())
    assert refreshed["cached"] is True and stale_again["cached"] is True
    assert len(requests) == 3


@pytest.mark.parametrize("cache_header", [
    {"cache-control": "no-cache"},
    {"expires": "Wed, 21 Oct 2015 07:28:00 GMT"},
])
def test_tile_304_etag_only_preserves_revalidate_each_use_policy(tmp_path, monkeypatch, cache_header):
    now = [1_800_000_000.0]
    monkeypatch.setattr("pulse.navigation.time.time", lambda: now[0])
    requests = []
    png = _png()

    async def handler(request):
        requests.append(request)
        if request.headers.get("if-none-match") == '"tile-v1"':
            return httpx2.Response(304, headers={"etag": '"tile-v1"'})
        return httpx2.Response(200, content=png, headers={
            "content-type": "image/png", "etag": '"tile-v1"', **cache_header
        })

    navigation = Navigation(_enabled_config(), tmp_path / "map-cache", transport=httpx2.MockTransport(handler))

    async def run():
        await navigation.tile(1, 0, 0, True)
        await navigation.tile(1, 0, 0, True)
        await navigation.tile(1, 0, 0, True)
        await navigation.close()

    asyncio.run(run())
    assert len(requests) == 3


def test_tile_waiting_for_two_slots_is_in_global_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr("pulse.navigation.TILE_TIMEOUT_SECONDS", 0.01)
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx2.Response(200, content=_png(), headers={"content-type": "image/png"})

    navigation = Navigation(_enabled_config(), tmp_path / "map-cache", transport=httpx2.MockTransport(handler))

    async def run():
        await navigation._tile_semaphore.acquire()
        await navigation._tile_semaphore.acquire()
        try:
            with pytest.raises(DomainError) as error:
                await navigation.tile(1, 0, 0, True)
            assert error.value.status == 504
            assert navigation._tile_waiters == 0
        finally:
            navigation._tile_semaphore.release()
            navigation._tile_semaphore.release()
            await navigation.close()

    asyncio.run(run())
    assert requests == []


def test_tile_waiter_queue_has_a_fixed_bound(tmp_path):
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx2.Response(200, content=_png(), headers={"content-type": "image/png"})

    navigation = Navigation(_enabled_config(), tmp_path / "map-cache", transport=httpx2.MockTransport(handler))

    async def run():
        await navigation._tile_semaphore.acquire()
        await navigation._tile_semaphore.acquire()
        waiting = []
        try:
            for _ in range(2):
                waiting.append(asyncio.create_task(navigation.tile(1, 0, 0, True)))
                await asyncio.sleep(0)
            assert navigation._tile_waiters == 2
            with pytest.raises(DomainError) as error:
                await navigation.tile(1, 0, 0, True)
            assert error.value.status == 429
            assert navigation._tile_waiters == 2
        finally:
            for task in waiting:
                task.cancel()
            await asyncio.gather(*waiting, return_exceptions=True)
            assert navigation._tile_waiters == 0
            navigation._tile_semaphore.release()
            navigation._tile_semaphore.release()
            await navigation.close()

    asyncio.run(run())
    assert requests == []


def test_tile_without_cache_headers_uses_seven_day_default(tmp_path, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("pulse.navigation.time.time", lambda: now[0])
    requests = []
    png = _png()

    async def handler(request):
        requests.append(request)
        return httpx2.Response(200, content=png, headers={"content-type": "image/png"})

    navigation = Navigation(_enabled_config(), tmp_path / "map-cache", transport=httpx2.MockTransport(handler))

    async def run():
        await navigation.tile(1, 0, 0, True)
        now[0] += 6 * 24 * 60 * 60
        cached = await navigation.tile(1, 0, 0, True)
        await navigation.close()
        return cached

    cached = asyncio.run(run())
    assert cached["cached"] is True
    assert len(requests) == 1


@pytest.mark.parametrize("coordinates", [(19, 0, 0), (2, 4, 0), (2, 0, 4), (True, 0, 0)])
def test_tile_rejects_invalid_coordinates_without_network(tmp_path, coordinates):
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx2.Response(200, content=_png(), headers={"content-type": "image/png"})

    navigation = Navigation(_enabled_config(), tmp_path / "map-cache", transport=httpx2.MockTransport(handler))

    async def run():
        with pytest.raises(DomainError) as error:
            await navigation.tile(*coordinates, True)
        assert error.value.status == 400
        await navigation.close()

    asyncio.run(run())
    assert requests == []
    assert not (tmp_path / "map-cache").exists()


@pytest.mark.parametrize("body, content_type", [(b"not png", "image/png"), (_png(128, 256), "image/png"), (_png(), "text/html")])
def test_tile_rejects_malformed_png_or_wrong_content_type(tmp_path, body, content_type):
    async def handler(_request):
        return httpx2.Response(200, content=body, headers={"content-type": content_type})

    navigation = Navigation(_enabled_config(), tmp_path / "map-cache", transport=httpx2.MockTransport(handler))

    async def run():
        with pytest.raises(DomainError) as error:
            await navigation.tile(1, 0, 0, True)
        assert error.value.status == 502
        await navigation.close()

    asyncio.run(run())


def test_png_validator_checks_chunk_crc_and_dimensions():
    valid = _png()
    assert _png_256(valid)
    corrupt = valid[:-1] + bytes([valid[-1] ^ 1])
    assert not _png_256(corrupt)
    assert not _png_256(_png(255, 256))


def test_non_ascii_upstream_etag_is_not_replayed_as_ascii_header(tmp_path):
    requests = []
    async def handler(request):
        requests.append(request)
        return httpx2.Response(200, content=_png(), headers=[
            (b"content-type", b"image/png"), (b"cache-control", b"no-cache"), (b"etag", b'"\xe9"')])
    navigation = Navigation(_enabled_config(), tmp_path / "map-cache", transport=httpx2.MockTransport(handler))
    async def run():
        for _ in range(2):
            result = await navigation.tile(1, 0, 0, True)
            assert result["data_url"].startswith("data:image/png;base64,")
        await navigation.close()
    asyncio.run(run())
    assert len(requests) == 2 and all("if-none-match" not in request.headers for request in requests)


@pytest.mark.parametrize("contact", ["https://example.invalid/é", "https://example.invalid/" + "a" * 512])
def test_contact_cannot_break_user_agent_encoding(contact):
    with pytest.raises(ValueError):
        NavigationConfig(enabled=True, contact=contact)
