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


def downgrade_telemetry_to_v15(connection: sqlite3.Connection) -> None:
    """Remove v16/v17 additions before simulating a schema through v15."""
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
