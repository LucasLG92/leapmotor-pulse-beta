"""Helpers for rebuilding historical SQLite shapes in migration tests."""

import json
import sqlite3


_POST_V15_TELEMETRY_FIELDS = (
    "interior_temperature_c",
    "battery_min_temperature_c",
    "front_left_pressure_kpa",
    "front_right_pressure_kpa",
    "rear_left_pressure_kpa",
    "rear_right_pressure_kpa",
    "vehicle_security_code",
    "sentry_code",
)


def downgrade_backup_alerts_to_v17(connection: sqlite3.Connection) -> None:
    """Remove v18 backup-alert additions from a current Store fixture."""
    for table in (
        "backup_alert_deliveries",
        "backup_alert_events",
        "backup_alert_observations",
        "backup_alert_runtime",
    ):
        connection.execute(f"DROP TABLE IF EXISTS {table}")

    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    if "push_subscriptions" not in tables:
        return
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(push_subscriptions)")
    }
    for field in ("backup_alerts_consented_at", "backup_alerts_enabled"):
        if field in columns:
            connection.execute(f"ALTER TABLE push_subscriptions DROP COLUMN {field}")


def downgrade_telemetry_to_v15(connection: sqlite3.Connection) -> None:
    """Remove post-v15 additions before simulating a schema through v15."""
    rows = connection.execute(
        "SELECT id, field_sources_json FROM telemetry_snapshots ORDER BY rowid"
    ).fetchall()
    for snapshot_id, encoded_sources in rows:
        sources = json.loads(encoded_sources)
        for field in _POST_V15_TELEMETRY_FIELDS:
            sources.pop(field, None)
        connection.execute(
            "UPDATE telemetry_snapshots SET field_sources_json = ? WHERE id = ?",
            (json.dumps(sources, allow_nan=False, separators=(",", ":")), snapshot_id),
        )
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(telemetry_snapshots)")
    }
    for field in _POST_V15_TELEMETRY_FIELDS:
        if field in columns:
            connection.execute(f"ALTER TABLE telemetry_snapshots DROP COLUMN {field}")
    downgrade_backup_alerts_to_v17(connection)
