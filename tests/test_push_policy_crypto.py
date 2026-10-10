"""Interoperabilidad y límites de avisos con material efímero; ninguna red."""

from datetime import datetime, timedelta, timezone
import json
import os
import time
from types import SimpleNamespace

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
import http_ece
from joserfc import jwt
from joserfc.jwk import ECKey
import pytest

from pulse.push_crypto import PushCrypto
from pulse.push_policy import ChargingChangeDetector, PushConfig, encode_url64, validate_subscription


def material():
    private = ec.generate_private_key(ec.SECP256R1())
    config = PushConfig(enabled=True,
        vapid_private_key=encode_url64(private.private_numbers().private_value.to_bytes(32, "big")),
        storage_key=Fernet.generate_key().decode(), contact="mailto:operator@example.org")
    recipient = ec.generate_private_key(ec.SECP256R1())
    auth = os.urandom(16)
    subscription = {"endpoint": "https://fcm.googleapis.com/wp/test-only-endpoint",
        "keys": {"p256dh": encode_url64(recipient.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)), "auth": encode_url64(auth)}}
    return config, recipient, auth, subscription


@pytest.mark.parametrize("kind", ["test", "charging_observation"])
def test_vapid_signature_and_browser_decryption_are_interoperable(kind):
    config, recipient, auth, subscription = material()
    crypto = PushCrypto(config)
    protected = crypto.protect(subscription)
    endpoint, headers, body = crypto.request(protected, kind)
    assert endpoint == subscription["endpoint"]
    assert json.loads(http_ece.decrypt(body, private_key=recipient, auth_secret=auth, version="aes128gcm")) == {"version": 1, "kind": kind}
    authorization = headers["Authorization"]
    token, public = authorization.removeprefix("vapid t=").split(", k=")
    from pulse.push_policy import decode_url64
    public_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), decode_url64(public, 65))
    decoded = jwt.decode(token, ECKey.import_key(public_key), algorithms=["ES256"])
    assert decoded.header == {"alg": "ES256", "typ": "JWT"}
    assert decoded.claims["aud"] == "https://fcm.googleapis.com"
    assert decoded.claims["sub"] == "mailto:operator@example.org"
    assert time.time() < decoded.claims["exp"] <= time.time() + 3601
    assert headers["TTL"] == "60" and headers["Content-Encoding"] == "aes128gcm"
    assert headers["Urgency"] == "high"
    assert headers["Topic"] == "pulse-" + kind.replace("_", "-")
    assert public == config.public_key
    # RFC8188: la clave efímera de contenido ocupa el keyid del cuerpo, no VAPID.
    assert encode_url64(body[21:21 + body[20]]) != public
    assert subscription["endpoint"] not in protected
    assert subscription["keys"]["auth"] not in protected
    with pytest.raises(InvalidToken):
        wrong, _, _, _ = material()
        PushCrypto(wrong).open(protected)
    with pytest.raises(ValueError):
        crypto.request(protected, "unlock")


def test_disabled_configuration_does_not_read_keys(monkeypatch):
    class DisabledEnvironment:
        def get(self, name, default=None):
            assert name == "PULSE_PUSH_ENABLED"
            return "false"
    monkeypatch.setattr("pulse.push_policy.os", SimpleNamespace(environ=DisabledEnvironment()))
    assert PushConfig.from_env() == PushConfig()
    assert PushConfig().public_key is None
    enabled, _, _, _ = material()
    representation = repr(enabled)
    assert enabled.vapid_private_key not in representation
    assert enabled.storage_key not in representation
    assert enabled.contact not in representation
    with pytest.raises(ValueError):
        PushConfig(enabled=True)


@pytest.mark.parametrize("endpoint", [
    "http://fcm.googleapis.com/wp/x", "https://127.0.0.1/push", "https://localhost/push",
    "https://fcm.googleapis.com.evil.example/wp/x", "https://evil.example/fcm.googleapis.com",
    "https://user@fcm.googleapis.com/wp/x", "https://fcm.googleapis.com:443/wp/x",
    "https://fcm.googleapis.com/wp/x#fragment", "https://fcm.googleapis.com/",
    "https://fcm.googleapis.com/wp/x\n", "https://fcm.googleapis.com\\@evil.example/x",
    "https://push.apple.com/x", "https://evil.notify.windows.com/x",
])
def test_arbitrary_or_ambiguous_destinations_rejected(endpoint):
    _, _, _, subscription = material()
    subscription["endpoint"] = endpoint
    with pytest.raises(ValueError) as error:
        validate_subscription(subscription)
    assert endpoint not in str(error.value)


@pytest.mark.parametrize("endpoint", ["https://fcm.googleapis.com/wp/x?opaque=1",
    "https://updates.push.services.mozilla.com/wpush/v2/x", "https://web.push.apple.com/Q/x"])
def test_documented_services_accept_valid_curve_keys(endpoint):
    _, _, _, subscription = material()
    subscription["endpoint"] = endpoint
    assert validate_subscription(subscription) == subscription
    subscription["keys"]["p256dh"] = encode_url64(bytes(65))
    with pytest.raises(ValueError):
        validate_subscription(subscription)


def reading(now, charging, **updates):
    return {"source": "MATE-API B10ReadClient", "observed_at": now.isoformat(),
            "vehicle": {"charging": charging}} | updates


def test_only_new_adjacent_provider_observations_produce_a_change():
    now = datetime.now(timezone.utc)
    detector = ChargingChangeDetector()
    assert not detector.observe(reading(now - timedelta(seconds=30), False), now, demo=False)
    assert detector.observe(reading(now - timedelta(seconds=20), True), now, demo=False)
    assert not detector.observe(reading(now - timedelta(seconds=25), False), now, demo=False)
    assert not detector.observe(reading(now - timedelta(seconds=10), True), now, demo=False)
    assert detector.observe(reading(now, False), now, demo=False)
    assert not detector.observe(reading(now, True), now, demo=True)
    assert not detector.observe(reading(now + timedelta(seconds=1), True), now + timedelta(seconds=1), demo=False)
    assert not detector.observe(reading(now + timedelta(seconds=700), False), now + timedelta(seconds=700), demo=False)


@pytest.mark.parametrize("updates,charging", [({"source": "demo"}, True),
    ({"observed_at": None}, True), ({"observed_at": "2026-10-08T12:00:00"}, True),
    ({}, "true"), ({}, None)])
def test_unqualified_observation_resets_baseline(updates, charging):
    now = datetime.now(timezone.utc)
    detector = ChargingChangeDetector()
    detector.observe(reading(now - timedelta(seconds=1), False), now, demo=False)
    assert not detector.observe(reading(now, charging, **updates), now, demo=False)
    assert not detector.observe(reading(now + timedelta(seconds=1), True), now + timedelta(seconds=1), demo=False)


@pytest.mark.parametrize("age", [-1, 301])
def test_future_and_stale_values_do_not_trigger(age):
    now = datetime.now(timezone.utc)
    detector = ChargingChangeDetector()
    detector.observe(reading(now - timedelta(seconds=30), False), now, demo=False)
    assert not detector.observe(reading(now - timedelta(seconds=age), True), now, demo=False)
