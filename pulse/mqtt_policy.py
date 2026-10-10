"""Política pura de configuración y publicaciones MQTT para Home Assistant."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import ipaddress
import json
import math
import os
import re
from collections.abc import Mapping
from typing import Any


_ALLOWED_FIELDS = ("battery_pct", "range_km", "charging")
_FIELD_SET = frozenset(_ALLOWED_FIELDS)
_SLUG_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?\Z")
_DNS_LABEL_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")
_ERROR_TYPE = "La configuración MQTT tiene tipos no válidos."
_ERROR_RANGE = "La configuración MQTT contiene un valor fuera de rango."
_ERROR_HOST = "El host MQTT no es válido."
_ERROR_ENABLED = "La configuración MQTT activada está incompleta."
_ERROR_FIELDS = "La selección de campos MQTT no es válida."
_ERROR_INSTANCE = "El identificador MQTT de instancia no es válido."
_ERROR_PREFIX = "El prefijo de descubrimiento MQTT no es válido."
_ERROR_TLS = "MQTT sin TLS solo se permite en loopback."
_ERROR_CREDENTIALS = "Las credenciales MQTT no son válidas."
_ERROR_NOW = "La hora MQTT debe incluir UTC."
_ERROR_INPUT = "Los datos MQTT no cumplen el contrato."


def _valid_host(value: str) -> bool:
    if not value or value != value.strip():
        return False
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return False
    if any(character in value for character in "*/?#@\\") or any(character.isspace() for character in value):
        return False

    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        pass

    if ":" in value:
        return False
    hostname = value[:-1] if value.endswith(".") else value
    if not hostname or len(hostname) > 253:
        return False
    return all(_DNS_LABEL_RE.fullmatch(label) for label in hostname.split("."))


def _is_loopback_host(value: str) -> bool:
    return value in {"localhost", "127.0.0.1", "::1"}


def _parse_env_bool(value: str) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError(_ERROR_TYPE)


def _parse_env_int(value: str) -> int:
    if not value or not value.isascii() or not value.isdecimal():
        raise ValueError(_ERROR_TYPE)
    try:
        return int(value)
    except ValueError:
        raise ValueError(_ERROR_TYPE) from None


@dataclass(frozen=True)
class MqttConfig:
    enabled: bool = False
    host: str = field(default="", repr=False)
    port: int = 8883
    username: str = field(default="", repr=False)
    password: str = field(default="", repr=False)
    tls: bool = True
    ca_file: str = ""
    instance: str = ""
    discovery_prefix: str = "homeassistant"
    fields: tuple[str, ...] = ()
    max_age_seconds: int = 900
    poll_seconds: int = 15

    def __post_init__(self) -> None:
        for name in ("enabled", "tls"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(_ERROR_TYPE)
        for name in ("host", "username", "password", "ca_file", "instance", "discovery_prefix"):
            if type(getattr(self, name)) is not str:
                raise ValueError(_ERROR_TYPE)
        for name in ("port", "max_age_seconds", "poll_seconds"):
            if type(getattr(self, name)) is not int:
                raise ValueError(_ERROR_TYPE)
        if type(self.fields) is not tuple or any(type(item) is not str for item in self.fields):
            raise ValueError(_ERROR_TYPE)

        if self.port < 1 or self.port > 65535:
            raise ValueError(_ERROR_RANGE)
        if self.max_age_seconds < 30 or self.max_age_seconds > 7200:
            raise ValueError(_ERROR_RANGE)
        if self.poll_seconds < 1 or self.poll_seconds > 60:
            raise ValueError(_ERROR_RANGE)

        if self.host and not _valid_host(self.host):
            raise ValueError(_ERROR_HOST)
        if self.username and any(ord(character) < 32 or ord(character) == 127 for character in self.username):
            raise ValueError(_ERROR_CREDENTIALS)
        if self.password and any(ord(character) < 32 or ord(character) == 127 for character in self.password):
            raise ValueError(_ERROR_CREDENTIALS)
        if self.password and not self.username:
            raise ValueError(_ERROR_CREDENTIALS)
        if not self.tls and (not self.host or not _is_loopback_host(self.host)):
            raise ValueError(_ERROR_TLS)

        if len(set(self.fields)) != len(self.fields) or any(name not in _FIELD_SET for name in self.fields):
            raise ValueError(_ERROR_FIELDS)
        if self.discovery_prefix and (
            not _SLUG_RE.fullmatch(self.discovery_prefix) or self.discovery_prefix == "pulse"
        ):
            raise ValueError(_ERROR_PREFIX)
        if self.instance and not _SLUG_RE.fullmatch(self.instance):
            raise ValueError(_ERROR_INSTANCE)
        if self.enabled and (
            not self.host or not self.instance or not self.fields or not self.discovery_prefix
        ):
            raise ValueError(_ERROR_ENABLED)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> MqttConfig:
        """Lee únicamente las variables MQTT reconocidas y explícitas."""
        source = os.environ if env is None else env
        if not isinstance(source, Mapping):
            raise ValueError(_ERROR_TYPE)

        def get(name: str, default: str) -> str:
            value = source.get(f"PULSE_MQTT_{name}", default)
            if type(value) is not str:
                raise ValueError(_ERROR_TYPE)
            return value

        raw_fields = get("FIELDS", "")
        parts = raw_fields.split(",")
        if raw_fields.strip() and any(not part.strip() for part in parts):
            raise ValueError(_ERROR_FIELDS)
        fields = tuple(part.strip() for part in parts if part.strip())
        return cls(
            enabled=_parse_env_bool(get("ENABLED", "false")),
            host=get("HOST", ""),
            port=_parse_env_int(get("PORT", "8883")),
            username=get("USERNAME", ""),
            password=get("PASSWORD", ""),
            tls=_parse_env_bool(get("TLS", "true")),
            ca_file=get("CA_FILE", ""),
            instance=get("INSTANCE", ""),
            discovery_prefix=get("DISCOVERY_PREFIX", "homeassistant"),
            fields=fields,
            max_age_seconds=_parse_env_int(get("MAX_AGE_SECONDS", "900")),
            poll_seconds=_parse_env_int(get("POLL_SECONDS", "15")),
        )

    @property
    def topic_prefix(self) -> str:
        return f"pulse/{self.instance}"

    @property
    def client_id(self) -> str:
        return f"pulse-{self.instance}"

    @property
    def bridge_availability_topic(self) -> str:
        return f"{self.topic_prefix}/availability/bridge"

    @property
    def birth_topic(self) -> str:
        return f"{self.discovery_prefix}/status"


def _empty_publication(data_status: str, *, observed_at: str | None = None, remaining_seconds: int = 0) -> dict[str, Any]:
    return {
        "data_status": data_status,
        "observed_at": observed_at,
        "remaining_seconds": remaining_seconds,
        "values": {},
        "source": "unavailable",
    }


def _parse_observed_at(value: Any) -> datetime | None:
    if type(value) is not str or not value or value != value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _selected_value(field_name: str, value: Any) -> tuple[bool, int | float | bool | None]:
    if field_name == "charging":
        return (type(value) is bool, value if type(value) is bool else None)
    if type(value) not in (int, float):
        return False, None
    try:
        if not math.isfinite(value):
            return False, None
    except OverflowError:
        return False, None
    maximum = 100 if field_name == "battery_pct" else 5000
    if value < 0 or value > maximum:
        return False, None
    return True, value


def prepare_publication(config: MqttConfig, context: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Devuelve solo campos seleccionados de telemetría física vigente."""
    if type(config) is not MqttConfig:
        raise ValueError(_ERROR_INPUT)
    if type(now) is not datetime or now.tzinfo is None or now.utcoffset() is None or now.utcoffset().total_seconds() != 0:
        raise ValueError(_ERROR_NOW)
    now_utc = now.astimezone(timezone.utc)

    if not config.enabled:
        return _empty_publication("no_data")
    if type(context) is not dict or type(context.get("demo_enabled")) is not bool:
        return _empty_publication("unavailable")
    if context["demo_enabled"]:
        return _empty_publication("demo")
    snapshot = context.get("snapshot")
    if type(snapshot) is not dict:
        return _empty_publication("unavailable")

    observed = _parse_observed_at(snapshot.get("observed_at"))
    if observed is None:
        return _empty_publication("unavailable")
    observed_at = _iso_utc(observed)
    if observed > now_utc:
        return _empty_publication("unavailable", observed_at=observed_at)

    remaining_seconds = (
        observed + timedelta(seconds=config.max_age_seconds) - now_utc
    ) // timedelta(seconds=1)
    if remaining_seconds <= 0:
        return _empty_publication("unavailable", observed_at=observed_at)

    vehicle = snapshot.get("vehicle")
    if type(vehicle) is not dict:
        return _empty_publication(
            "unavailable", observed_at=observed_at, remaining_seconds=remaining_seconds
        )
    values: dict[str, int | float | bool] = {}
    for field_name in config.fields:
        valid, value = _selected_value(field_name, vehicle.get(field_name))
        if valid:
            values[field_name] = value  # type: ignore[assignment]
    if not values:
        return _empty_publication(
            "unavailable", observed_at=observed_at, remaining_seconds=remaining_seconds
        )
    return {
        "data_status": "ready",
        "observed_at": observed_at,
        "remaining_seconds": remaining_seconds,
        "values": values,
        "source": "telemetry",
    }


def _availability_topic(config: MqttConfig, field_name: str) -> str:
    return f"{config.topic_prefix}/availability/{field_name}"


def _discovery_topic(config: MqttConfig, field_name: str) -> str:
    return f"{config.discovery_prefix}/sensor/pulse_{config.instance}/{field_name}/config"


def _sensor_payload(config: MqttConfig, field_name: str, remaining_seconds: int) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": {
            "battery_pct": "Batería",
            "range_km": "Autonomía",
            "charging": "Carga",
        }[field_name],
        "unique_id": f"pulse_{config.instance}_{field_name}",
        "device": {
            "identifiers": [f"pulse_{config.instance}"],
            "name": "Leapmotor Pulse",
            "manufacturer": "Pulse",
        },
        "state_topic": f"{config.topic_prefix}/state/{field_name}",
        "value_template": "{{ value_json.value }}",
        "availability": [
            {"topic": config.bridge_availability_topic},
            {"topic": _availability_topic(config, field_name)},
        ],
        "availability_mode": "all",
        "expire_after": max(1, remaining_seconds),
    }
    if field_name == "battery_pct":
        payload.update(device_class="battery", state_class="measurement", unit_of_measurement="%")
    elif field_name == "range_km":
        payload.update(device_class="distance", state_class="measurement", unit_of_measurement="km")
    else:
        payload.update(device_class="enum", options=["charging", "not_charging"])
    return payload


def discovery_messages(config: MqttConfig, remaining_seconds: int) -> list[tuple[str, str]]:
    """Construye los mensajes JSON retenibles de descubrimiento seleccionados."""
    if type(config) is not MqttConfig or type(remaining_seconds) is not int:
        raise ValueError(_ERROR_INPUT)
    if not config.enabled:
        return []
    messages: list[tuple[str, str]] = []
    for field_name in config.fields:
        payload = _sensor_payload(config, field_name, remaining_seconds)
        messages.append((
            _discovery_topic(config, field_name),
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        ))
    return messages


def discovery_tombstones(config: MqttConfig) -> list[tuple[str, str]]:
    """Limpia solo los topics propios de los tres campos que no están seleccionados."""
    if type(config) is not MqttConfig:
        raise ValueError(_ERROR_INPUT)
    if not config.enabled:
        return []
    selected = frozenset(config.fields)
    return [(_discovery_topic(config, field_name), "") for field_name in _ALLOWED_FIELDS if field_name not in selected]
