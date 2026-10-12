from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime
import json

import pytest

from pulse.contracts import MqttStatus
from pulse.mqtt_policy import (
    MqttConfig,
    discovery_messages,
    discovery_tombstones,
    prepare_publication,
)

METRIC_VALUES = {
    "interior_temperature_c": 22.5,
    "battery_min_temperature_c": -8.25,
    "front_left_pressure_kpa": 245.0,
    "front_right_pressure_kpa": 246.0,
    "rear_left_pressure_kpa": 247.0,
    "rear_right_pressure_kpa": 248.0,
}
ALL_MQTT_FIELDS = ("battery_pct", "range_km", "charging", *METRIC_VALUES)


def enabled_config(**overrides) -> MqttConfig:
    values = {
        "enabled": True,
        "host": "broker",
        "instance": "garage-1",
        "fields": ("battery_pct", "range_km", "charging"),
    }
    values.update(overrides)
    return MqttConfig(**values)


def telemetry_context(observed_at: str, vehicle: dict) -> dict:
    return {
        "demo_enabled": False,
        "snapshot": {
            "id": "private-snapshot-id",
            "source": "private-provider-name",
            "observed_at": observed_at,
            "vehicle": vehicle,
            "field_sources": {"battery_pct": "private-source-detail"},
        },
    }


def test_configuration_defaults_repr_and_explicit_environment_parsing():
    defaults = MqttConfig()
    assert defaults.enabled is False
    assert defaults.host == ""
    assert defaults.port == 8883
    assert defaults.username == defaults.password == ""
    assert defaults.tls is True
    assert defaults.ca_file == ""
    assert defaults.discovery_prefix == "homeassistant"
    assert defaults.fields == ()
    assert defaults.max_age_seconds == 900
    assert defaults.poll_seconds == 15
    with pytest.raises(FrozenInstanceError):
        defaults.enabled = True

    secret_env = {
        "PULSE_MQTT_ENABLED": "true",
        "PULSE_MQTT_HOST": "broker.lan",
        "PULSE_MQTT_PORT": "8883",
        "PULSE_MQTT_USERNAME": "private-user",
        "PULSE_MQTT_PASSWORD": "private-password",
        "PULSE_MQTT_INSTANCE": "house-2",
        "PULSE_MQTT_FIELDS": " battery_pct, charging, interior_temperature_c ",
        "PULSE_MQTT_MAX_AGE_SECONDS": "30",
        "PULSE_MQTT_POLL_SECONDS": "1",
        "PULSE_MQTT_UNRECOGNIZED_TOKEN": "must-not-be-read",
        "OTHER_SECRET": "also-private",
    }
    config = MqttConfig.from_env(secret_env)
    assert config.fields == ("battery_pct", "charging", "interior_temperature_c")
    assert config.max_age_seconds == 30
    assert config.poll_seconds == 1
    assert "private-user" not in repr(config)
    assert "private-password" not in repr(config)
    assert "broker.lan" not in repr(config)
    assert "must-not-be-read" not in repr(config)
    assert "also-private" not in repr(config)
    assert config.topic_prefix == "pulse/house-2"
    assert config.client_id == "pulse-house-2"
    assert config.bridge_availability_topic == "pulse/house-2/availability/bridge"
    assert config.birth_topic == "homeassistant/status"


def test_mqtt_status_contract_accepts_expanded_operator_selection():
    status = MqttStatus(
        enabled=True,
        status="connected",
        data_status="published",
        fields=list(ALL_MQTT_FIELDS),
        max_age_seconds=900,
        observed_at="2026-10-08T10:00:00Z",
        last_published_at="2026-10-08T10:00:00Z",
        reason_code="published",
    )
    assert status.fields == list(ALL_MQTT_FIELDS)
    disabled = MqttStatus(
        enabled=False,
        status="disabled",
        data_status="no_data",
        fields=[],
        max_age_seconds=900,
        observed_at=None,
        last_published_at=None,
        reason_code="disabled",
    )
    assert disabled.fields == []


@pytest.mark.parametrize(
    "env, hidden_value",
    [
        ({"PULSE_MQTT_ENABLED": "yes"}, "yes"),
        ({"PULSE_MQTT_TLS": "1"}, "1"),
        ({"PULSE_MQTT_PORT": "65536"}, "65536"),
        ({"PULSE_MQTT_POLL_SECONDS": "0"}, "0"),
        ({"PULSE_MQTT_MAX_AGE_SECONDS": "7201"}, "7201"),
        ({"PULSE_MQTT_FIELDS": "battery_pct,private_field"}, "private_field"),
        ({"PULSE_MQTT_FIELDS": "battery_pct,,charging"}, "charging"),
    ],
)
def test_environment_rejects_invalid_values_without_echoing_them(env, hidden_value):
    with pytest.raises(ValueError) as error:
        MqttConfig.from_env(env)
    assert hidden_value not in str(error.value)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"port": True},
        {"poll_seconds": False},
        {"enabled": 1},
        {"fields": ["battery_pct"]},
        {"host": "mqtt://broker.local"},
        {"host": "broker.local/path"},
        {"host": "user@broker.local"},
        {"host": "broker*.local"},
        {"fields": ("battery_pct", "battery_pct")},
        {"discovery_prefix": "homeassistant/nested"},
        {"discovery_prefix": "pulse"},
        {"instance": "House 1"},
        {"password": "private-password"},
    ],
)
def test_configuration_rejects_invalid_types_names_and_private_endpoint_forms(kwargs):
    with pytest.raises(ValueError) as error:
        MqttConfig(**kwargs)
    assert "private-password" not in str(error.value)


@pytest.mark.parametrize("host", ["broker.example", "localhost.localdomain", "192.168.1.20", "2001:db8::1"])
def test_tls_plaintext_requires_an_explicit_loopback_destination(host):
    with pytest.raises(ValueError):
        MqttConfig(tls=False, host=host)
    assert MqttConfig(tls=False, host="localhost").tls is False
    assert MqttConfig(tls=False, host="127.0.0.1").tls is False
    assert MqttConfig(tls=False, host="::1").tls is False


def test_enabled_configuration_requires_host_instance_and_selected_fields():
    for kwargs in (
        {"enabled": True, "host": "broker", "instance": "garage"},
        {"enabled": True, "instance": "garage", "fields": ("battery_pct",)},
        {"enabled": True, "host": "broker", "fields": ("battery_pct",)},
    ):
        with pytest.raises(ValueError):
            MqttConfig(**kwargs)


def test_publication_selects_only_current_whitelisted_telemetry_and_hides_provider_metadata():
    config = enabled_config(fields=("battery_pct", "charging"))
    context = telemetry_context(
        "2026-10-08T10:00:00Z",
        {
            "battery_pct": 72.5,
            "range_km": 290,
            "charging": False,
            "odometer_km": 840,
            "vehicle_name": "private-vehicle-name",
        },
    )
    result = prepare_publication(config, context, datetime.fromisoformat("2026-10-08T10:01:00+00:00"))
    assert result == {
        "data_status": "ready",
        "observed_at": "2026-10-08T10:00:00Z",
        "remaining_seconds": 840,
        "values": {"battery_pct": 72.5, "charging": False},
        "source": "telemetry",
    }
    rendered = json.dumps(result)
    for private_value in ("private-snapshot-id", "private-provider-name", "private-source-detail", "private-vehicle-name"):
        assert private_value not in rendered


def test_disabled_and_demo_modes_never_return_values_or_demo_timestamps():
    context = telemetry_context("2026-10-08T10:00:00Z", {"battery_pct": 99, "charging": True})
    disabled = prepare_publication(MqttConfig(), context, datetime.fromisoformat("2026-10-08T10:01:00+00:00"))
    assert disabled == {
        "data_status": "no_data",
        "observed_at": None,
        "remaining_seconds": 0,
        "values": {},
        "source": "unavailable",
    }

    demo_context = {"demo_enabled": True, "snapshot": context["snapshot"]}
    demo = prepare_publication(enabled_config(), demo_context, datetime.fromisoformat("2026-10-08T10:01:00+00:00"))
    assert demo["data_status"] == "demo"
    assert demo["values"] == {}
    assert demo["observed_at"] is None
    assert demo["source"] == "unavailable"
    assert discovery_messages(MqttConfig(), 10) == []
    assert discovery_tombstones(MqttConfig()) == []


def test_numeric_boundaries_are_allowed_but_invalid_or_boolean_numbers_are_unavailable():
    config = enabled_config()
    now = datetime.fromisoformat("2026-10-08T10:00:30+00:00")
    valid = prepare_publication(
        config,
        telemetry_context("2026-10-08T10:00:00Z", {"battery_pct": 0, "range_km": 5000, "charging": True}),
        now,
    )
    assert valid["data_status"] == "ready"
    assert valid["values"] == {"battery_pct": 0, "range_km": 5000, "charging": True}

    invalid = prepare_publication(
        config,
        telemetry_context(
            "2026-10-08T10:00:00Z",
            {"battery_pct": True, "range_km": float("nan"), "charging": 1},
        ),
        now,
    )
    assert invalid["data_status"] == "unavailable"
    assert invalid["values"] == {}
    assert invalid["source"] == "unavailable"


@pytest.mark.parametrize(
    "field_name, minimum, maximum",
    [
        ("interior_temperature_c", -100, 100),
        ("battery_min_temperature_c", -100, 150),
        ("front_left_pressure_kpa", 0, 1000),
        ("front_right_pressure_kpa", 0, 1000),
        ("rear_left_pressure_kpa", 0, 1000),
        ("rear_right_pressure_kpa", 0, 1000),
    ],
)
def test_added_numeric_fields_accept_bounds_and_reject_invalid_values(field_name, minimum, maximum):
    config = enabled_config(fields=(field_name,))
    now = datetime.fromisoformat("2026-10-08T10:00:30+00:00")

    for boundary in (minimum, maximum):
        result = prepare_publication(
            config,
            telemetry_context("2026-10-08T10:00:00Z", {field_name: boundary}),
            now,
        )
        assert result["values"] == {field_name: boundary}

    invalid_values = (True, "12.5", float("nan"), float("inf"), minimum - 0.01, maximum + 0.01)
    for invalid_value in invalid_values:
        result = prepare_publication(
            config,
            telemetry_context("2026-10-08T10:00:00Z", {field_name: invalid_value}),
            now,
        )
        assert result["data_status"] == "unavailable"
        assert result["values"] == {}


def test_selected_new_fields_publish_only_valid_available_subset():
    config = enabled_config(fields=("interior_temperature_c", "battery_min_temperature_c", "front_left_pressure_kpa"))
    vehicle = {
        "interior_temperature_c": -4.5,
        "battery_min_temperature_c": float("nan"),
        "front_left_pressure_kpa": 243,
        "rear_left_pressure_kpa": 888,
    }
    result = prepare_publication(
        config,
        telemetry_context("2026-10-08T10:00:00Z", vehicle),
        datetime.fromisoformat("2026-10-08T10:00:30+00:00"),
    )
    assert result["data_status"] == "ready"
    assert result["values"] == {"interior_temperature_c": -4.5, "front_left_pressure_kpa": 243}


def test_future_expired_and_dst_transition_timestamps_use_actual_utc_elapsed_time():
    config = enabled_config(max_age_seconds=1800, fields=("battery_pct",))
    dst_now = datetime.fromisoformat("2026-03-29T01:10:00.000001+00:00")
    across_dst = prepare_publication(
        config,
        telemetry_context("2026-03-29T01:50:00+01:00", {"battery_pct": 50}),
        dst_now,
    )
    assert across_dst["data_status"] == "ready"
    assert across_dst["remaining_seconds"] == 599
    assert across_dst["observed_at"] == "2026-03-29T00:50:00Z"

    future = prepare_publication(
        config,
        telemetry_context("2026-03-29T01:10:01+00:00", {"battery_pct": 50}),
        dst_now,
    )
    assert future["data_status"] == "unavailable"
    assert future["values"] == {}

    expired = prepare_publication(
        enabled_config(max_age_seconds=900, fields=("battery_pct",)),
        telemetry_context("2026-10-08T10:00:00Z", {"battery_pct": 50}),
        datetime.fromisoformat("2026-10-08T10:15:00+00:00"),
    )
    assert expired["data_status"] == "unavailable"
    assert expired["remaining_seconds"] == 0
    assert expired["values"] == {}


def test_discovery_is_namespaced_selected_and_contains_no_credentials_or_commands():
    config = enabled_config(
        username="private-user",
        password="private-password",
        fields=("battery_pct", "range_km", "charging"),
    )
    messages = discovery_messages(config, 0)
    assert [topic for topic, _ in messages] == [
        "homeassistant/sensor/pulse_garage-1/battery_pct/config",
        "homeassistant/sensor/pulse_garage-1/range_km/config",
        "homeassistant/sensor/pulse_garage-1/charging/config",
    ]
    payloads = [json.loads(payload) for _, payload in messages]
    battery, distance, charging = payloads
    assert battery["expire_after"] == distance["expire_after"] == charging["expire_after"] == 1
    assert battery["device_class"] == "battery"
    assert battery["state_class"] == "measurement" and battery["unit_of_measurement"] == "%"
    assert distance["device_class"] == "distance"
    assert distance["state_class"] == "measurement" and distance["unit_of_measurement"] == "km"
    assert charging["device_class"] == "enum"
    assert charging["options"] == ["charging", "not_charging"]
    for field_name, payload in zip(("battery_pct", "range_km", "charging"), payloads, strict=True):
        assert payload["unique_id"] == f"pulse_garage-1_{field_name}"
        assert payload["device"] == {
            "identifiers": ["pulse_garage-1"],
            "name": "Leapmotor Pulse",
            "manufacturer": "Pulse",
        }
        assert payload["state_topic"] == f"pulse/garage-1/state/{field_name}"
        assert payload["value_template"] == "{{ value_json.value }}"
        assert payload["availability"] == [
            {"topic": "pulse/garage-1/availability/bridge"},
            {"topic": f"pulse/garage-1/availability/{field_name}"},
        ]
        assert payload["availability_mode"] == "all"
        assert not any("command" in key for key in payload)

    rendered = "".join(payload for _, payload in messages)
    assert "private-user" not in rendered
    assert "private-password" not in rendered


def test_added_metric_discovery_uses_home_assistant_units_and_measurement_state_class():
    fields = tuple(METRIC_VALUES)
    config = enabled_config(fields=fields)
    messages = discovery_messages(config, 321)
    payloads = {
        topic.rsplit("/", 2)[-2]: json.loads(payload)
        for topic, payload in messages
    }
    assert set(payloads) == set(fields)

    for field_name in ("interior_temperature_c", "battery_min_temperature_c"):
        payload = payloads[field_name]
        assert payload["device_class"] == "temperature"
        assert payload["state_class"] == "measurement"
        assert payload["unit_of_measurement"] == "°C"
        assert payload["expire_after"] == 321
    for field_name in fields[2:]:
        payload = payloads[field_name]
        assert payload["device_class"] == "pressure"
        assert payload["state_class"] == "measurement"
        assert payload["unit_of_measurement"] == "kPa"
        assert payload["expire_after"] == 321


def test_tombstones_clear_only_unselected_original_pulse_entities():
    config = enabled_config(fields=("battery_pct",))
    assert discovery_tombstones(config) == [
        ("homeassistant/sensor/pulse_garage-1/range_km/config", ""),
        ("homeassistant/sensor/pulse_garage-1/charging/config", ""),
        ("homeassistant/sensor/pulse_garage-1/interior_temperature_c/config", ""),
        ("homeassistant/sensor/pulse_garage-1/battery_min_temperature_c/config", ""),
        ("homeassistant/sensor/pulse_garage-1/front_left_pressure_kpa/config", ""),
        ("homeassistant/sensor/pulse_garage-1/front_right_pressure_kpa/config", ""),
        ("homeassistant/sensor/pulse_garage-1/rear_left_pressure_kpa/config", ""),
        ("homeassistant/sensor/pulse_garage-1/rear_right_pressure_kpa/config", ""),
    ]
    legacy_selection_tombstones = discovery_tombstones(
        enabled_config(fields=("battery_pct", "range_km", "charging"))
    )
    assert {topic.rsplit("/", 2)[-2] for topic, _ in legacy_selection_tombstones} == set(METRIC_VALUES)
    assert discovery_tombstones(enabled_config(fields=ALL_MQTT_FIELDS)) == []
