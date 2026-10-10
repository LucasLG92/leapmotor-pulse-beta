"""Cache pública PVPC y refresco ligero, sin bloquear el ejecutor SQLite."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from pulse.domain import DomainError
from pulse.pvpc import PvpcError, fetch_day, quote_charge

MADRID = ZoneInfo("Europe/Madrid")


class Electricity:
    def __init__(self, coordinator, *, fetcher=fetch_day,
                 clock: Callable[[], datetime] | None = None):
        self.coordinator = coordinator
        self.fetcher = fetcher
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.lock = asyncio.Lock()
        self.wake = asyncio.Event()
        self.task = None
        self.failures: dict[str, datetime] = {}

    async def start(self):
        self.task = asyncio.create_task(self._refresh_loop(), name="pulse-pvpc")

    async def close(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None

    def settings_changed(self):
        self.wake.set()

    def _valid_day(self, day: date):
        today = self.clock().astimezone(MADRID).date()
        if day < date(2014, 4, 1) or day > today + timedelta(days=1):
            raise DomainError("El PVPC solo puede consultarse para fechas históricas, hoy o mañana.")

    async def day(self, day: date):
        self._valid_day(day)
        key = day.isoformat()
        cached = await self.coordinator.call("read_pvpc_day", key)
        if cached is not None:
            return cached
        async with self.lock:
            # Una consulta simultánea puede haber completado este día al esperar.
            cached = await self.coordinator.call("read_pvpc_day", key)
            if cached is not None:
                return cached
            now = self.clock().astimezone(timezone.utc)
            retry_at = self.failures.get(key)
            if retry_at is not None and now < retry_at:
                raise DomainError("Los precios oficiales no están disponibles. Reintenta en unos minutos.", 503)
            try:
                snapshot = await self.fetcher(day)
                await self.coordinator.call("cache_pvpc_day", snapshot)
            except PvpcError:
                self.failures[key] = now + timedelta(minutes=10)
                # Límite de memoria incluso ante muchas fechas distintas fallidas.
                for old in list(self.failures)[:-128]:
                    del self.failures[old]
                raise DomainError("No se pudo obtener la serie PVPC oficial completa. Reintenta en unos minutos.", 503) from None
            self.failures.pop(key, None)
            return snapshot

    async def _days_for_window(self, values):
        try:
            start = datetime.fromisoformat(values["started_at"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(values["ended_at"].replace("Z", "+00:00"))
            if start.tzinfo is None or end.tzinfo is None:
                raise ValueError
            duration = (end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds()
            if not 0 < duration <= 72 * 3600:
                raise ValueError
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
            raise DomainError("Indica inicio y fin con zona horaria; la carga debe durar entre 0 y 72 horas.") from None
        first = start.astimezone(MADRID).date()
        # Fin exclusivo: una carga que termina a medianoche no necesita el día siguiente.
        last = (end.astimezone(timezone.utc) - timedelta(microseconds=1)).astimezone(MADRID).date()
        days = []
        day = first
        while day <= last:
            days.append(await self.day(day))
            day += timedelta(days=1)
        return days

    async def quote(self, values):
        days = await self._days_for_window(values)
        try:
            return quote_charge(values["started_at"], values["ended_at"], values["kwh"],
                                days, calculated_at=self.clock())
        except PvpcError:
            raise DomainError("No hay precios completos para calcular esta carga. Revisa las fechas.", 503) from None

    async def quote_intervals(self, session, distribution):
        from pulse.charger_store import validate_session
        from pulse.temporal_pvpc import validate_energy_distribution, quote_charge_intervals
        try:
            energy = validate_energy_distribution(distribution, session["started_at"],
                session["ended_at"], session["kwh"], expected_session_id=session["id"])
            normalized = validate_session(session, energy["scope"])
        except (KeyError, TypeError, ValueError):
            raise DomainError("La energía por intervalos no cubre esta sesión de forma coherente.") from None
        days = await self._days_for_window(normalized)
        try:
            return quote_charge_intervals(normalized["started_at"], normalized["ended_at"],
                normalized["kwh"], energy, days, calculated_at=self.clock())
        except PvpcError:
            raise DomainError("No hay precios completos para calcular esta carga. Revisa las fechas.", 503) from None

    async def _refresh_loop(self):
        while True:
            self.wake.clear()
            settings = await self.coordinator.call("get_settings")
            if settings["pricing_method"] == "pvpc":
                today = self.clock().astimezone(MADRID).date()
                for day in (today, today + timedelta(days=1)):
                    try:
                        await self.day(day)
                    except DomainError:
                        # Mañana puede no estar publicado: no se inventa una curva.
                        pass
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=3600)
            except asyncio.TimeoutError:
                pass
