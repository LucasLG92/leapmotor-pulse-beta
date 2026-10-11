"""Unit fixtures for the isolated SwiftPM evidence helper; no Apple tooling required."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ios-spm-audit.py"
SPEC = importlib.util.spec_from_file_location("ios_spm_audit", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


REVISION = "0123456789abcdef0123456789abcdef01234567"
MANIFEST = b'''// fixture manifest; deliberately not copied from upstream
.binaryTarget(name: "Capacitor", url: "https://example.invalid/Capacitor.zip", checksum: "9316f568444411d362dcb4d1946e7572d4aef5d690c122ff6f1cb8a44a920c9f")
.binaryTarget(name: "Cordova", url: "https://example.invalid/Cordova.zip", checksum: "bdcd8eab2d62a077efd76c3c8b9629e65ae7043c1462289a1e55de9be19bbf1f")
'''


def resolved(version: int = 2, **overrides: object) -> bytes:
    pin = {
        "identity": audit.EXPECTED_IDENTITY,
        "kind": "remoteSourceControl",
        "location": audit.EXPECTED_LOCATION,
        "state": {"revision": REVISION, "version": audit.EXPECTED_VERSION},
    }
    for key, value in overrides.items():
        if key == "state":
            pin["state"].update(value)  # type: ignore[arg-type]
        else:
            pin[key] = value
    lock = {"pins": [pin], "version": version}
    if version == 3:
        lock["originHash"] = "a" * 64
    return json.dumps(lock).encode()


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200):
        self.body = body
        self.status = status

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def getcode(self) -> int:
        return self.status

    def read(self, limit: int = -1) -> bytes:
        return self.body[:limit]


class IOSPMAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        # macOS can expose the temporary root through /var -> /private/var.
        self.root = Path(self.temp.name).resolve()
        self.lockfile = self.root / "Package.resolved"
        self.lockfile.write_bytes(resolved())
        self.checkout = self.root / "checkout"
        self.checkout.mkdir()
        (self.checkout / "Package.swift").write_bytes(MANIFEST)
        self.output = self.root / "evidence"
        self._git_patcher = patch.object(
            audit.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout=REVISION + "\n"),
        )
        self.git_run = self._git_patcher.start()
        self.addCleanup(self._git_patcher.stop)
        self._manifest_patcher = patch.object(
            audit, "EXPECTED_MANIFEST_SHA256", hashlib.sha256(MANIFEST).hexdigest()
        )
        self._manifest_patcher.start()
        self.addCleanup(self._manifest_patcher.stop)
        self.addCleanup(self.temp.cleanup)

    def mock_osv(self, payload: object) -> Mock:
        response = FakeResponse(json.dumps(payload).encode())
        patcher = patch.object(audit.urllib.request, "build_opener", return_value=Mock(open=Mock(return_value=response)))
        opener = patcher.start()
        self.addCleanup(patcher.stop)
        return opener

    def test_capture_accepts_lock_schema_2_and_3_and_writes_private_evidence(self) -> None:
        for schema in (2, 3):
            with self.subTest(schema=schema):
                if self.output.exists():
                    for child in self.output.iterdir():
                        child.unlink()
                    self.output.rmdir()
                self.lockfile.write_bytes(resolved(schema))
                self.mock_osv({})
                self.assertEqual(audit._capture(self.lockfile, self.checkout, self.output), 0)
                self.assertEqual({p.name for p in self.output.iterdir()}, {
                    "Package.resolved", "inventory.json", "osv-query.json", "receipt.json"
                })
                self.assertEqual(stat_mode(self.output), 0o700)
                self.assertTrue(all(stat_mode(p) == 0o600 for p in self.output.iterdir()))
                receipt = json.loads((self.output / "receipt.json").read_text())
                self.assertEqual(receipt["status"], "no_matched_known_commit_advisories")

    def test_rejects_identity_url_version_revision_duplicate_keys_and_malformed_json(self) -> None:
        cases = [
            resolved(identity="other-package"),
            resolved(location="https://user:pass@github.com/ionic-team/capacitor-swift-pm.git"),
            resolved(state={"version": "9.0.0"}),
            resolved(state={"revision": "A" * 40}),
            resolved(state={"branch": "main"}),
            resolved(extra="unexpected-field"),
            json.dumps({"pins": [], "version": 2.0}).encode(),
            b'{"pins":[],"pins":[],"version":2}',
            b"{not json",
        ]
        for content in cases:
            with self.subTest(content=content[:45]):
                with self.assertRaises(audit.AuditError):
                    audit._parse_lock(content)

    def test_rejects_oversized_and_symlink_lockfiles(self) -> None:
        self.lockfile.write_bytes(b" " * (audit.MAX_INPUT_BYTES + 1))
        with self.assertRaises(audit.AuditError):
            audit._read_regular_file(self.lockfile, audit.MAX_INPUT_BYTES)
        target = self.root / "real-lock"
        target.write_bytes(resolved())
        link = self.root / "link-lock"
        link.symlink_to(target)
        with self.assertRaises(audit.AuditError):
            audit._read_regular_file(link, audit.MAX_INPUT_BYTES)

    def test_rejects_pagination_invalid_response_and_oversized_osv(self) -> None:
        for body in (b'{"next_page_token":"more"}', b"[]", b'{"vulns":"bad"}',
                     b'{"error":"unexpected"}', b"not-json"):
            with self.subTest(body=body):
                response = FakeResponse(body)
                patcher = patch.object(
                    audit.urllib.request,
                    "build_opener",
                    return_value=Mock(open=Mock(return_value=response)),
                )
                patcher.start()
                self.addCleanup(patcher.stop)
                with self.assertRaises(audit.AuditError):
                    audit._query_osv(REVISION)
        response = FakeResponse(b"{}" * (audit.MAX_OSV_BYTES // 2 + 1))
        patcher = patch.object(audit.urllib.request, "build_opener", return_value=Mock(open=Mock(return_value=response)))
        patcher.start()
        self.addCleanup(patcher.stop)
        with self.assertRaises(audit.AuditError):
            audit._query_osv(REVISION)

    def test_network_errors_fail_without_exposing_server_details(self) -> None:
        patcher = patch.object(
            audit.urllib.request,
            "build_opener",
            return_value=Mock(open=Mock(side_effect=urllib.error.URLError("private server detail"))),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        with self.assertRaises(audit.AuditError) as error:
            audit._query_osv(REVISION)
        self.assertNotIn("private server detail", str(error.exception))

    def test_advisory_ids_are_retained_and_cause_nonzero_capture(self) -> None:
        self.mock_osv({"vulns": [{"id": "GHSA-aaaa-bbbb-cccc", "aliases": ["CVE-2026-0001"]}]})
        self.assertEqual(audit._capture(self.lockfile, self.checkout, self.output), 1)
        evidence = json.loads((self.output / "osv-query.json").read_text())
        self.assertEqual(evidence["query"]["vulnerability_ids"], ["CVE-2026-0001", "GHSA-aaaa-bbbb-cccc"])
        self.assertEqual(evidence["status"], "advisories_found")

    def test_verify_detects_changed_lock_and_never_queries_osv(self) -> None:
        self.mock_osv({})
        audit._capture(self.lockfile, self.checkout, self.output)
        self.lockfile.write_bytes(resolved(state={"revision": "f" * 40}))
        with patch.object(audit, "_query_osv", side_effect=AssertionError("verify called OSV")):
            with self.assertRaises(audit.AuditError):
                audit._verify(self.lockfile, self.checkout, self.output)

    def test_verify_accepts_unchanged_evidence_without_another_network_query(self) -> None:
        self.mock_osv({})
        audit._capture(self.lockfile, self.checkout, self.output)
        with patch.object(audit, "_query_osv", side_effect=AssertionError("verify called OSV")):
            self.assertEqual(audit._verify(self.lockfile, self.checkout, self.output), 0)

    def test_verify_rejects_tampered_saved_query(self) -> None:
        self.mock_osv({})
        audit._capture(self.lockfile, self.checkout, self.output)
        query = self.output / "osv-query.json"
        evidence = json.loads(query.read_text())
        evidence["query"]["commit"] = "f" * 40
        query.write_text(json.dumps(evidence))
        with self.assertRaises(audit.AuditError):
            audit._verify(self.lockfile, self.checkout, self.output)

    def test_capture_rejects_checkout_revision_or_manifest_mismatch_before_network(self) -> None:
        with patch.object(audit, "_query_osv", side_effect=AssertionError("invalid checkout queried OSV")):
            self.git_run.return_value.stdout = "f" * 40 + "\n"
            with self.assertRaises(audit.AuditError):
                audit._capture(self.lockfile, self.checkout, self.output)
            self.git_run.return_value.stdout = REVISION + "\n"
            (self.checkout / "Package.swift").write_bytes(MANIFEST + b"\n")
            with self.assertRaises(audit.AuditError):
                audit._capture(self.lockfile, self.checkout, self.output)
        self.assertFalse(self.output.exists())


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


if __name__ == "__main__":
    unittest.main()
