"""Shared provider contract and the safe no-material default."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


SDK_NAME = "MATE-API"
SDK_PACKAGE = "mate-api"
SDK_VERSION = "0.1.0a16"
SDK_SOURCE = "https://github.com/ProtossBlaster/MATE-API"
SDK_COMMIT = "5b7d30c30ae10eb18677214e36a9d42398a769e6"
SDK_LICENSE = "MIT"

COMMANDS = frozenset(
    {"lock", "unlock", "climate_on", "climate_off", "charge_start", "charge_stop"}
)


class ProviderUnavailable(RuntimeError):
    """Provider failure with a fixed, non-sensitive reason code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@runtime_checkable
class VehicleProvider(Protocol):
    def connection_status(self) -> dict[str, Any]: ...

    def fetch_snapshot(self) -> dict[str, Any]: ...

    def command_status(self, command: str) -> dict[str, Any]: ...

    def execute_command(self, command: str, *, confirmed: bool = False) -> dict[str, Any]: ...


def sdk_metadata() -> dict[str, Any]:
    """Return public dependency provenance; never include connection material."""
    return {
        "name": SDK_NAME,
        "package": SDK_PACKAGE,
        "version": SDK_VERSION,
        "source": SDK_SOURCE,
        "commit": SDK_COMMIT,
        "license": SDK_LICENSE,
        "optional": True,
    }


def unknown_capabilities() -> dict[str, Any]:
    return {
        "availability": "unknown",
        "observed_at": None,
        "reason": "capabilities_not_validated",
    }


class UnconfiguredProvider:
    """Default provider when no B10 and authorized connection material are present."""

    def connection_status(self) -> dict[str, Any]:
        return {
            "status": "missing_material",
            "reason": (
                "Faltan un B10 y material de conexión autorizado. Pulse no ha iniciado "
                "sesión ni se ha conectado con el servicio."
            ),
            "freshness": "unknown",
            "last_contact_at": None,
            "last_observed_at": None,
            "capabilities": unknown_capabilities(),
            "commands_enabled": False,
            "sdk": sdk_metadata(),
        }

    def fetch_snapshot(self) -> dict[str, Any]:
        raise ProviderUnavailable(
            "missing_material",
            "No se puede leer el vehículo mientras falten el B10 y material autorizado.",
        )

    def command_status(self, command: str) -> dict[str, Any]:
        if not isinstance(command, str) or command not in COMMANDS:
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
            "reason": "commands_not_qualified",
        }

    def execute_command(self, command: str, *, confirmed: bool = False) -> dict[str, Any]:
        if not isinstance(command, str) or command not in COMMANDS:
            raise ProviderUnavailable("unsupported_command", "Comando no admitido.")
        raise ProviderUnavailable(
            "commands_not_qualified",
            "Los comandos físicos siguen bloqueados hasta completar la validación F6.",
        )
