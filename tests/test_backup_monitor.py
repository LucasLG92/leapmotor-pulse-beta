"""Backup health is metadata-only, fail-closed and never transmits user data."""
from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "deploy/community/backup-monitor.py"
SPEC = importlib.util.spec_from_file_location("backup_monitor", SCRIPT)
assert SPEC and SPEC.loader
monitor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(monitor)


class BackupMonitorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root / "copies"
        self.directory.mkdir(mode=0o700)
        self.state = self.root / "state"
        self.uid = os.geteuid()
        self.now = datetime(2026, 10, 12, 2, tzinfo=timezone.utc)
        self.job = {"LoadState": "loaded", "ActiveState": "inactive", "Result": "success"}
        self.timer = {"LoadState": "loaded", "ActiveState": "active", "UnitFileState": "enabled"}

    def backup(self, profile="sqlite", created=None):
        created = created or self.now - timedelta(hours=2)
        if profile == "sqlite":
            name = "application-" + created.strftime("%Y%m%dT%H%M%SZ") + ".sqlite3"
            document = {"file": name, "schema": 16, "bytes": 9, "sha256": "ab" * 32, "table_counts": {"synthetic": 0}}
            manifest = self.directory / (name.removesuffix(".sqlite3") + ".json")
        else:
            name = "application-archive-20261012T020000Z.tar.zst"
            document = {"archive": name, "bytes": 9, "sha256": "ab" * 32,
                        "created_at": created.isoformat(), }
            manifest = self.directory / (name + ".json")
        artifact = self.directory / name
        artifact.write_bytes(b"synthetic")
        artifact.chmod(0o600)
        manifest.write_text(json.dumps(document))
        manifest.chmod(0o600)
        return artifact, manifest, document

    def check(self, profile="sqlite", **kwargs):
        return monitor.inspect(profile, self.directory, kwargs.pop("now", self.now), self.uid,
                               kwargs.pop("job", self.job), kwargs.pop("timer", self.timer), **kwargs)

    def test_both_existing_manifest_formats_and_utc_age(self):
        for profile in ["sqlite", "archive"]:
            with self.subTest(profile=profile):
                self.backup(profile)
                result = self.check(profile)
                self.assertEqual(result["status"], "healthy")
                self.assertEqual(result["age_seconds"], 7200)
                self.assertFalse(result["artifact_content_hash_verified"])
                self.assertFalse(result["restore_verified"])
                self.assertFalse(result["notification_delivered"])
                self.assertNotIn("synthetic", json.dumps(result))

    def test_stale_threshold_and_25_hour_dst_day(self):
        for hours, extra, expected in [(25, 0, "healthy"), (36, 0, "healthy"), (36, 1, "alert")]:
            with self.subTest(hours=hours, extra_seconds=extra):
                for p in self.directory.iterdir():
                    p.unlink()
                self.backup(created=self.now - timedelta(hours=hours, seconds=extra))
                result = self.check()
                self.assertEqual(result["status"], expected)
                self.assertEqual("copy_stale" in result["issues"], expected == "alert")

    def test_missing_copy_and_unavailable_directory(self):
        self.assertIn("copy_missing", self.check()["issues"])
        self.directory.rmdir()
        self.assertIn("backup_directory_unavailable_or_unsafe", self.check()["issues"])

    def test_failed_job_or_reported_failure_even_with_fresh_copy(self):
        self.backup()
        for job in [{**self.job, "Result": "exit-code"}, {**self.job, "ActiveState": "failed"}]:
            with self.subTest(job=job):
                self.assertIn("backup_job_failed", self.check(job=job)["issues"])
        self.assertIn("backup_job_failed", self.check(reported_failure=True)["issues"])
        self.assertIn("backup_job_unavailable", self.check(job={"LoadState": "not-found"})["issues"])

    def test_timer_must_be_active_and_enabled(self):
        self.backup()
        for key, value in [("LoadState", "not-found"), ("ActiveState", "inactive"), ("UnitFileState", "disabled"), ("UnitFileState", "masked")]:
            with self.subTest(property=key, value=value):
                self.assertIn("backup_timer_inactive", self.check(timer={**self.timer, key: value})["issues"])

    def test_wrong_disk_never_reads_copies(self):
        with patch.object(monitor, "inspect_copies", side_effect=AssertionError("must not read wrong disk")):
            self.assertEqual(self.check("archive", disk_matches=False)["issues"], ["independent_disk_unavailable"])

    def test_manifest_fields_size_and_artifact_identity(self):
        _, manifest, base = self.backup()
        for key, value in [("file", "../../unrelated"), ("bytes", 10), ("bytes", True), ("schema", True), ("sha256", "unknown")]:
            with self.subTest(key=key):
                manifest.write_text(json.dumps({**base, key: value}))
                self.assertIn("invalid_backup_metadata", self.check()["issues"])

    def test_archive_metadata_is_bound_to_artifact_and_aware_timestamp(self):
        _, manifest, base = self.backup("archive")
        for key, value in [("archive", "../unrelated"), ("bytes", 10), ("bytes", True), ("sha256", "unknown"), ("created_at", "invalid"), ("created_at", "2026-10-12T00:00:00")]:
            with self.subTest(key=key):
                manifest.write_text(json.dumps({**base, key: value}))
                self.assertIn("invalid_backup_metadata", self.check("archive")["issues"])

    def test_invalid_completed_copy_is_not_hidden_by_older_valid_copy(self):
        self.backup(created=self.now - timedelta(hours=3))
        _, manifest, _ = self.backup(created=self.now - timedelta(hours=1))
        manifest.write_text("not JSON")
        self.assertEqual(self.check()["status"], "alert")

    def test_future_timestamp_is_not_a_fresh_backup(self):
        self.backup(created=self.now + timedelta(seconds=1))
        result = self.check()
        self.assertIn("future_copy_timestamp", result["issues"])
        self.assertIsNone(result["age_seconds"])

    def test_duplicate_keys_and_oversize_json_fail_closed(self):
        _, manifest, base = self.backup()
        for content in ['{"bytes":9,"bytes":9}', " " * (monitor.MAX_JSON_BYTES + 1)]:
            with self.subTest(size=len(content)):
                manifest.write_text(content)
                self.assertIn("invalid_backup_metadata", self.check()["issues"])

    def test_deep_json_and_utc_overflow_are_fixed_metadata_errors(self):
        _, manifest, base = self.backup("archive")
        manifest.write_text('{"nested":' + '[' * 2000 + '0' + ']' * 2000 + '}')
        self.assertIn("invalid_backup_metadata", self.check("archive")["issues"])
        for date in ["9999-12-31T23:59:59-01:00", "0001-01-01T00:00:00+01:00"]:
            with self.subTest(date=date):
                manifest.write_text(json.dumps({**base, "created_at": date}))
                self.assertIn("invalid_backup_metadata", self.check("archive")["issues"])

    def test_manifest_and_artifact_symlinks_and_hardlinks_rejected(self):
        artifact, manifest, _ = self.backup()
        for target in [manifest, artifact]:
            with self.subTest(name=target.suffix):
                saved = target.with_name("unrelated-" + target.name)
                target.rename(saved)
                target.symlink_to(saved)
                self.assertIn("invalid_backup_metadata", self.check()["issues"])
                target.unlink()
                os.link(saved, target)
                self.assertIn("invalid_backup_metadata", self.check()["issues"])
                target.unlink()
                saved.rename(target)

    def test_fifo_does_not_block_inspection(self):
        _, manifest, _ = self.backup()
        manifest.unlink()
        os.mkfifo(manifest, 0o600)
        self.assertIn("invalid_backup_metadata", self.check()["issues"])

    def test_directory_symlinks_including_ancestor_are_rejected(self):
        self.backup()
        link = self.root / "link"
        link.symlink_to(self.directory, target_is_directory=True)
        result = monitor.inspect("sqlite", link, self.now, self.uid, self.job, self.timer)
        self.assertEqual(result["status"], "alert")
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        result = monitor.inspect("sqlite", alias / "copies", self.now, self.uid, self.job, self.timer)
        self.assertEqual(result["status"], "alert")

    def test_modes_ownership_and_inventory_bounds(self):
        _, manifest, _ = self.backup()
        manifest.chmod(0o644)
        self.assertEqual(self.check()["status"], "alert")
        manifest.chmod(0o600)
        self.directory.chmod(0o755)
        self.assertEqual(self.check()["status"], "alert")
        self.directory.chmod(0o700)
        self.assertEqual(monitor.inspect("sqlite", self.directory, self.now, self.uid + 1, self.job, self.timer)["status"], "alert")
        with patch.object(monitor, "MAX_MANIFESTS", 0):
            self.assertIn("inventory_limit_exceeded", self.check()["issues"])

    def test_same_size_corruption_is_not_claimed_as_hash_or_restore_check(self):
        artifact, _, _ = self.backup()
        artifact.write_bytes(b"different")
        result = self.check()
        self.assertEqual(result["status"], "healthy")
        self.assertFalse(result["artifact_content_hash_verified"])
        self.assertFalse(result["restore_verified"])

    def test_alert_dedup_recovery_and_private_atomic_state(self):
        first = monitor.persist(self.state, self.check(), self.uid)
        self.assertEqual(first["transition"], "alert_started")
        again = monitor.persist(self.state, self.check(now=self.now + timedelta(minutes=1)), self.uid)
        self.assertEqual(again["transition"], "unchanged")
        self.assertEqual(first["alert_since"], again["alert_since"])
        self.backup()
        recovered = monitor.persist(self.state, self.check(now=self.now + timedelta(minutes=2)), self.uid)
        self.assertEqual(recovered["transition"], "recovered")
        healthy = monitor.persist(self.state, self.check(now=self.now + timedelta(minutes=3)), self.uid)
        self.assertEqual(healthy["transition"], "unchanged")
        self.assertEqual(healthy["last_recovered_at"], recovered["last_recovered_at"])
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.state / "sqlite.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads((self.state / "sqlite.json").read_text()), healthy)
        self.assertFalse(list(self.state.glob("*.tmp")))

    def test_v1_upgrade_starts_empty_journal_and_keeps_new_transition(self):
        self.backup()
        prior = monitor.persist(self.state, self.check(), self.uid)
        legacy = {key: value for key, value in prior.items()
                  if key not in {"journal_id", "event_sequence", "events"}}
        legacy["schema_version"] = 1
        (self.state / "sqlite.json").write_text(json.dumps(legacy))

        alert = self.check(now=self.now + timedelta(minutes=1), reported_failure=True)
        upgraded = monitor.persist(self.state, alert, self.uid)
        self.assertEqual(upgraded["schema_version"], 2)
        self.assertEqual(upgraded["transition"], "alert_started")
        self.assertEqual(upgraded["event_sequence"], 1)
        self.assertEqual([event["kind"] for event in upgraded["events"]], ["backup_alert"])
        self.assertNotEqual(upgraded["journal_id"], legacy.get("journal_id"))
        self.assertEqual(upgraded["artifact_content_hash_verified"], False)
        self.assertEqual(upgraded["restore_verified"], False)
        self.assertEqual(upgraded["notification_delivered"], False)

    def test_journal_retains_latest_64_with_monotonic_sequence(self):
        self.backup()
        for index in range(130):
            moment = self.now + timedelta(minutes=index)
            report = self.check(now=moment, reported_failure=index % 2 == 0)
            monitor.persist(self.state, report, self.uid)
        current = json.loads((self.state / "sqlite.json").read_text())
        self.assertEqual(current["event_sequence"], 130)
        self.assertEqual(len(current["events"]), 64)
        self.assertEqual([event["sequence"] for event in current["events"]], list(range(67, 131)))
        self.assertEqual(current["events"][-1]["kind"], "backup_recovery")

    def test_malformed_v2_journal_and_reordered_events_fail_before_replace(self):
        self.backup()
        baseline = monitor.persist(self.state, self.check(reported_failure=True), self.uid)
        second = monitor.persist(self.state, self.check(now=self.now + timedelta(minutes=1)), self.uid)
        self.assertEqual(second["transition"], "recovered")
        target = self.state / "sqlite.json"
        for mutate in [
            lambda raw: raw.update(journal_id="not-an-id"),
            lambda raw: raw.update(event_sequence=True),
            lambda raw: raw.update(events=list(reversed(raw["events"]))),
            lambda raw: raw["events"][1].update(sequence=1),
            lambda raw: raw["events"][1].update(kind="unsupported-reason"),
            lambda raw: raw["events"][1].update(kind=["unsupported-reason"]),
            lambda raw: raw["events"][1].update(id=raw["events"][0]["id"]),
            lambda raw: raw.update(events=raw["events"][:-1]),
            lambda raw: raw["events"][1].update(at="2026-10-12T03:00:00Z"),
        ]:
            with self.subTest(mutate=mutate):
                content = json.loads(json.dumps(second))
                mutate(content)
                encoded = json.dumps(content).encode()
                target.write_bytes(encoded)
                with self.assertRaises(monitor.MonitorError):
                    monitor.persist(self.state, self.check(now=self.now + timedelta(minutes=2)), self.uid)
                self.assertEqual(target.read_bytes(), encoded)
        self.assertEqual(baseline["journal_id"], second["journal_id"])

    def test_older_poll_cannot_erase_newer_failure(self):
        self.backup()
        newer = self.check(now=self.now + timedelta(minutes=1), reported_failure=True)
        monitor.persist(self.state, newer, self.uid)
        before = (self.state / "sqlite.json").read_bytes()
        result = monitor.persist(self.state, self.check(), self.uid)
        self.assertEqual(result["status"], "alert")
        self.assertEqual(result["transition"], "superseded_check")
        self.assertEqual((self.state / "sqlite.json").read_bytes(), before)

    def test_invalid_or_foreign_previous_state_is_preserved(self):
        self.backup()
        baseline = monitor.persist(self.state, self.check(), self.uid)
        for key, value in [("extra", "synthetic-sentinel"), ("status", {}), ("profile", "archive"), ("issues", ["synthetic-sentinel"]), ("last_recovered_at", {"value": "synthetic-sentinel"})]:
            with self.subTest(key=key):
                target = self.state / "sqlite.json"
                content = json.dumps({**baseline, key: value}).encode()
                target.write_bytes(content)
                with self.assertRaises(monitor.MonitorError):
                    monitor.persist(self.state, self.check(), self.uid)
                self.assertEqual(target.read_bytes(), content)

    def test_huge_previous_state_numbers_are_fixed_validation_errors(self):
        self.backup()
        baseline = monitor.persist(self.state, self.check(), self.uid)
        for key in ["age_seconds", "max_age_hours"]:
            with self.subTest(key=key):
                target = self.state / "sqlite.json"
                target.write_text(json.dumps({**baseline, key: 10**1000}))
                with self.assertRaises(monitor.MonitorError):
                    monitor.persist(self.state, self.check(), self.uid)

    def test_failed_atomic_replace_preserves_previous_snapshot(self):
        self.backup()
        monitor.persist(self.state, self.check(), self.uid)
        before = (self.state / "sqlite.json").read_bytes()
        with patch.object(monitor.os, "replace", side_effect=OSError("synthetic private diagnostic")):
            with self.assertRaises(OSError):
                monitor.persist(self.state, self.check(reported_failure=True), self.uid)
        self.assertEqual((self.state / "sqlite.json").read_bytes(), before)
        self.assertFalse(list(self.state.glob("*.tmp")))

    def test_state_parent_symlink_does_not_create_foreign_directory(self):
        foreign = self.root / "foreign"
        foreign.mkdir(mode=0o700)
        alias = self.root / "alias"
        alias.symlink_to(foreign, target_is_directory=True)
        with self.assertRaises(OSError):
            monitor.persist(alias / "state", self.check(), self.uid)
        self.assertFalse((foreign / "state").exists())

    def test_state_symlink_lock_or_nonprivate_parent_fails_closed(self):
        foreign = self.root / "foreign"
        foreign.write_text("synthetic unrelated content")
        self.state.mkdir(mode=0o700)
        lock = self.state / ".lock"
        lock.symlink_to(foreign)
        with self.assertRaises(OSError):
            monitor.persist(self.state, self.check(), self.uid)
        self.assertEqual(foreign.read_text(), "synthetic unrelated content")
        lock.unlink()
        self.state.rmdir()
        self.root.chmod(0o777)
        with self.assertRaises(monitor.MonitorError):
            monitor.persist(self.state, self.check(), self.uid)
        self.assertFalse(self.state.exists())
        self.root.chmod(0o700)

    def test_systemd_query_is_fixed_and_incomplete_or_failed_output_rejected(self):
        complete = subprocess.CompletedProcess([], 0, "LoadState=loaded\nActiveState=inactive\nResult=success\n", "")
        with patch.object(monitor.subprocess, "run", return_value=complete) as run:
            result = monitor.systemd_properties("application-backup.service", ["LoadState", "ActiveState", "Result"])
            self.assertEqual(result, self.job)
            self.assertEqual(run.call_args.args[0], ["systemctl", "show", "--property=LoadState", "--property=ActiveState", "--property=Result", "application-backup.service"])
        for value in [subprocess.CompletedProcess([], 1, "private", "private"), subprocess.CompletedProcess([], 0, "Result=success\n", ""), subprocess.CompletedProcess([], 0, complete.stdout + "Result=success\n", "")]:
            with self.subTest(return_code=value.returncode):
                with patch.object(monitor.subprocess, "run", return_value=value):
                    with self.assertRaises(monitor.MonitorError):
                        monitor.systemd_properties("application-backup.service", ["LoadState", "ActiveState", "Result"])

    def test_mount_root_and_effective_target_both_required(self):
        archive_uuid = "00000000-0000-4000-8000-000000000000"
        good = {"filesystems": [{"target": str(monitor.ARCHIVE_MOUNT), "fstype": monitor.ARCHIVE_FSTYPE, "uuid": archive_uuid}]}
        responses = [subprocess.CompletedProcess([], 0, json.dumps(good), "")] * 2
        with patch.dict(os.environ, {"BACKUP_MONITOR_ARCHIVE_UUID": archive_uuid}), patch.object(monitor.subprocess, "run", side_effect=responses):
            self.assertTrue(monitor.independent_disk_matches(self.directory))
        for bad in [{"filesystems": []}, {"filesystems": [{**good["filesystems"][0], "target": "nested"}]}, {"filesystems": [{**good["filesystems"][0], "uuid": "other"}]}]:
            with self.subTest(bad=bad):
                with patch.dict(os.environ, {"BACKUP_MONITOR_ARCHIVE_UUID": archive_uuid}), patch.object(monitor.subprocess, "run", side_effect=[responses[0], subprocess.CompletedProcess([], 0, json.dumps(bad), "")]):
                    self.assertFalse(monitor.independent_disk_matches(self.directory))

    def test_inspection_failure_persists_alert_without_diagnostic_leak(self):
        output = io.StringIO()
        with patch.object(monitor.os, "geteuid", return_value=0), patch.object(monitor, "systemd_properties", side_effect=OSError("synthetic-sentinel")), patch.object(monitor, "persist", side_effect=lambda directory, report, uid: report) as save, contextlib.redirect_stdout(output):
            self.assertEqual(monitor.main(["--profile", "sqlite", "--state-directory", str(self.state)]), 2)
        self.assertEqual(save.call_args.args[1]["issues"], ["inspection_unavailable"])
        self.assertNotIn("synthetic-sentinel", output.getvalue())

    def test_invalid_configuration_never_queries_or_writes(self):
        for value in ["nan", "inf", "0", "169"]:
            with self.subTest(value=value):
                with patch.object(monitor.os, "geteuid", return_value=0), patch.object(monitor, "systemd_properties") as read, patch.object(monitor, "persist") as save, contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(monitor.main(["--profile", "sqlite", "--max-age-hours", value]), 2)
                    read.assert_not_called()
                    save.assert_not_called()

    def test_monitor_alert_does_not_invoke_backup_failure_handler(self):
        units = SCRIPT.parent
        common = (units / "application-backup-monitor@.service").read_text()
        failure = (units / "application-backup-monitor-failure@.service").read_text()
        self.assertNotIn("OnFailure=", common)
        self.assertNotIn("OnFailure=", failure)
        self.assertNotIn("ReadOnlyPaths=", common)
        self.assertIn("ProtectSystem=strict", common)
        self.assertIn("--reported-failure", failure)
        for name, profile in [("application-backup.service.example", "sqlite"), ("application-archive-backup.service.example", "archive")]:
            self.assertIn("OnFailure=application-backup-monitor-failure@" + profile + ".service", (units / name).read_text())
        for name, profile in [("application-backup-monitor-sqlite.timer", "sqlite"), ("application-backup-monitor-archive.timer", "archive")]:
            timer = (units / name).read_text()
            self.assertIn("OnCalendar=hourly", timer)
            self.assertIn("Unit=application-backup-monitor@" + profile + ".service", timer)
        for name, service in [("application-backup.timer.example", "application-backup.service"), ("application-archive-backup.timer.example", "application-archive-backup.service")]:
            self.assertIn("OnCalendar=daily", (units / name).read_text())
            self.assertIn("Unit=" + service, (units / name).read_text())

if __name__ == "__main__":
    unittest.main()
