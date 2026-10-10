"""Límites ASGI, CSRF y cabeceras también para las respuestas de rechazo."""

import hmac
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from pulse.auth import authenticated, local_peer
from pulse.domain import DomainError


MAX_BODY = 5 * 1024 * 1024
NATIVE_MAX_BODY = 4096
PUBLIC_API = {"/api/session", "/api/auth/login", "/api/auth/callback"}


class SecurityMiddleware:
    def __init__(self, app, *, config, sessions, native_gateway=None):
        self.app, self.config, self.sessions = app, config, sessions
        self.native_gateway = native_gateway

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)
        path = scope["path"]
        native = path.startswith("/api/native/")
        record = self.sessions.get(request.cookies.get(self.config.cookie_name, ""))
        scope["pulse.session"] = record
        scope["session"] = record.transient if record else {}

        async def secure_send(message):
            if message["type"] == "http.response.start":
                headers = dict(message.get("headers", []))
                headers.update({
                    b"x-content-type-options": b"nosniff", b"referrer-policy": b"no-referrer",
                    b"x-frame-options": b"DENY",
                    b"permissions-policy": b"camera=(), microphone=(), geolocation=()",
                    b"content-security-policy": (
                        b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                        b"font-src 'self'; connect-src 'self'; worker-src 'self'; manifest-src 'self'; "
                        b"object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
                    ),
                    b"cache-control": b"no-store" if path.startswith("/api/") or path in {"/healthz", "/readyz"}
                        else b"public, max-age=31536000, immutable" if path.startswith("/assets/")
                        and message["status"] in {200, 206, 304} else b"no-cache",
                })
                if self.config.auth_mode == "oidc":
                    headers[b"strict-transport-security"] = b"max-age=31536000"
                if "pulse.cookie" in scope:
                    cookie = Response()
                    sid = scope["pulse.cookie"]
                    if sid:
                        cookie.set_cookie(self.config.cookie_name, sid, max_age=self.config.session_ttl,
                            httponly=True, secure=self.config.auth_mode == "oidc", samesite="lax", path="/")
                    else:
                        cookie.delete_cookie(self.config.cookie_name, httponly=True,
                            secure=self.config.auth_mode == "oidc", samesite="lax", path="/")
                    headers[b"set-cookie"] = cookie.raw_headers[-1][1]
                message["headers"] = list(headers.items())
            await send(message)

        async def reject(message, status):
            await JSONResponse({"detail": message}, status_code=status)(scope, receive, secure_send)

        if path.startswith("/api/"):
            if self.config.auth_mode == "local" and not local_peer(scope):
                return await reject("Acceso remoto cerrado. Configura OIDC para acceder desde fuera.", 403)
            if not native and path not in PUBLIC_API and not authenticated(request, self.config, self.sessions):
                return await reject("Inicia sesión para acceder a Pulse.", 401)
        if path in {"/api/session", "/api/auth/login"} and record is None:
            try:
                sid, record = self.sessions.create({"id": "local", "name": "Usuario local"} if self.config.auth_mode == "local" else None)
            except DomainError as error:
                return await reject(str(error), error.status)
            scope.update({"pulse.session": record, "session": record.transient, "pulse.cookie": sid})
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            expected = self.config.public_url.rstrip("/") if self.config.auth_mode == "oidc" else str(request.base_url).rstrip("/")
            if origin is not None and origin != expected:
                return await reject("Origen no permitido.", 403)
            if not native and (not record or not self.sessions.active(record)
                    or not hmac.compare_digest(record.csrf.encode("ascii"), request.headers.get("x-pulse-csrf", "").encode("utf-8"))):
                return await reject("La sesión ha caducado. Recarga la página.", 403)
            if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                return await reject("Se requiere JSON.", 415)
            try:
                length = int(request.headers.get("content-length", "0"))
            except ValueError:
                return await reject("Tamaño de petición inválido.", 400)
            max_body = NATIVE_MAX_BODY if native or path.startswith(("/api/push/", "/api/navigation/")) else MAX_BODY
            if length < 0 or length > max_body:
                return await reject("La petición supera el tamaño permitido.", 413)
            data = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                data.extend(message.get("body", b""))
                if len(data) > max_body:
                    return await reject("La petición supera el tamaño permitido.", 413)
                if not message.get("more_body", False):
                    break
            delivered = False

            original_receive = receive

            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(data), "more_body": False}
                return await original_receive()
            receive = replay
        if native:
            # Las rutas nativas no heredan identidad de cookies ni un bypass CSRF.
            # Cada ruta admite un tipo de permiso explícito, de alcance acotado.
            if self.native_gateway is None:
                return await reject("El enlace con dispositivos nativos aún no está configurado.", 503)
            origin = request.headers.get("origin")
            if origin is not None and origin != self.config.public_url.rstrip("/"):
                return await reject("Origen no permitido.", 403)
            try:
                await self.native_gateway.authorize(request)
            except DomainError as error:
                return await reject(str(error), error.status)
            except Exception:
                return await reject("No se pudo verificar el acceso del dispositivo. Reintenta más tarde.", 503)
        await self.app(scope, receive, secure_send)
