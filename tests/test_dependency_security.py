"""Security gate fixtures: incomplete or failed OSV scans cannot become green."""

from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check-dependency-report.py"
SPEC = importlib.util.spec_from_file_location("dependency_report", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


class DependencySecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.output = self.root / "evidence"
        self.output.mkdir()
        for name in (*audit.LOCKFILES, "scripts/check-dependencies.sh"):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("synthetic fixture\n")
        self.report = {"results": [
            {"source": {"path": str(self.root / name)}, "packages": [
                {"package": {"name": "fixture", "version": "1.0.0", "ecosystem": "npm"}}
            ]} for name in audit.LOCKFILES
        ]}

    def invoke(self, *args: str) -> int:
        with patch.object(audit, "ROOT", self.root), patch.object(sys, "argv", [str(SCRIPT), *args]):
            with contextlib.redirect_stdout(io.StringIO()):
                return audit.main()

    def prepare(self) -> None:
        self.assertEqual(self.invoke("prepare", str(self.output)), 0)
        (self.output / "report.json").write_text(json.dumps(self.report))

    def test_complete_clean_scan_passes(self) -> None:
        result = audit.evaluate(self.report, self.root, 0)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["package_entries"], 6)
        self.assertEqual(set(result["inventories"]), set(audit.LOCKFILES))

    def test_all_advisories_survive_without_severity_filter(self) -> None:
        self.report["results"][0]["packages"][0]["vulnerabilities"] = [
            {"id": "GHSA-fixture-low", "database_specific": {"severity": "LOW"}},
            {"id": "GHSA-fixture-high", "database_specific": {"severity": "HIGH"}},
        ]
        for code in (0, 1):
            with self.subTest(scan_exit=code):
                result = audit.evaluate(self.report, self.root, code)
                self.assertEqual(result["status"], "advisories")
                self.assertEqual(result["known_advisory_ids"], ["GHSA-fixture-high", "GHSA-fixture-low"])
        self.prepare()
        self.assertEqual(self.invoke("check", str(self.output), "1"), 1)

    def test_any_missing_lockfile_is_rejected(self) -> None:
        for index in range(6):
            with self.subTest(missing=index):
                report = copy.deepcopy(self.report)
                report["results"].pop(index)
                with self.assertRaises(ValueError):
                    audit.evaluate(report, self.root, 0)

    def test_duplicate_or_outside_source_is_rejected(self) -> None:
        for path in (self.report["results"][0]["source"]["path"], "/outside/requirements-lock.txt"):
            with self.subTest(path=path):
                report = copy.deepcopy(self.report)
                report["results"][1]["source"]["path"] = path
                with self.assertRaises(ValueError):
                    audit.evaluate(report, self.root, 0)

    def test_empty_or_invalid_package_inventory_is_rejected(self) -> None:
        for packages in ([], {}, None):
            with self.subTest(packages=packages):
                report = copy.deepcopy(self.report)
                report["results"][0]["packages"] = packages
                with self.assertRaises(ValueError):
                    audit.evaluate(report, self.root, 0)

    def test_package_identity_cannot_be_incomplete(self) -> None:
        for key in ("name", "version", "ecosystem"):
            with self.subTest(key=key):
                report = copy.deepcopy(self.report)
                report["results"][0]["packages"][0]["package"][key] = ""
                with self.assertRaises(ValueError):
                    audit.evaluate(report, self.root, 0)

    def test_malformed_vulnerability_inventory_is_rejected(self) -> None:
        for value in (None, {}, [{"id": ""}]):
            with self.subTest(value=value):
                report = copy.deepcopy(self.report)
                report["results"][0]["packages"][0]["vulnerabilities"] = value
                with self.assertRaises(ValueError):
                    audit.evaluate(report, self.root, 0)

    def test_scanner_failure_cannot_pass_even_with_clean_inventory(self) -> None:
        for code in (1, 2, 127, -9):
            with self.subTest(code=code):
                with self.assertRaises(ValueError):
                    audit.evaluate(self.report, self.root, code)

    def test_missing_or_malformed_report_writes_failed_summary(self) -> None:
        self.prepare()
        report = self.output / "report.json"
        for body in (None, "{not-json", '{"results":null}'):
            with self.subTest(body=body):
                if body is None:
                    report.unlink(missing_ok=True)
                else:
                    report.write_text(body)
                self.assertEqual(self.invoke("check", str(self.output), "0"), 2)
                summary = json.loads((self.output / "summary.json").read_text())
                self.assertEqual(summary["status"], "failed")
                self.assertFalse(summary["inventory_complete"])

    def test_lock_changed_during_scan_is_rejected(self) -> None:
        self.prepare()
        (self.root / audit.LOCKFILES[0]).write_text("changed input\n")
        self.assertEqual(self.invoke("check", str(self.output), "0"), 2)
        summary = json.loads((self.output / "summary.json").read_text())
        self.assertEqual(summary["status"], "failed")
        self.assertIn("changed during", summary["error"])

    def test_prepared_evidence_is_incomplete_until_check_finishes(self) -> None:
        self.prepare()
        self.assertEqual(json.loads((self.output / "summary.json").read_text())["status"], "incomplete")
        self.assertEqual(self.invoke("check", str(self.output), "0"), 0)
        summary = json.loads((self.output / "summary.json").read_text())
        self.assertEqual(summary["status"], "passed")
        self.assertEqual(len(summary["report_sha256"]), 64)

    def test_existing_report_directory_is_preserved_before_download(self) -> None:
        sentinel = self.output / "sentinel"
        sentinel.write_text("keep existing evidence\n")
        result = subprocess.run(
            ["bash", str(SCRIPT.with_name("check-dependencies.sh")), "--report-dir", str(self.output)],
            capture_output=True, text=True, timeout=5,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(self.output.iterdir()), [sentinel])
        self.assertEqual(sentinel.read_text(), "keep existing evidence\n")

    def shell_with_curl_fixture(self, body: str) -> tuple[subprocess.CompletedProcess, Path]:
        commands = self.root / "commands"
        commands.mkdir()
        curl = commands / "curl"
        curl.write_text("#!/usr/bin/env python3\n" + body)
        curl.chmod(0o700)
        output = self.root / "new-report"
        environment = dict(os.environ, PATH=str(commands) + os.pathsep + os.environ["PATH"])
        result = subprocess.run(
            ["bash", str(SCRIPT.with_name("check-dependencies.sh")), "--report-dir", str(output)],
            capture_output=True, text=True, timeout=10, env=environment,
        )
        return result, output

    def test_download_failure_retains_stage_and_diagnostic(self) -> None:
        result, output = self.shell_with_curl_fixture(
            "import sys\nprint('synthetic transport failure', file=sys.stderr)\nsys.exit(7)\n"
        )
        self.assertEqual(result.returncode, 7)
        summary = json.loads((output / "summary.json").read_text())
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["failure_stage"], "download")
        self.assertEqual(summary["stage_exit_code"], 7)
        self.assertIn("synthetic transport failure", (output / "download.log").read_text())
        self.assertFalse((output / "report.json").exists())

    def test_unverified_scanner_is_never_executed(self) -> None:
        marker = self.root / "must-not-execute"
        program = f"#!/bin/sh\ntouch '{marker}'\n"
        result, output = self.shell_with_curl_fixture(
            "import sys\nfrom pathlib import Path\n"
            f"Path(sys.argv[sys.argv.index('--output')+1]).write_text({program!r})\n"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(marker.exists())
        summary = json.loads((output / "summary.json").read_text())
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["failure_stage"], "integrity")
        self.assertFalse((output / "report.json").exists())


if __name__ == "__main__":
    unittest.main()
