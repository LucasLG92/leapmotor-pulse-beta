"""Synthetic tests for the interval-based PVPC engine."""

from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from pulse import pvpc, temporal_pvpc


UTC = timezone.utc
MADRID = ZoneInfo("Europe/Madrid")
FETCHED_AT = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
SCOPE = "a" * 64
SESSION_ID = "b" * 32


def iso(value):
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def distribution(start, end, intervals, **overrides):
    return {
        "version": 1,
        "scope": SCOPE,
        "session_id": SESSION_ID,
        "source_id": "synthetic.test",
        "source_version": "v1",
        "basis": "charger_total",
        "intervals": [
            {"started_at": iso(left), "ended_at": iso(right), "kwh": energy}
            for left, right, energy in intervals
        ],
        **overrides,
    }


def synthetic_day(day, prices_by_utc_hour=None):
    """Build a local-only REData-shaped payload and pass it through pvpc parsing."""
    prices_by_utc_hour = prices_by_utc_hour or {}
    local_start = datetime.combine(day, time.min, tzinfo=MADRID)
    local_end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=MADRID)
    cursor = local_start.astimezone(UTC)
    end = local_end.astimezone(UTC)
    values = []
    while cursor < end:
        price = prices_by_utc_hour.get(cursor, Decimal("0.1"))
        values.append({
            "datetime": cursor.astimezone(MADRID).isoformat(),
            "value": float(price * 1000),
        })
        cursor += timedelta(hours=1)
    payload = {"included": [{
        "type": "PVPC",
        "id": "1001",
        "attributes": {"values": values},
    }]}
    return pvpc.parse_day(payload, day, FETCHED_AT)


def test_uneven_distribution_prices_energy_differently_from_uniform_estimate():
    start = datetime(2026, 1, 15, 8, tzinfo=UTC)
    middle = start + timedelta(hours=1)
    end = middle + timedelta(hours=1)
    curve = synthetic_day(date(2026, 1, 15), {
        start: Decimal("0.1"), middle: Decimal("0.3"),
    })
    source = distribution(start, end, [(start, middle, 1), (middle, end, 3)])

    quote = temporal_pvpc.quote_charge_intervals(
        iso(start), iso(end), 4, source, [curve], calculated_at=FETCHED_AT,
    )

    assert quote["cost_eur"] == 1.0
    assert quote["tariff_eur_kwh"] == 0.25
    assert [line["kwh"] for line in quote["pricing"]["intervals"]] == [1.0, 3.0]
    assert quote["pricing"]["accuracy"] == "estimated_intervals"
    assert quote["pricing"]["energy_distribution"] == source
    uniform = pvpc.quote_charge(
        iso(start), iso(end), 4, [curve], calculated_at=FETCHED_AT,
    )
    assert uniform["cost_eur"] == 0.8
    assert quote["cost_eur"] != uniform["cost_eur"]


def test_explicit_zero_pause_is_preserved_in_quote_and_snapshot():
    start = datetime(2026, 1, 15, 8, tzinfo=UTC)
    first_end = start + timedelta(hours=1)
    pause_end = first_end + timedelta(hours=1)
    end = pause_end + timedelta(hours=1)
    curve = synthetic_day(date(2026, 1, 15), {
        start: Decimal("0.1"), first_end: Decimal("0.2"),
        pause_end: Decimal("-0.1"),
    })
    source = distribution(start, end, [
        (start, first_end, 1), (first_end, pause_end, 0), (pause_end, end, 1),
    ])
    quote = temporal_pvpc.quote_charge_intervals(
        iso(start), iso(end), 2, source, [curve], calculated_at=FETCHED_AT,
    )

    assert [line["kwh"] for line in quote["pricing"]["intervals"]] == [1.0, 0.0, 1.0]
    assert [line["cost_eur"] for line in quote["pricing"]["intervals"]] == [0.1, 0.0, -0.1]
    assert quote["cost_eur"] == 0.0
    assert temporal_pvpc.validate_temporal_pricing_snapshot(
        quote["pricing"], iso(start), iso(end), 2, quote["cost_eur"]
    ) == quote["pricing"]


def test_partial_hours_and_madrid_midnight_are_split_at_tariff_boundaries():
    start = datetime(2026, 1, 15, 23, 30, tzinfo=UTC)
    end = start + timedelta(hours=1)
    midnight = datetime(2026, 1, 16, 0, tzinfo=UTC)
    first_day = synthetic_day(date(2026, 1, 15), {start.replace(minute=0): Decimal("0.1")})
    second_day = synthetic_day(date(2026, 1, 16), {midnight: Decimal("0.3")})
    source = distribution(start, end, [(start, end, 1)])
    quote = temporal_pvpc.quote_charge_intervals(
        iso(start), iso(end), 1, source, [first_day, second_day],
        calculated_at=FETCHED_AT,
    )

    assert [(line["started_at"], line["ended_at"]) for line in quote["pricing"]["intervals"]] == [
        ("2026-01-15T23:30:00Z", "2026-01-16T00:00:00Z"),
        ("2026-01-16T00:00:00Z", "2026-01-16T00:30:00Z"),
    ]
    assert [line["kwh"] for line in quote["pricing"]["intervals"]] == [0.5, 0.5]
    assert quote["cost_eur"] == 0.2

    partial_start = datetime(2026, 1, 15, 8, 15, tzinfo=UTC)
    partial_end = datetime(2026, 1, 15, 9, 45, tzinfo=UTC)
    split = synthetic_day(date(2026, 1, 15), {
        datetime(2026, 1, 15, 8, tzinfo=UTC): Decimal("0.1"),
        datetime(2026, 1, 15, 9, tzinfo=UTC): Decimal("0.3"),
    })
    partial_midpoint = datetime(2026, 1, 15, 9, tzinfo=UTC)
    partial_curve = distribution(partial_start, partial_end, [
        (partial_start, partial_midpoint, 0.5),
        (partial_midpoint, partial_end, 0.5),
    ])
    partial_quote = temporal_pvpc.quote_charge_intervals(
        iso(partial_start), iso(partial_end), 1, partial_curve, [split],
        calculated_at=FETCHED_AT,
    )
    assert [line["kwh"] for line in partial_quote["pricing"]["intervals"]] == [0.5, 0.5]
    assert partial_quote["cost_eur"] == 0.2


def test_spring_forward_day_uses_23_real_hours_and_skips_nonexistent_local_hour():
    day = date(2025, 3, 30)
    start = datetime(2025, 3, 30, 0, 30, tzinfo=UTC)
    boundary = datetime(2025, 3, 30, 1, tzinfo=UTC)
    end = datetime(2025, 3, 30, 2, 0, tzinfo=UTC)
    curve = synthetic_day(day, {
        start.replace(minute=0): Decimal("0.1"),
        boundary: Decimal("0.3"),
    })
    source = distribution(start, end, [
        (start, boundary, 0.5), (boundary, end, 1),
    ])

    quote = temporal_pvpc.quote_charge_intervals(
        iso(start), iso(end), 1.5, source, [curve], calculated_at=FETCHED_AT,
    )

    assert len(curve["intervals"]) == 23
    assert [line["started_at"] for line in quote["pricing"]["intervals"]] == [
        "2025-03-30T00:30:00Z", "2025-03-30T01:00:00Z",
    ]
    assert [line["kwh"] for line in quote["pricing"]["intervals"]] == [0.5, 1.0]
    assert [line["price_eur_kwh"] for line in quote["pricing"]["intervals"]] == [0.1, 0.3]


def test_fall_back_day_prices_both_repeated_madrid_hours_as_distinct_utc_hours():
    day = date(2025, 10, 26)
    start = datetime(2025, 10, 26, 0, 30, tzinfo=UTC)
    boundary = datetime(2025, 10, 26, 1, tzinfo=UTC)
    end = datetime(2025, 10, 26, 1, 30, tzinfo=UTC)
    curve = synthetic_day(day, {
        start.replace(minute=0): Decimal("0.1"),
        boundary: Decimal("0.3"),
    })
    source = distribution(start, end, [(start, end, 1)])

    quote = temporal_pvpc.quote_charge_intervals(
        iso(start), iso(end), 1, source, [curve], calculated_at=FETCHED_AT,
    )

    assert len(curve["intervals"]) == 25
    assert [line["price_eur_kwh"] for line in quote["pricing"]["intervals"]] == [0.1, 0.3]
    assert quote["cost_eur"] == 0.2


@pytest.mark.parametrize(("price", "expected_cost"), [("0.05", 0.01), ("-0.05", -0.01)])
def test_positive_and_negative_exact_half_cent_round_away_from_zero(price, expected_cost):
    start = datetime(2026, 1, 15, 8, tzinfo=UTC)
    end = start + timedelta(hours=1)
    curve = synthetic_day(date(2026, 1, 15), {start: Decimal(price)})
    source = distribution(start, end, [(start, end, Decimal("0.1"))])

    quote = temporal_pvpc.quote_charge_intervals(
        iso(start), iso(end), Decimal("0.1"), source, [curve],
        calculated_at=FETCHED_AT,
    )

    assert quote["cost_eur"] == expected_cost
    restored = temporal_pvpc.validate_temporal_pricing_snapshot(
        quote["pricing"], iso(start), iso(end), Decimal("0.1"), quote["cost_eur"]
    )
    assert restored == quote["pricing"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value, a, b: value["intervals"].__setitem__(1, {
            **value["intervals"][1], "started_at": "2026-01-15T09:01:00Z",
        }),  # gap
        lambda value, a, b: value["intervals"].__setitem__(1, {
            **value["intervals"][1], "started_at": "2026-01-15T08:59:00Z",
        }),  # overlap
        lambda value, a, b: value["intervals"].__setitem__(0, {
            **value["intervals"][0], "started_at": "2026-01-15T07:59:00Z",
        }),  # outside session
        lambda value, a, b: value["intervals"][1].__setitem__("kwh", 0.4),  # sum mismatch
        lambda value, a, b: value.__setitem__("scope", "bad"),
        lambda value, a, b: value.__setitem__("source_id", "medidor-ñ"),
        lambda value, a, b: value.__setitem__("session_id", "!" * 32),
        lambda value, a, b: value["intervals"][0].__setitem__("kwh", True),
        lambda value, a, b: value["intervals"][0].__setitem__("kwh", float("nan")),
        lambda value, a, b: value["intervals"][0].__setitem__("kwh", float("inf")),
        lambda value, a, b: value["intervals"][0].__setitem__(
            "started_at", "2026-01-15T08:00:00"
        ),
        lambda value, a, b: value.__setitem__("basis", "grid_import"),
        lambda value, a, b: value.__setitem__("extra", "field"),
    ],
)
def test_energy_distribution_rejects_gaps_overlaps_identity_and_invalid_numbers(mutate):
    start = datetime(2026, 1, 15, 8, tzinfo=UTC)
    middle = start + timedelta(hours=1)
    end = middle + timedelta(hours=1)
    source = distribution(start, end, [(start, middle, 0.5), (middle, end, 0.5)])
    mutate(source, start, end)

    with pytest.raises(pvpc.PvpcError) as error:
        temporal_pvpc.validate_energy_distribution(source, iso(start), iso(end), 1)
    assert error.value.code == "invalid_pricing"


def test_energy_distribution_checks_expected_identity_duration_and_limits():
    start = datetime(2026, 1, 15, 8, tzinfo=UTC)
    end = start + timedelta(hours=1)
    source = distribution(start, end, [(start, end, 1)])
    with pytest.raises(pvpc.PvpcError):
        temporal_pvpc.validate_energy_distribution(
            source, iso(start), iso(end), 1, expected_scope="c" * 64
        )
    with pytest.raises(pvpc.PvpcError):
        temporal_pvpc.validate_energy_distribution(
            source, iso(start), iso(end), 1, expected_session_id="c" * 32
        )
    with pytest.raises(pvpc.PvpcError):
        temporal_pvpc.validate_energy_distribution(source, iso(start), iso(end), True)
    with pytest.raises(pvpc.PvpcError):
        temporal_pvpc.validate_energy_distribution(source, iso(start), iso(end), 501)
    with pytest.raises(pvpc.PvpcError):
        temporal_pvpc.validate_energy_distribution(
            source, iso(start), iso(end + timedelta(seconds=1)), 1
        )
    with pytest.raises(pvpc.PvpcError):
        temporal_pvpc.validate_energy_distribution(
            source, iso(start), iso(start + timedelta(hours=72, seconds=1)), 1
        )
    too_long_interval = distribution(
        start, start + timedelta(seconds=3601),
        [(start, start + timedelta(seconds=3601), 1)],
    )
    with pytest.raises(pvpc.PvpcError):
        temporal_pvpc.validate_energy_distribution(
            too_long_interval, iso(start), iso(start + timedelta(seconds=3601)), 1
        )

    too_many = deepcopy(source)
    one_second = start
    rows = []
    for index in range(1025):
        left = one_second + timedelta(seconds=index)
        rows.append({
            "started_at": iso(left), "ended_at": iso(left + timedelta(seconds=1)),
            "kwh": 1 / 1025,
        })
    too_many["intervals"] = rows
    with pytest.raises(pvpc.PvpcError):
        temporal_pvpc.validate_energy_distribution(
            too_many, iso(start), iso(start + timedelta(seconds=1025)), 1
        )


def test_scope_contract_and_aware_non_utc_inputs_normalize_for_snapshot_replay():
    start = datetime(2026, 1, 15, 8, tzinfo=UTC)
    end = start + timedelta(hours=1)
    start_offset = "2026-01-15T09:00:00+01:00"
    end_offset = "2026-01-15T10:00:00+01:00"
    source = {
        "version": 1,
        "scope": SCOPE.upper(),
        "session_id": SESSION_ID.upper(),
        "source_id": "synthetic.test",
        "source_version": "v1",
        "basis": "charger_total",
        "intervals": [{
            "started_at": start_offset,
            "ended_at": end_offset,
            "kwh": 1,
        }],
    }
    normalized = temporal_pvpc.validate_energy_distribution(
        source, start_offset, end_offset, 1,
        expected_scope=SCOPE.upper(), expected_session_id=SESSION_ID.upper(),
    )

    assert set(normalized) == {
        "version", "scope", "session_id", "source_id", "source_version", "basis", "intervals",
    }
    assert normalized["scope"] == SCOPE
    assert normalized["session_id"] == SESSION_ID
    assert normalized["intervals"] == [{
        "started_at": iso(start), "ended_at": iso(end), "kwh": 1.0,
    }]

    curve = synthetic_day(date(2026, 1, 15), {start: Decimal("0.2")})
    quote = temporal_pvpc.quote_charge_intervals(
        start_offset, end_offset, 1, source, [curve], calculated_at=FETCHED_AT,
    )
    assert quote["pricing"]["energy_distribution"] == normalized
    assert quote["pricing"]["intervals"][0]["started_at"] == iso(start)
    assert quote["pricing"]["intervals"][0]["ended_at"] == iso(end)

    offset_snapshot = deepcopy(quote["pricing"])
    offset_snapshot["intervals"][0]["started_at"] = start.astimezone(MADRID).isoformat()
    offset_snapshot["intervals"][0]["ended_at"] = end.astimezone(MADRID).isoformat()
    assert temporal_pvpc.validate_temporal_pricing_snapshot(
        offset_snapshot, start_offset, end_offset, 1, quote["cost_eur"]
    ) == quote["pricing"]

    with pytest.raises(pvpc.PvpcError):
        temporal_pvpc.validate_energy_distribution(
            source, "2026-01-15T08:00:00", end_offset, 1
        )


def test_temporal_snapshot_recomputes_energy_cost_and_hourly_consistency():
    start = datetime(2026, 1, 15, 8, tzinfo=UTC)
    split = start + timedelta(minutes=30)
    end = start + timedelta(hours=1)
    curve = synthetic_day(date(2026, 1, 15), {start: Decimal("0.1")})
    source = distribution(start, end, [(start, split, 0.5), (split, end, 0.5)])
    quote = temporal_pvpc.quote_charge_intervals(
        iso(start), iso(end), 1, source, [curve], calculated_at=FETCHED_AT,
    )
    restored = temporal_pvpc.validate_temporal_pricing_snapshot(
        quote["pricing"], iso(start), iso(end), 1, quote["cost_eur"]
    )
    assert restored == quote["pricing"]

    for change in (
        lambda item: item["intervals"][0].__setitem__("kwh", 0.4),
        lambda item: item["intervals"][0].__setitem__("cost_eur", 0.2),
        lambda item: item["intervals"][0].__setitem__("ended_at", "2026-01-15T08:45:00Z"),
        lambda item: item["intervals"][1].__setitem__("price_eur_kwh", 0.2),
        lambda item: item["intervals"][1].__setitem__(
            "price_fetched_at", "2026-10-08T10:00:00Z"
        ),
    ):
        tampered = deepcopy(quote["pricing"])
        change(tampered)
        with pytest.raises(pvpc.PvpcError) as error:
            temporal_pvpc.validate_temporal_pricing_snapshot(
                tampered, iso(start), iso(end), 1, quote["cost_eur"]
            )
        assert error.value.code == "invalid_pricing"

    with pytest.raises(pvpc.PvpcError):
        temporal_pvpc.validate_temporal_pricing_snapshot(
            quote["pricing"], iso(start), iso(end), 1, 0.02
        )


def test_temporal_snapshot_rejects_more_than_2048_lines():
    start = datetime(2026, 1, 15, 8, tzinfo=UTC)
    end = start + timedelta(hours=1)
    curve = synthetic_day(date(2026, 1, 15), {start: Decimal("0.1")})
    source = distribution(start, end, [(start, end, 1)])
    quote = temporal_pvpc.quote_charge_intervals(
        iso(start), iso(end), 1, source, [curve], calculated_at=FETCHED_AT,
    )
    oversized = deepcopy(quote["pricing"])
    oversized["intervals"] *= 2049

    with pytest.raises(pvpc.PvpcError) as error:
        temporal_pvpc.validate_temporal_pricing_snapshot(
            oversized, iso(start), iso(end), 1, quote["cost_eur"]
        )
    assert error.value.code == "invalid_pricing"
