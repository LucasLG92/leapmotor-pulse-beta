"""Public REData PVPC parsing and charge-price estimates for mainland Spain.

This module only reads the public REData endpoint. It does not use vehicle
credentials or infer energy from a charge duration. A quote distributes the
reported session kWh evenly over its elapsed seconds and is therefore an
estimate, not a measurement from a wallbox.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
from collections.abc import Mapping
from typing import Any
from zoneinfo import ZoneInfo

import httpx2


REE_URL = "https://apidatos.ree.es/es/datos/mercados/precios-mercados-tiempo-real"
PVPC_ID = "1001"
PVPC_TYPE = "PVPC"
REGION = "peninsula"
TIMEZONE = "Europe/Madrid"
SOURCE = "REE REData PVPC 1001"
MAX_RESPONSE_BYTES = 512 * 1024
REQUEST_TIMEOUT_SECONDS = 8.0
CONNECT_TIMEOUT_SECONDS = 3.0
MAX_CHARGE_SECONDS = 72 * 60 * 60
MAX_CHARGE_KWH = Decimal("500")
MIN_PRICE_EUR_KWH = Decimal("-5")
MAX_PRICE_EUR_KWH = Decimal("5")
_CENT = Decimal("0.01")
_ENERGY_TOLERANCE = Decimal("0.000000001")
_COST_TOLERANCE = Decimal("0.000000001")
_MADRID = ZoneInfo(TIMEZONE)
_UTC = timezone.utc


_ERROR_MESSAGES = {
    "invalid_day": "La fecha PVPC no es válida.",
    "invalid_payload": "La respuesta PVPC no tiene el formato esperado.",
    "series_missing": "La respuesta no contiene la serie PVPC peninsular requerida.",
    "series_duplicate": "La respuesta contiene la serie PVPC requerida más de una vez.",
    "incomplete_day": "La serie PVPC no cubre todas las horas locales del día.",
    "invalid_value": "La serie PVPC contiene un precio no válido.",
    "invalid_timestamp": "La serie PVPC contiene una hora no válida.",
    "response_too_large": "La respuesta PVPC supera el tamaño permitido.",
    "upstream_status": "REE no ha devuelto una respuesta PVPC disponible.",
    "network_error": "No se pudo consultar el servicio público PVPC.",
    "timeout": "La consulta del servicio público PVPC agotó el tiempo límite.",
    "invalid_json": "La respuesta pública PVPC no es JSON válido.",
    "invalid_snapshot": "El snapshot PVPC no cumple el contrato de validación.",
    "missing_day": "Falta un día de precios para cubrir la carga completa.",
    "unpriced_interval": "No hay precio PVPC para cubrir toda la carga.",
    "invalid_charge": "Los datos de la carga no son válidos para cotizar PVPC.",
    "invalid_pricing": "El desglose PVPC de la carga no es válido.",
}


class PvpcError(ValueError):
    """Expected PVPC failure with a fixed, non-sensitive public message."""

    def __init__(self, code: str) -> None:
        self.code = code if code in _ERROR_MESSAGES else "invalid_payload"
        super().__init__(_ERROR_MESSAGES[self.code])


def _aware_datetime(value: Any, *, code: str = "invalid_timestamp") -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PvpcError(code)
    return value


def _parse_iso_datetime(value: Any, *, code: str = "invalid_timestamp") -> datetime:
    if not isinstance(value, str) or not value:
        raise PvpcError(code)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except (TypeError, ValueError, OverflowError):
        raise PvpcError(code) from None
    return _aware_datetime(parsed, code=code)


def _iso_utc(value: datetime) -> str:
    normalized = _aware_datetime(value).astimezone(_UTC)
    return normalized.isoformat().replace("+00:00", "Z")


def _decimal_number(value: Any, *, code: str, lower: Decimal | None = None,
                    upper: Decimal | None = None) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise PvpcError(code)
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise PvpcError(code) from None
    if not number.is_finite() or (lower is not None and number < lower) or (
        upper is not None and number > upper
    ):
        raise PvpcError(code)
    return number


def _local_day_bounds(day: date) -> tuple[datetime, datetime, int]:
    try:
        next_day = day + timedelta(days=1)
    except OverflowError:
        raise PvpcError("invalid_day") from None
    start_local = datetime.combine(day, time.min, tzinfo=_MADRID)
    end_local = datetime.combine(next_day, time.min, tzinfo=_MADRID)
    start_utc = start_local.astimezone(_UTC)
    end_utc = end_local.astimezone(_UTC)
    seconds = int((end_utc - start_utc).total_seconds())
    if seconds not in (23 * 3600, 24 * 3600, 25 * 3600):
        raise PvpcError("invalid_day")
    return start_utc, end_utc, seconds // 3600


def _timestamp_matches_madrid(value: datetime, expected_utc: datetime) -> bool:
    expected_local = expected_utc.astimezone(_MADRID)
    return (
        value.utcoffset() == expected_local.utcoffset()
        and value.replace(tzinfo=None) == expected_local.replace(tzinfo=None)
    )


def validate_day_snapshot(snapshot: Any) -> dict[str, Any]:
    """Validate and normalize one complete REData PVPC day snapshot.

    The required fields and hourly coverage are strict. Snapshot timestamps
    must use UTC; their absolute instants must match the real local-day bounds.
    """
    if not isinstance(snapshot, Mapping) or set(snapshot) != {
        "date", "region", "timezone", "source", "fetched_at", "intervals"
    }:
        raise PvpcError("invalid_snapshot")
    if (
        snapshot.get("region") != REGION
        or snapshot.get("timezone") != TIMEZONE
        or snapshot.get("source") != SOURCE
    ):
        raise PvpcError("invalid_snapshot")
    raw_day = snapshot.get("date")
    if not isinstance(raw_day, str):
        raise PvpcError("invalid_snapshot")
    try:
        day = date.fromisoformat(raw_day)
    except (TypeError, ValueError):
        raise PvpcError("invalid_snapshot") from None
    if day.isoformat() != raw_day:
        raise PvpcError("invalid_snapshot")

    fetched_at = _parse_iso_datetime(snapshot.get("fetched_at"), code="invalid_snapshot")
    starts_at, ends_at, expected_count = _local_day_bounds(day)
    intervals = snapshot.get("intervals")
    if not isinstance(intervals, list) or len(intervals) != expected_count:
        raise PvpcError("incomplete_day")

    normalized_intervals: list[dict[str, Any]] = []
    for index, interval in enumerate(intervals):
        if not isinstance(interval, Mapping) or set(interval) != {
            "started_at", "ended_at", "price_eur_kwh"
        }:
            raise PvpcError("invalid_snapshot")
        expected_start = starts_at + timedelta(hours=index)
        expected_end = expected_start + timedelta(hours=1)
        parsed_start = _parse_iso_datetime(interval.get("started_at"))
        parsed_end = _parse_iso_datetime(interval.get("ended_at"))
        if (
            parsed_start.utcoffset() != timedelta(0)
            or parsed_end.utcoffset() != timedelta(0)
            or parsed_start.astimezone(_UTC) != expected_start
            or parsed_end.astimezone(_UTC) != expected_end
            or expected_end > ends_at
        ):
            raise PvpcError("incomplete_day")
        price = _decimal_number(
            interval.get("price_eur_kwh"),
            code="invalid_value",
            lower=MIN_PRICE_EUR_KWH,
            upper=MAX_PRICE_EUR_KWH,
        )
        normalized_intervals.append({
            "started_at": _iso_utc(parsed_start),
            "ended_at": _iso_utc(parsed_end),
            "price_eur_kwh": float(price),
        })

    if normalized_intervals[0]["started_at"] != _iso_utc(starts_at) or (
        normalized_intervals[-1]["ended_at"] != _iso_utc(ends_at)
    ):
        raise PvpcError("incomplete_day")
    return {
        "date": raw_day,
        "region": REGION,
        "timezone": TIMEZONE,
        "source": SOURCE,
        "fetched_at": _iso_utc(fetched_at),
        "intervals": normalized_intervals,
    }


def parse_day(payload: Any, day: date, fetched_at: datetime) -> dict[str, Any]:
    """Parse one REData response into a validated hourly peninsula snapshot."""
    if not isinstance(day, date) or isinstance(day, datetime):
        raise PvpcError("invalid_day")
    fetched_at = _aware_datetime(fetched_at)
    if not isinstance(payload, Mapping):
        raise PvpcError("invalid_payload")
    included = payload.get("included")
    if not isinstance(included, list):
        raise PvpcError("invalid_payload")

    matches = [
        item for item in included
        if isinstance(item, Mapping) and item.get("type") == PVPC_TYPE and item.get("id") == PVPC_ID
    ]
    if not matches:
        raise PvpcError("series_missing")
    if len(matches) != 1:
        raise PvpcError("series_duplicate")
    attributes = matches[0].get("attributes")
    if not isinstance(attributes, Mapping):
        raise PvpcError("invalid_payload")
    values = attributes.get("values")
    if not isinstance(values, list):
        raise PvpcError("invalid_payload")

    day_start, _day_end, expected_count = _local_day_bounds(day)
    if len(values) != expected_count:
        raise PvpcError("incomplete_day")
    parsed: list[tuple[datetime, Decimal]] = []
    for item in values:
        if not isinstance(item, Mapping):
            raise PvpcError("invalid_payload")
        timestamp = _parse_iso_datetime(item.get("datetime"))
        price_mwh = _decimal_number(item.get("value"), code="invalid_value")
        price_kwh = price_mwh / Decimal("1000")
        if price_kwh < MIN_PRICE_EUR_KWH or price_kwh > MAX_PRICE_EUR_KWH:
            raise PvpcError("invalid_value")
        parsed.append((timestamp, price_kwh))

    parsed.sort(key=lambda point: point[0].astimezone(_UTC))
    intervals: list[dict[str, Any]] = []
    for index, (timestamp, price) in enumerate(parsed):
        expected_start = day_start + timedelta(hours=index)
        expected_end = expected_start + timedelta(hours=1)
        if (
            not _timestamp_matches_madrid(timestamp, expected_start)
            or timestamp.astimezone(_UTC) != expected_start
        ):
            raise PvpcError("incomplete_day")
        intervals.append({
            "started_at": _iso_utc(expected_start),
            "ended_at": _iso_utc(expected_end),
            "price_eur_kwh": float(price),
        })

    snapshot = {
        "date": day.isoformat(),
        "region": REGION,
        "timezone": TIMEZONE,
        "source": SOURCE,
        "fetched_at": _iso_utc(fetched_at),
        "intervals": intervals,
    }
    return validate_day_snapshot(snapshot)


async def _read_bounded_response(client: Any, params: dict[str, str]) -> bytes:
    timeout = httpx2.Timeout(REQUEST_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS)
    chunks: list[bytes] = []
    total_bytes = 0
    async with client.stream(
        "GET",
        REE_URL,
        params=params,
        headers={"Accept": "application/json"},
        follow_redirects=False,
        timeout=timeout,
    ) as response:
        if response.status_code != 200:
            raise PvpcError("upstream_status")
        raw_length = response.headers.get("content-length")
        if raw_length is not None:
            try:
                content_length = int(raw_length)
            except ValueError:
                content_length = None
            if content_length is not None and content_length > MAX_RESPONSE_BYTES:
                raise PvpcError("response_too_large")
        async for chunk in response.aiter_bytes():
            total_bytes += len(chunk)
            if total_bytes > MAX_RESPONSE_BYTES:
                raise PvpcError("response_too_large")
            chunks.append(chunk)
    return b"".join(chunks)


async def fetch_day(day: date, *, client: Any | None = None) -> dict[str, Any]:
    """Fetch one public REData day with a fixed destination and bounded body."""
    if not isinstance(day, date) or isinstance(day, datetime):
        raise PvpcError("invalid_day")
    params = {
        "start_date": f"{day.isoformat()}T00:00",
        "end_date": f"{day.isoformat()}T23:59",
        "time_trunc": "hour",
        "geo_trunc": "electric_system",
        "geo_limit": "peninsular",
        "geo_ids": "8741",
    }

    async def fetch_with(request_client: Any) -> dict[str, Any]:
        async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
            body = await _read_bounded_response(request_client, params)
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError, TypeError, RecursionError):
            raise PvpcError("invalid_json") from None
        return parse_day(payload, day, datetime.now(_UTC))

    try:
        if client is not None:
            return await fetch_with(client)
        async with httpx2.AsyncClient(
            trust_env=False,
            follow_redirects=False,
            timeout=httpx2.Timeout(REQUEST_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS),
        ) as owned_client:
            return await fetch_with(owned_client)
    except PvpcError:
        raise
    except asyncio.CancelledError:
        raise
    except (asyncio.TimeoutError, httpx2.TimeoutException):
        raise PvpcError("timeout") from None
    except httpx2.HTTPError:
        raise PvpcError("network_error") from None


def _charge_values(started_at: Any, ended_at: Any, kwh: Any) -> tuple[datetime, datetime, Decimal]:
    start = _parse_iso_datetime(started_at, code="invalid_charge").astimezone(_UTC)
    end = _parse_iso_datetime(ended_at, code="invalid_charge").astimezone(_UTC)
    energy = _decimal_number(kwh, code="invalid_charge", lower=Decimal("0"), upper=MAX_CHARGE_KWH)
    duration = (end - start).total_seconds()
    if energy <= 0 or duration <= 0 or duration > MAX_CHARGE_SECONDS:
        raise PvpcError("invalid_charge")
    return start, end, energy


def _round_money(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def quote_charge(
    started_at: str,
    ended_at: str,
    kwh: float,
    days: list[dict[str, Any]],
    *,
    calculated_at: datetime,
) -> dict[str, Any]:
    """Estimate a charge cost from complete PVPC days and total measured kWh."""
    start, end, energy = _charge_values(started_at, ended_at, kwh)
    calculated_at = _aware_datetime(calculated_at, code="invalid_charge")
    if not isinstance(days, list):
        raise PvpcError("invalid_snapshot")

    snapshots: dict[str, dict[str, Any]] = {}
    for day_snapshot in days:
        normalized = validate_day_snapshot(day_snapshot)
        if normalized["date"] in snapshots:
            raise PvpcError("invalid_snapshot")
        snapshots[normalized["date"]] = normalized

    first_day = start.astimezone(_MADRID).date()
    last_day = (end - timedelta(microseconds=1)).astimezone(_MADRID).date()
    required_days: list[date] = []
    cursor_day = first_day
    while cursor_day <= last_day:
        required_days.append(cursor_day)
        cursor_day += timedelta(days=1)
    if any(day.isoformat() not in snapshots for day in required_days):
        raise PvpcError("missing_day")

    duration_seconds = Decimal(str((end - start).total_seconds()))
    total_seconds = duration_seconds
    cursor = start
    priced_intervals: list[dict[str, Any]] = []
    weighted_price_seconds = Decimal("0")
    for day in required_days:
        snapshot = snapshots[day.isoformat()]
        for source_interval in snapshot["intervals"]:
            interval_start = _parse_iso_datetime(source_interval["started_at"]).astimezone(_UTC)
            interval_end = _parse_iso_datetime(source_interval["ended_at"]).astimezone(_UTC)
            overlap_start = max(start, interval_start)
            overlap_end = min(end, interval_end)
            if overlap_start >= overlap_end:
                continue
            if overlap_start != cursor:
                raise PvpcError("unpriced_interval")
            overlap_seconds = Decimal(str((overlap_end - overlap_start).total_seconds()))
            interval_kwh = energy * overlap_seconds / total_seconds
            price = Decimal(str(source_interval["price_eur_kwh"]))
            exact_cost = interval_kwh * price
            interval_cost = exact_cost
            weighted_price_seconds += overlap_seconds * price
            priced_intervals.append({
                "started_at": _iso_utc(overlap_start),
                "ended_at": _iso_utc(overlap_end),
                "kwh": float(interval_kwh),
                "price_eur_kwh": float(price),
                "cost_eur": float(interval_cost),
                "price_fetched_at": snapshot["fetched_at"],
            })
            cursor = overlap_end

    if cursor != end or not priced_intervals:
        raise PvpcError("unpriced_interval")
    # Aggregate before division/float serialization so a decimal half-cent is
    # rounded once from the exact time-weighted price, including negative ties.
    unrounded_total = energy * weighted_price_seconds / total_seconds
    cost = _round_money(unrounded_total)
    pricing = {
        "method": "pvpc",
        "accuracy": "estimated_uniform",
        "region": REGION,
        "source": SOURCE,
        "taxes_included": False,
        "calculated_at": _iso_utc(calculated_at),
        "intervals": priced_intervals,
    }
    return {
        "cost_eur": float(cost),
        "tariff_eur_kwh": float(weighted_price_seconds / total_seconds),
        "pricing": pricing,
    }


def validate_pricing_snapshot(
    pricing: Any,
    started_at: str,
    ended_at: str,
    kwh: float,
    cost_eur: float,
) -> dict[str, Any]:
    """Strictly validate and normalize a stored/imported PVPC cost breakdown."""
    start, end, total_energy = _charge_values(started_at, ended_at, kwh)
    supplied_cost = _decimal_number(
        cost_eur,
        code="invalid_pricing",
        lower=Decimal("-2500"),
        upper=Decimal("2500"),
    )
    if not isinstance(pricing, Mapping) or set(pricing) != {
        "method", "accuracy", "region", "source", "taxes_included", "calculated_at", "intervals"
    }:
        raise PvpcError("invalid_pricing")
    if (
        pricing.get("method") != "pvpc"
        or pricing.get("accuracy") != "estimated_uniform"
        or pricing.get("region") != REGION
        or pricing.get("source") != SOURCE
        or pricing.get("taxes_included") is not False
    ):
        raise PvpcError("invalid_pricing")
    calculated_at = _parse_iso_datetime(pricing.get("calculated_at"), code="invalid_pricing")
    raw_intervals = pricing.get("intervals")
    if not isinstance(raw_intervals, list) or not raw_intervals:
        raise PvpcError("invalid_pricing")

    total_seconds = Decimal(str((end - start).total_seconds()))
    cursor = start
    normalized_intervals: list[dict[str, Any]] = []
    total_line_energy = Decimal("0")
    weighted_price_seconds = Decimal("0")
    fetched_by_day: dict[date, datetime] = {}
    for index, interval in enumerate(raw_intervals):
        if not isinstance(interval, Mapping) or set(interval) != {
            "started_at", "ended_at", "kwh", "price_eur_kwh", "cost_eur", "price_fetched_at"
        }:
            raise PvpcError("invalid_pricing")
        interval_start = _parse_iso_datetime(interval.get("started_at"), code="invalid_pricing").astimezone(_UTC)
        interval_end = _parse_iso_datetime(interval.get("ended_at"), code="invalid_pricing").astimezone(_UTC)
        if interval_start != cursor or interval_end <= interval_start or interval_end > end:
            raise PvpcError("invalid_pricing")
        if (interval_end - interval_start).total_seconds() > 3600:
            raise PvpcError("invalid_pricing")
        if index > 0 and (interval_start.minute or interval_start.second or interval_start.microsecond):
            raise PvpcError("invalid_pricing")
        if interval_end != end and (
            interval_end.minute or interval_end.second or interval_end.microsecond
        ):
            raise PvpcError("invalid_pricing")

        interval_energy = _decimal_number(
            interval.get("kwh"), code="invalid_pricing", lower=Decimal("0"), upper=MAX_CHARGE_KWH
        )
        price = _decimal_number(
            interval.get("price_eur_kwh"),
            code="invalid_pricing",
            lower=MIN_PRICE_EUR_KWH,
            upper=MAX_PRICE_EUR_KWH,
        )
        interval_cost = _decimal_number(
            interval.get("cost_eur"),
            code="invalid_pricing",
            lower=Decimal("-2500"),
            upper=Decimal("2500"),
        )
        expected_seconds = Decimal(str((interval_end - interval_start).total_seconds()))
        expected_energy = total_energy * expected_seconds / total_seconds
        if abs(interval_energy - expected_energy) > _ENERGY_TOLERANCE:
            raise PvpcError("invalid_pricing")
        if abs(interval_cost - expected_energy * price) > _COST_TOLERANCE:
            raise PvpcError("invalid_pricing")

        fetched_at = _parse_iso_datetime(interval.get("price_fetched_at"), code="invalid_pricing")
        local_day = interval_start.astimezone(_MADRID).date()
        prior_fetch = fetched_by_day.setdefault(local_day, fetched_at.astimezone(_UTC))
        if prior_fetch != fetched_at.astimezone(_UTC):
            raise PvpcError("invalid_pricing")
        normalized_intervals.append({
            "started_at": _iso_utc(interval_start),
            "ended_at": _iso_utc(interval_end),
            "kwh": float(interval_energy),
            "price_eur_kwh": float(price),
            "cost_eur": float(interval_cost),
            "price_fetched_at": _iso_utc(fetched_at),
        })
        total_line_energy += interval_energy
        weighted_price_seconds += expected_seconds * price
        cursor = interval_end

    if cursor != end or abs(total_line_energy - total_energy) > _ENERGY_TOLERANCE:
        raise PvpcError("invalid_pricing")
    exact_total_cost = total_energy * weighted_price_seconds / total_seconds
    if supplied_cost != _round_money(supplied_cost) or _round_money(exact_total_cost) != supplied_cost:
        raise PvpcError("invalid_pricing")

    return {
        "method": "pvpc",
        "accuracy": "estimated_uniform",
        "region": REGION,
        "source": SOURCE,
        "taxes_included": False,
        "calculated_at": _iso_utc(calculated_at),
        "intervals": normalized_intervals,
    }


__all__ = [
    "PVPC_ID",
    "PVPC_TYPE",
    "REE_URL",
    "REGION",
    "SOURCE",
    "TIMEZONE",
    "PvpcError",
    "fetch_day",
    "parse_day",
    "quote_charge",
    "validate_day_snapshot",
    "validate_pricing_snapshot",
]
