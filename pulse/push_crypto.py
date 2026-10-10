"""Cifrado Web Push por biblioteca MIT; VAPID con primitives existentes."""

import json
import time
from urllib.parse import urlsplit

from joserfc import jwt
from joserfc.jwk import ECKey
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import ec
import http_ece

from pulse.push_policy import PushConfig, decode_url64, validate_subscription


class PushCrypto:
    def __init__(self, config: PushConfig):
        self.config = config
        self._cipher = Fernet(config.storage_key.encode("ascii"))
        self._signing_key = ECKey.import_key(ec.derive_private_key(int.from_bytes(decode_url64(config.vapid_private_key, 32), "big"), ec.SECP256R1()))

    def protect(self, subscription: dict) -> str:
        return self._cipher.encrypt(json.dumps(validate_subscription(subscription), separators=(",", ":")).encode()).decode("ascii")

    def open(self, ciphertext: str) -> dict:
        data = self._cipher.decrypt(ciphertext.encode("ascii"))
        if len(data) > 4096:
            raise ValueError("Suscripción no disponible.")
        return validate_subscription(json.loads(data))

    def request(self, ciphertext: str, kind: str) -> tuple[str, dict[str, str], bytes]:
        if kind not in {"test", "charging_observation"}:
            raise ValueError("Tipo de aviso no admitido.")
        subscription = self.open(ciphertext)
        payload = json.dumps({"version": 1, "kind": kind}, separators=(",", ":")).encode()
        body = http_ece.encrypt(payload, private_key=ec.generate_private_key(ec.SECP256R1()),
                                dh=decode_url64(subscription["keys"]["p256dh"], 65),
                                auth_secret=decode_url64(subscription["keys"]["auth"], 16),
                                version="aes128gcm")
        endpoint = subscription["endpoint"]
        url = urlsplit(endpoint)
        claims = {"aud": f"https://{url.hostname}", "sub": self.config.contact, "exp": int(time.time()) + 3600}
        token = jwt.encode({"alg": "ES256", "typ": "JWT"}, claims, self._signing_key, algorithms=["ES256"])
        headers = {"Authorization": f"vapid t={token}, k={self.config.public_key}",
                   "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
                   # Visible tests and charging changes should attempt immediate delivery,
                   # including while a battery-powered device is locked (RFC8030 §5.3).
                   "TTL": "60", "Urgency": "high", "Topic": "pulse-" + kind.replace("_", "-")}
        return endpoint, headers, body
