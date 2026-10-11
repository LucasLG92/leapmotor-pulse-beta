#!/usr/bin/env python3
"""Capture and locally verify the resolved Capacitor Swift package and OSV query."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import stat
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


EXPECTED_IDENTITY = "capacitor-swift-pm"
EXPECTED_LOCATION = "https://github.com/ionic-team/capacitor-swift-pm.git"
EXPECTED_VERSION = "8.5.3"
EXPECTED_MANIFEST_SHA256 = "5d8ea57cd6ad551ce324fe005a744491ee75f67f54ac4114503ffb03a7a56f44"
EXPECTED_BINARY_CHECKSUMS = {
    "Capacitor": "9316f568444411d362dcb4d1946e7572d4aef5d690c122ff6f1cb8a44a920c9f",
    "Cordova": "bdcd8eab2d62a077efd76c3c8b9629e65ae7043c1462289a1e55de9be19bbf1f",
}
OSV_ENDPOINT = "https://api.osv.dev/v1/query"
MAX_INPUT_BYTES = 64 * 1024
MAX_OSV_BYTES = 2 * 1024 * 1024
GIT_TIMEOUT_SECONDS = 10
OSV_TIMEOUT_SECONDS = 20
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class AuditError(Exception):
    """Expected input or audit failure, safe to report without source details."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AuditError("JSON contains duplicate keys")
        result[key] = value
    return result


def _load_json_bytes(data: bytes) -> Any:
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except AuditError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise AuditError("invalid JSON input") from None


def _read_regular_file(path: Path, max_bytes: int) -> bytes:
    try:
        if _has_symlink_component(path):
            raise AuditError("symlink inputs are not accepted")
        info = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
            raise AuditError("input file type or size is not accepted")
        with path.open("rb") as stream:
            data = stream.read(max_bytes + 1)
    except AuditError:
        raise
    except OSError:
        raise AuditError("input file is unavailable") from None
    if len(data) > max_bytes:
        raise AuditError("input file exceeds size limit")
    return data


def _has_symlink_component(path: Path) -> bool:
    """Check the lexical path, including parents, without resolving symlinks."""
    absolute = path if path.is_absolute() else Path.cwd() / path
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current = current / component
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                return True
        except FileNotFoundError:
            # Missing output components are fine; any later existing component
            # cannot occur below a missing directory.
            return False
        except OSError:
            return True
    return False


def _parse_lock(data: bytes) -> tuple[dict[str, str], Any]:
    lock = _load_json_bytes(data)
    if not isinstance(lock, dict) or set(lock) - {"pins", "version", "originHash"}:
        raise AuditError("Package.resolved has an unsupported shape")
    if type(lock.get("version")) is not int or lock["version"] not in (2, 3):
        raise AuditError("Package.resolved schema version is unsupported")
    if not isinstance(lock.get("pins"), list):
        raise AuditError("Package.resolved schema version is unsupported")
    if lock.get("version") == 2 and "originHash" in lock:
        raise AuditError("Package.resolved v2 must not contain originHash")
    if "originHash" in lock and (
        lock.get("version") != 3
        or not isinstance(lock["originHash"], str)
        or not SHA256_RE.fullmatch(lock["originHash"])
    ):
        raise AuditError("Package.resolved originHash is invalid for its schema")
    pins = lock["pins"]
    if not pins or len(pins) != 1:
        raise AuditError("Package.resolved must contain exactly one package pin")
    pin = pins[0]
    if not isinstance(pin, dict) or set(pin) != {"identity", "kind", "location", "state"}:
        raise AuditError("package pin has an unsupported shape")
    if pin["identity"] != EXPECTED_IDENTITY or pin["kind"] != "remoteSourceControl":
        raise AuditError("package identity or kind does not match the approved dependency")
    location = pin["location"]
    if location != EXPECTED_LOCATION or not isinstance(location, str):
        raise AuditError("package location does not match the approved repository")
    state = pin["state"]
    if not isinstance(state, dict) or set(state) - {"revision", "version", "branch"}:
        raise AuditError("package state has an unsupported shape")
    if not {"revision", "version"} <= set(state) or state.get("branch") not in (None,):
        raise AuditError("package state must specify a release version and revision")
    revision = state.get("revision")
    version = state.get("version")
    if not isinstance(revision, str) or not REVISION_RE.fullmatch(revision):
        raise AuditError("package revision must be a full lowercase Git revision")
    if version != EXPECTED_VERSION:
        raise AuditError("package version does not match the approved release")
    return {"identity": EXPECTED_IDENTITY, "version": EXPECTED_VERSION, "revision": revision}, lock


def _checkout_manifest(checkout: Path) -> tuple[str, dict[str, str]]:
    if _has_symlink_component(checkout) or not checkout.is_dir():
        raise AuditError("package checkout is unavailable or is a symlink")
    manifest = checkout / "Package.swift"
    raw = _read_regular_file(manifest, MAX_INPUT_BYTES)
    digest = hashlib.sha256(raw).hexdigest()
    if digest != EXPECTED_MANIFEST_SHA256:
        raise AuditError("package manifest checksum does not match the approved manifest")
    text = raw.decode("utf-8", errors="strict")
    found = dict(re.findall(
        r'\.binaryTarget\s*\(\s*name:\s*"([A-Za-z0-9_-]+)"\s*,\s*url:\s*"[^"]+"\s*,\s*checksum:\s*"([0-9a-f]{64})"\s*\)',
        text,
        flags=re.DOTALL,
    ))
    if found != EXPECTED_BINARY_CHECKSUMS:
        raise AuditError("package manifest binary target checksums do not match the approved values")
    return digest, found


def _git_head(checkout: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "--verify", "HEAD"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=GIT_TIMEOUT_SECONDS,
            text=True,
            env={
                "PATH": os.defpath,
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_TERMINAL_PROMPT": "0",
            },
        )
    except (OSError, subprocess.TimeoutExpired):
        raise AuditError("could not read package checkout revision") from None
    revision = result.stdout.strip()
    if result.returncode != 0 or not REVISION_RE.fullmatch(revision):
        raise AuditError("package checkout did not provide a full Git revision")
    return revision


def _validate_checkout(checkout: Path, revision: str) -> tuple[str, dict[str, str]]:
    manifest_sha, checksums = _checkout_manifest(checkout)
    if _git_head(checkout) != revision:
        raise AuditError("package checkout revision differs from Package.resolved")
    return manifest_sha, checksums


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def _query_osv(revision: str) -> tuple[dict[str, Any], list[str]]:
    payload = json.dumps({"commit": revision}, separators=(",", ":")).encode("ascii")
    request = urllib.request.Request(
        OSV_ENDPOINT,
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        response = opener.open(request, timeout=OSV_TIMEOUT_SECONDS)
        with response:
            status = getattr(response, "status", response.getcode())
            if status != 200:
                raise AuditError("OSV returned an unsuccessful HTTP status")
            body = response.read(MAX_OSV_BYTES + 1)
    except AuditError:
        raise
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError, http.client.HTTPException):
        raise AuditError("OSV request failed") from None
    if len(body) > MAX_OSV_BYTES:
        raise AuditError("OSV response exceeds size limit")
    result = _load_json_bytes(body)
    if not isinstance(result, dict) or set(result) - {"vulns", "next_page_token"}:
        raise AuditError("OSV response has an invalid shape")
    token = result.get("next_page_token")
    if token not in (None, ""):
        raise AuditError("OSV response is paginated and cannot be fully audited")
    vulns = result.get("vulns", [])
    if not isinstance(vulns, list):
        raise AuditError("OSV vulnerability list has an invalid shape")
    identifiers: set[str] = set()
    for vulnerability in vulns:
        if not isinstance(vulnerability, dict) or not isinstance(vulnerability.get("id"), str):
            raise AuditError("OSV vulnerability entry has an invalid shape")
        identifiers.add(vulnerability["id"])
        aliases = vulnerability.get("aliases", [])
        if not isinstance(aliases, list) or any(not isinstance(alias, str) for alias in aliases):
            raise AuditError("OSV vulnerability aliases have an invalid shape")
        identifiers.update(aliases)
    if len(identifiers) > 10000 or any(
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value)
        for value in identifiers
    ):
        raise AuditError("OSV vulnerability identifiers exceed accepted limits")
    ids = sorted(identifiers)
    query = {
        "api": OSV_ENDPOINT,
        "commit": revision,
        "limits": {"response_bytes": MAX_OSV_BYTES, "timeout_seconds": OSV_TIMEOUT_SECONDS},
        "vulnerability_ids": ids,
    }
    return query, ids


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode("ascii")


def _write_new_file(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(path, 0o600)


def _create_private_output_directory(path: Path) -> None:
    if _has_symlink_component(path):
        raise AuditError("output path contains a symlink")
    absolute = path if path.is_absolute() else Path.cwd() / path
    if absolute.exists() or absolute.is_symlink():
        raise AuditError("output directory must be new")
    missing: list[Path] = []
    current = absolute
    while not current.exists():
        missing.append(current)
        current = current.parent
    if not current.is_dir() or current.is_symlink():
        raise AuditError("output parent is not a directory")
    for directory in reversed(missing):
        directory.mkdir(mode=0o700, exist_ok=False)
        os.chmod(directory, 0o700)


def _capture(lockfile: Path, checkout: Path, output: Path) -> int:
    lock_bytes = _read_regular_file(lockfile, MAX_INPUT_BYTES)
    package, _ = _parse_lock(lock_bytes)
    manifest_sha, checksums = _validate_checkout(checkout, package["revision"])
    query, advisory_ids = _query_osv(package["revision"])
    status = "advisories_found" if advisory_ids else "no_matched_known_commit_advisories"
    inventory = {
        "package": package,
        "manifest_sha256": manifest_sha,
        "binary_targets": [
            {"name": name, "checksum": checksum}
            for name, checksum in sorted(checksums.items())
        ],
    }
    artifact_query = {"query": query, "status": status}
    receipt = {
        "schema_version": 1,
        "package": package,
        "status": status,
        "sha256": {
            "Package.resolved": hashlib.sha256(lock_bytes).hexdigest(),
            "manifest": manifest_sha,
            "inventory.json": hashlib.sha256(_json_bytes(inventory)).hexdigest(),
            "osv-query.json": hashlib.sha256(_json_bytes(artifact_query)).hexdigest(),
        },
    }
    try:
        _create_private_output_directory(output)
        _write_new_file(output / "Package.resolved", lock_bytes)
        _write_new_file(output / "inventory.json", _json_bytes(inventory))
        _write_new_file(output / "osv-query.json", _json_bytes(artifact_query))
        _write_new_file(output / "receipt.json", _json_bytes(receipt))
    except (OSError, FileExistsError):
        raise AuditError("output directory could not be created as a new private artifact") from None
    print("audit evidence captured; status=" + status)
    return 1 if advisory_ids else 0


def _verify(lockfile: Path, checkout: Path, output: Path) -> int:
    lock_bytes = _read_regular_file(lockfile, MAX_INPUT_BYTES)
    package, _ = _parse_lock(lock_bytes)
    manifest_sha, _ = _validate_checkout(checkout, package["revision"])
    try:
        if _has_symlink_component(output) or not output.is_dir():
            raise AuditError("receipt directory is unavailable")
        saved_lock = _read_regular_file(output / "Package.resolved", MAX_INPUT_BYTES)
        inventory_data = _read_regular_file(output / "inventory.json", MAX_INPUT_BYTES)
        query_data = _read_regular_file(output / "osv-query.json", MAX_INPUT_BYTES)
        receipt_data = _read_regular_file(output / "receipt.json", MAX_INPUT_BYTES)
    except AuditError:
        raise
    receipt = _load_json_bytes(receipt_data)
    inventory = _load_json_bytes(inventory_data)
    query_artifact = _load_json_bytes(query_data)
    if not all(isinstance(item, dict) for item in (receipt, inventory, query_artifact)):
        raise AuditError("saved audit evidence has an invalid shape")
    expected_hashes = {
        "Package.resolved": hashlib.sha256(saved_lock).hexdigest(),
        "manifest": manifest_sha,
        "inventory.json": hashlib.sha256(inventory_data).hexdigest(),
        "osv-query.json": hashlib.sha256(query_data).hexdigest(),
    }
    if saved_lock != lock_bytes:
        raise AuditError("current Package.resolved differs from captured evidence")
    if receipt.get("schema_version") != 1 or receipt.get("package") != package:
        raise AuditError("receipt package binding does not match current inputs")
    if receipt.get("sha256") != expected_hashes:
        raise AuditError("receipt hashes do not match saved evidence")
    if inventory.get("package") != package or inventory.get("manifest_sha256") != manifest_sha:
        raise AuditError("inventory does not match current package inputs")
    expected_query = query_artifact.get("query")
    if (not isinstance(expected_query, dict) or expected_query.get("commit") != package["revision"]
            or expected_query.get("api") != OSV_ENDPOINT
            or expected_query.get("limits") != {"response_bytes": MAX_OSV_BYTES, "timeout_seconds": OSV_TIMEOUT_SECONDS}
            or query_artifact.get("status") != receipt.get("status")
            or receipt.get("status") not in {"advisories_found", "no_matched_known_commit_advisories"}):
        raise AuditError("saved OSV evidence does not match the captured query")
    ids = expected_query.get("vulnerability_ids")
    if not isinstance(ids, list) or any(not isinstance(value, str) for value in ids):
        raise AuditError("saved OSV identifiers have an invalid shape")
    if (receipt["status"] == "no_matched_known_commit_advisories") != (not ids):
        raise AuditError("saved OSV status does not match its identifiers")
    print("audit evidence verified; status=" + receipt["status"])
    return 1 if ids else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("capture", "verify"):
        command = subparsers.add_parser(name)
        command.add_argument("--lockfile", required=True, type=Path)
        command.add_argument("--checkout", required=True, type=Path)
        command.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "capture":
            return _capture(args.lockfile, args.checkout, args.output)
        return _verify(args.lockfile, args.checkout, args.output)
    except AuditError as exc:
        # These messages are fixed strings and never include private paths or server bodies.
        print("iOS SPM audit failed: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
