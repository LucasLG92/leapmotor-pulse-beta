#!/usr/bin/env python3
"""Retain six-lock OSV evidence; incomplete scans must never pass."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCKFILES = (
    "requirements-lock.txt",
    "requirements-dev-lock.txt",
    "requirements-build-lock.txt",
    "apps/web/pnpm-lock.yaml",
    "apps/mobile/pnpm-lock.yaml",
    "apps/mobile/android/app/gradle.lockfile",
)


def lock_hashes(root: Path) -> dict[str, str]:
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in LOCKFILES}


def evaluate(report: dict, root: Path, scan_status: int) -> dict:
    root = root.resolve()
    inventories = {}
    advisories = set()
    results = report["results"]
    if not isinstance(results, list):
        raise ValueError("Invalid scan results")
    for result in results:
        path = Path(result["source"]["path"]).resolve().relative_to(root).as_posix()
        packages = result["packages"]
        if path not in LOCKFILES or path in inventories or not isinstance(packages, list) or not packages:
            raise ValueError("Incomplete or unexpected dependency inventory")
        findings = []
        for entry in packages:
            package = entry["package"]
            if not all(isinstance(package[key], str) and package[key] for key in ("name", "version", "ecosystem")):
                raise ValueError("Incomplete package identity")
            vulnerabilities = entry.get("vulnerabilities", [])
            if not isinstance(vulnerabilities, list):
                raise ValueError("Invalid vulnerability inventory")
            for vulnerability in vulnerabilities:
                advisory_id = vulnerability["id"]
                if not isinstance(advisory_id, str) or not advisory_id:
                    raise ValueError("Invalid advisory identity")
                advisories.add(advisory_id)
                findings.append({"id": advisory_id, "package": package})
        inventories[path] = {"package_entries": len(packages), "findings": findings}
    if set(inventories) != set(LOCKFILES):
        raise ValueError("Missing dependency lockfile in scan result")
    if scan_status not in (0, 1) or (scan_status == 1 and not advisories):
        raise ValueError("Scanner failed without a complete advisory result")
    return {
        "status": "advisories" if advisories else "passed",
        "inventory_complete": True,
        "package_entries": sum(item["package_entries"] for item in inventories.values()),
        "known_advisory_ids": sorted(advisories),
        "inventories": inventories,
    }


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    if len(sys.argv) not in (3, 4, 5) or sys.argv[1] not in ("prepare", "check", "fail"):
        print("Usage: check-dependency-report.py prepare DIRECTORY | check DIRECTORY SCAN_STATUS | fail DIRECTORY STAGE EXIT_CODE", file=sys.stderr)
        return 2
    directory = Path(sys.argv[2])
    if sys.argv[1] == "prepare":
        if len(sys.argv) != 3:
            return 2
        write_json(directory / "inputs.json", {
            "started_at_utc": datetime.now(timezone.utc).isoformat(),
            "lockfile_sha256": lock_hashes(ROOT),
            "checker_sha256": hashlib.sha256((ROOT / "scripts/check-dependencies.sh").read_bytes()).hexdigest(),
            "report_checker_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        })
        write_json(directory / "summary.json", {"status": "incomplete", "inventory_complete": False})
        return 0
    if sys.argv[1] == "fail":
        if len(sys.argv) != 5 or sys.argv[3] not in ("download", "integrity"):
            return 2
        try:
            exit_code = int(sys.argv[4])
        except ValueError:
            return 2
        if exit_code == 0:
            return 2
        write_json(directory / "summary.json", {
            "status": "failed", "inventory_complete": False,
            "failure_stage": sys.argv[3], "stage_exit_code": exit_code,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "scope": "OSV was not queried; no dependency scan result",
        })
        return 2
    summary = {"status": "failed", "inventory_complete": False}
    try:
        if len(sys.argv) != 4:
            raise ValueError("Missing scanner exit status")
        scan_status = int(sys.argv[3])
        summary["scan_exit_code"] = scan_status
        inputs = json.loads((directory / "inputs.json").read_text(encoding="utf-8"))
        if inputs["lockfile_sha256"] != lock_hashes(ROOT):
            raise ValueError("Dependency lockfiles changed during the scan")
        raw = (directory / "report.json").read_bytes()
        summary["report_sha256"] = hashlib.sha256(raw).hexdigest()
        summary.update(evaluate(json.loads(raw), ROOT, scan_status))
    except (OSError, ValueError, TypeError, KeyError) as error:
        summary["error"] = str(error)
    summary["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    summary["scope"] = "Six source lockfiles only; not a complete security review"
    write_json(directory / "summary.json", summary)
    print(json.dumps(summary, indent=2))
    return {"passed": 0, "advisories": 1}.get(summary["status"], 2)


if __name__ == "__main__":
    raise SystemExit(main())
