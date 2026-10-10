"""Synthetic tests for public REData parsing and PVPC quote validation."""

import asyncio
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx2
import pytest

from pulse import pvpc


MADRID = ZoneInfo("Europe/Madrid")
UTC = timezone.utc
SYNTHETIC = "test-only synthetic REData-shaped data; not a provider response"


def synthetic_payload(day: date, *, value_mwh=150.0, values_override=None):
    start = datetime.combine(day, time.min, tzinfo=MADRID)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=MADRID)
    rows = []
    current = start
    index = 0
    while current.astimezone(UTC) < end.astimezone(UTC):
        value = value_mwh(index) if callable(value_mwh) else value_mwh
        rows.append({"datetime": current.isoformat(), "value": value})
        current = (current.astimezone(UTC) + timedelta(hours=1)).astimezone(MADRID)
        index += 1
    if values_override is not None:
        rows = values_override(rows)
    return {
        "included": [{
            "type": "PVPC",
            "id": "1001",
            "attributes": {"values": rows},
        }],
        "fixture_label": SYNTHETIC,
    }


def snapshot_for(day: date, *, value_mwh=150.0):
    return pvpc.parse_day(
        synthetic_payload(day, value_mwh=value_mwh),
        day,
        datetime(2026, 10, 8, 9, 0, tzinfo=UTC),
    )


def test_parse_normal_day_converts_eur_per_mwh_to_eur_per_kwh():
    snapshot = snapshot_for(date(2026, 1, 15), value_mwh=187.5)

    assert snapshot["region"] == "peninsula"
    assert snapshot["timezone"] == "Europe/Madrid"
    assert len(snapshot["intervals"]) == 24
    assert snapshot["intervals"][0]["price_eur_kwh"] == 0.1875
    assert snapshot["intervals"][0]["started_at"].endswith("Z")
    assert snapshot["fetched_at"] == "2026-10-08T09:00:00Z"


@pytest.mark.parametrize(("day", "hours"), [(date(2025, 3, 30), 23), (date(2025, 10, 26), 25)])
def test_parse_dst_days_has_full_real_hour_coverage(day, hours):
    snapshot = snapshot_for(day)

    assert len(snapshot["intervals"]) == hours
    assert snapshot["intervals"][0]["started_at"] == (
        datetime.combine(day, time.min, tzinfo=MADRID).astimezone(UTC).isoformat().replace("+00:00", "Z")
    )
    assert snapshot["intervals"][-1]["ended_at"] == (
        datetime.combine(day + timedelta(days=1), time.min, tzinfo=MADRID)
        .astimezone(UTC).isoformat().replace("+00:00", "Z")
    )


def test_negative_pvpc_price_is_preserved():
    snapshot = snapshot_for(date(2026, 1, 15), value_mwh=-125.0)

    assert snapshot["intervals"][0]["price_eur_kwh"] == -0.125


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda rows: rows[:-1], "incomplete_day"),
        (lambda rows: rows + [rows[-1]], "incomplete_day"),
        (lambda rows: [{**rows[0], "datetime": "2026-01-15T00:15:00+01:00"}, *rows[1:]], "incomplete_day"),
        (lambda rows: [{**rows[0], "value": 5_001_000}, *rows[1:]], "invalid_value"),
        (lambda rows: [{**rows[0], "datetime": "2026-01-15T00:00:00+00:00"}, *rows[1:]], "incomplete_day"),
    ],
)
def test_rejects_incomplete_duplicate_spot_or_invalid_series(mutate, code):
    day = date(2026, 1, 15)
    payload = synthetic_payload(day, values_override=mutate)

    with pytest.raises(pvpc.PvpcError) as error:
        pvpc.parse_day(payload, day, datetime(2026, 10, 8, tzinfo=UTC))

    assert error.value.code == code


def test_rejects_missing_or_duplicate_pvpc_identity():
    day = date(2026, 1, 15)
    payload = synthetic_payload(day)
    payload["included"][0]["id"] = "other"
    with pytest.raises(pvpc.PvpcError, match="serie PVPC") as missing:
        pvpc.parse_day(payload, day, datetime(2026, 10, 8, tzinfo=UTC))
    assert missing.value.code == "series_missing"

    payload = synthetic_payload(day)
    payload["included"].append(deepcopy(payload["included"][0]))
    with pytest.raises(pvpc.PvpcError) as duplicate:
        pvpc.parse_day(payload, day, datetime(2026, 10, 8, tzinfo=UTC))
    assert duplicate.value.code == "series_duplicate"


def test_day_snapshot_validator_rejects_gaps_bad_offsets_and_extra_fields():
    snapshot = snapshot_for(date(2026, 1, 15))
    broken = deepcopy(snapshot)
    broken["intervals"][3]["started_at"] = "2026-01-15T03:15:00Z"
    with pytest.raises(pvpc.PvpcError):
        pvpc.validate_day_snapshot(broken)

    wrong_offset = deepcopy(snapshot)
    wrong_offset["intervals"][0]["started_at"] = "2026-01-15T01:00:00+02:00"
    with pytest.raises(pvpc.PvpcError):
        pvpc.validate_day_snapshot(wrong_offset)

    extra_field = deepcopy(snapshot)
    extra_field["vehicle_id"] = "synthetic"
    with pytest.raises(pvpc.PvpcError):
        pvpc.validate_day_snapshot(extra_field)


def test_quote_splits_energy_across_hour_and_midnight_with_negative_prices():
    first_day = date(2026, 1, 15)
    second_day = first_day + timedelta(days=1)
    day_one = snapshot_for(first_day, value_mwh=lambda index: -100 if index == 23 else 100)
    day_two = snapshot_for(second_day, value_mwh=300)
    quote = pvpc.quote_charge(
        "2026-01-15T22:30:00Z",
        "2026-01-15T23:30:00Z",
        4.0,
        [day_one, day_two],
        calculated_at=datetime(2026, 10, 8, 10, 0, tzinfo=UTC),
    )

    assert quote["cost_eur"] == 0.4
    assert quote["tariff_eur_kwh"] == pytest.approx(0.1)
    assert [row["kwh"] for row in quote["pricing"]["intervals"]] == [2.0, 2.0]
    assert [row["price_eur_kwh"] for row in quote["pricing"]["intervals"]] == [-0.1, 0.3]
    assert quote["pricing"]["taxes_included"] is False


def test_quote_uses_both_repeated_dst_hours_as_two_elapsed_hours():
    day = date(2025, 10, 26)
    snapshot = snapshot_for(day, value_mwh=lambda index: 100 if index == 2 else 300)
    quote = pvpc.quote_charge(
        "2025-10-26T02:00:00+02:00",
        "2025-10-26T03:00:00+01:00",
        2.0,
        [snapshot],
        calculated_at=datetime(2026, 10, 8, tzinfo=UTC),
    )

    assert len(quote["pricing"]["intervals"]) == 2
    assert [item["price_eur_kwh"] for item in quote["pricing"]["intervals"]] == [0.1, 0.3]
    assert quote["cost_eur"] == 0.4


def test_quote_rejects_missing_day_bad_duration_and_invalid_energy():
    day = date(2026, 1, 15)
    with pytest.raises(pvpc.PvpcError) as missing:
        pvpc.quote_charge(
            "2026-01-15T22:30:00Z", "2026-01-15T23:30:00Z", 1.0, [],
            calculated_at=datetime(2026, 10, 8, tzinfo=UTC),
        )
    assert missing.value.code == "missing_day"

    snapshot = snapshot_for(day)
    with pytest.raises(pvpc.PvpcError):
        pvpc.quote_charge(
            "2026-01-15T00:00:00Z", "2026-01-18T01:00:00Z", 1.0, [snapshot],
            calculated_at=datetime(2026, 10, 8, tzinfo=UTC),
        )
    with pytest.raises(pvpc.PvpcError):
        pvpc.quote_charge(
            "2026-01-15T00:00:00Z", "2026-01-15T01:00:00Z", 0.0, [snapshot],
            calculated_at=datetime(2026, 10, 8, tzinfo=UTC),
        )


def test_pricing_snapshot_validates_and_normalizes_quote_but_rejects_tampering():
    day = date(2026, 1, 15)
    snapshot = snapshot_for(day, value_mwh=-125)
    quote = pvpc.quote_charge(
        "2026-01-15T08:30:00Z",
        "2026-01-15T09:30:00Z",
        2.0,
        [snapshot],
        calculated_at=datetime(2026, 10, 8, 10, tzinfo=UTC),
    )
    normalized = pvpc.validate_pricing_snapshot(
        quote["pricing"],
        "2026-01-15T08:30:00Z",
        "2026-01-15T09:30:00Z",
        2.0,
        quote["cost_eur"],
    )
    assert normalized == quote["pricing"]

    tampered = deepcopy(quote["pricing"])
    tampered["intervals"][0]["kwh"] += 0.5
    with pytest.raises(pvpc.PvpcError):
        pvpc.validate_pricing_snapshot(
            tampered, "2026-01-15T08:30:00Z", "2026-01-15T09:30:00Z", 2.0, quote["cost_eur"]
        )

    tampered = deepcopy(quote["pricing"])
    tampered["intervals"][1]["started_at"] = "2026-01-15T09:15:00Z"
    with pytest.raises(pvpc.PvpcError):
        pvpc.validate_pricing_snapshot(
            tampered, "2026-01-15T08:30:00Z", "2026-01-15T09:30:00Z", 2.0, quote["cost_eur"]
        )


@pytest.mark.parametrize(("sign", "expected_cost"), [(1, 0.21), (-1, -0.21)])
def test_half_cent_rounding_survives_quote_export_and_import(sign, expected_cost):
    day = date(2026, 1, 15)
    prices_mwh = {9: sign * 10, 10: sign * 10, 11: sign * 595}
    snapshot = snapshot_for(day, value_mwh=lambda index: prices_mwh.get(index, 10))
    quote = pvpc.quote_charge(
        "2026-01-15T08:00:00Z",
        "2026-01-15T11:00:00Z",
        1.0,
        [snapshot],
        calculated_at=datetime(2026, 10, 8, 10, tzinfo=UTC),
    )

    assert quote["cost_eur"] == expected_cost
    assert quote["tariff_eur_kwh"] == pytest.approx(sign * 0.205)
    # The line floats can sum just below the exact tie; import recomputes from
    # price x elapsed seconds instead of rounding that serialized sum.
    serialized_cost = sum(
        (Decimal(str(item["cost_eur"])) for item in quote["pricing"]["intervals"]),
        Decimal("0"),
    )
    assert serialized_cost == Decimal("0.204999999999999997") * sign
    imported = pvpc.validate_pricing_snapshot(
        quote["pricing"],
        "2026-01-15T08:00:00Z",
        "2026-01-15T11:00:00Z",
        1.0,
        quote["cost_eur"],
    )
    assert imported == quote["pricing"]


def test_fetch_day_uses_public_fixed_request_and_normalizes_synthetic_response():
    day = date(2025, 10, 26)
    payload = synthetic_payload(day)
    observed_requests = []

    async def handler(request):
        observed_requests.append(request)
        return httpx2.Response(200, json=payload)

    async def run():
        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(handler), trust_env=False
        ) as client:
            return await pvpc.fetch_day(day, client=client)

    result = asyncio.run(run())
    request = observed_requests[0]
    assert request.url.scheme == "https"
    assert request.url.host == "apidatos.ree.es"
    assert request.url.params["time_trunc"] == "hour"
    assert request.url.params["geo_limit"] == "peninsular"
    assert request.url.params["geo_ids"] == "8741"
    assert len(result["intervals"]) == 25


def test_fetch_day_fails_closed_on_error_response_and_oversize_without_reflection():
    day = date(2026, 1, 15)

    async def unavailable(_request):
        return httpx2.Response(503, text="synthetic private-looking value")

    async def oversized(_request):
        return httpx2.Response(200, content=b"x" * (pvpc.MAX_RESPONSE_BYTES + 1))

    async def oversized_header(_request):
        return httpx2.Response(
            200,
            content=b"{}",
            headers={"content-length": str(pvpc.MAX_RESPONSE_BYTES + 1)},
        )

    async def redirect(_request):
        return httpx2.Response(302, headers={"location": "https://example.invalid/"})

    async def malformed_json(_request):
        return httpx2.Response(200, content=b"{")

    async def run(handler):
        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(handler), trust_env=False
        ) as client:
            return await pvpc.fetch_day(day, client=client)

    with pytest.raises(pvpc.PvpcError) as status:
        asyncio.run(run(unavailable))
    assert status.value.code == "upstream_status"
    assert "synthetic private-looking value" not in str(status.value)

    with pytest.raises(pvpc.PvpcError) as large:
        asyncio.run(run(oversized))
    assert large.value.code == "response_too_large"

    with pytest.raises(pvpc.PvpcError) as header_large:
        asyncio.run(run(oversized_header))
    assert header_large.value.code == "response_too_large"

    with pytest.raises(pvpc.PvpcError) as redirected:
        asyncio.run(run(redirect))
    assert redirected.value.code == "upstream_status"

    with pytest.raises(pvpc.PvpcError) as malformed:
        asyncio.run(run(malformed_json))
    assert malformed.value.code == "invalid_json"


def test_fetch_day_converts_transport_timeout_to_fixed_error(monkeypatch):
    day = date(2026, 1, 15)
    monkeypatch.setattr(pvpc, "REQUEST_TIMEOUT_SECONDS", 0.01)

    async def slow(_request):
        await asyncio.sleep(0.1)
        return httpx2.Response(200, json=synthetic_payload(day))

    async def run():
        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(slow), trust_env=False
        ) as client:
            return await pvpc.fetch_day(day, client=client)

    with pytest.raises(pvpc.PvpcError) as error:
        asyncio.run(run())
    assert error.value.code == "timeout"
