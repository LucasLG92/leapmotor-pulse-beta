"""Actual metadata producer → reader → SQLite → encryption, without delivery."""
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
import http_ece
import pytest

from pulse.backup_alerts import BackupAlertsConfig, read_monitor_reports
from pulse.domain import Store
from pulse.push_crypto import PushCrypto
from pulse.push_policy import PushConfig, encode_url64


def test_overwritten_failure_and_recovery_reach_community_once_only_payloads(tmp_path):
    script = Path(__file__).resolve().parents[1] / "deploy/community/backup-monitor.py"
    if not script.is_file():
        pytest.fail("Community backup monitor source is missing.")
    spec = importlib.util.spec_from_file_location("backup_monitor_pipeline_fixture", script)
    producer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(producer)
    directory = tmp_path / "reports"
    directory.mkdir(mode=0o700)
    config = BackupAlertsConfig(enabled=True, state_directory=str(directory),
                               owner_id="pipeline-owner", host_profile="archive")
    now = datetime(2026, 10, 13, 9, tzinfo=timezone.utc)
    iso = lambda value: value.isoformat().replace("+00:00", "Z")
    base = {"schema_version": 1, "profile": "sqlite", "checked_at": iso(now),
            "status": "healthy", "issues": [], "latest_copy_at": iso(now - timedelta(hours=2)),
            "age_seconds": 7200, "max_age_hours": 36,
            "artifact_content_hash_verified": False, "restore_verified": False,
            "notification_delivered": False}
    archive_base = dict(base, profile="archive")
    producer.persist(directory, base, os.geteuid())
    producer.persist(directory, archive_base, os.geteuid())
    store = Store(tmp_path / "pulse.sqlite3", clock=lambda: now)
    assert store.observe_backup_reports(config.scope, read_monitor_reports(config, now))["event_count"] == 0

    sender = ec.generate_private_key(ec.SECP256R1())
    recipient = ec.generate_private_key(ec.SECP256R1())
    auth = os.urandom(16)
    crypto = PushCrypto(PushConfig(enabled=True,
        vapid_private_key=encode_url64(sender.private_numbers().private_value.to_bytes(32, "big")),
        storage_key=Fernet.generate_key().decode(), contact="mailto:operator@example.org"))
    subscription = {"endpoint": "https://fcm.googleapis.com/wp/pipeline-fixture-only",
                    "expirationTime": None, "keys": {
                        "p256dh": encode_url64(recipient.public_key().public_bytes(
                            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)),
                        "auth": encode_url64(auth)}}
    row = store.upsert_push_subscription(config.owner_id,
        hashlib.sha256(subscription["endpoint"].encode()).hexdigest(), "b" * 64,
        "Pipeline fixture", crypto.protect(subscription), iso(now + timedelta(hours=8)))
    row = store.set_backup_alert_consent(config.owner_id, row["id"], True, iso(now),
                                         expected_revision=row["revision"])

    # The consumer misses the failing poll and sees a later healthy report.
    now += timedelta(minutes=1)
    producer.persist(directory, dict(archive_base, checked_at=iso(now), status="alert",
                                    issues=["copy_missing"], latest_copy_at=None,
                                    age_seconds=None), os.geteuid())
    now += timedelta(minutes=1)
    producer.persist(directory, dict(archive_base, checked_at=iso(now)), os.geteuid())
    now += timedelta(minutes=1)
    producer.persist(directory, dict(archive_base, checked_at=iso(now)), os.geteuid())
    reports = read_monitor_reports(config, now)
    assert all(report["status"] == "healthy" for report in reports)
    assert store.observe_backup_reports(config.scope, reports)["event_count"] == 2
    candidates = store.list_backup_alert_candidates(config.scope, config.owner_id)
    assert [event["kind"] for event in candidates] == ["backup_alert", "backup_recovery"]
    for event in candidates:
        reserved = store.reserve_backup_alert_delivery(config.scope, config.owner_id, row["id"],
            row["revision"], event["event_id"], row["backup_alerts_consented_at"])
        assert reserved is not None
        endpoint, _, encrypted = crypto.request(reserved["ciphertext"], event["kind"])
        assert endpoint == subscription["endpoint"]
        assert json.loads(http_ece.decrypt(encrypted, private_key=recipient, auth_secret=auth,
            version="aes128gcm")) == {"version": 1, "kind": event["kind"]}
        assert store.finish_backup_alert_delivery(config.scope, config.owner_id, row["id"],
            row["revision"], event["event_id"], "accepted")
        now += timedelta(seconds=60)

    reopened = Store(store.path, clock=lambda: now)
    assert reopened.observe_backup_reports(config.scope, read_monitor_reports(config, now))["event_count"] == 2
    for event in candidates:
        assert reopened.reserve_backup_alert_delivery(config.scope, config.owner_id, row["id"],
            row["revision"], event["event_id"], row["backup_alerts_consented_at"]) is None
    archive = json.dumps(reopened.export_data(owner_id=config.owner_id))
    for protected in ("backup_alert_events", "backup_alert_deliveries", "ciphertext",
                      config.owner_id, subscription["endpoint"]):
        assert protected not in archive
