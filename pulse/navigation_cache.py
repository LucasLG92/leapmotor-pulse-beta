"""Bounded RAM caches and a private, quota-limited OSM tile cache."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import copy
import errno
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import struct
import time
from typing import Any


TILE_MAX_BYTES = 128 * 1024
TILE_CACHE_MAX_BYTES = 64 * 1024 * 1024
TILE_CACHE_MAX_FILES = 1024
_TILE_MAGIC = b"PNAV1\n"
_TILE_NAME_RE = re.compile(r"[0-9a-f]{64}\.tile\Z")
_TILE_KEY_RE = re.compile(r"[0-9a-f]{64}\Z")
_MAX_METADATA_BYTES = 4096
_MAX_TILE_FILE_BYTES = TILE_MAX_BYTES + _MAX_METADATA_BYTES + len(_TILE_MAGIC) + 4


class TileCacheError(OSError):
    """Local cache failure without exposing a path or filesystem detail."""


class TTLCache:
    """Small FIFO/LRU TTL cache for ephemeral public search results."""

    def __init__(self, max_entries: int, ttl_seconds: float, *, clock=None) -> None:
        if type(max_entries) is not int or max_entries < 1 or ttl_seconds <= 0:
            raise ValueError("Los límites de caché no son válidos.")
        self.max_entries = max_entries
        self.ttl_seconds = float(ttl_seconds)
        self._clock = clock or time.monotonic
        self._items: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def get(self, key: str) -> Any | None:
        item = self._items.get(key)
        if item is None:
            return None
        expires_at, value = item
        if expires_at <= self._clock():
            del self._items[key]
            return None
        self._items.move_to_end(key)
        return copy.deepcopy(value)

    def set(self, key: str, value: Any) -> None:
        now = self._clock()
        self._purge_expired(now)
        self._items.pop(key, None)
        while len(self._items) >= self.max_entries:
            self._items.popitem(last=False)
        self._items[key] = (now + self.ttl_seconds, copy.deepcopy(value))

    def clear(self) -> None:
        self._items.clear()

    def __len__(self) -> int:
        self._purge_expired(self._clock())
        return len(self._items)

    def _purge_expired(self, now: float) -> None:
        for key, (expires_at, _value) in tuple(self._items.items()):
            if expires_at <= now:
                del self._items[key]


@dataclass(frozen=True)
class TileEntry:
    data: bytes
    expires_at: float
    etag: str | None = None
    last_modified: str | None = None
    cache_control: str | None = None
    expires: str | None = None


class TileDiskCache:
    """Atomic no-follow files under ``navigation-tiles`` with no fresh eviction."""

    def __init__(self, cache_dir: str | os.PathLike[str]) -> None:
        raw_path = Path(cache_dir)
        if ".." in raw_path.parts:
            raise TileCacheError(errno.EINVAL, "invalid cache directory")
        self.directory = (raw_path if raw_path.is_absolute() else Path.cwd() / raw_path) / "navigation-tiles"

    def load(self, key: str) -> TileEntry | None:
        name = self._name(key)
        directory_fd = self._open_directory(create=True)
        try:
            try:
                file_fd = os.open(name, self._file_flags(os.O_RDONLY), dir_fd=directory_fd)
            except FileNotFoundError:
                return None
            except OSError as error:
                if error.errno in {errno.ELOOP, errno.EMLINK}:
                    return None
                raise
            try:
                metadata = os.fstat(file_fd)
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_TILE_FILE_BYTES:
                    return None
                chunks: list[bytes] = []
                remaining = metadata.st_size
                while remaining:
                    chunk = os.read(file_fd, min(remaining, 16 * 1024))
                    if not chunk:
                        return None
                    chunks.append(chunk)
                    remaining -= len(chunk)
                return self._decode(b"".join(chunks))
            finally:
                os.close(file_fd)
        finally:
            os.close(directory_fd)

    def store(self, key: str, entry: TileEntry, *, now: float | None = None) -> bool:
        name = self._name(key)
        timestamp = time.time() if now is None else float(now)
        if (not isinstance(entry, TileEntry) or not isinstance(entry.data, bytes)
                or len(entry.data) > TILE_MAX_BYTES or not math.isfinite(entry.expires_at)
                or not self._safe_validator(entry.etag) or not self._safe_validator(entry.last_modified)
                or not self._safe_validator(entry.cache_control) or not self._safe_validator(entry.expires)):
            raise TileCacheError(errno.EINVAL, "invalid cache entry")
        encoded = self._encode(entry)
        if len(encoded) > TILE_CACHE_MAX_BYTES:
            return False

        directory_fd = self._open_directory(create=True)
        try:
            usage, count, existing_size = self._usage_and_clean_expired(directory_fd, timestamp, name)
            projected_bytes = usage - existing_size + len(encoded)
            projected_files = count + (0 if existing_size else 1)
            if projected_bytes > TILE_CACHE_MAX_BYTES or projected_files > TILE_CACHE_MAX_FILES:
                return False
            temporary = f".tmp-{secrets.token_hex(16)}"
            file_fd = os.open(temporary, self._file_flags(os.O_WRONLY | os.O_CREAT | os.O_EXCL),
                              0o600, dir_fd=directory_fd)
            try:
                os.fchmod(file_fd, 0o600)
                view = memoryview(encoded)
                while view:
                    written = os.write(file_fd, view)
                    if written <= 0:
                        raise TileCacheError(errno.EIO, "short cache write")
                    view = view[written:]
                os.fsync(file_fd)
            except BaseException:
                try:
                    os.unlink(temporary, dir_fd=directory_fd)
                except OSError:
                    pass
                raise
            finally:
                os.close(file_fd)
            try:
                os.replace(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
                os.fsync(directory_fd)
            except BaseException:
                try:
                    os.unlink(temporary, dir_fd=directory_fd)
                except OSError:
                    pass
                raise
            return True
        finally:
            os.close(directory_fd)

    def remove(self, key: str) -> None:
        name = self._name(key)
        directory_fd = self._open_directory(create=True)
        try:
            try:
                metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                if stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                    os.unlink(name, dir_fd=directory_fd)
            except FileNotFoundError:
                return
        finally:
            os.close(directory_fd)

    def _open_directory(self, *, create: bool) -> int:
        if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
            raise TileCacheError(errno.ENOTSUP, "safe directory flags unavailable")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        directory_fd = os.open(self.directory.anchor or "/", flags)
        try:
            for component in self.directory.parts[1:]:
                try:
                    next_fd = os.open(component, flags, dir_fd=directory_fd)
                except FileNotFoundError:
                    if not create:
                        raise
                    try:
                        os.mkdir(component, 0o700, dir_fd=directory_fd)
                    except FileExistsError:
                        pass
                    next_fd = os.open(component, flags, dir_fd=directory_fd)
                os.close(directory_fd)
                directory_fd = next_fd
            os.fchmod(directory_fd, 0o700)
            return directory_fd
        except BaseException:
            os.close(directory_fd)
            raise

    @staticmethod
    def _file_flags(flags: int) -> int:
        return flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)

    @staticmethod
    def _name(key: str) -> str:
        if not isinstance(key, str) or not _TILE_KEY_RE.fullmatch(key):
            raise TileCacheError(errno.EINVAL, "invalid tile key")
        return key + ".tile"

    @staticmethod
    def _safe_validator(value: str | None) -> bool:
        return value is None or (isinstance(value, str) and len(value) <= 512 and value.isascii()
                                 and not any(ord(char) < 32 or ord(char) == 127
                                             for char in value))

    @staticmethod
    def _encode(entry: TileEntry) -> bytes:
        metadata = json.dumps(
            {"expires_at": entry.expires_at, "etag": entry.etag, "last_modified": entry.last_modified,
             "cache_control": entry.cache_control, "expires": entry.expires},
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
        if len(metadata) > _MAX_METADATA_BYTES:
            raise TileCacheError(errno.EINVAL, "cache metadata too large")
        return _TILE_MAGIC + struct.pack(">I", len(metadata)) + metadata + entry.data

    @staticmethod
    def _decode(raw: bytes) -> TileEntry | None:
        if not raw.startswith(_TILE_MAGIC) or len(raw) < len(_TILE_MAGIC) + 4:
            return None
        metadata_size = struct.unpack(">I", raw[len(_TILE_MAGIC):len(_TILE_MAGIC) + 4])[0]
        offset = len(_TILE_MAGIC) + 4
        if metadata_size > _MAX_METADATA_BYTES or offset + metadata_size > len(raw):
            return None
        try:
            metadata = json.loads(raw[offset:offset + metadata_size].decode("utf-8"))
            old_fields = {"expires_at", "etag", "last_modified"}
            current_fields = old_fields | {"cache_control", "expires"}
            if (not isinstance(metadata, dict)
                    or frozenset(metadata) not in {frozenset(old_fields), frozenset(current_fields)}):
                return None
            expires_at = metadata["expires_at"]
            etag, last_modified = metadata["etag"], metadata["last_modified"]
            if (type(expires_at) not in {int, float} or not math.isfinite(expires_at)
                    or not TileDiskCache._safe_validator(etag)
                    or not TileDiskCache._safe_validator(last_modified)
                    or not TileDiskCache._safe_validator(metadata.get("cache_control"))
                    or not TileDiskCache._safe_validator(metadata.get("expires"))):
                return None
            data = raw[offset + metadata_size:]
            if len(data) > TILE_MAX_BYTES:
                return None
            return TileEntry(data=data, expires_at=float(expires_at), etag=etag,
                             last_modified=last_modified, cache_control=metadata.get("cache_control"),
                             expires=metadata.get("expires"))
        except (UnicodeDecodeError, ValueError, TypeError, struct.error):
            return None

    def _usage_and_clean_expired(self, directory_fd: int, now: float, replacing: str) -> tuple[int, int, int]:
        total_bytes = 0
        total_files = 0
        existing_size = 0
        for filename in os.listdir(directory_fd):
            if not _TILE_NAME_RE.fullmatch(filename):
                continue
            try:
                metadata = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(metadata.st_mode):
                continue
            tile = self._read_named(directory_fd, filename, metadata.st_size)
            if tile is None or tile.expires_at <= now:
                try:
                    os.unlink(filename, dir_fd=directory_fd)
                except FileNotFoundError:
                    pass
                continue
            total_bytes += metadata.st_size
            total_files += 1
            if filename == replacing:
                existing_size = metadata.st_size
        return total_bytes, total_files, existing_size

    @staticmethod
    def _read_named(directory_fd: int, name: str, size: int) -> TileEntry | None:
        if size > _MAX_TILE_FILE_BYTES:
            return None
        try:
            file_fd = os.open(name, TileDiskCache._file_flags(os.O_RDONLY), dir_fd=directory_fd)
        except (FileNotFoundError, OSError):
            return None
        try:
            chunks = []
            remaining = size
            while remaining:
                chunk = os.read(file_fd, min(remaining, 16 * 1024))
                if not chunk:
                    return None
                chunks.append(chunk)
                remaining -= len(chunk)
            return TileDiskCache._decode(b"".join(chunks))
        finally:
            os.close(file_fd)


__all__ = [
    "TILE_CACHE_MAX_BYTES",
    "TILE_CACHE_MAX_FILES",
    "TILE_MAX_BYTES",
    "TTLCache",
    "TileCacheError",
    "TileDiskCache",
    "TileEntry",
]
