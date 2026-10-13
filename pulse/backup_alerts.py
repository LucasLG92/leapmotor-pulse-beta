"""Bounded, read-only consumer contract for private backup-monitor metadata."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat


MAX_REPORT_BYTES = 64 * 1024
MAX_MONITOR_EVENTS = 64
ISSUES = frozenset({
    "backup_job_unavailable", "backup_job_failed", "backup_timer_inactive",
    "independent_disk_unavailable", "copy_missing", "inventory_limit_exceeded",
    "invalid_backup_metadata", "future_copy_timestamp", "copy_stale",
    "backup_directory_unavailable_or_unsafe", "inspection_unavailable",
})
EVENT_KINDS = frozenset({"backup_alert", "backup_recovery"})
_ID = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_PROFILE = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z", re.ASCII)
REPORT_FIELDS = frozenset({
    "schema_version", "profile", "checked_at", "status", "issues",
    "latest_copy_at", "age_seconds", "max_age_hours",
    "artifact_content_hash_verified", "restore_verified", "notification_delivered",
    "transition", "alert_since", "last_recovered_at", "journal_id",
    "event_sequence", "events",
})


@dataclass(frozen=True)
class BackupAlertsConfig:
    enabled: bool = False
    state_directory: str = field(default="", repr=False)
    owner_id: str = field(default="", repr=False)
    host_profile: str = field(default="host", repr=False)
    poll_seconds: int = 60
    stale_after_seconds: int = 7200

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise ValueError("La activación de avisos de copias debe ser booleana.")
        if (type(self.poll_seconds) is not int or not 30 <= self.poll_seconds <= 300
                or type(self.stale_after_seconds) is not int or not 3600 <= self.stale_after_seconds <= 86400):
            raise ValueError("Los intervalos de los avisos de copias no son válidos.")
        if not isinstance(self.host_profile, str) or not _PROFILE.fullmatch(self.host_profile) or self.host_profile == "sqlite":
            raise ValueError("El perfil de la copia del host no es válido.")
        if (not isinstance(self.state_directory, str) or not isinstance(self.owner_id, str)
                or len(self.state_directory) > 4096 or len(self.owner_id) > 255
                or any(ord(c) < 32 or ord(c) == 127 for c in self.state_directory + self.owner_id)):
            raise ValueError("La configuración de avisos de copias no es válida.")
        if self.state_directory and (not Path(self.state_directory).is_absolute() or ".." in Path(self.state_directory).parts):
            raise ValueError("El directorio de estados debe ser una ruta absoluta explícita.")
        if self.enabled and (not self.state_directory or not self.owner_id or self.owner_id != self.owner_id.strip()):
            raise ValueError("Los avisos de copias requieren directorio y propietario explícitos.")

    @property
    def scope(self) -> str:
        return hashlib.sha256((self.state_directory + "\n" + self.host_profile + "\n" + self.owner_id).encode()).hexdigest()

    @classmethod
    def from_env(cls):
        value = os.environ.get("PULSE_BACKUP_ALERTS_ENABLED", "false")
        if value not in {"true", "false"}:
            raise ValueError("PULSE_BACKUP_ALERTS_ENABLED debe ser true o false.")
        if value == "false":
            return cls()
        try:
            return cls(enabled=True, state_directory=os.environ.get("PULSE_BACKUP_ALERTS_STATE_DIRECTORY", ""),
                       owner_id=os.environ.get("PULSE_BACKUP_ALERTS_OWNER_ID", ""),
                       host_profile=os.environ.get("PULSE_BACKUP_ALERTS_HOST_PROFILE", "host"),
                       poll_seconds=int(os.environ.get("PULSE_BACKUP_ALERTS_POLL_SECONDS", "60")),
                       stale_after_seconds=int(os.environ.get("PULSE_BACKUP_ALERTS_STALE_AFTER_SECONDS", "7200")))
        except (ValueError, TypeError):
            raise ValueError("La configuración de avisos de copias no es válida.") from None


def _instant(value):
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None or result.year < 2014:
        raise ValueError
    return result.astimezone(timezone.utc)


def _iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _number(value, maximum, *, nullable=False):
    if nullable and value is None:
        return
    if type(value) not in {int, float} or not math.isfinite(value) or not 0 <= value <= maximum:
        raise ValueError


def normalize_report(raw: dict, expected_profile: str, logical_profile: str, now: datetime, stale_after_seconds: int) -> dict:
    """Return only fixed codes and validated event metadata; never report paths."""
    if not isinstance(raw, dict) or set(raw) != REPORT_FIELDS or type(raw["schema_version"]) is not int or raw["schema_version"] != 2:
        raise ValueError
    if raw["profile"] != expected_profile or not isinstance(raw["status"], str) or raw["status"] not in {"healthy", "alert"}:
        raise ValueError
    issues = raw["issues"]
    if (not isinstance(issues, list) or any(not isinstance(x, str) or x not in ISSUES for x in issues)
            or len(issues) != len(set(issues)) or (raw["status"] == "alert") != bool(issues)):
        raise ValueError
    if any(raw[k] is not False for k in ("artifact_content_hash_verified", "restore_verified", "notification_delivered")):
        raise ValueError
    if raw["transition"] not in {"unchanged", "alert_started", "alert_changed", "recovered"}:
        raise ValueError
    checked = _instant(raw["checked_at"])
    if (checked - now).total_seconds() > 60:
        raise ValueError
    for key in ("latest_copy_at", "alert_since", "last_recovered_at"):
        if raw[key] is not None and _instant(raw[key]) > checked:
            raise ValueError
    if (raw["status"] == "alert") != (raw["alert_since"] is not None):
        raise ValueError
    if raw["status"] == "alert" and raw["last_recovered_at"] is not None and _instant(raw["last_recovered_at"]) > _instant(raw["alert_since"]):
        raise ValueError
    _number(raw["age_seconds"], 315537897600, nullable=True)
    _number(raw["max_age_hours"], 168)
    if raw["max_age_hours"] < 1:
        raise ValueError
    if not isinstance(raw["journal_id"], str) or not _ID.fullmatch(raw["journal_id"]):
        raise ValueError
    sequence = raw["event_sequence"]
    events = raw["events"]
    if (type(sequence) is not int or not 0 <= sequence <= 2**53 - 1
            or not isinstance(events, list) or len(events) != min(sequence, MAX_MONITOR_EVENTS)):
        raise ValueError
    clean = []
    prior_sequence = max(0, sequence - len(events))
    prior_time = None
    ids = set()
    for event in events:
        if (not isinstance(event, dict) or set(event) != {"id", "sequence", "kind", "at"}
                or not isinstance(event["id"], str) or not _ID.fullmatch(event["id"])
                or event["id"] == raw["journal_id"] or event["id"] in ids
                or not isinstance(event["kind"], str) or event["kind"] not in EVENT_KINDS
                or type(event["sequence"]) is not int or event["sequence"] != prior_sequence + 1):
            raise ValueError
        at = _instant(event["at"])
        if at > checked or (prior_time is not None and at < prior_time):
            raise ValueError
        clean.append({"id": event["id"], "sequence": event["sequence"], "kind": event["kind"], "at": _iso(at)})
        prior_sequence, prior_time = event["sequence"], at
        ids.add(event["id"])
    if prior_sequence != sequence:
        raise ValueError
    if clean and clean[-1]["kind"] != ("backup_alert" if raw["status"] == "alert" else "backup_recovery"):
        raise ValueError
    reason = "stale" if (now - checked).total_seconds() > stale_after_seconds else None
    state = {"profile": logical_profile, "status": "unavailable" if reason else raw["status"],
             "checked_at": _iso(checked), "journal_id": raw["journal_id"], "sequence": sequence,
             "events": clean, "reason_code": reason}
    state["fingerprint"] = hashlib.sha256(json.dumps({"status": state["status"], "issues": sorted(issues),
        "reason": reason, "journal": raw["journal_id"]}, sort_keys=True).encode()).hexdigest()
    return state


def _private_directory(path: Path) -> int:
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:]:
            following = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _document(directory: int, name: str) -> dict:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size > MAX_REPORT_BYTES):
            raise ValueError
        with os.fdopen(fd, "rb", closefd=False) as stream:
            content = stream.read(MAX_REPORT_BYTES + 1)
        if len(content) > MAX_REPORT_BYTES:
            raise ValueError
        return json.loads(content)
    finally:
        os.close(fd)


def read_monitor_reports(config: BackupAlertsConfig, now: datetime) -> tuple[dict, ...]:
    """Blocking filesystem I/O: callers run it in a thread; disabled means no I/O."""
    if not config.enabled:
        return ()
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("La comprobación requiere una fecha con zona horaria.")
    now = _instant(_iso(now))
    result = []
    for logical, actual in (("sqlite", "sqlite"), ("host", config.host_profile)):
        directory = None
        try:
            directory = _private_directory(Path(config.state_directory))
            raw = _document(directory, actual + ".json")
            report = normalize_report(raw, actual, logical, now, config.stale_after_seconds)
        except FileNotFoundError:
            reason = "missing"
        except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
            reason = "invalid"
        else:
            result.append(report)
            continue
        finally:
            if directory is not None:
                os.close(directory)
        result.append({"profile": logical, "status": "unavailable", "checked_at": None,
                       "journal_id": None, "sequence": 0, "events": [], "reason_code": reason,
                       "fingerprint": hashlib.sha256(reason.encode()).hexdigest()})
    return tuple(result)
