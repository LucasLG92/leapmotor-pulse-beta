"""Original controlled reader/restore scenarios; no network or physical qualification."""
import asyncio
from datetime import datetime, timedelta, timezone

import httpx2
import pytest

from pulse.charger import ChargerReader
from pulse.coordinator import Coordinator
from pulse.evcc import EvccConfig, iso


class Lab:
    def __init__(self):
        self.now = datetime(2026, 10, 8, 10, 7, tzinfo=timezone.utc)
        self.tick = 0
        self.entity = "lp-1"
        self.fail_energy = False
        self.paths = []
        self.parameters = []
        self.block_energy = False
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.raise_after_release = False

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)
        self.tick += seconds

    async def handler(self, request):
        self.paths.append(request.url.path)
        if request.url.path == "/api/state":
            return httpx2.Response(200, json={"loadpoints": [{"title": "Casa", "name": self.entity,
                "connected": True, "charging": True, "sessionEnergy": 4, "chargePower": 7200}]})
        if request.url.path == "/api/sessions":
            return httpx2.Response(200, json=[{"id": 1, "loadpoint": "Casa", "created": "2026-10-08T08:00:00Z",
                "finished": "2026-10-08T09:00:00Z", "chargedEnergy": 4}])
        assert request.url.path == "/api/history/energy"
        self.parameters.append(dict(request.url.params))
        if self.block_energy:
            self.entered.set()
            await self.release.wait()
        if self.fail_energy or self.raise_after_release:
            return httpx2.Response(503, json={"PRIVATE": "not published"})
        start = datetime.fromisoformat(request.url.params["from"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(request.url.params["to"].replace("Z", "+00:00"))
        data = []
        while start + timedelta(minutes=15) <= end:
            # Deliberate hole: absence must never be converted to a zero slot.
            if start.minute != 30:
                data.append({"start": iso(start), "end": iso(start + timedelta(minutes=15)),
                             "energy": 2, "returnEnergy": 0})
            start += timedelta(minutes=15)
        return httpx2.Response(200, json=[{"title": "Casa", "group": "loadpoint", "data": data}])


async def make_reader(path, lab, *, enabled=True):
    coordinator = Coordinator(path)
    await coordinator.start()
    coordinator.store._clock = lambda: lab.now
    config = EvccConfig(True, "http://127.0.0.1:7099", "Casa", history_months=1,
                        energy_history_enabled=enabled)
    return coordinator, ChargerReader(config, coordinator, transport=httpx2.MockTransport(lab.handler),
                                       clock=lambda: lab.now, monotonic=lambda: lab.tick)


def test_background_candidates_keep_holes_and_do_not_price_or_poll_on_views(tmp_path):
    async def run():
        lab = Lab()
        coordinator, reader = await make_reader(tmp_path / "pulse.sqlite3", lab)
        try:
            await reader.poll_once()
            assert lab.paths == ["/api/state", "/api/sessions"]
            lab.advance(3600)
            await reader.poll_once()
            status = await reader.status()
            energy = status["energy_history"]
            assert status["status"] == status["history_status"] == "ready"
            assert energy["status"] == "received" and energy["validation"] == "not_qualified"
            assert energy["retained_intervals"] == 2 and energy["total_kwh"] == 4
            archive = coordinator.store.export_data()
            assert len(archive["charger_energy"]["intervals"]) == 2
            assert archive["charger_pricing"] == []
            assert archive["charges"] == []
            assert all(row["started_at"] != "2026-10-08T10:30:00.000000Z"
                       for row in archive["charger_energy"]["intervals"])
            before = list(lab.paths)
            for _ in range(5):
                await reader.status()
                await reader.sessions()
            assert lab.paths == before
            assert lab.parameters[-1]["from"] == "2026-10-08T10:15:00Z"
            assert lab.parameters[-1]["to"] == "2026-10-08T11:00:00Z"
            assert lab.parameters[-1]["name"] == "lp-1"
        finally:
            await reader.close()
            await coordinator.close()
    asyncio.run(run())


def test_experimental_error_preserves_core_and_remains_until_new_history_response(tmp_path):
    async def run():
        lab = Lab()
        coordinator, reader = await make_reader(tmp_path / "pulse.sqlite3", lab)
        try:
            await reader.poll_once()
            lab.advance(3600)
            await reader.poll_once()
            old = coordinator.store.export_data()["charger_energy"]["intervals"]
            lab.fail_energy = True
            lab.advance(600)
            await reader.poll_once()
            assert (await reader.status())["energy_history"]["status"] == "error"
            lab.advance(60)
            await reader.poll_once()
            status = await reader.status()
            assert status["status"] == "ready" and status["history_status"] == "ready"
            assert status["energy_history"]["status"] == "error"
            assert "PRIVATE" not in str(status)
            assert (await reader.sessions())["total"] == 1
            assert coordinator.store.export_data()["charger_energy"]["intervals"] == old
        finally:
            await reader.close()
            await coordinator.close()
    asyncio.run(run())


def test_history_receipt_follows_response_without_extending_observed_window(tmp_path):
    async def run():
        lab = Lab()
        coordinator, reader = await make_reader(tmp_path / "pulse.sqlite3", lab)
        task = None
        try:
            await reader.poll_once()
            lab.advance(3600)
            observed_at = lab.now
            lab.block_energy = True
            task = asyncio.create_task(reader.poll_once())
            await asyncio.wait_for(lab.entered.wait(), 1)
            lab.advance(3)
            lab.release.set()
            await asyncio.wait_for(task, 1)
            archive = coordinator.store.export_data()["charger_energy"]
            epoch = archive["epochs"][0]
            assert datetime.fromisoformat(epoch["last_seen_at"].replace("Z", "+00:00")) == observed_at
            assert archive["intervals"]
            for slot in archive["intervals"]:
                assert datetime.fromisoformat(slot["received_at"].replace("Z", "+00:00")) == lab.now
                assert datetime.fromisoformat(slot["ended_at"].replace("Z", "+00:00")) <= observed_at
            assert (await reader.status())["energy_history"]["last_success_at"] == iso(lab.now)
        finally:
            lab.release.set()
            if task is not None:
                await task
            await reader.close()
            await coordinator.close()
    asyncio.run(run())


@pytest.mark.parametrize("entity", [None, "bad/name"])
def test_missing_identity_does_not_disable_charger_or_query_metrics(tmp_path, entity):
    async def run():
        lab = Lab()
        lab.entity = entity
        coordinator, reader = await make_reader(tmp_path / "pulse.sqlite3", lab)
        try:
            await reader.poll_once()
            lab.advance(3600)
            await reader.poll_once()
            status = await reader.status()
            assert status["status"] == "ready" and (await reader.sessions())["total"] == 1
            assert status["energy_history"]["reason_code"] == "no_identity"
            assert "/api/history/energy" not in lab.paths
        finally:
            await reader.close()
            await coordinator.close()
    asyncio.run(run())


@pytest.mark.parametrize("late_failure", [False, True])
def test_restore_discards_inflight_curve_and_starts_new_observed_binding(tmp_path, late_failure):
    async def run():
        lab = Lab()
        coordinator, reader = await make_reader(tmp_path / "pulse.sqlite3", lab)
        task = None
        try:
            await reader.poll_once()
            lab.advance(3600)
            lab.block_energy = True
            lab.raise_after_release = late_failure
            task = asyncio.create_task(reader.poll_once())
            await asyncio.wait_for(lab.entered.wait(), 1)
            document = coordinator.store.export_data()
            await coordinator.restore(document)
            assert (await reader.status())["status"] == "waiting"
            lab.release.set()
            await asyncio.wait_for(task, 1)
            restored = coordinator.store.export_data()["charger_energy"]
            assert restored["intervals"] == []
            assert all(epoch["closed_at"] is not None for epoch in restored["epochs"])
            assert (await reader.status())["energy_history"]["status"] == "waiting"
            old_id = restored["epochs"][0]["id"]
            lab.block_energy = lab.raise_after_release = False
            lab.advance(1)
            await reader.poll_once()
            epochs = coordinator.store.export_data()["charger_energy"]["epochs"]
            assert len(epochs) == 2 and any(e["id"] != old_id and e["closed_at"] is None for e in epochs)
            assert coordinator.store.export_data()["charger_energy"]["intervals"] == []
        finally:
            lab.release.set()
            if task is not None:
                await task
            await reader.close()
            await coordinator.close()
    asyncio.run(run())


def test_default_has_no_experimental_requests_or_storage(tmp_path):
    async def run():
        lab = Lab()
        coordinator, reader = await make_reader(tmp_path / "pulse.sqlite3", lab, enabled=False)
        try:
            await reader.poll_once()
            lab.advance(3600)
            await reader.poll_once()
            assert (await reader.status())["energy_history"]["status"] == "disabled"
            assert "/api/history/energy" not in lab.paths
            assert coordinator.store.export_data()["charger_energy"] == {"epochs": [], "intervals": []}
        finally:
            await reader.close()
            await coordinator.close()
    asyncio.run(run())


def test_restart_closes_bindings_of_former_configurations(tmp_path):
    async def run():
        lab = Lab()
        coordinator, reader = await make_reader(tmp_path / "pulse.sqlite3", lab)
        try:
            await reader.poll_once()
            coordinator.store.observe_charger_energy("a" * 64,
                {"entity": "lp-2", "loadpoint": "Otro punto", "contract_revision": "evcc-0.316.2-15m-v1"},
                received_at=iso(lab.now))
            original_ids = {e["id"] for e in coordinator.store.export_data()["charger_energy"]["epochs"]}
            lab.advance(60)
            await reader.start()
            # A new process has no corroboration for any previous configuration.
            epochs = coordinator.store.export_data()["charger_energy"]["epochs"]
            assert all(e["closed_at"] is not None for e in epochs if e["id"] in original_ids)
        finally:
            await reader.close()
            await coordinator.close()
    asyncio.run(run())


def test_status_does_not_mix_a_former_live_read_with_restored_state(tmp_path):
    async def run():
        lab = Lab()
        coordinator, reader = await make_reader(tmp_path / "pulse.sqlite3", lab)
        original_call = coordinator.call
        task = None
        entered, release = asyncio.Event(), asyncio.Event()
        try:
            await reader.poll_once()
            document = coordinator.store.export_data()
            async def interleaved(method, *args, **kwargs):
                result = await original_call(method, *args, **kwargs)
                if method == "charger_observation":
                    entered.set()
                    await release.wait()
                return result
            coordinator.call = interleaved
            task = asyncio.create_task(reader.status())
            await asyncio.wait_for(entered.wait(), 1)
            await coordinator.restore(document)
            release.set()
            result = await asyncio.wait_for(task, 1)
            assert result["status"] == "waiting" and result["observation"] is None
            assert result["last_success_at"] is None
            assert result["history_status"] == "waiting"
            assert result["energy_history"]["status"] == "waiting"
        finally:
            release.set()
            if task is not None:
                await task
            coordinator.call = original_call
            await reader.close()
            await coordinator.close()
    asyncio.run(run())
