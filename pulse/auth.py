"""Sesiones opacas en memoria: ninguna credencial OIDC llega al navegador."""

from dataclasses import dataclass, field
import hmac
import ipaddress
import secrets
import time
from typing import Any

from authlib.integrations.starlette_client import OAuth
from fastapi import Request
from starlette.responses import JSONResponse, RedirectResponse

from pulse.config import Config
from pulse.contracts import SessionResponse
from pulse.domain import DomainError


@dataclass
class Session:
    csrf: str = field(default_factory=lambda: secrets.token_urlsafe(32), repr=False)
    created: float = field(default_factory=time.monotonic)
    user: dict[str, str] | None = field(default=None, repr=False)
    transient: dict[str, Any] = field(default_factory=dict, repr=False)
    valid: bool = True
    ttl: int | None = None


class Sessions:
    """Acceso únicamente desde el event loop; tamaño y vida absolutos limitados."""

    def __init__(self, config: Config):
        self.config = config
        self.records: dict[str, Session] = {}

    def get(self, sid: str) -> Session | None:
        record = self.records.get(sid)
        if record and self.active(record):
            return record
        self.delete(sid)
        return None

    def active(self, record: Session) -> bool:
        ttl = record.ttl
        if ttl is None:
            ttl = self.config.session_ttl if record.user else min(600, self.config.session_ttl)
        else:
            ttl = min(ttl, self.config.session_ttl)
        return record.valid and time.monotonic() - record.created < ttl

    def create(self, user: dict[str, str] | None = None, *, ttl: int | None = None) -> tuple[str, Session]:
        if ttl is not None and (type(ttl) is not int or not 1 <= ttl <= self.config.session_ttl):
            raise ValueError("La duración de sesión debe estar dentro del límite configurado.")
        for sid in list(self.records):
            self.get(sid)
        if len(self.records) >= self.config.max_sessions:
            anonymous = [(sid, record) for sid, record in self.records.items() if record.user is None]
            if anonymous:
                self.delete(min(anonymous, key=lambda item: item[1].created)[0])
            else:
                raise DomainError("Hay demasiadas sesiones abiertas. Inténtalo más tarde.", 429)
        sid = secrets.token_urlsafe(32)
        record = Session(user=user, ttl=ttl)
        self.records[sid] = record
        return sid, record

    def delete(self, sid: str) -> None:
        record = self.records.pop(sid, None)
        if record:
            record.valid = False
            record.transient.clear()

    def rotate(self, request: Request, user: dict[str, str]):
        self.delete(request.cookies.get(self.config.cookie_name, ""))
        sid, record = self.create(user)
        request.scope["pulse.session"] = record
        request.scope["session"] = record.transient
        request.scope["pulse.cookie"] = sid


def local_peer(scope: dict) -> bool:
    peer = scope.get("client", ("", 0))[0]
    try:
        loopback = ipaddress.ip_address(peer).is_loopback
    except ValueError:
        loopback = peer == "testclient"
    headers = {key.lower() for key, _ in scope.get("headers", [])}
    return loopback and not headers.intersection({
        b"forwarded", b"x-forwarded-for", b"x-forwarded-host", b"x-forwarded-proto", b"remote-user",
    })


def authenticated(request: Request, config: Config, sessions: Sessions) -> bool:
    if config.auth_mode == "local":
        return local_peer(request.scope)
    record = request.scope.get("pulse.session")
    return bool(record and record.user and sessions.active(record))


def build_oidc(config: Config):
    if config.auth_mode != "oidc":
        return None
    oauth = OAuth()
    return oauth.register(
        name="pulse", client_id=config.oidc_client_id, client_secret=config.oidc_client_secret,
        server_metadata_url=config.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration",
        client_kwargs={"scope": "openid profile email", "code_challenge_method": "S256", "timeout": 15},
    )


def register_auth(app, config: Config, sessions: Sessions, oidc):
    @app.get("/api/session", response_model=SessionResponse)
    async def session(request: Request):
        record = request.scope.get("pulse.session")
        is_authenticated = authenticated(request, config, sessions)
        return {"csrf_token": record.csrf, "authenticated": is_authenticated,
                "user": ({"name": "Usuario local", "id": "local"} if config.auth_mode == "local" and is_authenticated
                         else record.user),
                "login_url": "/api/auth/login" if config.auth_mode == "oidc" else None}

    @app.get("/api/auth/login")
    async def login(request: Request):
        if oidc is None:
            return RedirectResponse("/", status_code=303)
        # Una sola autorización pendiente por sesión; no aceptar redirecciones del cliente.
        request.session.clear()
        try:
            metadata = await oidc.load_server_metadata()
            if metadata.get("issuer") != config.oidc_issuer.rstrip("/"):
                raise ValueError("Issuer distinto")
            response = await oidc.authorize_redirect(request, config.callback_url)
            request.session["pulse.login_started"] = time.monotonic()
            return response
        except Exception:
            request.session.clear()
            return JSONResponse({"detail": "No se pudo iniciar la autenticación."}, status_code=503)

    @app.get("/api/auth/callback")
    async def callback(request: Request):
        if oidc is None:
            raise DomainError("Autenticación remota no configurada.", 404)
        started = request.session.get("pulse.login_started", 0)
        if not started or time.monotonic() - started > 600:
            request.session.clear()
            raise DomainError("La solicitud de acceso ha caducado.", 401)
        try:
            token = await oidc.authorize_access_token(request, claims_options={
                "iss": {"essential": True, "value": config.oidc_issuer.rstrip("/")},
                "aud": {"essential": True, "value": config.oidc_client_id},
            }, leeway=30)
            # Authlib entrega userinfo solo tras verificar ID token, nonce, firma y claims.
            claims = token.get("userinfo")
            if not token.get("id_token") or not claims or not claims.get("sub"):
                raise ValueError("Respuesta OIDC incompleta")
            profile = await oidc.userinfo(token=token)
            if not profile or profile.get("sub") != claims["sub"]:
                raise ValueError("UserInfo no coincide con la identidad verificada")
            username = profile.get("preferred_username")
            if username not in config.allowed_users:
                request.session.clear()
                raise DomainError("Tu usuario no tiene acceso a Pulse.", 403)
            user = {"id": str(claims["sub"]), "name": str(profile.get("name") or username)}
        except DomainError:
            raise
        except Exception:
            request.session.clear()
            raise DomainError("No se pudo verificar el acceso. Inicia sesión de nuevo.", 401) from None
        request.session.clear()
        sessions.rotate(request, user)
        return RedirectResponse("/", status_code=303)

    @app.post("/api/auth/logout")
    async def logout(request: Request):
        sessions.delete(request.cookies.get(config.cookie_name, ""))
        request.scope["pulse.cookie"] = ""
        # Cierra Pulse. La sesión de Authelia es independiente y no se manipula aquí.
        return {"ok": True}
