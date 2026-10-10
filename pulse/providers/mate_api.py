"""Original Pulse adapter for the pinned MATE-API B10 read client.

The optional SDK is deliberately duck-typed so ordinary Pulse startup does not
import or install it. A trusted bootstrap may inject an already configured
``B10ReadClient``. This adapter never creates a login, reads credentials, or
uses the SDK's command surface.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import math
from threading import RLock
from typing import Any, Protocol

from pulse.providers.base import (
    COMMANDS,
    ProviderUnavailable,
    sdk_metadata,
    unknown_capabilities,
)


class B10ReadClient(Protocol):
    """Public SDK surface used by this adapter."""

    def read(self, vin: str, /) -> object: ...


_SUPPORTED_COMMANDS = COMMANDS


class MateApiB10Provider:
    """Fetch normalized B10 reads from an explicitly injected SDK read client."""

    def __init__(self, read_client: B10ReadClient, vehicle_id: str) -> None:
        if not callable(getattr(read_client, "read", None)):
            raise ValueError("Se requiere un cliente SDK con la operación read(vin).")
        if (
            not isinstance(vehicle_id, str)
            or not vehicle_id
            or len(vehicle_id) > 64
            or not vehicle_id.isascii()
            or not vehicle_id.isalnum()
        ):
            raise ValueError("La identidad del vehículo no tiene un formato admitido.")

        self._read_client = read_client
        self._vehicle_id = vehicle_id
        self._lock = RLock()
        self._status = "not_connected"
        self._reason = "Cliente configurado; Pulse aún no ha solicitado una lectura."
        self._last_contact_at: str | None = None
        self._last_observed_at: str | None = None

    def __repr__(self) -> str:
        return "MateApiB10Provider(<read client>, <vehicle id>)"

    def connection_status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "status": self._status,
                "reason": self._reason,
                "freshness": "unknown",
                "last_contact_at": self._last_contact_at,
                "last_observed_at": self._last_observed_at,
                "capabilities": unknown_capabilities(),
                "commands_enabled": False,
                "sdk": sdk_metadata(),
            }

    def fetch_snapshot(self) -> dict[str, Any]:
        """Perform one SDK read when explicitly called by a collector."""
        try:
            bundle = self._read_client.read(self._vehicle_id)
            snapshot = self._normalize_bundle(bundle)
        except ProviderUnavailable as error:
            self._mark_unavailable(error.code)
            raise error from None
        except Exception:
            # SDK exception text can contain provider details. Keep only a fixed code.
            self._mark_unavailable()
            raise ProviderUnavailable(
                "read_failed",
                "La lectura del vehículo no se completó; no se ha reintentado.",
            ) from None

        with self._lock:
            self._status = "connected"
            self._reason = "El SDK devolvió una lectura; la frescura física sigue sin validar."
            self._last_contact_at = snapshot["provider_received_at"]
            self._last_observed_at = snapshot["observed_at"]
        return snapshot

    def command_status(self, command: str) -> dict[str, Any]:
        if not isinstance(command, str) or command not in _SUPPORTED_COMMANDS:
            return {
                "command": command,
                "availability": "unknown",
                "enabled": False,
                "reason": "unsupported_command",
            }
        return {
            "command": command,
            "availability": "unknown",
            "enabled": False,
            "reason": "capabilities_not_validated",
        }

    def execute_command(self, command: str, *, confirmed: bool = False) -> dict[str, Any]:
        """Keep real commands closed until capability and F6 evidence exist."""
        if not isinstance(command, str) or command not in _SUPPORTED_COMMANDS:
            raise ProviderUnavailable("unsupported_command", "Comando no admitido.")
        if type(confirmed) is not bool:
            raise ProviderUnavailable("invalid_confirmation", "La confirmación debe ser booleana.")
        if not confirmed:
            raise ProviderUnavailable("confirmation_required", "El comando requiere confirmación.")

        status = self.command_status(command)
        if status["availability"] != "available":
            raise ProviderUnavailable(
                "capability_unknown",
                "No hay evidencia validada de capacidad y permiso para este vehículo.",
            )
        # No command method exists in this adapter while F6 is pending.
        raise ProviderUnavailable("commands_not_qualified", "Comandos físicos deshabilitados.")

    def _mark_unavailable(self, code: str | None = None) -> None:
        with self._lock:
            self._status = "unavailable"
            self._reason = {
                "authentication_required": "La sesión necesita autorización para continuar las lecturas.",
                "authentication_failed": "No se pudo autorizar la sesión; el inicio de sesión no se repite automáticamente.",
                "session_refresh_failed": "La renovación de sesión falló; las lecturas esperan el próximo intento permitido.",
                "session_refresh_cooldown": "La renovación de sesión está en espera antes de volver a intentarlo.",
                "session_refresh_invalid": "La sesión renovada no es válida; se requiere autorización de nuevo.",
                "session_refresh_uncertain": "No se pudo confirmar la renovación de sesión; se requiere revisar la conexión.",
                "session_storage_failed": "No se pudo recuperar o guardar la sesión cifrada; se requiere revisar la conexión.",
                "session_closed": "Las lecturas de esta conexión están detenidas.",
            }.get(code, "La última lectura no se completó; no se ha reintentado.")

    def _normalize_bundle(self, bundle: object) -> dict[str, Any]:
        telemetry = getattr(bundle, "telemetry", None)
        configuration = getattr(bundle, "configuration", None)
        if telemetry is None or configuration is None:
            raise ProviderUnavailable("invalid_sdk_response", "Respuesta SDK incompleta.")
        if getattr(telemetry, "vin", None) != self._vehicle_id:
            raise ProviderUnavailable("vehicle_identity_mismatch", "La lectura no coincide con el vehículo configurado.")
        if getattr(configuration, "vin", None) != self._vehicle_id:
            raise ProviderUnavailable("vehicle_identity_mismatch", "La configuración no coincide con el vehículo configurado.")

        values = getattr(telemetry, "values", None)
        if not isinstance(values, Mapping):
            raise ProviderUnavailable("invalid_sdk_response", "La telemetría normalizada no es válida.")

        battery_pct, battery_source = _first_number(
            values, ("precise_soc_percent", "soc_percent"), minimum=0, maximum=100
        )
        range_km, range_source = _first_number(
            values, ("expected_range_km",), minimum=0, maximum=5000
        )
        odometer_km, odometer_source = _first_number(
            values, ("odometer_km",), minimum=0, maximum=10_000_000
        )
        observed_at = _iso_datetime(getattr(telemetry, "observed_at", None))
        provider_received_at = _iso_datetime(getattr(telemetry, "received_at", None))
        configuration_observed_at = _iso_datetime(getattr(configuration, "observed_at", None))

        return {
            "vehicle": {
                "model": None,
                "battery_pct": battery_pct,
                "range_km": range_km,
                "odometer_km": odometer_km,
                "locked": None,
                "climate_on": None,
                "charging": None,
            },
            "field_sources": {
                "battery_pct": battery_source,
                "range_km": range_source,
                "odometer_km": odometer_source,
                "locked": None,
                "climate_on": None,
                "charging": None,
            },
            "observed_at": observed_at,
            "configuration_observed_at": configuration_observed_at,
            "provider_received_at": provider_received_at,
            "freshness": "unknown",
            "capabilities": unknown_capabilities(),
            "source": "MATE-API B10ReadClient",
        }


def _first_number(
    values: Mapping[str, Any],
    names: tuple[str, ...],
    *,
    minimum: float,
    maximum: float,
) -> tuple[float | int | None, str | None]:
    for name in names:
        value = values.get(name)
        if type(value) not in (int, float):
            continue
        try:
            valid = math.isfinite(value) and minimum <= value <= maximum
        except OverflowError:
            valid = False
        if not valid:
            continue
        return value, name
    return None, None


def _iso_datetime(value: object) -> str | None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.isoformat()
