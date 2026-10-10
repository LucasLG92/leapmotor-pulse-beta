"""Pure UTC observation-window coverage calculations for Pulse telemetry."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import math
from typing import Any


_UTC = timezone.utc
_MAX_INTERVAL_SECONDS = 15 * 60
_MAX_ODOMETER_SPEED_KMH = 250


def calculate_observation_coverage(
    snapshots: Sequence[Mapping[str, Any]],
    *,
    window_start: datetime,
    window_end: datetime,
) -> dict[str, Any]:
    """Measure interval coverage without treating receipt time as observation time.

    ``snapshots`` must be in local ingestion order, oldest first. Timestamps
    outside the requested observation window do not contribute points or
    intervals. All future timestamps are counted and excluded, including the
    collector's tolerated clock skew, because this calculation uses a strict
    observation window.
    """
    start = _aware_utc(window_start, "window_start")
    end = _aware_utc(window_end, "window_end")
    if end <= start:
        raise ValueError("window_end debe ser posterior a window_start.")
    if not isinstance(snapshots, Sequence) or isinstance(snapshots, (str, bytes)):
        raise ValueError("snapshots debe ser una secuencia de objetos.")

    sample_count = len(snapshots)
    observed_count = 0
    missing_count = 0
    future_count = 0
    outside_count = 0
    non_monotonic_count = 0
    duplicate_count = 0
    seen_times: set[datetime] = set()
    high_watermark: datetime | None = None
    accepted: list[tuple[datetime, float | None]] = []

    for snapshot in snapshots:
        if not isinstance(snapshot, Mapping):
            missing_count += 1
            continue
        observed = _parse_observed_at(snapshot.get("observed_at"))
        if observed is None:
            missing_count += 1
            continue
        if observed > end:
            future_count += 1
            continue
        if observed < start:
            outside_count += 1
            continue

        observed_count += 1
        duplicate = observed in seen_times
        if duplicate:
            duplicate_count += 1
        seen_times.add(observed)

        non_monotonic = high_watermark is not None and observed < high_watermark
        if non_monotonic:
            non_monotonic_count += 1
        if high_watermark is None or observed > high_watermark:
            high_watermark = observed
        if duplicate or non_monotonic:
            continue
        accepted.append((observed, _valid_odometer(snapshot.get("vehicle"))))

    window_seconds = (end - start).total_seconds()
    intervals: list[tuple[float, float]] = []
    gap_count = 0
    eligible_interval_count = 0
    max_gap_seconds: float | None = None
    odometer_deltas: list[float] = []

    for previous, current in zip(accepted, accepted[1:]):
        previous_at, previous_odometer = previous
        current_at, current_odometer = current
        gap = (current_at - previous_at).total_seconds()
        if gap <= 0:
            # This is defensive; duplicate and non-monotonic samples have
            # already been filtered before constructing intervals.
            continue
        max_gap_seconds = gap if max_gap_seconds is None else max(max_gap_seconds, gap)
        if gap > _MAX_INTERVAL_SECONDS:
            gap_count += 1
            continue
        eligible_interval_count += 1
        left = max(0.0, (previous_at - start).total_seconds())
        right = min(window_seconds, (current_at - start).total_seconds())
        if right > left:
            intervals.append((left, right))

        if previous_odometer is None or current_odometer is None:
            continue
        delta = current_odometer - previous_odometer
        if delta < 0 or delta / gap * 3_600 > _MAX_ODOMETER_SPEED_KMH:
            continue
        odometer_deltas.append(delta)

    covered_seconds = _union_interval_seconds(intervals)
    coverage_percent = (
        round(covered_seconds * 100 / window_seconds, 2) if observed_count else None
    )
    return {
        "sample_count": sample_count,
        "observed_count": observed_count,
        "missing_observation_count": missing_count,
        "future_timestamp_count": future_count,
        "outside_window_count": outside_count,
        "retention_limit": 1_000,
        "non_monotonic_count": non_monotonic_count,
        "duplicate_time_count": duplicate_count,
        "gap_count": gap_count,
        "max_gap_seconds": None if max_gap_seconds is None else round(max_gap_seconds, 3),
        "covered_seconds": round(covered_seconds, 3),
        "coverage_percent": coverage_percent,
        "first_observed_at": _format_utc(accepted[0][0]) if accepted else None,
        "last_observed_at": _format_utc(accepted[-1][0]) if accepted else None,
        "odometer_delta_km": (
            round(math.fsum(odometer_deltas), 3) if odometer_deltas else None
        ),
        "eligible_interval_count": eligible_interval_count,
        "validation": "not_qualified",
    }


def _parse_observed_at(value: Any) -> datetime | None:
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
    return parsed.astimezone(_UTC)


def _aware_utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} debe incluir zona horaria.")
    return value.astimezone(_UTC)


def _valid_odometer(vehicle: Any) -> float | None:
    if not isinstance(vehicle, Mapping):
        return None
    value = vehicle.get("odometer_km")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _union_interval_seconds(intervals: Sequence[tuple[float, float]]) -> float:
    if not intervals:
        return 0.0
    ordered = sorted(intervals)
    total = 0.0
    left, right = ordered[0]
    for next_left, next_right in ordered[1:]:
        if next_left <= right:
            right = max(right, next_right)
        else:
            total += right - left
            left, right = next_left, next_right
    return total + right - left


def _format_utc(value: datetime) -> str:
    return value.astimezone(_UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")

