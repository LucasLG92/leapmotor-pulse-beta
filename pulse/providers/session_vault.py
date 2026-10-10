"""Cifrado atómico de una sesión privada con lease exclusivo por directorio."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
import re
import secrets
import stat
import threading

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


MAX_SESSION_BYTES = 128 * 1024
_MAGIC = b"PSV1"
_NONCE_BYTES = 12
_TAG_BYTES = 16
_SESSION_NAME = "session.bin"
_LEASE_NAME = ".session.lock"
_TEMP_PREFIX = ".session.tmp-"
_ERROR_MESSAGE = "La sesión cifrada no está disponible."
_ERROR_CODE = "session_vault_unavailable"
_BINDING_RE = re.compile(r"[\x21-\x7e]{1,128}\Z")


class VaultUnavailable(RuntimeError):
    """Fallo local del vault descrito siempre con el mismo texto y código."""

    code = _ERROR_CODE

    def __init__(self) -> None:
        super().__init__(_ERROR_MESSAGE)


class EncryptedSessionVault:
    """Guarda una sesión cifrada bajo un directorio privado y bloqueado."""

    def __init__(self, directory: Path, key: bytes, binding: str) -> None:
        try:
            path = Path(directory)
        except (TypeError, ValueError, OSError):
            raise VaultUnavailable() from None
        if (not path.is_absolute() or ".." in path.parts
                or type(key) is not bytes or len(key) != 32
                or not isinstance(binding, str) or not _BINDING_RE.fullmatch(binding)):
            raise VaultUnavailable() from None

        # No filesystem calls belong in construction; the first load/save opens
        # the directory and holds its lease until close().
        self._directory = path
        self._key: bytes | None = key
        self._binding = binding.encode("ascii")
        self._directory_fd: int | None = None
        self._lease_fd: int | None = None
        self._closed = False
        self._reserved = False
        self._mutex = threading.RLock()

    def __repr__(self) -> str:
        return "<EncryptedSessionVault>"

    def reserve(self) -> None:
        """Claim this object for one bootstrap without touching the filesystem."""
        with self._mutex:
            if self._closed or self._key is None or self._reserved:
                raise VaultUnavailable() from None
            self._reserved = True

    def load(self) -> bytes | None:
        with self._mutex:
            self._require_open()
            file_fd: int | None = None
            try:
                self._ensure_open()
                assert self._directory_fd is not None
                try:
                    file_fd = os.open(
                        _SESSION_NAME,
                        self._file_flags(os.O_RDONLY),
                        dir_fd=self._directory_fd,
                    )
                except FileNotFoundError:
                    return None

                metadata = os.fstat(file_fd)
                self._validate_session_metadata(metadata)
                encoded = self._read_bounded(file_fd, metadata.st_size)
                self._validate_session_metadata(os.fstat(file_fd))
                return self._decrypt(encoded)
            except VaultUnavailable:
                raise
            except Exception:
                raise VaultUnavailable() from None
            finally:
                self._close_fd_quietly(file_fd)

    def save(self, payload: bytes) -> None:
        with self._mutex:
            self._require_open()
            if type(payload) is not bytes or len(payload) > MAX_SESSION_BYTES:
                raise VaultUnavailable() from None

            temp_name: str | None = None
            temp_fd: int | None = None
            temp_identity: tuple[int, int] | None = None
            try:
                self._ensure_open()
                assert self._directory_fd is not None
                self._validate_existing_session()
                encoded = self._encrypt(payload)

                temp_name = _TEMP_PREFIX + secrets.token_hex(16)
                temp_fd = os.open(
                    temp_name,
                    self._file_flags(os.O_WRONLY | os.O_CREAT | os.O_EXCL),
                    0o600,
                    dir_fd=self._directory_fd,
                )
                metadata = os.fstat(temp_fd)
                temp_identity = (metadata.st_dev, metadata.st_ino)
                os.fchmod(temp_fd, 0o600)
                metadata = os.fstat(temp_fd)
                self._validate_private_file_metadata(metadata, max_size=len(encoded))
                self._write_all(temp_fd, encoded)
                os.fsync(temp_fd)
                os.close(temp_fd)
                temp_fd = None

                # Recheck immediately before replacing the directory entry.
                self._validate_existing_session()
                os.replace(
                    temp_name,
                    _SESSION_NAME,
                    src_dir_fd=self._directory_fd,
                    dst_dir_fd=self._directory_fd,
                )
                temp_name = None
                os.fsync(self._directory_fd)
            except VaultUnavailable:
                self._cleanup_temp(temp_name, temp_fd, temp_identity)
                raise
            except Exception:
                self._cleanup_temp(temp_name, temp_fd, temp_identity)
                raise VaultUnavailable() from None

    def close(self) -> None:
        with self._mutex:
            if self._closed:
                return
            self._closed = True
            self._key = None
            lease_fd, self._lease_fd = self._lease_fd, None
            directory_fd, self._directory_fd = self._directory_fd, None
            # Closing the lease descriptor releases flock even if explicit
            # unlock reports an OS error.
            if lease_fd is not None:
                try:
                    fcntl.flock(lease_fd, fcntl.LOCK_UN)
                except OSError:
                    pass
                self._close_fd_quietly(lease_fd)
            self._close_fd_quietly(directory_fd)

    def _require_open(self) -> None:
        if self._closed or self._key is None:
            raise VaultUnavailable() from None

    def _ensure_open(self) -> None:
        if self._directory_fd is not None and self._lease_fd is not None:
            return

        directory_fd = self._open_directory()
        lease_fd: int | None = None
        try:
            lease_fd = self._open_lease(directory_fd)
            self._directory_fd = directory_fd
            self._lease_fd = lease_fd
        except BaseException:
            self._close_fd_quietly(lease_fd)
            self._close_fd_quietly(directory_fd)
            raise

    def _open_directory(self) -> int:
        if (not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY")
                or not hasattr(os, "O_CLOEXEC") or not hasattr(os, "O_NONBLOCK")):
            raise VaultUnavailable() from None
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        directory_fd = os.open("/", flags)
        parts = self._directory.parts
        if len(parts) == 1:
            try:
                self._validate_directory_metadata(os.fstat(directory_fd))
                return directory_fd
            except BaseException:
                self._close_fd_quietly(directory_fd)
                raise

        try:
            self._validate_ancestor_metadata(os.fstat(directory_fd))
            for component in parts[1:-1]:
                next_fd = os.open(component, flags, dir_fd=directory_fd)
                try:
                    self._validate_ancestor_metadata(os.fstat(next_fd))
                except BaseException:
                    self._close_fd_quietly(next_fd)
                    raise
                self._close_fd_quietly(directory_fd)
                directory_fd = next_fd

            final_component = parts[-1]
            created = False
            try:
                final_fd = os.open(final_component, flags, dir_fd=directory_fd)
            except FileNotFoundError:
                try:
                    os.mkdir(final_component, 0o700, dir_fd=directory_fd)
                    created = True
                except FileExistsError:
                    pass
                final_fd = os.open(final_component, flags, dir_fd=directory_fd)

            try:
                metadata = os.fstat(final_fd)
                if created and metadata.st_uid == os.getuid():
                    # A stricter process umask may have reduced mkdir's mode;
                    # only the directory this call just created may be fixed.
                    os.fchmod(final_fd, 0o700)
                    metadata = os.fstat(final_fd)
                self._validate_directory_metadata(metadata)
                if created:
                    os.fsync(directory_fd)
                return final_fd
            except BaseException:
                self._close_fd_quietly(final_fd)
                raise
        finally:
            self._close_fd_quietly(directory_fd)

    def _open_lease(self, directory_fd: int) -> int:
        flags = self._file_flags(os.O_RDWR)
        created = False
        try:
            lease_fd = os.open(
                _LEASE_NAME,
                flags | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=directory_fd,
            )
            created = True
        except FileExistsError:
            lease_fd = os.open(_LEASE_NAME, flags, dir_fd=directory_fd)

        try:
            if created:
                os.fchmod(lease_fd, 0o600)
            self._validate_private_file_metadata(os.fstat(lease_fd), max_size=None)
            fcntl.flock(lease_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return lease_fd
        except BaseException:
            self._close_fd_quietly(lease_fd)
            raise

    def _validate_existing_session(self) -> None:
        assert self._directory_fd is not None
        try:
            metadata = os.stat(
                _SESSION_NAME,
                dir_fd=self._directory_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return
        self._validate_session_metadata(metadata)

    def _validate_session_metadata(self, metadata: os.stat_result) -> None:
        self._validate_private_file_metadata(
            metadata,
            max_size=len(_MAGIC) + _NONCE_BYTES + MAX_SESSION_BYTES + _TAG_BYTES,
        )

    @staticmethod
    def _validate_directory_metadata(metadata: os.stat_result) -> None:
        if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o700):
            raise VaultUnavailable() from None

    @staticmethod
    def _validate_ancestor_metadata(metadata: os.stat_result) -> None:
        owner_uid = os.getuid()
        sticky = bool(metadata.st_mode & stat.S_ISVTX)
        if (not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid not in {owner_uid, 0}
                or (metadata.st_mode & 0o022 and not sticky)):
            raise VaultUnavailable() from None

    @staticmethod
    def _validate_private_file_metadata(
        metadata: os.stat_result,
        *,
        max_size: int | None,
    ) -> None:
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_nlink != 1
                or (max_size is not None and metadata.st_size > max_size)):
            raise VaultUnavailable() from None

    def _encrypt(self, payload: bytes) -> bytes:
        if self._key is None:
            raise VaultUnavailable() from None
        nonce = secrets.token_bytes(_NONCE_BYTES)
        try:
            ciphertext = AESGCM(self._key).encrypt(nonce, payload, _MAGIC + self._binding)
        except Exception:
            raise VaultUnavailable() from None
        return _MAGIC + nonce + ciphertext

    def _decrypt(self, encoded: bytes) -> bytes:
        minimum_size = len(_MAGIC) + _NONCE_BYTES + _TAG_BYTES
        if (self._key is None or len(encoded) < minimum_size
                or len(encoded) > minimum_size + MAX_SESSION_BYTES
                or not encoded.startswith(_MAGIC)):
            raise VaultUnavailable() from None
        offset = len(_MAGIC)
        nonce = encoded[offset:offset + _NONCE_BYTES]
        ciphertext = encoded[offset + _NONCE_BYTES:]
        try:
            payload = AESGCM(self._key).decrypt(nonce, ciphertext, _MAGIC + self._binding)
        except (InvalidTag, ValueError, TypeError):
            raise VaultUnavailable() from None
        if len(payload) > MAX_SESSION_BYTES:
            raise VaultUnavailable() from None
        return payload

    @staticmethod
    def _read_bounded(file_fd: int, size: int) -> bytes:
        maximum = len(_MAGIC) + _NONCE_BYTES + MAX_SESSION_BYTES + _TAG_BYTES
        if size < 0 or size > maximum:
            raise VaultUnavailable() from None
        chunks: list[bytes] = []
        remaining = size
        while remaining:
            chunk = os.read(file_fd, min(remaining, 16 * 1024))
            if not chunk:
                raise VaultUnavailable() from None
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(file_fd, 1):
            raise VaultUnavailable() from None
        return b"".join(chunks)

    @staticmethod
    def _write_all(file_fd: int, data: bytes) -> None:
        view = memoryview(data)
        while view:
            written = os.write(file_fd, view)
            if written <= 0:
                raise VaultUnavailable() from None
            view = view[written:]

    def _cleanup_temp(
        self,
        temp_name: str | None,
        temp_fd: int | None,
        temp_identity: tuple[int, int] | None,
    ) -> None:
        self._close_fd_quietly(temp_fd)
        if (temp_name is None or temp_identity is None or self._directory_fd is None):
            return
        try:
            named = os.stat(temp_name, dir_fd=self._directory_fd, follow_symlinks=False)
            if (stat.S_ISREG(named.st_mode) and named.st_uid == os.getuid()
                    and named.st_nlink == 1
                    and (named.st_dev, named.st_ino) == temp_identity):
                os.unlink(temp_name, dir_fd=self._directory_fd)
        except OSError:
            pass

    @staticmethod
    def _file_flags(flags: int) -> int:
        return flags | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK

    @staticmethod
    def _close_fd_quietly(file_fd: int | None) -> None:
        if file_fd is not None:
            try:
                os.close(file_fd)
            except OSError:
                pass
