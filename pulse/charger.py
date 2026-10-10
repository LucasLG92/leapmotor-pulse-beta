"""One optional charger reader; API views use SQLite, never the upstream API."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import time

from pulse.evcc import EvccClient, EvccConfig, EvccError, iso
from pulse.charger_pricing import ChargerPricing


_MESSAGES = {
    "disabled": "El cargador todavía no está enlazado.",
    "waiting": "Esperando la primera lectura del cargador.",
    "ready": "Lectura disponible de evcc.",
    "stale": "La última lectura ha quedado antigua; no confirma el estado actual.",
    "contract": "evcc no devolvió los campos esperados del punto de carga seleccionado.",
    "network": "No se pudo contactar con evcc. Se conservan los datos anteriores.",
    "access": "evcc o su proxy no permiten esta lectura. Revisa el acceso configurado.",
    "large": "La respuesta de evcc supera el límite de lectura permitido.",
    "timeout": "La consulta de evcc agotó el tiempo disponible.",
}


class ChargerReader:
    def __init__(self, config: EvccConfig, coordinator, *, transport=None, clock=None, monotonic=None,
                 electricity=None, energy_curve_reader=None):
        self.config = config
        self.coordinator = coordinator
        self.client = EvccClient(config, transport=transport)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.monotonic = monotonic or time.monotonic
        self.task = None
        self.lock = asyncio.Lock()
        self.last_success_at = None
        self.last_history_at = None
        self.last_success_tick = None
        self.next_history_tick = 0.0
        self.error = None
        self.history_error = None
        self.last_observation_flags = None
        self.last_history_attempt_tick = None
        self.history_refresh_pending = False
        self.energy_epoch = None
        self.energy_error = None
        self.last_energy_at = None
        self.energy_empty = False
        self.observed_import_epoch = coordinator.import_epoch
        self.pricing = ChargerPricing(
            config.scope, coordinator, electricity, interval_seconds=min(60, config.poll_seconds),
            monotonic=self.monotonic, energy_curve_reader=energy_curve_reader
        ) if config.enabled and electricity is not None else None

    async def start(self):
        if self.config.enabled and self.task is None:
            if self.config.energy_history_enabled:
                # A process restart is a gap in observed configuration, never
                # evidence of historical continuity of a metrics entity.
                await self.coordinator.call("end_all_charger_energy_epochs",
                    received_at=iso(self.clock()), expected_import_epoch=self.coordinator.import_epoch)
            self.task = asyncio.create_task(self._loop(), name="pulse-evcc-reader")
            if self.pricing is not None:
                await self.pricing.start()

    async def close(self):
        try:
            if self.task is not None:
                self.task.cancel()
                try:
                    await self.task
                except asyncio.CancelledError:
                    pass
                finally:
                    self.task = None
        finally:
            if self.pricing is not None:
                await self.pricing.close()

    def settings_changed(self):
        if self.pricing is not None:
            self.pricing.changed()

    async def _loop(self):
        while True:
            await self.poll_once()
            await asyncio.sleep(self.config.poll_seconds)

    async def poll_once(self):
        if not self.config.enabled:
            return
        async with self.lock:
            self._reset_after_import()
            epoch = self.coordinator.import_epoch
            previous = (self.error, self.history_error, self.last_success_at, self.last_history_at,
                        self.energy_error, self.last_energy_at)
            changed = False
            try:
                if self.config.energy_history_enabled:
                    detail = await self.client.state_with_identity(received_at=self.clock())
                    observation, identity = detail["observation"], detail["identity"]
                else:
                    observation, identity = await self.client.state(received_at=self.clock()), None
                observation["received_at"] = iso(self.clock())
                saved = await self.coordinator.call("ingest_charger", self.config.scope,
                    observation=observation, expected_import_epoch=epoch)
                if saved is None:
                    return
                changed |= saved["changed"]
                if self.config.energy_history_enabled:
                    prior_energy_id = self.energy_epoch["id"] if self.energy_epoch else None
                    binding = await self.coordinator.call("observe_charger_energy", self.config.scope,
                        identity, received_at=observation["received_at"], expected_import_epoch=epoch)
                    if binding is None:
                        return
                    self.energy_epoch = binding["epoch"]
                    changed |= binding["changed"]
                    if self.energy_epoch is None:
                        self.energy_error = "no_identity"
                    elif self.energy_epoch["id"] != prior_energy_id:
                        self.energy_error = "waiting"
                flags = (observation["connected"], observation["charging"])
                if self.last_observation_flags is not None:
                    was_connected, was_charging = self.last_observation_flags
                    if (was_connected and not flags[0]) or (was_charging and not flags[1]):
                        # A hint only: retrieve confirmed closed history, never
                        # fabricate an end time from a pause/disconnect/status.
                        self.history_refresh_pending = True
                self.last_observation_flags = flags
                self.last_success_at = observation["received_at"]
                self.last_success_tick = self.monotonic()
                self.error = None
            except EvccError as error:
                if epoch != self.coordinator.import_epoch:
                    return
                self.error = error.code
                self.energy_epoch = None
            except Exception:
                if epoch != self.coordinator.import_epoch:
                    return
                # Do not log upstream payloads, URLs, identity or arbitrary exception text.
                self.error = "contract"
                self.energy_epoch = None
            if epoch != self.coordinator.import_epoch:
                return
            if self.error and self.config.energy_history_enabled:
                await self.coordinator.call("end_charger_energy_epoch", self.config.scope,
                    received_at=iso(self.clock()), expected_import_epoch=epoch)
            now_tick = self.monotonic()
            event_due = (self.history_refresh_pending and self.history_error is None
                         and self.last_history_attempt_tick is not None
                         and now_tick - self.last_history_attempt_tick >= min(60, self.config.poll_seconds))
            if now_tick >= self.next_history_tick or event_due:
                # Cooldown starts before I/O, including failing or partial historical reads.
                self.next_history_tick = now_tick + self.config.history_seconds
                self.last_history_attempt_tick = now_tick
                self.history_refresh_pending = False
                current = self.clock().astimezone(timezone.utc)
                self.history_error = None
                for back in range(self.config.history_months):
                    month_index = current.year * 12 + current.month - 1 - back
                    year, month_zero = divmod(month_index, 12)
                    try:
                        items = await self.client.sessions(year=year, month=month_zero + 1,
                                                         received_at=self.clock())
                        received_at = iso(self.clock())
                        for item in items:
                            item["received_at"] = received_at
                        saved = await self.coordinator.call("ingest_charger", self.config.scope,
                            sessions=items, expected_import_epoch=epoch)
                        if saved is None:
                            return
                        changed |= saved["changed"]
                    except EvccError as error:
                        if epoch != self.coordinator.import_epoch:
                            return
                        self.history_error = error.code
                        break
                    except Exception:
                        if epoch != self.coordinator.import_epoch:
                            return
                        self.history_error = "contract"
                        break
                if self.history_error is None:
                    self.last_history_at = iso(self.clock())
                if self.config.energy_history_enabled and self.energy_epoch is not None:
                    energy_changed = await self._read_energy(epoch)
                    if energy_changed is None:
                        return
                    changed |= energy_changed
            if epoch != self.coordinator.import_epoch:
                return
            if changed or previous != (self.error, self.history_error, self.last_success_at, self.last_history_at,
                                       self.energy_error, self.last_energy_at):
                self.coordinator._publish()
                if self.pricing is not None:
                    self.pricing.changed()

    def _reset_after_import(self):
        if self.observed_import_epoch == self.coordinator.import_epoch:
            return
        self.observed_import_epoch = self.coordinator.import_epoch
        self.last_success_at = self.last_history_at = self.last_success_tick = None
        self.last_observation_flags = self.last_history_attempt_tick = None
        self.energy_epoch = self.energy_error = self.last_energy_at = None
        self.energy_empty = False
        self.error = self.history_error = None
        self.history_refresh_pending = False
        self.next_history_tick = 0.0

    async def _read_energy(self, import_epoch):
        """Bounded background acquisition only; candidates never change a quote."""
        binding = self.energy_epoch
        received = datetime.fromisoformat(binding["last_seen_at"].replace("Z", "+00:00"))
        first = datetime.fromisoformat(binding["first_seen_at"].replace("Z", "+00:00"))
        end = received.replace(minute=received.minute // 15 * 15, second=0, microsecond=0)
        # Ceil the first observed binding. A current name does not identify
        # energy preceding it, including the partial first quarter-hour.
        start = first.replace(minute=first.minute // 15 * 15, second=0, microsecond=0)
        if start < first:
            start += timedelta(minutes=15)
        start = max(start, end - timedelta(hours=24))
        if start >= end:
            self.energy_error = "waiting"
            return False
        try:
            slots = await self.client.energy_history(entity=binding["entity"], started_at=start,
                ended_at=end, received_at=received)
            # The binding limits the queried period; receipt is the time the
            # historical response actually reached Pulse.
            captured_received = iso(self.clock())
            for slot in slots:
                slot["epoch_id"] = binding["id"]
                slot["received_at"] = captured_received
            result = await self.coordinator.call("ingest_charger_energy", self.config.scope,
                binding["id"], slots, expected_import_epoch=import_epoch)
            if result is None:
                return None
            self.last_energy_at = captured_received
            self.energy_empty = not slots
            self.energy_error = None
            return result["changed"]
        except EvccError as error:
            if import_epoch != self.coordinator.import_epoch:
                return None
            self.energy_error = error.code
        except Exception:
            if import_epoch != self.coordinator.import_epoch:
                return None
            self.energy_error = "contract"
        return False

    async def energy_status(self):
        enabled = self.config.enabled and self.config.energy_history_enabled
        summary = await self.coordinator.call("charger_energy_summary", self.config.scope) if enabled else {
            "retained_intervals": 0, "total_kwh": 0.0, "first_started_at": None,
            "last_ended_at": None, "last_received_at": None,
            "retention_limit": 2048, "validation": "not_qualified"}
        if not enabled:
            state, reason = "disabled", "disabled"
            message = "La adquisición experimental de energía por tramos está apagada."
        elif self.energy_error in {"waiting", "no_identity"} or self.energy_epoch is None:
            state, reason = "waiting", self.energy_error or "no_identity"
            message = ("No se pueden leer los tramos de este punto de carga. Se conservan sus sesiones y costes."
                       if reason == "no_identity" else "Esperando el primer tramo completo de energía. Su pertenencia a una carga sigue pendiente de validar.")
        elif self.energy_error:
            state, reason = "error", self.energy_error
            message = "No se pudieron actualizar los tramos. Se conservan los candidatos anteriores."
        elif self.last_energy_at:
            state, reason = "received", "empty" if self.energy_empty else "not_qualified"
            message = ("El origen no devolvió tramos para esta ventana; no se interpreta como energía cero."
                       if self.energy_empty else "Tramos candidatos recibidos. Su pertenencia a una carga sigue sin validar; no cambian el coste.")
        else:
            state, reason = "waiting", "waiting"
            message = "Esperando la primera consulta experimental de tramos."
        return {"enabled": enabled, "status": state, "reason_code": reason, "message": message,
                "last_success_at": self.last_energy_at, **summary}

    async def status(self):
        self._reset_after_import()
        read_epoch = self.coordinator.import_epoch
        if not self.config.enabled:
            status, reason = "disabled", "disabled"
        elif self.error:
            status, reason = "error", self.error
        elif self.last_success_tick is None:
            status, reason = "waiting", "waiting"
        elif not 0 <= self.monotonic() - self.last_success_tick <= max(90, 3 * self.config.poll_seconds):
            status, reason = "stale", "stale"
        else:
            status, reason = "ready", "ready"
        observation = await self.coordinator.call("charger_observation", self.config.scope) if self.config.enabled else None
        energy = await self.energy_status()
        if read_epoch != self.coordinator.import_epoch:
            # A restore between the two reads cannot combine a formerly live
            # observation with a new database or revive its ready status.
            self._reset_after_import()
            status = reason = "waiting" if self.config.enabled else "disabled"
            observation = None
            energy = {**energy, "status": "waiting" if energy["enabled"] else "disabled",
                      "reason_code": "waiting" if energy["enabled"] else "disabled",
                      "message": "Esperando una lectura posterior a la restauración.",
                      "last_success_at": None, "retained_intervals": 0, "total_kwh": 0.0,
                      "first_started_at": None, "last_ended_at": None, "last_received_at": None}
        return {"enabled": self.config.enabled, "status": status, "message": _MESSAGES[reason],
                "reason_code": reason, "last_success_at": self.last_success_at,
                "history_status": "disabled" if not self.config.enabled else "error" if self.history_error
                    else "ready" if self.last_history_at else "waiting",
                "history_message": _MESSAGES[self.history_error] if self.history_error else None,
                "last_history_at": self.last_history_at, "observation": observation,
                "validation": "not_qualified", "retention_limit": 1000,
                "history_months": self.config.history_months,
                "energy_history": energy}

    async def sessions(self, limit=20, offset=0):
        if not self.config.enabled:
            return {"items": [], "total": 0, "limit": limit, "offset": offset}
        return await self.coordinator.call("priced_charger_sessions", self.config.scope, limit=limit, offset=offset)

    async def pricing_history(self, session_id, limit=10, offset=0):
        if not self.config.enabled:
            return {"session_id": session_id, "items": [], "total": 0,
                    "limit": limit, "offset": offset, "retention_limit": 2048}
        return await self.coordinator.call("charger_pricing_history", self.config.scope,
                                           session_id, limit=limit, offset=offset)
