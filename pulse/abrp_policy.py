"""Configuración privada y validación mínima para compartir SOC con ABRP."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import os
import re
from collections.abc import Mapping
from typing import Any


@dataclass(frozen=True)
class AbrpConfig:
    enabled: bool = False
    api_key: str = field(default="", repr=False)
    user_token: str = field(default="", repr=False)
    poll_seconds: int = 60
    send_interval_seconds: int = 300
    max_age_seconds: int = 300

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("PULSE_ABRP_ENABLED debe ser booleano.")
        if not isinstance(self.api_key, str) or not isinstance(self.user_token, str):
            raise ValueError("Las credenciales ABRP deben ser texto.")
        _integer(self.poll_seconds, "poll_seconds", 1, 60)
        _integer(self.send_interval_seconds, "send_interval_seconds", 60, 3600)
        _integer(self.max_age_seconds, "max_age_seconds", 30, 300)
        if self.enabled:
            _credential(self.api_key, "api_key")
            _credential(self.user_token, "user_token")

    @property
    def scope(self) -> str:
        """Hash interno de las credenciales ABRP a las que se da consentimiento.

        Los límites de frecuencia y antigüedad son política local y no cambian
        el destino del consentimiento; cambiar la clave o el token sí lo hace.
        """
        canonical = json.dumps(
            {
                "api_key": self.api_key,
                "user_token": self.user_token,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        return hashlib.sha256(canonical).hexdigest()

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> AbrpConfig:
        if env is not None and not isinstance(env, Mapping):
            raise ValueError("La configuración de entorno ABRP debe ser un mapa.")
        values = os.environ if env is None else env
        enabled_value = values.get("PULSE_ABRP_ENABLED", "false")
        if not isinstance(enabled_value, str) or enabled_value not in {"true", "false"}:
            raise ValueError("PULSE_ABRP_ENABLED debe ser true o false.")
        return cls(
            enabled=enabled_value == "true",
            api_key=values.get("PULSE_ABRP_API_KEY", ""),
            user_token=values.get("PULSE_ABRP_USER_TOKEN", ""),
            poll_seconds=_env_integer(values, "PULSE_ABRP_POLL_SECONDS", 60),
            send_interval_seconds=_env_integer(
                values, "PULSE_ABRP_SEND_INTERVAL_SECONDS", 300
            ),
            max_age_seconds=_env_integer(values, "PULSE_ABRP_MAX_AGE_SECONDS", 300),
        )


@dataclass(frozen=True)
class AbrpObservation:
    snapshot_id: str
    observed_at: datetime
    utc_seconds: int
    soc_percent: float


def inspect_observation(
    snapshot: Any,
    now: datetime,
    max_age_seconds: int,
) -> tuple[AbrpObservation | None, str]:
    """Validate one already-persisted live snapshot without retaining it."""
    if not isinstance(snapshot, Mapping):
        return None, "observation_invalid"
    try:
        snapshot_id = snapshot.get("id")
        if not isinstance(snapshot_id, str) or not 1 <= len(snapshot_id) <= 128:
            raise ValueError
        if snapshot.get("source") != "MATE-API B10ReadClient":
            raise ValueError

        vehicle = snapshot.get("vehicle")
        sources = snapshot.get("field_sources")
        if not isinstance(vehicle, Mapping) or not isinstance(sources, Mapping):
            raise ValueError
        soc = vehicle.get("battery_pct")
        if type(soc) not in (int, float):
            raise ValueError
        soc_value = float(soc)
        if not math.isfinite(soc_value) or not 0 <= soc_value <= 100:
            raise ValueError
        if sources.get("battery_pct") not in {"precise_soc_percent", "soc_percent"}:
            raise ValueError

        observed = _aware_datetime(snapshot.get("observed_at"))
        current = _aware_datetime(now)
        if observed > current:
            raise ValueError
        age = (current - observed).total_seconds()
        if age > max_age_seconds:
            return None, "observation_stale"

        return (
            AbrpObservation(
                snapshot_id=snapshot_id,
                observed_at=observed,
                utc_seconds=math.floor(observed.timestamp()),
                soc_percent=soc_value,
            ),
            "",
        )
    except (AttributeError, OverflowError, TypeError, ValueError):
        return None, "observation_invalid"


def observation_attempt_key(observation: AbrpObservation) -> str:
    """Stable deduplication key for the UTC second and SOC sent on the wire."""
    try:
        value = Decimal(str(observation.soc_percent))
        if not value.is_finite():
            raise InvalidOperation
        if value == 0:
            value = Decimal(0)
        soc = format(value.normalize(), "f")
        raw = f"{observation.utc_seconds}:{soc}".encode("ascii")
    except (InvalidOperation, UnicodeEncodeError, ValueError):
        raise ValueError("Observación inválida para deduplicación.") from None
    return hashlib.sha256(raw).hexdigest()


def attempt_cooldown_active(
    last_attempt_at: Any,
    now: datetime,
    send_interval_seconds: int,
) -> bool:
    """Fail closed for a malformed timestamp, a clock rewind, or an active cooldown."""
    if last_attempt_at is None:
        return False
    try:
        previous = _aware_datetime(last_attempt_at)
        current = _aware_datetime(now)
        elapsed = (current - previous).total_seconds()
        return elapsed < send_interval_seconds
    except (OverflowError, TypeError, ValueError):
        return True


def _aware_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and 1 <= len(value) <= 64:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError
    return parsed.astimezone(timezone.utc)


def _credential(value: Any, field_name: str) -> None:
    try:
        if not isinstance(value, str) or not 1 <= len(value) <= 512:
            raise ValueError
        if any(ord(character) < 33 or ord(character) > 126 for character in value):
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError(f"PULSE_ABRP_{field_name.upper()} no es válido.") from None


def _integer(value: Any, field_name: str, minimum: int, maximum: int) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"Límite ABRP inválido: {field_name}.")


def _env_integer(values: Mapping[str, str], name: str, default: int) -> int:
    raw = values.get(name, str(default))
    if not isinstance(raw, str) or re.fullmatch(r"[0-9]+", raw) is None:
        raise ValueError(f"{name} debe ser un entero decimal.")
    try:
        return int(raw)
    except (ValueError, OverflowError):
        raise ValueError(f"{name} debe ser un entero decimal.") from None


__all__ = [
    "AbrpConfig",
    "AbrpObservation",
    "attempt_cooldown_active",
    "inspect_observation",
    "observation_attempt_key",
]
