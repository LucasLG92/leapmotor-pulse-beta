"""Local cache/lifecycle/API validation with fake evcc; no real charger or server."""
import asyncio
from datetime import datetime, timedelta, timezone
import time

from fastapi.testclient import TestClient
import httpx2

from pulse.app import create_app
from pulse.charger import ChargerReader
from pulse.config import Config
from pulse.coordinator import Coordinator
from pulse.evcc import EvccConfig
from pulse.pvpc import parse_day
from zoneinfo import ZoneInfo


async def synthetic_pvpc(day):
    madrid=ZoneInfo("Europe/Madrid")
    start=datetime(day.year,day.month,day.day,tzinfo=madrid).astimezone(timezone.utc)
    end=(datetime(day.year,day.month,day.day,tzinfo=madrid)+timedelta(days=1)).astimezone(timezone.utc)
    points=[]
    while start<end:
        points.append({"datetime":start.astimezone(madrid).isoformat(),"value":100})
        start+=timedelta(hours=1)
    return parse_day({"included":[{"type":"PVPC","id":"1001","attributes":{"values":points}}]},
                     day,datetime.now(timezone.utc))


def test_disabled_charger_has_no_task_or_network_and_no_data(tmp_path):
    def unexpected(_request): raise AssertionError("Disabled reader made a network request")
    app = create_app(tmp_path/"pulse.sqlite3", config=Config(hosts=("testserver",)),
                     evcc_transport=httpx2.MockTransport(unexpected))
    with TestClient(app) as client:
        status = client.get("/api/charger")
        assert status.status_code == 200 and status.json()["status"] == "disabled"
        assert status.headers["Cache-Control"] == "no-store"
        assert app.state.charger.task is None
        assert client.get("/api/charger/sessions").json()["total"] == 0
        assert client.post("/api/charger").status_code in (403,405)


def test_one_reader_ui_and_quote_never_poll_upstream_or_count_manual_twice(tmp_path):
    calls=[]; now=datetime.now(timezone.utc)
    async def handler(request):
        calls.append(request.url.path)
        if request.url.path=="/api/state":
            return httpx2.Response(200,json={"loadpoints":[{"title":"Casa","connected":True,
                "charging":False,"chargePower":0,"chargedEnergy":10,"chargeDuration":3600.25}]})
        return httpx2.Response(200,json=[{"id":9,"loadpoint":"Casa","created":(now-timedelta(hours=3)).isoformat(),
                "finished":(now-timedelta(hours=1)).isoformat(),"chargedEnergy":10,"chargeDuration":3600250000000}])
    config=Config(hosts=("testserver",),evcc=EvccConfig(True,"http://127.0.0.1:7070","Casa",history_months=1))
    app=create_app(tmp_path/"pulse.sqlite3",config=config,evcc_transport=httpx2.MockTransport(handler),
                   pvpc_fetcher=synthetic_pvpc)
    with TestClient(app) as client:
        csrf=client.get("/api/session").json()["csrf_token"]
        deadline=time.monotonic()+2
        while client.get("/api/charger").json()["history_status"]!="ready":
            assert time.monotonic()<deadline
            time.sleep(.01)
        assert calls==["/api/state","/api/sessions"]
        for _ in range(10):
            assert client.get("/api/charger").json()["observation"]["charging"] is False
            assert client.get("/api/charger/sessions?limit=1").json()["total"]==1
            assert client.get("/api/charger/sessions?limit=1").json()["items"][0]["active_seconds"]==3600.25
            assert client.get("/api/state").json()["energy"]["totals_by_source"]["manual"]["kwh"]==0
        assert calls==["/api/state","/api/sessions"] and app.state.collector is None
        assert client.get("/api/charger/sessions?limit=101").status_code==422
        assert client.get("/api/charger/sessions?offset=1001").status_code==422
        item=client.get("/api/charger/sessions").json()["items"][0]
        payload={"started_at":item["started_at"],"ended_at":item["ended_at"],"kwh":item["kwh"],"pricing_method":"pvpc"}
        assert client.post("/api/charges/quote",json=payload).status_code==403
        quote=client.post("/api/charges/quote",json=payload,headers={"X-Pulse-CSRF":csrf})
        assert quote.status_code==200 and quote.json()["cost_eur"]==1
        assert calls==["/api/state","/api/sessions"]
        exported=client.get("/api/export").json()
        assert exported["schema_version"]==15 and len(exported["charger"])==1
        assert "127.0.0.1:7070" not in str(exported)
    assert app.state.charger.task is None


def test_reader_preserves_history_and_marks_errors_staleness_history_cooldown(tmp_path):
    async def run():
        current=datetime.now(timezone.utc);tick=100.0;failing=False;calls=[]
        session_start=current-timedelta(hours=3);session_end=current-timedelta(hours=2)
        async def handler(request):
            calls.append(request.url.path)
            if failing: return httpx2.Response(503,json={"PRIVATE":"do not leak"})
            if request.url.path=="/api/state":
                return httpx2.Response(200,json={"loadpoints":[{"title":"Casa","connected":True,"charging":True,
                    "chargePower":7200,"chargedEnergy":4,"chargeDuration":900.5}]})
            return httpx2.Response(200,json=[{"id":1,"loadpoint":"Casa","created":session_start.isoformat(),
                "finished":session_end.isoformat(),"chargedEnergy":4}])
        coordinator=Coordinator(tmp_path/"pulse.sqlite3");await coordinator.start()
        reader=ChargerReader(EvccConfig(True,"http://127.0.0.1:7070","Casa",history_months=1),coordinator,
            transport=httpx2.MockTransport(handler),clock=lambda:current,monotonic=lambda:tick)
        try:
            await reader.poll_once()
            assert (await reader.status())["status"]=="ready"
            first=(await reader.sessions())["items"]
            tick+=181
            assert (await reader.status())["status"]=="stale"
            failing=True
            await reader.poll_once()
            status=await reader.status()
            assert status["status"]=="error" and status["observation"]["charged_kwh"]==4
            assert "PRIVATE" not in str(status)
            assert (await reader.sessions())["items"]==first
            assert calls.count("/api/sessions")==1
            tick+=600;await reader.poll_once()
            assert (await reader.status())["history_status"]=="error"
            assert calls.count("/api/sessions")==2
            failing=False;current+=timedelta(seconds=1);tick+=600
            await reader.poll_once()
            assert (await reader.sessions())["total"]==1
            assert (await reader.status())["status"]=="ready"
        finally:
            await reader.close();await coordinator.close()
    asyncio.run(run())
