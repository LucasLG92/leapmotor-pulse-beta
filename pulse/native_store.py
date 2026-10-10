"""Cryptographic helpers for native widget pairing and opaque grants.

Raw codes and credentials are created for one-time return to a caller. This
module never logs or persists them; Store persists only SHA-256 digests.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
import secrets


_TOKEN_BODY = re.compile(r"[A-Za-z0-9_-]{43}\Z")
PAIRING_TTL_SECONDS = 5 * 60
PAIRING_COOLDOWN_SECONDS = 30
WIDGET_GRANT_TTL_SECONDS = 30 * 24 * 60 * 60
MAX_WIDGET_GRANTS_PER_PROFILE = 5


def new_pairing_code() -> str:
    return secrets.token_urlsafe(32)


def new_widget_token() -> str:
    return "pw_" + secrets.token_urlsafe(32)


def validate_random_256(value: object, *, prefixed: bool = False) -> str:
    """Require the canonical unpadded base64url representation of 32 random bytes."""
    if not isinstance(value, str):
        raise ValueError("token format")
    if prefixed:
        if not value.startswith("pw_"):
            raise ValueError("token format")
        body = value[3:]
    else:
        body = value
    if not _TOKEN_BODY.fullmatch(body):
        raise ValueError("token format")
    try:
        decoded = base64.urlsafe_b64decode(body + "=")
    except (ValueError, binascii.Error):
        raise ValueError("token format") from None
    if len(decoded) != 32 or base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=") != body:
        raise ValueError("token format")
    return value


def digest_secret(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def digest_matches(stored: str, candidate: str) -> bool:
    return secrets.compare_digest(stored, candidate)
