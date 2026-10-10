"""Avisos Web Push con permiso explícito, sesión vigente y entrega acotada."""

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import time

from fastapi import Request
import httpx2 as httpx

from pulse.domain import DomainError
from pulse.push_contracts import (PushCapabilities, PushDevice, PushDeviceList, PushPayload,
                                   PushRegister, PushTest, PushTestResult)
from pulse.push_crypto import PushCrypto
from pulse.push_policy import ChargingChangeDetector, PushConfig, validate_subscription


def _now():
    return datetime.now(timezone.utc)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class HttpPushTransport:
    def __init__(self):
        self.client = None

    async def start(self):
        self.client = httpx.AsyncClient(trust_env=False, follow_redirects=False, verify=True,
                                       timeout=httpx.Timeout(5, connect=3),
                                       limits=httpx.Limits(max_connections=2, max_keepalive_connections=2))

    async def send(self, endpoint: str, headers: dict, body: bytes) -> int:
        async with asyncio.timeout(8):
            async with self.client.stream("POST", endpoint, headers=headers, content=body) as response:
                # No reflejar ni cargar cuerpos de error de un servicio externo.
                return response.status_code

    async def close(self):
        if self.client:
            await self.client.aclose()
            self.client = None


class PushService:
    def __init__(self, config: PushConfig, coordinator, sessions, cookie_name: str, *, transport=None):
        self.config, self.coordinator, self.sessions, self.cookie_name = config, coordinator, sessions, cookie_name
        self.crypto = PushCrypto(config) if config.enabled else None
        self.transport = transport if transport is not None else HttpPushTransport()
        self._observations = asyncio.Queue(maxsize=1)
        self._detector = ChargingChangeDetector()
        self._task = None
        self._sending = asyncio.Lock()
        self._test_cooldown: dict[str, float] = {}
        self._closed = False

    def capabilities(self):
        return {"enabled": self.config.enabled, "reason_code": "available" if self.config.enabled else "disabled",
                "application_server_key": self.config.public_key, "privacy_mode": "generic", "max_devices": 8, "scope": "pwa"}

    async def start(self):
        if not self.config.enabled:
            return
        await self.transport.start()
        self._task = asyncio.create_task(self._watch_observations(), name="pulse-push-observations")

    def observe(self, snapshot):
        """Solo lo llama Collector después de guardar una lectura del proveedor."""
        if not self.config.enabled or self._closed or self._task is None:
            return
        if self._observations.full():
            self._observations.get_nowait()
        self._observations.put_nowait(snapshot)

    def _session_active(self, row: dict) -> bool:
        try:
            expiry = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
            if expiry <= _now():
                return False
            return any(record.user and record.user["id"] == row["owner_id"]
                       and _hash(sid) == row["session_hash"] and self.sessions.active(record)
                       for sid, record in tuple(self.sessions.records.items()))
        except (ValueError, TypeError, KeyError):
            return False

    def _public(self, row: dict) -> dict:
        active = self.config.enabled and self._session_active(row)
        fields = ("id", "name", "endpoint_hash", "created_at", "updated_at", "expires_at",
                  "last_attempt_at", "last_accepted_at", "last_result")
        return {**{key: row[key] for key in fields}, "session_active": bool(active),
                "state": "active" if active else "session_required"}

    async def devices(self, owner: str):
        rows = await self.coordinator.call("list_push_subscriptions", owner)
        return {"items": [self._public(row) for row in rows], "max_devices": 8}

    async def register(self, request: Request, owner: str, values: PushRegister):
        if not self.config.enabled or self._closed:
            raise DomainError("Los avisos aún no están configurados en el servidor.", 503)
        try:
            subscription = validate_subscription(values.subscription.model_dump())
            record = request.scope["pulse.session"]
            sid = request.cookies.get(self.cookie_name, "")
            if not sid or self.sessions.records.get(sid) is not record or not self.sessions.active(record):
                raise DomainError("Inicia sesión de nuevo para activar los avisos.", 401)
            ttl = self.sessions.config.session_ttl
            if record.ttl is not None:
                ttl = min(ttl, record.ttl)
            remaining = ttl - (time.monotonic() - record.created)
            expiry = _now() + timedelta(seconds=remaining)
            browser_expiry = values.subscription.expirationTime
            if browser_expiry is not None:
                expiry = min(expiry, datetime.fromtimestamp(browser_expiry / 1000, timezone.utc))
            if expiry <= _now():
                raise DomainError("La suscripción ha caducado. Actívala de nuevo.", 410)
            protected = self.crypto.protect(subscription)
        except DomainError:
            raise
        except Exception:
            raise DomainError("La suscripción o su servicio de avisos no son válidos o no están admitidos.", 422) from None
        row = await self.coordinator.call("upsert_push_subscription", owner, _hash(subscription["endpoint"]),
                                          _hash(sid), values.name, protected,
                                          expiry.isoformat().replace("+00:00", "Z"), write=True)
        return self._public(row)

    async def revoke(self, owner: str, item_id: str):
        return await self.coordinator.call("delete_push_subscription", owner, item_id, write=True)

    async def test(self, owner: str, item_id: str, *, confirmed: bool):
        if not confirmed:
            raise DomainError("Confirma que quieres enviar un aviso de prueba.")
        if not self.config.enabled or self._closed:
            raise DomainError("Los avisos aún no están configurados en el servidor.", 503)
        row = await self.coordinator.call("get_push_subscription", owner, item_id)
        if not self._session_active(row):
            raise DomainError("Inicia sesión y reactiva los avisos de ese dispositivo.", 409)
        if self._sending.locked():
            raise DomainError("Hay un aviso en curso. Espera antes de enviar otro.", 409)
        now = time.monotonic()
        self._test_cooldown = {key: until for key, until in self._test_cooldown.items() if until > now}
        if self._test_cooldown.get(owner, 0) > now:
            raise DomainError("Espera un minuto antes de enviar otra prueba.", 429)
        self._test_cooldown[owner] = now + 60
        async with self._sending:
            await self._send(row, "test")
        return {"status": "accepted", "detail": "El servicio de avisos aceptó la prueba. Comprueba el dispositivo."}

    async def _send(self, row: dict, kind: str):
        if self._closed or not self._session_active(row):
            raise DomainError("La sesión de avisos ha caducado. Reactívala en Pulse.", 409)
        reserved = await self.coordinator.call("reserve_push_attempt", row["owner_id"], row["id"], row["revision"], write=True)
        try:
            endpoint, headers, body = await asyncio.to_thread(self.crypto.request, reserved["ciphertext"], kind)
            if kind != "test" and await self.coordinator.call("push_demo_enabled"):
                raise DomainError("La demo no genera avisos del coche.", 409)
            # Volver a verificar revocación/generación/sesión después del cifrado.
            current = await self.coordinator.call("get_push_subscription", row["owner_id"], row["id"])
            if current["revision"] != row["revision"] or self._closed or not self._session_active(current):
                raise DomainError("El permiso de avisos ha cambiado. Vuelve a consultarlo.", 409)
            async with asyncio.timeout(8):
                status = await self.transport.send(endpoint, headers, body)
        except asyncio.CancelledError:
            # La reserva queda pending: un cierre no justifica repetir el envío.
            raise
        except DomainError:
            await self.coordinator.call("finish_push_attempt", row["owner_id"], row["id"], row["revision"], "failed", write=True)
            raise
        except Exception:
            await self.coordinator.call("finish_push_attempt", row["owner_id"], row["id"], row["revision"], "uncertain", write=True)
            raise DomainError("No se pudo confirmar el envío. No se repetirá automáticamente.", 503) from None
        if status in {404, 410}:
            # Revocar únicamente la generación que realmente produjo este resultado.
            await self.coordinator.call("delete_push_subscription", row["owner_id"], row["id"],
                                         expected_revision=row["revision"], write=True)
            raise DomainError("Ese dispositivo ya no admite avisos. Actívalo de nuevo.", 410)
        result = "accepted" if 200 <= status < 300 else "failed"
        await self.coordinator.call("finish_push_attempt", row["owner_id"], row["id"], row["revision"], result, write=True)
        if result != "accepted":
            raise DomainError("El servicio de avisos rechazó el envío. Revisa la configuración antes de reintentar.", 503)

    async def _watch_observations(self):
        while True:
            snapshot = await self._observations.get()
            try:
                demo = await self.coordinator.call("push_demo_enabled")
                if not self._detector.observe(snapshot, _now(), demo=demo):
                    continue
                async with self._sending:
                    for row in await self.coordinator.call("list_push_subscriptions"):
                        if self._session_active(row):
                            try:
                                await self._send(row, "charging_observation")
                            except DomainError:
                                pass  # Estado de intento persistido; no reenvío de ese aviso.
            except asyncio.CancelledError:
                raise
            except Exception:
                self._detector.previous = None

    async def close(self):
        self._closed = True
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        if self.config.enabled:
            await self.transport.close()


def register_push(app, service: PushService, owner_id):
    @app.get("/api/push/capabilities", response_model=PushCapabilities)
    async def capabilities(request: Request):
        owner_id(request)
        return service.capabilities()

    @app.get("/api/push/subscriptions", response_model=PushDeviceList)
    async def devices(request: Request):
        return await service.devices(owner_id(request))

    @app.post("/api/push/subscriptions", response_model=PushDevice)
    async def subscribe(request: Request, values: PushRegister):
        return await service.register(request, owner_id(request), values)

    @app.delete("/api/push/subscriptions/{item_id}")
    async def revoke(request: Request, item_id: str, values: PushPayload):
        return await service.revoke(owner_id(request), item_id)

    @app.post("/api/push/subscriptions/{item_id}/test", response_model=PushTestResult)
    async def test(request: Request, item_id: str, values: PushTest):
        return await service.test(owner_id(request), item_id, confirmed=values.confirmed)
