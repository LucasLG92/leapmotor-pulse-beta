"""Access token nativo validado por introspección; no tokens en sesión o logs."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import math
import re
import time
import unicodedata
from urllib.parse import quote_plus

import httpx2 as httpx
from starlette.requests import Request

from pulse.config import Config
from pulse.domain import DomainError
from pulse.widget_profiles import validate_owner_id, WidgetProfileValidationError

WIDGET_SCOPE = "pulse.widget.read"
APP_SCOPES = frozenset({"openid", "profile", "pulse.app.access"})
WIDGET_SCOPES = frozenset({"openid", WIDGET_SCOPE})
MAX_INTROSPECTION_BYTES = 64 * 1024
_BEARER = re.compile(r"Bearer ([A-Za-z0-9._~+/-]+={0,2})", re.IGNORECASE)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _constant(value):
    raise ValueError


@dataclass(frozen=True)
class NativeIdentity:
    issuer: str
    subject: str = field(repr=False)
    client_id: str

    def storage_identity(self):
        return {"issuer": self.issuer, "subject": self.subject, "client_id": self.client_id}


@dataclass(frozen=True)
class NativeAppIdentity:
    issuer: str
    subject: str = field(repr=False)
    username: str = field(repr=False)
    name: str = field(repr=False)

    def session_user(self):
        return {"id": self.subject, "name": self.name}


def bearer_token(request: Request) -> str:
    headers = [value for key, value in request.scope["headers"] if key.lower() == b"authorization"]
    if len(headers) != 1:
        raise DomainError("El permiso de acceso no es válido o ha caducado.", 401)
    try:
        value = headers[0].decode("ascii")
    except UnicodeDecodeError:
        raise DomainError("El permiso de acceso no es válido o ha caducado.", 401) from None
    match = _BEARER.fullmatch(value)
    if not match or not 1 <= len(match[1]) <= 4096:
        raise DomainError("El permiso de acceso no es válido o ha caducado.", 401)
    return match[1]


class NativeAccessValidator:
    """El canje del widget y el bootstrap de la app validan OAuth por scopes separados."""

    def __init__(self, config: Config, *, transport=None, clock=time.time):
        self.config, self.transport, self.clock = config, transport, clock
        self.slots = asyncio.Semaphore(2)

    def require_enabled(self):
        if not self.config.native_enabled:
            raise DomainError("El enlace con dispositivos nativos aún no está configurado.", 503)

    async def identity(self, token: str) -> NativeIdentity:
        identity, _ = await self._validated(token, WIDGET_SCOPES)
        return identity

    async def app_identity(self, token: str) -> NativeAppIdentity:
        if not self.config.native_app_enabled:
            raise DomainError("El acceso de la app nativa aún no está configurado.", 503)
        identity, profile = await self._validated(token, APP_SCOPES)
        username = profile.get("preferred_username") if isinstance(profile, dict) else None
        if type(username) is not str or username not in self.config.allowed_users:
            raise DomainError("Tu usuario no tiene acceso a Pulse.", 403)
        name = _safe_display_name(profile.get("name"), username)
        return NativeAppIdentity(identity.issuer, identity.subject, username, name)

    async def _validated(self, token: str, expected_scopes: frozenset[str]):
        self.require_enabled()
        if token.startswith("pw_"):
            raise DomainError("El permiso de acceso no es válido o ha caducado.", 401)
        acquired = False
        try:
            try:
                await asyncio.wait_for(self.slots.acquire(), timeout=0.05)
                acquired = True
            except TimeoutError:
                raise DomainError("Hay demasiados enlaces en curso. Reintenta en unos segundos.", 429) from None
            async with asyncio.timeout(8):
                async with httpx.AsyncClient(
                    transport=self.transport, trust_env=False, follow_redirects=False,
                    timeout=httpx.Timeout(8, connect=3),
                ) as client:
                    claims = await self._json(client,
                        "POST", self.config.native_introspection_url,
                        auth=httpx.BasicAuth(quote_plus(self.config.native_introspection_client_id, safe=""),
                                            quote_plus(self.config.native_introspection_client_secret, safe="")),
                        headers={"Accept": "application/json"},
                        data={"token": token, "token_type_hint": "access_token"},
                    )
                    identity = self._identity(claims, expected_scopes)
                    # UserInfo es un recurso protegido de access token; introspection
                    # puede aceptar también refresh aunque hint sea access_token.
                    profile = await self._json(client, "GET", self.config.native_userinfo_url,
                        unauthorized=True, headers={"Accept": "application/json", "Authorization": "Bearer " + token})
                    if not isinstance(profile, dict) or profile.get("sub") != identity.subject:
                        raise DomainError("El permiso de acceso no es válido o ha caducado.", 401)
        except DomainError:
            raise
        except Exception:
            raise DomainError("No se pudo verificar el acceso del dispositivo. Reintenta más tarde.", 503) from None
        finally:
            if acquired:
                self.slots.release()
        return identity, profile

    async def _json(self, client, method, url, *, unauthorized=False, **kwargs):
        async with client.stream(method, url, **kwargs) as response:
            if unauthorized and response.status_code in (401, 403):
                raise DomainError("El permiso de acceso no es válido o ha caducado.", 401)
            if (response.status_code != 200 or
                    response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json"):
                raise ValueError
            if "content-length" in response.headers:
                length = int(response.headers["content-length"])
                if length < 0 or length > MAX_INTROSPECTION_BYTES:
                    raise ValueError
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_INTROSPECTION_BYTES:
                    raise ValueError
            return json.loads(body, object_pairs_hook=_object, parse_constant=_constant)

    def _identity(self, claims, expected_scopes=WIDGET_SCOPES) -> NativeIdentity:
        config = self.config
        try:
            if not isinstance(claims, dict) or claims.get("active") is not True:
                raise ValueError
            if claims.get("iss") != config.oidc_issuer.rstrip("/"):
                raise ValueError
            if claims.get("client_id") != config.native_client_id:
                raise ValueError
            if "token_type" in claims and str(claims["token_type"]).lower() != "bearer":
                raise ValueError
            audience = claims.get("aud")
            if isinstance(audience, str):
                audience = [audience]
            if (not isinstance(audience, list) or not audience or
                    any(not isinstance(value, str) for value in audience) or
                    audience != [config.native_audience]):
                raise ValueError
            scopes = claims.get("scope")
            if not isinstance(scopes, str) or set(scopes.split()) != expected_scopes:
                raise ValueError
            now = self.clock()
            expiry = claims.get("exp")
            if type(expiry) is not int or not math.isfinite(expiry) or expiry <= now:
                raise ValueError
            for key in ("nbf", "iat"):
                value = claims.get(key)
                if value is not None and (type(value) is not int or value > now + 30 or value >= expiry):
                    raise ValueError
            subject = validate_owner_id(claims.get("sub"))
        except (ValueError, TypeError, OverflowError, WidgetProfileValidationError):
            raise DomainError("El permiso de acceso no es válido o ha caducado.", 401) from None
        return NativeIdentity(config.oidc_issuer.rstrip("/"), subject, config.native_client_id)


def _safe_display_name(value, username: str) -> str:
    """Elimina controles y limita el nombre antes de guardarlo en la sesión."""
    raw = value if type(value) is str else ""
    cleaned = "".join(character for character in raw
                      if unicodedata.category(character) not in {"Cc", "Cf"}).strip()[:128]
    fallback = "".join(character for character in username
                       if unicodedata.category(character) not in {"Cc", "Cf"}).strip()[:128]
    return cleaned or fallback
