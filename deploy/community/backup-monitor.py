"""Read backup metadata, retain local health transitions; never send or restore data."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import uuid


SQLITE_DIRECTORY = Path(os.environ.get(
    "BACKUP_MONITOR_SQLITE_DIRECTORY", "/var/backups/application/daily"
))
ARCHIVE_MOUNT = Path(os.environ.get(
    "BACKUP_MONITOR_ARCHIVE_MOUNT", "/mnt/external-backup"
))
ARCHIVE_FSTYPE = os.environ.get("BACKUP_MONITOR_ARCHIVE_FSTYPE", "ext4")
PROFILES = {
    "sqlite": {
        "directory": SQLITE_DIRECTORY,
        "job": "application-backup.service",
        "timer": "application-backup.timer",
        "manifest": re.compile(r"application-(\d{8}T\d{6}Z)\.json"),
    },
    "archive": {
        "directory": Path(os.environ.get(
            "BACKUP_MONITOR_ARCHIVE_DIRECTORY", str(ARCHIVE_MOUNT / "application")
        )),
        "job": "application-archive-backup.service",
        "timer": "application-archive-backup.timer",
        "manifest": re.compile(
            r"application-archive-(\d{8}T\d{6}Z)\.tar\.zst\.json"
        ),
    },
}
MAX_JSON_BYTES = 64 * 1024
MAX_MANIFESTS = 256
MAX_MONITOR_EVENTS = 64
MAX_EVENT_SEQUENCE = 2**53 - 1
EVENT_KINDS = {"backup_alert", "backup_recovery"}
EVENT_ID = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
ISSUES = {
    "backup_job_unavailable", "backup_job_failed", "backup_timer_inactive",
    "independent_disk_unavailable", "copy_missing", "inventory_limit_exceeded",
    "invalid_backup_metadata", "future_copy_timestamp", "copy_stale",
    "backup_directory_unavailable_or_unsafe", "inspection_unavailable",
}


class MonitorError(Exception):
    """Only a fixed public reason code escapes a failed private inspection."""


def timestamp(value):
    if not isinstance(value, str):
        raise MonitorError("invalid_timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise MonitorError("invalid_timestamp") from None
    if result.tzinfo is None:
        raise MonitorError("invalid_timestamp")
    try:
        return result.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise MonitorError("invalid_timestamp") from None


def private_file(info, uid):
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
        raise MonitorError("unsafe_file")


def directory_descriptor(path):
    """Hold a descriptor and reject symlinks in every path component."""
    if not path.is_absolute() or ".." in path.parts:
        raise MonitorError("unsafe_directory")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in path.parts[1:]:
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def private_directory(path, uid):
    fd = directory_descriptor(path)
    info = os.fstat(fd)
    if info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o700:
        os.close(fd)
        raise MonitorError("unsafe_directory")
    return fd


def document_at(fd, name, uid):
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        private_file(os.fstat(descriptor), uid)
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read(MAX_JSON_BYTES + 1)
        if len(data) > MAX_JSON_BYTES:
            raise MonitorError("oversize_json")
        try:
            value = json.loads(data, object_pairs_hook=unique_object)
        except (ValueError, UnicodeError, RecursionError):
            raise MonitorError("invalid_json") from None
        if not isinstance(value, dict):
            raise MonitorError("invalid_json")
        return value
    finally:
        os.close(descriptor)


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise MonitorError("invalid_json")
        value[key] = item
    return value


def systemd_properties(unit, properties):
    completed = subprocess.run(
        ["systemctl", "show", *("--property=" + key for key in properties), unit],
        capture_output=True, text=True, timeout=5, check=False,
    )
    if completed.returncode or len(completed.stdout) > MAX_JSON_BYTES:
        raise MonitorError("systemd_unavailable")
    values = {}
    for line in completed.stdout.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key not in properties or key in values:
            raise MonitorError("systemd_unavailable")
        values[key] = value
    if set(values) != set(properties):
        raise MonitorError("systemd_unavailable")
    return values


def independent_disk_matches(directory):
    expected_uuid = os.environ.get("BACKUP_MONITOR_ARCHIVE_UUID", "")
    if not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", expected_uuid):
        return False
    expected = [{
        "target": str(ARCHIVE_MOUNT),
        "fstype": ARCHIVE_FSTYPE,
        "uuid": expected_uuid.lower(),
    }]
    for argument, path in [("--mountpoint", ARCHIVE_MOUNT), ("--target", directory)]:
        completed = subprocess.run(
            ["findmnt", "-J", "-o", "TARGET,FSTYPE,UUID", argument, str(path)],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if completed.returncode or len(completed.stdout) > MAX_JSON_BYTES:
            return False
        try:
            if json.loads(completed.stdout).get("filesystems") != expected:
                return False
        except (ValueError, AttributeError, RecursionError):
            return False
    return True


def inspect_copies(profile, directory, now, uid):
    fd = private_directory(directory, uid)
    try:
        names = [name for name in os.listdir(fd) if PROFILES[profile]["manifest"].fullmatch(name)]
        if not names:
            return None, ["copy_missing"]
        if len(names) > MAX_MANIFESTS:
            return None, ["inventory_limit_exceeded"]
        dates = []
        invalid = False
        future = False
        for name in names:
            try:
                document = document_at(fd, name, uid)
                artifact = name.removesuffix(".json") if profile == "archive" else name.removesuffix(".json") + ".sqlite3"
                info = os.stat(artifact, dir_fd=fd, follow_symlinks=False)
                private_file(info, uid)
                if (type(document.get("bytes")) is not int or document["bytes"] <= 0
                        or document["bytes"] != info.st_size
                        or not isinstance(document.get("sha256"), str)
                        or not re.fullmatch(r"[0-9a-f]{64}", document["sha256"])):
                    raise MonitorError("invalid_manifest")
                if profile == "sqlite":
                    if document.get("file") != artifact or type(document.get("schema")) is not int or document["schema"] < 1:
                        raise MonitorError("invalid_manifest")
                    token = PROFILES[profile]["manifest"].fullmatch(name).group(1)
                    created = datetime.strptime(token, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                else:
                    if document.get("archive") != artifact:
                        raise MonitorError("invalid_manifest")
                    created = timestamp(document.get("created_at"))
                if created > now:
                    future = True
                else:
                    dates.append(created)
            except (OSError, MonitorError, ValueError):
                invalid = True
        issues = (["invalid_backup_metadata"] if invalid else []) + (["future_copy_timestamp"] if future else [])
        return max(dates) if dates else None, issues
    finally:
        os.close(fd)


def inspect(profile, directory, now, uid, job, timer, max_age_hours=36, disk_matches=True, reported_failure=False):
    if (profile not in PROFILES or now.tzinfo is None or type(max_age_hours) not in {int, float}
            or not 1 <= max_age_hours <= 168
            or (type(max_age_hours) is float and not math.isfinite(max_age_hours))):
        raise MonitorError("invalid_configuration")
    issues = []
    if job.get("LoadState") != "loaded":
        issues.append("backup_job_unavailable")
    elif job.get("Result") != "success" or job.get("ActiveState") == "failed":
        issues.append("backup_job_failed")
    if reported_failure and "backup_job_failed" not in issues:
        issues.append("backup_job_failed")
    if timer.get("LoadState") != "loaded" or timer.get("ActiveState") != "active" or timer.get("UnitFileState") not in {"enabled", "enabled-runtime"}:
        issues.append("backup_timer_inactive")
    if profile == "archive" and not disk_matches:
        issues.append("independent_disk_unavailable")
        latest = None
    else:
        try:
            latest, copy_issues = inspect_copies(profile, directory, now, uid)
            issues.extend(copy_issues)
        except (OSError, MonitorError):
            latest = None
            issues.append("backup_directory_unavailable_or_unsafe")
    age = (now - latest).total_seconds() if latest is not None else None
    if age is not None and age > max_age_hours * 3600:
        issues.append("copy_stale")
    return {
        "schema_version": 1, "profile": profile,
        "checked_at": now.astimezone(timezone.utc).isoformat(),
        "status": "alert" if issues else "healthy", "issues": sorted(set(issues)),
        "latest_copy_at": latest.isoformat() if latest else None,
        "age_seconds": age, "max_age_hours": max_age_hours,
        "artifact_content_hash_verified": False, "restore_verified": False,
        "notification_delivered": False,
    }


def validate_previous(previous, report):
    version = previous.get("schema_version")
    base_fields = set(report)
    state_fields = {"transition", "alert_since", "last_recovered_at"}
    if (type(version) is not int or version not in {1, 2}
            or set(previous) != base_fields | state_fields | ({"journal_id", "event_sequence", "events"} if version == 2 else set())
            or version == 1 and report.get("schema_version") != 1
            or version == 2 and report.get("schema_version") not in {1, 2}
            or previous.get("profile") != report["profile"]
            or not isinstance(previous.get("status"), str) or previous["status"] not in {"healthy", "alert"}
            or not isinstance(previous.get("issues"), list)
            or any(not isinstance(item, str) or item not in ISSUES for item in previous["issues"])
            or len(previous["issues"]) != len(set(previous["issues"]))
            or (previous["status"] == "alert") != bool(previous["issues"])
            or not isinstance(previous.get("transition"), str) or previous["transition"] not in {"unchanged", "alert_started", "alert_changed", "recovered"}
            or any(previous.get(key) is not False for key in ["artifact_content_hash_verified", "restore_verified", "notification_delivered"])):
        raise MonitorError("invalid_previous_state")
    if type(previous["schema_version"]) is not int or previous["schema_version"] != version:
        raise MonitorError("invalid_previous_state")
    for key in ["checked_at", "latest_copy_at", "alert_since", "last_recovered_at"]:
        if key == "checked_at" or previous[key] is not None:
            timestamp(previous[key])
    # No representable datetime interval can exceed the full year1..9999 span.
    for key, minimum, maximum in [("age_seconds", 0, 315537897600), ("max_age_hours", 1, 168)]:
        value = previous[key]
        if key == "age_seconds" and value is None:
            continue
        if (type(value) not in {int, float} or not minimum <= value <= maximum
                or (type(value) is float and not math.isfinite(value))):
            raise MonitorError("invalid_previous_state")
    checked = timestamp(previous["checked_at"])
    for key in ["latest_copy_at", "alert_since", "last_recovered_at"]:
        if previous[key] is not None and timestamp(previous[key]) > checked:
            raise MonitorError("invalid_previous_state")
    if ((previous["status"] == "alert") != (previous["alert_since"] is not None)
            or (previous["status"] == "alert" and previous["last_recovered_at"] is not None
                and timestamp(previous["last_recovered_at"]) > timestamp(previous["alert_since"]))):
        raise MonitorError("invalid_previous_state")
    if version == 2:
        journal_id = previous["journal_id"]
        sequence = previous["event_sequence"]
        events = previous["events"]
        if (not isinstance(journal_id, str) or not EVENT_ID.fullmatch(journal_id)
                or type(sequence) is not int or not 0 <= sequence <= MAX_EVENT_SEQUENCE
                or not isinstance(events, list) or len(events) > MAX_MONITOR_EVENTS
                or len(events) != min(sequence, MAX_MONITOR_EVENTS)):
            raise MonitorError("invalid_previous_state")
        prior_sequence = sequence - len(events)
        prior_time = None
        ids = set()
        for event in events:
            if (not isinstance(event, dict) or set(event) != {"id", "sequence", "kind", "at"}
                    or not isinstance(event["id"], str) or not EVENT_ID.fullmatch(event["id"])
                    or event["id"] == journal_id or event["id"] in ids
                    or type(event["sequence"]) is not int or event["sequence"] != prior_sequence + 1
                    or not isinstance(event["kind"], str) or event["kind"] not in EVENT_KINDS):
                raise MonitorError("invalid_previous_state")
            event_at = timestamp(event["at"])
            if event_at > checked or (prior_time is not None and event_at < prior_time):
                raise MonitorError("invalid_previous_state")
            ids.add(event["id"])
            prior_sequence = event["sequence"]
            prior_time = event_at
        if (prior_sequence != sequence
                or bool(events) and events[-1]["kind"] != ("backup_alert" if previous["status"] == "alert" else "backup_recovery")):
            raise MonitorError("invalid_previous_state")


def persist(directory, report, uid):
    try:
        fd = private_directory(directory, uid)
    except FileNotFoundError:
        # Validate the parent before creating anything; mkdir(path) could follow a symlink.
        parent = directory_descriptor(directory.parent)
        try:
            info = os.fstat(parent)
            if info.st_uid != uid or stat.S_IMODE(info.st_mode) & 0o022:
                raise MonitorError("unsafe_state_parent")
            try:
                os.mkdir(directory.name, 0o700, dir_fd=parent)
            except FileExistsError:
                pass
        finally:
            os.close(parent)
        fd = private_directory(directory, uid)
    lock = None
    temporary = None
    try:
        lock = os.open(".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=fd)
        private_file(os.fstat(lock), uid)
        fcntl.flock(lock, fcntl.LOCK_EX)
        name = report["profile"] + ".json"
        try:
            previous = document_at(fd, name, uid)
        except FileNotFoundError:
            previous = None
        if previous is not None:
            validate_previous(previous, report)
            # A poll started before OnFailure must not erase its newer alert.
            if timestamp(previous.get("checked_at")) > timestamp(report["checked_at"]):
                return {**previous, "transition": "superseded_check"}
        changed = previous is None or previous["status"] != report["status"] or previous["issues"] != report["issues"]
        transition = "unchanged"
        if report["status"] == "alert" and changed:
            transition = "alert_started" if previous is None or previous["status"] != "alert" else "alert_changed"
        elif report["status"] == "healthy" and previous is not None and previous["status"] == "alert":
            transition = "recovered"
        result = {
            **report, "schema_version": 2, "transition": transition,
            "alert_since": (previous.get("alert_since") if previous and previous["status"] == "alert" else report["checked_at"]) if report["status"] == "alert" else None,
            "last_recovered_at": report["checked_at"] if transition == "recovered" else previous.get("last_recovered_at") if previous else None,
        }
        if previous is not None and previous["schema_version"] == 2:
            journal_id = previous["journal_id"]
            sequence = previous["event_sequence"]
            events = list(previous["events"])
        else:
            # A v1 report has no reliable event history; start a fresh journal without reconstructing it.
            journal_id = uuid.uuid4().hex
            sequence = 0
            events = []
        event_kind = {"alert_started": "backup_alert", "alert_changed": "backup_alert", "recovered": "backup_recovery"}.get(transition)
        if event_kind is not None:
            if sequence >= MAX_EVENT_SEQUENCE:
                raise MonitorError("event_sequence_exhausted")
            event_id = uuid.uuid4().hex
            while event_id == journal_id or any(event["id"] == event_id for event in events):
                event_id = uuid.uuid4().hex
            sequence += 1
            events.append({"id": event_id, "sequence": sequence, "kind": event_kind,
                           "at": timestamp(report["checked_at"]).isoformat().replace("+00:00", "Z")})
            events = events[-MAX_MONITOR_EVENTS:]
        result.update({"journal_id": journal_id, "event_sequence": sequence, "events": events})
        temporary = "." + report["profile"] + "-" + uuid.uuid4().hex + ".tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(result, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
        temporary = None
        os.fsync(fd)
        return result
    finally:
        try:
            if temporary is not None:
                os.unlink(temporary, dir_fd=fd)
        finally:
            if lock is not None:
                os.close(lock)
            os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, choices=PROFILES)
    parser.add_argument("--state-directory", type=Path, default=Path("/var/lib/application-backup-monitor"))
    parser.add_argument("--max-age-hours", type=float, default=36)
    parser.add_argument("--reported-failure", action="store_true")
    args = parser.parse_args(argv)
    if os.geteuid() != 0 or not math.isfinite(args.max_age_hours) or not 1 <= args.max_age_hours <= 168:
        print(json.dumps({"profile": args.profile, "status": "error", "reason": "invalid_configuration", "notification_delivered": False}))
        return 2
    now = datetime.now(timezone.utc)
    inspection_failed = False
    try:
        profile = PROFILES[args.profile]
        job = systemd_properties(profile["job"], ["LoadState", "ActiveState", "Result"])
        timer = systemd_properties(profile["timer"], ["LoadState", "ActiveState", "UnitFileState"])
        disk_matches = args.profile != "archive" or independent_disk_matches(profile["directory"])
        report = inspect(args.profile, profile["directory"], now, 0, job, timer, args.max_age_hours, disk_matches, args.reported_failure)
    except (OSError, MonitorError, subprocess.SubprocessError):
        # Never include subprocess stdout/stderr, manifests or paths in a diagnostic.
        inspection_failed = True
        report = {
            "schema_version": 1, "profile": args.profile, "checked_at": now.isoformat(),
            "status": "alert", "issues": ["inspection_unavailable"] + (["backup_job_failed"] if args.reported_failure else []), "latest_copy_at": None,
            "age_seconds": None, "max_age_hours": args.max_age_hours,
            "artifact_content_hash_verified": False, "restore_verified": False, "notification_delivered": False,
        }
    try:
        report = persist(args.state_directory, report, 0)
    except (OSError, MonitorError, ValueError):
        print(json.dumps({"profile": args.profile, "status": "error", "reason": "state_unavailable", "notification_delivered": False}))
        return 2
    print(json.dumps(report, sort_keys=True))
    if inspection_failed:
        return 2
    return 1 if report["status"] == "alert" else 0


if __name__ == "__main__":
    raise SystemExit(main())
