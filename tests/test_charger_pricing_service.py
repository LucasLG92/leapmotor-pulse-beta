"""Automatic charging costs: synthetic source sessions, no upstream network."""
import asyncio
from copy import deepcopy
from datetime import timedelta

from pulse.charger import ChargerReader
from pulse.charger_pricing import ChargerPricing, MAX_QUOTES_PER_PASS
from pulse.coordinator import Coordinator
from pulse.evcc import EvccConfig
from test_charger_pricing_store import NOW, quote_for, scope, session


class Electricity:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.waiting = None
        self.release = None

    async def quote(self, payload):
        self.calls.append(deepcopy(payload))
        if self.waiting is not None:
            self.waiting.set()
            await self.release.wait()
        if self.fail:
            raise RuntimeError('Synthetic provider failure, never public')
        return quote_for(payload)


async def setup(path, count=1, *, pvpc=True):
    coordinator = Coordinator(path)
    await coordinator.start()
    coordinator.store._clock = lambda: NOW
    records = [session(index=i + 1) for i in range(count)]
    await coordinator.call('ingest_charger', scope(), sessions=records)
    if pvpc:
        await coordinator.call('update_settings', {'pricing_method': 'pvpc'})
    return coordinator, records


def test_disabled_reader_and_manual_tariff_make_no_price_requests(tmp_path):
    async def run():
        coordinator, records = await setup(tmp_path / 'pulse.sqlite3', pvpc=False)
        electricity = Electricity()
        worker = ChargerPricing(scope(), coordinator, electricity)
        reader = ChargerReader(EvccConfig(), coordinator, electricity=electricity)
        try:
            await reader.start()
            assert reader.task is None and reader.pricing is None
            await worker.price_once()
            assert electricity.calls == []
            item = (await coordinator.call('priced_charger_sessions', scope()))['items'][0]
            assert item['pricing_status'] == 'disabled' and item['pvpc'] is None
        finally:
            await reader.close()
            await coordinator.close()
    asyncio.run(run())


def test_background_prices_once_without_manual_record_or_page_request(tmp_path):
    async def run():
        coordinator, records = await setup(tmp_path / 'pulse.sqlite3')
        electricity = Electricity()
        worker = ChargerPricing(scope(), coordinator, electricity)
        try:
            await worker.price_once()
            await worker.price_once()
            assert len(electricity.calls) == 1
            item = (await coordinator.call('priced_charger_sessions', scope()))['items'][0]
            assert item['pricing_status'] == 'ready' and item['pvpc']['cost_eur'] == 1.25
            assert item['pvpc']['pricing']['accuracy'] == 'estimated_uniform'
            assert (await coordinator.call('list_charges'))['total'] == 0
            assert len((await coordinator.call('export_data'))['charger_pricing']) == 1
        finally:
            await worker.close()
            await coordinator.close()
    asyncio.run(run())


def test_failure_stays_pending_and_retries_after_bounded_cooldown(tmp_path):
    async def run():
        coordinator, records = await setup(tmp_path / 'pulse.sqlite3')
        electricity = Electricity(); electricity.fail = True
        tick = [100.0]
        worker = ChargerPricing(scope(), coordinator, electricity, monotonic=lambda: tick[0])
        try:
            await worker.price_once()
            assert len(electricity.calls) == 1
            item = (await coordinator.call('priced_charger_sessions', scope()))['items'][0]
            assert item['pricing_status'] == 'pending' and item['pvpc'] is None
            tick[0] += 599
            electricity.fail = False
            await worker.price_once()
            assert len(electricity.calls) == 1
            tick[0] += 1
            await worker.price_once()
            assert len(electricity.calls) == 2
            assert (await coordinator.call('priced_charger_sessions', scope()))['items'][0]['pricing_status'] == 'ready'
        finally:
            await coordinator.close()
    asyncio.run(run())


def test_bounded_batch_drains_and_zero_energy_never_requests_price(tmp_path):
    async def run():
        coordinator, records = await setup(tmp_path / 'pulse.sqlite3', count=7)
        zero = session(index=8, kwh=0)
        await coordinator.call('ingest_charger', scope(), sessions=[zero])
        electricity = Electricity(); worker = ChargerPricing(scope(), coordinator, electricity)
        try:
            await worker.price_once()
            assert len(electricity.calls) == MAX_QUOTES_PER_PASS
            await worker.price_once()
            assert len(electricity.calls) == 7
            await worker.price_once()
            assert len(electricity.calls) == 7
            items = (await coordinator.call('priced_charger_sessions', scope()))['items']
            assert next(item for item in items if item['id'] == zero['id'])['pricing_status'] == 'ineligible'
        finally:
            await coordinator.close()
    asyncio.run(run())


def test_correction_while_quote_is_pending_rejects_the_old_cost(tmp_path):
    async def run():
        coordinator, records = await setup(tmp_path / 'pulse.sqlite3')
        electricity = Electricity(); electricity.waiting = asyncio.Event(); electricity.release = asyncio.Event()
        worker = ChargerPricing(scope(), coordinator, electricity)
        job = asyncio.create_task(worker.price_once())
        try:
            await asyncio.wait_for(electricity.waiting.wait(), 1)
            corrected = {**records[0], 'kwh': 12, 'received_at': NOW.isoformat()}
            await coordinator.call('ingest_charger', scope(), sessions=[corrected])
            electricity.release.set(); await asyncio.wait_for(job, 1)
            item = (await coordinator.call('priced_charger_sessions', scope()))['items'][0]
            assert item['kwh'] == 12 and item['pvpc'] is None
            electricity.waiting = None
            await worker.price_once()
            assert (await coordinator.call('priced_charger_sessions', scope()))['items'][0]['pvpc']['cost_eur'] == 1.5
        finally:
            if not job.done(): job.cancel()
            await coordinator.close()
    asyncio.run(run())


def test_import_epoch_rejects_late_quote_even_for_same_session_inputs(tmp_path):
    async def run():
        coordinator, records = await setup(tmp_path / 'pulse.sqlite3')
        archive = await coordinator.call('export_data')
        electricity = Electricity(); electricity.waiting = asyncio.Event(); electricity.release = asyncio.Event()
        worker = ChargerPricing(scope(), coordinator, electricity)
        job = asyncio.create_task(worker.price_once())
        try:
            await asyncio.wait_for(electricity.waiting.wait(), 1)
            assert coordinator.import_epoch == 0
            await coordinator.restore(archive)
            assert coordinator.import_epoch == 1
            electricity.release.set(); await asyncio.wait_for(job, 1)
            assert (await coordinator.call('export_data'))['charger_pricing'] == []
            electricity.waiting = None
            await worker.price_once()
            assert len((await coordinator.call('export_data'))['charger_pricing']) == 1
        finally:
            if not job.done(): job.cancel()
            await coordinator.close()
    asyncio.run(run())


def test_settings_changed_during_quote_does_not_save_new_cost(tmp_path):
    async def run():
        coordinator, records = await setup(tmp_path / 'pulse.sqlite3')
        electricity = Electricity(); electricity.waiting = asyncio.Event(); electricity.release = asyncio.Event()
        worker = ChargerPricing(scope(), coordinator, electricity)
        job = asyncio.create_task(worker.price_once())
        try:
            await asyncio.wait_for(electricity.waiting.wait(), 1)
            await coordinator.call('update_settings', {'pricing_method': 'manual'})
            electricity.release.set(); await asyncio.wait_for(job, 1)
            assert (await coordinator.call('export_data'))['charger_pricing'] == []
            assert (await coordinator.call('priced_charger_sessions', scope()))['items'][0]['pricing_status'] == 'disabled'
        finally:
            if not job.done(): job.cancel()
            await coordinator.close()
    asyncio.run(run())


def test_coordinator_epoch_is_checked_at_executor_execution(tmp_path):
    async def run():
        coordinator, records = await setup(tmp_path / 'pulse.sqlite3')
        try:
            epoch = coordinator.import_epoch
            await coordinator.restore(await coordinator.call('export_data'))
            quote = quote_for(records[0])
            result = await coordinator.call('save_charger_pricing', scope(), records[0], quote,
                                            expected_import_epoch=epoch)
            assert result is None and (await coordinator.call('export_data'))['charger_pricing'] == []
        finally:
            await coordinator.close()
    asyncio.run(run())


def test_pause_refreshes_history_but_only_confirmed_closed_session_is_imported(tmp_path):
    async def run():
        import httpx2
        coordinator, records = await setup(tmp_path / 'pulse.sqlite3', count=0)
        clock = [NOW]; tick = [100.0]; connected = [True]; charging = [True]
        closed = []; calls = []
        coordinator.store._clock = lambda: clock[0]
        config = EvccConfig(True, 'http://127.0.0.1:7070', 'Garage', history_months=1)

        def handler(request):
            calls.append(request.url.path)
            if request.url.path == '/api/state':
                return httpx2.Response(200, json={'loadpoints': [{
                    'title': 'Garage', 'connected': connected[0], 'charging': charging[0],
                    'chargePower': 7000 if charging[0] else 0, 'chargedEnergy': 10,
                    'chargeDuration': 3000}]})
            return httpx2.Response(200, json=closed)

        reader = ChargerReader(config, coordinator, transport=httpx2.MockTransport(handler),
                               clock=lambda: clock[0], monotonic=lambda: tick[0])
        try:
            await reader.poll_once()
            assert calls.count('/api/sessions') == 1
            assert (await reader.sessions())['total'] == 0
            charging[0] = False
            tick[0] += 60; clock[0] += timedelta(seconds=60)
            await reader.poll_once()
            assert calls.count('/api/sessions') == 2
            assert (await reader.sessions())['total'] == 0  # A pause creates nothing.
            connected[0] = False
            closed.append({'id': 1, 'loadpoint': 'Garage', 'created': '2026-10-08T12:00:00Z',
                           'finished': '2026-10-08T13:00:00Z', 'chargedEnergy': 10})
            tick[0] += 60; clock[0] += timedelta(seconds=60)
            await reader.poll_once()
            assert calls.count('/api/sessions') == 3
            item = (await reader.sessions())['items'][0]
            assert item['started_at'] == '2026-10-08T12:00:00.000000Z'
            assert item['ended_at'] == '2026-10-08T13:00:00.000000Z'
            assert item['kwh'] == 10 and item['pricing_status'] == 'pending'
            assert (await coordinator.call('list_charges'))['total'] == 0
        finally:
            await reader.close(); await coordinator.close()
    asyncio.run(run())
