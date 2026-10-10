#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_root"

docker compose config --quiet
docker build --pull --tag leapmotor-pulse:0.1.0 .
docker run --rm --read-only --entrypoint python leapmotor-pulse:0.1.0 -c '
import os
import sqlite3
import gzip
import importlib.util
import shutil
import ssl
from pathlib import Path
import authlib
import cryptography
from cryptography.hazmat.bindings.openssl.binding import Binding
import fastapi
import http_ece
import joserfc

assert os.getuid() == 10001, os.getuid()
assert sqlite3.sqlite_version == "3.53.4", sqlite3.sqlite_version
assert ssl.OPENSSL_VERSION.startswith("OpenSSL 3.0.22 "), ssl.OPENSSL_VERSION
binding = Binding()
embedded_ssl = binding.ffi.string(binding.lib.OpenSSL_version(0)).decode("ascii")
assert cryptography.__version__ == "50.0.2", cryptography.__version__
assert embedded_ssl.startswith("OpenSSL 4.0.3 "), embedded_ssl
assert shutil.which("node") is None
assert importlib.util.find_spec("setuptools") is None
assert importlib.util.find_spec("pip") is None
assert importlib.util.find_spec("ensurepip") is None
dist = Path("/app/apps/web/dist")
assert (dist / "index.html.gz").is_file() and (dist / "sw.js.gz").is_file()
for compressed in dist.rglob("*.gz"):
    original = Path(str(compressed)[:-3])
    assert gzip.decompress(compressed.read_bytes()) == original.read_bytes()
print("runtime image checks passed")
'
