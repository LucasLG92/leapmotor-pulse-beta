"""Bounded background PVPC estimates for confirmed, imported charger sessions.

No live charging flag creates a session or an end time. The reader imports
closed sessions, and this worker prices their reported energy. It never adds
manual/demo charges or makes a request on behalf of a page GET.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import inspect
import time


MAX_QUOTES_PER_PASS = 4
MAX_PENDING_SESSIONS = 1000
RETRY_SECONDS = 600
ENERGY_READ_TIMEOUT_SECONDS = 8


def eligible_session(session):
    try:
        start = datetime.fromisoformat(session["started_at"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(session["ended_at"].replace("Z", "+00:00"))
        if start.tzinfo is None or end.tzinfo is None:
            return False
        seconds = (end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds()
        return 0 < seconds <= 72 * 3600 and 0 < session["kwh"] <= 500
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return False


class ChargerPricing:
    def __init__(self, scope, coordinator, electricity, *, interval_seconds=60, monotonic=None,
                 energy_curve_reader=None):
        if energy_curve_reader is not None and not inspect.iscoroutinefunction(energy_curve_reader):
            raise ValueError("La fuente temporal requiere un lector async explícito.")
        self.scope = scope
        self.coordinator = coordinator
        self.electricity = electricity
        self.interval_seconds = interval_seconds
        self.monotonic = monotonic or time.monotonic
        self.task = None
        self.wake = asyncio.Event()
        self.lock = asyncio.Lock()
        self.failures = {}
        self.energy_curve_reader = energy_curve_reader
        self.last_temporal_session_id = None
        self.temporal_import_epoch = None

    async def start(self):
        if self.task is None:
            self.task = asyncio.create_task(self._loop(), name="pulse-charger-pvpc")

    async def close(self):
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None

    def changed(self):
        self.wake.set()

    @staticmethod
    def _key(session, epoch):
        return json.dumps([epoch, session["id"], session["started_at"],
                           session["ended_at"], session["kwh"]], separators=(",", ":"))

    async def price_once(self):
        async with self.lock:
            settings = await self.coordinator.call("get_settings")
            if settings["pricing_method"] != "pvpc":
                return
            if self.energy_curve_reader is not None:
                await self._price_temporal_once()
                return
            epoch = self.coordinator.import_epoch
            candidates = await self.coordinator.call(
                "charger_pricing_candidates", self.scope, limit=MAX_PENDING_SESSIONS)
            keys = {self._key(session, epoch) for session in candidates}
            self.failures = {key: tick for key, tick in self.failures.items() if key in keys}
            attempted = 0
            for session in candidates:
                if not eligible_session(session):
                    continue
                key = self._key(session, epoch)
                if self.failures.get(key, 0) > self.monotonic():
                    continue
                if attempted >= MAX_QUOTES_PER_PASS:
                    break
                attempted += 1
                # Recheck before each outbound request. Store also checks the
                # current mode and session inputs inside its write transaction.
                current = await self.coordinator.call("get_settings")
                if current["pricing_method"] != "pvpc" or epoch != self.coordinator.import_epoch:
                    break
                try:
                    quote = await self.electricity.quote({
                        "started_at": session["started_at"], "ended_at": session["ended_at"],
                        "kwh": session["kwh"], "pricing_method": "pvpc"})
                    saved = await self.coordinator.call(
                        "save_charger_pricing", self.scope, session, quote,
                        expected_import_epoch=epoch)
                except Exception:
                    # Keep public state pending. Never copy provider payloads,
                    # URLs, user data or arbitrary exception text to responses/logs.
                    self.failures[key] = self.monotonic() + RETRY_SECONDS
                    continue
                self.failures.pop(key, None)
                if saved is not None and saved["changed"]:
                    self.coordinator._publish()

    async def _price_temporal_once(self):
        """Optional session-linked source; never qualify evcc's candidate ledger."""
        from pulse.temporal_pvpc import validate_energy_distribution
        epoch = self.coordinator.import_epoch
        if epoch != self.temporal_import_epoch:
            self.temporal_import_epoch = epoch
            self.failures.clear()
            self.last_temporal_session_id = None
        entries = await self.coordinator.call("charger_temporal_pricing_candidates", self.scope,
            limit=MAX_QUOTES_PER_PASS, after_session_id=self.last_temporal_session_id)
        self.failures = {key: tick for key, tick in self.failures.items() if tick > self.monotonic()}
        attempted = 0
        for entry in entries:
            session, previous = entry["session"], entry["quote"]
            # Advance even over cooldown/ineligible rows, without validating
            # every retained curve on SQLite's shared executor.
            self.last_temporal_session_id = session["id"]
            key = self._key(session, epoch)
            if not eligible_session(session) or self.failures.get(key, 0) > self.monotonic():
                continue
            if attempted >= MAX_QUOTES_PER_PASS:
                break
            if not await self._pricing_current(epoch):
                break
            attempted += 1
            self.last_temporal_session_id = session["id"]
            try:
                raw = await asyncio.wait_for(self.energy_curve_reader(self.scope, dict(session)),
                                              timeout=ENERGY_READ_TIMEOUT_SECONDS)
                if not await self._pricing_current(epoch):
                    break
                if raw is None:
                    # Missing source data does not remove a historical result.
                    if previous is not None:
                        continue
                    quote = await self.electricity.quote({"started_at": session["started_at"],
                        "ended_at": session["ended_at"], "kwh": session["kwh"], "pricing_method": "pvpc"})
                    saved = await self.coordinator.call("save_charger_pricing", self.scope,
                        session, quote, expected_import_epoch=epoch)
                else:
                    distribution = validate_energy_distribution(raw, session["started_at"],
                        session["ended_at"], session["kwh"], expected_scope=self.scope,
                        expected_session_id=session["id"])
                    if previous is not None and previous["pricing"].get("energy_distribution") == distribution:
                        continue
                    quote = await self.electricity.quote_intervals(session, distribution)
                    saved = await self.coordinator.call("save_charger_temporal_pricing", self.scope,
                        session, quote, previous, expected_import_epoch=epoch)
                if saved is not None and saved["changed"]:
                    self.coordinator._publish()
            except Exception:
                # No untrusted exception text/payload is copied to UI/logs.
                pass
            finally:
                self.failures[key] = self.monotonic() + RETRY_SECONDS
                for stale in list(self.failures)[:-MAX_PENDING_SESSIONS]:
                    del self.failures[stale]

    async def _pricing_current(self, epoch):
        current = await self.coordinator.call("get_settings")
        return current["pricing_method"] == "pvpc" and epoch == self.coordinator.import_epoch

    async def _loop(self):
        while True:
            self.wake.clear()
            try:
                await self.price_once()
            except Exception:
                # An unexpected storage failure does not permanently kill the
                # background worker or invent a successful price.
                pass
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                pass
