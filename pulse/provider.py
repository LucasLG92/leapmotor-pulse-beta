"""Stable entry point for the currently unconfigured vehicle provider."""

from __future__ import annotations

from typing import Any

from pulse.providers.base import ProviderUnavailable, UnconfiguredProvider, VehicleProvider


_provider: VehicleProvider = UnconfiguredProvider()


def configure_provider(provider: VehicleProvider) -> None:
    """Install an explicitly constructed provider during trusted app startup.

    This function performs structural validation only. It does not log in, read
    files, contact the cloud, or enable commands.
    """
    required = ("connection_status", "fetch_snapshot", "command_status", "execute_command")
    if any(not callable(getattr(provider, name, None)) for name in required):
        raise TypeError("El proveedor no implementa el contrato Pulse.")
    global _provider
    _provider = provider


def connection_status() -> dict[str, Any]:
    """Return in-memory status without initiating network or filesystem access."""
    return _provider.connection_status()


def fetch_snapshot() -> dict[str, Any]:
    """Request one provider read; callers decide when synchronization occurs."""
    return _provider.fetch_snapshot()


def command_status(command: str) -> dict[str, Any]:
    return _provider.command_status(command)


def execute_command(command: str, *, confirmed: bool = False) -> dict[str, Any]:
    return _provider.execute_command(command, confirmed=confirmed)


__all__ = [
    "ProviderUnavailable",
    "command_status",
    "configure_provider",
    "connection_status",
    "execute_command",
    "fetch_snapshot",
]
