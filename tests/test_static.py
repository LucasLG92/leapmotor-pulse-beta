"""HTTP behavior and private/public boundary of precompressed build assets."""
import gzip
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pulse.app import create_app
from pulse.config import Config


@pytest.fixture
def files(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    data = {
        "index.html": b"<html><body>Generic Pulse shell</body></html>" * 30,
        "sw.js": b"self.addEventListener('fetch', () => {});" * 50,
        "manifest.webmanifest": b'{"name":"Pulse","start_url":"/"}',
        "icon.svg": b'<svg xmlns="http://www.w3.org/2000/svg"></svg>' * 30,
        "assets/main.js": b"console.log('generic static build');" * 100,
        "assets/main.css": b"body { color: #18352b; background: #f6f7f2; }" * 100,
    }
    for name, body in data.items():
        (dist / name).write_bytes(body)
        (dist / (name + ".gz")).write_bytes(gzip.compress(body, mtime=0))
    # PNG is not a compressed text candidate.
    (dist / "icon-192.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 50)
    return dist, data


@pytest.fixture
def client(tmp_path, files):
    dist, _ = files
    config = Config(hosts=("testserver",))
    app = create_app(tmp_path / "data/pulse.sqlite3", config=config, static_dir=dist)
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        yield client


@pytest.mark.parametrize("url,name", [
    ("/", "index.html"), ("/energy", "index.html"), ("/sw.js", "sw.js"),
    ("/icon.svg", "icon.svg"), ("/assets/main.js", "assets/main.js"),
    ("/assets/main.css", "assets/main.css"),
])
def test_gzip_public_bytes_mime_and_vary(client, files, url, name):
    dist, data = files
    response = client.get(url, headers={"Accept-Encoding": "gzip"})
    assert response.status_code == 200
    assert response.content == data[name]
    assert response.headers["content-encoding"] == "gzip"
    assert int(response.headers["content-length"]) == (dist / (name + ".gz")).stat().st_size
    assert response.headers["vary"] == "Accept-Encoding"
    assert "gzip" not in response.headers["content-type"]
    assert "set-cookie" not in response.headers
    if url == "/sw.js":
        assert response.headers["service-worker-allowed"] == "/"
        assert response.headers["content-type"].startswith("application/javascript")


@pytest.mark.parametrize("encoding", ["identity", "", "br", "gzip;q=0", "GZip;Q=0", "*;q=1,gzip;q=0", "gzip;q=0.4,identity;q=1", "gzip;q=oops", "gzip;q=NaN", "gzip;q=1.5", "gzip;q=0,gzip"])
def test_refused_gzip_serves_original(client, files, encoding):
    response = client.get("/assets/main.js", headers={"Accept-Encoding": encoding})
    assert response.status_code == 200
    assert response.content == files[1]["assets/main.js"]
    assert "content-encoding" not in response.headers
    assert response.headers["vary"] == "Accept-Encoding"


def test_wildcard_and_explicit_identity_refusal(client):
    assert client.get("/assets/main.js", headers={"Accept-Encoding": "*"}).headers["content-encoding"] == "gzip"
    assert client.get("/assets/main.js", headers={"Accept-Encoding": "gzip;q=0.5,identity;q=0"}).headers["content-encoding"] == "gzip"
    response = client.get("/assets/main.js", headers={"Accept-Encoding": "*;q=0"})
    assert response.status_code == 406
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["vary"] == "Accept-Encoding"


@pytest.mark.parametrize("values", [("gzip", "gzip;q=0,identity"), ("gzip;q=0,identity", "gzip")])
def test_multiple_encoding_headers_preserve_refusal(client, files, values):
    response = client.get("/assets/main.js", headers=[("Accept-Encoding", value) for value in values])
    assert response.status_code == 200
    assert response.content == files[1]["assets/main.js"]
    assert "content-encoding" not in response.headers


def test_representation_etags_and_conditional_requests(client):
    plain = client.get("/assets/main.js", headers={"Accept-Encoding": "identity"})
    zipped = client.get("/assets/main.js", headers={"Accept-Encoding": "gzip"})
    assert plain.headers["etag"] != zipped.headers["etag"]
    for response, encoding in [(plain, "identity"), (zipped, "gzip")]:
        revalidated = client.get("/assets/main.js", headers={"Accept-Encoding": encoding, "If-None-Match": response.headers["etag"]})
        assert revalidated.status_code == 304
        assert revalidated.content == b""
        assert revalidated.headers["vary"] == "Accept-Encoding"
    assert client.get("/assets/main.js", headers={"Accept-Encoding": "gzip", "If-None-Match": plain.headers["etag"]}).status_code == 200


@pytest.mark.parametrize("url", ["/", "/settings", "/sw.js", "/manifest.webmanifest", "/assets/main.js"])
def test_head_preserves_selected_length_without_body(client, url):
    get = client.get(url, headers={"Accept-Encoding": "gzip"})
    head = client.head(url, headers={"Accept-Encoding": "gzip"})
    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-length"] == get.headers["content-length"]
    assert head.headers["etag"] == get.headers["etag"]


def test_range_uses_original_representation(client, files):
    response = client.get("/assets/main.js", headers={"Accept-Encoding": "gzip", "Range": "bytes=0-9"})
    assert response.status_code == 206
    assert response.content == files[1]["assets/main.js"][:10]
    assert "content-encoding" not in response.headers
    assert response.headers["content-range"].startswith("bytes 0-9/")
    assert client.get("/assets/main.js", headers={"Accept-Encoding": "gzip,identity;q=0", "Range": "bytes=0-9"}).status_code == 406


def test_missing_and_stale_sidecars_fall_back_without_truncation(client, files):
    dist, data = files
    sidecar = dist / "assets/main.js.gz"
    sidecar.unlink()
    response = client.get("/assets/main.js", headers={"Accept-Encoding": "gzip"})
    assert "content-encoding" not in response.headers and response.content == data["assets/main.js"]
    assert client.get("/assets/main.js", headers={"Accept-Encoding": "gzip,identity;q=0"}).status_code == 406
    sidecar.write_bytes(gzip.compress(data["assets/main.js"], mtime=0))
    os.utime(sidecar, (1, 1))
    response = client.get("/assets/main.js", headers={"Accept-Encoding": "gzip"})
    assert "content-encoding" not in response.headers and response.content == data["assets/main.js"]


def test_sidecar_symlink_cannot_escape_public_root(client, files, tmp_path):
    dist, data = files
    outside = tmp_path / "not-public.gz"
    outside.write_bytes(gzip.compress(b"OUTSIDE_SENTINEL", mtime=0))
    sidecar = dist / "assets/main.js.gz"
    sidecar.unlink()
    sidecar.symlink_to(outside)
    response = client.get("/assets/main.js", headers={"Accept-Encoding": "gzip"})
    assert response.content == data["assets/main.js"] and "content-encoding" not in response.headers


@pytest.mark.parametrize("url", ["/assets/main.js.gz", "/index.html.gz", "/sw.js.gz", "/assets/missing.js", "/assets/%2e%2e%2findex.html"])
def test_sidecar_and_traversal_not_exposed_or_immutable(client, url):
    response = client.get(url, headers={"Accept-Encoding": "gzip"})
    assert response.status_code == 404
    assert "immutable" not in response.headers["cache-control"]


def test_private_api_and_session_cookie_are_never_compressed(client):
    session = client.get("/api/session", headers={"Accept-Encoding": "gzip"})
    assert session.status_code == 200 and "set-cookie" in session.headers
    for response in [session, client.get("/api/state", headers={"Accept-Encoding": "gzip"}),
                     client.get("/api/export", headers={"Accept-Encoding": "gzip"})]:
        assert "content-encoding" not in response.headers
        assert response.headers["cache-control"] == "no-store"
    assert client.get("/settings", headers={"Accept-Encoding": "gzip"}).content == client.get("/", headers={"Accept-Encoding": "identity"}).content
