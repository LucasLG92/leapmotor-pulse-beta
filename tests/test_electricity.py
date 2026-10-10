"""La consulta oficial comparte cache; sus fallos nunca fabrican un precio."""

import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest

from pulse.domain import DomainError
from pulse.electricity import Electricity
from pulse.pvpc import PvpcError


class Cache:
    def __init__(self, method="manual"):
        self.days = {}
        self.method = method

    async def call(self, method, *args):
        if method == "read_pvpc_day":
            return self.days.get(args[0])
        if method == "cache_pvpc_day":
            self.days[args[0]["date"]] = args[0]
        if method == "get_settings":
            return {"pricing_method": self.method}


def test_cache_coalesces_concurrent_downloads_and_survives_service_restart():
    async def run():
        calls = []
        cache = Cache()

        async def fetch(day):
            calls.append(day)
            await asyncio.sleep(.01)
            return {"date": day.isoformat(), "intervals": []}

        service = Electricity(cache, fetcher=fetch)
        today = date(2025, 10, 26)
        results = await asyncio.gather(*(service.day(today) for _ in range(5)))
        assert len(calls) == 1
        assert all(result == results[0] for result in results)
        restarted = Electricity(cache, fetcher=fetch)
        assert await restarted.day(today) == results[0]
        assert len(calls) == 1

    asyncio.run(run())


def test_failure_has_cooldown_and_retry_without_a_cached_zero():
    async def run():
        current = datetime(2026, 10, 8, 10, tzinfo=timezone.utc)
        cache = Cache()
        calls = []

        async def fetch(day):
            calls.append(day)
            raise PvpcError("No disponible")

        service = Electricity(cache, fetcher=fetch, clock=lambda: current)
        for _ in range(2):
            with pytest.raises(DomainError) as error:
                await service.day(date(2026, 10, 8))
            assert error.value.status == 503
        assert len(calls) == 1
        assert cache.days == {}
        current += timedelta(minutes=11)
        with pytest.raises(DomainError):
            await service.day(date(2026, 10, 8))
        assert len(calls) == 2

    asyncio.run(run())


def test_default_manual_never_downloads_background_prices_and_pvpc_wakes():
    async def run():
        cache = Cache()
        calls = []

        async def fetch(day):
            calls.append(day)
            return {"date": day.isoformat(), "intervals": []}

        service = Electricity(cache, fetcher=fetch)
        await service.start()
        await asyncio.sleep(.01)
        assert calls == []
        cache.method = "pvpc"
        service.settings_changed()
        await asyncio.sleep(.02)
        assert len(calls) == 2
        await service.close()
        assert service.task is None

    asyncio.run(run())


def test_invalid_periods_never_download_and_future_day_is_bounded():
    async def run():
        calls = []

        async def fetch(day):
            calls.append(day)

        service = Electricity(Cache(), fetcher=fetch,
                              clock=lambda: datetime(2026, 10, 8, tzinfo=timezone.utc))
        for values in ({"started_at": "2026-10-08", "ended_at": "2026-10-09", "kwh": 20},
                       {"started_at": "2026-10-08T00:00:00Z", "ended_at": "2026-10-07T00:00:00Z", "kwh": 20},
                       {"started_at": "2026-10-08T00:00:00Z", "ended_at": "2026-10-12T00:00:00Z", "kwh": 20}):
            with pytest.raises(DomainError):
                await service.quote(values)
        with pytest.raises(DomainError):
            await service.day(date(2026, 10, 10))
        assert calls == []

    asyncio.run(run())
