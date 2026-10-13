"""Informational charging estimates and manual-charge comparisons.

This module never controls a vehicle or charger. Charging efficiency is a user
estimate, prices are supplied manually by the caller, and uncovered time is
left uncovered. It deliberately uses only the Python standard library.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext, ROUND_CEILING
import math
import re
from typing import Any


_UTC = timezone.utc
_MAX_TARIFFS = 336
_MAX_COMPARISON_ROWS = 10_000
_MAX_COMPARISON_DAYS = 3650
_MAX_CHARGE_KWH = Decimal("300")
_MAX_POWER_KW = Decimal("350")
_MIN_EFFICIENCY_PCT = Decimal("50")
_MAX_EFFICIENCY_PCT = Decimal("100")
_MIN_PRICE_EUR_KWH = Decimal("-1")
_MAX_PRICE_EUR_KWH = Decimal("5")
_MAX_CHARGE_WINDOW = timedelta(days=7)
_ISO_OFFSET_SUFFIX = re.compile(r"(?:Z|[+-]\d{2}:\d{2})\Z")


class EnergyPlanningError(ValueError):
    """Validation error with a stable, non-sensitive code for the API layer."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _decimal(
    value: Any,
    *,
    lower: Decimal | None = None,
    upper: Decimal | None = None,
    positive: bool = False,
) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise EnergyPlanningError("invalid_number")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError, OverflowError):
        raise EnergyPlanningError("invalid_number") from None
    if (
        not number.is_finite()
        or (positive and number <= 0)
        or (lower is not None and number < lower)
        or (upper is not None and number > upper)
    ):
        raise EnergyPlanningError("invalid_number")
    try:
        converted = float(number)
    except (OverflowError, ValueError):
        raise EnergyPlanningError("invalid_number") from None
    if not math.isfinite(converted) or (number != 0 and converted == 0):
        raise EnergyPlanningError("invalid_number")
    return number


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value or not _ISO_OFFSET_SUFFIX.search(value):
        raise EnergyPlanningError("invalid_timestamp")
    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(normalized)
        return parsed.astimezone(_UTC)
    except (TypeError, ValueError, OverflowError):
        raise EnergyPlanningError("invalid_timestamp") from None


def _aware_utc(value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise EnergyPlanningError("invalid_timestamp")
    try:
        return value.astimezone(_UTC)
    except (OverflowError, ValueError):
        raise EnergyPlanningError("invalid_timestamp") from None


def _iso_utc(value: datetime) -> str:
    return value.astimezone(_UTC).isoformat().replace("+00:00", "Z")


def _timedelta_microseconds(value: timedelta) -> int:
    return (value.days * 86_400 + value.seconds) * 1_000_000 + value.microseconds


def _hours(microseconds: int) -> Decimal:
    return Decimal(microseconds) / Decimal(3_600_000_000)


def _as_float(value: Decimal) -> float:
    result = float(value)
    if not math.isfinite(result) or (value != 0 and result == 0):
        raise EnergyPlanningError("invalid_calculation")
    return result


def _normalized_tariffs(raw_tariffs: Any, start: datetime, deadline: datetime) -> list[dict[str, Any]]:
    if not isinstance(raw_tariffs, list) or len(raw_tariffs) > _MAX_TARIFFS:
        raise EnergyPlanningError("invalid_tariffs")

    parsed: list[tuple[datetime, datetime, Decimal]] = []
    for raw in raw_tariffs:
        if not isinstance(raw, Mapping) or set(raw) != {"start_at", "end_at", "price_eur_kwh"}:
            raise EnergyPlanningError("invalid_tariff")
        interval_start = _parse_timestamp(raw["start_at"])
        interval_end = _parse_timestamp(raw["end_at"])
        length = _timedelta_microseconds(interval_end - interval_start)
        if length <= 0 or length > 3_600_000_000:
            raise EnergyPlanningError("invalid_tariff")
        price = _decimal(
            raw["price_eur_kwh"], lower=_MIN_PRICE_EUR_KWH, upper=_MAX_PRICE_EUR_KWH
        )
        parsed.append((interval_start, interval_end, price))

    parsed.sort(key=lambda item: (item[0], item[1]))
    previous_end: datetime | None = None
    candidates: list[dict[str, Any]] = []
    for interval_start, interval_end, price in parsed:
        if previous_end is not None and interval_start < previous_end:
            raise EnergyPlanningError("overlapping_tariffs")
        previous_end = interval_end

        clipped_start = max(interval_start, start)
        clipped_end = min(interval_end, deadline)
        if clipped_start >= clipped_end:
            continue
        duration_us = _timedelta_microseconds(clipped_end - clipped_start)
        candidates.append({
            "start": clipped_start,
            "end": clipped_end,
            "duration_us": duration_us,
            "price": price,
        })
    return candidates


def _schedule(
    ordered: list[dict[str, Any]],
    target_battery_kwh: Decimal,
    power_kw: Decimal,
    efficiency: Decimal,
) -> tuple[list[dict[str, Any]], Decimal, Decimal, Decimal]:
    remaining = target_battery_kwh
    selected: list[dict[str, Any]] = []
    total_grid = Decimal("0")
    total_battery = Decimal("0")
    total_cost = Decimal("0")
    for candidate in ordered:
        grid_capacity = power_kw * _hours(candidate["duration_us"])
        battery_capacity = grid_capacity * efficiency
        battery_energy = min(battery_capacity, remaining)
        if battery_energy <= 0:
            continue
        grid_energy = battery_energy / efficiency
        cost = grid_energy * candidate["price"]
        duration_us = min(candidate["duration_us"], int((grid_energy / power_kw * Decimal("3600000000")).to_integral_value(rounding=ROUND_CEILING)))
        useful_end = candidate["start"] + timedelta(microseconds=duration_us)
        selected.append({
            "started_at": _iso_utc(candidate["start"]),
            "ended_at": _iso_utc(useful_end),
            "tariff_ended_at": _iso_utc(candidate["end"]),
            "duration_seconds": duration_us / 1_000_000,
            "price_eur_kwh": _as_float(candidate["price"]),
            "grid_energy_kwh": _as_float(grid_energy),
            "battery_energy_kwh": _as_float(battery_energy),
            "estimated_cost_eur": _as_float(cost),
            "partial": battery_energy < battery_capacity,
        })
        total_grid += grid_energy
        total_battery += battery_energy
        total_cost += cost
        remaining -= battery_energy
        if remaining <= 0:
            break
    selected.sort(key=lambda item: (item["started_at"], item["ended_at"]))
    return selected, total_grid, total_battery, total_cost


def plan_charging(values: Mapping[str, Any]) -> dict[str, Any]:
    """Choose the least expensive explicitly priced intervals for a target.

    ``energy_kwh`` is the requested battery-side energy. ``power_kw`` is the
    maximum grid-side charging power; the supplied efficiency converts grid
    energy to battery energy. Missing tariff periods are never filled in.
    """
    expected_fields = {
        "energy_kwh", "power_kw", "efficiency_pct", "earliest_at", "deadline_at", "tariffs"
    }
    if not isinstance(values, Mapping) or set(values) != expected_fields:
        raise EnergyPlanningError("invalid_request")

    requested_battery = _decimal(values["energy_kwh"], lower=Decimal("0"), upper=_MAX_CHARGE_KWH, positive=True)
    power_kw = _decimal(values["power_kw"], lower=Decimal("0"), upper=_MAX_POWER_KW, positive=True)
    efficiency_pct = _decimal(
        values["efficiency_pct"], lower=_MIN_EFFICIENCY_PCT, upper=_MAX_EFFICIENCY_PCT
    )
    efficiency = efficiency_pct / Decimal("100")
    start = _parse_timestamp(values["earliest_at"])
    deadline = _parse_timestamp(values["deadline_at"])
    window_us = _timedelta_microseconds(deadline - start)
    if window_us <= 0 or deadline - start > _MAX_CHARGE_WINDOW:
        raise EnergyPlanningError("invalid_window")

    candidates = _normalized_tariffs(values["tariffs"], start, deadline)
    coverage_us = sum(candidate["duration_us"] for candidate in candidates)
    with localcontext() as context:
        context.prec = 34
        price_order = sorted(candidates, key=lambda item: (item["price"], item["start"], item["end"]))
        intervals, total_grid, battery_energy, estimated_cost = _schedule(
            price_order, requested_battery, power_kw, efficiency
        )
        baseline_order = sorted(candidates, key=lambda item: (item["start"], item["end"]))
        earliest_intervals, earliest_grid, _, earliest_cost = _schedule(
            baseline_order, battery_energy, power_kw, efficiency
        )

        complete = battery_energy >= requested_battery
        unsatisfied = max(Decimal("0"), requested_battery - battery_energy)
        savings = earliest_cost - estimated_cost if complete else None
        if savings is not None and savings < 0 and abs(savings) < Decimal("1e-30"):
            savings = Decimal("0")

    warnings: list[str] = ["efficiency_is_user_estimate", "tariffs_are_user_supplied"]
    if coverage_us < window_us:
        warnings.append("tariff_coverage_incomplete")
    if not complete:
        warnings.append("requested_energy_unreachable")

    return {
        "purpose": "informational_estimate",
        "requested_energy_kwh": _as_float(requested_battery),
        "efficiency_pct": _as_float(efficiency_pct),
        "coverage": {
            "window_seconds": window_us / 1_000_000,
            "priced_seconds": coverage_us / 1_000_000,
            "complete": coverage_us == window_us,
        },
        "intervals": intervals,
        "grid_energy_kwh": _as_float(total_grid),
        "battery_energy_kwh": _as_float(battery_energy),
        "estimated_cost_eur": _as_float(estimated_cost),
        "satisfied_energy_kwh": _as_float(battery_energy),
        "unsatisfied_energy_kwh": _as_float(unsatisfied),
        "earliest_feasible": {
            "intervals": earliest_intervals,
            "grid_energy_kwh": _as_float(earliest_grid),
            "estimated_cost_eur": _as_float(earliest_cost),
        },
        "estimated_savings_eur": _as_float(savings) if savings is not None else None,
        "warnings": warnings,
    }


def _row_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    keys = getattr(value, "keys", None)
    if callable(keys):
        try:
            return {key: value[key] for key in keys()}
        except (KeyError, TypeError, ValueError):
            pass
    raise EnergyPlanningError("invalid_charge")


def _manual_amount(value: Any, field: str) -> Decimal | None:
    if value is None:
        return None
    if field == "kwh":
        return _decimal(value, lower=Decimal("0"), upper=Decimal("500"), positive=True)
    return _decimal(value, lower=Decimal("-2500"), upper=Decimal("2500"))


def _location(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise EnergyPlanningError("invalid_charge")
    normalized = " ".join(value.split())
    if len(normalized) > 120:
        raise EnergyPlanningError("invalid_charge")
    return normalized or None


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    with localcontext() as context:
        context.prec = 34
        known_kwh = [row["kwh"] for row in rows if row["kwh"] is not None]
        known_cost = [row["cost_eur"] for row in rows if row["cost_eur"] is not None]
        paired = [row for row in rows if row["kwh"] is not None and row["cost_eur"] is not None]
        total_kwh = sum(known_kwh, Decimal("0"))
        total_cost = sum(known_cost, Decimal("0"))
        paired_kwh = sum((row["kwh"] for row in paired), Decimal("0"))
        paired_cost = sum((row["cost_eur"] for row in paired), Decimal("0"))
        missing_kwh = sum(row["kwh"] is None for row in rows)
        missing_cost = sum(row["cost_eur"] is None for row in rows)
        effective = paired_cost / paired_kwh if paired_kwh > 0 else None
    return {
        "charge_count": len(rows),
        "kwh": _as_float(total_kwh) if known_kwh else None,
        "cost_eur": _as_float(total_cost) if known_cost else None,
        "known_kwh_count": len(known_kwh),
        "known_cost_count": len(known_cost),
        "paired_count": len(paired),
        "paired_kwh": _as_float(paired_kwh) if paired else None,
        "paired_cost_eur": _as_float(paired_cost) if paired else None,
        "effective_cost_eur_kwh": _as_float(effective) if effective is not None else None,
        "missing_kwh_count": missing_kwh,
        "missing_cost_count": missing_cost,
    }


def compare_manual_charges(rows: Any, days: int, now: datetime) -> dict[str, Any]:
    """Aggregate only manual entries in a UTC window, preserving unknown values.

    Rows without a ``source`` field are accepted as results of a caller query
    against the manual table. Explicit non-manual sources are ignored. Location
    is grouped only when an explicit manual label is present; otherwise it stays
    ``None`` and is reported as unknown.
    """
    if type(days) is not int or not 1 <= days <= _MAX_COMPARISON_DAYS:
        raise EnergyPlanningError("invalid_days")
    current = _aware_utc(now)
    try:
        window_start = current - timedelta(days=days)
    except OverflowError:
        raise EnergyPlanningError("invalid_window") from None
    if not isinstance(rows, (list, tuple)) or len(rows) > _MAX_COMPARISON_ROWS:
        raise EnergyPlanningError("invalid_rows")

    normalized: list[dict[str, Any]] = []
    excluded_non_manual = 0
    for raw in rows:
        row = _row_mapping(raw)
        if "source" in row and row["source"] != "manual":
            excluded_non_manual += 1
            continue
        started_at = _parse_timestamp(row.get("started_at"))
        if started_at < window_start or started_at > current:
            continue
        kwh = _manual_amount(row.get("kwh"), "kwh")
        cost = _manual_amount(row.get("cost_eur"), "cost_eur")
        location = _location(row.get("location"))
        normalized.append({
            "month": started_at.strftime("%Y-%m"),
            "location": location,
            "kwh": kwh,
            "cost_eur": cost,
        })

    months: dict[str, list[dict[str, Any]]] = {}
    locations_by_month: dict[str, dict[str | None, list[dict[str, Any]]]] = {}
    for row in normalized:
        months.setdefault(row["month"], []).append(row)
        locations_by_month.setdefault(row["month"], {}).setdefault(
            row["location"], []
        ).append(row)

    month_results = []
    for month in sorted(months):
        groups = [
            {"location": location, **_aggregate(group_rows)}
            for location, group_rows in sorted(
                locations_by_month[month].items(),
                key=lambda item: (item[0] is not None, item[0] or ""),
            )
        ]
        month_results.append({"month": month, **_aggregate(months[month]), "locations": groups})

    return {
        "basis": "manual_entries_only",
        "days": days,
        "window_start": _iso_utc(window_start),
        "window_end": _iso_utc(current),
        "totals": _aggregate(normalized),
        "months": month_results,
        "excluded_non_manual_count": excluded_non_manual,
    }
