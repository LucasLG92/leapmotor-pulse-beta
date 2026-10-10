"""PVPC estimates based on a validated charger-energy curve.

The curve is supplied by a configured caller. Its shape does not prove that
energy came from the grid or that the source is a physical meter.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import math
import re
from typing import Any
from zoneinfo import ZoneInfo

from . import pvpc


_UTC = timezone.utc
_MADRID = ZoneInfo(pvpc.TIMEZONE)
_DISTRIBUTION_FIELDS = {
    "version", "scope", "session_id", "source_id", "source_version",
    "basis", "intervals",
}
_DISTRIBUTION_ROW_FIELDS = {"started_at", "ended_at", "kwh"}
_PRICING_FIELDS = {
    "method", "accuracy", "region", "source", "taxes_included",
    "calculated_at", "intervals", "energy_distribution",
}
_PRICING_ROW_FIELDS = {
    "started_at", "ended_at", "kwh", "price_eur_kwh", "cost_eur",
    "price_fetched_at",
}
_HEX64 = re.compile(r"[0-9a-fA-F]{64}\Z", re.ASCII)
_HEX32 = re.compile(r"[0-9a-fA-F]{32}\Z", re.ASCII)
_SOURCE_ID = re.compile(r"[A-Za-z0-9._-]{1,64}\Z", re.ASCII)
_CENT = Fraction(1, 100)
_ENERGY_TOLERANCE = Decimal("0.000000001")
_COST_TOLERANCE = Decimal("0.000000001")
_MAX_CHARGE_SECONDS = 72 * 60 * 60
_MAX_CHARGE_KWH = Decimal("500")
_MAX_DISTRIBUTION_INTERVALS = 1024
_MAX_PRICING_INTERVALS = 2048


def _invalid() -> None:
    raise pvpc.PvpcError("invalid_pricing")


def _decimal(value: Any, *, lower: Decimal, upper: Decimal) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        _invalid()
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError, OverflowError):
        _invalid()
    if not result.is_finite() or result < lower or result > upper:
        _invalid()
    # A normalized JSON number must remain representable as a nonzero float.
    converted = float(result)
    if not converted or not math.isfinite(converted):
        if result != 0:
            _invalid()
    return result


def _utc_datetime(value: Any) -> datetime:
    if not isinstance(value, str):
        _invalid()
    try:
        parsed = pvpc._parse_iso_datetime(value, code="invalid_pricing")
    except pvpc.PvpcError:
        _invalid()
    return parsed.astimezone(_UTC)


def _aware_datetime(value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _invalid()
    return value.astimezone(_UTC)


def _iso(value: datetime) -> str:
    return pvpc._iso_utc(value)


def _microseconds(value: timedelta) -> int:
    return (value.days * 86400 + value.seconds) * 1_000_000 + value.microseconds


def _fraction(value: Decimal) -> Fraction:
    return Fraction(value)


def _float(value: Decimal | Fraction) -> float:
    result = float(value)
    if not math.isfinite(result):
        _invalid()
    return result


def _round_half_up_to_cent(value: Fraction) -> Fraction:
    """Round once to cents, with ties away from zero for either sign."""
    sign = -1 if value < 0 else 1
    cents = abs(value) * 100
    whole, remainder = divmod(cents.numerator, cents.denominator)
    if remainder * 2 >= cents.denominator:
        whole += 1
    return Fraction(sign * whole, 100)


def _charge_values(started_at: Any, ended_at: Any, kwh: Any) -> tuple[datetime, datetime, Decimal]:
    start = _utc_datetime(started_at)
    end = _utc_datetime(ended_at)
    energy = _decimal(kwh, lower=Decimal("0"), upper=_MAX_CHARGE_KWH)
    elapsed = _microseconds(end - start)
    if energy <= 0 or elapsed <= 0 or elapsed > _MAX_CHARGE_SECONDS * 1_000_000:
        _invalid()
    return start, end, energy


def validate_energy_distribution(
    value: Any,
    started_at: Any,
    ended_at: Any,
    kwh: Any,
    *,
    expected_scope: str | None = None,
    expected_session_id: str | None = None,
) -> dict[str, Any]:
    """Validate and normalize one complete, explicitly supplied energy curve."""
    start, end, total_energy = _charge_values(started_at, ended_at, kwh)
    if not isinstance(value, Mapping) or set(value) != _DISTRIBUTION_FIELDS:
        _invalid()
    scope = value.get("scope")
    session_id = value.get("session_id")
    source_id = value.get("source_id")
    source_version = value.get("source_version")
    if (
        type(value.get("version")) is not int or value.get("version") != 1
        or not isinstance(scope, str) or not _HEX64.fullmatch(scope)
        or not isinstance(session_id, str) or not _HEX32.fullmatch(session_id)
        or not isinstance(source_id, str) or not _SOURCE_ID.fullmatch(source_id)
        or not isinstance(source_version, str) or not _SOURCE_ID.fullmatch(source_version)
        or value.get("basis") != "charger_total"
    ):
        _invalid()
    normalized_scope = scope.lower()
    normalized_session_id = session_id.lower()
    if expected_scope is not None:
        if not isinstance(expected_scope, str) or not _HEX64.fullmatch(expected_scope):
            _invalid()
        if normalized_scope != expected_scope.lower():
            _invalid()
    if expected_session_id is not None:
        if not isinstance(expected_session_id, str) or not _HEX32.fullmatch(expected_session_id):
            _invalid()
        if normalized_session_id != expected_session_id.lower():
            _invalid()

    raw_intervals = value.get("intervals")
    if (
        not isinstance(raw_intervals, list)
        or not raw_intervals
        or len(raw_intervals) > _MAX_DISTRIBUTION_INTERVALS
    ):
        _invalid()

    cursor = start
    total = Decimal("0")
    normalized_intervals: list[dict[str, Any]] = []
    for raw in raw_intervals:
        if not isinstance(raw, Mapping) or set(raw) != _DISTRIBUTION_ROW_FIELDS:
            _invalid()
        interval_start = _utc_datetime(raw.get("started_at"))
        interval_end = _utc_datetime(raw.get("ended_at"))
        interval_energy = _decimal(
            raw.get("kwh"), lower=Decimal("0"), upper=_MAX_CHARGE_KWH
        )
        elapsed = _microseconds(interval_end - interval_start)
        if (
            interval_start != cursor
            or interval_end <= interval_start
            or elapsed > 3600 * 1_000_000
            or interval_end > end
        ):
            _invalid()
        normalized_intervals.append({
            "started_at": _iso(interval_start),
            "ended_at": _iso(interval_end),
            "kwh": _float(interval_energy),
        })
        total += interval_energy
        cursor = interval_end

    if cursor != end or abs(total - total_energy) > _ENERGY_TOLERANCE:
        _invalid()
    return {
        "version": 1,
        "scope": normalized_scope,
        "session_id": normalized_session_id,
        "source_id": source_id,
        "source_version": source_version,
        "basis": "charger_total",
        "intervals": normalized_intervals,
    }


def _day_map(days: Any, start: datetime, end: datetime) -> dict[date, dict[str, Any]]:
    if not isinstance(days, list):
        _invalid()
    snapshots: dict[date, dict[str, Any]] = {}
    for raw in days:
        try:
            normalized = pvpc.validate_day_snapshot(raw)
        except pvpc.PvpcError:
            _invalid()
        day = date.fromisoformat(normalized["date"])
        if day in snapshots:
            _invalid()
        snapshots[day] = normalized

    first_day = start.astimezone(_MADRID).date()
    last_day = (end - timedelta(microseconds=1)).astimezone(_MADRID).date()
    required: set[date] = set()
    current = first_day
    while current <= last_day:
        required.add(current)
        current += timedelta(days=1)
    if not required.issubset(snapshots):
        _invalid()
    return snapshots


def _hour_curve(
    snapshots: Mapping[date, dict[str, Any]],
) -> dict[datetime, tuple[Decimal, str]]:
    curve: dict[datetime, tuple[Decimal, str]] = {}
    for snapshot in snapshots.values():
        fetched_at = snapshot["fetched_at"]
        for row in snapshot["intervals"]:
            hour = _utc_datetime(row["started_at"])
            price = _decimal(
                row["price_eur_kwh"], lower=pvpc.MIN_PRICE_EUR_KWH,
                upper=pvpc.MAX_PRICE_EUR_KWH,
            )
            if hour in curve:
                _invalid()
            curve[hour] = (price, fetched_at)
    return curve


def quote_charge_intervals(
    started_at: Any,
    ended_at: Any,
    kwh: Any,
    energy_distribution: Any,
    days: Any,
    *,
    calculated_at: datetime,
) -> dict[str, Any]:
    """Quote a charger-total curve, splitting only at PVPC hour boundaries."""
    start, end, total_energy = _charge_values(started_at, ended_at, kwh)
    normalized_distribution = validate_energy_distribution(
        energy_distribution, started_at, ended_at, kwh
    )
    calculated = _aware_datetime(calculated_at)
    snapshots = _day_map(days, start, end)
    curve = _hour_curve(snapshots)

    lines: list[dict[str, Any]] = []
    exact_cost = Fraction(0)
    cursor = start
    for source in normalized_distribution["intervals"]:
        source_start = _utc_datetime(source["started_at"])
        source_end = _utc_datetime(source["ended_at"])
        source_energy = _fraction(Decimal(str(source["kwh"])))
        source_duration = _microseconds(source_end - source_start)
        position = source_start
        if position != cursor:
            _invalid()
        while position < source_end:
            hour_start = position.replace(minute=0, second=0, microsecond=0)
            hour_end = hour_start + timedelta(hours=1)
            interval_end = min(source_end, hour_end)
            if interval_end <= position:
                _invalid()
            curve_row = curve.get(hour_start)
            if curve_row is None:
                _invalid()
            price, fetched_at = curve_row
            portion = source_energy * Fraction(
                _microseconds(interval_end - position), source_duration
            )
            line_cost = portion * _fraction(price)
            exact_cost += line_cost
            lines.append({
                "started_at": _iso(position),
                "ended_at": _iso(interval_end),
                "kwh": _float(portion),
                "price_eur_kwh": _float(price),
                "cost_eur": _float(line_cost),
                "price_fetched_at": fetched_at,
            })
            if len(lines) > _MAX_PRICING_INTERVALS:
                _invalid()
            cursor = interval_end
            position = interval_end
    if cursor != end or not lines:
        _invalid()

    rounded_cost = _round_half_up_to_cent(exact_cost)
    tariff = exact_cost / _fraction(total_energy)
    pricing = {
        "method": "pvpc",
        "accuracy": "estimated_intervals",
        "region": pvpc.REGION,
        "source": pvpc.SOURCE,
        "taxes_included": False,
        "calculated_at": _iso(calculated),
        "intervals": lines,
        "energy_distribution": normalized_distribution,
    }
    return {
        "cost_eur": _float(rounded_cost),
        "tariff_eur_kwh": _float(tariff),
        "pricing": pricing,
    }


def _expected_parts(
    distribution: Mapping[str, Any],
) -> list[tuple[datetime, datetime, Fraction]]:
    parts: list[tuple[datetime, datetime, Fraction]] = []
    for source in distribution["intervals"]:
        source_start = _utc_datetime(source["started_at"])
        source_end = _utc_datetime(source["ended_at"])
        source_energy = _fraction(Decimal(str(source["kwh"])))
        source_duration = _microseconds(source_end - source_start)
        position = source_start
        while position < source_end:
            hour_end = position.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            interval_end = min(source_end, hour_end)
            parts.append((position, interval_end, source_energy * Fraction(
                _microseconds(interval_end - position), source_duration
            )))
            if len(parts) > _MAX_PRICING_INTERVALS:
                _invalid()
            position = interval_end
    return parts


def validate_temporal_pricing_snapshot(
    pricing: Any,
    started_at: Any,
    ended_at: Any,
    kwh: Any,
    cost_eur: Any,
) -> dict[str, Any]:
    """Validate temporal lines by rebuilding them from the source energy curve."""
    start, end, total_energy = _charge_values(started_at, ended_at, kwh)
    supplied_cost = _decimal(
        cost_eur, lower=Decimal("-2500"), upper=Decimal("2500")
    )
    if not isinstance(pricing, Mapping) or set(pricing) != _PRICING_FIELDS:
        _invalid()
    if (
        pricing.get("method") != "pvpc"
        or pricing.get("accuracy") != "estimated_intervals"
        or pricing.get("region") != pvpc.REGION
        or pricing.get("source") != pvpc.SOURCE
        or pricing.get("taxes_included") is not False
    ):
        _invalid()
    try:
        calculated = pvpc._parse_iso_datetime(
            pricing.get("calculated_at"), code="invalid_pricing"
        )
    except pvpc.PvpcError:
        _invalid()
    raw_distribution = pricing.get("energy_distribution")
    distribution = validate_energy_distribution(
        raw_distribution, started_at, ended_at, kwh
    )
    raw_lines = pricing.get("intervals")
    if (
        not isinstance(raw_lines, list)
        or not raw_lines
        or len(raw_lines) > _MAX_PRICING_INTERVALS
    ):
        _invalid()
    expected = _expected_parts(distribution)
    if len(raw_lines) != len(expected):
        _invalid()

    prices_by_hour: dict[datetime, Decimal] = {}
    fetched_by_madrid_day: dict[date, datetime] = {}
    normalized_lines: list[dict[str, Any]] = []
    reconstructed_cost = Fraction(0)
    for raw, (expected_start, expected_end, expected_energy) in zip(raw_lines, expected):
        if not isinstance(raw, Mapping) or set(raw) != _PRICING_ROW_FIELDS:
            _invalid()
        interval_start = _utc_datetime(raw.get("started_at"))
        interval_end = _utc_datetime(raw.get("ended_at"))
        if interval_start != expected_start or interval_end != expected_end:
            _invalid()
        price = _decimal(
            raw.get("price_eur_kwh"), lower=pvpc.MIN_PRICE_EUR_KWH,
            upper=pvpc.MAX_PRICE_EUR_KWH,
        )
        interval_energy = _decimal(
            raw.get("kwh"), lower=Decimal("0"), upper=_MAX_CHARGE_KWH
        )
        interval_cost = _decimal(
            raw.get("cost_eur"), lower=Decimal("-2500"), upper=Decimal("2500")
        )
        if abs(interval_energy - Decimal(expected_energy.numerator) / Decimal(expected_energy.denominator)) > _ENERGY_TOLERANCE:
            _invalid()
        expected_line_cost = expected_energy * _fraction(price)
        if abs(interval_cost - Decimal(expected_line_cost.numerator) / Decimal(expected_line_cost.denominator)) > _COST_TOLERANCE:
            _invalid()

        hour_start = interval_start.replace(minute=0, second=0, microsecond=0)
        if interval_end > hour_start + timedelta(hours=1):
            _invalid()
        prior_price = prices_by_hour.setdefault(hour_start, price)
        if prior_price != price:
            _invalid()
        try:
            fetched_at = pvpc._parse_iso_datetime(
                raw.get("price_fetched_at"), code="invalid_pricing"
            )
        except pvpc.PvpcError:
            _invalid()
        fetched_utc = fetched_at.astimezone(_UTC)
        madrid_day = interval_start.astimezone(_MADRID).date()
        prior_fetch = fetched_by_madrid_day.setdefault(madrid_day, fetched_utc)
        if prior_fetch != fetched_utc:
            _invalid()

        reconstructed_cost += expected_line_cost
        normalized_lines.append({
            "started_at": _iso(interval_start),
            "ended_at": _iso(interval_end),
            "kwh": _float(expected_energy),
            "price_eur_kwh": _float(price),
            "cost_eur": _float(expected_line_cost),
            "price_fetched_at": _iso(fetched_at),
        })

    rounded_cost = _round_half_up_to_cent(reconstructed_cost)
    if supplied_cost != Decimal(rounded_cost.numerator) / Decimal(rounded_cost.denominator):
        _invalid()
    if supplied_cost != supplied_cost.quantize(Decimal("0.01")):
        _invalid()

    return {
        "method": "pvpc",
        "accuracy": "estimated_intervals",
        "region": pvpc.REGION,
        "source": pvpc.SOURCE,
        "taxes_included": False,
        "calculated_at": _iso(calculated),
        "intervals": normalized_lines,
        "energy_distribution": distribution,
    }


__all__ = [
    "quote_charge_intervals",
    "validate_energy_distribution",
    "validate_temporal_pricing_snapshot",
]
