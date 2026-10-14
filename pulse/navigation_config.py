"""Explicit, disabled-by-default configuration for navigation services."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import os
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit


_ORIGIN_DEFAULTS = {
    "tiles_origin": "https://tile.openstreetmap.org",
    "places_origin": "https://nominatim.openstreetmap.org",
    "chargers_origin": "https://overpass-api.de",
}
_EMAIL_RE = re.compile(
    r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+\Z"
)
_PHOTON_PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
)


def _clean_url(value: str, *, name: str, origin_only: bool) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or any(
        ord(char) < 32 or ord(char) == 127 or char.isspace() for char in value
    ):
        raise ValueError(f"{name} no es válida.")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise ValueError(f"{name} no es válida.") from None
    if (parsed.scheme.lower() != "https" or not hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
            or (origin_only and parsed.path not in {"", "/"})
            or (port is not None and not 1 <= port <= 65535)):
        raise ValueError(f"{name} debe ser HTTPS y no puede incluir credenciales, consulta ni ruta no permitida.")
    if origin_only:
        # Keep the exact operator-selected authority (including a non-default port),
        # while ensuring callers can only append fixed endpoint paths.
        return f"https://{parsed.netloc}"
    return value


def _clean_photon_private_origin(value: str) -> str:
    """Accept only an HTTPS origin addressed directly to a private IP literal."""
    if (not isinstance(value, str) or not value or value != value.strip() or "%" in value
            or "?" in value or "#" in value
            or any(ord(char) < 32 or ord(char) == 127 or char.isspace() for char in value)):
        raise ValueError("PULSE_NAVIGATION_PLACES_ORIGIN debe ser un origen Photon privado.")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
        address = ipaddress.ip_address(hostname) if hostname else None
    except ValueError:
        raise ValueError("PULSE_NAVIGATION_PLACES_ORIGIN debe ser un origen Photon privado.") from None
    if (parsed.scheme.lower() != "https" or address is None or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"}
            or (port is not None and not 1 <= port <= 65535)):
        raise ValueError("PULSE_NAVIGATION_PLACES_ORIGIN debe ser un origen Photon privado.")
    if not address.is_loopback and (
        address.is_link_local or address.is_multicast or address.is_reserved or address.is_unspecified
        or (address.version == 6 and address.ipv4_mapped is not None)
        or not any(address.version == network.version and address in network
                   for network in _PHOTON_PRIVATE_NETWORKS)
    ):
        raise ValueError("PULSE_NAVIGATION_PLACES_ORIGIN debe ser un origen Photon privado.")
    return f"https://{parsed.netloc}"


def _absolute_file_path(value: str, *, env_name: str, required: bool) -> str:
    if not isinstance(value, str) or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{env_name} debe ser una ruta absoluta.")
    if not value:
        if required:
            raise ValueError(f"{env_name} es obligatorio para Photon privado habilitado.")
        return ""
    try:
        if not Path(value).is_absolute():
            raise ValueError
    except (OSError, ValueError):
        raise ValueError(f"{env_name} debe ser una ruta absoluta.") from None
    return value


def _contact(value: str, *, required: bool) -> str:
    if not isinstance(value, str):
        raise ValueError("PULSE_NAVIGATION_CONTACT debe ser un contacto HTTPS o correo válido.")
    if not value:
        if required:
            raise ValueError("PULSE_NAVIGATION_CONTACT es obligatorio al activar navegación.")
        return ""
    if len(value) > 512 or not value.isascii():
        raise ValueError("PULSE_NAVIGATION_CONTACT debe usar un contacto ASCII de hasta 512 caracteres.")
    try:
        if urlsplit(value).scheme.lower() == "https":
            return _clean_url(value, name="PULSE_NAVIGATION_CONTACT", origin_only=False)
    except ValueError:
        raise ValueError("PULSE_NAVIGATION_CONTACT debe ser un contacto HTTPS o correo válido.") from None
    if (len(value) <= 254 and _EMAIL_RE.fullmatch(value) and ".." not in value
            and len(value.split("@", 1)[0]) <= 64):
        return value
    raise ValueError("PULSE_NAVIGATION_CONTACT debe ser un contacto HTTPS o correo válido.")


@dataclass(frozen=True)
class NavigationConfig:
    """Fixed HTTPS destinations and explicit contact, disabled until configured."""

    enabled: bool = False
    contact: str = ""
    tiles_origin: str = _ORIGIN_DEFAULTS["tiles_origin"]
    places_origin: str = _ORIGIN_DEFAULTS["places_origin"]
    chargers_origin: str = _ORIGIN_DEFAULTS["chargers_origin"]
    places_provider: Literal["nominatim", "photon_private"] = "nominatim"
    geocoder_ca_file: str = ""
    geocoder_cert_file: str = ""
    geocoder_key_file: str = ""

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("PULSE_NAVIGATION_ENABLED debe ser true o false.")
        if self.places_provider not in ("nominatim", "photon_private"):
            raise ValueError("PULSE_NAVIGATION_PLACES_PROVIDER no es válido.")
        object.__setattr__(self, "contact", _contact(self.contact, required=self.enabled))
        for field_name in ("tiles_origin", "chargers_origin"):
            value = getattr(self, field_name)
            normalized = _clean_url(value, name=f"PULSE_NAVIGATION_{field_name.upper()}", origin_only=True)
            object.__setattr__(self, field_name, normalized)
        if self.places_provider == "photon_private":
            object.__setattr__(self, "places_origin", _clean_photon_private_origin(self.places_origin))
        else:
            object.__setattr__(self, "places_origin", _clean_url(
                self.places_origin, name="PULSE_NAVIGATION_PLACES_ORIGIN", origin_only=True))
        required = self.places_provider == "photon_private" and self.enabled
        for field_name, env_name in (
            ("geocoder_ca_file", "PULSE_NAVIGATION_GEOCODER_CA_FILE"),
            ("geocoder_cert_file", "PULSE_NAVIGATION_GEOCODER_CERT_FILE"),
            ("geocoder_key_file", "PULSE_NAVIGATION_GEOCODER_KEY_FILE"),
        ):
            object.__setattr__(self, field_name, _absolute_file_path(
                getattr(self, field_name), env_name=env_name, required=required))

    @classmethod
    def from_env(cls) -> "NavigationConfig":
        enabled = os.environ.get("PULSE_NAVIGATION_ENABLED", "false")
        if enabled not in {"true", "false"}:
            raise ValueError("PULSE_NAVIGATION_ENABLED debe ser true o false.")
        try:
            return cls(
                enabled=enabled == "true",
                contact=os.environ.get("PULSE_NAVIGATION_CONTACT", ""),
                tiles_origin=os.environ.get("PULSE_NAVIGATION_TILES_ORIGIN", _ORIGIN_DEFAULTS["tiles_origin"]),
                places_origin=os.environ.get("PULSE_NAVIGATION_PLACES_ORIGIN", _ORIGIN_DEFAULTS["places_origin"]),
                chargers_origin=os.environ.get("PULSE_NAVIGATION_CHARGERS_ORIGIN", _ORIGIN_DEFAULTS["chargers_origin"]),
                places_provider=os.environ.get("PULSE_NAVIGATION_PLACES_PROVIDER", "nominatim"),
                geocoder_ca_file=os.environ.get("PULSE_NAVIGATION_GEOCODER_CA_FILE", ""),
                geocoder_cert_file=os.environ.get("PULSE_NAVIGATION_GEOCODER_CERT_FILE", ""),
                geocoder_key_file=os.environ.get("PULSE_NAVIGATION_GEOCODER_KEY_FILE", ""),
            )
        except (TypeError, ValueError):
            raise ValueError("La configuración de navegación no es válida; revisa activación, contacto y orígenes HTTPS.") from None


__all__ = ["NavigationConfig"]
