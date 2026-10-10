"""Bounds, permissions and no-follow behavior for navigation caches."""

import hashlib
import json
import os
import re
import stat
import struct

import pytest

from pulse.navigation_cache import (TTLCache, TileCacheError, TileDiskCache, TileEntry,
                                    _TILE_MAGIC)


def _key(value):
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def test_ttl_cache_is_bounded_expires_entries_and_returns_copies():
    now = [10.0]
    cache = TTLCache(2, 5, clock=lambda: now[0])
    original = {"places": ["Madrid"]}
    cache.set("first", original)
    original["places"].append("Mutado")
    returned = cache.get("first")
    assert returned == {"places": ["Madrid"]}
    returned["places"].append("Otra mutación")
    assert cache.get("first") == {"places": ["Madrid"]}

    cache.set("second", {"value": 2})
    cache.set("third", {"value": 3})
    assert len(cache) == 2
    assert cache.get("first") is None
    now[0] = 14.9
    assert cache.get("second") == {"value": 2}
    now[0] = 15.0
    assert cache.get("second") is None
    assert len(cache) == 0


def test_disk_cache_is_lazy_private_and_uses_hashed_regular_files(tmp_path):
    cache_dir = tmp_path / "map-cache"
    cache = TileDiskCache(cache_dir)
    assert not cache_dir.exists()

    key = _key("4/3/5")
    entry = TileEntry(b"synthetic png", 500.0, '"v1"', None,
                      "max-age=60", "Wed, 21 Oct 2030 07:28:00 GMT")
    assert cache.store(key, entry, now=100.0)
    directory = cache_dir / "navigation-tiles"
    files = list(directory.iterdir())
    assert len(files) == 1
    assert files[0].name == key + ".tile"
    assert re.fullmatch(r"[0-9a-f]{64}\.tile", files[0].name)
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o600
    assert cache.load(key) == entry


def test_disk_cache_reads_legacy_metadata_without_policy_fields(tmp_path):
    cache = TileDiskCache(tmp_path / "map-cache")
    key = _key("4/3/5")
    directory = tmp_path / "map-cache" / "navigation-tiles"
    directory.mkdir(parents=True, mode=0o700)
    metadata = json.dumps({
        "expires_at": 500.0, "etag": '"legacy"', "last_modified": None,
    }, separators=(",", ":")).encode()
    old_entry = _TILE_MAGIC + struct.pack(">I", len(metadata)) + metadata + b"legacy tile bytes"
    path = directory / (key + ".tile")
    path.write_bytes(old_entry)
    os.chmod(path, 0o600)

    assert cache.load(key) == TileEntry(b"legacy tile bytes", 500.0, '"legacy"', None)


def test_disk_cache_refuses_fresh_eviction_when_file_quota_is_full(tmp_path, monkeypatch):
    monkeypatch.setattr("pulse.navigation_cache.TILE_CACHE_MAX_FILES", 1)
    cache = TileDiskCache(tmp_path / "map-cache")
    first_key, second_key = _key("0/0/0"), _key("1/0/0")
    first = TileEntry(b"first", 500.0)
    second = TileEntry(b"second", 500.0)

    assert cache.store(first_key, first, now=100.0)
    assert cache.store(second_key, second, now=100.0) is False
    assert cache.load(first_key) == first
    assert cache.load(second_key) is None


def test_disk_cache_removes_expired_entry_before_admitting_new_tile(tmp_path, monkeypatch):
    monkeypatch.setattr("pulse.navigation_cache.TILE_CACHE_MAX_FILES", 1)
    cache = TileDiskCache(tmp_path / "map-cache")
    first_key, second_key = _key("0/0/0"), _key("1/0/0")
    assert cache.store(first_key, TileEntry(b"expired", 100.0), now=50.0)
    assert cache.store(second_key, TileEntry(b"fresh", 300.0), now=200.0)
    assert cache.load(first_key) is None
    assert cache.load(second_key) == TileEntry(b"fresh", 300.0)


def test_disk_cache_rejects_traversal_and_symlinked_directories(tmp_path):
    with pytest.raises(TileCacheError):
        TileDiskCache(tmp_path / ".." / "outside")

    real = tmp_path / "real-cache"
    real.mkdir()
    alias = tmp_path / "cache-alias"
    alias.symlink_to(real, target_is_directory=True)
    cache = TileDiskCache(alias)
    with pytest.raises(OSError):
        cache.load(_key("0/0/0"))


def test_disk_cache_does_not_follow_tile_file_symlinks(tmp_path):
    cache = TileDiskCache(tmp_path / "map-cache")
    key = _key("2/1/1")
    directory = tmp_path / "map-cache" / "navigation-tiles"
    directory.mkdir(parents=True, mode=0o700)
    outside = tmp_path / "outside.tile"
    outside.write_bytes(b"outside")
    (directory / (key + ".tile")).symlink_to(outside)

    assert cache.load(key) is None
    assert outside.read_bytes() == b"outside"


def test_disk_cache_validates_digest_and_bounded_body(tmp_path):
    cache = TileDiskCache(tmp_path / "map-cache")
    with pytest.raises(TileCacheError):
        cache.load("../../outside")
    with pytest.raises(TileCacheError):
        cache.store(_key("0/0/0"), TileEntry(b"x" * (128 * 1024 + 1), 200.0), now=100.0)


def test_cache_cannot_persist_validators_that_break_http_ascii_encoding(tmp_path):
    cache = TileDiskCache(tmp_path / "map-cache")
    with pytest.raises(TileCacheError):
        cache.store(_key("0/0/0"), TileEntry(b"tile", 200.0, etag='"é"'), now=100.0)
