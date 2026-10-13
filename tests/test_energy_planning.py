from datetime import datetime

import pytest

from pulse.energy_planning import EnergyPlanningError, compare_manual_charges, plan_charging


def plan_values(**changes):
    values = {
        "energy_kwh": 1,
        "power_kw": 1,
        "efficiency_pct": 100,
        "earliest_at": "2026-01-01T00:00:00Z",
        "deadline_at": "2026-01-01T01:00:00Z",
        "tariffs": [{
            "start_at": "2026-01-01T00:00:00Z",
            "end_at": "2026-01-01T01:00:00Z",
            "price_eur_kwh": 0.2,
        }],
    }
    values.update(changes)
    return values


def test_dst_fall_back_offsets_are_distinct_absolute_intervals():
    result = plan_charging(plan_values(
        energy_kwh=1,
        earliest_at="2026-10-25T01:00:00+02:00",
        deadline_at="2026-10-25T03:00:00+01:00",
        tariffs=[
            {"start_at": "2026-10-25T02:00:00+01:00", "end_at": "2026-10-25T03:00:00+01:00", "price_eur_kwh": 0.2},
            {"start_at": "2026-10-25T02:00:00+02:00", "end_at": "2026-10-25T02:00:00+01:00", "price_eur_kwh": 0.1},
            {"start_at": "2026-10-25T01:00:00+02:00", "end_at": "2026-10-25T02:00:00+02:00", "price_eur_kwh": 0.3},
        ],
    ))

    assert result["coverage"] == {"window_seconds": 10_800, "priced_seconds": 10_800, "complete": True}
    assert result["intervals"] == [{
        "started_at": "2026-10-25T00:00:00Z",
        "ended_at": "2026-10-25T01:00:00Z",
        "tariff_ended_at": "2026-10-25T01:00:00Z",
        "duration_seconds": 3600.0,
        "price_eur_kwh": 0.1,
        "grid_energy_kwh": 1.0,
        "battery_energy_kwh": 1.0,
        "estimated_cost_eur": 0.1,
        "partial": False,
    }]
    assert result["earliest_feasible"]["estimated_cost_eur"] == 0.3
    assert result["estimated_savings_eur"] == pytest.approx(0.2)


def test_half_hour_prices_keep_gaps_and_last_partial_segment():
    result = plan_charging(plan_values(
        energy_kwh=0.75,
        earliest_at="2026-01-01T07:00:00Z",
        deadline_at="2026-01-01T09:00:00Z",
        tariffs=[
            {"start_at": "2026-01-01T08:00:00Z", "end_at": "2026-01-01T08:30:00Z", "price_eur_kwh": 0.1},
            {"start_at": "2026-01-01T07:00:00Z", "end_at": "2026-01-01T07:30:00Z", "price_eur_kwh": 0.4},
        ],
    ))

    assert result["coverage"] == {"window_seconds": 7200, "priced_seconds": 3600, "complete": False}
    assert "tariff_coverage_incomplete" in result["warnings"]
    assert [item["started_at"] for item in result["intervals"]] == [
        "2026-01-01T07:00:00Z", "2026-01-01T08:00:00Z"
    ]
    assert [item["grid_energy_kwh"] for item in result["intervals"]] == [0.25, 0.5]
    assert result["intervals"][0]["partial"] is True
    assert result["satisfied_energy_kwh"] == 0.75
    assert result["estimated_cost_eur"] == pytest.approx(0.15)
    assert result["earliest_feasible"]["estimated_cost_eur"] == pytest.approx(0.225)
    assert result["estimated_savings_eur"] == pytest.approx(0.075)


def test_efficiency_is_an_explicit_estimate_and_grid_energy_covers_battery_target():
    result = plan_charging(plan_values(
        energy_kwh=9,
        power_kw=10,
        efficiency_pct=90,
    ))

    assert result["grid_energy_kwh"] == 10
    assert result["battery_energy_kwh"] == 9
    assert result["estimated_cost_eur"] == 2
    assert "efficiency_is_user_estimate" in result["warnings"]
    assert result["purpose"] == "informational_estimate"


def test_negative_manual_price_is_used_but_unreachable_target_has_no_savings():
    result = plan_charging(plan_values(
        energy_kwh=1,
        power_kw=0.5,
        tariffs=[{
            "start_at": "2026-01-01T00:00:00Z",
            "end_at": "2026-01-01T01:00:00Z",
            "price_eur_kwh": -0.2,
        }],
    ))

    assert result["estimated_cost_eur"] == pytest.approx(-0.1)
    assert result["grid_energy_kwh"] == 0.5
    assert result["battery_energy_kwh"] == 0.5
    assert result["unsatisfied_energy_kwh"] == 0.5
    assert result["estimated_savings_eur"] is None
    assert "requested_energy_unreachable" in result["warnings"]


def test_missing_tariffs_do_not_create_priced_intervals_or_claim_full_coverage():
    result = plan_charging(plan_values(tariffs=[]))

    assert result["intervals"] == []
    assert result["grid_energy_kwh"] == 0
    assert result["estimated_cost_eur"] == 0
    assert result["coverage"]["complete"] is False
    assert result["estimated_savings_eur"] is None
    assert "tariff_coverage_incomplete" in result["warnings"]


@pytest.mark.parametrize("changes", [
    {"energy_kwh": float("nan")},
    {"power_kw": float("inf")},
    {"efficiency_pct": 49.9},
    {"power_kw": 351},
])
def test_plan_rejects_non_finite_and_out_of_range_numbers(changes):
    with pytest.raises(EnergyPlanningError):
        plan_charging(plan_values(**changes))


def test_plan_requires_explicit_timezones_and_positive_bounded_window():
    with pytest.raises(EnergyPlanningError, match="invalid_timestamp"):
        plan_charging(plan_values(earliest_at="2026-01-01T00:00:00"))
    with pytest.raises(EnergyPlanningError, match="invalid_window"):
        plan_charging(plan_values(
            earliest_at="2026-01-01T00:00:00Z",
            deadline_at="2026-01-01T00:00:00Z",
        ))
    with pytest.raises(EnergyPlanningError, match="invalid_window"):
        plan_charging(plan_values(deadline_at="2026-01-09T00:00:00Z"))


def test_plan_rejects_overlapping_and_overlong_tariff_intervals():
    overlapping = [
        {"start_at": "2026-01-01T00:00:00Z", "end_at": "2026-01-01T00:45:00Z", "price_eur_kwh": 0.1},
        {"start_at": "2026-01-01T00:30:00Z", "end_at": "2026-01-01T01:00:00Z", "price_eur_kwh": 0.2},
    ]
    with pytest.raises(EnergyPlanningError, match="overlapping_tariffs"):
        plan_charging(plan_values(tariffs=overlapping))
    overlong = [{
        "start_at": "2026-01-01T00:00:00Z",
        "end_at": "2026-01-01T01:00:00.000001Z",
        "price_eur_kwh": 0.1,
    }]
    with pytest.raises(EnergyPlanningError, match="invalid_tariff"):
        plan_charging(plan_values(tariffs=overlong))


def test_manual_comparison_groups_by_month_and_explicit_or_unknown_location():
    rows = [
        {"source": "manual", "started_at": "2026-10-02T10:00:00Z", "kwh": 10, "cost_eur": 2},
        {"source": "manual", "started_at": "2026-10-03T10:00:00Z", "kwh": 30, "cost_eur": 12, "location": " Casa  "},
        {"source": "manual", "started_at": "2026-10-04T10:00:00Z", "kwh": None, "cost_eur": 1},
        {"source": "manual", "started_at": "2026-10-05T10:00:00Z", "kwh": 0.5, "cost_eur": None},
        {"source": "demo", "started_at": "2026-10-05T10:00:00Z", "kwh": float("nan"), "cost_eur": 9999},
        {"started_at": "2026-09-30T23:30:00-02:00", "kwh": 2, "cost_eur": -0.2},
    ]

    result = compare_manual_charges(
        rows, 30, datetime.fromisoformat("2026-10-13T00:00:00+00:00")
    )
    totals = result["totals"]
    october = result["months"][0]

    assert result["basis"] == "manual_entries_only"
    assert result["excluded_non_manual_count"] == 1
    assert result["window_start"] == "2026-09-13T00:00:00Z"
    assert totals["charge_count"] == 5
    assert totals["kwh"] == 42.5
    assert totals["cost_eur"] == 14.8
    assert totals["paired_kwh"] == 42
    assert totals["paired_cost_eur"] == 13.8
    assert totals["effective_cost_eur_kwh"] == pytest.approx(13.8 / 42)
    assert totals["missing_kwh_count"] == 1
    assert totals["missing_cost_count"] == 1
    assert october["month"] == "2026-10"
    assert october["charge_count"] == 5
    assert [group["location"] for group in october["locations"]] == [None, "Casa"]
    assert october["locations"][0]["charge_count"] == 4


def test_manual_comparison_preserves_unknowns_and_validates_bounds():
    result = compare_manual_charges(
        [{"started_at": "2026-10-10T00:00:00Z", "kwh": None, "cost_eur": None}],
        30,
        datetime.fromisoformat("2026-10-13T00:00:00+00:00"),
    )
    assert result["totals"]["kwh"] is None
    assert result["totals"]["cost_eur"] is None
    assert result["totals"]["effective_cost_eur_kwh"] is None
    assert result["totals"]["missing_kwh_count"] == 1
    assert result["totals"]["missing_cost_count"] == 1

    with pytest.raises(EnergyPlanningError):
        compare_manual_charges([], 0, datetime.now().astimezone())
    with pytest.raises(EnergyPlanningError):
        compare_manual_charges([], 30, datetime(2026, 10, 13))
    with pytest.raises(EnergyPlanningError):
        compare_manual_charges([{}] * 10_001, 30, datetime.now().astimezone())


def test_partial_plan_exposes_useful_end_not_full_price_window():
    result = plan_charging(plan_values(energy_kwh=1, power_kw=10, efficiency_pct=100))
    interval = result['intervals'][0]
    assert interval['partial'] is True
    assert interval['duration_seconds'] == 360
    from datetime import datetime
    assert (datetime.fromisoformat(interval['ended_at']) - datetime.fromisoformat(interval['started_at'])).total_seconds() == 360
    assert interval['ended_at'] < interval['tariff_ended_at']
