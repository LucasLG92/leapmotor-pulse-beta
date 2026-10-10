"""Private, bounded session serialization; no SDK import or certificate-file reads."""

import base64
from datetime import datetime
import json
from pathlib import Path


_FIELDS = frozenset({"version", "state", "context", "token", "user_id", "device_id", "key",
                     "client_cert", "expires_at", "refresh_token", "refresh_expires_at"})
_MAX_BYTES = 128 * 1024


def _instant(value, *, optional=False):
    if value is None and optional:
        return None
    if (not isinstance(value, datetime) or value.tzinfo is None
            or value.utcoffset() is None):
        raise ValueError("Invalid session timestamp")
    return value.isoformat()


def _paths(values):
    if not isinstance(values, (tuple, list)) or len(values) != 2:
        raise ValueError("Invalid certificate references")
    paths = []
    for value in values:
        if not isinstance(value, (str, Path)):
            raise ValueError("Invalid certificate references")
        text = str(value)
        path = Path(text)
        if (not path.is_absolute() or ".." in path.parts or len(text) > 4096
                or any(ord(character) < 32 or ord(character) == 127 for character in text)):
            raise ValueError("Invalid certificate references")
        paths.append(path)
    return tuple(paths)


def encode_session(session, context):
    paths = _paths(session.client_cert)
    data = {
        "version": 1, "state": "ready", "context": context, "token": session.token,
        "user_id": session.user_id, "device_id": session.device_id,
        "key": base64.b64encode(session.key).decode("ascii"),
        "client_cert": [str(path) for path in paths],
        "expires_at": _instant(session.expires_at),
        "refresh_token": session.refresh_token,
        "refresh_expires_at": _instant(session.refresh_expires_at, optional=True),
    }
    payload = json.dumps(data, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()
    if len(payload) > _MAX_BYTES:
        raise ValueError("Session payload too large")
    return payload


def encode_pending(context, state):
    if state not in {"acquisition_pending", "renewal_pending"}:
        raise ValueError("Invalid session operation")
    return json.dumps({"version": 1, "state": state, "context": context},
                      separators=(",", ":")).encode()


def _unique(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise ValueError("Duplicate session field")
        data[key] = value
    return data


def _date(value, *, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, str) or len(value) > 80:
        raise ValueError("Invalid session timestamp")
    date = datetime.fromisoformat(value)
    _instant(date)
    return date


def decode_session(payload, context, session_type):
    if type(payload) is not bytes or not 0 < len(payload) <= _MAX_BYTES:
        raise ValueError("Invalid session payload")
    data = json.loads(payload, object_pairs_hook=_unique)
    if (not isinstance(data, dict) or set(data) != _FIELDS
            or type(data["version"]) is not int or data["version"] != 1
            or data["context"] != context or data["state"] != "ready"):
        raise ValueError("Invalid session binding")
    if not isinstance(data["key"], str):
        raise ValueError("Invalid session key")
    key = base64.b64decode(data["key"], validate=True)
    if len(key) != 32:
        raise ValueError("Invalid session key")
    return session_type(
        token=data["token"], user_id=data["user_id"], device_id=data["device_id"], key=key,
        client_cert=_paths(data["client_cert"]), expires_at=_date(data["expires_at"]),
        refresh_token=data["refresh_token"],
        refresh_expires_at=_date(data["refresh_expires_at"], optional=True),
    )
