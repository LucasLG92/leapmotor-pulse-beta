"""Session-linked temporal pricing: synthetic energy/prices, no physical source."""
import asyncio
from copy import deepcopy
from datetime import date
import json
import sqlite3

import pytest
import httpx2
from fastapi.testclient import TestClient

from pulse.charger_pricing import ChargerPricing
from pulse.contracts import ChargeQuote
from pulse.coordinator import Coordinator
from pulse.domain import DomainError, Store
from pulse.electricity import Electricity
from pulse import pvpc
from pulse.app import create_app
from schema_helpers import downgrade_telemetry_to_v15
from pulse.config import Config
from pulse.evcc import EvccConfig
from pulse.temporal_pvpc import quote_charge_intervals
from test_charger_pricing_history import NOW, scope, session, synthetic_day


def record():
    return session(ended_at="2026-10-08T14:00:00Z", kwh=10)


def prices():
    day = synthetic_day(date(2026, 10, 8))
    for row in day["intervals"]:
        if row["started_at"] == "2026-10-08T12:00:00Z":
            row["price_eur_kwh"] = 0.1
        elif row["started_at"] == "2026-10-08T13:00:00Z":
            row["price_eur_kwh"] = 0.3
    return day


def energy(item=None, *, first=1, revision="v1"):
    item = item or record()
    return {"version": 1, "scope": scope(), "session_id": item["id"],
            "source_id": "synthetic-session-meter", "source_version": revision,
            "basis": "charger_total", "intervals": [
                {"started_at": item["started_at"], "ended_at": "2026-10-08T13:00:00Z", "kwh": first},
                {"started_at": "2026-10-08T13:00:00Z", "ended_at": item["ended_at"], "kwh": 10-first}]}


def uniform(item):
    return pvpc.quote_charge(item["started_at"], item["ended_at"], item["kwh"],
                             [prices()], calculated_at=NOW)


def temporal(item, distribution=None):
    return quote_charge_intervals(item["started_at"], item["ended_at"], item["kwh"],
                                   distribution or energy(item), [prices()], calculated_at=NOW)


def initialized(path):
    store = Store(path, clock=lambda: NOW)
    item = record()
    store.ingest_charger(scope(), sessions=[item])
    store.update_settings({"pricing_method": "pvpc"})
    return store, store.charger_pricing_candidates(scope())[0]


def test_uniform_upgrade_and_curve_correction_preserve_auditable_quotes(tmp_path):
    store, item = initialized(tmp_path / "source.sqlite3")
    old = uniform(item)
    assert old["cost_eur"] == 2.0
    store.save_charger_pricing(scope(), item, old)
    new = temporal(item)
    assert new["cost_eur"] == 2.8
    assert store.save_charger_temporal_pricing(scope(), item, new, old) == {"changed": True}
    changed = temporal(item, energy(item, first=4, revision="v2"))
    assert changed["cost_eur"] == 2.2
    assert store.save_charger_temporal_pricing(scope(), item, changed, new) == {"changed": True}
    events = store.charger_pricing_history(scope(), item["id"])["items"]
    assert [event["quote"]["cost_eur"] for event in events] == [2.2, 2.8, 2.0]
    assert store.save_charger_temporal_pricing(scope(), item, changed, changed) == {"changed": False}
    # An older reader cannot replace a newer result, even for identical session inputs.
    assert store.save_charger_temporal_pricing(scope(), item, new, old) == {"changed": False}
    assert store.charger_pricing_history(scope(), item["id"])["total"] == 3


def test_quote_event_failure_rolls_back_the_replacement(tmp_path, monkeypatch):
    store, item = initialized(tmp_path / "source.sqlite3")
    old = uniform(item)
    store.save_charger_pricing(scope(), item, old)
    old = store.priced_charger_sessions(scope())["items"][0]["pvpc"]
    def fail(*args, **kwargs):
        raise RuntimeError("Synthetic event failure")
    monkeypatch.setattr(store, "_append_charger_pricing_event", fail)
    with pytest.raises(RuntimeError):
        store.save_charger_temporal_pricing(scope(), item, temporal(item), old)
    assert store.priced_charger_sessions(scope())["items"][0]["pvpc"] == old
    assert store.charger_pricing_history(scope(), item["id"])["total"] == 1


def test_temporal_identity_export_and_private_response_fields(tmp_path):
    store, item = initialized(tmp_path / "source.sqlite3")
    quote = temporal(item)
    store.save_charger_temporal_pricing(scope(), item, quote, None)
    document = store.export_data()
    assert document["schema_version"] == 16
    assert document["charger_pricing"][0]["quote"] == quote
    public = ChargeQuote.model_validate(quote).model_dump()
    distribution = public["pricing"]["energy_distribution"]
    assert "scope" not in distribution and "session_id" not in distribution
    assert distribution["intervals"] == quote["pricing"]["energy_distribution"]["intervals"]
    restored = Store(tmp_path / "restored.sqlite3", clock=lambda: NOW)
    restored.import_data(document)
    assert restored.priced_charger_sessions(scope())["items"][0]["pvpc"] == quote
    assert restored.charger_pricing_history(scope(), item["id"])["total"] == 1
    with sqlite3.connect(restored.path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 20
    for version in (11, 10, 9, 8):
        old = deepcopy(document)
        old["schema_version"] = version
        old.pop("manual_journeys")
        old["settings"].pop("vehicle_color")
        if version < 11:
            old.pop("charger_pricing_history")
        if version < 10:
            old.pop("charger_energy")
        if version < 9:
            old.pop("observed_events")
        with pytest.raises(DomainError):
            restored.import_data(old)
    assert restored.priced_charger_sessions(scope())["items"][0]["pvpc"] == quote
    wrong = deepcopy(quote)
    wrong["pricing"]["energy_distribution"]["session_id"] = "a"*32
    with pytest.raises(DomainError):
        store.save_charger_temporal_pricing(scope(), item, wrong, quote)
    wrong = deepcopy(quote)
    wrong["pricing"]["energy_distribution"]["scope"] = scope(2)
    with pytest.raises(DomainError):
        store.save_charger_temporal_pricing(scope(), item, wrong, quote)


def test_temporal_results_are_not_accepted_for_manual_charges(tmp_path):
    store, item = initialized(tmp_path / "source.sqlite3")
    with pytest.raises(DomainError):
        store.add_charge({"started_at": item["started_at"], "ended_at": item["ended_at"],
                          "kwh": item["kwh"], "pricing_method": "pvpc"}, pricing_snapshot=temporal(item))


async def prepared(path):
    coordinator = Coordinator(path)
    await coordinator.start()
    coordinator.store._clock = lambda: NOW
    item = record()
    await coordinator.call("ingest_charger", scope(), sessions=[item])
    await coordinator.call("update_settings", {"pricing_method": "pvpc"})
    item = (await coordinator.call("charger_pricing_candidates", scope()))[0]
    calls = []
    async def fetcher(day):
        calls.append(day)
        assert day == date(2026, 10, 8)
        return prices()
    return coordinator, item, Electricity(coordinator, fetcher=fetcher, clock=lambda: NOW), calls


def test_background_source_upgrade_unchanged_replay_and_none_preserve_result(tmp_path):
    async def run():
        coordinator, item, electricity, downloads = await prepared(tmp_path / "worker.sqlite3")
        raw = [None]
        reads = []
        async def reader(selected_scope, selected_session):
            reads.append(selected_session["id"])
            assert selected_scope == scope() and selected_session == item
            return deepcopy(raw[0])
        tick = [0.0]
        worker = ChargerPricing(scope(), coordinator, electricity, energy_curve_reader=reader,
                                 monotonic=lambda: tick[0])
        try:
            await worker.price_once()
            assert coordinator.store.priced_charger_sessions(scope())["items"][0]["pvpc"]["cost_eur"] == 2.0
            raw[0] = energy(item)
            tick[0] += 600
            await worker.price_once()
            assert coordinator.store.priced_charger_sessions(scope())["items"][0]["pvpc"]["cost_eur"] == 2.8
            for value in (deepcopy(raw[0]), None):
                raw[0] = value; tick[0] += 600
                await worker.price_once()
            assert len(downloads) == 1
            assert coordinator.store.charger_pricing_history(scope(), item["id"])["total"] == 2
            assert len(reads) == 4
        finally:
            await coordinator.close()
    asyncio.run(run())


@pytest.mark.parametrize("change", ["restore", "correction", "manual"])
def test_state_change_while_source_waits_discards_stale_quote(tmp_path, change):
    async def run():
        coordinator, item, electricity, downloads = await prepared(tmp_path / "worker.sqlite3")
        started, release = asyncio.Event(), asyncio.Event()
        async def reader(selected_scope, selected_session):
            started.set(); await release.wait()
            return energy(selected_session)
        worker = ChargerPricing(scope(), coordinator, electricity, energy_curve_reader=reader)
        job = asyncio.create_task(worker.price_once())
        try:
            await asyncio.wait_for(started.wait(), 1)
            if change == "restore":
                archive = await coordinator.call("export_data")
                await coordinator.restore(archive)
            elif change == "correction":
                await coordinator.call("ingest_charger", scope(), sessions=[{**item, "kwh": 12,
                    "received_at": "2026-10-08T15:00:00Z"}])
            else:
                await coordinator.call("update_settings", {"pricing_method": "manual"})
            release.set(); await asyncio.wait_for(job, 1)
            assert coordinator.store.priced_charger_sessions(scope())["items"][0]["pvpc"] is None
            assert coordinator.store.charger_pricing_history(scope(), item["id"])["total"] == 0
            if change != "correction":
                assert downloads == []
        finally:
            if not job.done():
                job.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await job
            await coordinator.close()
    asyncio.run(run())


def test_invalid_source_and_timeout_do_not_price_or_delete_previous_result(tmp_path, monkeypatch):
    async def run():
        coordinator, item, electricity, downloads = await prepared(tmp_path / "worker.sqlite3")
        previous = uniform(item)
        await coordinator.call("save_charger_pricing", scope(), item, previous)
        previous = coordinator.store.priced_charger_sessions(scope())["items"][0]["pvpc"]
        tick = [0.0]
        async def invalid(selected_scope, selected_session):
            value = energy(selected_session)
            value["intervals"].pop()
            return value
        worker = ChargerPricing(scope(), coordinator, electricity, energy_curve_reader=invalid,
                                 monotonic=lambda: tick[0])
        try:
            await worker.price_once()
            assert downloads == []
            async def stalled(selected_scope, selected_session):
                await asyncio.Event().wait()
            monkeypatch.setattr("pulse.charger_pricing.ENERGY_READ_TIMEOUT_SECONDS", 0.01)
            worker.energy_curve_reader = stalled
            tick[0] += 600
            await asyncio.wait_for(worker.price_once(), 1)
            assert coordinator.store.priced_charger_sessions(scope())["items"][0]["pvpc"] == previous
            assert coordinator.store.charger_pricing_history(scope(), item["id"])["total"] == 1
        finally:
            await coordinator.close()
    asyncio.run(run())


def test_fair_bounded_reader_reaches_older_already_priced_sessions(tmp_path):
    async def run():
        coordinator, item, electricity, downloads = await prepared(tmp_path / "worker.sqlite3")
        records = [session(index=i, ended_at=item["ended_at"], kwh=10) for i in range(2, 11)]
        await coordinator.call("ingest_charger", scope(), sessions=records)
        seen = []
        async def reader(selected_scope, selected_session):
            seen.append(selected_session["id"])
            return None
        tick = [0.0]
        worker = ChargerPricing(scope(), coordinator, electricity, energy_curve_reader=reader,
                                 monotonic=lambda: tick[0])
        try:
            await worker.price_once()
            assert len(seen) == 4
            # Advancing cooldown before each pass would starve old rows without a cursor.
            for _ in range(2):
                tick[0] += 600
                before = len(seen)
                await worker.price_once()
                assert len(seen)-before <= 4
            assert len(set(seen)) == 10
            assert len(downloads) == 1
        finally:
            await coordinator.close()
    asyncio.run(run())


def test_closing_background_worker_cancels_waiting_source_without_quote(tmp_path):
    async def run():
        coordinator, item, electricity, downloads = await prepared(tmp_path / "worker.sqlite3")
        started, cancelled = asyncio.Event(), asyncio.Event()
        async def reader(selected_scope, selected_session):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        worker = ChargerPricing(scope(), coordinator, electricity, energy_curve_reader=reader)
        try:
            await worker.start()
            await asyncio.wait_for(started.wait(), 1)
            await asyncio.wait_for(worker.close(), 1)
            assert cancelled.is_set() and worker.task is None
            assert downloads == []
            assert coordinator.store.priced_charger_sessions(scope())["items"][0]["pvpc"] is None
        finally:
            await worker.close()
            await coordinator.close()
    asyncio.run(run())


def test_semantic_migration_preserves_db13_uniform_data(tmp_path):
    path = tmp_path / "old.sqlite3"
    store, item = initialized(path)
    store.save_charger_pricing(scope(), item, uniform(item))
    before = store.export_data()
    with sqlite3.connect(path) as db:
        downgrade_telemetry_to_v15(db)
        db.execute("ALTER TABLE settings DROP COLUMN vehicle_color")
        db.execute("DROP TABLE IF EXISTS manual_charge_requests")
        db.execute("DROP TABLE IF EXISTS manual_journey_requests")
        db.execute("DROP TABLE IF EXISTS manual_journeys")
        db.execute("PRAGMA user_version = 13")
        tables_before = db.execute(
            "SELECT name,sql FROM sqlite_master "
            "WHERE name NOT IN ('settings', 'telemetry_snapshots') ORDER BY name"
        ).fetchall()
        push_columns_before = {
            row[1] for row in db.execute("PRAGMA table_info(push_subscriptions)")
        }
    migrated = Store(path, clock=lambda: NOW)
    assert migrated.export_data() == before
    assert migrated.get_settings()["vehicle_color"] is None
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 20
        tables_after = db.execute(
            "SELECT name,sql FROM sqlite_master "
            "WHERE name NOT IN ('settings', 'telemetry_snapshots') ORDER BY name"
        ).fetchall()
        push_columns_after = {
            row[1] for row in db.execute("PRAGMA table_info(push_subscriptions)")
        }
        assert "vehicle_color" in {
            row[1] for row in db.execute("PRAGMA table_info(settings)")
        }
    backup_alert_objects = {
        "backup_alert_deliveries",
        "backup_alert_events",
        "backup_alert_observations",
        "backup_alert_runtime",
        "manual_charge_requests",
        "manual_journeys",
        "manual_journey_requests",
        "idx_backup_alert_deliveries_recent",
        "idx_backup_alert_events_recent",
        "idx_manual_charge_requests_expiry",
        "idx_manual_charge_requests_owner_expiry",
        "idx_manual_journeys_owner_started",
        "idx_manual_journey_requests_expiry",
        "idx_manual_journey_requests_owner_expiry",
        "sqlite_autoindex_backup_alert_deliveries_1",
        "sqlite_autoindex_backup_alert_events_1",
        "sqlite_autoindex_backup_alert_observations_1",
        "sqlite_autoindex_manual_charge_requests_1",
        "sqlite_autoindex_manual_journeys_1",
        "sqlite_autoindex_manual_journey_requests_1",
    }
    assert [
        row for row in tables_after
        if row[0] not in backup_alert_objects | {"push_subscriptions"}
    ] == [row for row in tables_before if row[0] != "push_subscriptions"]
    assert {row[0] for row in tables_after if row[0] in backup_alert_objects} == backup_alert_objects
    assert push_columns_after == push_columns_before | {
        "backup_alerts_enabled", "backup_alerts_consented_at",
    }


def test_private_http_quotes_and_history_do_not_expose_association_ids(tmp_path):
    config = Config(hosts=("testserver",), evcc=EvccConfig(True, "http://127.0.0.1:7070", "Garage"))
    async def unavailable(_day):
        raise pvpc.PvpcError("missing_day")
    app = create_app(tmp_path / "http.sqlite3", config=config, pvpc_fetcher=unavailable,
                     evcc_transport=httpx2.MockTransport(lambda request: httpx2.Response(503)))
    with TestClient(app) as client:
        store = app.state.store
        store._clock = lambda: NOW
        item = session(source_scope=config.evcc.scope, ended_at="2026-10-08T14:00:00Z", kwh=10)
        store.ingest_charger(config.evcc.scope, sessions=[item])
        store.update_settings({"pricing_method": "pvpc"})
        distribution = energy(item)
        distribution["scope"] = config.evcc.scope
        quote = temporal(item, distribution)
        store.save_charger_temporal_pricing(config.evcc.scope, item, quote, None)
        response = client.get("/api/charger/sessions")
        assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
        public = response.json()["items"][0]["pvpc"]
        assert public["cost_eur"] == 2.8 and public["pricing"]["accuracy"] == "estimated_intervals"
        response = client.get("/api/charger/pricing-history", params={"session_id": item["id"]})
        assert response.status_code == 200
        for result in (public, response.json()["items"][0]["quote"]):
            assert "scope" not in result["pricing"]["energy_distribution"]
            assert "session_id" not in result["pricing"]["energy_distribution"]
