"""Real HTTP decoding over synthetic streams, without services or credentials."""

import asyncio
import gzip
import json
import zlib

import httpx2
import pytest

from pulse.config import Config
from pulse.domain import DomainError
from pulse.evcc import EvccClient, EvccConfig, EvccError
from pulse.native_auth import MAX_INTROSPECTION_BYTES, NativeAccessValidator
from pulse.navigation import PLACES_MAX_BYTES, Navigation
from pulse.navigation_cache import TILE_MAX_BYTES
from pulse.navigation_config import NavigationConfig
from pulse.pvpc import MAX_RESPONSE_BYTES, PvpcError, _read_bounded_response


LIMITS = {
    "pvpc": MAX_RESPONSE_BYTES,
    "evcc": 1024 * 1024,
    "places": PLACES_MAX_BYTES,
    "tiles": TILE_MAX_BYTES,
    "native": MAX_INTROSPECTION_BYTES,
}


class WireStream(httpx2.AsyncByteStream):
    def __init__(self, body):
        self.body = body
        self.closed = False

    async def __aiter__(self):
        # Several wire chunks exercise decoder state across receive boundaries.
        for offset in range(0, len(self.body), 127):
            yield self.body[offset:offset + 127]

    async def aclose(self):
        self.closed = True


def encode(body, coding):
    if coding == "identity":
        return body, None
    if coding == "gzip":
        return gzip.compress(body, mtime=0), "gzip"
    if coding == "deflate":
        return zlib.compress(body), "deflate"
    if coding == "raw-deflate":
        compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
        return compressor.compress(body) + compressor.flush(), "deflate"
    return zlib.compress(gzip.compress(body, mtime=0)), "gzip, deflate"


async def read_upstream(reader, transport, tmp_path):
    if reader == "evcc":
        evcc = EvccClient(EvccConfig(enabled=True, origin="http://127.0.0.1:7070", loadpoint="Fixture"),
                          transport=transport)
        return await evcc._read("/api/state")
    navigation = Navigation(NavigationConfig(), tmp_path / "unused-map-cache")
    try:
        async with httpx2.AsyncClient(transport=transport, trust_env=False) as client:
            if reader == "pvpc":
                return await _read_bounded_response(client, {})
            if reader == "places":
                return await navigation._read_json(client, "GET", "https://upstream.example.invalid/places",
                    max_bytes=PLACES_MAX_BYTES, timeout=8, headers={"Accept": "application/json"})
            if reader == "tiles":
                status, _headers, body = await navigation._read_tile_response(
                    client, "https://upstream.example.invalid/tile", {"Accept": "image/png"})
                assert status == 200
                return body
            validator = NativeAccessValidator(Config())
            return await validator._json(client, "GET", "https://upstream.example.invalid/userinfo")
    finally:
        await navigation.close()


@pytest.mark.parametrize("reader", LIMITS)
@pytest.mark.parametrize("coding", ["identity", "gzip", "deflate", "raw-deflate", "gzip-deflate"])
@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_decoded_response_limit_and_stream_close(reader, coding, offset, tmp_path):
    maximum = LIMITS[reader]
    size = maximum + offset
    body = b'{"fixture":"' + b"x" * (size - len(b'{"fixture":""}')) + b'"}'
    assert len(body) == size
    wire, encoding = encode(body, coding)
    stream = WireStream(wire)
    headers = {"content-type": "image/png" if reader == "tiles" else "application/json"}
    if encoding:
        headers["content-encoding"] = encoding
    # No declared length: the application must count the decoded stream itself.
    transport = httpx2.MockTransport(lambda _request: httpx2.Response(200, headers=headers, stream=stream))
    if offset > 0:
        errors = {"pvpc": (PvpcError, "response_too_large"), "evcc": (EvccError, "large"),
                  "places": (DomainError, None), "tiles": (DomainError, None), "native": (ValueError, None)}
        error_type, code = errors[reader]
        with pytest.raises(error_type) as caught:
            asyncio.run(read_upstream(reader, transport, tmp_path))
        if code:
            assert caught.value.code == code
        if reader in {"places", "tiles"}:
            assert caught.value.status == 502
    else:
        result = asyncio.run(read_upstream(reader, transport, tmp_path))
        assert result == (json.loads(body) if reader in {"evcc", "native"} else body)
    assert stream.closed
    assert not (tmp_path / "unused-map-cache").exists()


@pytest.mark.parametrize("reader", LIMITS)
def test_small_declared_compressed_length_does_not_bypass_decoded_limit(reader, tmp_path):
    maximum = LIMITS[reader]
    body = b'{"fixture":"' + b"x" * (maximum * 4) + b'"}'
    wire = gzip.compress(body, mtime=0)
    assert len(wire) < maximum
    stream = WireStream(wire)
    headers = {"content-type": "image/png" if reader == "tiles" else "application/json",
               "content-encoding": "gzip", "content-length": str(len(wire))}
    transport = httpx2.MockTransport(lambda _request: httpx2.Response(200, headers=headers, stream=stream))
    with pytest.raises({"pvpc": PvpcError, "evcc": EvccError, "places": DomainError,
                        "tiles": DomainError, "native": ValueError}[reader]):
        asyncio.run(read_upstream(reader, transport, tmp_path))
    assert stream.closed
