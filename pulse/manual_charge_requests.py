"""Helpers for short-lived, server-reserved manual charge creation attempts."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping


REQUEST_TTL_DAYS = 7
MAX_REQUESTS_PER_OWNER = 128
MAX_REQUESTS_GLOBAL = 2_048
_REQUEST_ID_RE = re.compile(r"[0-9a-f]{32}\Z")


def valid_request_id(value: Any) -> bool:
    return isinstance(value, str) and _REQUEST_ID_RE.fullmatch(value) is not None


def attempt_fingerprint(canonical_attempt: Mapping[str, Any]) -> str:
    """Hash canonical domain values without retaining any raw request payload."""
    encoded = json.dumps(
        dict(canonical_attempt),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
