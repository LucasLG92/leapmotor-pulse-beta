"""Avisos Web Push con permiso explícito, sesión vigente y entrega acotada."""

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import logging
import time

from fastapi import Request
import httpx2 as httpx

from pulse.domain import DomainError
from pulse.backup_alerts import BackupAlertsConfig, read_monitor_reports
from pulse.push_contracts import (BackupAlertsConsent, PushCapabilities, PushDevice, PushDeviceList, PushPayload,
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
    def __init__(self, config: PushConfig, coordinator, sessions, cookie_name: str, *, transport=None,
                 backup_config: BackupAlertsConfig | None = None):
        self.config, self.coordinator, self.sessions, self.cookie_name = config, coordinator, sessions, cookie_name
        self.backup_config = backup_config if backup_config is not None else BackupAlertsConfig()
        self.crypto = PushCrypto(config) if config.enabled else None
        self.transport = transport if transport is not None else HttpPushTransport()
        self._observations = asyncio.Queue(maxsize=1)
        self._detector = ChargingChangeDetector()
        self._task = None
        self._backup_task = None
        self._sending = asyncio.Lock()
        self._test_cooldown: dict[str, float] = {}
        self._closed = False

    def capabilities(self, owner: str | None = None):
        return {"enabled": self.config.enabled, "reason_code": "available" if self.config.enabled else "disabled",
                "application_server_key": self.config.public_key, "privacy_mode": "generic", "max_devices": 8, "scope": "pwa",
                "backup_alerts_available": bool(self.config.enabled and self.backup_config.enabled
                                                 and owner == self.backup_config.owner_id)}

    async def start(self):
        if not self.config.enabled and not self.backup_config.enabled:
            return
        if self.backup_config.enabled:
            # Activate/reset the configured scope before the app exposes routes or
            # the first monitor read can race a new consent with scope activation.
            while self.backup_config.enabled:
                selected = self.backup_config
                scope = selected.scope
                await self.coordinator.call("ensure_backup_alert_scope", scope, write=True)
                if (not self.backup_config.enabled
                        or self.backup_config.scope == scope):
                    break
        if self.config.enabled:
            await self.transport.start()
            self._task = asyncio.create_task(self._watch_observations(), name="pulse-push-observations")
        if self.backup_config.enabled:
            self._backup_task = asyncio.create_task(self._watch_backup_reports(), name="pulse-backup-alerts")

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
                "state": "active" if active else "session_required",
                "backup_alerts_enabled": bool(self.config.enabled and self.backup_config.enabled
                    and row["owner_id"] == self.backup_config.owner_id and row["backup_alerts_enabled"])}

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

    async def set_backup_alerts(self, owner: str, item_id: str, *, enabled: bool, confirmed: bool):
        if enabled and not confirmed:
            raise DomainError("Confirma que quieres recibir avisos genéricos de copias en este dispositivo.")
        if enabled and (not self.config.enabled or not self.backup_config.enabled
                        or owner != self.backup_config.owner_id or self._closed):
            raise DomainError("Los avisos de copias no están disponibles para esta cuenta.", 503)
        row = await self.coordinator.call("get_push_subscription", owner, item_id)
        if enabled and not self._session_active(row):
            raise DomainError("Inicia sesión y reactiva los avisos de ese dispositivo.", 409)
        consented_at = _now().isoformat(timespec="microseconds").replace("+00:00", "Z") if enabled else None
        updated = await self.coordinator.call("set_backup_alert_consent", owner, item_id,
            enabled, consented_at, expected_revision=row["revision"], write=True)
        if enabled and (self._closed or not self._session_active(updated)):
            await self.coordinator.call("set_backup_alert_consent", owner, item_id, False, None,
                expected_revision=updated["revision"], write=True)
            raise DomainError("La sesión de avisos ha cambiado. Vuelve a iniciar sesión.", 409)
        return self._public(updated)

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

    async def _watch_backup_reports(self):
        while True:
            selected = self.backup_config
            if self._closed or not selected.enabled:
                return
            scope, owner = selected.scope, selected.owner_id
            import_epoch = self.coordinator.import_epoch
            try:
                reports = await asyncio.to_thread(read_monitor_reports, selected, _now())
                if (self._closed or not self.backup_config.enabled
                        or self.backup_config.scope != scope):
                    continue
                saved = await self.coordinator.call(
                    "observe_backup_reports", scope, reports, write=True,
                    expected_import_epoch=import_epoch,
                )
                if (saved is None or self._closed or not self.backup_config.enabled
                        or self.backup_config.scope != scope):
                    continue
                if self.config.enabled and owner == self.backup_config.owner_id:
                    candidates = await self.coordinator.call("list_backup_alert_candidates", scope, owner)
                    rows = await self.coordinator.call("list_push_subscriptions", owner)
                    async with self._sending:
                        for event in candidates:
                            for row in rows:
                                if (row.get("backup_alerts_enabled") and row.get("backup_alerts_consented_at")
                                        and self._session_active(row) and not self._closed
                                        and self.backup_config.scope == scope):
                                    try:
                                        await self._send_backup_event(
                                            row, event, scope, expected_import_epoch=import_epoch,
                                        )
                                    except DomainError:
                                        pass
            except asyncio.CancelledError:
                raise
            except Exception:
                # No source paths, report contents, owner IDs, or transport details in logs.
                logging.getLogger(__name__).warning("No se pudo actualizar el estado de avisos de copias.")
            await asyncio.sleep(selected.poll_seconds)

    async def _send_backup_event(self, row: dict, event: dict, scope: str, *,
                                 expected_import_epoch: int | None = None):
        if expected_import_epoch is None:
            expected_import_epoch = self.coordinator.import_epoch
        if (self._closed or not self.config.enabled or self.crypto is None
                or not self.backup_config.enabled or scope != self.backup_config.scope
                or expected_import_epoch != self.coordinator.import_epoch
                or row["owner_id"] != self.backup_config.owner_id
                or not row.get("backup_alerts_enabled") or not row.get("backup_alerts_consented_at")
                or not self._session_active(row)):
            return
        kind = event["kind"]
        if kind not in {"backup_alert", "backup_recovery"}:
            return
        reserved = await self.coordinator.call(
            "reserve_backup_alert_delivery", scope, row["owner_id"], row["id"], row["revision"],
            event["event_id"], row["backup_alerts_consented_at"], write=True,
            expected_import_epoch=expected_import_epoch,
        )
        if reserved is None:
            return
        try:
            endpoint, headers, body = await asyncio.to_thread(self.crypto.request, reserved["ciphertext"], kind)
            current = await self.coordinator.call("get_push_subscription", row["owner_id"], row["id"])
            if (current["revision"] != row["revision"] or not current["backup_alerts_enabled"]
                    or current["backup_alerts_consented_at"] != row["backup_alerts_consented_at"]
                    or self._closed or scope != self.backup_config.scope
                    or expected_import_epoch != self.coordinator.import_epoch
                    or not self._session_active(current)):
                await self.coordinator.call(
                    "finish_backup_alert_delivery", scope, row["owner_id"], row["id"],
                    row["revision"], event["event_id"], "uncertain", write=True,
                )
                raise DomainError("El permiso de avisos de copias ha cambiado. Vuelve a consultarlo.", 409)
            async with asyncio.timeout(8):
                status = await self.transport.send(endpoint, headers, body)
        except asyncio.CancelledError:
            # La reserva queda pending; cerrar o cancelar nunca autoriza un reenvío.
            raise
        except DomainError:
            await self.coordinator.call(
                "finish_backup_alert_delivery", scope, row["owner_id"], row["id"],
                row["revision"], event["event_id"], "failed", write=True,
            )
            raise
        except Exception:
            await self.coordinator.call(
                "finish_backup_alert_delivery", scope, row["owner_id"], row["id"],
                row["revision"], event["event_id"], "uncertain", write=True,
            )
            raise DomainError("No se pudo confirmar el envío. No se repetirá automáticamente.", 503) from None
        if status in {404, 410}:
            await self.coordinator.call("delete_push_subscription", row["owner_id"], row["id"],
                expected_revision=row["revision"], write=True)
            raise DomainError("Ese dispositivo ya no admite avisos. Actívalo de nuevo.", 410)
        result = "accepted" if 200 <= status < 300 else "failed"
        await self.coordinator.call(
            "finish_backup_alert_delivery", scope, row["owner_id"], row["id"],
            row["revision"], event["event_id"], result, write=True,
        )
        if result != "accepted":
            raise DomainError("El servicio de avisos rechazó el envío.", 503)

    async def close(self):
        self._closed = True
        tasks = [task for task in (self._task, self._backup_task) if task]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._task = self._backup_task = None
        if self.config.enabled:
            await self.transport.close()


def register_push(app, service: PushService, owner_id):
    @app.get("/api/push/capabilities", response_model=PushCapabilities)
    async def capabilities(request: Request):
        return service.capabilities(owner_id(request))

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

    @app.post("/api/push/subscriptions/{item_id}/backup-alerts", response_model=PushDevice)
    async def backup_alert_consent(request: Request, item_id: str, values: BackupAlertsConsent):
        return await service.set_backup_alerts(owner_id(request), item_id,
            enabled=values.enabled, confirmed=values.confirmed)
