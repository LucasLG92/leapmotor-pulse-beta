from datetime import datetime, timedelta, timezone
import math

import pytest

from pulse.abrp_policy import (
    AbrpConfig,
    attempt_cooldown_active,
    inspect_observation,
    observation_attempt_key,
)


UTC = timezone.utc
NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


def snapshot(**updates):
    value = {
        "id": "a" * 32,
        "source": "MATE-API B10ReadClient",
        "observed_at": NOW.isoformat(),
        "vehicle": {"battery_pct": 72.5},
        "field_sources": {"battery_pct": "precise_soc_percent"},
    }
    value.update(updates)
    return value


def test_config_is_opt_in_strict_and_hides_credentials_from_repr():
    disabled = AbrpConfig()
    assert disabled.enabled is False
    assert disabled.poll_seconds == 60
    assert disabled.send_interval_seconds == 300
    assert disabled.max_age_seconds == 300

    enabled = AbrpConfig(enabled=True, api_key="synthetic-key", user_token="synthetic-token")
    assert "synthetic-key" not in repr(enabled)
    assert "synthetic-token" not in repr(enabled)
    assert len(enabled.scope) == 64
    assert enabled.scope != AbrpConfig(
        enabled=True, api_key="synthetic-key", user_token="different-token"
    ).scope
    assert enabled.scope != AbrpConfig(
        enabled=True, api_key="different-key", user_token="synthetic-token"
    ).scope
    assert enabled.scope == AbrpConfig(
        enabled=True,
        api_key="synthetic-key",
        user_token="synthetic-token",
        poll_seconds=30,
        send_interval_seconds=600,
        max_age_seconds=299,
    ).scope

    with pytest.raises(ValueError):
        AbrpConfig(enabled="true")
    with pytest.raises(ValueError):
        AbrpConfig(enabled=False, api_key=[])
    with pytest.raises(ValueError):
        AbrpConfig(enabled=True, api_key="synthetic key", user_token="synthetic-token")
    with pytest.raises(ValueError):
        AbrpConfig(enabled=True, api_key="synthetic-key", user_token="tøken")
    with pytest.raises(ValueError):
        AbrpConfig(enabled=True, api_key="synthetic-key", user_token="synthetic-token",
                   poll_seconds=True)


def test_from_env_uses_only_supplied_mapping_and_rejects_invalid_values():
    config = AbrpConfig.from_env({
        "PULSE_ABRP_ENABLED": "true",
        "PULSE_ABRP_API_KEY": "synthetic-key",
        "PULSE_ABRP_USER_TOKEN": "synthetic-token",
        "PULSE_ABRP_POLL_SECONDS": "12",
        "PULSE_ABRP_SEND_INTERVAL_SECONDS": "600",
        "PULSE_ABRP_MAX_AGE_SECONDS": "90",
    })
    assert config.enabled is True
    assert (config.poll_seconds, config.send_interval_seconds, config.max_age_seconds) == (12, 600, 90)
    assert AbrpConfig.from_env({}).enabled is False

    for env in (
        {"PULSE_ABRP_ENABLED": "yes"},
        {"PULSE_ABRP_ENABLED": []},
        {"PULSE_ABRP_POLL_SECONDS": " 2"},
        {"PULSE_ABRP_SEND_INTERVAL_SECONDS": "60.0"},
        {"PULSE_ABRP_MAX_AGE_SECONDS": "301"},
    ):
        with pytest.raises(ValueError):
            AbrpConfig.from_env(env)
    with pytest.raises(ValueError):
        AbrpConfig.from_env([("PULSE_ABRP_ENABLED", "false")])


@pytest.mark.parametrize("soc", [0, 100, 0.0, 72.5])
def test_observation_accepts_only_recent_precise_live_battery(soc):
    source = snapshot()
    source["vehicle"]["battery_pct"] = soc
    observation, reason = inspect_observation(source, NOW, 300)
    assert reason == ""
    assert observation is not None
    assert observation.snapshot_id == "a" * 32
    assert observation.utc_seconds == int(NOW.timestamp())
    assert observation.soc_percent == float(soc)


@pytest.mark.parametrize(
    "updates",
    [
        {"source": "configured_provider"},
        {"field_sources": {"battery_pct": "approximate_soc"}},
        {"vehicle": {"battery_pct": True}},
        {"vehicle": {"battery_pct": math.nan}},
        {"vehicle": {"battery_pct": math.inf}},
        {"vehicle": {"battery_pct": -0.01}},
        {"vehicle": {"battery_pct": 100.01}},
        {"observed_at": (NOW + timedelta(seconds=1)).isoformat()},
        {"observed_at": NOW.replace(tzinfo=None).isoformat()},
        {"id": ""},
    ],
)
def test_invalid_or_non_live_observations_are_rejected(updates):
    source = snapshot(**updates)
    observation, reason = inspect_observation(source, NOW, 300)
    assert observation is None
    assert reason == "observation_invalid"


def test_staleness_timezone_and_deduplication_are_canonical():
    stale = snapshot(observed_at=(NOW - timedelta(seconds=301)).isoformat())
    observation, reason = inspect_observation(stale, NOW, 300)
    assert observation is None
    assert reason == "observation_stale"

    offset = NOW.astimezone(timezone(timedelta(hours=2))).isoformat()
    observed, reason = inspect_observation(snapshot(observed_at=offset), NOW, 300)
    assert reason == ""
    assert observed is not None
    assert observed.utc_seconds == int(NOW.timestamp())
    assert observation_attempt_key(observed) == observation_attempt_key(observed)

    changed_soc = snapshot()
    changed_soc["vehicle"]["battery_pct"] = 72.6
    changed, _ = inspect_observation(changed_soc, NOW, 300)
    assert changed is not None
    assert observation_attempt_key(changed) != observation_attempt_key(observed)


def test_cooldown_fails_closed_on_clock_rewind_and_malformed_time():
    assert not attempt_cooldown_active(None, NOW, 300)
    assert attempt_cooldown_active(NOW.isoformat(), NOW + timedelta(seconds=299), 300)
    assert not attempt_cooldown_active(NOW.isoformat(), NOW + timedelta(seconds=300), 300)
    assert attempt_cooldown_active(NOW.isoformat(), NOW - timedelta(seconds=1), 300)
    assert attempt_cooldown_active("not-a-time", NOW, 300)
