"""Selección/privacidad y caducidad usando hora observada, no la recepción."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from pulse.native_snapshot import widget_snapshot

NOW = datetime(2026, 10, 8, 10, tzinfo=timezone.utc)


def context(fields=None):
    return {"mode": "unconfigured", "demo_vehicle": None,
            "profile": {"id": "fixture", "name": "Batería", "revision": 2,
                        "fields": fields or ["battery_pct", "charging"], "appearance": "system",
                        "privacy_mode": "visible", "max_age_minutes": 60},
            "telemetry": {"observed_at": (NOW - timedelta(minutes=2)).isoformat(),
                          "server_received_at": NOW.isoformat(),
                          "vehicle": {"battery_pct": 71.2, "charging": False, "range_km": 200,
                                      "locked": True, "location": "private-fixture"}}}


def energy_context(*, calculated_at=NOW, window_start=None, sessions=3,
                   kwh=23.457, cost_eur=-4.25, privacy_mode="visible",
                   max_age_minutes=60):
    window_start = window_start or calculated_at - timedelta(days=30)
    return {
        "profile": {
            "id": "fixture", "name": "Energía", "revision": 2,
            "fields": ["energy_kwh", "energy_cost_eur"], "appearance": "system",
            "privacy_mode": privacy_mode, "max_age_minutes": max_age_minutes,
        },
        "energy": {
            "calculated_at": calculated_at.isoformat(),
            "window_start": window_start.isoformat(),
            "window_end": calculated_at.isoformat(),
            "sessions": sessions, "kwh": kwh, "cost_eur": cost_eur,
        },
    }


def test_selected_only_no_identity_location_or_physical_freshness_claim():
    snapshot = widget_snapshot(context(), now=NOW)
    assert snapshot["values"] == {"battery_pct": 71.2, "charging": False}
    assert snapshot["physical_freshness"] == "unknown"
    assert snapshot["temporal_status"] == "fresh"
    assert snapshot["source"] == "telemetry"
    assert snapshot["schema_version"] == 2
    assert snapshot["kind"] == "vehicle" and snapshot["energy_window"] is None
    assert "private-fixture" not in str(snapshot)
    assert snapshot["valid_until"] == "2026-10-08T10:58:00Z"


@pytest.mark.parametrize("observed", [None, "bad-date", "2026-10-08T10:01:01Z",
                                     "2026-10-08T09:00:00Z", "2026-10-08T08:59:59Z"])
def test_missing_future_and_exact_expiry_hide_values_despite_recent_receipt(observed):
    value = context()
    value["telemetry"]["observed_at"] = observed
    snapshot = widget_snapshot(value, now=NOW)
    assert snapshot["values"] == {"battery_pct": None, "charging": None}
    assert snapshot["valid_until"] is None
    assert snapshot["temporal_status"] in {"unknown", "unavailable"}


def test_masked_by_server_and_future_tolerance_never_extends_ttl():
    value = context()
    value["profile"]["privacy_mode"] = "masked"
    assert all(field is None for field in widget_snapshot(value, now=NOW)["values"].values())
    value["profile"]["privacy_mode"] = "visible"
    value["telemetry"]["observed_at"] = (NOW + timedelta(seconds=30)).isoformat()
    assert widget_snapshot(value, now=NOW)["valid_until"] == "2026-10-08T11:00:00Z"


def test_demo_explicit_no_telemetry_fallback_and_values_sanitized():
    value = context()
    value["mode"] = "demo"
    value["demo_vehicle"] = {"observed_at": NOW.isoformat(), "battery_pct": True, "charging": 1}
    snapshot = widget_snapshot(value, now=NOW)
    assert snapshot["source"] == "demo" and snapshot["server_received_at"] is None
    assert snapshot["values"] == {"battery_pct": None, "charging": None}
    value["demo_vehicle"] = None
    assert all(field is None for field in widget_snapshot(value, now=NOW)["values"].values())


def test_unavailable_and_no_mutation_of_context():
    value = context()
    value["telemetry"] = None
    before = deepcopy(value)
    snapshot = widget_snapshot(value, now=NOW)
    assert snapshot["source"] == "unavailable" and snapshot["valid_until"] is None
    assert value == before
    with pytest.raises(ValueError):
        widget_snapshot(value, now=NOW.replace(tzinfo=None))


def test_locked_is_selected_as_a_strict_vehicle_boolean():
    value = context(["locked"])
    snapshot = widget_snapshot(value, now=NOW)
    assert snapshot["values"] == {"locked": True}
    value["telemetry"]["vehicle"]["locked"] = 1
    assert widget_snapshot(value, now=NOW)["values"] == {"locked": None}


def test_energy_snapshot_uses_manual_source_and_coherent_utc_window():
    snapshot = widget_snapshot(energy_context(), now=NOW)
    assert snapshot["schema_version"] == 2
    assert snapshot["kind"] == "energy"
    assert snapshot["source"] == "manual_energy"
    assert snapshot["energy_window"] == {
        "started_at": "2026-09-08T10:00:00Z",
        "ended_at": "2026-10-08T10:00:00Z",
    }
    assert snapshot["observed_at"] == snapshot["server_received_at"] == snapshot["generated_at"]
    assert snapshot["temporal_status"] == "fresh"
    assert snapshot["reason_code"] == "energy_calculated"
    assert snapshot["valid_until"] == "2026-10-08T11:00:00Z"
    assert snapshot["values"] == {"energy_kwh": 23.457, "energy_cost_eur": -4.25}


def test_energy_rounds_are_domain_owned_and_masking_keeps_values_hidden():
    value = energy_context(privacy_mode="masked", max_age_minutes=120)
    snapshot = widget_snapshot(value, now=NOW)
    assert snapshot["temporal_status"] == "fresh"
    assert snapshot["valid_until"] == "2026-10-08T12:00:00Z"
    assert snapshot["values"] == {"energy_kwh": None, "energy_cost_eur": None}


@pytest.mark.parametrize(
    "changes, reason",
    [
        ({"sessions": 0}, "no_manual_sessions"),
        ({"kwh": True}, "energy_aggregate_invalid"),
        ({"kwh": 1_000_000_001}, "energy_aggregate_invalid"),
        ({"cost_eur": False}, "energy_aggregate_invalid"),
        ({"cost_eur": -1_000_000_001}, "energy_aggregate_invalid"),
    ],
)
def test_empty_or_invalid_energy_aggregate_is_unavailable(changes, reason):
    value = energy_context()
    value["energy"].update(changes)
    snapshot = widget_snapshot(value, now=NOW)
    assert snapshot["temporal_status"] == "unavailable"
    assert snapshot["reason_code"] == reason
    assert snapshot["valid_until"] is None
    assert snapshot["values"] == {"energy_kwh": None, "energy_cost_eur": None}


@pytest.mark.parametrize(
    "calculated_at, window_start",
    [
        (NOW - timedelta(seconds=61), NOW - timedelta(days=30, seconds=61)),
        (NOW + timedelta(seconds=2), NOW - timedelta(days=30) + timedelta(seconds=2)),
        (NOW, NOW - timedelta(days=30, seconds=2)),
    ],
)
def test_energy_window_rejects_stale_future_and_wrong_duration(calculated_at, window_start):
    snapshot = widget_snapshot(
        energy_context(calculated_at=calculated_at, window_start=window_start), now=NOW
    )
    assert snapshot["temporal_status"] == "unavailable"
    assert snapshot["reason_code"] == "energy_window_invalid"
    assert snapshot["valid_until"] is None
    assert all(value is None for value in snapshot["values"].values())


def test_energy_window_accepts_contract_clock_tolerances():
    calculation = NOW + timedelta(seconds=1)
    value = energy_context(
        calculated_at=calculation,
        window_start=calculation - timedelta(days=30) + timedelta(seconds=1),
    )
    snapshot = widget_snapshot(value, now=NOW)
    assert snapshot["temporal_status"] == "fresh"
    assert snapshot["reason_code"] == "energy_calculated"
    assert snapshot["valid_until"] == "2026-10-08T11:00:00Z"
