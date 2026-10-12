"""Original synthetic metadata checks; never inspect an operator backup."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

import pytest

from pulse.backup_alerts import BackupAlertsConfig, normalize_report, read_monitor_reports


NOW = datetime(2026, 10, 13, 8, tzinfo=timezone.utc)


def iso(value):
    return value.isoformat().replace("+00:00", "Z")


def report(profile="sqlite", *, events=False):
    return {
        "schema_version": 2, "profile": profile, "checked_at": iso(NOW),
        "status": "healthy", "issues": [], "latest_copy_at": iso(NOW - timedelta(hours=4)),
        "age_seconds": 14400, "max_age_hours": 36,
        "artifact_content_hash_verified": False, "restore_verified": False, "notification_delivered": False,
        "transition": "recovered" if events else "unchanged", "alert_since": None,
        "last_recovered_at": iso(NOW) if events else None,
        "journal_id": "a" * 32, "event_sequence": 2 if events else 0,
        "events": [
            {"id": "b" * 32, "sequence": 1, "kind": "backup_alert", "at": iso(NOW - timedelta(minutes=2))},
            {"id": "c" * 32, "sequence": 2, "kind": "backup_recovery", "at": iso(NOW)},
        ] if events else [],
    }


def configured(tmp_path, **values):
    directory = tmp_path / "reports"
    directory.mkdir(mode=0o700)
    return BackupAlertsConfig(enabled=True, state_directory=str(directory), owner_id="synthetic-owner", **values)


def write(config, name, value):
    path = Path(config.state_directory) / name
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return path


def test_default_disabled_does_not_touch_filesystem(monkeypatch):
    monkeypatch.setattr(os, "open", lambda *_args, **_kwargs: pytest.fail("filesystem opened"))
    assert read_monitor_reports(BackupAlertsConfig(), NOW) == ()
    monkeypatch.setenv("PULSE_BACKUP_ALERTS_ENABLED", "false")
    monkeypatch.setenv("PULSE_BACKUP_ALERTS_STATE_DIRECTORY", "/ignored/private-path")
    assert BackupAlertsConfig.from_env() == BackupAlertsConfig()


def test_config_is_explicit_bounded_and_does_not_echo_values(monkeypatch, tmp_path):
    config = configured(tmp_path)
    assert config.owner_id not in repr(config) and config.state_directory not in repr(config)
    assert len(config.scope) == 64
    for values in [
        {"enabled": 1}, {"enabled": True}, {"poll_seconds": True}, {"poll_seconds": 29},
        {"stale_after_seconds": 1}, {"host_profile": "sqlite"}, {"host_profile": "../host"},
        {"owner_id": []}, {"owner_id": "x" * 256}, {"state_directory": "relative"}, {"state_directory": "/safe/../other"},
        {"enabled": True, "state_directory": "/fake", "owner_id": " synthetic-owner"},
    ]:
        with pytest.raises(ValueError):
            BackupAlertsConfig(**values)
    monkeypatch.setenv("PULSE_BACKUP_ALERTS_ENABLED", "TRUE")
    with pytest.raises(ValueError):
        BackupAlertsConfig.from_env()
    monkeypatch.setenv("PULSE_BACKUP_ALERTS_ENABLED", "true")
    monkeypatch.setenv("PULSE_BACKUP_ALERTS_STATE_DIRECTORY", config.state_directory)
    monkeypatch.setenv("PULSE_BACKUP_ALERTS_OWNER_ID", config.owner_id)
    assert BackupAlertsConfig.from_env().scope == config.scope
    monkeypatch.setenv("PULSE_BACKUP_ALERTS_POLL_SECONDS", "private-invalid-value")
    with pytest.raises(ValueError) as error:
        BackupAlertsConfig.from_env()
    assert "private-invalid-value" not in str(error.value)


def test_valid_journal_retains_overwritten_failure_and_recovery(tmp_path):
    config = configured(tmp_path)
    write(config, "sqlite.json", report(events=True))
    write(config, "archive.json", report("archive"))
    reports = read_monitor_reports(config, NOW)
    assert [r["profile"] for r in reports] == ["sqlite", "host"]
    assert [e["kind"] for e in reports[0]["events"]] == ["backup_alert", "backup_recovery"]
    assert reports[0]["status"] == reports[1]["status"] == "healthy"
    assert config.state_directory not in json.dumps(reports)
    assert "latest_copy_at" not in reports[0] and "issues" not in reports[0]
    later = report(events=True)
    later["checked_at"] = iso(NOW + timedelta(minutes=1))
    assert normalize_report(later, "sqlite", "sqlite", NOW + timedelta(minutes=1), 7200)["fingerprint"] == reports[0]["fingerprint"]


def test_archive_profile_is_normalized_and_only_expected_two_files_are_read(tmp_path):
    config = configured(tmp_path, host_profile="archive")
    write(config, "sqlite.json", report())
    write(config, "archive.json", report("archive"))
    (Path(config.state_directory) / "unrelated.json").symlink_to("/must-not-be-read")
    reports = read_monitor_reports(config, NOW)
    assert reports[1]["profile"] == "host"
    assert "archive" not in json.dumps(reports)


def test_absent_stale_invalid_and_future_reports_have_fixed_reasons(tmp_path):
    config = configured(tmp_path)
    assert all(r["reason_code"] == "missing" for r in read_monitor_reports(config, NOW))
    write(config, "sqlite.json", report())
    stale = read_monitor_reports(config, NOW + timedelta(seconds=7201))[0]
    assert stale["status"] == "unavailable" and stale["reason_code"] == "stale"
    future = report()
    future["checked_at"] = iso(NOW + timedelta(seconds=61))
    write(config, "sqlite.json", future)
    assert read_monitor_reports(config, NOW)[0]["reason_code"] == "invalid"
    legacy = report()
    legacy["schema_version"] = 1
    write(config, "sqlite.json", legacy)
    assert read_monitor_reports(config, NOW)[0]["reason_code"] == "invalid"


@pytest.mark.parametrize("change", [
    {"schema_version": True}, {"status": "unknown"}, {"status": []},
    {"profile": "other"}, {"issues": ["copy_missing"]}, {"issues": ["private-invalid-value"]},
    {"transition": "superseded_check"}, {"checked_at": "2026-10-13T08:00:00"},
    {"latest_copy_at": "2099-01-01T00:00:00Z"}, {"age_seconds": True},
    {"age_seconds": float("inf")}, {"max_age_hours": 0}, {"max_age_hours": 169},
    {"restore_verified": True}, {"notification_delivered": True}, {"artifact_content_hash_verified": True},
    {"journal_id": "bad"}, {"journal_id": []}, {"event_sequence": True},
    {"event_sequence": 2**53}, {"event_sequence": 1}, {"events": {}},
    {"alert_since": iso(NOW)}, {"private_field": "private-invalid-value"},
])
def test_malformed_metadata_cannot_be_promoted(tmp_path, change):
    config = configured(tmp_path)
    raw = report()
    raw.update(change)
    write(config, "sqlite.json", raw)
    item = read_monitor_reports(config, NOW)[0]
    assert item["status"] == "unavailable" and item["reason_code"] == "invalid"
    assert "private-invalid-value" not in json.dumps(item)


def test_journal_identity_order_bounds_and_timestamps(tmp_path):
    config = configured(tmp_path)
    cases = []
    for key, value in [("id", "c" * 32), ("sequence", 2), ("kind", "test"), ("at", iso(NOW + timedelta(seconds=1)))]:
        raw = report(events=True)
        raw["events"][0][key] = value
        cases.append(raw)
    raw = report(events=True)
    raw["events"].reverse()
    cases.append(raw)
    raw = report(events=True)
    raw["events"][0]["private_field"] = "private-invalid-value"
    cases.append(raw)
    raw = report(events=True)
    raw["events"] *= 33
    cases.append(raw)
    for raw in cases:
        write(config, "sqlite.json", raw)
        assert read_monitor_reports(config, NOW)[0]["reason_code"] == "invalid"
    retained = report(events=True)
    retained["event_sequence"] = 100
    retained["events"] = [{"id": f"{number:032x}", "sequence": number,
                          "kind": "backup_recovery" if number == 100 else "backup_alert",
                          "at": iso(NOW)} for number in range(37, 101)]
    write(config, "sqlite.json", retained)
    assert read_monitor_reports(config, NOW)[0]["sequence"] == 100


def test_no_follow_permissions_hardlinks_and_bounded_json(tmp_path):
    config = configured(tmp_path)
    directory = Path(config.state_directory)
    source = write(config, "sqlite.json", report())
    outside = tmp_path / "outside.json"
    source.rename(outside)
    source.symlink_to(outside)
    assert read_monitor_reports(config, NOW)[0]["reason_code"] == "invalid"
    source.unlink()
    os.link(outside, source)
    assert read_monitor_reports(config, NOW)[0]["reason_code"] == "invalid"
    source.unlink()
    source = write(config, "sqlite.json", report())
    source.chmod(0o644)
    assert read_monitor_reports(config, NOW)[0]["reason_code"] == "invalid"
    source.chmod(0o600)
    source.write_bytes(b" " * (65536 + 1))
    assert read_monitor_reports(config, NOW)[0]["reason_code"] == "invalid"
    source.write_text("private-invalid-value")
    assert "private-invalid-value" not in json.dumps(read_monitor_reports(config, NOW))
    directory.chmod(0o755)
    assert all(r["reason_code"] == "invalid" for r in read_monitor_reports(config, NOW))
    directory.chmod(0o700)
    link = tmp_path / "linked-directory"
    link.symlink_to(directory, target_is_directory=True)
    linked = BackupAlertsConfig(enabled=True, state_directory=str(link), owner_id="synthetic-owner")
    assert all(r["reason_code"] == "invalid" for r in read_monitor_reports(linked, NOW))


def test_unreadable_or_special_file_does_not_block(tmp_path, monkeypatch):
    config = configured(tmp_path)
    source = Path(config.state_directory) / "sqlite.json"
    os.mkfifo(source, 0o600)
    assert read_monitor_reports(config, NOW)[0]["reason_code"] == "invalid"
    source.unlink()
    source = write(config, "sqlite.json", report())
    original = os.open

    def denied(path, flags, *args, **kwargs):
        if path == "sqlite.json":
            raise PermissionError("private-invalid-value")
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", denied)
    assert "private-invalid-value" not in json.dumps(read_monitor_reports(config, NOW))


def test_clock_must_be_aware_and_journal_matches_producer_retention(tmp_path):
    config = configured(tmp_path)
    with pytest.raises(ValueError):
        read_monitor_reports(config, NOW.replace(tzinfo=None))
    raw = report(events=True)
    raw["events"] = raw["events"][1:]
    write(config, "sqlite.json", raw)
    assert read_monitor_reports(config, NOW)[0]["reason_code"] == "invalid"
    raw = report(events=True)
    raw["events"][0]["id"] = raw["journal_id"]
    write(config, "sqlite.json", raw)
    assert read_monitor_reports(config, NOW)[0]["reason_code"] == "invalid"
    raw = report(events=True)
    raw["events"][-1]["kind"] = "backup_alert"
    write(config, "sqlite.json", raw)
    assert read_monitor_reports(config, NOW)[0]["reason_code"] == "invalid"


def test_actual_producer_journal_is_consumable_after_later_healthy_poll(tmp_path):
    import importlib.util

    module_path = Path(__file__).resolve().parents[1] / "deploy/community/backup-monitor.py"
    if not module_path.is_file():
        pytest.fail("Community backup monitor source is missing.")
    spec = importlib.util.spec_from_file_location("pulse_monitor_reader_contract_fixture", module_path)
    producer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(producer)
    config = configured(tmp_path, host_profile="archive")
    root = Path(config.state_directory)
    base = report("archive")
    for field in ("transition", "alert_since", "last_recovered_at", "journal_id", "event_sequence", "events"):
        base.pop(field)
    base["schema_version"] = 1
    first = dict(base, status="alert", issues=["copy_missing"], latest_copy_at=None, age_seconds=None)
    producer.persist(root, first, os.geteuid())
    recovered = dict(base, checked_at=iso(NOW + timedelta(minutes=1)))
    producer.persist(root, recovered, os.geteuid())
    latest = dict(base, checked_at=iso(NOW + timedelta(minutes=2)))
    producer.persist(root, latest, os.geteuid())
    actual = read_monitor_reports(config, NOW + timedelta(minutes=2))[1]
    assert actual["status"] == "healthy" and actual["reason_code"] is None
    assert [event["kind"] for event in actual["events"]] == ["backup_alert", "backup_recovery"]
