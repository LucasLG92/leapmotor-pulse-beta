"""Consent-gated public place, charger and OSM tile lookups."""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import math
import re
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit
import zlib

import httpx2

from pulse.domain import DomainError
from pulse.navigation_cache import TILE_MAX_BYTES, TTLCache, TileCacheError, TileDiskCache, TileEntry
from pulse.navigation_config import NavigationConfig


SOURCE = "OpenStreetMap"
ATTRIBUTION = "© OpenStreetMap contributors"
PLACES_SOURCE = "OpenStreetMap Nominatim"
CHARGERS_SOURCE = "OpenStreetMap Overpass"
PLACES_MAX_BYTES = 256 * 1024
CHARGERS_MAX_BYTES = 512 * 1024
OVERPASS_MAX_ELEMENTS = 4096
REQUEST_TIMEOUT_SECONDS = 10.0
PLACES_TIMEOUT_SECONDS = 10.0
CHARGERS_TIMEOUT_SECONDS = 8.0
TILE_TIMEOUT_SECONDS = 10.0
DEFAULT_TILE_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_TILE_ZOOM = 18
MAX_SEARCH_RESULTS = 5
MAX_CHARGER_RESULTS = 60
MAX_TILE_WAITERS = 2
_EARTH_RADIUS_METERS = 6_371_008.8
_URL_RE = re.compile(r"(?:[a-z][a-z0-9+.-]{0,20}://|www\.)", re.IGNORECASE)
_POWER_RE = re.compile(r"\s*([0-9]+(?:\.[0-9]+)?)\s*(?:kw)?\s*\Z", re.IGNORECASE)
_CONNECTOR_KEYS = {
    "socket:type2": "type2",
    "socket:type2_combo": "type2_combo",
    "socket:ccs": "ccs",
    "socket:chademo": "chademo",
    "socket:tesla_supercharger": "tesla_supercharger",
    "socket:tesla_destination": "tesla_destination",
    "socket:schuko": "schuko",
    "socket:cee_blue": "cee_blue",
    "socket:cee_red": "cee_red",
}
_CONNECTOR_OUTPUT_KEYS = (
    "socket:type2:output", "socket:type2_combo:output", "socket:ccs:output",
    "socket:chademo:output", "socket:tesla_supercharger:output", "socket:tesla_destination:output",
)

_MESSAGES = {
    "disabled": "La navegación todavía no está configurada en el servidor.",
    "consent": "Confirma el envío de esta consulta al servicio público de mapas.",
    "invalid": "La solicitud de navegación no es válida.",
    "rate": "Espera antes de volver a consultar el servicio público de mapas.",
    "upstream": "El servicio público de mapas no devolvió una respuesta válida.",
    "timeout": "La consulta al servicio público de mapas agotó el tiempo disponible.",
    "cache_full": "La caché de mapas está llena; inténtalo de nuevo más tarde.",
    "cache": "No se pudo acceder a la caché local de mapas.",
    "closed": "El servicio de navegación ya se ha cerrado.",
}


def _fail(code: str, status: int) -> DomainError:
    return DomainError(_MESSAGES[code], status)


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate json key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError("invalid json constant")


def _parse_json(body: bytes) -> Any:
    try:
        return json.loads(body, object_pairs_hook=_json_object, parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, ValueError, TypeError, RecursionError):
        raise _fail("upstream", 502) from None


def _iso_utc(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise _fail("upstream", 502)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _finite_number(value: Any, lower: float, upper: float, *, allow_string: bool = False) -> float:
    if type(value) in {int, float}:
        try:
            number = float(value)
        except (ValueError, OverflowError):
            raise ValueError("invalid coordinate") from None
    elif allow_string and isinstance(value, str) and len(value) <= 32:
        try:
            number = float(value)
        except (ValueError, OverflowError):
            raise ValueError("invalid coordinate") from None
    else:
        raise ValueError("invalid coordinate")
    if not math.isfinite(number) or not lower <= number <= upper:
        raise ValueError("invalid coordinate")
    return number


def _normalize_query(query: Any) -> str:
    if (not isinstance(query, str) or not 3 <= len(query) <= 120
            or any(ord(char) < 32 or ord(char) == 127 for char in query)):
        raise _fail("invalid", 400)
    normalized = " ".join(query.split())
    if not 3 <= len(normalized) <= 120:
        raise _fail("invalid", 400)
    return normalized


def _query_cache_key(query: str) -> str:
    normalized = " ".join(query.casefold().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _coordinate(value: Any, lower: float, upper: float) -> float:
    if type(value) not in {int, float}:
        raise _fail("invalid", 400)
    try:
        number = float(value)
    except (ValueError, OverflowError):
        raise _fail("invalid", 400) from None
    if not math.isfinite(number) or not lower <= number <= upper:
        raise _fail("invalid", 400)
    return 0.0 if number == 0 else number


def _viewport_cache_key(latitude: float, longitude: float) -> str:
    # Only a digest enters RAM cache keys; coordinate precision is one metre.
    key = f"{latitude:.5f},{longitude:.5f}".encode("ascii")
    return hashlib.sha256(key).hexdigest()


def _display_text(value: Any, *, maximum: int = 160) -> str | None:
    if not isinstance(value, str):
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    text = " ".join(value.split())
    if not text or len(text) > maximum or "<" in text or ">" in text or _URL_RE.search(text):
        return None
    return text


def _identifier(value: Any) -> int | None:
    if type(value) is int and 1 <= value <= 2**53 - 1:
        return value
    return None


def _places(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise _fail("upstream", 502)
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    # Malformed individual rows are discarded; a malformed top-level envelope fails closed.
    for row in payload:
        if len(result) >= MAX_SEARCH_RESULTS:
            break
        if not isinstance(row, dict):
            continue
        osm_type = row.get("osm_type")
        osm_id = _identifier(row.get("osm_id"))
        if not isinstance(osm_type, str) or osm_type not in {"node", "way", "relation"} or osm_id is None:
            continue
        item_id = f"{osm_type}/{osm_id}"
        if item_id in seen:
            continue
        try:
            latitude = _finite_number(row.get("lat"), -90, 90, allow_string=True)
            longitude = _finite_number(row.get("lon"), -180, 180, allow_string=True)
        except ValueError:
            continue
        name = _display_text(row.get("name"))
        if name is None:
            display_name = row.get("display_name")
            if isinstance(display_name, str):
                name = _display_text(display_name.split(",", 1)[0])
        if name is None:
            continue
        seen.add(item_id)
        result.append({"id": item_id, "name": name, "latitude": latitude, "longitude": longitude})
    return result


def _haversine(latitude: float, longitude: float, other_latitude: float, other_longitude: float) -> int:
    lat1, lat2 = math.radians(latitude), math.radians(other_latitude)
    delta_lat = lat2 - lat1
    delta_lon = math.radians(other_longitude - longitude)
    value = (math.sin(delta_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2)
             * math.sin(delta_lon / 2) ** 2)
    # Guard against floating point drift at antipodal points.
    value = min(1.0, max(0.0, value))
    return int(round(2 * _EARTH_RADIUS_METERS * math.asin(math.sqrt(value))))


def _positive_tag(tags: dict[str, Any], key: str) -> bool:
    value = tags.get(key)
    return isinstance(value, str) and value.strip().casefold() not in {"", "no", "false", "0"}


def _connectors(tags: dict[str, Any]) -> list[str]:
    return sorted(label for key, label in _CONNECTOR_KEYS.items() if _positive_tag(tags, key))


def _power_kw(tags: dict[str, Any]) -> float | None:
    for key in ("maxpower", "charging_station:output"):
        value = tags.get(key)
        if not isinstance(value, str) or len(value) > 32:
            continue
        match = _POWER_RE.fullmatch(value)
        if match is None:
            continue
        try:
            power = float(match.group(1))
        except ValueError:
            continue
        if math.isfinite(power) and 0 < power <= 2000:
            return power
    # Connector-specific outputs are source measurements; choose the maximum
    # documented value and never sum independent connector ratings.
    outputs = []
    for key in _CONNECTOR_OUTPUT_KEYS:
        value = tags.get(key)
        if not isinstance(value, str) or len(value) > 32:
            continue
        match = _POWER_RE.fullmatch(value)
        if match is None:
            continue
        try:
            power = float(match.group(1))
        except ValueError:
            continue
        if math.isfinite(power) and 0 < power <= 2000:
            outputs.append(power)
    if outputs:
        return max(outputs)
    return None


def _address(tags: dict[str, Any]) -> str | None:
    full = _display_text(tags.get("addr:full"))
    if full is not None:
        return full
    parts = []
    for key in ("addr:housenumber", "addr:street", "addr:postcode", "addr:city"):
        part = _display_text(tags.get(key), maximum=80)
        if part is not None:
            parts.append(part)
    combined = ", ".join(parts)
    return combined if parts and len(combined) <= 160 else None


def _station(row: Any, latitude: float, longitude: float) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    kind = row.get("type")
    if not isinstance(kind, str) or kind not in {"node", "way", "relation"}:
        return None
    osm_id = _identifier(row.get("id"))
    tags = row.get("tags")
    if osm_id is None or not isinstance(tags, dict):
        return None
    if kind == "node":
        raw_latitude, raw_longitude = row.get("lat"), row.get("lon")
    else:
        center = row.get("center")
        if not isinstance(center, dict):
            return None
        raw_latitude, raw_longitude = center.get("lat"), center.get("lon")
    try:
        station_latitude = _finite_number(raw_latitude, -90, 90)
        station_longitude = _finite_number(raw_longitude, -180, 180)
    except ValueError:
        return None
    name = _display_text(tags.get("name"))
    if name is None or tags.get("amenity") != "charging_station":
        return None
    distance = _haversine(latitude, longitude, station_latitude, station_longitude)
    if distance > 5000:
        return None
    operator = _display_text(tags.get("operator"))
    return {
        "id": f"{kind}/{osm_id}",
        "name": name,
        "latitude": station_latitude,
        "longitude": station_longitude,
        "operator": operator,
        "address": _address(tags),
        "connectors": _connectors(tags),
        "power_kw": _power_kw(tags),
        "distance_m": distance,
    }


def _stations(payload: Any, latitude: float, longitude: float) -> tuple[list[dict[str, Any]], bool]:
    if (not isinstance(payload, dict) or "remark" in payload
            or not isinstance(payload.get("elements"), list)):
        raise _fail("upstream", 502)
    elements = payload["elements"]
    if len(elements) > OVERPASS_MAX_ELEMENTS:
        raise _fail("upstream", 502)
    stations: list[dict[str, Any]] = []
    seen: set[str] = set()
    # Invalid or incomplete elements are omitted; no location, name or power is synthesized.
    for row in elements:
        station = _station(row, latitude, longitude)
        if station is None or station["id"] in seen:
            continue
        seen.add(station["id"])
        stations.append(station)
    stations.sort(key=lambda item: (item["distance_m"], item["id"]))
    truncated = len(stations) > MAX_CHARGER_RESULTS
    return stations[:MAX_CHARGER_RESULTS], truncated


def _safe_header(value: str | None) -> str | None:
    if (value is None or len(value) > 512 or not value.isascii()
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        return None
    return value


def _tile_expiry(headers: httpx2.Headers, now: float) -> float | None:
    cache_control = headers.get("cache-control")
    expires_header = headers.get("expires")
    if "*" in {part.strip() for part in headers.get("vary", "").split(",")}:
        return None
    if cache_control is not None:
        directives: dict[str, str | None] = {}
        for component in cache_control.split(","):
            name, separator, value = component.strip().partition("=")
            if name:
                directives[name.casefold()] = value.strip().strip('"') if separator else None
        if "no-store" in directives or "private" in directives:
            return None
        if "no-cache" in directives:
            return now
        age_header = headers.get("age", "0")
        try:
            age = max(0, int(age_header)) if len(age_header) <= 16 else 0
        except ValueError:
            age = 0
        for name in ("s-maxage", "max-age"):
            value = directives.get(name)
            if value is not None:
                if len(value) > 10:
                    return now
                try:
                    seconds = int(value)
                except ValueError:
                    return now
                if seconds < 0 or seconds > 2**31 - 1:
                    return now
                return now + max(0, seconds - age)
    if expires_header is not None:
        try:
            expires = parsedate_to_datetime(expires_header)
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            return max(now, expires.timestamp())
        except (TypeError, ValueError, OverflowError):
            return now
    return now + DEFAULT_TILE_TTL_SECONDS


def _merged_tile_policy(entry: TileEntry, response_headers: httpx2.Headers,
                        now: float) -> tuple[float | None, str | None, str | None]:
    """Merge 304 policy fields; validators and Age/Date only come from this response."""
    response_cache_control = response_headers.get("cache-control")
    response_expires = response_headers.get("expires")
    cache_control = (entry.cache_control if response_cache_control is None
                     else _safe_header(response_cache_control))
    expires = entry.expires if response_expires is None else _safe_header(response_expires)
    policy_headers: dict[str, str] = {}
    if cache_control is not None:
        policy_headers["cache-control"] = cache_control
    if expires is not None:
        policy_headers["expires"] = expires
    # A 304's Age and Vary may update the policy; never retain stale Age/Date from 200.
    for name in ("age", "vary"):
        value = _safe_header(response_headers.get(name))
        if value is not None:
            policy_headers[name] = value
    if ((response_cache_control is not None and cache_control is None)
            or (response_expires is not None and expires is None)):
        return now, cache_control, expires
    return _tile_expiry(httpx2.Headers(policy_headers), now), cache_control, expires


def _referer_origin(referer: Any) -> str | None:
    if not isinstance(referer, str) or not referer or len(referer) > 2048 or not referer.isascii():
        return None
    try:
        if any(ord(char) < 32 or ord(char) == 127 for char in referer):
            return None
        parsed = urlsplit(referer)
        port = parsed.port
        hostname = parsed.hostname
        if (parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.netloc.endswith(":")
                or (port is not None and not 1 <= port <= 65535)):
            return None
        if ":" in hostname:
            ipaddress.IPv6Address(hostname)
        elif (not re.fullmatch(r"[A-Za-z0-9.-]+", hostname)
              or any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
                     for label in hostname.rstrip(".").split("."))):
            return None
        return f"{parsed.scheme.lower()}://{parsed.netloc}"
    except ValueError:
        return None


def _png_256(data: bytes) -> bool:
    """Validate bounded PNG framing, every chunk CRC, dimensions and IEND."""
    signature = b"\x89PNG\r\n\x1a\n"
    if len(data) < 8 + 25 + 12 or not data.startswith(signature):
        return False
    offset = len(signature)
    chunk_index = 0
    saw_idat = False
    idat_ended = False
    saw_palette = False
    saw_iend = False
    compressed_parts: list[bytes] = []
    width = height = depth = color_type = None
    while offset < len(data):
        if len(data) - offset < 12:
            return False
        size = int.from_bytes(data[offset:offset + 4], "big")
        kind = data[offset + 4:offset + 8]
        end = offset + 12 + size
        if size > TILE_MAX_BYTES or end > len(data):
            return False
        content = data[offset + 4:offset + 8 + size]
        expected_crc = int.from_bytes(data[offset + 8 + size:end], "big")
        if zlib.crc32(content) & 0xFFFFFFFF != expected_crc:
            return False
        chunk = data[offset + 8:offset + 8 + size]
        if any(char not in b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz" for char in kind):
            return False
        # The third chunk-type letter is reserved and must remain uppercase.
        if kind[2] & 0x20:
            return False
        if chunk_index == 0 and kind != b"IHDR":
            return False
        if kind not in {b"IHDR", b"PLTE", b"IDAT", b"IEND"} and not kind[0] & 0x20:
            return False
        if chunk_index == 0:
            if kind != b"IHDR" or size != 13:
                return False
            width, height = int.from_bytes(chunk[:4], "big"), int.from_bytes(chunk[4:8], "big")
            depth, color_type = chunk[8], chunk[9]
            if (width, height) != (256, 256) or chunk[10] != 0 or chunk[11] != 0 or chunk[12] not in {0, 1}:
                return False
            if color_type not in {0, 2, 3, 4, 6}:
                return False
            allowed_depths = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8},
                              4: {8, 16}, 6: {8, 16}}
            if depth not in allowed_depths[color_type]:
                return False
        elif kind == b"IHDR":
            return False
        if kind == b"PLTE":
            if saw_idat or saw_palette or size == 0 or size > 768 or size % 3:
                return False
            if color_type in {0, 4} or (color_type == 3 and size // 3 > (1 << depth)):
                return False
            saw_palette = True
        if kind == b"IDAT":
            if saw_iend or idat_ended:
                return False
            saw_idat = True
            compressed_parts.append(chunk)
        elif saw_idat and kind != b"IEND":
            idat_ended = True
        if kind == b"IEND":
            if size != 0 or not saw_idat or end != len(data):
                return False
            saw_iend = True
        elif saw_iend:
            return False
        offset = end
        chunk_index += 1
    if not saw_idat or not saw_iend or offset != len(data) or (color_type == 3 and not saw_palette):
        return False
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    row_bytes = (256 * channels * depth + 7) // 8
    expected_size = 256 * (row_bytes + 1)
    try:
        inflater = zlib.decompressobj()
        raw = inflater.decompress(b"".join(compressed_parts), expected_size + 1)
        if len(raw) != expected_size or inflater.unconsumed_tail or inflater.unused_data or not inflater.eof:
            return False
        if any(raw[index * (row_bytes + 1)] > 4 for index in range(256)):
            return False
    except zlib.error:
        return False
    return True


class Navigation:
    """Consent-gated public lookups; status and disabled paths perform no I/O."""

    def __init__(self, config: NavigationConfig, cache_dir: str | Path, transport=None):
        if not isinstance(config, NavigationConfig):
            raise TypeError("Navigation requiere NavigationConfig.")
        if transport is not None and not isinstance(transport, httpx2.AsyncBaseTransport):
            raise TypeError("El transporte de navegación solo admite httpx2.AsyncBaseTransport de fixtures.")
        self.config = config
        self.cache_dir = Path(cache_dir)
        self.transport = transport
        self._client: httpx2.AsyncClient | None = None
        self._closed = False
        self._places_cache = TTLCache(64, 30 * 60)
        self._chargers_cache = TTLCache(32, 60 * 60)
        self._tile_cache = TileDiskCache(self.cache_dir)
        self._places_busy = False
        self._places_next = 0.0
        self._chargers_busy = False
        self._chargers_next = 0.0
        self._tile_semaphore = asyncio.Semaphore(2)
        self._tile_waiters = 0
        self.user_agent = f"Leapmotor Pulse/1.0 (navigation; contact: {config.contact})"

    def status(self) -> dict[str, Any]:
        return {"enabled": self.config.enabled, "source": SOURCE,
                "reason": "ready" if self.config.enabled else "disabled", "attribution": ATTRIBUTION}

    async def search(self, query: str, consent: bool) -> dict[str, Any]:
        self._guard(consent)
        normalized = _normalize_query(query)
        key = _query_cache_key(normalized)
        cached = self._places_cache.get(key)
        if cached is not None:
            return {**cached, "cached": True}
        self._take_rate_slot("places", 1.0)
        try:
            client = self._ensure_client()
            params = {"format": "jsonv2", "q": normalized, "limit": "5", "addressdetails": "0"}
            body = await self._read_json(
                client, "GET", self.config.places_origin + "/search", params=params,
                headers={"Accept": "application/json", "Accept-Language": "es",
                         "User-Agent": self.user_agent},
                max_bytes=PLACES_MAX_BYTES, timeout=PLACES_TIMEOUT_SECONDS,
            )
            places = _places(_parse_json(body))
            result = {"source": PLACES_SOURCE, "fetched_at": _iso_utc(), "places": places}
            self._places_cache.set(key, result)
            return {**result, "cached": False}
        finally:
            self._release_rate_slot("places")

    async def chargers(self, latitude: float, longitude: float, consent: bool) -> dict[str, Any]:
        self._guard(consent)
        center_latitude = _coordinate(latitude, -90, 90)
        center_longitude = _coordinate(longitude, -180, 180)
        # Cache and query use one deterministic centre, without retaining coordinates in keys.
        center_latitude = round(center_latitude, 5)
        center_longitude = round(center_longitude, 5)
        key = _viewport_cache_key(center_latitude, center_longitude)
        cached = self._chargers_cache.get(key)
        if cached is not None:
            return {**cached, "cached": True}
        self._take_rate_slot("chargers", 10.0)
        query = self._overpass_query(center_latitude, center_longitude)
        try:
            client = self._ensure_client()
            body = await self._read_json(
                client, "POST", self.config.chargers_origin + "/api/interpreter",
                headers={"Accept": "application/json", "User-Agent": self.user_agent,
                         "Content-Type": "application/x-www-form-urlencoded"},
                content=urlencode({"data": query}),
                max_bytes=CHARGERS_MAX_BYTES, timeout=CHARGERS_TIMEOUT_SECONDS,
            )
            stations, truncated = _stations(_parse_json(body), center_latitude, center_longitude)
            result = {"source": CHARGERS_SOURCE, "fetched_at": _iso_utc(), "truncated": truncated,
                      "stations": stations}
            self._chargers_cache.set(key, result)
            return {**result, "cached": False}
        finally:
            self._release_rate_slot("chargers")

    async def tile(self, z: int, x: int, y: int, consent: bool, referer: str | None = None) -> dict[str, Any]:
        self._guard(consent)
        if type(z) is not int or type(x) is not int or type(y) is not int or not 0 <= z <= MAX_TILE_ZOOM:
            raise _fail("invalid", 400)
        maximum = 1 << z
        if not 0 <= x < maximum or not 0 <= y < maximum:
            raise _fail("invalid", 400)
        key = hashlib.sha256(f"{z}/{x}/{y}".encode("ascii")).hexdigest()
        now = time.time()
        try:
            entry = self._tile_cache.load(key)
        except (OSError, TileCacheError):
            raise _fail("cache", 503) from None
        if entry is not None and not _png_256(entry.data):
            try:
                self._tile_cache.remove(key)
            except (OSError, TileCacheError):
                raise _fail("cache", 503) from None
            entry = None
        if entry is not None and entry.expires_at > now:
            return {"data_url": self._data_url(entry.data), "cached": True}
        self._guard_open()
        headers = {"Accept": "image/png", "User-Agent": self.user_agent}
        safe_referer = _referer_origin(referer)
        if safe_referer is not None:
            headers["Referer"] = safe_referer
        if entry is not None and entry.etag is not None:
            headers["If-None-Match"] = entry.etag
        elif entry is not None and entry.last_modified is not None:
            headers["If-Modified-Since"] = entry.last_modified
        queued = False
        try:
            # A small explicit waiter cap prevents bursts from accumulating an
            # unbounded task queue behind the two active tile requests.
            async with asyncio.timeout(TILE_TIMEOUT_SECONDS):
                if self._tile_semaphore.locked():
                    if self._tile_waiters >= MAX_TILE_WAITERS:
                        raise _fail("rate", 429)
                    self._tile_waiters += 1
                    queued = True
                try:
                    async with self._tile_semaphore:
                        if queued:
                            self._tile_waiters -= 1
                            queued = False
                        client = self._ensure_client()
                        response = await self._read_tile_response(
                            client, self.config.tiles_origin + f"/{z}/{x}/{y}.png", headers,
                        )
                finally:
                    if queued:
                        self._tile_waiters -= 1
                        queued = False
        except DomainError:
            raise
        except asyncio.CancelledError:
            raise
        except (asyncio.TimeoutError, httpx2.TimeoutException):
            raise _fail("timeout", 504) from None
        except httpx2.HTTPError:
            raise _fail("upstream", 502) from None
        if response[0] == 304:
            if entry is None:
                raise _fail("upstream", 502)
            expires_at, cache_control, expires = _merged_tile_policy(entry, response[1], time.time())
            if expires_at is None:
                try:
                    self._tile_cache.remove(key)
                except (OSError, TileCacheError):
                    raise _fail("cache", 503) from None
            else:
                updated = TileEntry(entry.data, expires_at,
                                    _safe_header(response[1].get("etag")) or entry.etag,
                                    _safe_header(response[1].get("last-modified")) or entry.last_modified,
                                    cache_control, expires)
                try:
                    self._tile_cache.store(key, updated)
                except (OSError, TileCacheError):
                    # The upstream has validated this representation; preserve and serve it.
                    pass
            return {"data_url": self._data_url(entry.data), "cached": True}
        status, response_headers, data = response
        if status != 200 or not _png_256(data):
            raise _fail("upstream", 502)
        expires_at = _tile_expiry(response_headers, time.time())
        if expires_at is not None:
            new_entry = TileEntry(data, expires_at, _safe_header(response_headers.get("etag")),
                                  _safe_header(response_headers.get("last-modified")),
                                  _safe_header(response_headers.get("cache-control")),
                                  _safe_header(response_headers.get("expires")))
            try:
                stored = self._tile_cache.store(key, new_entry)
            except (OSError, TileCacheError):
                raise _fail("cache", 503) from None
            if not stored:
                raise _fail("cache_full", 503)
        else:
            try:
                self._tile_cache.remove(key)
            except (OSError, TileCacheError):
                raise _fail("cache", 503) from None
        return {"data_url": self._data_url(data), "cached": False}

    async def close(self) -> None:
        self._closed = True
        self._places_cache.clear()
        self._chargers_cache.clear()
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _guard(self, consent: bool) -> None:
        if type(consent) is not bool or not consent:
            raise _fail("consent", 400)
        if not self.config.enabled:
            raise _fail("disabled", 503)
        self._guard_open()

    def _guard_open(self) -> None:
        if self._closed:
            raise _fail("closed", 503)

    def _ensure_client(self) -> httpx2.AsyncClient:
        self._guard_open()
        if self._client is None:
            self._client = httpx2.AsyncClient(
                transport=self.transport,
                trust_env=False,
                follow_redirects=False,
                verify=True,
                timeout=httpx2.Timeout(REQUEST_TIMEOUT_SECONDS, connect=3.0),
                limits=httpx2.Limits(max_connections=2, max_keepalive_connections=2),
            )
        return self._client

    def _take_rate_slot(self, service: str, interval: float) -> None:
        now = time.monotonic()
        if service == "places":
            if self._places_busy or now < self._places_next:
                raise _fail("rate", 429)
            self._places_busy = True
            self._places_next = now + interval
        else:
            if self._chargers_busy or now < self._chargers_next:
                raise _fail("rate", 429)
            self._chargers_busy = True
            self._chargers_next = now + interval

    def _release_rate_slot(self, service: str) -> None:
        if service == "places":
            self._places_busy = False
        else:
            self._chargers_busy = False

    @staticmethod
    def _overpass_query(latitude: float, longitude: float) -> str:
        lat, lon = f"{latitude:.5f}", f"{longitude:.5f}"
        return (
            "[out:json][timeout:8][maxsize:524288];("
            f'node["amenity"="charging_station"](around:5000,{lat},{lon});'
            f'way["amenity"="charging_station"](around:5000,{lat},{lon});'
            f'relation["amenity"="charging_station"](around:5000,{lat},{lon});'
            ");out center tags;"
        )

    async def _read_json(self, client: httpx2.AsyncClient, method: str, url: str, *, max_bytes: int,
                         timeout: float, headers: dict[str, str], params: dict[str, str] | None = None,
                         content: str | None = None) -> bytes:
        chunks: list[bytes] = []
        size = 0
        try:
            async with asyncio.timeout(timeout):
                async with client.stream(
                    method, url, params=params, content=content, headers=headers,
                    timeout=httpx2.Timeout(timeout, connect=3.0), follow_redirects=False,
                ) as response:
                    if response.status_code != 200:
                        raise _fail("upstream", 502)
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
                    if content_type != "application/json":
                        raise _fail("upstream", 502)
                    if self._declared_too_large(response.headers, max_bytes):
                        raise _fail("upstream", 502)
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            raise _fail("upstream", 502)
                        chunks.append(chunk)
        except DomainError:
            raise
        except asyncio.CancelledError:
            raise
        except (asyncio.TimeoutError, httpx2.TimeoutException):
            raise _fail("timeout", 504) from None
        except httpx2.HTTPError:
            raise _fail("upstream", 502) from None
        return b"".join(chunks)

    @staticmethod
    def _declared_too_large(headers: httpx2.Headers, maximum: int) -> bool:
        raw = headers.get("content-length")
        if raw is None or len(raw) > 20:
            return False
        try:
            return int(raw) > maximum
        except ValueError:
            return False

    async def _read_tile_response(self, client: httpx2.AsyncClient, url: str,
                                  headers: dict[str, str]) -> tuple[int, httpx2.Headers, bytes]:
        chunks: list[bytes] = []
        size = 0
        try:
            async with asyncio.timeout(TILE_TIMEOUT_SECONDS):
                async with client.stream(
                    "GET", url, headers=headers,
                    timeout=httpx2.Timeout(TILE_TIMEOUT_SECONDS, connect=3.0), follow_redirects=False,
                ) as response:
                    if response.status_code == 304:
                        return 304, response.headers, b""
                    if response.status_code != 200:
                        raise _fail("upstream", 502)
                    if response.headers.get("content-type", "").split(";", 1)[0].strip().casefold() != "image/png":
                        raise _fail("upstream", 502)
                    if self._declared_too_large(response.headers, TILE_MAX_BYTES):
                        raise _fail("upstream", 502)
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > TILE_MAX_BYTES:
                            raise _fail("upstream", 502)
                        chunks.append(chunk)
                    return 200, response.headers, b"".join(chunks)
        except DomainError:
            raise
        except asyncio.CancelledError:
            raise
        except (asyncio.TimeoutError, httpx2.TimeoutException):
            raise _fail("timeout", 504) from None
        except httpx2.HTTPError:
            raise _fail("upstream", 502) from None

    @staticmethod
    def _data_url(data: bytes) -> str:
        return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


__all__ = ["Navigation", "NavigationConfig"]
