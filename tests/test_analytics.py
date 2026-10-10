from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from pulse.analytics import calculate_observation_coverage, _union_interval_seconds
from pulse.domain import DomainError, Store


UTC = timezone.utc


def make_store(tmp_path, *, clock=None):
    if clock is None:
        fixed = datetime(2026, 10, 8, 20, 0, tzinfo=UTC)
        clock = lambda: fixed
    return Store(tmp_path / "pulse.sqlite3", clock=clock)


def telemetry(observed_at, odometer_km=None):
    return {
        "observed_at": observed_at,
        "vehicle": {"odometer_km": odometer_km},
    }


def test_energy_analytics_uses_utc_months_and_weighted_tariffs_by_source(tmp_path):
    store = make_store(tmp_path)
    store.update_settings({"tariff_eur_kwh": 0.2})
    september = store.add_charge({"started_at": "2026-10-01T01:00:00+02:00", "kwh": 10})
    store.update_settings({"tariff_eur_kwh": 0.4})
    october = store.add_charge({"started_at": "2026-10-01T03:00:00+02:00", "kwh": 10})
    store.add_charge({"started_at": "2026-10-09T00:00:00Z", "kwh": 40})
    store.add_charge({"started_at": "2026-09-01T00:00:00Z", "kwh": 60})
    store.set_demo(True)

    result = store.energy_analytics(days=30)
    assert result["window_start"] == "2026-09-08T20:00:00.000000Z"
    assert result["window_end"] == "2026-10-08T20:00:00.000000Z"
    assert result["bucket_timezone"] == "UTC"
    assert result["manual"] == {
        "sessions": 2,
        "kwh": 20.0,
        "cost_eur": 6.0,
        "effective_tariff_eur_kwh": 0.3,
    }
    assert result["months"] == [
        {
            "month": "2026-09",
            "manual": {
                "sessions": 1,
                "kwh": 10.0,
                "cost_eur": 2.0,
                "effective_tariff_eur_kwh": 0.2,
            },
            "demo": {
                "sessions": 0,
                "kwh": 0.0,
                "cost_eur": 0.0,
                "effective_tariff_eur_kwh": None,
            },
        },
        {
            "month": "2026-10",
            "manual": {
                "sessions": 1,
                "kwh": 10.0,
                "cost_eur": 4.0,
                "effective_tariff_eur_kwh": 0.4,
            },
            "demo": {
                "sessions": 2,
                "kwh": 44.6,
                "cost_eur": 7.58,
                "effective_tariff_eur_kwh": pytest.approx(7.58 / 44.6),
            },
        },
    ]
    assert result["demo"]["sessions"] == 2
    assert result["demo"]["kwh"] == 44.6
    assert september["started_at"] == "2026-09-30T23:00:00.000000Z"
    assert october["started_at"] == "2026-10-01T01:00:00.000000Z"

    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE app_meta SET demo_enabled = 0 WHERE id = 1")
        assert connection.execute("SELECT COUNT(*) FROM demo_charges").fetchone()[0] == 2
    disabled = store.energy_analytics(days=30)
    assert disabled["demo"] == {
        "sessions": 0,
        "kwh": 0.0,
        "cost_eur": 0.0,
        "effective_tariff_eur_kwh": None,
    }
    assert all(month["demo"]["sessions"] == 0 for month in disabled["months"])


def test_energy_analytics_returns_zero_aggregates_and_unqualified_empty_coverage(tmp_path):
    result = make_store(tmp_path).energy_analytics()
    empty_aggregate = {
        "sessions": 0,
        "kwh": 0.0,
        "cost_eur": 0.0,
        "effective_tariff_eur_kwh": None,
    }
    assert result["manual"] == empty_aggregate
    assert result["demo"] == empty_aggregate
    assert result["months"]
    assert result["observations"] == {
        "sample_count": 0,
        "observed_count": 0,
        "missing_observation_count": 0,
        "future_timestamp_count": 0,
        "outside_window_count": 0,
        "retention_limit": 1_000,
        "non_monotonic_count": 0,
        "duplicate_time_count": 0,
        "gap_count": 0,
        "max_gap_seconds": None,
        "covered_seconds": 0.0,
        "coverage_percent": None,
        "first_observed_at": None,
        "last_observed_at": None,
        "odometer_delta_km": None,
        "eligible_interval_count": 0,
        "validation": "not_qualified",
    }
    assert result["estimates"] == {
        "consumption_kwh_100km": None,
        "reason_code": "energy_not_observed",
    }


def test_coverage_excludes_future_duplicate_and_non_monotonic_points(tmp_path):
    start = datetime(2026, 10, 8, 0, 0, tzinfo=UTC)
    end = start + timedelta(hours=2)
    observations = [
        telemetry(start.isoformat(), 100),
        telemetry((start + timedelta(minutes=5)).isoformat(), 101),
        telemetry((start + timedelta(minutes=5)).isoformat(), 500),
        telemetry((start + timedelta(minutes=4)).isoformat(), 1_000),
        telemetry((start + timedelta(minutes=10)).isoformat(), 102),
        {"observed_at": None, "provider_received_at": end.isoformat()},
        telemetry((end + timedelta(seconds=30)).isoformat(), 200),
        telemetry((start - timedelta(days=1)).isoformat(), 300),
    ]

    result = calculate_observation_coverage(
        observations, window_start=start, window_end=end
    )
    assert result["sample_count"] == 8
    assert result["observed_count"] == 5
    assert result["missing_observation_count"] == 1
    assert result["future_timestamp_count"] == 1
    assert result["outside_window_count"] == 1
    assert result["duplicate_time_count"] == 1
    assert result["non_monotonic_count"] == 1
    assert result["eligible_interval_count"] == 2
    assert result["gap_count"] == 0
    assert result["max_gap_seconds"] == 300
    assert result["covered_seconds"] == 600
    assert result["coverage_percent"] == 8.33
    assert result["first_observed_at"] == "2026-10-08T00:00:00.000000Z"
    assert result["last_observed_at"] == "2026-10-08T00:10:00.000000Z"
    assert result["odometer_delta_km"] == 2
    assert result["validation"] == "not_qualified"


def test_coverage_marks_long_gaps_and_rejects_future_only_data(tmp_path):
    start = datetime(2026, 10, 8, 0, 0, tzinfo=UTC)
    end = start + timedelta(days=1)
    long_gap = calculate_observation_coverage(
        [
            telemetry(start.isoformat(), 10),
            telemetry((start + timedelta(minutes=20)).isoformat(), 11),
        ],
        window_start=start,
        window_end=end,
    )
    assert long_gap["gap_count"] == 1
    assert long_gap["max_gap_seconds"] == 1_200
    assert long_gap["eligible_interval_count"] == 0
    assert long_gap["covered_seconds"] == 0
    assert long_gap["odometer_delta_km"] is None

    boundary = calculate_observation_coverage(
        [
            telemetry(start.isoformat(), 10),
            telemetry((start + timedelta(seconds=900)).isoformat(), 10.1),
        ],
        window_start=start,
        window_end=end,
    )
    assert boundary["eligible_interval_count"] == 1
    assert boundary["gap_count"] == 0
    assert boundary["covered_seconds"] == 900

    future = calculate_observation_coverage(
        [telemetry((end + timedelta(seconds=30)).isoformat(), 11)],
        window_start=start,
        window_end=end,
    )
    assert future["future_timestamp_count"] == 1
    assert future["observed_count"] == 0
    assert future["coverage_percent"] is None


def test_coverage_odometer_reset_jump_and_real_zero_are_distinct():
    start = datetime(2026, 10, 8, 0, 0, tzinfo=UTC)
    end = start + timedelta(hours=1)
    result = calculate_observation_coverage(
        [
            telemetry(start.isoformat(), 100),
            telemetry((start + timedelta(minutes=5)).isoformat(), 103),
            telemetry((start + timedelta(minutes=10)).isoformat(), 90),
            telemetry((start + timedelta(minutes=15)).isoformat(), 500),
            telemetry((start + timedelta(minutes=20)).isoformat(), 500),
        ],
        window_start=start,
        window_end=end,
    )
    assert result["eligible_interval_count"] == 4
    assert result["odometer_delta_km"] == 3

    zero = calculate_observation_coverage(
        [
            telemetry(start.isoformat(), 100),
            telemetry((start + timedelta(minutes=5)).isoformat(), 100),
        ],
        window_start=start,
        window_end=end,
    )
    assert zero["odometer_delta_km"] == 0


def test_interval_union_never_double_counts_overlaps():
    assert _union_interval_seconds([(0, 600), (300, 900), (900, 1_000)]) == 1_000


def test_analytics_preserves_local_ingestion_order_after_store_clock_rollback(tmp_path):
    clock = [datetime(2026, 10, 8, 15, 30, tzinfo=UTC)]
    store = make_store(tmp_path, clock=lambda: clock[0])
    first = _record_snapshot(store, "2026-10-08T15:00:00Z", 10)
    clock[0] = datetime(2026, 10, 8, 15, 10, tzinfo=UTC)
    second = _record_snapshot(store, "2026-10-08T15:05:00Z", 11)

    result = store.energy_analytics(days=1)
    assert second["server_received_at"] < first["server_received_at"]
    assert result["observations"]["sample_count"] == 2
    assert result["observations"]["observed_count"] == 2
    assert result["observations"]["non_monotonic_count"] == 0
    assert result["observations"]["eligible_interval_count"] == 1
    assert result["observations"]["covered_seconds"] == 300
    assert result["observations"]["odometer_delta_km"] == 1


@pytest.mark.parametrize("days", [0, -1, True, 3_651])
def test_energy_analytics_rejects_unbounded_windows(tmp_path, days):
    with pytest.raises(DomainError):
        make_store(tmp_path).energy_analytics(days=days)


def _record_snapshot(store, observed_at, odometer_km):
    return store.record_snapshot(
        {
            "source": "MATE-API B10ReadClient",
            "observed_at": observed_at,
            "provider_received_at": observed_at,
            "vehicle": {
                "battery_pct": 70,
                "range_km": 300,
                "odometer_km": odometer_km,
                "locked": None,
                "climate_on": None,
                "charging": None,
            },
            "field_sources": {
                "battery_pct": "soc_percent",
                "range_km": "expected_range_km",
                "odometer_km": "odometer_km",
                "locked": None,
                "climate_on": None,
                "charging": None,
            },
        }
    )
