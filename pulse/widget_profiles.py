"""Validation primitives for owner-scoped, presentation-only widget profiles."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


PROFILE_VEHICLE_FIELDS = frozenset({"battery_pct", "range_km", "charging", "locked"})
PROFILE_ENERGY_FIELDS = frozenset({"energy_kwh", "energy_cost_eur"})
LEGACY_PROFILE_FIELDS = frozenset({"battery_pct", "range_km", "charging"})
PROFILE_FIELDS = PROFILE_VEHICLE_FIELDS | PROFILE_ENERGY_FIELDS
PROFILE_APPEARANCES = frozenset({"system", "light", "dark"})
PROFILE_PRIVACY_MODES = frozenset({"masked", "visible"})
PROFILE_DATA_KEYS = frozenset(
    {"name", "fields", "appearance", "privacy_mode", "max_age_minutes"}
)
MAX_PROFILES_PER_OWNER = 5


class WidgetProfileValidationError(ValueError):
    """A profile or owner identifier is not valid for persistence."""


def profile_kind(fields: Any) -> str:
    """Derive the widget family from its selected fields."""
    if not isinstance(fields, list) or not 1 <= len(fields) <= 3:
        raise WidgetProfileValidationError("fields debe contener de 1 a 3 campos.")
    if any(not isinstance(field, str) or field not in PROFILE_FIELDS for field in fields):
        raise WidgetProfileValidationError("fields contiene un campo no admitido.")
    if len(set(fields)) != len(fields):
        raise WidgetProfileValidationError("fields no puede repetir campos.")
    selected = frozenset(fields)
    if selected <= PROFILE_VEHICLE_FIELDS:
        return "vehicle"
    if selected <= PROFILE_ENERGY_FIELDS:
        return "energy"
    raise WidgetProfileValidationError("fields no puede mezclar vehículo y energía.")


def validate_owner_id(value: Any) -> str:
    """Validate an authentication subject without rewriting its identity."""
    if not isinstance(value, str) or not value or len(value) > 255:
        raise WidgetProfileValidationError("owner_id debe ser texto de 1 a 255 caracteres.")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise WidgetProfileValidationError("owner_id contiene texto no válido.") from None
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise WidgetProfileValidationError("owner_id contiene caracteres no permitidos.")
    return value


def validate_profile_data(value: Any, *, partial: bool = False) -> dict[str, Any]:
    """Return canonical writable fields, rejecting every unrecognized key."""
    if not isinstance(value, Mapping):
        raise WidgetProfileValidationError("El perfil debe ser un objeto.")
    keys = set(value)
    if not keys or keys - PROFILE_DATA_KEYS:
        raise WidgetProfileValidationError("El perfil contiene campos vacíos o no admitidos.")
    if not partial and keys != PROFILE_DATA_KEYS:
        raise WidgetProfileValidationError("El perfil debe incluir todos sus campos editables.")

    normalized: dict[str, Any] = {}
    if "name" in value:
        name = value["name"]
        if not isinstance(name, str):
            raise WidgetProfileValidationError("name debe ser texto.")
        name = name.strip()
        if not 1 <= len(name) <= 60:
            raise WidgetProfileValidationError("name debe tener entre 1 y 60 caracteres.")
        try:
            name.encode("utf-8")
        except UnicodeEncodeError:
            raise WidgetProfileValidationError("name contiene texto no válido.") from None
        if any(ord(character) < 32 or ord(character) == 127 for character in name):
            raise WidgetProfileValidationError("name contiene caracteres no permitidos.")
        normalized["name"] = name

    if "fields" in value:
        fields = value["fields"]
        profile_kind(fields)
        normalized["fields"] = list(fields)

    if "appearance" in value:
        appearance = value["appearance"]
        if not isinstance(appearance, str) or appearance not in PROFILE_APPEARANCES:
            raise WidgetProfileValidationError("appearance debe ser system, light o dark.")
        normalized["appearance"] = appearance

    if "privacy_mode" in value:
        privacy_mode = value["privacy_mode"]
        if not isinstance(privacy_mode, str) or privacy_mode not in PROFILE_PRIVACY_MODES:
            raise WidgetProfileValidationError("privacy_mode debe ser masked o visible.")
        normalized["privacy_mode"] = privacy_mode

    if "max_age_minutes" in value:
        age = value["max_age_minutes"]
        if type(age) is not int or not 15 <= age <= 120:
            raise WidgetProfileValidationError("max_age_minutes debe ser un entero entre 15 y 120.")
        normalized["max_age_minutes"] = age

    return normalized
