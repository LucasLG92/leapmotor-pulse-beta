"""Prepare the audited generic Pulse iOS IPA for later local signing.

This tool only writes public runtime configuration into an unsigned IPA. It
does not sign, install, or upload the result.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import stat
import struct
import tempfile
import unicodedata
import urllib.parse
import zipfile


AUTHORIZED_SOURCE_SHA256 = "31cbe14937738616844c4036760717fb677ba83ba42030f4ba6c63d633d7d88c"
DEFAULT_APP_GROUP = "group.com.lucas.pulse"
APP_ID = "com.lucas.pulse"
WIDGET_ID = "com.lucas.pulse.widget"
APP_INFO = "Payload/App.app/Info.plist"
APP_EXECUTABLE = "Payload/App.app/App"
WIDGET_PREFIX = "Payload/App.app/PlugIns/PulseWidget.appex/"
WIDGET_INFO = WIDGET_PREFIX + "Info.plist"
WIDGET_EXECUTABLE = WIDGET_PREFIX + "PulseWidget"

CONFIG_KEYS = (
    "pulse.apiOrigin",
    "pulse.issuer",
    "pulse.authorizationEndpoint",
    "pulse.tokenEndpoint",
    "pulse.clientId",
    "pulse.audience",
)
APP_INFO_FIELDS = (
    "PulseAPIOrigin",
    "PulseOIDCIssuer",
    "PulseAuthorizationEndpoint",
    "PulseTokenEndpoint",
    "PulseClientID",
    "PulseAudience",
    "PulseCallback",
)
SIGNING_SUFFIXES = {
    ".cer", ".crt", ".csr", ".der", ".jks", ".key", ".keystore", ".mobileconfig",
    ".mobileprovision", ".p12", ".p7b", ".p7c", ".p8", ".pem", ".pfx",
    ".privatekey", ".provision", ".provisionprofile", ".provisioningprofile",
}

MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_ENTRIES = 512
MAX_ENTRY_BYTES = 64 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
MAX_COMPRESSION_RATIO = 1000
MAX_CONFIG_BYTES = 32 * 1024
MAX_PUBLIC_VALUE_UTF16 = 512
RFC3986_PATH_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~!$&'()*+,;=:@/"
)
ARM64 = 0x0100000C
LC_CODE_SIGNATURE = 0x1D
LC_BUILD_VERSION = 0x32
LC_VERSION_MIN_IPHONEOS = 0x25


class PackageError(ValueError):
    """A fixed, non-sensitive error suitable for displaying in a terminal."""


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _has_whitespace_or_control(value: str) -> bool:
    return any(character.isspace() or unicodedata.category(character) in {"Cc", "Cf"} for character in value)


def _read_regular_file(path: Path, max_bytes: int, error: str) -> bytes:
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > max_bytes:
                raise PackageError(error)
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(descriptor, min(1024 * 1024, max_bytes + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > max_bytes:
                    raise PackageError(error)
            if total != metadata.st_size:
                raise PackageError(error)
            return b"".join(chunks)
        finally:
            os.close(descriptor)
    except PackageError:
        raise
    except OSError:
        raise PackageError(error) from None


def _validate_public_value(value: str) -> bool:
    return (
        bool(value)
        and _utf16_length(value) <= MAX_PUBLIC_VALUE_UTF16
        and not _has_whitespace_or_control(value)
    )


def _strict_https(value: str, *, origin_only: bool = False) -> urllib.parse.SplitResult:
    if (
        not isinstance(value, str)
        or not value
        or _utf16_length(value) > MAX_PUBLIC_VALUE_UTF16
        or _has_whitespace_or_control(value)
        or "\\" in value
        or "%" in value
        or "?" in value
        or "#" in value
    ):
        raise PackageError("Public configuration contains an invalid HTTPS URL.")
    try:
        parsed = urllib.parse.urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        raise PackageError("Public configuration contains an invalid HTTPS URL.") from None
    if (
        parsed.scheme.lower() != "https"
        or not parsed.netloc
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.netloc.isascii()
        or any(character.isspace() or unicodedata.category(character) in {"Cc", "Cf"} for character in parsed.netloc)
        or "%" in parsed.netloc
        or (port is not None and not 1 <= port <= 65535)
        or not host.isascii()
    ):
        raise PackageError("Public configuration contains an invalid HTTPS URL.")

    authority = parsed.netloc
    if authority.startswith("["):
        closing = authority.find("]")
        if closing < 0:
            raise PackageError("Public configuration contains an invalid HTTPS URL.")
        suffix = authority[closing + 1 :]
        if suffix and (not suffix.startswith(":") or not suffix[1:].isdigit()):
            raise PackageError("Public configuration contains an invalid HTTPS URL.")
        if suffix == ":":
            raise PackageError("Public configuration contains an invalid HTTPS URL.")
    else:
        if authority.count(":") > 1:
            raise PackageError("Public configuration contains an invalid HTTPS URL.")
        if ":" in authority and not authority.rsplit(":", 1)[1].isdigit():
            raise PackageError("Public configuration contains an invalid HTTPS URL.")

    path = parsed.path
    if (
        "//" in path
        or any(part in {".", ".."} for part in path.split("/"))
        or not path.isascii()
        or any(character not in RFC3986_PATH_CHARS for character in path)
        or (origin_only and path != "")
    ):
        raise PackageError("Public configuration contains an invalid HTTPS URL.")
    return parsed


def _authority_key(parsed: urllib.parse.SplitResult) -> tuple[str, int]:
    try:
        port = parsed.port or 443
    except ValueError:
        raise PackageError("Public configuration contains an invalid HTTPS URL.") from None
    return (parsed.hostname.lower(), port)  # type: ignore[union-attr]


def _normalized_path(path: str) -> str:
    return path.strip("/")


def _validate_runtime_config(values: dict[str, str]) -> dict[str, str]:
    _strict_https(values["pulse.apiOrigin"], origin_only=True)
    issuer = _strict_https(values["pulse.issuer"])
    authorization = _strict_https(values["pulse.authorizationEndpoint"])
    token = _strict_https(values["pulse.tokenEndpoint"])
    for key in ("pulse.clientId", "pulse.audience"):
        if not _validate_public_value(values[key]):
            raise PackageError("Public configuration contains an invalid value.")

    if _authority_key(issuer) != _authority_key(authorization) or _authority_key(issuer) != _authority_key(token):
        raise PackageError("OAuth endpoints must use the issuer authority.")

    issuer_path = _normalized_path(issuer.path)
    prefix = issuer_path + "/" if issuer_path else ""
    for endpoint in (authorization, token):
        endpoint_path = _normalized_path(endpoint.path)
        if not endpoint_path or endpoint_path == issuer_path or not endpoint_path.startswith(prefix):
            raise PackageError("OAuth endpoints must be below the issuer path.")

    api_origin = values["pulse.apiOrigin"]
    callback = api_origin + "/native/oauth/callback"
    _strict_https(callback)
    return {
        "PulseAPIOrigin": api_origin,
        "PulseOIDCIssuer": values["pulse.issuer"],
        "PulseAuthorizationEndpoint": values["pulse.authorizationEndpoint"],
        "PulseTokenEndpoint": values["pulse.tokenEndpoint"],
        "PulseClientID": values["pulse.clientId"],
        "PulseAudience": values["pulse.audience"],
        "PulseCallback": callback,
    }


def read_public_config(path: Path) -> dict[str, str]:
    raw = _read_regular_file(path, MAX_CONFIG_BYTES, "Public configuration file is invalid.")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise PackageError("Public configuration file is invalid.") from None

    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "!")):
            continue
        if "=" not in line:
            raise PackageError("Public configuration file is invalid.")
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if key not in CONFIG_KEYS or key in values or not value:
            raise PackageError("Public configuration must contain the six public keys only.")
        values[key] = value
    if set(values) != set(CONFIG_KEYS):
        raise PackageError("Public configuration must contain the six public keys only.")
    return _validate_runtime_config(values)


def _validate_app_group(value: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) > 255
        or not re.fullmatch(r"group\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)*", value)
    ):
        raise PackageError("App Group identifier is invalid.")
    return value


def _safe_archive_name(info: zipfile.ZipInfo) -> str:
    name = info.orig_filename
    if (
        not name
        or name != info.filename
        or "\x00" in name
        or "\\" in name
        or not unicodedata.is_normalized("NFC", name)
        or name.startswith("/")
        or re.match(r"^[A-Za-z]:", name)
    ):
        raise PackageError("IPA contains an unsafe archive path.")
    path = PurePosixPath(name)
    parts = name.split("/")
    meaningful_parts = parts[:-1] if info.is_dir() and parts[-1] == "" else parts
    if (
        path.is_absolute()
        or not meaningful_parts
        or any(part in {"", ".", ".."} for part in meaningful_parts)
        or not name.startswith("Payload/App.app/")
    ):
        raise PackageError("IPA contains an unsafe archive path.")
    if any(part in {"_CodeSignature", "SC_Info", "embedded.mobileprovision"} for part in parts):
        raise PackageError("Signed apps and signing material are not accepted.")
    if any(PurePosixPath(part).suffix.lower() in SIGNING_SUFFIXES for part in parts):
        raise PackageError("Signed apps and signing material are not accepted.")
    mode = info.external_attr >> 16
    kind = stat.S_IFMT(mode)
    if kind == stat.S_IFLNK or (kind not in {0, stat.S_IFREG, stat.S_IFDIR}):
        raise PackageError("IPA links and special files are not accepted.")
    if info.flag_bits & 0x1:
        raise PackageError("Encrypted IPA entries are not accepted.")
    return name


def _read_archive(raw: bytes) -> list[tuple[zipfile.ZipInfo, bytes]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw), "r")
    except (OSError, zipfile.BadZipFile, ValueError):
        raise PackageError("Input is not a valid IPA archive.") from None

    with archive:
        try:
            infos = archive.infolist()
        except Exception:
            raise PackageError("Input is not a valid IPA archive.") from None
        if not infos or len(infos) > MAX_ENTRIES:
            raise PackageError("IPA entry limit exceeded.")
        names: set[str] = set()
        folded_names: set[str] = set()
        total_uncompressed = 0
        members: list[tuple[zipfile.ZipInfo, bytes]] = []
        for info in infos:
            name = _safe_archive_name(info)
            normalized = name.rstrip("/")
            folded = normalized.casefold()
            if name in names or folded in folded_names:
                raise PackageError("IPA contains duplicate archive paths.")
            names.add(name)
            folded_names.add(folded)
            if info.file_size < 0 or info.file_size > MAX_ENTRY_BYTES:
                raise PackageError("IPA entry size limit exceeded.")
            if info.compress_size < 0 or info.compress_size > MAX_ARCHIVE_BYTES:
                raise PackageError("IPA archive size limit exceeded.")
            total_uncompressed += info.file_size
            if total_uncompressed > MAX_TOTAL_UNCOMPRESSED_BYTES:
                raise PackageError("IPA total size limit exceeded.")
            if info.file_size and (
                not info.compress_size
                or info.file_size > info.compress_size * MAX_COMPRESSION_RATIO
            ):
                raise PackageError("IPA compression ratio limit exceeded.")

            try:
                with archive.open(info, "r") as stream:
                    chunks: list[bytes] = []
                    actual_size = 0
                    while True:
                        chunk = stream.read(1024 * 1024)
                        if not chunk:
                            break
                        actual_size += len(chunk)
                        if actual_size > MAX_ENTRY_BYTES:
                            raise PackageError("IPA entry size limit exceeded.")
                        chunks.append(chunk)
                    data = b"".join(chunks)
            except PackageError:
                raise
            except Exception:
                raise PackageError("IPA contains an invalid archive entry.") from None
            if actual_size != info.file_size:
                raise PackageError("IPA entry size does not match its metadata.")
            members.append((info, data))
        return members


def _parse_macho_slice(data: bytes, offset: int, size: int) -> tuple[int, list[int], bool]:
    if offset < 0 or size < 32 or offset + size > len(data):
        raise PackageError("Native executable is not a valid iPhone Mach-O binary.")
    slice_data = data[offset : offset + size]
    magic = slice_data[:4]
    if magic == b"\xcf\xfa\xed\xfe":
        endian = "<"
    elif magic == b"\xfe\xed\xfa\xcf":
        endian = ">"
    else:
        raise PackageError("Native executable is not a 64-bit Mach-O binary.")
    try:
        _, cpu_type, _, file_type, command_count, command_bytes, _, _ = struct.unpack_from(
            endian + "IiiIIIII", slice_data, 0
        )
    except struct.error:
        raise PackageError("Native executable is not a valid iPhone Mach-O binary.") from None
    command_end = 32 + command_bytes
    if cpu_type != ARM64 or file_type != 2 or command_count <= 0 or command_count > 4096 or command_end > size:
        raise PackageError("Native executable must be an arm64 iPhone app binary.")

    platforms: list[int] = []
    has_signature = False
    cursor = 32
    for _ in range(command_count):
        if cursor + 8 > command_end:
            raise PackageError("Native executable has invalid load commands.")
        command, command_size = struct.unpack_from(endian + "II", slice_data, cursor)
        if command_size < 8 or cursor + command_size > command_end:
            raise PackageError("Native executable has invalid load commands.")
        if command == LC_CODE_SIGNATURE:
            has_signature = True
        elif command == LC_BUILD_VERSION:
            if command_size < 24:
                raise PackageError("Native executable has invalid platform metadata.")
            platforms.append(struct.unpack_from(endian + "I", slice_data, cursor + 8)[0])
        elif command == LC_VERSION_MIN_IPHONEOS:
            platforms.append(2)
        cursor += command_size
    if cursor != command_end or platforms != [2]:
        raise PackageError("Native executable must target iPhoneOS.")
    return cpu_type, platforms, has_signature


def _validate_macho(data: bytes) -> None:
    if len(data) < 32:
        raise PackageError("Native executable is not a valid iPhone Mach-O binary.")
    magic = data[:4]
    slices: list[tuple[int, int]] = []
    if magic in {b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf"}:
        slices.append((0, len(data)))
    elif magic in {b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"}:
        endian = ">" if magic in {b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"} else "<"
        fat64 = magic in {b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"}
        try:
            _, count = struct.unpack_from(endian + "II", data, 0)
        except struct.error:
            raise PackageError("Native executable is not a valid universal Mach-O binary.") from None
        arch_size = 32 if fat64 else 20
        if count <= 0 or count > 64 or 8 + count * arch_size > len(data):
            raise PackageError("Native executable is not a valid universal Mach-O binary.")
        for index in range(count):
            cursor = 8 + index * arch_size
            try:
                if fat64:
                    cpu_type, _, slice_offset, slice_size, _, _ = struct.unpack_from(endian + "iiQQII", data, cursor)
                else:
                    cpu_type, _, slice_offset, slice_size, _ = struct.unpack_from(endian + "iiIII", data, cursor)
            except struct.error:
                raise PackageError("Native executable is not a valid universal Mach-O binary.") from None
            if cpu_type != ARM64:
                raise PackageError("Native executable must contain only arm64 iPhone slices.")
            slices.append((slice_offset, slice_size))
    else:
        raise PackageError("Native executable is not a Mach-O binary.")

    if not slices:
        raise PackageError("Native executable has no arm64 slice.")
    for offset, size in slices:
        cpu_type, _, has_signature = _parse_macho_slice(data, offset, size)
        if cpu_type != ARM64:
            raise PackageError("Native executable must contain arm64 iPhone slices.")
        if has_signature:
            raise PackageError("Input IPA is already signed.")


def _load_plist(data: bytes) -> dict:
    try:
        value = plistlib.loads(data)
    except (ValueError, plistlib.InvalidFileException):
        raise PackageError("IPA bundle metadata is invalid.") from None
    if not isinstance(value, dict):
        raise PackageError("IPA bundle metadata is invalid.")
    return value


def _validate_bundle(info: dict, identifier: str, executable: str) -> tuple[str, str]:
    if (
        info.get("CFBundleIdentifier") != identifier
        or info.get("CFBundleExecutable") != executable
        or info.get("CFBundleSupportedPlatforms") != ["iPhoneOS"]
    ):
        raise PackageError("IPA bundle identity or iPhone platform is unexpected.")
    version = info.get("CFBundleShortVersionString")
    build = info.get("CFBundleVersion")
    if (
        not isinstance(version, str)
        or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,2}", version)
        or not isinstance(build, str)
        or not re.fullmatch(r"[1-9][0-9]*", build)
    ):
        raise PackageError("IPA bundle version metadata is invalid.")
    return version, build


def _validate_and_configure(members: list[tuple[zipfile.ZipInfo, bytes]], app_group: str, config: dict[str, str]) -> tuple[list[tuple[zipfile.ZipInfo, bytes]], dict]:
    files = {info.filename: data for info, data in members if not info.is_dir()}
    if not {APP_INFO, APP_EXECUTABLE, WIDGET_INFO, WIDGET_EXECUTABLE}.issubset(files):
        raise PackageError("IPA is missing an expected Pulse app or widget bundle file.")

    app_info = _load_plist(files[APP_INFO])
    widget_info = _load_plist(files[WIDGET_INFO])
    app_version = _validate_bundle(app_info, APP_ID, "App")
    widget_version = _validate_bundle(widget_info, WIDGET_ID, "PulseWidget")
    if app_version != widget_version:
        raise PackageError("App and widget versions must match.")
    if any(app_info.get(field) != "" for field in APP_INFO_FIELDS):
        raise PackageError("Only the audited generic, unconfigured IPA is accepted.")
    if app_info.get("PulseAppGroup") != DEFAULT_APP_GROUP or widget_info.get("PulseAppGroup") != DEFAULT_APP_GROUP:
        raise PackageError("Input IPA does not have the audited default App Group.")

    _validate_macho(files[APP_EXECUTABLE])
    _validate_macho(files[WIDGET_EXECUTABLE])

    app_info.update(config)
    app_info["PulseAppGroup"] = app_group
    widget_info["PulseAppGroup"] = app_group
    updated_app_plist = plistlib.dumps(app_info, fmt=plistlib.FMT_XML, sort_keys=False)
    updated_widget_plist = plistlib.dumps(widget_info, fmt=plistlib.FMT_XML, sort_keys=False)
    configured = []
    for info, data in members:
        if info.filename == APP_INFO:
            data = updated_app_plist
        elif info.filename == WIDGET_INFO:
            data = updated_widget_plist
        configured.append((info, data))

    return configured, {
        "bundle_id": APP_ID,
        "widget_bundle_id": WIDGET_ID,
        "version": app_version[0],
        "build_number": app_version[1],
    }


def _build_ipa(members: list[tuple[zipfile.ZipInfo, bytes]], archive_comment: bytes) -> bytes:
    output = io.BytesIO()
    try:
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zipped:
            zipped.comment = archive_comment
            for original_info, data in members:
                info = copy.copy(original_info)
                zipped.writestr(info, data)
    except Exception:
        raise PackageError("Configured IPA could not be assembled.") from None
    result = output.getvalue()
    if len(result) > MAX_ARCHIVE_BYTES:
        raise PackageError("Configured IPA archive size limit exceeded.")
    try:
        with zipfile.ZipFile(io.BytesIO(result), "r") as check:
            if check.testzip() is not None:
                raise PackageError("Configured IPA integrity check failed.")
    except (OSError, zipfile.BadZipFile):
        raise PackageError("Configured IPA integrity check failed.") from None
    return result


def _output_paths(output: Path) -> tuple[Path, Path]:
    output = Path(os.path.abspath(output))
    return output, Path(str(output) + ".receipt.json")


def _ensure_new_destinations(output: Path, receipt_path: Path, input_path: Path, config_path: Path) -> None:
    output_parent = output.parent
    if not output_parent.is_dir():
        raise PackageError("Output directory must already exist.")
    resolved_output = output.resolve(strict=False)
    resolved_receipt = receipt_path.resolve(strict=False)
    if (
        output.exists()
        or output.is_symlink()
        or receipt_path.exists()
        or receipt_path.is_symlink()
    ):
        raise PackageError("Output IPA or receipt already exists; preserve previous files.")
    if (
        resolved_output == input_path.resolve(strict=False)
        or resolved_output == config_path.resolve(strict=False)
        or resolved_receipt in {input_path.resolve(strict=False), config_path.resolve(strict=False)}
        or resolved_output == resolved_receipt
    ):
        raise PackageError("Input, configuration, output, and receipt paths must be separate.")


def _write_private_file(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(data):
            written = os.write(descriptor, data[offset:])
            if written <= 0:
                raise OSError("short write")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_pair(staged_ipa: Path, staged_receipt: Path, output: Path, receipt_path: Path) -> None:
    published: list[Path] = []
    try:
        # Reserve the receipt path first. Each link is atomic and refuses to
        # replace an existing destination; a crash in the gap can leave only
        # the private receipt, never a partial final IPA.
        os.link(staged_receipt, receipt_path, follow_symlinks=False)
        published.append(receipt_path)
        _fsync_directory(output.parent)
        os.link(staged_ipa, output, follow_symlinks=False)
        published.append(output)
        _fsync_directory(output.parent)
    except OSError:
        for path in reversed(published):
            try:
                path.unlink()
            except OSError:
                pass
        try:
            _fsync_directory(output.parent)
        except OSError:
            pass
        raise PackageError("Output IPA and private receipt could not be published.") from None


def configure_ipa(
    input_ipa: Path,
    expected_sha256: str,
    public_config: Path,
    output: Path,
    app_group: str = DEFAULT_APP_GROUP,
) -> dict:
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256 or ""):
        raise PackageError("An expected SHA-256 digest is required.")
    expected_sha256 = expected_sha256.lower()
    if expected_sha256 != AUTHORIZED_SOURCE_SHA256:
        raise PackageError("Expected SHA-256 does not match the audited generic IPA.")
    app_group = _validate_app_group(app_group)
    input_ipa = Path(input_ipa)
    public_config = Path(public_config)
    output, receipt_path = _output_paths(Path(output))
    _ensure_new_destinations(output, receipt_path, input_ipa, public_config)

    raw_ipa = _read_regular_file(input_ipa, MAX_ARCHIVE_BYTES, "Input IPA file is invalid or too large.")
    actual_sha256 = hashlib.sha256(raw_ipa).hexdigest()
    if actual_sha256 != expected_sha256:
        raise PackageError("Input IPA SHA-256 does not match the required digest.")
    config = read_public_config(public_config)

    try:
        with zipfile.ZipFile(io.BytesIO(raw_ipa), "r") as source_archive:
            archive_comment = source_archive.comment
    except (OSError, zipfile.BadZipFile):
        raise PackageError("Input is not a valid IPA archive.") from None
    members = _read_archive(raw_ipa)
    configured_members, metadata = _validate_and_configure(members, app_group, config)
    configured_ipa = _build_ipa(configured_members, archive_comment)
    ipa_digest = hashlib.sha256(configured_ipa).hexdigest()
    receipt = {
        "schema_version": 1,
        "classification": "locally_configured_unsigned_ios_ipa",
        "source_ipa_sha256": actual_sha256,
        "ipa_sha256": ipa_digest,
        "ipa_bytes": len(configured_ipa),
        "archive_entries": len(configured_members),
        **metadata,
        "app_group": app_group,
        "configured": True,
        "signed": False,
        "installed": False,
        "uploaded": False,
        "requires_local_signing_and_profile_validation": True,
        "app_group_entitlement_and_profile_match_verified": False,
        "device_installation_verified": False,
        "widget_runtime_verified": False,
    }
    receipt_bytes = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8")
    try:
        with tempfile.TemporaryDirectory(prefix=".pulse-ios-configure-", dir=output.parent) as temp_name:
            stage = Path(temp_name)
            os.chmod(stage, 0o700)
            staged_ipa = stage / "configured.ipa"
            staged_receipt = stage / "receipt.json"
            _write_private_file(staged_ipa, configured_ipa)
            _write_private_file(staged_receipt, receipt_bytes)
            _publish_pair(staged_ipa, staged_receipt, output, receipt_path)
    except PackageError:
        raise
    except OSError:
        raise PackageError("Configured IPA and private receipt could not be staged.") from None
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_ipa", type=Path, help="Audited generic unsigned IPA")
    parser.add_argument("--expected-sha256", required=True, help="Required audited source IPA SHA-256")
    parser.add_argument("--public-config", required=True, type=Path, help="Public six-key native config file")
    parser.add_argument("--output", required=True, type=Path, help="New output IPA path")
    parser.add_argument("--app-group", default=DEFAULT_APP_GROUP, help="App Group to write in both bundle Info.plists")
    args = parser.parse_args()
    try:
        configure_ipa(args.input_ipa, args.expected_sha256, args.public_config, args.output, args.app_group)
    except PackageError as error:
        raise SystemExit(str(error)) from None
    print("Configured unsigned IPA and private receipt created; signing, entitlement/profile match, installation, and widget acceptance remain pending.")


if __name__ == "__main__":
    main()
