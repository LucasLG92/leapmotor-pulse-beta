"""SQLite-backed domain store for the original Leapmotor Pulse application."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
import math
import re
import sqlite3
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping
import uuid
from zoneinfo import ZoneInfo

from pulse.analytics import calculate_observation_coverage
from pulse.charger_store import (
    ChargerDataError,
    ChargerStoreMixin,
    validate_charger_archive,
    validate_charger_future_dates,
)
from pulse.charger_pricing_store import (
    ChargerPricingStoreMixin,
    validate_charger_pricing_archive,
)
from pulse.charger_energy_store import (
    ChargerEnergyStoreMixin,
    validate_charger_energy_archive,
    validate_charger_energy_future_dates,
)
from pulse.charger_pricing_history import (
    ChargerPricingHistoryStoreMixin,
    validate_charger_pricing_history_archive,
    validate_charger_pricing_history_future_dates,
)
from pulse.native_store import (
    MAX_WIDGET_GRANTS_PER_PROFILE,
    PAIRING_COOLDOWN_SECONDS,
    PAIRING_TTL_SECONDS,
    WIDGET_GRANT_TTL_SECONDS,
    digest_matches,
    digest_secret,
    new_pairing_code,
    new_widget_token,
    validate_random_256,
)
from pulse.widget_profiles import (
    LEGACY_PROFILE_FIELDS,
    MAX_PROFILES_PER_OWNER,
    WidgetProfileValidationError,
    profile_kind,
    validate_owner_id,
    validate_profile_data,
)
from pulse.push_store import PushStoreMixin
from pulse.backup_alert_store import BackupAlertStoreMixin
from pulse.observed_events import (
    ObservedEventError, ObservedEventsStoreMixin, select_changes,
    validate_observed_events_archive, validate_observed_event_future_dates,
)
from pulse.manual_charge_requests import (
    MAX_REQUESTS_GLOBAL,
    MAX_REQUESTS_PER_OWNER,
    REQUEST_TTL_DAYS,
    attempt_fingerprint,
    valid_request_id,
)

SCHEMA_VERSION = 19
EXPORT_VERSION = 15
MAX_IMPORT_BYTES = 1_000_000
MAX_V3_IMPORT_BYTES = 5_000_000
MAX_V4_IMPORT_BYTES = 5_000_000
MAX_V5_IMPORT_BYTES = 5_000_000
MAX_V6_IMPORT_BYTES = 5_000_000
MAX_V7_IMPORT_BYTES = 5_000_000
MAX_V8_IMPORT_BYTES = 5_000_000
MAX_V9_IMPORT_BYTES = 5_000_000
MAX_V10_IMPORT_BYTES = 5_000_000
MAX_V11_IMPORT_BYTES = 5_000_000
MAX_V12_IMPORT_BYTES = 5_000_000
MAX_V13_IMPORT_BYTES = 5_000_000
MAX_V14_IMPORT_BYTES = 5_000_000
MAX_V15_IMPORT_BYTES = 5_000_000
MAX_ROWS_PER_COLLECTION = 100_000
MAX_TEXT_LENGTH = 240
MAX_STATE_CHARGES = 100
MAX_COMMAND_HISTORY = 500
MAX_WIDGET_PROFILE_IMPORT_ROWS = 5_000
MAX_TELEMETRY_SNAPSHOTS = 1_000
MAX_TELEMETRY_HISTORY = 100
MAX_ANALYTICS_DAYS = 3_650
SQLITE_BUSY_TIMEOUT_MS = 5_000
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_UUID_HEX_RE = re.compile(r"[0-9a-f]{32}\Z")
_TELEMETRY_SOURCE = "MATE-API B10ReadClient"
_TELEMETRY_NUMERIC_LIMITS = {
    "battery_pct": (0, 100),
    "range_km": (0, 5000),
    "odometer_km": (0, 10_000_000),
    "interior_temperature_c": (-100, 100),
    "battery_min_temperature_c": (-100, 150),
    "front_left_pressure_kpa": (0, 1000),
    "front_right_pressure_kpa": (0, 1000),
    "rear_left_pressure_kpa": (0, 1000),
    "rear_right_pressure_kpa": (0, 1000),
}
_TELEMETRY_BOOLEAN_FIELDS = ("locked", "climate_on", "charging")
_TELEMETRY_SECURITY_CODE_FIELDS = ("vehicle_security_code", "sentry_code")
_TELEMETRY_FIELD_SOURCES = {
    "battery_pct": frozenset({"precise_soc_percent", "soc_percent"}),
    "range_km": frozenset({"expected_range_km"}),
    "odometer_km": frozenset({"odometer_km"}),
    "interior_temperature_c": frozenset({"interior_temperature_c"}),
    "battery_min_temperature_c": frozenset({"battery_min_temperature_c"}),
    "front_left_pressure_kpa": frozenset({"front_left_pressure_kpa"}),
    "front_right_pressure_kpa": frozenset({"front_right_pressure_kpa"}),
    "rear_left_pressure_kpa": frozenset({"rear_left_pressure_kpa"}),
    "rear_right_pressure_kpa": frozenset({"rear_right_pressure_kpa"}),
    "vehicle_security_code": frozenset({"vehicle_security_code"}),
    "sentry_code": frozenset({"sentry_code"}),
    "locked": frozenset(),
    "climate_on": frozenset(),
    "charging": frozenset(),
}
_TELEMETRY_FIELDS = (
    tuple(_TELEMETRY_NUMERIC_LIMITS)
    + _TELEMETRY_SECURITY_CODE_FIELDS
    + _TELEMETRY_BOOLEAN_FIELDS
)
_TELEMETRY_METRIC_FIELDS = frozenset({
    "interior_temperature_c", "battery_min_temperature_c",
    "front_left_pressure_kpa", "front_right_pressure_kpa",
    "rear_left_pressure_kpa", "rear_right_pressure_kpa",
})
_TELEMETRY_LEGACY_FIELDS = frozenset({
    "battery_pct", "range_km", "odometer_km", *_TELEMETRY_BOOLEAN_FIELDS,
})

_DEFAULT_SETTINGS: dict[str, Any] = {
    "vehicle_name": "Mi Leapmotor B10",
    "model": "B10",
    "tariff_eur_kwh": 0.15,
    "home_label": "Casa",
    "pricing_method": "manual",
    "vehicle_color": None,
}

_LEGACY_SETTINGS_FIELDS = {"vehicle_name", "model", "tariff_eur_kwh", "home_label"}
_V12_SETTINGS_FIELDS = set(_DEFAULT_SETTINGS) - {"vehicle_color"}
_VEHICLE_COLOR_IDS = frozenset({
    "starry_night_blue", "midnight_purple", "tundra_grey",
    "metallic_black", "stellar_silver", "lunar_white",
})

_COMMANDS: tuple[tuple[str, str], ...] = (
    ("lock", "Bloquear"),
    ("unlock", "Desbloquear"),
    ("climate_on", "Activar climatización"),
    ("climate_off", "Desactivar climatización"),
    ("charge_start", "Iniciar carga"),
    ("charge_stop", "Detener carga"),
)
_COMMAND_LABELS = dict(_COMMANDS)
_V1_EXPORT_FIELDS = {
    "schema_version", "exported_at", "mode", "settings", "charges", "journeys",
    "maintenance", "protection",
}
_DEFAULT_DEMO_VEHICLE: dict[str, Any] = {
    "battery_pct": 82.0,
    "range_km": 356.0,
    "odometer_km": 1240.0,
    "locked": True,
    "climate_on": False,
    "charging": False,
}


class DomainError(Exception):
    """Expected domain failure, with the HTTP-compatible status for the API."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status
        self.status_code = status


class Store(ChargerStoreMixin, ChargerPricingStoreMixin, ChargerEnergyStoreMixin,
            ChargerPricingHistoryStoreMixin,
            PushStoreMixin, BackupAlertStoreMixin, ObservedEventsStoreMixin):
    """A per-call SQLite store with explicit initialization and short transactions."""

    def __init__(
        self,
        path: str | Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        raw_path = str(path)
        if raw_path in ("", ":memory:"):
            raise ValueError("Store necesita una ruta SQLite persistente.")
        self.path = str(Path(raw_path).expanduser())
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._observed_baseline_id: str | None = None
        self._analytics_snapshot_cache: tuple[
            tuple[Any, ...], tuple[dict[str, Any], ...]
        ] | None = None
        _require_supported_sqlite()
        Path(self.path).resolve().parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _clock_now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("El reloj de Store debe devolver una fecha con zona horaria.")
        return value.astimezone(timezone.utc)

    def _now(self) -> str:
        return _format_datetime(self._clock_now())

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=SQLITE_BUSY_TIMEOUT_MS / 1_000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            journal_mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            if str(journal_mode).lower() != "wal":
                raise DomainError("SQLite no pudo activar el modo WAL para Pulse.", 500)
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_schema(connection)
            connection.commit()
        except DomainError:
            if connection is not None:
                connection.rollback()
            raise
        except sqlite3.Error:
            if connection is not None:
                connection.rollback()
            raise DomainError("No se pudo inicializar el almacenamiento Pulse.", 500) from None
        except Exception:
            if connection is not None:
                connection.rollback()
            raise
        finally:
            if connection is not None:
                connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        """Open a write transaction; schema work is intentionally init-only."""
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except DomainError:
            if connection is not None:
                connection.rollback()
            raise
        except sqlite3.Error:
            if connection is not None:
                connection.rollback()
            raise DomainError("No se pudo acceder al almacenamiento Pulse.", 500) from None
        except Exception:
            if connection is not None:
                connection.rollback()
            raise
        finally:
            if connection is not None:
                connection.close()

    @contextmanager
    def _read_transaction(self) -> Iterator[sqlite3.Connection]:
        """Open a deferred snapshot so reads do not reserve SQLite's writer lock."""
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.execute("BEGIN")
            yield connection
            connection.commit()
        except DomainError:
            if connection is not None:
                connection.rollback()
            raise
        except sqlite3.Error:
            if connection is not None:
                connection.rollback()
            raise DomainError("No se pudo acceder al almacenamiento Pulse.", 500) from None
        except Exception:
            if connection is not None:
                connection.rollback()
            raise
        finally:
            if connection is not None:
                connection.close()

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION or version < 0:
            raise DomainError("La base de datos Pulse usa una versión no compatible.", 500)
        if version == 0:
            existing = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchone()
            if existing is not None:
                raise DomainError("La base de datos Pulse no tiene una versión reconocida.", 500)
            _create_v1_schema(connection, self._now())
            connection.execute("PRAGMA user_version = 1")
            version = 1
        if version == 1:
            self._migrate_v1_to_v2(connection)
            connection.execute("PRAGMA user_version = 2")
            version = 2
        if version == 2:
            self._migrate_v2_to_v3(connection)
            connection.execute("PRAGMA user_version = 3")
            version = 3
        if version == 3:
            self._migrate_v3_to_v4(connection)
            connection.execute("PRAGMA user_version = 4")
            version = 4
        if version == 4:
            self._migrate_v4_to_v5(connection)
            connection.execute("PRAGMA user_version = 5")
            version = 5
        if version == 5:
            self._migrate_v5_to_v6(connection)
            connection.execute("PRAGMA user_version = 6")
            version = 6
        if version == 6:
            self._migrate_v6_to_v7(connection)
            connection.execute("PRAGMA user_version = 7")
            version = 7
        if version == 7:
            self._migrate_v7_to_v8(connection)
            connection.execute("PRAGMA user_version = 8")
            version = 8
        if version == 8:
            self._migrate_v8_to_v9(connection)
            connection.execute("PRAGMA user_version = 9")
            version = 9
        if version == 9:
            self._migrate_v9_to_v10(connection)
            connection.execute("PRAGMA user_version = 10")
            version = 10
        if version == 10:
            self._migrate_v10_to_v11(connection)
            connection.execute("PRAGMA user_version = 11")
            version = 11
        if version == 11:
            self._migrate_v11_to_v12(connection)
            connection.execute("PRAGMA user_version = 12")
            version = 12
        if version == 12:
            self._migrate_v12_to_v13(connection)
            connection.execute("PRAGMA user_version = 13")
            version = 13
        if version == 13:
            # New quote semantics: older binaries must not open temporal quotes.
            # Tables/data are unchanged; preserve uniform historical snapshots.
            connection.execute("PRAGMA user_version = 14")
            version = 14
        if version == 14:
            self._migrate_v14_to_v15(connection)
            connection.execute("PRAGMA user_version = 15")
            version = 15
        if version == 15:
            self._migrate_v15_to_v16(connection)
            connection.execute("PRAGMA user_version = 16")
            version = 16
        if version == 16:
            self._migrate_v16_to_v17(connection)
            connection.execute("PRAGMA user_version = 17")
            version = 17
        if version == 17:
            self._migrate_v17_to_v18(connection)
            connection.execute("PRAGMA user_version = 18")
            version = 18
        if version == 18:
            self._migrate_v18_to_v19(connection)
            connection.execute("PRAGMA user_version = 19")

    def _migrate_v17_to_v18(self, connection: sqlite3.Connection) -> None:
        """Add opt-in backup notices and bounded operational state, outside exports."""
        connection.execute(
            """ALTER TABLE push_subscriptions ADD COLUMN backup_alerts_enabled INTEGER NOT NULL
               DEFAULT 0 CHECK (backup_alerts_enabled IN (0, 1))"""
        )
        connection.execute(
            "ALTER TABLE push_subscriptions ADD COLUMN backup_alerts_consented_at TEXT"
        )
        self._create_backup_alert_schema(connection)

    def _migrate_v18_to_v19(self, connection: sqlite3.Connection) -> None:
        """Add operational idempotency reservations, excluded from JSON archives."""
        connection.execute(
            """CREATE TABLE manual_charge_requests (
                id TEXT PRIMARY KEY
                    CHECK (length(id) = 32 AND id NOT GLOB '*[^0-9a-f]*'),
                owner_id TEXT NOT NULL
                    CHECK (length(owner_id) BETWEEN 1 AND 255),
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                attempt_fingerprint TEXT,
                charge_id TEXT,
                CHECK (
                    (attempt_fingerprint IS NULL AND charge_id IS NULL)
                    OR (attempt_fingerprint IS NOT NULL
                        AND length(attempt_fingerprint) = 64
                        AND attempt_fingerprint NOT GLOB '*[^0-9a-f]*'
                        AND charge_id IS NOT NULL
                        AND length(charge_id) = 32
                        AND charge_id NOT GLOB '*[^0-9a-f]*')
                )
            )"""
        )
        connection.execute(
            "CREATE INDEX idx_manual_charge_requests_expiry ON manual_charge_requests(expires_at)"
        )
        connection.execute(
            """CREATE INDEX idx_manual_charge_requests_owner_expiry
               ON manual_charge_requests(owner_id, expires_at)"""
        )

    def _migrate_v14_to_v15(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """ALTER TABLE settings ADD COLUMN vehicle_color TEXT
               CHECK (vehicle_color IS NULL OR vehicle_color IN (
                   'starry_night_blue', 'midnight_purple', 'tundra_grey',
                   'metallic_black', 'stellar_silver', 'lunar_white'
               ))"""
        )

    def _migrate_v15_to_v16(self, connection: sqlite3.Connection) -> None:
        legacy_sources: list[tuple[str, str]] = []
        try:
            rows = connection.execute(
                """SELECT id, battery_pct, range_km, odometer_km, locked, climate_on,
                          charging, field_sources_json
                   FROM telemetry_snapshots ORDER BY rowid"""
            ).fetchall()
            for row in rows:
                source_map = json.loads(row["field_sources_json"])
                if not isinstance(source_map, Mapping):
                    raise DomainError("La base contiene fuentes de telemetría corruptas.", 500)
                _, normalized_sources = _normalize_telemetry_values(
                    {
                        "battery_pct": row["battery_pct"],
                        "range_km": row["range_km"],
                        "odometer_km": row["odometer_km"],
                        "locked": _nullable_int_to_bool(row["locked"]),
                        "climate_on": _nullable_int_to_bool(row["climate_on"]),
                        "charging": _nullable_int_to_bool(row["charging"]),
                    },
                    source_map,
                    allow_missing_fields=_TELEMETRY_METRIC_FIELDS
                    | frozenset(_TELEMETRY_SECURITY_CODE_FIELDS),
                )
                legacy_sources.append((row["id"], _encode_json(normalized_sources)))
        except DomainError:
            raise
        except (json.JSONDecodeError, TypeError, KeyError, ValueError):
            raise DomainError("La base contiene fuentes de telemetría corruptas.", 500) from None

        for field, (minimum, maximum) in _TELEMETRY_NUMERIC_LIMITS.items():
            if field in {"battery_pct", "range_km", "odometer_km"}:
                continue
            connection.execute(
                f"ALTER TABLE telemetry_snapshots ADD COLUMN {field} REAL "
                f"CHECK ({field} IS NULL OR {field} BETWEEN {minimum} AND {maximum})"
            )
        connection.executemany(
            "UPDATE telemetry_snapshots SET field_sources_json = ? WHERE id = ?",
            [(sources_json, snapshot_id) for snapshot_id, sources_json in legacy_sources],
        )

    def _migrate_v16_to_v17(self, connection: sqlite3.Connection) -> None:
        """Add nullable security-code observations without inferring history."""
        normalized_sources: list[tuple[str, str]] = []
        try:
            rows = connection.execute(
                """SELECT id, battery_pct, range_km, odometer_km, locked, climate_on,
                          charging, interior_temperature_c, battery_min_temperature_c,
                          front_left_pressure_kpa, front_right_pressure_kpa,
                          rear_left_pressure_kpa, rear_right_pressure_kpa, field_sources_json
                   FROM telemetry_snapshots ORDER BY rowid"""
            ).fetchall()
            for row in rows:
                source_map = json.loads(row["field_sources_json"])
                if not isinstance(source_map, Mapping):
                    raise DomainError("La base contiene fuentes de telemetría corruptas.", 500)
                _, sources = _normalize_telemetry_values(
                    {
                        "battery_pct": row["battery_pct"],
                        "range_km": row["range_km"],
                        "odometer_km": row["odometer_km"],
                        "interior_temperature_c": row["interior_temperature_c"],
                        "battery_min_temperature_c": row["battery_min_temperature_c"],
                        "front_left_pressure_kpa": row["front_left_pressure_kpa"],
                        "front_right_pressure_kpa": row["front_right_pressure_kpa"],
                        "rear_left_pressure_kpa": row["rear_left_pressure_kpa"],
                        "rear_right_pressure_kpa": row["rear_right_pressure_kpa"],
                        "vehicle_security_code": None,
                        "sentry_code": None,
                        "locked": _nullable_int_to_bool(row["locked"]),
                        "climate_on": _nullable_int_to_bool(row["climate_on"]),
                        "charging": _nullable_int_to_bool(row["charging"]),
                    },
                    source_map,
                    allow_missing_fields=frozenset(_TELEMETRY_SECURITY_CODE_FIELDS),
                )
                normalized_sources.append((row["id"], _encode_json(sources)))
        except DomainError:
            raise
        except (json.JSONDecodeError, TypeError, KeyError, ValueError):
            raise DomainError("La base contiene fuentes de telemetría corruptas.", 500) from None

        for field in _TELEMETRY_SECURITY_CODE_FIELDS:
            connection.execute(
                f"ALTER TABLE telemetry_snapshots ADD COLUMN {field} INTEGER "
                f"CHECK ({field} IS NULL OR (typeof({field}) = 'integer' "
                f"AND {field} BETWEEN 0 AND 255))"
            )
        connection.executemany(
            "UPDATE telemetry_snapshots SET field_sources_json = ? WHERE id = ?",
            [(sources_json, snapshot_id) for snapshot_id, sources_json in normalized_sources],
        )

    def _migrate_v10_to_v11(self, connection: sqlite3.Connection) -> None:
        connection.execute("""CREATE TABLE abrp_preference (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            scope TEXT NOT NULL,
            enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
            generation INTEGER NOT NULL CHECK(generation >= 0),
            last_attempt_key TEXT,
            last_attempt_at TEXT
        )""")
        connection.execute("""INSERT INTO abrp_preference
            (id, scope, enabled, generation) VALUES (1, '', 0, 0)""")

    def _migrate_v7_to_v8(self, connection: sqlite3.Connection) -> None:
        """Advance the profile-semantics guard without changing stored tables."""
        # V8 changes the meaning of the JSON profile fields; old binaries must
        # stop opening the database because they cannot render the new families.

    def _migrate_v5_to_v6(self, connection: sqlite3.Connection) -> None:
        """Add private, owner-scoped Web Push subscription storage."""
        connection.execute(
            """CREATE TABLE push_subscriptions (
                id TEXT PRIMARY KEY CHECK (
                    length(id) = 32 AND id NOT GLOB '*[^0-9a-f]*'
                ),
                owner_id TEXT NOT NULL CHECK (length(owner_id) BETWEEN 1 AND 255),
                endpoint_hash TEXT NOT NULL UNIQUE CHECK (
                    length(endpoint_hash) = 64 AND endpoint_hash NOT GLOB '*[^0-9a-f]*'
                ),
                session_hash TEXT NOT NULL CHECK (
                    length(session_hash) = 64 AND session_hash NOT GLOB '*[^0-9a-f]*'
                ),
                name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 60),
                ciphertext TEXT NOT NULL CHECK (length(ciphertext) BETWEEN 1 AND 8192),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK (revision > 0),
                last_attempt_at TEXT,
                last_accepted_at TEXT,
                last_result TEXT NOT NULL CHECK (
                    last_result IN ('never', 'pending', 'accepted', 'failed', 'uncertain')
                )
            )"""
        )
        connection.execute(
            "CREATE INDEX idx_push_subscriptions_owner_updated "
            "ON push_subscriptions(owner_id, updated_at DESC, id)"
        )
        connection.execute(
            "CREATE INDEX idx_push_subscriptions_expiry "
            "ON push_subscriptions(expires_at, id)"
        )

    def _migrate_v2_to_v3(self, connection: sqlite3.Connection) -> None:
        """Add owner-scoped widget configuration and bounded telemetry history."""
        connection.execute(
            """CREATE TABLE widget_profiles (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                name TEXT NOT NULL,
                fields_json TEXT NOT NULL,
                appearance TEXT NOT NULL CHECK (appearance IN ('system', 'light', 'dark')),
                privacy_mode TEXT NOT NULL CHECK (privacy_mode IN ('masked', 'visible')),
                max_age_minutes INTEGER NOT NULL CHECK (max_age_minutes BETWEEN 15 AND 120),
                revision INTEGER NOT NULL CHECK (revision > 0),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CHECK (length(owner_id) BETWEEN 1 AND 255),
                CHECK (length(name) BETWEEN 1 AND 60)
            )"""
        )
        connection.execute(
            "CREATE INDEX idx_widget_profiles_owner_created "
            "ON widget_profiles(owner_id, created_at, id)"
        )
        connection.execute(
            """CREATE TABLE telemetry_snapshots (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL CHECK (source = 'MATE-API B10ReadClient'),
                observed_at TEXT,
                provider_received_at TEXT,
                server_received_at TEXT NOT NULL,
                battery_pct REAL CHECK (battery_pct IS NULL OR battery_pct BETWEEN 0 AND 100),
                range_km REAL CHECK (range_km IS NULL OR range_km BETWEEN 0 AND 5000),
                odometer_km REAL CHECK (odometer_km IS NULL OR odometer_km BETWEEN 0 AND 10000000),
                locked INTEGER CHECK (locked IS NULL OR locked IN (0, 1)),
                climate_on INTEGER CHECK (climate_on IS NULL OR climate_on IN (0, 1)),
                charging INTEGER CHECK (charging IS NULL OR charging IN (0, 1)),
                field_sources_json TEXT NOT NULL
            )"""
        )
        connection.execute(
            "CREATE INDEX idx_telemetry_server_received "
            "ON telemetry_snapshots(server_received_at DESC, id DESC)"
        )

    def _migrate_v3_to_v4(self, connection: sqlite3.Connection) -> None:
        """Add charge-level pricing snapshots and a local PVPC day cache."""
        connection.execute(
            "ALTER TABLE settings ADD COLUMN pricing_method TEXT NOT NULL DEFAULT 'manual' "
            "CHECK (pricing_method IN ('manual', 'pvpc'))"
        )

        # Existing manual charges remain manual with their recorded costs and tariffs.
        # Rebuild the table because SQLite cannot widen the historic non-negative checks.
        manual_indexes = [
            row[0]
            for row in connection.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'index' AND tbl_name = 'manual_charges' AND sql IS NOT NULL"
            ).fetchall()
        ]
        connection.execute(
            """CREATE TABLE manual_charges_v4 (
                id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                kwh REAL NOT NULL CHECK (kwh > 0 AND kwh <= 500),
                cost_eur REAL NOT NULL CHECK (cost_eur >= -2500 AND cost_eur <= 2500),
                tariff_eur_kwh REAL CHECK (
                    tariff_eur_kwh IS NULL OR (tariff_eur_kwh >= -5 AND tariff_eur_kwh <= 5)
                ),
                cost_cents INTEGER CHECK (
                    cost_cents IS NULL OR (cost_cents >= -250000 AND cost_cents <= 250000)
                ),
                ended_at TEXT,
                pricing_method TEXT CHECK (
                    pricing_method IS NULL OR pricing_method IN ('manual', 'pvpc')
                ),
                pricing_json TEXT,
                CHECK (pricing_json IS NULL OR pricing_method = 'pvpc'),
                CHECK (pricing_method != 'pvpc' OR (ended_at IS NOT NULL AND pricing_json IS NOT NULL))
            )"""
        )
        connection.execute(
            """INSERT INTO manual_charges_v4
               (id, started_at, kwh, cost_eur, tariff_eur_kwh, cost_cents,
                ended_at, pricing_method, pricing_json)
               SELECT id, started_at, kwh, cost_eur, tariff_eur_kwh, cost_cents,
                      NULL, 'manual', NULL
               FROM manual_charges"""
        )
        connection.execute("DROP TABLE manual_charges")
        connection.execute("ALTER TABLE manual_charges_v4 RENAME TO manual_charges")
        for statement in manual_indexes:
            connection.execute(statement)

        connection.execute("ALTER TABLE demo_charges ADD COLUMN ended_at TEXT")
        connection.execute(
            "ALTER TABLE demo_charges ADD COLUMN pricing_method TEXT "
            "CHECK (pricing_method IS NULL)"
        )
        connection.execute(
            "ALTER TABLE demo_charges ADD COLUMN pricing_json TEXT CHECK (pricing_json IS NULL)"
        )
        connection.execute(
            """CREATE TABLE pvpc_day_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL CHECK (length(date) = 10),
                region TEXT NOT NULL CHECK (region = 'peninsula'),
                timezone TEXT NOT NULL CHECK (timezone = 'Europe/Madrid'),
                source TEXT NOT NULL CHECK (source = 'REE REData PVPC 1001'),
                fetched_at TEXT NOT NULL,
                intervals_json TEXT NOT NULL
            )"""
        )
        connection.execute(
            "CREATE INDEX idx_pvpc_day_cache_date_order ON pvpc_day_cache(date, id DESC)"
        )

    def _migrate_v4_to_v5(self, connection: sqlite3.Connection) -> None:
        """Add one-time device pairing and revocable widget-only grant storage."""
        connection.execute(
            """CREATE TABLE widget_pairings (
                profile_id TEXT PRIMARY KEY
                    REFERENCES widget_profiles(id) ON DELETE CASCADE,
                code_hash TEXT NOT NULL UNIQUE CHECK (length(code_hash) = 64),
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                consumed_at TEXT,
                CHECK (expires_at > created_at)
            )"""
        )
        connection.execute(
            "CREATE INDEX idx_widget_pairings_expiry ON widget_pairings(expires_at)"
        )
        connection.execute(
            """CREATE TABLE widget_grants (
                id TEXT PRIMARY KEY CHECK (length(id) = 32),
                profile_id TEXT NOT NULL
                    REFERENCES widget_profiles(id) ON DELETE CASCADE,
                issuer TEXT NOT NULL CHECK (length(issuer) BETWEEN 1 AND 2048),
                subject TEXT NOT NULL CHECK (length(subject) BETWEEN 1 AND 255),
                client_id TEXT NOT NULL CHECK (length(client_id) BETWEEN 1 AND 255),
                installation_hash TEXT NOT NULL CHECK (length(installation_hash) = 64),
                token_hash TEXT NOT NULL UNIQUE CHECK (length(token_hash) = 64),
                device_name TEXT NOT NULL CHECK (length(device_name) BETWEEN 1 AND 60),
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                last_seen_at TEXT,
                CHECK (expires_at > created_at),
                UNIQUE (profile_id, issuer, subject, client_id, installation_hash)
            )"""
        )
        connection.execute(
            "CREATE INDEX idx_widget_grants_profile_expiry "
            "ON widget_grants(profile_id, expires_at, created_at, id)"
        )

    def _migrate_v1_to_v2(self, connection: sqlite3.Connection) -> None:
        # Preserve every v1 field. Tariffs were not stored then, so historical
        # entries keep their costs and carry an unknown rate until explicitly set.
        for table in ("manual_charges", "demo_charges"):
            connection.execute(
                f"ALTER TABLE {table} ADD COLUMN tariff_eur_kwh REAL "
                "CHECK (tariff_eur_kwh IS NULL OR (tariff_eur_kwh >= 0 AND tariff_eur_kwh <= 5))"
            )
            connection.execute(
                f"ALTER TABLE {table} ADD COLUMN cost_cents INTEGER "
                "CHECK (cost_cents IS NULL OR (cost_cents >= 0 AND cost_cents <= 250000))"
            )
            connection.execute(
                f"UPDATE {table} SET cost_cents = CAST(ROUND(cost_eur * 100) AS INTEGER) "
                "WHERE ABS(cost_eur * 100 - ROUND(cost_eur * 100)) < 0.0000001"
            )
        for table, column in (
            ("manual_charges", "started_at"),
            ("demo_charges", "started_at"),
            ("demo_journeys", "started_at"),
            ("demo_protection_events", "created_at"),
        ):
            for row in connection.execute(f"SELECT id, {column} FROM {table}").fetchall():
                normalized = _parse_datetime(row[column], column)
                if normalized != row[column]:
                    connection.execute(
                        f"UPDATE {table} SET {column} = ? WHERE id = ?", (normalized, row["id"])
                    )
        connection.execute(
            """CREATE TABLE demo_vehicle (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                battery_pct REAL NOT NULL CHECK (battery_pct >= 0 AND battery_pct <= 100),
                range_km REAL NOT NULL CHECK (range_km >= 0 AND range_km <= 1000),
                odometer_km REAL NOT NULL CHECK (odometer_km >= 0 AND odometer_km <= 10000000),
                locked INTEGER NOT NULL CHECK (locked IN (0, 1)),
                climate_on INTEGER NOT NULL CHECK (climate_on IN (0, 1)),
                charging INTEGER NOT NULL CHECK (charging IN (0, 1)),
                observed_at TEXT NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE demo_command_journal (
                id TEXT PRIMARY KEY,
                command TEXT NOT NULL CHECK (command IN (
                    'lock', 'unlock', 'climate_on', 'climate_off', 'charge_start', 'charge_stop'
                )),
                status TEXT NOT NULL CHECK (status = 'simulated'),
                created_at TEXT NOT NULL,
                message TEXT NOT NULL
            )"""
        )
        connection.execute(
            "CREATE INDEX idx_manual_charges_started_at ON manual_charges(started_at)"
        )
        connection.execute(
            "CREATE INDEX idx_demo_charges_started_at ON demo_charges(started_at)"
        )
        connection.execute(
            "CREATE INDEX idx_demo_command_created_at ON demo_command_journal(created_at)"
        )
        self._reset_demo_vehicle(connection, self._now())

    def state(self, app_name: str, version: str) -> dict[str, Any]:
        app_name = _text(app_name, "app_name", max_length=80)
        version = _text(version, "version", max_length=40)
        with self._read_transaction() as connection:
            return self._state(connection, app_name, version)

    def state_context(self, app_name: str, version: str) -> dict[str, Any]:
        """Resumen y última lectura del mismo estado persistido, sin consulta al coche."""
        app_name = _text(app_name, "app_name", max_length=80)
        version = _text(version, "version", max_length=40)
        with self._read_transaction() as connection:
            state = self._state(connection, app_name, version)
            snapshot = None if state["mode"] == "demo" else _read_latest_telemetry(connection)
            return {"state": state, "snapshot": snapshot}

    def _state(self, connection: sqlite3.Connection, app_name: str, version: str) -> dict[str, Any]:
        meta = connection.execute("SELECT * FROM app_meta WHERE id = 1").fetchone()
        demo = bool(meta["demo_enabled"])
        settings = _read_settings(connection)
        sessions = _list_charges(connection, MAX_STATE_CHARGES, 0, demo)

        manual = _charge_totals(connection, "manual_charges")
        demo_charges = _charge_totals(connection, "demo_charges") if demo else (0.0, 0.0)
        manual_totals = _totals_dict(manual[0], manual[1], 0.0, 0.0)
        journey_totals = _journey_totals(connection) if demo else (0.0, 0.0)
        demo_totals = _totals_dict(demo_charges[0], demo_charges[1], *journey_totals)
        totals = _totals_dict(
            math.fsum((manual[0], demo_charges[0])),
            math.fsum((manual[1], demo_charges[1])),
            journey_totals[0],
            journey_totals[1],
        )

        journeys = _read_journeys(connection, limit=MAX_STATE_CHARGES) if demo else []
        maintenance = _read_maintenance(connection, "manual_maintenance", limit=MAX_STATE_CHARGES)
        if demo:
            maintenance += _read_maintenance(
                connection, "demo_maintenance", limit=MAX_STATE_CHARGES
            )
        maintenance.sort(key=lambda item: (item["due_date"], item["id"]))
        maintenance = maintenance[:MAX_STATE_CHARGES]
        events = _read_events(connection, limit=MAX_STATE_CHARGES) if demo else []
        command_history = _read_command_history(connection, limit=50) if demo else []

        vehicle: dict[str, Any] | None = None
        if demo:
            demo_vehicle = _read_demo_vehicle(connection)
            vehicle = {
                "name": settings["vehicle_name"],
                "model": settings["model"],
                **demo_vehicle,
            }

        reason = (
            "Datos ficticios de demostración; no hay conexión con el vehículo."
            if demo
            else "Aún no hay una integración habilitada."
        )
        return {
            "app": {"name": app_name, "version": version},
            "mode": "demo" if demo else "unconfigured",
            "updated_at": meta["updated_at"],
            "vehicle": vehicle,
            "settings": settings,
            "energy": {
                "sessions": sessions,
                "totals": totals,
                "totals_by_source": {"manual": manual_totals, "demo": demo_totals},
            },
            "journeys": journeys,
            "maintenance": maintenance,
            "protection": {
                "enabled": bool(meta["protection_enabled"]) if demo else False,
                "events": events,
                "observed_count": 0 if demo else int(connection.execute("SELECT COUNT(*) FROM observed_events").fetchone()[0]),
            },
            "commands": _command_state(demo),
            "command_history": command_history,
            "connection": {
                "status": "demo" if demo else "disconnected",
                "reason": reason,
            },
        }

    def list_charges(self, limit: int = MAX_STATE_CHARGES, offset: int = 0) -> dict[str, Any]:
        limit, offset = _pagination(limit, offset)
        with self._read_transaction() as connection:
            meta = connection.execute("SELECT demo_enabled FROM app_meta WHERE id = 1").fetchone()
            demo = bool(meta["demo_enabled"])
            total = _charge_count(connection, demo)
            items = _list_charges(connection, limit, offset, demo)
            return {"items": items, "limit": limit, "offset": offset, "total": total}

    def command_history(self, limit: int = 50) -> list[dict[str, Any]]:
        limit = _bounded_int(limit, "limit", minimum=1, maximum=MAX_COMMAND_HISTORY)
        with self._read_transaction() as connection:
            return _read_command_history(connection, limit)

    def list_widget_profiles(self, owner_id: str) -> dict[str, Any]:
        owner = _validate_profile_owner(owner_id)
        with self._read_transaction() as connection:
            rows = connection.execute(
                """SELECT * FROM widget_profiles
                   WHERE owner_id = ? ORDER BY created_at, id""",
                (owner,),
            ).fetchall()
            return {"items": [_widget_profile_item(row) for row in rows]}

    def create_widget_profile(
        self, owner_id: str, data: Mapping[str, Any]
    ) -> dict[str, Any]:
        owner = _validate_profile_owner(owner_id)
        try:
            normalized = validate_profile_data(data, partial=True)
        except WidgetProfileValidationError as error:
            raise DomainError(str(error)) from None
        if not {"name", "fields"}.issubset(normalized):
            raise DomainError("Un perfil requiere name y fields.")
        normalized.setdefault("appearance", "system")
        normalized.setdefault("privacy_mode", "masked")
        normalized.setdefault("max_age_minutes", 60)

        profile_id = uuid.uuid4().hex
        now = self._now()
        with self._transaction() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM widget_profiles WHERE owner_id = ?", (owner,)
            ).fetchone()[0]
            if count >= MAX_PROFILES_PER_OWNER:
                raise DomainError("Cada usuario puede guardar como máximo 5 perfiles.", 409)
            connection.execute(
                """INSERT INTO widget_profiles
                   (id, owner_id, name, fields_json, appearance, privacy_mode,
                    max_age_minutes, revision, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                (
                    profile_id, owner, normalized["name"], _encode_json(normalized["fields"]),
                    normalized["appearance"], normalized["privacy_mode"],
                    normalized["max_age_minutes"], now, now,
                ),
            )
            self._touch(connection)
            row = connection.execute(
                "SELECT * FROM widget_profiles WHERE id = ?", (profile_id,)
            ).fetchone()
            return _widget_profile_item(row)

    def update_widget_profile(
        self, owner_id: str, id: str, data: Mapping[str, Any]
    ) -> dict[str, Any]:
        owner = _validate_profile_owner(owner_id)
        profile_id = _identifier(id)
        try:
            normalized = validate_profile_data(data, partial=True)
        except WidgetProfileValidationError as error:
            raise DomainError(str(error)) from None
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM widget_profiles WHERE owner_id = ? AND id = ?",
                (owner, profile_id),
            ).fetchone()
            if row is None:
                raise DomainError("No se encontró el perfil solicitado.", 404)
            # Existing grants follow their owner's current profile settings.
            # Pending codes are tied to the profile revision and cannot survive an edit.
            connection.execute(
                "DELETE FROM widget_pairings WHERE profile_id = ?", (profile_id,)
            )
            current = _widget_profile_item(row)
            current.update(normalized)
            next_revision = int(row["revision"]) + 1
            if next_revision > 9_223_372_036_854_775_807:
                raise DomainError("La revisión del perfil agotó el rango permitido.", 409)
            connection.execute(
                """UPDATE widget_profiles SET name = ?, fields_json = ?, appearance = ?,
                   privacy_mode = ?, max_age_minutes = ?, revision = ?, updated_at = ?
                   WHERE owner_id = ? AND id = ?""",
                (
                    current["name"], _encode_json(current["fields"]), current["appearance"],
                    current["privacy_mode"], current["max_age_minutes"], next_revision,
                    self._now(), owner, profile_id,
                ),
            )
            self._touch(connection)
            updated = connection.execute(
                "SELECT * FROM widget_profiles WHERE owner_id = ? AND id = ?",
                (owner, profile_id),
            ).fetchone()
            return _widget_profile_item(updated)

    def delete_widget_profile(self, owner_id: str, id: str) -> dict[str, Any]:
        owner = _validate_profile_owner(owner_id)
        profile_id = _identifier(id)
        with self._transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM widget_profiles WHERE owner_id = ? AND id = ?",
                (owner, profile_id),
            )
            if cursor.rowcount == 0:
                raise DomainError("No se encontró el perfil solicitado.", 404)
            self._touch(connection)
            return {"ok": True, "id": profile_id}

    def create_widget_pairing(self, owner_id: str, profile_id: str) -> dict[str, str]:
        owner = _validate_profile_owner(owner_id)
        profile_id = _profile_identifier(profile_id)
        now_dt = self._clock_now()
        now = _format_datetime(now_dt)
        expires_at = _format_datetime(now_dt + timedelta(seconds=PAIRING_TTL_SECONDS))
        with self._transaction() as connection:
            connection.execute(
                "DELETE FROM widget_pairings WHERE expires_at <= ?", (now,)
            )
            profile = connection.execute(
                "SELECT id FROM widget_profiles WHERE owner_id = ? AND id = ?",
                (owner, profile_id),
            ).fetchone()
            if profile is None:
                raise DomainError("No se encontró el perfil solicitado.", 404)

            previous = connection.execute(
                "SELECT created_at FROM widget_pairings WHERE profile_id = ?",
                (profile_id,),
            ).fetchone()
            if previous is not None:
                previous_created = _datetime_from_normalized(previous["created_at"])
                elapsed = (now_dt - previous_created).total_seconds()
                if elapsed < PAIRING_COOLDOWN_SECONDS:
                    raise DomainError("Espera antes de emitir otro código para este perfil.", 429)

            pairing_code = new_pairing_code()
            connection.execute(
                """INSERT INTO widget_pairings
                   (profile_id, code_hash, created_at, expires_at, consumed_at)
                   VALUES (?, ?, ?, ?, NULL)
                   ON CONFLICT(profile_id) DO UPDATE SET
                       code_hash = excluded.code_hash,
                       created_at = excluded.created_at,
                       expires_at = excluded.expires_at,
                       consumed_at = NULL""",
                (profile_id, digest_secret(pairing_code), now, expires_at),
            )
            self._touch(connection)
            return {"pairing_code": pairing_code, "expires_at": expires_at}

    def register_widget_grant(
        self,
        identity: Mapping[str, Any],
        pairing_code: str,
        installation_id: str,
        device_name: str,
    ) -> dict[str, Any]:
        identity_data = _exact_object(identity, {"issuer", "subject", "client_id"}, "identity")
        issuer = _identity_value(identity_data["issuer"], "issuer", maximum=2048)
        subject = _validate_profile_owner(identity_data["subject"])
        client_id = _identity_value(identity_data["client_id"], "client_id", maximum=255)
        try:
            pairing_code = validate_random_256(pairing_code)
        except ValueError:
            raise _widget_auth_error() from None
        try:
            installation_id = validate_random_256(installation_id)
        except ValueError:
            raise DomainError("La instalación no es válida.") from None
        device_name = _text(device_name, "device_name", max_length=60)
        pairing_hash = digest_secret(pairing_code)
        installation_hash = digest_secret(installation_id)
        now_dt = self._clock_now()
        now = _format_datetime(now_dt)
        expires_at = _format_datetime(now_dt + timedelta(seconds=WIDGET_GRANT_TTL_SECONDS))

        with self._transaction() as connection:
            # Pruning and code consumption share this transaction with grant creation.
            connection.execute(
                "DELETE FROM widget_pairings WHERE expires_at <= ?", (now,)
            )
            connection.execute(
                "DELETE FROM widget_grants WHERE expires_at <= ?", (now,)
            )
            pairing = connection.execute(
                """SELECT pairing.profile_id, profile.owner_id
                   FROM widget_pairings AS pairing
                   JOIN widget_profiles AS profile ON profile.id = pairing.profile_id
                   WHERE pairing.code_hash = ? AND pairing.consumed_at IS NULL
                     AND pairing.expires_at > ?""",
                (pairing_hash, now),
            ).fetchone()
            if pairing is None or pairing["owner_id"] != subject:
                raise _widget_auth_error()

            profile_id = pairing["profile_id"]
            connection.execute(
                """DELETE FROM widget_grants
                   WHERE profile_id = ? AND issuer = ? AND subject = ? AND client_id = ?
                     AND installation_hash = ?""",
                (profile_id, issuer, subject, client_id, installation_hash),
            )
            active_count = int(connection.execute(
                "SELECT COUNT(*) FROM widget_grants WHERE profile_id = ? AND expires_at > ?",
                (profile_id, now),
            ).fetchone()[0])
            if active_count >= MAX_WIDGET_GRANTS_PER_PROFILE:
                raise DomainError("El perfil ya tiene el máximo de dispositivos enlazados.", 409)

            grant_id = uuid.uuid4().hex
            widget_token = new_widget_token()
            connection.execute(
                """INSERT INTO widget_grants
                   (id, profile_id, issuer, subject, client_id, installation_hash,
                    token_hash, device_name, created_at, expires_at, last_seen_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
                (
                    grant_id, profile_id, issuer, subject, client_id, installation_hash,
                    digest_secret(widget_token), device_name, now, expires_at,
                ),
            )
            consumed = connection.execute(
                """UPDATE widget_pairings SET consumed_at = ?
                   WHERE profile_id = ? AND code_hash = ? AND consumed_at IS NULL
                     AND expires_at > ?""",
                (now, profile_id, pairing_hash, now),
            )
            if consumed.rowcount != 1:
                raise _widget_auth_error()
            self._touch(connection)
            grant = {
                "id": grant_id,
                "profile_id": profile_id,
                "device_name": device_name,
                "created_at": now,
                "expires_at": expires_at,
                "last_seen_at": None,
            }
        return {"grant": grant, "widget_token": widget_token}

    def list_widget_grants(self, owner_id: str, profile_id: str) -> dict[str, Any]:
        owner = _validate_profile_owner(owner_id)
        profile_id = _profile_identifier(profile_id)
        now = self._now()
        with self._read_transaction() as connection:
            profile = connection.execute(
                "SELECT id FROM widget_profiles WHERE owner_id = ? AND id = ?",
                (owner, profile_id),
            ).fetchone()
            if profile is None:
                raise DomainError("No se encontró el perfil solicitado.", 404)
            rows = connection.execute(
                """SELECT id, profile_id, device_name, created_at, expires_at, last_seen_at
                   FROM widget_grants WHERE profile_id = ? AND expires_at > ?
                   ORDER BY created_at, id""",
                (profile_id, now),
            ).fetchall()
            return {"items": [_public_widget_grant(row) for row in rows]}

    def revoke_widget_grant(
        self, owner_id: str, profile_id: str, grant_id: str
    ) -> dict[str, bool]:
        owner = _validate_profile_owner(owner_id)
        profile_id = _profile_identifier(profile_id)
        grant_id = _profile_identifier(grant_id)
        with self._transaction() as connection:
            profile = connection.execute(
                "SELECT id FROM widget_profiles WHERE owner_id = ? AND id = ?",
                (owner, profile_id),
            ).fetchone()
            if profile is None:
                raise DomainError("No se encontró el perfil solicitado.", 404)
            deleted = connection.execute(
                "DELETE FROM widget_grants WHERE id = ? AND profile_id = ?",
                (grant_id, profile_id),
            )
            if deleted.rowcount == 0:
                raise DomainError("No se encontró el permiso solicitado.", 404)
            self._touch(connection)
            return {"ok": True}

    def widget_grant_context(self, widget_token: str, grant_id: str) -> dict[str, Any]:
        token_hash = _widget_token_digest(widget_token)
        grant_id = _widget_auth_id(grant_id)
        now = self._now()
        with self._read_transaction() as connection:
            return _widget_grant_context(connection, token_hash, grant_id, now)

    def widget_snapshot_context(self, widget_token: str, grant_id: str) -> dict[str, Any]:
        token_hash = _widget_token_digest(widget_token)
        grant_id = _widget_auth_id(grant_id)
        calculated_at = self._clock_now()
        now = _format_datetime(calculated_at)
        with self._read_transaction() as connection:
            context = _widget_grant_context(connection, token_hash, grant_id, now)
            if profile_kind(context["profile"]["fields"]) == "energy":
                window_start = _format_datetime(calculated_at - timedelta(days=30))
                energy = _manual_widget_energy_aggregate(connection, window_start, now)
                return {
                    **context,
                    "energy": {
                        "calculated_at": now,
                        "window_start": window_start,
                        "window_end": now,
                        **energy,
                    },
                }

            demo_enabled = bool(
                connection.execute(
                    "SELECT demo_enabled FROM app_meta WHERE id = 1"
                ).fetchone()[0]
            )
            telemetry_row = connection.execute(
                "SELECT * FROM telemetry_snapshots ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            telemetry = (
                None if telemetry_row is None else _telemetry_snapshot_item(telemetry_row)
            )
            demo_vehicle = None
            if demo_enabled:
                vehicle = _read_demo_vehicle(connection)
                demo_vehicle = {
                    **{
                        field: vehicle[field]
                        for field in context["profile"]["fields"]
                    },
                    "observed_at": vehicle["observed_at"],
                }
            return {
                **context,
                "mode": "demo" if demo_enabled else "unconfigured",
                "telemetry": telemetry,
                "demo_vehicle": demo_vehicle,
            }

    def revoke_native_widget_grant(self, widget_token: str, grant_id: str) -> dict[str, bool]:
        token_hash = _widget_token_digest(widget_token)
        grant_id = _widget_auth_id(grant_id)
        now = self._now()
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT token_hash, expires_at FROM widget_grants WHERE id = ?",
                (grant_id,),
            ).fetchone()
            if (
                row is None
                or row["expires_at"] <= now
                or not digest_matches(row["token_hash"], token_hash)
            ):
                raise _widget_auth_error()
            connection.execute("DELETE FROM widget_grants WHERE id = ?", (grant_id,))
            self._touch(connection)
            return {"ok": True}

    def revoke_all_widget_grants(self) -> dict[str, bool]:
        """Invalidate native access after an explicit out-of-store DB recovery."""
        with self._transaction() as connection:
            connection.execute("DELETE FROM widget_pairings")
            connection.execute("DELETE FROM widget_grants")
            self._touch(connection)
            return {"ok": True}

    def record_snapshot(self, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        normalized = _normalize_provider_snapshot(snapshot)
        snapshot_id = uuid.uuid4().hex
        received_at = self._now()
        vehicle = normalized["vehicle"]
        with self._transaction() as connection:
            previous_row = None if self._observed_baseline_id is None else connection.execute(
                "SELECT * FROM telemetry_snapshots WHERE id = ?", (self._observed_baseline_id,)
            ).fetchone()
            previous = None if previous_row is None else _telemetry_snapshot_item(previous_row)
            connection.execute(
                """INSERT INTO telemetry_snapshots
                   (id, source, observed_at, provider_received_at, server_received_at,
                    battery_pct, range_km, odometer_km, locked, climate_on, charging,
                    interior_temperature_c, battery_min_temperature_c,
                    front_left_pressure_kpa, front_right_pressure_kpa,
                    rear_left_pressure_kpa, rear_right_pressure_kpa,
                    vehicle_security_code, sentry_code, field_sources_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    snapshot_id, normalized["source"], normalized["observed_at"],
                    normalized["provider_received_at"], received_at,
                    vehicle["battery_pct"], vehicle["range_km"], vehicle["odometer_km"],
                    _nullable_bool_to_int(vehicle["locked"]),
                    _nullable_bool_to_int(vehicle["climate_on"]),
                    _nullable_bool_to_int(vehicle["charging"]),
                    vehicle["interior_temperature_c"],
                    vehicle["battery_min_temperature_c"],
                    vehicle["front_left_pressure_kpa"],
                    vehicle["front_right_pressure_kpa"],
                    vehicle["rear_left_pressure_kpa"],
                    vehicle["rear_right_pressure_kpa"],
                    vehicle["vehicle_security_code"],
                    vehicle["sentry_code"],
                    _encode_json(normalized["field_sources"]),
                ),
            )
            connection.execute(
                """DELETE FROM telemetry_snapshots WHERE id NOT IN (
                       SELECT id FROM telemetry_snapshots
                       ORDER BY rowid DESC LIMIT ?
                   )""",
                (MAX_TELEMETRY_SNAPSHOTS,),
            )
            self._touch(connection)
            row = connection.execute(
                "SELECT * FROM telemetry_snapshots WHERE id = ?", (snapshot_id,)
            ).fetchone()
            stored = _telemetry_snapshot_item(row)
            demo = bool(connection.execute("SELECT demo_enabled FROM app_meta WHERE id = 1").fetchone()[0])
            events, baseline_id = select_changes(previous, stored, demo=demo)
            self._append_observed_events(connection, events)
        # Never move the comparison baseline ahead of a failed transaction.
        self._observed_baseline_id = baseline_id
        return stored

    def observed_events(self, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        limit = _bounded_int(limit, "limit", minimum=1, maximum=100)
        offset = _bounded_int(offset, "offset", minimum=0, maximum=2_147_483_647)
        return super().observed_events(limit, offset)

    def telemetry_latest(self) -> dict[str, Any] | None:
        with self._read_transaction() as connection:
            return _read_latest_telemetry(connection)

    def mqtt_context(self) -> dict[str, Any]:
        """Lectura consistente para el puente; nunca consulta al proveedor."""
        with self._read_transaction() as connection:
            demo_enabled = bool(connection.execute(
                "SELECT demo_enabled FROM app_meta WHERE id = 1"
            ).fetchone()[0])
            row = connection.execute(
                "SELECT * FROM telemetry_snapshots ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            return {"demo_enabled": demo_enabled,
                    "snapshot": None if row is None else _telemetry_snapshot_item(row)}

    @staticmethod
    def _abrp_scope(scope: str) -> None:
        if type(scope) is not str or not re.fullmatch(r"[0-9a-f]{64}", scope):
            raise DomainError("La vinculación ABRP no es válida.")

    def abrp_context(self, scope: str) -> dict[str, Any]:
        """Preferencia privada y lectura local coherente, sin consultar el coche."""
        self._abrp_scope(scope)
        with self._read_transaction() as connection:
            preference = connection.execute("SELECT * FROM abrp_preference WHERE id = 1").fetchone()
            same_scope = preference["scope"] == scope
            demo = bool(connection.execute("SELECT demo_enabled FROM app_meta WHERE id = 1").fetchone()[0])
            return {
                "demo_enabled": demo,
                "snapshot": None if demo else _read_latest_telemetry(connection),
                "consent": bool(same_scope and preference["enabled"]),
                "generation": preference["generation"],
                "last_attempt_key": preference["last_attempt_key"] if same_scope else None,
                "last_attempt_at": preference["last_attempt_at"] if same_scope else None,
            }

    def set_abrp_consent(self, scope: str, enabled: bool) -> dict[str, Any]:
        self._abrp_scope(scope)
        if type(enabled) is not bool:
            raise DomainError("El consentimiento ABRP debe ser booleano.")
        with self._transaction() as connection:
            if enabled and bool(connection.execute("SELECT demo_enabled FROM app_meta WHERE id = 1").fetchone()[0]):
                raise DomainError("Sal de la demostración para compartir lecturas con ABRP.", 409)
            current = connection.execute("SELECT * FROM abrp_preference WHERE id = 1").fetchone()
            generation = current["generation"] + 1
            same_scope = current["scope"] == scope
            connection.execute("""UPDATE abrp_preference SET scope = ?, enabled = ?, generation = ?,
                last_attempt_key = ?, last_attempt_at = ? WHERE id = 1""",
                (scope, int(enabled), generation,
                 current["last_attempt_key"] if same_scope else None,
                 current["last_attempt_at"] if same_scope else None))
            return {"consent": enabled, "generation": generation}

    def reserve_abrp(self, scope: str, generation: int, key: str, snapshot_id: str, *,
                     attempt_at: datetime, send_interval_seconds: int = 300) -> bool:
        """Reserva duradera antes del envío: no reintentar una respuesta ambigua."""
        self._abrp_scope(scope)
        if (type(generation) is not int or generation < 0 or type(key) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", key) or type(snapshot_id) is not str
                or not _ID_RE.fullmatch(snapshot_id) or not isinstance(attempt_at, datetime)
                or attempt_at.tzinfo is None or attempt_at.utcoffset() is None
                or type(send_interval_seconds) is not int or not 60 <= send_interval_seconds <= 3600):
            raise DomainError("La reserva ABRP no es válida.")
        now = attempt_at.astimezone(timezone.utc)
        with self._transaction() as connection:
            preference = connection.execute("SELECT * FROM abrp_preference WHERE id = 1").fetchone()
            demo = bool(connection.execute("SELECT demo_enabled FROM app_meta WHERE id = 1").fetchone()[0])
            latest = connection.execute("SELECT id FROM telemetry_snapshots ORDER BY rowid DESC LIMIT 1").fetchone()
            if (demo or not preference["enabled"] or preference["scope"] != scope
                    or preference["generation"] != generation or preference["last_attempt_key"] == key
                    or latest is None or latest["id"] != snapshot_id):
                return False
            if preference["last_attempt_at"] is not None:
                try:
                    previous = _datetime_from_normalized(_parse_datetime(
                        preference["last_attempt_at"], "last_attempt_at"))
                except DomainError:
                    return False
                if (now - previous).total_seconds() < send_interval_seconds:
                    return False
            connection.execute("""UPDATE abrp_preference SET last_attempt_key = ?, last_attempt_at = ?
                WHERE id = 1""", (key, _format_datetime(now)))
            return True

    def telemetry_history(
        self, limit: int = MAX_TELEMETRY_HISTORY, offset: int = 0
    ) -> dict[str, Any]:
        limit = _bounded_int(limit, "limit", minimum=1, maximum=MAX_TELEMETRY_HISTORY)
        offset = _bounded_int(offset, "offset", minimum=0, maximum=2_147_483_647)
        with self._read_transaction() as connection:
            total = int(connection.execute(
                "SELECT COUNT(*) FROM telemetry_snapshots"
            ).fetchone()[0])
            rows = connection.execute(
                """SELECT * FROM telemetry_snapshots
                   ORDER BY rowid DESC LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
            return {
                "items": [_telemetry_snapshot_item(row) for row in rows],
                "total": total,
                "limit": limit,
                "offset": offset,
            }

    def energy_analytics(self, days: int = 30) -> dict[str, Any]:
        days = _bounded_int(days, "days", minimum=1, maximum=MAX_ANALYTICS_DAYS)
        with self._read_transaction() as connection:
            window_end = self._clock_now()
            window_start = window_end - timedelta(days=days)
            start_text = _format_datetime(window_start)
            end_text = _format_datetime(window_end)

            manual_months = _monthly_charge_aggregates(
                connection, "manual_charges", start_text, end_text
            )
            demo_enabled = bool(
                connection.execute("SELECT demo_enabled FROM app_meta WHERE id = 1").fetchone()[0]
            )
            demo_months = (
                _monthly_charge_aggregates(connection, "demo_charges", start_text, end_text)
                if demo_enabled
                else {}
            )
            months: list[dict[str, Any]] = []
            for month in _months_intersecting(window_start, window_end):
                months.append(
                    {
                        "month": month,
                        "manual": _charge_aggregate(manual_months.get(month)),
                        "demo": _charge_aggregate(demo_months.get(month)),
                    }
                )
            manual = _combine_charge_aggregates(manual_months.values())
            demo = _combine_charge_aggregates(demo_months.values())

            snapshot_rows = connection.execute(
                "SELECT * FROM telemetry_snapshots ORDER BY rowid DESC LIMIT ?",
                (MAX_TELEMETRY_SNAPSHOTS,),
            ).fetchall()
            # Reuse validation only for identical stored values and ingestion
            # order. Include types and float representation to detect corrupt
            # replacements even when Python numeric equality would hide them.
            row_key = (
                tuple(snapshot_rows[0].keys()) if snapshot_rows else (),
                tuple(
                    tuple((type(value), value.hex() if type(value) is float else value) for value in row)
                    for row in snapshot_rows
                ),
            )
            cached = self._analytics_snapshot_cache
            if cached is not None and cached[0] == row_key:
                snapshots = cached[1]
            else:
                snapshots = tuple(_telemetry_snapshot_item(row) for row in reversed(snapshot_rows))
                # Publish one complete entry only after every row validates.
                self._analytics_snapshot_cache = (row_key, snapshots)
            observations = calculate_observation_coverage(
                snapshots, window_start=window_start, window_end=window_end
            )

            return {
                "window_start": start_text,
                "window_end": end_text,
                "bucket_timezone": "UTC",
                "manual": manual,
                "demo": demo,
                "months": months,
                "observations": observations,
                "estimates": {
                    "consumption_kwh_100km": None,
                    "reason_code": "energy_not_observed",
                },
            }

    def update_settings(self, values: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(values, Mapping):
            raise DomainError("Los ajustes deben ser un objeto JSON.")
        allowed = set(_DEFAULT_SETTINGS)
        if not values or set(values) - allowed:
            raise DomainError("Los ajustes contienen campos vacíos o no admitidos.")

        normalized: dict[str, Any] = {}
        for key, value in values.items():
            if key == "tariff_eur_kwh":
                normalized[key] = _number(value, key, minimum=0, maximum=5)
            elif key == "pricing_method":
                normalized[key] = _validate_pricing_method(value)
            elif key == "vehicle_color":
                normalized[key] = _validate_vehicle_color(value)
            else:
                normalized[key] = _text(
                    value,
                    key,
                    max_length=80,
                    allow_empty=(key == "home_label"),
                )

        with self._transaction() as connection:
            current = _read_settings(connection)
            current.update(normalized)
            connection.execute(
                """UPDATE settings SET vehicle_name = ?, model = ?, tariff_eur_kwh = ?, home_label = ?,
                   pricing_method = ?, vehicle_color = ?
                   WHERE id = 1""",
                (
                    current["vehicle_name"],
                    current["model"],
                    current["tariff_eur_kwh"],
                    current["home_label"],
                    current["pricing_method"],
                    current["vehicle_color"],
                ),
            )
            self._touch(connection)
            return {"ok": True, "settings": current}

    def get_settings(self) -> dict[str, Any]:
        """Return persisted settings without collecting or exposing full app state."""
        with self._read_transaction() as connection:
            return _read_settings(connection)

    def read_pvpc_day(self, day: str) -> dict[str, Any] | None:
        """Read the latest locally cached PVPC day; this method never calls a provider."""
        day = _parse_date(day, "date")
        with self._read_transaction() as connection:
            row = connection.execute(
                """SELECT date, region, timezone, source, fetched_at, intervals_json
                   FROM pvpc_day_cache WHERE date = ? ORDER BY id DESC LIMIT 1""",
                (day,),
            ).fetchone()
            if row is None:
                return None
            return {
                "date": row["date"],
                "region": row["region"],
                "timezone": row["timezone"],
                "source": row["source"],
                "fetched_at": row["fetched_at"],
                "intervals": json.loads(row["intervals_json"]),
            }

    def cache_pvpc_day(self, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        """Persist a validated provider day snapshot and retain the newest 400 ingestions."""
        try:
            from pulse.pvpc import validate_day_snapshot
        except ImportError:
            raise DomainError("El validador PVPC no está disponible.", 500) from None
        try:
            validated = validate_day_snapshot(snapshot)
        except DomainError:
            raise
        except (TypeError, ValueError, KeyError, AttributeError):
            raise DomainError("El snapshot PVPC no tiene una forma válida.") from None
        normalized = _validate_pvpc_day_snapshot(validated)
        with self._transaction() as connection:
            connection.execute(
                """INSERT INTO pvpc_day_cache
                   (date, region, timezone, source, fetched_at, intervals_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    normalized["date"], normalized["region"], normalized["timezone"],
                    normalized["source"], normalized["fetched_at"],
                    _encode_json(normalized["intervals"]),
                ),
            )
            connection.execute(
                """DELETE FROM pvpc_day_cache WHERE id NOT IN
                   (SELECT id FROM pvpc_day_cache ORDER BY id DESC LIMIT 400)"""
            )
        return normalized

    def set_demo(self, enabled: bool) -> dict[str, Any]:
        enabled = _boolean(enabled, "enabled")
        with self._transaction() as connection:
            meta = connection.execute("SELECT demo_enabled FROM app_meta WHERE id = 1").fetchone()
            currently_enabled = bool(meta["demo_enabled"])
            if enabled == currently_enabled:
                return {"ok": True, "mode": "demo" if enabled else "unconfigured"}

            now = self._now()
            if enabled:
                self._reset_demo_vehicle(connection, now)
                _seed_demo(connection, self._clock_now())
                connection.execute(
                    "UPDATE app_meta SET demo_enabled = 1, protection_enabled = 1 WHERE id = 1"
                )
            else:
                for table in (
                    "demo_charges",
                    "demo_journeys",
                    "demo_maintenance",
                    "demo_protection_events",
                    "demo_command_journal",
                ):
                    connection.execute(f"DELETE FROM {table}")
                self._reset_demo_vehicle(connection, now)
                connection.execute(
                    "UPDATE app_meta SET demo_enabled = 0, protection_enabled = 0 WHERE id = 1"
                )
            self._touch(connection)
        self._observed_baseline_id = None
        return {"ok": True, "mode": "demo" if enabled else "unconfigured"}

    def push_demo_enabled(self) -> bool:
        """Read the demo gate used by the private push dispatcher."""
        with self._read_transaction() as connection:
            row = connection.execute(
                "SELECT demo_enabled FROM app_meta WHERE id = 1"
            ).fetchone()
            return bool(row["demo_enabled"])

    def command(self, command: str, confirmed: bool) -> dict[str, Any]:
        confirmed = _boolean(confirmed, "confirmed")
        if not confirmed:
            raise DomainError("Confirma la simulación antes de continuar.")
        command = _text(command, "command", max_length=32)
        if command not in _COMMAND_LABELS:
            raise DomainError("El comando solicitado no existe.")

        with self._transaction() as connection:
            meta = connection.execute("SELECT demo_enabled FROM app_meta WHERE id = 1").fetchone()
            if not bool(meta["demo_enabled"]):
                raise DomainError("Los controles están bloqueados hasta validar una integración.", 409)
            label = _COMMAND_LABELS[command]
            message = (
                f"{label}: simulación completada. No se envió ninguna orden al vehículo."
            )
            now = self._now()
            connection.execute(
                """INSERT INTO demo_command_journal (id, command, status, created_at, message)
                   VALUES (?, ?, 'simulated', ?, ?)""",
                (uuid.uuid4().hex, command, now, message),
            )
            self._apply_demo_command(connection, command, now)
            self._touch(connection)
            return {"status": "simulated", "command": command, "message": message}

    def _apply_demo_command(
        self, connection: sqlite3.Connection, command: str, observed_at: str
    ) -> None:
        changes: dict[str, int] = {}
        if command == "lock":
            changes["locked"] = 1
        elif command == "unlock":
            changes["locked"] = 0
        elif command == "climate_on":
            changes["climate_on"] = 1
        elif command == "climate_off":
            changes["climate_on"] = 0
        elif command == "charge_start":
            changes["charging"] = 1
        elif command == "charge_stop":
            changes["charging"] = 0
        assignments = ", ".join(f"{field} = ?" for field in changes)
        connection.execute(
            f"UPDATE demo_vehicle SET {assignments}, observed_at = ? WHERE id = 1",
            (*changes.values(), observed_at),
        )

    def set_protection(self, enabled: bool) -> dict[str, Any]:
        enabled = _boolean(enabled, "enabled")
        with self._transaction() as connection:
            meta = connection.execute("SELECT demo_enabled FROM app_meta WHERE id = 1").fetchone()
            if not bool(meta["demo_enabled"]):
                raise DomainError("La protección solo está disponible en la demo.", 409)
            connection.execute(
                "UPDATE app_meta SET protection_enabled = ? WHERE id = 1", (int(enabled),)
            )
            self._touch(connection)
            return {"ok": True, "enabled": enabled}

    def simulate_event(self) -> dict[str, Any]:
        with self._transaction() as connection:
            meta = connection.execute(
                "SELECT demo_enabled, protection_enabled FROM app_meta WHERE id = 1"
            ).fetchone()
            if not bool(meta["demo_enabled"]):
                raise DomainError("Los eventos de protección solo se simulan en la demo.", 409)
            event = {
                "id": uuid.uuid4().hex,
                "created_at": self._now(),
                "kind": "movement",
                "message": "Movimiento ficticio detectado en una zona de demostración.",
            }
            connection.execute(
                """INSERT INTO demo_protection_events (id, created_at, kind, message)
                   VALUES (?, ?, ?, ?)""",
                (event["id"], event["created_at"], event["kind"], event["message"]),
            )
            self._touch(connection)
            return event

    def reserve_charge_creation(self, owner_id: str) -> dict[str, str]:
        """Create a server-generated, owner-bound request with a seven-day TTL."""
        owner = _validate_profile_owner(owner_id)
        reservation: dict[str, str] | None = None
        capacity_reached = False
        with self._transaction() as connection:
            now_value = self._clock_now()
            now = _format_datetime(now_value)
            expires_at = _format_datetime(now_value + timedelta(days=REQUEST_TTL_DAYS))
            connection.execute(
                "DELETE FROM manual_charge_requests WHERE expires_at <= ?", (now,)
            )
            owner_count = int(connection.execute(
                "SELECT COUNT(*) FROM manual_charge_requests WHERE owner_id = ?", (owner,)
            ).fetchone()[0])
            global_count = int(connection.execute(
                "SELECT COUNT(*) FROM manual_charge_requests"
            ).fetchone()[0])
            if owner_count >= MAX_REQUESTS_PER_OWNER or global_count >= MAX_REQUESTS_GLOBAL:
                capacity_reached = True
            else:
                request_id = uuid.uuid4().hex
                connection.execute(
                    """INSERT INTO manual_charge_requests
                       (id, owner_id, created_at, expires_at)
                       VALUES (?, ?, ?, ?)""",
                    (request_id, owner, now, expires_at),
                )
                reservation = {"id": request_id, "expires_at": expires_at}
        if capacity_reached:
            raise DomainError("Se alcanzó el límite de solicitudes de carga pendientes.", 429)
        if reservation is None:
            raise DomainError("No se pudo reservar la solicitud de carga.", 500)
        return reservation

    def preflight_charge_creation(
        self, owner_id: str, request_id: str, values: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Check ownership, expiry, and a prior successful attempt before pricing."""
        owner = _validate_profile_owner(owner_id)
        if not valid_request_id(request_id):
            raise DomainError("La solicitud de creación ya no está disponible.", 409)
        canonical_attempt = _canonical_charge_creation_attempt(values)
        fingerprint = attempt_fingerprint(canonical_attempt)
        with self._read_transaction() as connection:
            now_value = self._clock_now()
            reservation = connection.execute(
                "SELECT * FROM manual_charge_requests WHERE id = ?", (request_id,)
            ).fetchone()
            if (
                reservation is None
                or reservation["owner_id"] != owner
                or not _manual_charge_request_is_current(reservation, now_value)
            ):
                raise DomainError("La solicitud de creación ya no está disponible.", 409)
            if not _manual_charge_request_link_is_valid(reservation):
                raise DomainError("La solicitud de creación ya no está disponible.", 409)
            previous_fingerprint = reservation["attempt_fingerprint"]
            if previous_fingerprint is None:
                return {"fingerprint": fingerprint, "charge": None}
            if previous_fingerprint != fingerprint:
                raise DomainError("La solicitud ya se utilizó con otros datos.", 409)
            charge = connection.execute(
                "SELECT * FROM manual_charges WHERE id = ?", (reservation["charge_id"],)
            ).fetchone()
            if charge is None:
                raise DomainError("La carga de esta solicitud ya no existe.", 409)
            return {
                "fingerprint": fingerprint,
                "charge": _charge_item(charge, "manual"),
            }

    def add_charge(
        self,
        values: Mapping[str, Any],
        *,
        pricing_snapshot: Mapping[str, Any] | None = None,
        request_id: str | None = None,
        owner_id: str | None = None,
        request_attempt: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(values, Mapping) or set(values) - {
            "started_at", "kwh", "ended_at", "pricing_method"
        } or not {"started_at", "kwh"} <= set(values):
            raise DomainError("La carga contiene campos vacíos o no admitidos.")
        started_at = _parse_datetime(values["started_at"], "started_at")
        kwh = _number(values["kwh"], "kwh", minimum=0, maximum=500, exclusive_minimum=True)
        ended_at = _parse_optional_datetime(values.get("ended_at"), "ended_at")

        request_owner = None
        canonical_attempt = None
        fingerprint = None
        if request_id is not None:
            if not valid_request_id(request_id) or request_attempt is None:
                raise DomainError("La solicitud de creación ya no está disponible.", 409)
            request_owner = _validate_profile_owner(owner_id)
            canonical_attempt = _canonical_charge_creation_attempt(request_attempt)
            if (
                canonical_attempt["started_at"] != started_at
                or canonical_attempt["ended_at"] != ended_at
                or canonical_attempt["kwh"] != kwh
                or (
                    "pricing_method" in canonical_attempt
                    and values.get("pricing_method") != canonical_attempt["pricing_method"]
                )
            ):
                raise DomainError("La carga no coincide con el intento reservado.", 409)
            fingerprint = attempt_fingerprint(canonical_attempt)
        elif owner_id is not None or request_attempt is not None:
            raise DomainError("La solicitud de creación ya no está disponible.", 409)

        with self._transaction() as connection:
            if request_id is not None:
                now_value = self._clock_now()
                now = _format_datetime(now_value)
                reservation = connection.execute(
                    "SELECT * FROM manual_charge_requests WHERE id = ?", (request_id,)
                ).fetchone()
                if (
                    reservation is None
                    or reservation["owner_id"] != request_owner
                    or not _manual_charge_request_is_current(reservation, now_value)
                ):
                    raise DomainError("La solicitud de creación ya no está disponible.", 409)
                if not _manual_charge_request_link_is_valid(reservation):
                    raise DomainError("La solicitud de creación ya no está disponible.", 409)
                previous_fingerprint = reservation["attempt_fingerprint"]
                if previous_fingerprint is not None:
                    if previous_fingerprint != fingerprint:
                        raise DomainError("La solicitud ya se utilizó con otros datos.", 409)
                    charge = connection.execute(
                        "SELECT * FROM manual_charges WHERE id = ?", (reservation["charge_id"],)
                    ).fetchone()
                    if charge is None:
                        raise DomainError("La carga de esta solicitud ya no existe.", 409)
                    return _read_charge(connection, "manual_charges", charge["id"], "manual")

            settings = _read_settings(connection)
            method = values.get("pricing_method", settings["pricing_method"])
            _validate_pricing_method(method)
            if (
                canonical_attempt is not None
                and "pricing_method" in canonical_attempt
                and method != canonical_attempt["pricing_method"]
            ):
                raise DomainError("La carga no coincide con el intento reservado.", 409)
            _validate_charge_window(started_at, ended_at)
            if method == "pvpc":
                if ended_at is None:
                    raise DomainError("La tarificación PVPC requiere ended_at.")
                cost, tariff, pricing = _validate_pvpc_quote(
                    pricing_snapshot, started_at, ended_at, kwh
                )
            else:
                if pricing_snapshot is not None:
                    raise DomainError("Una carga manual no admite un snapshot de precio PVPC.")
                tariff = settings["tariff_eur_kwh"]
                cost, cents = _money_snapshot(kwh, tariff)
                pricing = None
            cents = _cents_if_exact(cost)
            charge_id = uuid.uuid4().hex
            connection.execute(
                """INSERT INTO manual_charges
                   (id, started_at, kwh, cost_eur, tariff_eur_kwh, cost_cents,
                    ended_at, pricing_method, pricing_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    charge_id, started_at, kwh, cost, tariff, cents, ended_at,
                    method, _encode_json(pricing) if pricing is not None else None,
                ),
            )
            if request_id is not None:
                cursor = connection.execute(
                    """UPDATE manual_charge_requests
                       SET attempt_fingerprint = ?, charge_id = ?
                       WHERE id = ? AND owner_id = ? AND expires_at > ?
                         AND attempt_fingerprint IS NULL AND charge_id IS NULL""",
                    (fingerprint, charge_id, request_id, request_owner, now),
                )
                if cursor.rowcount != 1:
                    raise DomainError("La solicitud de creación ya no está disponible.", 409)
            self._touch(connection)
            return _read_charge(connection, "manual_charges", charge_id, "manual")

    def get_charge(self, id: str) -> dict[str, Any]:
        charge_id = _identifier(id)
        with self._read_transaction() as connection:
            row = connection.execute(
                "SELECT * FROM manual_charges WHERE id = ?", (charge_id,)
            ).fetchone()
            if row is None:
                raise DomainError("No se encontró la carga manual solicitada.", 404)
            return _charge_item(row, "manual")

    def update_charge(
        self,
        id: str,
        values: Mapping[str, Any],
        *,
        pricing_snapshot: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        charge_id = _identifier(id)
        allowed = {"started_at", "kwh", "ended_at", "pricing_method", "tariff_eur_kwh"}
        if not isinstance(values, Mapping) or not values or set(values) - allowed:
            raise DomainError("La carga contiene campos vacíos o no admitidos.")
        normalized: dict[str, Any] = {}
        if "started_at" in values:
            normalized["started_at"] = _parse_datetime(values["started_at"], "started_at")
        if "kwh" in values:
            normalized["kwh"] = _number(
                values["kwh"], "kwh", minimum=0, maximum=500, exclusive_minimum=True
            )
        if "ended_at" in values:
            normalized["ended_at"] = _parse_optional_datetime(values["ended_at"], "ended_at")
        if "pricing_method" in values:
            normalized["pricing_method"] = _validate_pricing_method(values["pricing_method"])
        if "tariff_eur_kwh" in values:
            normalized["tariff_eur_kwh"] = _number(
                values["tariff_eur_kwh"], "tariff_eur_kwh", minimum=0, maximum=5
            )

        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM manual_charges WHERE id = ?", (charge_id,)
            ).fetchone()
            if row is None:
                raise DomainError("No se encontró la carga manual solicitada.", 404)
            started_at = normalized.get("started_at", row["started_at"])
            ended_at = normalized.get("ended_at", row["ended_at"])
            kwh = normalized.get("kwh", float(row["kwh"]))
            old_method = row["pricing_method"] or "manual"
            method = normalized.get("pricing_method", old_method)
            _validate_charge_window(started_at, ended_at)

            if method == "pvpc":
                if "tariff_eur_kwh" in normalized:
                    raise DomainError("No se puede editar manualmente la tarifa de una carga PVPC.")
                if ended_at is None:
                    raise DomainError("La tarificación PVPC requiere ended_at.")
                changed_pricing_inputs = (
                    started_at != row["started_at"]
                    or ended_at != row["ended_at"]
                    or kwh != float(row["kwh"])
                    or method != old_method
                )
                if pricing_snapshot is not None:
                    cost, tariff, pricing = _validate_pvpc_quote(
                        pricing_snapshot, started_at, ended_at, kwh
                    )
                    pricing_json = _encode_json(pricing)
                    cents = _cents_if_exact(cost)
                elif changed_pricing_inputs:
                    raise DomainError(
                        "Al cambiar una carga PVPC se requiere un snapshot de precio actualizado."
                    )
                else:
                    cost = float(row["cost_eur"])
                    tariff = float(row["tariff_eur_kwh"])
                    cents = row["cost_cents"]
                    pricing_json = row["pricing_json"]
            else:
                if pricing_snapshot is not None:
                    raise DomainError("Una carga manual no admite un snapshot de precio PVPC.")
                converting_from_pvpc = old_method == "pvpc" or row["pricing_json"] is not None
                tariff = normalized.get(
                    "tariff_eur_kwh",
                    None
                    if converting_from_pvpc
                    else (
                        float(row["tariff_eur_kwh"])
                        if row["tariff_eur_kwh"] is not None
                        else None
                    ),
                )
                recalculate = (
                    converting_from_pvpc
                    or "kwh" in normalized
                    or "tariff_eur_kwh" in normalized
                )
                if recalculate:
                    if tariff is None:
                        raise DomainError(
                            "Indica tariff_eur_kwh para recalcular una carga manual sin tarifa guardada."
                        )
                    cost, cents = _money_snapshot(kwh, tariff)
                else:
                    cost, cents = float(row["cost_eur"]), row["cost_cents"]
                pricing_json = None

            connection.execute(
                """UPDATE manual_charges
                   SET started_at = ?, ended_at = ?, kwh = ?, cost_eur = ?,
                       tariff_eur_kwh = ?, cost_cents = ?, pricing_method = ?, pricing_json = ?
                   WHERE id = ?""",
                (
                    started_at, ended_at, kwh, cost, tariff, cents, method,
                    pricing_json, charge_id,
                ),
            )
            self._touch(connection)
            return _read_charge(connection, "manual_charges", charge_id, "manual")

    def delete_charge(self, id: str) -> dict[str, Any]:
        charge_id = _identifier(id)
        with self._transaction() as connection:
            cursor = connection.execute("DELETE FROM manual_charges WHERE id = ?", (charge_id,))
            if cursor.rowcount == 0:
                raise DomainError("No se encontró la carga manual solicitada.", 404)
            self._touch(connection)
            return {"ok": True, "id": charge_id}

    def add_maintenance(self, values: Mapping[str, Any]) -> dict[str, Any]:
        values = _exact_object(values, {"title", "due_date"}, "mantenimiento")
        title = _text(values["title"], "title", max_length=120)
        due_date = _parse_date(values["due_date"], "due_date")
        item = {
            "id": uuid.uuid4().hex,
            "title": title,
            "due_date": due_date,
            "completed": False,
        }
        with self._transaction() as connection:
            connection.execute(
                """INSERT INTO manual_maintenance (id, title, due_date, completed)
                   VALUES (?, ?, ?, 0)""",
                (item["id"], item["title"], item["due_date"]),
            )
            self._touch(connection)
        return item

    def update_maintenance(self, id: str, values: Mapping[str, Any]) -> dict[str, Any]:
        item_id = _identifier(id)
        allowed = {"title", "due_date", "completed"}
        if not isinstance(values, Mapping) or not values or set(values) - allowed:
            raise DomainError("El mantenimiento contiene campos vacíos o no admitidos.")
        normalized: dict[str, Any] = {}
        if "title" in values:
            normalized["title"] = _text(values["title"], "title", max_length=120)
        if "due_date" in values:
            normalized["due_date"] = _parse_date(values["due_date"], "due_date")
        if "completed" in values:
            normalized["completed"] = _boolean(values["completed"], "completed")
        with self._transaction() as connection:
            table = self._maintenance_table(connection, item_id)
            row = connection.execute(f"SELECT * FROM {table} WHERE id = ?", (item_id,)).fetchone()
            current = {
                "title": row["title"],
                "due_date": row["due_date"],
                "completed": bool(row["completed"]),
            }
            current.update(normalized)
            connection.execute(
                f"UPDATE {table} SET title = ?, due_date = ?, completed = ? WHERE id = ?",
                (current["title"], current["due_date"], int(current["completed"]), item_id),
            )
            self._touch(connection)
            return {"id": item_id, **current}

    def delete_maintenance(self, id: str) -> dict[str, Any]:
        item_id = _identifier(id)
        with self._transaction() as connection:
            table = self._maintenance_table(connection, item_id)
            connection.execute(f"DELETE FROM {table} WHERE id = ?", (item_id,))
            self._touch(connection)
            return {"ok": True, "id": item_id}

    def _maintenance_table(self, connection: sqlite3.Connection, item_id: str) -> str:
        manual = connection.execute(
            "SELECT id FROM manual_maintenance WHERE id = ?", (item_id,)
        ).fetchone()
        if manual is not None:
            return "manual_maintenance"
        demo = connection.execute(
            "SELECT id FROM demo_maintenance WHERE id = ?", (item_id,)
        ).fetchone()
        if demo is not None:
            meta = connection.execute("SELECT demo_enabled FROM app_meta WHERE id = 1").fetchone()
            if bool(meta["demo_enabled"]):
                return "demo_maintenance"
        raise DomainError("No se encontró el mantenimiento solicitado.", 404)

    def complete_maintenance(self, id: str, completed: bool) -> dict[str, Any]:
        result = self.update_maintenance(id, {"completed": completed})
        return {"ok": True, "id": result["id"], "completed": result["completed"]}

    def export_data(self, *, owner_id: str | None = None) -> dict[str, Any]:
        owner = None if owner_id is None else _validate_profile_owner(owner_id)
        with self._read_transaction() as connection:
            meta = connection.execute("SELECT * FROM app_meta WHERE id = 1").fetchone()
            demo = bool(meta["demo_enabled"])
            charges = _all_charges(connection, "manual_charges", "manual")
            maintenance = _read_maintenance_with_source(
                connection, "manual_maintenance", "manual"
            )
            journeys: list[dict[str, Any]] = []
            events: list[dict[str, Any]] = []
            command_history: list[dict[str, Any]] = []
            if demo:
                charges += _all_charges(connection, "demo_charges", "demo")
                maintenance += _read_maintenance_with_source(
                    connection, "demo_maintenance", "demo"
                )
                journeys = _read_journeys(connection)
                events = _read_events(connection)
                command_history = _read_command_history(connection, limit=None)
            profile_query = "SELECT * FROM widget_profiles"
            profile_args: tuple[Any, ...] = ()
            if owner is not None:
                profile_query += " WHERE owner_id = ?"
                profile_args = (owner,)
            profile_query += " ORDER BY owner_id, created_at, id"
            widget_profiles = [
                _widget_profile_export(row)
                for row in connection.execute(profile_query, profile_args).fetchall()
            ]
            telemetry_snapshots = [
                _telemetry_snapshot_item(row)
                for row in connection.execute(
                    """SELECT * FROM telemetry_snapshots
                       ORDER BY rowid DESC"""
                ).fetchall()
            ]
            charger = self._export_charger(connection)
            charger_pricing = self._export_charger_pricing(connection)
            charger_energy = self._export_charger_energy(connection)
            charger_pricing_history = self._export_charger_pricing_history(connection)
            observed_events = self._export_observed_events(connection)
            result = {
                "schema_version": EXPORT_VERSION,
                "exported_at": self._now(),
                "mode": "demo" if demo else "unconfigured",
                "settings": _read_settings(connection),
                "charges": sorted(charges, key=lambda item: (item["started_at"], item["id"])),
                "journeys": journeys,
                "maintenance": sorted(
                    maintenance, key=lambda item: (item["due_date"], item["id"])
                ),
                "protection": {
                    "enabled": bool(meta["protection_enabled"]) if demo else False,
                    "events": events,
                },
                "command_history": command_history,
                "widget_profiles": widget_profiles,
                "telemetry_snapshots": telemetry_snapshots,
                "charger": charger,
                "charger_pricing": charger_pricing,
                "charger_energy": charger_energy,
                "charger_pricing_history": charger_pricing_history,
                "observed_events": observed_events,
            }
            if demo:
                result["demo_vehicle"] = _read_demo_vehicle(connection)
            try:
                encoded = json.dumps(
                    result, allow_nan=False, separators=(",", ":"), ensure_ascii=False
                ).encode("utf-8")
            except (TypeError, ValueError, UnicodeEncodeError):
                raise DomainError("No se pudo validar el documento de exportación.", 500) from None
            if len(encoded) > MAX_V15_IMPORT_BYTES:
                raise DomainError(
                    "La exportación supera el tamaño máximo admitido para una restauración.", 413
                )
            return result

    def validate_import(self, data: Any, *, owner_id: str | None = None) -> dict[str, bool]:
        """Validate a complete restore without opening a write transaction."""
        self._prepare_import(data, owner_id=owner_id)
        return {"ok": True}

    def import_data(
        self, data: Any, *, owner_id: str | None = None
    ) -> dict[str, Any]:
        owner, document = self._prepare_import(data, owner_id=owner_id)
        with self._transaction() as connection:
            # The full document was validated before any write transaction starts.
            # JSON restores never grant permission to an external account.
            connection.execute("""UPDATE abrp_preference SET scope = '', enabled = 0,
                generation = generation + 1, last_attempt_key = NULL, last_attempt_at = NULL
                WHERE id = 1""")
            connection.execute("DELETE FROM manual_charge_requests")
            for table in (
                "manual_charges",
                "demo_charges",
                "demo_journeys",
                "manual_maintenance",
                "demo_maintenance",
                "demo_protection_events",
                "demo_command_journal",
                "telemetry_snapshots",
                "observed_events",
                "charger_energy_intervals",
                "charger_energy_epochs",
                "charger_pricing_history",
                "charger_pricing",
                "charger_sessions",
                "charger_observations",
            ):
                connection.execute(f"DELETE FROM {table}")
            if owner is None:
                connection.execute("DELETE FROM widget_profiles")
            else:
                connection.execute(
                    "DELETE FROM widget_profiles WHERE owner_id = ?", (owner,)
                )
            settings = document["settings"]
            connection.execute(
                """UPDATE settings SET vehicle_name = ?, model = ?, tariff_eur_kwh = ?, home_label = ?,
                   pricing_method = ?, vehicle_color = ?
                   WHERE id = 1""",
                (
                    settings["vehicle_name"],
                    settings["model"],
                    settings["tariff_eur_kwh"],
                    settings["home_label"],
                    settings["pricing_method"],
                    settings["vehicle_color"],
                ),
            )
            for item in document["charges"]:
                table = "demo_charges" if item["source"] == "demo" else "manual_charges"
                cents = _cents_if_exact(item["cost_eur"])
                connection.execute(
                    f"""INSERT INTO {table}
                        (id, started_at, kwh, cost_eur, tariff_eur_kwh, cost_cents,
                         ended_at, pricing_method, pricing_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        item["id"], item["started_at"], item["kwh"], item["cost_eur"],
                        item["tariff_eur_kwh"], cents, item["ended_at"],
                        item["pricing_method"],
                        _encode_json(item["pricing"]) if item["pricing"] is not None else None,
                    ),
                )
            for item in document["journeys"]:
                connection.execute(
                    """INSERT INTO demo_journeys
                       (id, started_at, from_label, to_label, distance_km, duration_min, energy_kwh)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        item["id"], item["started_at"], item["from_label"], item["to_label"],
                        item["distance_km"], item["duration_min"], item["energy_kwh"],
                    ),
                )
            for item in document["maintenance"]:
                table = "demo_maintenance" if item["source"] == "demo" else "manual_maintenance"
                connection.execute(
                    f"INSERT INTO {table} (id, title, due_date, completed) VALUES (?, ?, ?, ?)",
                    (item["id"], item["title"], item["due_date"], int(item["completed"])),
                )
            for event in document["protection"]["events"]:
                connection.execute(
                    """INSERT INTO demo_protection_events (id, created_at, kind, message)
                       VALUES (?, ?, ?, ?)""",
                    (event["id"], event["created_at"], event["kind"], event["message"]),
                )
            for item in document["command_history"]:
                connection.execute(
                    """INSERT INTO demo_command_journal (id, command, status, created_at, message)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        item["id"], item["command"], item["status"], item["created_at"],
                        item["message"],
                    ),
                )
            for item in document["widget_profiles"]:
                if owner is not None and item["owner_id"] != owner:
                    # Kept as a second guard next to the pre-transaction validation.
                    raise DomainError("La importación contiene perfiles de otro usuario.")
                connection.execute(
                    """INSERT INTO widget_profiles
                       (id, owner_id, name, fields_json, appearance, privacy_mode,
                        max_age_minutes, revision, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        item["id"], item["owner_id"], item["name"],
                        _encode_json(item["fields"]), item["appearance"],
                        item["privacy_mode"], item["max_age_minutes"], item["revision"],
                        item["created_at"], item["updated_at"],
                    ),
                )
            # The archive stores telemetry newest-first; reverse it while inserting so SQLite's
            # implicit rowid records the original local receipt order.
            for item in reversed(document["telemetry_snapshots"]):
                vehicle = item["vehicle"]
                connection.execute(
                    """INSERT INTO telemetry_snapshots
                       (id, source, observed_at, provider_received_at, server_received_at,
                        battery_pct, range_km, odometer_km, locked, climate_on, charging,
                        interior_temperature_c, battery_min_temperature_c,
                        front_left_pressure_kpa, front_right_pressure_kpa,
                        rear_left_pressure_kpa, rear_right_pressure_kpa,
                        vehicle_security_code, sentry_code, field_sources_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        item["id"], item["source"], item["observed_at"],
                        item["provider_received_at"], item["server_received_at"],
                        vehicle["battery_pct"], vehicle["range_km"], vehicle["odometer_km"],
                        _nullable_bool_to_int(vehicle["locked"]),
                        _nullable_bool_to_int(vehicle["climate_on"]),
                        _nullable_bool_to_int(vehicle["charging"]),
                        vehicle["interior_temperature_c"],
                        vehicle["battery_min_temperature_c"],
                        vehicle["front_left_pressure_kpa"],
                        vehicle["front_right_pressure_kpa"],
                        vehicle["rear_left_pressure_kpa"],
                        vehicle["rear_right_pressure_kpa"],
                        vehicle["vehicle_security_code"],
                        vehicle["sentry_code"],
                        _encode_json(item["field_sources"]),
                    ),
                )
            self._replace_charger_archive(connection, document["charger"])
            self._replace_charger_pricing_archive(connection, document["charger_pricing"])
            self._replace_charger_energy_archive(connection, document["charger_energy"])
            self._replace_charger_pricing_history_archive(connection, document["charger_pricing_history"])
            if document["schema_version"] < 11:
                self._baseline_charger_pricing_history(connection, origin="archive")
            self._replace_observed_events(connection, document["observed_events"])
            is_demo = document["mode"] == "demo"
            if is_demo:
                self._write_demo_vehicle(
                    connection, document["demo_vehicle"] or _default_demo_vehicle(self._now())
                )
            else:
                self._reset_demo_vehicle(connection, self._now())
            connection.execute(
                """UPDATE app_meta SET demo_enabled = ?, protection_enabled = ? WHERE id = 1""",
                (int(is_demo), int(document["protection"]["enabled"] if is_demo else False)),
            )
            self._touch(connection)
        self._observed_baseline_id = None
        return {"ok": True, "mode": document["mode"]}

    def _prepare_import(
        self, data: Any, *, owner_id: str | None
    ) -> tuple[str | None, dict[str, Any]]:
        owner = None if owner_id is None else _validate_profile_owner(owner_id)
        document = _validate_import(data)
        if owner is not None and any(
            item["owner_id"] != owner for item in document["widget_profiles"]
        ):
            raise DomainError("La importación contiene perfiles de otro usuario.")
        if document["telemetry_snapshots"]:
            future_limit = self._clock_now() + timedelta(seconds=60)
            for item in document["telemetry_snapshots"]:
                server_received_at = datetime.fromisoformat(
                    item["server_received_at"].replace("Z", "+00:00")
                )
                if server_received_at > future_limit:
                    raise DomainError(
                        "server_received_at no puede superar en más de 60 segundos el reloj de Store."
                    )
        try:
            validate_charger_future_dates(document["charger"], self._clock_now())
            validate_charger_energy_future_dates(document["charger_energy"], self._clock_now())
            validate_charger_pricing_history_future_dates(document["charger_pricing_history"], self._clock_now())
        except ChargerDataError as exc:
            raise DomainError(str(exc)) from None
        try:
            validate_observed_event_future_dates(document["observed_events"], self._clock_now())
        except ObservedEventError as exc:
            raise DomainError(str(exc)) from None
        return owner, document

    def _reset_demo_vehicle(self, connection: sqlite3.Connection, observed_at: str) -> None:
        default = _default_demo_vehicle(observed_at)
        self._write_demo_vehicle(connection, default)

    @staticmethod
    def _write_demo_vehicle(connection: sqlite3.Connection, vehicle: Mapping[str, Any]) -> None:
        connection.execute(
            """INSERT INTO demo_vehicle
               (id, battery_pct, range_km, odometer_km, locked, climate_on, charging, observed_at)
               VALUES (1, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET battery_pct=excluded.battery_pct,
                   range_km=excluded.range_km, odometer_km=excluded.odometer_km,
                   locked=excluded.locked, climate_on=excluded.climate_on,
                   charging=excluded.charging, observed_at=excluded.observed_at""",
            (
                vehicle["battery_pct"], vehicle["range_km"], vehicle["odometer_km"],
                int(vehicle["locked"]), int(vehicle["climate_on"]), int(vehicle["charging"]),
                vehicle["observed_at"],
            ),
        )

    def _touch(self, connection: sqlite3.Connection) -> None:
        connection.execute("UPDATE app_meta SET updated_at = ? WHERE id = 1", (self._now(),))


def _require_supported_sqlite() -> None:
    version = sqlite3.sqlite_version_info
    safe = version >= (3, 51, 3) or (3, 44, 6) <= version < (3, 45, 0) or (
        (3, 50, 7) <= version < (3, 51, 0)
    )
    if not safe:
        raise RuntimeError(
            "Pulse requiere SQLite >= 3.51.3 o una rama corregida 3.44.6/3.50.7. "
            f"Runtime detectado: {sqlite3.sqlite_version}."
        )


def _create_v1_schema(connection: sqlite3.Connection, now: str) -> None:
    statements = (
        """CREATE TABLE app_meta (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            demo_enabled INTEGER NOT NULL CHECK (demo_enabled IN (0, 1)),
            protection_enabled INTEGER NOT NULL CHECK (protection_enabled IN (0, 1)),
            updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            vehicle_name TEXT NOT NULL,
            model TEXT NOT NULL,
            tariff_eur_kwh REAL NOT NULL CHECK (tariff_eur_kwh >= 0 AND tariff_eur_kwh <= 5),
            home_label TEXT NOT NULL
        )""",
        """CREATE TABLE manual_charges (
            id TEXT PRIMARY KEY,
            started_at TEXT NOT NULL,
            kwh REAL NOT NULL CHECK (kwh > 0 AND kwh <= 500),
            cost_eur REAL NOT NULL CHECK (cost_eur >= 0 AND cost_eur <= 2500)
        )""",
        """CREATE TABLE demo_charges (
            id TEXT PRIMARY KEY,
            started_at TEXT NOT NULL,
            kwh REAL NOT NULL CHECK (kwh > 0 AND kwh <= 500),
            cost_eur REAL NOT NULL CHECK (cost_eur >= 0 AND cost_eur <= 2500)
        )""",
        """CREATE TABLE demo_journeys (
            id TEXT PRIMARY KEY,
            started_at TEXT NOT NULL,
            from_label TEXT NOT NULL,
            to_label TEXT NOT NULL,
            distance_km REAL NOT NULL CHECK (distance_km > 0 AND distance_km <= 10000),
            duration_min INTEGER NOT NULL CHECK (duration_min > 0 AND duration_min <= 1440),
            energy_kwh REAL NOT NULL CHECK (energy_kwh >= 0 AND energy_kwh <= 2000)
        )""",
        """CREATE TABLE manual_maintenance (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            due_date TEXT NOT NULL,
            completed INTEGER NOT NULL CHECK (completed IN (0, 1))
        )""",
        """CREATE TABLE demo_maintenance (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            due_date TEXT NOT NULL,
            completed INTEGER NOT NULL CHECK (completed IN (0, 1))
        )""",
        """CREATE TABLE demo_protection_events (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            kind TEXT NOT NULL,
            message TEXT NOT NULL
        )""",
    )
    for statement in statements:
        connection.execute(statement)
    connection.execute(
        "INSERT INTO app_meta (id, demo_enabled, protection_enabled, updated_at) VALUES (1, 0, 0, ?)",
        (now,),
    )
    connection.execute(
        """INSERT INTO settings (id, vehicle_name, model, tariff_eur_kwh, home_label)
           VALUES (1, ?, ?, ?, ?)""",
        (
            _DEFAULT_SETTINGS["vehicle_name"], _DEFAULT_SETTINGS["model"],
            _DEFAULT_SETTINGS["tariff_eur_kwh"], _DEFAULT_SETTINGS["home_label"],
        ),
    )


def _default_demo_vehicle(observed_at: str) -> dict[str, Any]:
    return {**_DEFAULT_DEMO_VEHICLE, "observed_at": observed_at}


def _seed_demo(connection: sqlite3.Connection, now: datetime) -> None:
    charge_1 = _format_datetime(now.replace(hour=17, minute=30, second=0, microsecond=0) - timedelta(days=2))
    charge_2 = _format_datetime(now.replace(hour=19, minute=15, second=0, microsecond=0) - timedelta(days=5))
    journey_1 = _format_datetime(now.replace(hour=8, minute=0, second=0, microsecond=0) - timedelta(days=2))
    journey_2 = _format_datetime(now.replace(hour=10, minute=20, second=0, microsecond=0) - timedelta(days=4))
    event_time = _format_datetime(now - timedelta(hours=1))
    due_date = (now.date() + timedelta(days=180)).isoformat()
    connection.executemany(
        """INSERT OR IGNORE INTO demo_charges
           (id, started_at, kwh, cost_eur, tariff_eur_kwh, cost_cents)
           VALUES (?, ?, ?, ?, 0.17, ?)""",
        (
            ("demo-charge-1", charge_1, 18.4, 3.13, 313),
            ("demo-charge-2", charge_2, 26.2, 4.45, 445),
        ),
    )
    connection.executemany(
        """INSERT OR IGNORE INTO demo_journeys
           (id, started_at, from_label, to_label, distance_km, duration_min, energy_kwh)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            (
                "demo-journey-1", journey_1, "Casa ficticia", "Parque ficticio",
                23.4, 34, 4.6,
            ),
            (
                "demo-journey-2", journey_2, "Parque ficticio", "Centro ficticio",
                16.8, 27, 3.5,
            ),
        ),
    )
    connection.execute(
        """INSERT OR IGNORE INTO demo_maintenance (id, title, due_date, completed)
           VALUES ('demo-maintenance-1', 'Revisión de ejemplo', ?, 0)""",
        (due_date,),
    )
    connection.execute(
        """INSERT OR IGNORE INTO demo_protection_events (id, created_at, kind, message)
           VALUES ('demo-event-1', ?, 'movement',
                   'Movimiento ficticio en una zona de demostración.')""",
        (event_time,),
    )


def _command_state(demo: bool) -> list[dict[str, Any]]:
    reason = (
        "Solo se ejecutará una simulación; no se enviarán órdenes al vehículo."
        if demo
        else "Bloqueado hasta que exista una integración validada."
    )
    return [
        {"id": command, "label": label, "enabled": demo, "reason": reason}
        for command, label in _COMMANDS
    ]


def _read_settings(connection: sqlite3.Connection) -> dict[str, Any]:
    row = connection.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    return {
        "vehicle_name": row["vehicle_name"],
        "model": row["model"],
        "tariff_eur_kwh": float(row["tariff_eur_kwh"]),
        "home_label": row["home_label"],
        "pricing_method": row["pricing_method"],
        "vehicle_color": row["vehicle_color"],
    }


def _read_demo_vehicle(connection: sqlite3.Connection) -> dict[str, Any]:
    row = connection.execute("SELECT * FROM demo_vehicle WHERE id = 1").fetchone()
    return {
        "battery_pct": float(row["battery_pct"]),
        "range_km": float(row["range_km"]),
        "odometer_km": float(row["odometer_km"]),
        "locked": bool(row["locked"]),
        "climate_on": bool(row["climate_on"]),
        "charging": bool(row["charging"]),
        "observed_at": row["observed_at"],
    }


def _charge_item(row: sqlite3.Row, source: str) -> dict[str, Any]:
    cost = float(row["cost_cents"] / 100) if row["cost_cents"] is not None else float(row["cost_eur"])
    pricing_json = row["pricing_json"]
    return {
        "id": row["id"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "kwh": float(row["kwh"]),
        "cost_eur": cost,
        "source": source,
        "pricing_method": row["pricing_method"],
        "tariff_eur_kwh": (
            float(row["tariff_eur_kwh"]) if row["tariff_eur_kwh"] is not None else None
        ),
        "pricing": json.loads(pricing_json) if pricing_json is not None else None,
    }


def _read_charge(
    connection: sqlite3.Connection, table: str, charge_id: str, source: str
) -> dict[str, Any]:
    row = connection.execute(f"SELECT * FROM {table} WHERE id = ?", (charge_id,)).fetchone()
    return _charge_item(row, source)


def _list_charges(
    connection: sqlite3.Connection, limit: int, offset: int, include_demo: bool
) -> list[dict[str, Any]]:
    union = """SELECT id, started_at, ended_at, kwh, cost_eur, tariff_eur_kwh,
                      cost_cents, pricing_method, pricing_json, 'manual' AS source
               FROM manual_charges"""
    if include_demo:
        union += """ UNION ALL SELECT id, started_at, ended_at, kwh, cost_eur,
                         tariff_eur_kwh, cost_cents, pricing_method, pricing_json,
                         'demo' AS source FROM demo_charges"""
    rows = connection.execute(
        f"SELECT * FROM ({union}) ORDER BY started_at DESC, id DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return [_charge_item(row, row["source"]) for row in rows]


def _all_charges(connection: sqlite3.Connection, table: str, source: str) -> list[dict[str, Any]]:
    return [
        _charge_item(row, source)
        for row in connection.execute(f"SELECT * FROM {table}").fetchall()
    ]


def _charge_count(connection: sqlite3.Connection, include_demo: bool) -> int:
    count = int(connection.execute("SELECT COUNT(*) FROM manual_charges").fetchone()[0])
    if include_demo:
        count += int(connection.execute("SELECT COUNT(*) FROM demo_charges").fetchone()[0])
    return count


def _charge_totals(connection: sqlite3.Connection, table: str) -> tuple[float, float]:
    row = connection.execute(
        """SELECT COALESCE(SUM(kwh), 0),
                  COALESCE(SUM(CASE WHEN cost_cents IS NULL THEN cost_eur ELSE cost_cents / 100.0 END), 0)
           FROM """ + table
    ).fetchone()
    return float(row[0]), float(row[1])


def _monthly_charge_aggregates(
    connection: sqlite3.Connection, table: str, window_start: str, window_end: str
) -> dict[str, tuple[int, float, float]]:
    if table not in {"manual_charges", "demo_charges"}:
        raise ValueError("Tabla de cargas no permitida.")
    rows = connection.execute(
        f"""SELECT substr(started_at, 1, 7) AS month,
                   COUNT(*) AS sessions,
                   COALESCE(SUM(kwh), 0) AS kwh,
                   COALESCE(SUM(
                       CASE WHEN cost_cents IS NULL THEN cost_eur ELSE cost_cents / 100.0 END
                   ), 0) AS cost_eur
            FROM {table}
            WHERE started_at >= ? AND started_at <= ?
            GROUP BY substr(started_at, 1, 7)
            ORDER BY month""",
        (window_start, window_end),
    ).fetchall()
    return {
        row["month"]: (int(row["sessions"]), float(row["kwh"]), float(row["cost_eur"]))
        for row in rows
    }


def _manual_widget_energy_aggregate(
    connection: sqlite3.Connection, window_start: str, window_end: str
) -> dict[str, Any]:
    """Aggregate only saved manual charges for the widget's bounded window."""
    by_month = _monthly_charge_aggregates(
        connection, "manual_charges", window_start, window_end
    )
    sessions = sum(value[0] for value in by_month.values())
    try:
        aggregate = _combine_charge_aggregates(by_month.values())
        kwh = aggregate["kwh"]
        cost_eur = aggregate["cost_eur"]
    except (OverflowError, ValueError):
        kwh = cost_eur = None

    def finite_in_range(value: Any, minimum: float, maximum: float) -> float | None:
        if type(value) not in (int, float):
            return None
        try:
            number = float(value)
        except OverflowError:
            return None
        if not math.isfinite(number) or not minimum <= number <= maximum:
            return None
        return number

    return {
        "sessions": sessions,
        "kwh": finite_in_range(kwh, 0, 1_000_000_000),
        "cost_eur": finite_in_range(cost_eur, -1_000_000_000, 1_000_000_000),
    }


def _charge_aggregate(value: tuple[int, float, float] | None) -> dict[str, Any]:
    if value is None:
        return {
            "sessions": 0,
            "kwh": 0.0,
            "cost_eur": 0.0,
            "effective_tariff_eur_kwh": None,
        }
    sessions, kwh, cost = value
    return {
        "sessions": sessions,
        "kwh": round(kwh, 3),
        "cost_eur": round(cost, 2),
        "effective_tariff_eur_kwh": round(cost / kwh, 6) if kwh > 0 else None,
    }


def _combine_charge_aggregates(
    values: Any,
) -> dict[str, Any]:
    groups = list(values)
    if not groups:
        return _charge_aggregate(None)
    combined = (
        sum(group[0] for group in groups),
        math.fsum(group[1] for group in groups),
        math.fsum(group[2] for group in groups),
    )
    return _charge_aggregate(combined)


def _months_intersecting(window_start: datetime, window_end: datetime) -> list[str]:
    year, month = window_start.year, window_start.month
    months: list[str] = []
    while (year, month) <= (window_end.year, window_end.month):
        months.append(f"{year:04d}-{month:02d}")
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1
    return months


def _totals_dict(
    kwh: float, cost: float, distance: float, trip_energy_kwh: float
) -> dict[str, float]:
    consumption = trip_energy_kwh * 100 / distance if distance else 0.0
    return {
        "kwh": round(kwh, 3),
        "cost_eur": round(cost, 2),
        "distance_km": round(distance, 1),
        "consumption_kwh_100km": round(consumption, 1),
    }


def _journey_totals(connection: sqlite3.Connection) -> tuple[float, float]:
    row = connection.execute(
        "SELECT COALESCE(SUM(distance_km), 0), COALESCE(SUM(energy_kwh), 0) FROM demo_journeys"
    ).fetchone()
    return float(row[0]), float(row[1])


def _read_journeys(
    connection: sqlite3.Connection, limit: int | None = None
) -> list[dict[str, Any]]:
    query = "SELECT * FROM demo_journeys ORDER BY started_at DESC, id DESC"
    args: tuple[Any, ...] = ()
    if limit is not None:
        query += " LIMIT ?"
        args = (limit,)
    return [
        {
            "id": row["id"],
            "started_at": row["started_at"],
            "from_label": row["from_label"],
            "to_label": row["to_label"],
            "distance_km": float(row["distance_km"]),
            "duration_min": int(row["duration_min"]),
            "energy_kwh": float(row["energy_kwh"]),
        }
        for row in connection.execute(query, args).fetchall()
    ]


def _read_maintenance(
    connection: sqlite3.Connection, table: str, limit: int | None = None
) -> list[dict[str, Any]]:
    query = f"SELECT * FROM {table} ORDER BY due_date, id"
    args: tuple[Any, ...] = ()
    if limit is not None:
        query += " LIMIT ?"
        args = (limit,)
    return [
        {
            "id": row["id"],
            "title": row["title"],
            "due_date": row["due_date"],
            "completed": bool(row["completed"]),
        }
        for row in connection.execute(query, args).fetchall()
    ]


def _read_maintenance_with_source(
    connection: sqlite3.Connection, table: str, source: str
) -> list[dict[str, Any]]:
    return [
        {
            "id": row["id"],
            "title": row["title"],
            "due_date": row["due_date"],
            "completed": bool(row["completed"]),
            "source": source,
        }
        for row in connection.execute(f"SELECT * FROM {table}").fetchall()
    ]


def _read_events(connection: sqlite3.Connection, limit: int | None = None) -> list[dict[str, Any]]:
    query = "SELECT * FROM demo_protection_events ORDER BY created_at DESC, id"
    args: tuple[Any, ...] = ()
    if limit is not None:
        query += " LIMIT ?"
        args = (limit,)
    return [
        {
            "id": row["id"],
            "created_at": row["created_at"],
            "kind": row["kind"],
            "message": row["message"],
        }
        for row in connection.execute(query, args).fetchall()
    ]


def _read_command_history(
    connection: sqlite3.Connection, limit: int | None
) -> list[dict[str, Any]]:
    query = "SELECT * FROM demo_command_journal ORDER BY created_at DESC, id DESC"
    args: tuple[Any, ...] = ()
    if limit is not None:
        query += " LIMIT ?"
        args = (limit,)
    return [
        {
            "id": row["id"],
            "command": row["command"],
            "status": row["status"],
            "created_at": row["created_at"],
            "message": row["message"],
        }
        for row in connection.execute(query, args).fetchall()
    ]


def _validate_profile_owner(value: Any) -> str:
    try:
        return validate_owner_id(value)
    except WidgetProfileValidationError as error:
        raise DomainError(str(error)) from None


def _identity_value(value: Any, name: str, *, maximum: int) -> str:
    normalized = _text(value, name, max_length=maximum)
    if normalized != value:
        raise DomainError(f"{name} debe conservar su forma exacta.")
    return normalized


def _profile_identifier(value: Any) -> str:
    if not isinstance(value, str) or not _UUID_HEX_RE.fullmatch(value):
        raise DomainError("El identificador no tiene un formato válido.")
    return value


def _widget_auth_id(value: Any) -> str:
    if not isinstance(value, str) or not _UUID_HEX_RE.fullmatch(value):
        raise _widget_auth_error()
    return value


def _widget_token_digest(value: Any) -> str:
    try:
        token = validate_random_256(value, prefixed=True)
    except ValueError:
        raise _widget_auth_error() from None
    return digest_secret(token)


def _widget_auth_error() -> DomainError:
    return DomainError("La credencial de widget no es válida.", 401)


def _public_widget_grant(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "profile_id": row["profile_id"],
        "device_name": row["device_name"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "last_seen_at": row["last_seen_at"],
    }


def _widget_grant_context(
    connection: sqlite3.Connection, token_hash: str, grant_id: str, now: str
) -> dict[str, Any]:
    row = connection.execute(
        "SELECT * FROM widget_grants WHERE id = ?", (grant_id,)
    ).fetchone()
    if (
        row is None
        or row["expires_at"] <= now
        or not digest_matches(row["token_hash"], token_hash)
    ):
        raise _widget_auth_error()
    profile_row = connection.execute(
        "SELECT * FROM widget_profiles WHERE id = ?", (row["profile_id"],)
    ).fetchone()
    if profile_row is None:
        raise _widget_auth_error()
    return {
        "profile": _widget_profile_item(profile_row),
        "grant": _public_widget_grant(row),
        "owner_id": profile_row["owner_id"],
        "issuer": row["issuer"],
        "client_id": row["client_id"],
    }


def _validate_widget_profile_export(
    raw: Any, *, allow_v12_fields: bool = True
) -> dict[str, Any]:
    item = _exact_object(
        raw,
        {
            "id", "owner_id", "name", "fields", "appearance", "privacy_mode",
            "max_age_minutes", "revision", "created_at", "updated_at",
        },
        "widget_profile",
    )
    profile_id = item["id"]
    if not isinstance(profile_id, str) or not _UUID_HEX_RE.fullmatch(profile_id):
        raise DomainError("El identificador del perfil no es válido.")
    owner = _validate_profile_owner(item["owner_id"])
    if not allow_v12_fields and (
        not isinstance(item["fields"], list)
        or any(
            not isinstance(field, str) or field not in LEGACY_PROFILE_FIELDS
            for field in item["fields"]
        )
    ):
        raise DomainError("Un formato anterior contiene campos de widget no admitidos.")
    try:
        values = validate_profile_data(
            {
                "name": item["name"],
                "fields": item["fields"],
                "appearance": item["appearance"],
                "privacy_mode": item["privacy_mode"],
                "max_age_minutes": item["max_age_minutes"],
            }
        )
    except WidgetProfileValidationError as error:
        raise DomainError(str(error)) from None
    return {
        "id": profile_id,
        "owner_id": owner,
        **values,
        "revision": _bounded_int(
            item["revision"], "revision", minimum=1, maximum=9_223_372_036_854_775_807
        ),
        "created_at": _parse_datetime(item["created_at"], "created_at"),
        "updated_at": _parse_datetime(item["updated_at"], "updated_at"),
    }


def _widget_profile_item(row: sqlite3.Row) -> dict[str, Any]:
    try:
        exported = _widget_profile_export(row)
    except DomainError:
        raise DomainError("El almacén contiene un perfil de widget inválido.", 500) from None
    return {key: value for key, value in exported.items() if key != "owner_id"}


def _widget_profile_export(row: sqlite3.Row) -> dict[str, Any]:
    try:
        fields = json.loads(row["fields_json"])
    except (json.JSONDecodeError, TypeError):
        raise DomainError("El almacén contiene un perfil de widget inválido.", 500) from None
    try:
        return _validate_widget_profile_export(
            {
                "id": row["id"],
                "owner_id": row["owner_id"],
                "name": row["name"],
                "fields": fields,
                "appearance": row["appearance"],
                "privacy_mode": row["privacy_mode"],
                "max_age_minutes": row["max_age_minutes"],
                "revision": row["revision"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )
    except DomainError:
        raise DomainError("El almacén contiene un perfil de widget inválido.", 500) from None


def _normalize_telemetry_values(
    vehicle_raw: Any,
    field_sources_raw: Any,
    *,
    allow_missing_fields: frozenset[str] = frozenset(),
) -> tuple[dict[str, Any], dict[str, str | None]]:
    if allow_missing_fields:
        if not allow_missing_fields <= set(_TELEMETRY_FIELDS):
            raise DomainError("vehicle no contiene campos permitidos.")
        if not isinstance(vehicle_raw, Mapping) or not isinstance(field_sources_raw, Mapping):
            raise DomainError("vehicle y field_sources deben ser objetos.")
        required_fields = set(_TELEMETRY_FIELDS) - allow_missing_fields
        if (
            not required_fields <= set(vehicle_raw)
            or set(vehicle_raw) - set(_TELEMETRY_FIELDS)
        ):
            raise DomainError("vehicle no contiene campos permitidos.")
        if (
            not required_fields <= set(field_sources_raw)
            or set(field_sources_raw) - set(_TELEMETRY_FIELDS)
        ):
            raise DomainError("field_sources no contiene campos permitidos.")
        vehicle_raw = {
            **vehicle_raw,
            **{field: None for field in allow_missing_fields if field not in vehicle_raw},
        }
        field_sources_raw = {
            **field_sources_raw,
            **{field: None for field in allow_missing_fields if field not in field_sources_raw},
        }
    vehicle = _exact_object(vehicle_raw, set(_TELEMETRY_FIELDS), "vehicle")
    source_map = _exact_object(field_sources_raw, set(_TELEMETRY_FIELDS), "field_sources")
    normalized_vehicle: dict[str, Any] = {}
    normalized_sources: dict[str, str | None] = {}

    for field, (minimum, maximum) in _TELEMETRY_NUMERIC_LIMITS.items():
        value = vehicle[field]
        source = source_map[field]
        if source is not None and (
            not isinstance(source, str) or source not in _TELEMETRY_FIELD_SOURCES[field]
        ):
            raise DomainError(f"field_sources.{field} no es un origen permitido.")
        if value is None:
            if source is not None:
                raise DomainError(f"field_sources.{field} requiere un valor disponible.")
            normalized_vehicle[field] = None
        else:
            if source is None:
                raise DomainError(f"{field} requiere un origen de proveedor reconocido.")
            normalized_vehicle[field] = _number(
                value, field, minimum=minimum, maximum=maximum
            )
        normalized_sources[field] = source

    for field in _TELEMETRY_SECURITY_CODE_FIELDS:
        value = vehicle[field]
        source = source_map[field]
        if source is not None and (
            type(source) is not str or source not in _TELEMETRY_FIELD_SOURCES[field]
        ):
            raise DomainError(f"field_sources.{field} no es un origen permitido.")
        if value is None:
            if source is not None:
                raise DomainError(f"field_sources.{field} requiere un valor disponible.")
            normalized_vehicle[field] = None
        else:
            if type(value) is not int or not 0 <= value <= 255:
                raise DomainError(f"{field} debe ser un entero entre 0 y 255.")
            if source is None:
                raise DomainError(f"{field} requiere un origen de proveedor reconocido.")
            normalized_vehicle[field] = value
        normalized_sources[field] = source

    for field in _TELEMETRY_BOOLEAN_FIELDS:
        value = vehicle[field]
        source = source_map[field]
        if source is not None:
            raise DomainError(f"field_sources.{field} no es un origen permitido.")
        normalized_vehicle[field] = None if value is None else _boolean(value, field)
        normalized_sources[field] = None

    return normalized_vehicle, normalized_sources


def _normalize_provider_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    root = _exact_object(
        snapshot,
        {"source", "observed_at", "provider_received_at", "vehicle", "field_sources"},
        "snapshot",
    )
    if root["source"] != _TELEMETRY_SOURCE:
        raise DomainError("source no corresponde al proveedor admitido.")
    observed_at = (
        None if root["observed_at"] is None else _parse_datetime(root["observed_at"], "observed_at")
    )
    provider_received_at = (
        None
        if root["provider_received_at"] is None
        else _parse_datetime(root["provider_received_at"], "provider_received_at")
    )
    vehicle, field_sources = _normalize_telemetry_values(
        root["vehicle"],
        root["field_sources"],
        allow_missing_fields=_TELEMETRY_METRIC_FIELDS
        | frozenset(_TELEMETRY_SECURITY_CODE_FIELDS),
    )
    return {
        "source": _TELEMETRY_SOURCE,
        "observed_at": observed_at,
        "provider_received_at": provider_received_at,
        "vehicle": vehicle,
        "field_sources": field_sources,
    }


def _validate_telemetry_export(
    raw: Any, *, schema_version: int = EXPORT_VERSION
) -> dict[str, Any]:
    item = _exact_object(
        raw,
        {
            "id", "source", "observed_at", "provider_received_at", "server_received_at",
            "vehicle", "field_sources",
        },
        "telemetry_snapshot",
    )
    snapshot_id = item["id"]
    if not isinstance(snapshot_id, str) or not _UUID_HEX_RE.fullmatch(snapshot_id):
        raise DomainError("El identificador de snapshot no es válido.")
    if item["source"] != _TELEMETRY_SOURCE:
        raise DomainError("El origen del snapshot no es válido.")
    observed_at = (
        None if item["observed_at"] is None else _parse_datetime(item["observed_at"], "observed_at")
    )
    provider_received_at = (
        None
        if item["provider_received_at"] is None
        else _parse_datetime(item["provider_received_at"], "provider_received_at")
    )
    server_received_at = _parse_datetime(item["server_received_at"], "server_received_at")
    vehicle_raw = item["vehicle"]
    sources_raw = item["field_sources"]
    if schema_version < 14:
        expected_fields = set(_TELEMETRY_LEGACY_FIELDS)
    elif schema_version == 14:
        expected_fields = set(_TELEMETRY_FIELDS) - set(_TELEMETRY_SECURITY_CODE_FIELDS)
    else:
        expected_fields = set(_TELEMETRY_FIELDS)
    if (
        not isinstance(vehicle_raw, Mapping)
        or not isinstance(sources_raw, Mapping)
        or set(vehicle_raw) != expected_fields
        or set(sources_raw) != expected_fields
    ):
        raise DomainError("El snapshot de telemetría no es válido.")
    vehicle, field_sources = _normalize_telemetry_values(
        vehicle_raw,
        sources_raw,
        allow_missing_fields=frozenset(set(_TELEMETRY_FIELDS) - expected_fields),
    )
    return {
        "id": snapshot_id,
        "source": _TELEMETRY_SOURCE,
        "observed_at": observed_at,
        "provider_received_at": provider_received_at,
        "server_received_at": server_received_at,
        "vehicle": vehicle,
        "field_sources": field_sources,
    }


def _read_latest_telemetry(connection: sqlite3.Connection) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM telemetry_snapshots ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    return None if row is None else _telemetry_snapshot_item(row)


def _telemetry_snapshot_item(row: sqlite3.Row) -> dict[str, Any]:
    try:
        field_sources = json.loads(row["field_sources_json"])
        stored = _validate_telemetry_export(
            {
                "id": row["id"],
                "source": row["source"],
                "observed_at": row["observed_at"],
                "provider_received_at": row["provider_received_at"],
                "server_received_at": row["server_received_at"],
                "vehicle": {
                    "battery_pct": row["battery_pct"],
                    "range_km": row["range_km"],
                    "odometer_km": row["odometer_km"],
                    "interior_temperature_c": row["interior_temperature_c"],
                    "battery_min_temperature_c": row["battery_min_temperature_c"],
                    "front_left_pressure_kpa": row["front_left_pressure_kpa"],
                    "front_right_pressure_kpa": row["front_right_pressure_kpa"],
                    "rear_left_pressure_kpa": row["rear_left_pressure_kpa"],
                    "rear_right_pressure_kpa": row["rear_right_pressure_kpa"],
                    "vehicle_security_code": row["vehicle_security_code"],
                    "sentry_code": row["sentry_code"],
                    "locked": _nullable_int_to_bool(row["locked"]),
                    "climate_on": _nullable_int_to_bool(row["climate_on"]),
                    "charging": _nullable_int_to_bool(row["charging"]),
                },
                "field_sources": field_sources,
            }
        )
    except (DomainError, json.JSONDecodeError, TypeError):
        raise DomainError("El almacén contiene un snapshot de telemetría inválido.", 500) from None
    return stored


def _nullable_bool_to_int(value: bool | None) -> int | None:
    return None if value is None else int(value)


def _nullable_int_to_bool(value: int | None) -> bool | None:
    if value is None:
        return None
    if type(value) is not int or value not in (0, 1):
        raise DomainError("El almacén contiene un booleano no válido.", 500)
    return bool(value)


def _encode_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError, UnicodeEncodeError):
        raise DomainError("No se pudo serializar un dato de Pulse.", 500) from None


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _exact_object(value: Any, keys: set[str], name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise DomainError(f"{name.capitalize()} debe contener exactamente: {', '.join(sorted(keys))}.")
    return dict(value)


def _text(
    value: Any,
    name: str,
    *,
    max_length: int = MAX_TEXT_LENGTH,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise DomainError(f"{name} debe ser texto.")
    normalized = value.strip()
    if len(normalized) > max_length or (not normalized and not allow_empty):
        raise DomainError(f"{name} tiene una longitud no válida.")
    try:
        normalized.encode("utf-8")
    except UnicodeEncodeError:
        raise DomainError(f"{name} contiene texto no válido.") from None
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise DomainError(f"{name} contiene caracteres no permitidos.")
    return normalized


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise DomainError("El identificador no tiene un formato válido.")
    return value


def _boolean(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise DomainError(f"{name} debe ser booleano.")
    return value


def _number(
    value: Any,
    name: str,
    *,
    minimum: float,
    maximum: float,
    exclusive_minimum: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DomainError(f"{name} debe ser un número finito.")
    try:
        number = float(value)
    except (OverflowError, ValueError):
        raise DomainError(f"{name} debe ser un número finito.") from None
    if not math.isfinite(number):
        raise DomainError(f"{name} debe ser un número finito.")
    if (number <= minimum if exclusive_minimum else number < minimum) or number > maximum:
        raise DomainError(f"{name} está fuera del rango permitido.")
    return number


def _money_snapshot(kwh: float, tariff: float) -> tuple[float, int]:
    try:
        amount = (Decimal(str(kwh)) * Decimal(str(tariff))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    except InvalidOperation:
        raise DomainError("No se pudo calcular el coste de la carga.") from None
    if not amount.is_finite() or amount < 0 or amount > Decimal("2500"):
        raise DomainError("El coste de la carga está fuera del rango permitido.")
    cents = int(amount * 100)
    return float(Decimal(cents) / Decimal(100)), cents


def _validate_pricing_method(value: Any) -> str:
    if not isinstance(value, str) or value not in {"manual", "pvpc"}:
        raise DomainError("pricing_method debe ser manual o pvpc.")
    return value


def _validate_vehicle_color(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in _VEHICLE_COLOR_IDS:
        raise DomainError("vehicle_color debe ser uno de los colores oficiales del B10 o null.")
    return value


def _parse_optional_datetime(value: Any, name: str) -> str | None:
    return None if value is None else _parse_datetime(value, name)


def _datetime_from_normalized(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _validate_charge_window(started_at: str, ended_at: str | None) -> None:
    if ended_at is None:
        return
    start = _datetime_from_normalized(started_at)
    end = _datetime_from_normalized(ended_at)
    if end <= start:
        raise DomainError("ended_at debe ser posterior a started_at.")
    if end - start > timedelta(hours=72):
        raise DomainError("Una carga no puede superar 72 horas.")


def _validate_pvpc_quote(
    snapshot: Any, started_at: str, ended_at: str | None, kwh: float, *, allow_temporal=False
) -> tuple[float, float, dict[str, Any]]:
    if ended_at is None:
        raise DomainError("La tarificación PVPC requiere ended_at.")
    if snapshot is None:
        raise DomainError("La tarificación PVPC requiere un snapshot calculado por el servidor.")
    quote = _exact_object(
        snapshot, {"cost_eur", "tariff_eur_kwh", "pricing"}, "pricing_snapshot"
    )
    cost = _number(quote["cost_eur"], "cost_eur", minimum=-2500, maximum=2500)
    tariff = _number(
        quote["tariff_eur_kwh"], "tariff_eur_kwh", minimum=-5, maximum=5
    )
    if not isinstance(quote["pricing"], Mapping):
        raise DomainError("El snapshot de precios PVPC no es válido.")
    try:
        from pulse.pvpc import validate_pricing_snapshot
    except ImportError:
        raise DomainError("El validador PVPC no está disponible.", 500) from None
    try:
        temporal = quote["pricing"].get("accuracy") == "estimated_intervals"
        if temporal:
            if not allow_temporal:
                raise DomainError("La distribución temporal solo se admite en sesiones del cargador.")
            from pulse.temporal_pvpc import validate_temporal_pricing_snapshot
            pricing = validate_temporal_pricing_snapshot(quote["pricing"], started_at, ended_at, kwh, cost)
        else:
            pricing = validate_pricing_snapshot(quote["pricing"], started_at, ended_at, kwh, cost)
    except DomainError:
        raise
    except (TypeError, ValueError, KeyError, AttributeError):
        raise DomainError("El snapshot de precios PVPC no es válido.") from None
    if not isinstance(pricing, Mapping):
        raise DomainError("El validador PVPC devolvió un snapshot no válido.", 500)
    normalized = pricing if temporal else _validate_pricing_payload(pricing, started_at, ended_at, kwh)
    try:
        weighted_price = sum(
            Decimal(str(interval["kwh"])) * Decimal(str(interval["price_eur_kwh"]))
            for interval in normalized["intervals"]
        ) / Decimal(str(kwh))
    except (InvalidOperation, ZeroDivisionError):
        raise DomainError("No se pudo verificar la tarifa efectiva PVPC.") from None
    if not weighted_price.is_finite() or abs(float(weighted_price) - tariff) > 1e-9:
        raise DomainError("La tarifa efectiva no coincide con los intervalos PVPC.")
    return cost, tariff, normalized


def _validate_pricing_payload(
    value: Mapping[str, Any], started_at: str, ended_at: str, kwh: float
) -> dict[str, Any]:
    pricing = _exact_object(
        value,
        {
            "method", "accuracy", "region", "source", "taxes_included",
            "calculated_at", "intervals",
        },
        "pricing",
    )
    if (
        pricing["method"] != "pvpc"
        or pricing["accuracy"] != "estimated_uniform"
        or pricing["region"] != "peninsula"
        or pricing["source"] != "REE REData PVPC 1001"
        or pricing["taxes_included"] is not False
    ):
        raise DomainError("El snapshot PVPC contiene metadatos no admitidos.")
    calculated_at = _parse_datetime(pricing["calculated_at"], "calculated_at")
    intervals_raw = pricing["intervals"]
    if not isinstance(intervals_raw, list) or not 1 <= len(intervals_raw) <= 300:
        raise DomainError("Los intervalos del snapshot PVPC no son válidos.")
    intervals: list[dict[str, Any]] = []
    last_end: str | None = None
    energy = Decimal(0)
    for raw in intervals_raw:
        interval = _exact_object(
            raw,
            {
                "started_at", "ended_at", "kwh", "price_eur_kwh", "cost_eur",
                "price_fetched_at",
            },
            "pricing_interval",
        )
        interval_start = _parse_datetime(interval["started_at"], "interval.started_at")
        interval_end = _parse_datetime(interval["ended_at"], "interval.ended_at")
        if interval_end <= interval_start or interval_start < started_at or interval_end > ended_at:
            raise DomainError("Un intervalo PVPC queda fuera del periodo de carga.")
        if last_end is not None and interval_start != last_end:
            raise DomainError("Los intervalos PVPC deben ser contiguos y ordenados.")
        interval_kwh = _number(
            interval["kwh"], "interval.kwh", minimum=0, maximum=500,
            exclusive_minimum=True,
        )
        price = _number(
            interval["price_eur_kwh"], "interval.price_eur_kwh", minimum=-5, maximum=5
        )
        interval_cost = _number(
            interval["cost_eur"], "interval.cost_eur", minimum=-2500, maximum=2500
        )
        fetched_at = _parse_datetime(interval["price_fetched_at"], "price_fetched_at")
        energy += Decimal(str(interval_kwh))
        intervals.append(
            {
                "started_at": interval_start,
                "ended_at": interval_end,
                "kwh": interval_kwh,
                "price_eur_kwh": price,
                "cost_eur": interval_cost,
                "price_fetched_at": fetched_at,
            }
        )
        last_end = interval_end
    if intervals[0]["started_at"] != started_at or intervals[-1]["ended_at"] != ended_at:
        raise DomainError("Los intervalos PVPC deben cubrir el periodo completo de carga.")
    if abs(float(energy) - kwh) > 1e-9:
        raise DomainError("La energía de los intervalos PVPC no coincide con la carga.")
    return {
        "method": "pvpc",
        "accuracy": "estimated_uniform",
        "region": "peninsula",
        "source": "REE REData PVPC 1001",
        "taxes_included": False,
        "calculated_at": calculated_at,
        "intervals": intervals,
    }


def _validate_pvpc_day_snapshot(value: Any) -> dict[str, Any]:
    snapshot = _exact_object(
        value,
        {"date", "region", "timezone", "source", "fetched_at", "intervals"},
        "pvpc_day",
    )
    day = _parse_date(snapshot["date"], "date")
    if (
        snapshot["region"] != "peninsula"
        or snapshot["timezone"] != "Europe/Madrid"
        or snapshot["source"] != "REE REData PVPC 1001"
    ):
        raise DomainError("El día PVPC contiene región, zona o fuente no admitidas.")
    fetched_at = _parse_datetime(snapshot["fetched_at"], "fetched_at")
    rows = snapshot["intervals"]
    if not isinstance(rows, list) or not 23 <= len(rows) <= 25:
        raise DomainError("Un día PVPC debe contener 23, 24 o 25 intervalos horarios.")
    madrid = ZoneInfo("Europe/Madrid")
    local_day = date.fromisoformat(day)
    next_day = local_day + timedelta(days=1)
    intervals: list[dict[str, Any]] = []
    prior_end: datetime | None = None
    for raw in rows:
        item = _exact_object(
            raw, {"started_at", "ended_at", "price_eur_kwh"}, "pvpc_day_interval"
        )
        started_at = _parse_datetime(item["started_at"], "started_at")
        ended_at = _parse_datetime(item["ended_at"], "ended_at")
        start_value = _datetime_from_normalized(started_at)
        end_value = _datetime_from_normalized(ended_at)
        if end_value - start_value != timedelta(hours=1):
            raise DomainError("Cada intervalo del día PVPC debe durar una hora.")
        if start_value.astimezone(madrid).date() != local_day:
            raise DomainError("Un intervalo PVPC no corresponde al día indicado.")
        if end_value.astimezone(madrid).date() not in (local_day, next_day):
            raise DomainError("Un intervalo PVPC no corresponde al día indicado.")
        if prior_end is not None and start_value != prior_end:
            raise DomainError("Los intervalos del día PVPC deben ser continuos.")
        price = _number(item["price_eur_kwh"], "price_eur_kwh", minimum=-5, maximum=5)
        intervals.append(
            {"started_at": started_at, "ended_at": ended_at, "price_eur_kwh": price}
        )
        prior_end = end_value
    expected_start = datetime.combine(local_day, datetime.min.time(), tzinfo=madrid)
    expected_end = datetime.combine(next_day, datetime.min.time(), tzinfo=madrid)
    if (
        _datetime_from_normalized(intervals[0]["started_at"]) != expected_start.astimezone(timezone.utc)
        or prior_end != expected_end.astimezone(timezone.utc)
    ):
        raise DomainError("Los intervalos PVPC no cubren el día local completo.")
    return {
        "date": day,
        "region": "peninsula",
        "timezone": "Europe/Madrid",
        "source": "REE REData PVPC 1001",
        "fetched_at": fetched_at,
        "intervals": intervals,
    }


def _cents_if_exact(value: float) -> int | None:
    try:
        decimal_value = Decimal(str(value))
        cents = decimal_value * Decimal(100)
    except InvalidOperation:
        return None
    if not decimal_value.is_finite() or cents != cents.to_integral_value():
        return None
    return int(cents)


def _parse_datetime(value: Any, name: str) -> str:
    if not isinstance(value, str) or len(value) > 64:
        raise DomainError(f"{name} debe ser una fecha ISO con zona horaria.")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        raise DomainError(f"{name} debe ser una fecha ISO con zona horaria.") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DomainError(f"{name} debe incluir una zona horaria.")
    return _format_datetime(parsed)


def _canonical_charge_creation_attempt(values: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize only user intent; tariff defaults and settings are not part of it."""
    allowed = {"started_at", "kwh", "ended_at", "pricing_method"}
    if (
        not isinstance(values, Mapping)
        or set(values) - allowed
        or not {"started_at", "kwh"} <= set(values)
    ):
        raise DomainError("La carga contiene campos vacíos o no admitidos.")
    started_at = _parse_datetime(values["started_at"], "started_at")
    kwh = _number(values["kwh"], "kwh", minimum=0, maximum=500, exclusive_minimum=True)
    ended_at = _parse_optional_datetime(values.get("ended_at"), "ended_at")
    _validate_charge_window(started_at, ended_at)
    canonical = {"started_at": started_at, "ended_at": ended_at, "kwh": kwh}
    if values.get("pricing_method") is not None:
        canonical["pricing_method"] = _validate_pricing_method(values["pricing_method"])
    return canonical


def _manual_charge_request_is_current(
    reservation: Mapping[str, Any], now: datetime
) -> bool:
    """Reject expired, future-created, malformed, or incorrectly bounded ledger rows."""
    try:
        created_at = _datetime_from_normalized(
            _parse_datetime(reservation["created_at"], "created_at")
        )
        expires_at = _datetime_from_normalized(
            _parse_datetime(reservation["expires_at"], "expires_at")
        )
    except (DomainError, KeyError, TypeError):
        return False
    return (
        created_at <= now < expires_at
        and expires_at - created_at == timedelta(days=REQUEST_TTL_DAYS)
    )


def _manual_charge_request_link_is_valid(reservation: Mapping[str, Any]) -> bool:
    """Treat only unlinked reservations or complete, well-shaped success links as valid."""
    fingerprint = reservation["attempt_fingerprint"]
    charge_id = reservation["charge_id"]
    if fingerprint is None or charge_id is None:
        return fingerprint is None and charge_id is None
    return (
        isinstance(fingerprint, str)
        and re.fullmatch(r"[0-9a-f]{64}", fingerprint) is not None
        and valid_request_id(charge_id)
    )


def _parse_date(value: Any, name: str) -> str:
    if not isinstance(value, str) or len(value) != 10:
        raise DomainError(f"{name} debe usar el formato YYYY-MM-DD.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise DomainError(f"{name} debe usar el formato YYYY-MM-DD.") from None
    if parsed.isoformat() != value:
        raise DomainError(f"{name} debe usar el formato YYYY-MM-DD.")
    return value


def _pagination(limit: Any, offset: Any) -> tuple[int, int]:
    return (
        _bounded_int(limit, "limit", minimum=1, maximum=MAX_STATE_CHARGES),
        _bounded_int(offset, "offset", minimum=0, maximum=2_147_483_647),
    )


def _bounded_int(value: Any, name: str, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise DomainError(f"{name} está fuera del rango permitido.")
    return value


def _reject_json_constant(_: str) -> None:
    raise DomainError("La importación contiene un número no finito.")


def _unique_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DomainError("El documento contiene campos JSON duplicados.")
        result[key] = value
    return result


def _validate_import(data: Any) -> dict[str, Any]:
    try:
        if isinstance(data, bytes):
            encoded_size = len(data)
            if encoded_size > MAX_V9_IMPORT_BYTES:
                raise DomainError("La importación supera el tamaño permitido.")
            decoded = json.loads(
                data.decode("utf-8"),
                parse_constant=_reject_json_constant,
                object_pairs_hook=_unique_object_pairs,
            )
        elif isinstance(data, str):
            encoded_size = len(data.encode("utf-8"))
            if encoded_size > MAX_V9_IMPORT_BYTES:
                raise DomainError("La importación supera el tamaño permitido.")
            decoded = json.loads(
                data, parse_constant=_reject_json_constant, object_pairs_hook=_unique_object_pairs
            )
        else:
            encoded = json.dumps(
                data, allow_nan=False, separators=(",", ":"), ensure_ascii=False
            )
            encoded_size = len(encoded.encode("utf-8"))
            if encoded_size > MAX_V9_IMPORT_BYTES:
                raise DomainError("La importación supera el tamaño permitido.")
            decoded = json.loads(encoded, parse_constant=_reject_json_constant)
    except DomainError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError, RecursionError):
        raise DomainError("El documento de importación no es JSON válido.") from None

    if not isinstance(decoded, Mapping):
        raise DomainError("El documento debe ser un objeto JSON.")
    version = decoded.get("schema_version")
    if type(version) is not int or version not in range(1, EXPORT_VERSION + 1):
        raise DomainError("La versión del documento de importación no es compatible.")
    if version in (1, 2) and encoded_size > MAX_IMPORT_BYTES:
        raise DomainError("La importación supera el tamaño permitido para versiones anteriores.")
    if version == 3 and encoded_size > MAX_V3_IMPORT_BYTES:
        raise DomainError("La importación supera el tamaño permitido para la versión 3.")
    if version == 4 and encoded_size > MAX_V4_IMPORT_BYTES:
        raise DomainError("La importación supera el tamaño permitido para la versión 4.")
    if version == 5 and encoded_size > MAX_V5_IMPORT_BYTES:
        raise DomainError("La importación supera el tamaño permitido para la versión 5.")
    if version == 6 and encoded_size > MAX_V6_IMPORT_BYTES:
        raise DomainError("La importación supera el tamaño permitido para la versión 6.")
    if version == 7 and encoded_size > MAX_V7_IMPORT_BYTES:
        raise DomainError("La importación supera el tamaño permitido para la versión 7.")
    if version == 8 and encoded_size > MAX_V8_IMPORT_BYTES:
        raise DomainError("La importación supera el tamaño permitido para la versión 8.")
    if version == 9 and encoded_size > MAX_V9_IMPORT_BYTES:
        raise DomainError("La importación supera el tamaño permitido para la versión 9.")
    if version == 10 and encoded_size > MAX_V10_IMPORT_BYTES:
        raise DomainError("La importación supera el tamaño permitido para la versión 10.")
    if version == 11 and encoded_size > MAX_V11_IMPORT_BYTES:
        raise DomainError("La importación supera el tamaño permitido para la versión 11.")
    if version == 12 and encoded_size > MAX_V12_IMPORT_BYTES:
        raise DomainError("La importación supera el tamaño permitido para la versión 12.")
    if version == 13 and encoded_size > MAX_V13_IMPORT_BYTES:
        raise DomainError("La importación supera el tamaño permitido para la versión 13.")
    if version == 14 and encoded_size > MAX_V14_IMPORT_BYTES:
        raise DomainError("La importación supera el tamaño permitido para la versión 14.")
    if version == 15 and encoded_size > MAX_V15_IMPORT_BYTES:
        raise DomainError("La importación supera el tamaño permitido para la versión 15.")
    mode = decoded.get("mode")
    if mode not in ("demo", "unconfigured"):
        raise DomainError("El modo del documento no es válido.")

    fields = set(_V1_EXPORT_FIELDS)
    if version >= 2:
        fields.add("command_history")
        if mode == "demo" and (version >= 3 or "demo_vehicle" in decoded):
            fields.add("demo_vehicle")
    if version >= 3:
        fields.update({"widget_profiles", "telemetry_snapshots"})
    if version >= 6:
        fields.add("charger")
    if version >= 8:
        fields.add("charger_pricing")
    if version >= 9:
        fields.add("observed_events")
    if version >= 10:
        fields.add("charger_energy")
    if version >= 11:
        fields.add("charger_pricing_history")
    root = _exact_object(decoded, fields, "documento")
    _parse_datetime(root["exported_at"], "exported_at")

    settings_fields = (
        set(_DEFAULT_SETTINGS)
        if version >= 13
        else _V12_SETTINGS_FIELDS if version >= 4
        else _LEGACY_SETTINGS_FIELDS
    )
    settings_raw = _exact_object(root["settings"], settings_fields, "settings")
    settings = {
        "vehicle_name": _text(settings_raw["vehicle_name"], "vehicle_name", max_length=80),
        "model": _text(settings_raw["model"], "model", max_length=80),
        "tariff_eur_kwh": _number(
            settings_raw["tariff_eur_kwh"], "tariff_eur_kwh", minimum=0, maximum=5
        ),
        "home_label": _text(
            settings_raw["home_label"], "home_label", max_length=80, allow_empty=True
        ),
        "pricing_method": (
            _validate_pricing_method(settings_raw["pricing_method"])
            if version >= 4
            else "manual"
        ),
        "vehicle_color": (
            _validate_vehicle_color(settings_raw["vehicle_color"])
            if version >= 13
            else None
        ),
    }

    charges_raw = _collection(root["charges"], "charges")
    charges: list[dict[str, Any]] = []
    charge_ids: set[str] = set()
    for raw in charges_raw:
        keys = {"id", "started_at", "kwh", "cost_eur", "source"}
        if version >= 2:
            keys.add("tariff_eur_kwh")
        if version >= 4:
            keys.update({"ended_at", "pricing_method", "pricing"})
        item = _exact_object(raw, keys, "charge")
        item_id = _identifier(item["id"])
        if item_id in charge_ids:
            raise DomainError("La importación contiene identificadores de carga duplicados.")
        charge_ids.add(item_id)
        source = item["source"]
        if source not in ("manual", "demo"):
            raise DomainError("El origen de una carga no es válido.")
        kwh = _number(
            item["kwh"], "kwh", minimum=0, maximum=500, exclusive_minimum=True
        )
        ended_at = (
            _parse_optional_datetime(item["ended_at"], "ended_at")
            if version >= 4
            else None
        )
        started_at = _parse_datetime(item["started_at"], "started_at")
        _validate_charge_window(started_at, ended_at)
        tariff_raw = item.get("tariff_eur_kwh")
        method = item.get("pricing_method") if version >= 4 else None
        pricing = item.get("pricing") if version >= 4 else None
        if source == "demo":
            cost = _number(item["cost_eur"], "cost_eur", minimum=0, maximum=2500)
            tariff = (
                None
                if tariff_raw is None
                else _number(tariff_raw, "tariff_eur_kwh", minimum=0, maximum=5)
            )
            if tariff is not None:
                expected_cost, _ = _money_snapshot(kwh, tariff)
                if Decimal(str(cost)) != Decimal(str(expected_cost)):
                    raise DomainError("El coste de una carga no coincide con su tarifa guardada.")
            if version >= 4 and (method is not None or pricing is not None):
                raise DomainError("Las cargas demo no admiten precios ni método tarifario.")
            method = None
            pricing = None
        elif version >= 4 and method == "pvpc":
            quote = {
                "cost_eur": item["cost_eur"],
                "tariff_eur_kwh": tariff_raw,
                "pricing": pricing,
            }
            cost, tariff, pricing = _validate_pvpc_quote(
                quote, started_at, ended_at, kwh
            )
        else:
            if version >= 4 and method != "manual":
                raise DomainError("pricing_method debe ser manual o pvpc para una carga manual.")
            cost = _number(item["cost_eur"], "cost_eur", minimum=0, maximum=2500)
            tariff = (
                None
                if tariff_raw is None
                else _number(tariff_raw, "tariff_eur_kwh", minimum=0, maximum=5)
            )
            if version >= 4 and pricing is not None:
                raise DomainError("Una carga manual no admite snapshot de precio PVPC.")
            if tariff is not None:
                expected_cost, _ = _money_snapshot(kwh, tariff)
                if Decimal(str(cost)) != Decimal(str(expected_cost)):
                    raise DomainError("El coste de una carga no coincide con su tarifa guardada.")
            method = "manual"
        charges.append(
            {
                "id": item_id,
                "started_at": started_at,
                "ended_at": ended_at,
                "kwh": kwh,
                "cost_eur": cost,
                "source": source,
                "tariff_eur_kwh": tariff,
                "pricing_method": method,
                "pricing": pricing,
            }
        )

    journeys_raw = _collection(root["journeys"], "journeys")
    journeys: list[dict[str, Any]] = []
    journey_ids: set[str] = set()
    for raw in journeys_raw:
        item = _exact_object(
            raw,
            {
                "id", "started_at", "from_label", "to_label", "distance_km",
                "duration_min", "energy_kwh",
            },
            "journey",
        )
        item_id = _identifier(item["id"])
        if item_id in journey_ids:
            raise DomainError("La importación contiene identificadores de trayecto duplicados.")
        journey_ids.add(item_id)
        duration = item["duration_min"]
        if type(duration) is not int or duration < 1 or duration > 1440:
            raise DomainError("duration_min está fuera del rango permitido.")
        journeys.append(
            {
                "id": item_id,
                "started_at": _parse_datetime(item["started_at"], "started_at"),
                "from_label": _text(item["from_label"], "from_label", max_length=120),
                "to_label": _text(item["to_label"], "to_label", max_length=120),
                "distance_km": _number(
                    item["distance_km"], "distance_km", minimum=0, maximum=10000,
                    exclusive_minimum=True,
                ),
                "duration_min": duration,
                "energy_kwh": _number(
                    item["energy_kwh"], "energy_kwh", minimum=0, maximum=2000
                ),
            }
        )

    maintenance_raw = _collection(root["maintenance"], "maintenance")
    maintenance: list[dict[str, Any]] = []
    maintenance_ids: set[str] = set()
    for raw in maintenance_raw:
        item = _exact_object(
            raw, {"id", "title", "due_date", "completed", "source"}, "maintenance"
        )
        item_id = _identifier(item["id"])
        if item_id in maintenance_ids:
            raise DomainError(
                "La importación contiene identificadores de mantenimiento duplicados."
            )
        maintenance_ids.add(item_id)
        source = item["source"]
        if source not in ("manual", "demo"):
            raise DomainError("El origen de un mantenimiento no es válido.")
        maintenance.append(
            {
                "id": item_id,
                "title": _text(item["title"], "title", max_length=120),
                "due_date": _parse_date(item["due_date"], "due_date"),
                "completed": _boolean(item["completed"], "completed"),
                "source": source,
            }
        )

    protection_raw = _exact_object(root["protection"], {"enabled", "events"}, "protection")
    protection = {"enabled": _boolean(protection_raw["enabled"], "enabled"), "events": []}
    event_ids: set[str] = set()
    for raw in _collection(protection_raw["events"], "events"):
        event = _exact_object(raw, {"id", "created_at", "kind", "message"}, "event")
        event_id = _identifier(event["id"])
        if event_id in event_ids:
            raise DomainError("La importación contiene identificadores de evento duplicados.")
        event_ids.add(event_id)
        protection["events"].append(
            {
                "id": event_id,
                "created_at": _parse_datetime(event["created_at"], "created_at"),
                "kind": _text(event["kind"], "kind", max_length=40),
                "message": _text(event["message"], "message", max_length=240),
            }
        )

    command_history: list[dict[str, Any]] = []
    if version >= 2:
        command_ids: set[str] = set()
        for raw in _collection(root["command_history"], "command_history"):
            item = _exact_object(
                raw, {"id", "command", "status", "created_at", "message"}, "command_history"
            )
            item_id = _identifier(item["id"])
            if item_id in command_ids:
                raise DomainError("La importación contiene identificadores de comando duplicados.")
            command_ids.add(item_id)
            command = _text(item["command"], "command", max_length=32)
            if command not in _COMMAND_LABELS or item["status"] != "simulated":
                raise DomainError("El historial solo admite comandos simulados reconocidos.")
            message = _text(item["message"], "message", max_length=240)
            canonical_message = (
                f"{_COMMAND_LABELS[command]}: simulación completada. "
                "No se envió ninguna orden al vehículo."
            )
            if message != canonical_message:
                raise DomainError("El historial contiene un mensaje de comando no reconocido.")
            command_history.append(
                {
                    "id": item_id,
                    "command": command,
                    "status": "simulated",
                    "created_at": _parse_datetime(item["created_at"], "created_at"),
                    "message": message,
                }
            )

    demo_vehicle = None
    if "demo_vehicle" in root:
        raw_vehicle = _exact_object(
            root["demo_vehicle"],
            {
                "battery_pct", "range_km", "odometer_km", "locked", "climate_on",
                "charging", "observed_at",
            },
            "demo_vehicle",
        )
        demo_vehicle = {
            "battery_pct": _number(
                raw_vehicle["battery_pct"], "battery_pct", minimum=0, maximum=100
            ),
            "range_km": _number(raw_vehicle["range_km"], "range_km", minimum=0, maximum=1000),
            "odometer_km": _number(
                raw_vehicle["odometer_km"], "odometer_km", minimum=0, maximum=10000000
            ),
            "locked": _boolean(raw_vehicle["locked"], "locked"),
            "climate_on": _boolean(raw_vehicle["climate_on"], "climate_on"),
            "charging": _boolean(raw_vehicle["charging"], "charging"),
            "observed_at": _parse_datetime(raw_vehicle["observed_at"], "observed_at"),
        }

    widget_profiles: list[dict[str, Any]] = []
    telemetry_snapshots: list[dict[str, Any]] = []
    charger: list[dict[str, Any]] = []
    charger_pricing: list[dict[str, Any]] = []
    charger_pricing_history: list[dict[str, Any]] = []
    charger_energy = {"epochs": [], "intervals": []}
    observed_events: list[dict[str, Any]] = []
    if version >= 3:
        profile_rows = _collection(root["widget_profiles"], "widget_profiles")
        if len(profile_rows) > MAX_WIDGET_PROFILE_IMPORT_ROWS:
            raise DomainError("widget_profiles supera el límite de exportación permitido.")
        profile_ids: set[str] = set()
        profiles_per_owner: dict[str, int] = {}
        for raw in profile_rows:
            item = _validate_widget_profile_export(raw, allow_v12_fields=version >= 7)
            if item["id"] in profile_ids:
                raise DomainError("La importación contiene identificadores de perfil duplicados.")
            profile_ids.add(item["id"])
            owner = item["owner_id"]
            profiles_per_owner[owner] = profiles_per_owner.get(owner, 0) + 1
            if profiles_per_owner[owner] > MAX_PROFILES_PER_OWNER:
                raise DomainError("La importación supera los 5 perfiles de un usuario.")
            widget_profiles.append(item)

        snapshot_rows = _collection(root["telemetry_snapshots"], "telemetry_snapshots")
        if len(snapshot_rows) > MAX_TELEMETRY_SNAPSHOTS:
            raise DomainError("telemetry_snapshots supera el límite de 1000 registros.")
        snapshot_ids: set[str] = set()
        for raw in snapshot_rows:
            item = _validate_telemetry_export(raw, schema_version=version)
            if item["id"] in snapshot_ids:
                raise DomainError("La importación contiene identificadores de snapshot duplicados.")
            snapshot_ids.add(item["id"])
            telemetry_snapshots.append(item)

    if version >= 6:
        try:
            charger = validate_charger_archive(root["charger"])
        except ChargerDataError as exc:
            raise DomainError(f"La importación de charger no es válida: {exc}") from None

    if version >= 8:
        try:
            charger_pricing = validate_charger_pricing_archive(
                root["charger_pricing"], charger
            )
        except ChargerDataError as exc:
            raise DomainError(f"La importación de charger_pricing no es válida: {exc}") from None

    if version >= 10:
        try:
            charger_energy = validate_charger_energy_archive(root["charger_energy"])
        except ChargerDataError as exc:
            raise DomainError(f"La importación de charger_energy no es válida: {exc}") from None

    if version >= 11:
        try:
            charger_pricing_history = validate_charger_pricing_history_archive(
                root["charger_pricing_history"], charger
            )
        except ChargerDataError as exc:
            raise DomainError(f"La importación de charger_pricing_history no es válida: {exc}") from None

    if version < 12 and any(
        item["quote"]["pricing"]["accuracy"] != "estimated_uniform"
        for item in [*charger_pricing, *charger_pricing_history]
    ):
        raise DomainError("La distribución temporal requiere el formato de copia 12.")

    is_demo = mode == "demo"
    if version >= 9:
        try:
            observed_events = validate_observed_events_archive(root["observed_events"], telemetry_snapshots)
        except ObservedEventError as exc:
            raise DomainError(f"La importación de observed_events no es válida: {exc}") from None
    if not is_demo and (
        journeys
        or protection["enabled"]
        or protection["events"]
        or command_history
        or any(item["source"] == "demo" for item in charges)
        or any(item["source"] == "demo" for item in maintenance)
    ):
        raise DomainError("Un documento sin demo no puede contener datos ficticios.")

    return {
        "schema_version": version,
        "exported_at": _parse_datetime(root["exported_at"], "exported_at"),
        "mode": mode,
        "settings": settings,
        "charges": charges,
        "journeys": journeys,
        "maintenance": maintenance,
        "protection": protection,
        "command_history": command_history,
        "demo_vehicle": demo_vehicle,
        "widget_profiles": widget_profiles,
        "telemetry_snapshots": telemetry_snapshots,
        "charger": charger,
        "charger_pricing": charger_pricing,
        "charger_energy": charger_energy,
        "charger_pricing_history": charger_pricing_history,
        "observed_events": observed_events,
    }


def _collection(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list) or len(value) > MAX_ROWS_PER_COLLECTION:
        raise DomainError(f"{name} debe ser una lista dentro del límite permitido.")
    return value
