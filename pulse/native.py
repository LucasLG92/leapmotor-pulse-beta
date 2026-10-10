"""Enlace de una app autenticada y autorización exclusiva del snapshot."""

from datetime import datetime, timezone
import re

from fastapi import Request, Security
from fastapi.security import HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse

from pulse.contracts import (NativeAppSessionResponse, NativeCapabilities, WidgetPairingResult,
                             WidgetGrantList, WidgetGrantResult, WidgetSnapshot)
from pulse.domain import DomainError
from pulse.native_auth import NativeAccessValidator, bearer_token
from pulse.native_snapshot import widget_snapshot

_GRANT_ROUTE = re.compile(r"/api/native/widget-grants/([a-f0-9]{32})(/snapshot)?")
_OAUTH = HTTPBearer(auto_error=False, scheme_name="NativeOAuthAccess",
                    description="Access token introspeccionado, audience Pulse y scope pulse.widget.read; solo el canje.")
_APP_OAUTH = HTTPBearer(auto_error=False, scheme_name="NativeAppOAuthAccess",
                         description="Access token introspeccionado, audience Pulse y scope pulse.app.access; solo el bootstrap de sesión.")
_WIDGET = HTTPBearer(auto_error=False, scheme_name="WidgetReadPermission",
                     description="Credencial opaca exclusiva del perfil; solo snapshot y desvinculación propia.")


class NativeRegistrationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    pairing_code: str = Field(min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]+$")
    installation_id: str = Field(min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]+$")
    device_name: str = Field(min_length=1, max_length=60)


class NativeEmptyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class NativeGateway:
    def __init__(self, config, coordinator, *, transport=None):
        self.config, self.coordinator = config, coordinator
        self.access = NativeAccessValidator(config, transport=transport)

    async def authorize(self, request: Request):
        self.access.require_enabled()
        path, method = request.url.path, request.method
        if path == "/api/native/app-sessions":
            if method != "POST":
                raise DomainError("Recurso no disponible.", 404)
            if not self.config.native_app_enabled:
                raise DomainError("El acceso de la app nativa aún no está configurado.", 503)
            request.scope["pulse.native_app_identity"] = await self.access.app_identity(bearer_token(request))
            return
        token = bearer_token(request)
        if path == "/api/native/widget-grants" and method == "POST":
            request.scope["pulse.native_identity"] = await self.access.identity(token)
            return
        match = _GRANT_ROUTE.fullmatch(path)
        if not match or (method, bool(match[2])) not in {("GET", True), ("DELETE", False)}:
            raise DomainError("Recurso no disponible.", 404)
        if not token.startswith("pw_"):
            raise DomainError("El permiso de acceso no es válido o ha caducado.", 401)
        context = await self.coordinator.call(
            "widget_snapshot_context" if method == "GET" else "widget_grant_context", token, match[1])
        if (context["issuer"] != self.config.oidc_issuer.rstrip("/")
                or context["client_id"] != self.config.native_client_id):
            raise DomainError("El permiso de acceso no es válido o ha caducado.", 401)
        request.scope["pulse.native_context"] = context


def register_native(app, config, coordinator, gateway, owner_id, sessions):
    @app.get("/api/native-capabilities", response_model=NativeCapabilities)
    async def capabilities():
        return {"enabled": config.native_enabled,
                "reason_code": "configured" if config.native_enabled else "not_configured"}

    @app.post("/api/native/app-sessions", response_model=NativeAppSessionResponse)
    async def app_session(request: Request, values: NativeEmptyPayload, credential=Security(_APP_OAUTH)):
        identity = request.scope["pulse.native_app_identity"]
        ttl = min(30 * 60, config.session_ttl)
        sid, record = sessions.create(identity.session_user(), ttl=ttl)
        # No se adopta ni revoca la cookie anterior: el token OAuth solo autentica
        # este canje y la aplicación recibe una sesión BFF nueva, acotada y opaca.
        request.scope.update({"pulse.session": record, "session": record.transient})
        response = JSONResponse({"ok": True, "expires_in": ttl})
        response.set_cookie(config.cookie_name, sid, max_age=ttl, httponly=True,
                            secure=config.auth_mode == "oidc", samesite="lax", path="/")
        return response

    @app.post("/api/widget-profiles/{profile_id}/pairings", response_model=WidgetPairingResult)
    async def pairing(request: Request, profile_id: str, values: NativeEmptyPayload):
        gateway.access.require_enabled()
        return await coordinator.call("create_widget_pairing", owner_id(request), profile_id, write=True)

    @app.get("/api/widget-profiles/{profile_id}/native-grants", response_model=WidgetGrantList)
    async def grants(request: Request, profile_id: str):
        return await coordinator.call("list_widget_grants", owner_id(request), profile_id)

    @app.delete("/api/widget-profiles/{profile_id}/native-grants/{grant_id}")
    async def revoke(request: Request, profile_id: str, grant_id: str, values: NativeEmptyPayload):
        return await coordinator.call("revoke_widget_grant", owner_id(request), profile_id, grant_id, write=True)

    @app.post("/api/native/widget-grants", response_model=WidgetGrantResult)
    async def registration(request: Request, values: NativeRegistrationPayload, credential=Security(_OAUTH)):
        identity = request.scope["pulse.native_identity"]
        return await coordinator.call("register_widget_grant", identity.storage_identity(),
                                      values.pairing_code, values.installation_id, values.device_name, write=True)

    @app.get("/api/native/widget-grants/{grant_id}/snapshot", response_model=WidgetSnapshot)
    async def snapshot(request: Request, grant_id: str, credential=Security(_WIDGET)):
        return widget_snapshot(request.scope["pulse.native_context"], now=datetime.now(timezone.utc))

    @app.delete("/api/native/widget-grants/{grant_id}")
    async def unlink(request: Request, grant_id: str, values: NativeEmptyPayload, credential=Security(_WIDGET)):
        return await coordinator.call("revoke_native_widget_grant", bearer_token(request), grant_id, write=True)
