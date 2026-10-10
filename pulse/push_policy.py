"""Política original de avisos: destinos públicos, consentimiento y lecturas recientes."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import base64
import os
import re
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization


def encode_url64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def decode_url64(value: str, size: int) -> bytes:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("Formato de clave inválido.")
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, TypeError):
        raise ValueError("Formato de clave inválido.") from None
    if len(decoded) != size or encode_url64(decoded) != value:
        raise ValueError("Formato de clave inválido.")
    return decoded


@dataclass(frozen=True)
class PushConfig:
    enabled: bool = False
    vapid_private_key: str = field(default="", repr=False)
    storage_key: str = field(default="", repr=False)
    contact: str = field(default="", repr=False)

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise ValueError("PULSE_PUSH_ENABLED debe ser booleano.")
        if not self.enabled:
            return
        try:
            ec.derive_private_key(int.from_bytes(decode_url64(self.vapid_private_key, 32), "big"), ec.SECP256R1())
            Fernet(self.storage_key.encode("ascii"))
            contact = urlsplit(self.contact)
            if (len(self.contact) > 255 or any(ord(c) < 33 or ord(c) > 126 for c in self.contact)
                    or not ((contact.scheme == "mailto" and re.fullmatch(r"[^@/?#]+@[^@/?#]+\.[^@/?#]+", contact.path)
                             and not contact.query and not contact.fragment)
                            or (contact.scheme == "https" and contact.hostname and not contact.username
                                and not contact.password and not contact.query and not contact.fragment))):
                raise ValueError
        except Exception:
            raise ValueError("Push requiere claves VAPID/cifrado y contacto válidos, configurados explícitamente.") from None

    @property
    def public_key(self) -> str | None:
        if not self.enabled:
            return None
        private = ec.derive_private_key(int.from_bytes(decode_url64(self.vapid_private_key, 32), "big"), ec.SECP256R1())
        return encode_url64(private.public_key().public_bytes(serialization.Encoding.X962,
                                                             serialization.PublicFormat.UncompressedPoint))

    @classmethod
    def from_env(cls):
        value = os.environ.get("PULSE_PUSH_ENABLED", "false")
        if value not in {"true", "false"}:
            raise ValueError("PULSE_PUSH_ENABLED debe ser true o false.")
        if value == "false":
            return cls()
        return cls(enabled=True, vapid_private_key=os.environ.get("PULSE_PUSH_VAPID_PRIVATE_KEY", ""),
                   storage_key=os.environ.get("PULSE_PUSH_STORAGE_KEY", ""),
                   contact=os.environ.get("PULSE_PUSH_CONTACT", ""))


def validate_subscription(subscription: dict) -> dict:
    """No destinos arbitrarios, claves deformadas ni expiraciones no finitas."""
    try:
        endpoint = subscription["endpoint"]
        if (not isinstance(endpoint, str) or not 1 <= len(endpoint) <= 2048
                or any(ord(c) < 33 or ord(c) > 126 for c in endpoint) or "\\" in endpoint):
            raise ValueError
        url = urlsplit(endpoint)
        host = url.hostname or ""
        allowed = host in {"fcm.googleapis.com", "updates.push.services.mozilla.com"} or bool(
            re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*\.push\.apple\.com", host))
        if (url.scheme != "https" or url.netloc != host or not allowed or url.fragment
                or not url.path.startswith("/") or url.path == "/"):
            raise ValueError
        keys = subscription["keys"]
        point = decode_url64(keys["p256dh"], 65)
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), point)
        decode_url64(keys["auth"], 16)
        return {"endpoint": endpoint, "keys": {"p256dh": keys["p256dh"], "auth": keys["auth"]}}
    except Exception:
        raise ValueError("La suscripción o su servicio de avisos no son válidos o no están admitidos.") from None


class ChargingChangeDetector:
    """Compara lecturas recién guardadas; no reconstruye huecos ni actuación física."""

    def __init__(self):
        self.previous: tuple[datetime, bool] | None = None

    def observe(self, snapshot: dict | None, now: datetime, *, demo: bool) -> bool:
        if demo or not snapshot or snapshot.get("source") != "MATE-API B10ReadClient":
            self.previous = None
            return False
        try:
            observed = datetime.fromisoformat(snapshot["observed_at"].replace("Z", "+00:00"))
            if observed.tzinfo is None or observed.utcoffset() is None:
                raise ValueError
            observed = observed.astimezone(timezone.utc)
            charging = snapshot["vehicle"]["charging"]
            age = (now - observed).total_seconds()
            if type(charging) is not bool or not 0 <= age <= 300:
                raise ValueError
        except (TypeError, ValueError, KeyError, AttributeError):
            self.previous = None
            return False
        previous = self.previous
        if previous and observed <= previous[0]:
            return False
        self.previous = (observed, charging)
        return bool(previous and 0 < (observed - previous[0]).total_seconds() <= 600
                    and previous[1] != charging)
