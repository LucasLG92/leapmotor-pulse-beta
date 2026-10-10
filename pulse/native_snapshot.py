"""Minimal, privacy-filtered snapshots for native widgets."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from typing import Any, Mapping

from pulse.collector import quality
from pulse.widget_profiles import profile_kind


_ENERGY_WINDOW = timedelta(days=30)
_WINDOW_TOLERANCE = timedelta(seconds=1)
_ENERGY_CLOCK_TOLERANCE = timedelta(seconds=1)
_ENERGY_MAX_RECEIPT_AGE = timedelta(seconds=60)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _aware_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and len(value) <= 64:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _selected_value(key: str, value: Any) -> float | int | bool | None:
    if key in {"charging", "locked"}:
        return value if type(value) is bool else None
    limits = {
        "battery_pct": (0, 100),
        "range_km": (0, 5000),
        "energy_kwh": (0, 1_000_000_000),
        "energy_cost_eur": (-1_000_000_000, 1_000_000_000),
    }
    if key not in limits or type(value) not in (float, int):
        return None
    try:
        valid = math.isfinite(value) and limits[key][0] <= value <= limits[key][1]
    except (OverflowError, TypeError):
        return None
    return value if valid else None


def _profile_projection(profile: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "id", "name", "revision", "fields", "appearance", "privacy_mode",
        "max_age_minutes",
    )
    return {key: profile[key] for key in keys}


def _vehicle_snapshot(context: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    profile = context["profile"]
    is_demo = context["mode"] == "demo"
    telemetry = context.get("telemetry") if not is_demo else None
    demo = context.get("demo_vehicle") if is_demo else None
    observed = (
        demo.get("observed_at") if demo is not None
        else telemetry.get("observed_at") if telemetry is not None
        else None
    )
    received = telemetry.get("server_received_at") if telemetry is not None else None
    values = (
        demo if demo is not None
        else telemetry.get("vehicle", {}) if telemetry is not None
        else {}
    )
    max_age_seconds = profile["max_age_minutes"] * 60
    status = quality(
        {"observed_at": observed} if observed else None,
        now,
        max_age_seconds=max_age_seconds,
    )
    valid_until = None
    if status["temporal_status"] in {"fresh", "stale"} and status["observed_at"]:
        source_time = _aware_datetime(status["observed_at"])
        if source_time is not None:
            deadline = min(
                source_time + timedelta(seconds=max_age_seconds),
                now + timedelta(seconds=max_age_seconds),
            )
            if deadline > now:
                valid_until = _iso(deadline)
            else:
                status.update(
                    temporal_status="unavailable", reason_code="observation_expired"
                )
    show = valid_until is not None and profile["privacy_mode"] == "visible"
    return {
        "schema_version": 2,
        "profile": _profile_projection(profile),
        "kind": "vehicle",
        "energy_window": None,
        "source": "demo" if is_demo else "telemetry" if telemetry is not None else "unavailable",
        "generated_at": _iso(now),
        "server_received_at": received,
        "observed_at": status["observed_at"],
        "physical_freshness": "unknown",
        "temporal_status": status["temporal_status"],
        "reason_code": status["reason_code"],
        "valid_until": valid_until,
        "values": {
            key: _selected_value(key, values.get(key)) if show else None
            for key in profile["fields"]
        },
    }


def _energy_snapshot(context: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    profile = context["profile"]
    energy = context.get("energy")
    if not isinstance(energy, Mapping):
        raise ValueError("El contexto energético del widget no es válido.")

    calculated_at = _aware_datetime(energy.get("calculated_at"))
    window_start = _aware_datetime(energy.get("window_start"))
    window_end = _aware_datetime(energy.get("window_end"))
    if calculated_at is None or window_start is None or window_end is None:
        raise ValueError("La ventana energética debe incluir fechas ISO con zona horaria.")

    energy_window = {
        "started_at": _iso(window_start),
        "ended_at": _iso(window_end),
    }
    window_valid = (
        window_end == calculated_at
        and abs((window_end - window_start) - _ENERGY_WINDOW) <= _WINDOW_TOLERANCE
        and now - window_end <= _ENERGY_MAX_RECEIPT_AGE
        and window_end - now <= _ENERGY_CLOCK_TOLERANCE
        and calculated_at - now <= _ENERGY_MAX_RECEIPT_AGE
    )

    sessions = energy.get("sessions")
    kwh = _selected_value("energy_kwh", energy.get("kwh"))
    cost_eur = _selected_value("energy_cost_eur", energy.get("cost_eur"))
    aggregate_valid = (
        type(sessions) is int
        and sessions > 0
        and kwh is not None
        and cost_eur is not None
    )
    valid_until = None
    reason_code = "energy_window_invalid"
    temporal_status = "unavailable"
    if window_valid and aggregate_valid:
        max_age = timedelta(minutes=profile["max_age_minutes"])
        deadline = min(calculated_at + max_age, now + max_age)
        if deadline > now:
            valid_until = _iso(deadline)
            reason_code = "energy_calculated"
            temporal_status = "fresh"
        else:
            reason_code = "energy_expired"
    elif window_valid and type(sessions) is int and sessions == 0:
        reason_code = "no_manual_sessions"
    elif window_valid:
        reason_code = "energy_aggregate_invalid"

    show = (
        temporal_status == "fresh"
        and valid_until is not None
        and profile["privacy_mode"] == "visible"
    )
    energy_values = {"energy_kwh": kwh, "energy_cost_eur": cost_eur}
    calculated_text = _iso(calculated_at)
    return {
        "schema_version": 2,
        "profile": _profile_projection(profile),
        "kind": "energy",
        "energy_window": energy_window,
        "source": "manual_energy",
        "generated_at": _iso(now),
        "server_received_at": calculated_text,
        "observed_at": calculated_text,
        "physical_freshness": "unknown",
        "temporal_status": temporal_status,
        "reason_code": reason_code,
        "valid_until": valid_until,
        "values": {
            field: energy_values[field] if show else None
            for field in profile["fields"]
        },
    }


def widget_snapshot(context: dict, *, now: datetime) -> dict:
    """Build a versioned snapshot with selected values and server-side masking."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("El reloj del widget debe incluir zona horaria.")
    now = now.astimezone(timezone.utc)
    kind = profile_kind(context["profile"]["fields"])
    if kind == "energy":
        return _energy_snapshot(context, now)
    return _vehicle_snapshot(context, now)
