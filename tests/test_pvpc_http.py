"""Cotización y persistencia por HTTP con datos horarios sintéticos originales."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from pulse.app import create_app
from pulse.pvpc import PvpcError, parse_day


def fake_prices(day, prices=None):
    local = datetime.combine(day, datetime.min.time(), tzinfo=ZoneInfo("Europe/Madrid"))
    start = local.astimezone(timezone.utc)
    end = (local + timedelta(days=1)).astimezone(timezone.utc)
    values = []
    cursor = start
    while cursor < end:
        hour = cursor.astimezone(ZoneInfo("Europe/Madrid")).hour
        values.append({"datetime": cursor.astimezone(ZoneInfo("Europe/Madrid")).isoformat(),
                       "value": (prices or {}).get(hour, 100)})
        cursor += timedelta(hours=1)
    return parse_day({"included": [{"id": "1001", "type": "PVPC", "attributes": {
        "title": "PVPC", "magnitude": "price", "values": values}}]}, day,
        fetched_at=datetime.now(timezone.utc))


def auth(client):
    return {"X-Pulse-CSRF": client.get("/api/session").json()["csrf_token"]}


def test_quote_calculates_hour_overlap_and_server_persists_its_own_snapshot(tmp_path):
    calls = []

    async def fetch(day):
        calls.append(day)
        return fake_prices(day, {10: 100, 11: 300})

    app = create_app(tmp_path / "pulse.sqlite3", hosts=["testserver"], pvpc_fetcher=fetch)
    with TestClient(app) as client:
        headers = auth(client)
        payload = {"started_at": "2025-10-01T10:30:00+02:00", "ended_at": "2025-10-01T11:30:00+02:00",
                   "kwh": 10.0, "pricing_method": "pvpc"}
        response = client.post("/api/charges/quote", json=payload, headers=headers)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        quote = response.json()
        assert quote["cost_eur"] == 2
        assert quote["pricing"]["accuracy"] == "estimated_uniform"
        assert quote["pricing"]["taxes_included"] is False
        assert len(quote["pricing"]["intervals"]) == 2
        stored = client.post("/api/charges", json=payload, headers=headers)
        assert stored.status_code == 200, stored.text
        charge = stored.json()["charge"]
        assert charge["cost_eur"] == quote["cost_eur"]
        assert charge["source"] == "manual"
        assert len(calls) == 1
        assert client.put("/api/settings", json={"tariff_eur_kwh": .8}, headers=headers).status_code == 200
        assert client.get("/api/charges").json()["items"][0]["pricing"] == charge["pricing"]
        data = client.get("/api/export").json()
        assert data["schema_version"] == 12
        restored = client.post("/api/import", json={"confirmed": True, "data": data}, headers=headers)
        assert restored.status_code == 200, restored.text
        assert client.get("/api/charges").json()["items"][0]["cost_eur"] == 2
        changed = client.put(f'/api/charges/{charge["id"]}', json={"kwh": 20.0}, headers=headers)
        assert changed.status_code == 200, changed.text
        assert changed.json()["charge"]["cost_eur"] == 4
        # El cliente no puede inyectar un desglose o un coste supuestamente oficial.
        assert client.post("/api/charges", json={**payload, "pricing": quote["pricing"]}, headers=headers).status_code == 422
        assert client.post("/api/charges/quote", json=payload).status_code == 403


def test_failure_preserves_records_and_rejects_missing_end_and_bad_modes(tmp_path):
    async def unavailable(day):
        raise PvpcError("No disponible")

    with TestClient(create_app(tmp_path / "pulse.sqlite3", hosts=["testserver"],
                              pvpc_fetcher=unavailable)) as client:
        headers = auth(client)
        payload = {"started_at": "2025-10-01T10:00:00Z", "ended_at": "2025-10-01T12:00:00Z",
                   "kwh": 10.0, "pricing_method": "pvpc"}
        response = client.post("/api/charges", json=payload, headers=headers)
        assert response.status_code == 503
        assert client.get("/api/charges").json()["total"] == 0
        assert client.post("/api/charges", json={k: v for k, v in payload.items() if k != "ended_at"}, headers=headers).status_code == 400
        assert client.post("/api/charges/quote", json={**payload, "pricing_method": "manual"}, headers=headers).status_code == 400
        assert client.post("/api/charges", json={**payload, "pricing_method": "other"}, headers=headers).status_code == 422


def test_negative_official_price_is_preserved_in_record_and_analytics(tmp_path):
    async def fetch(day):
        return fake_prices(day, {10: -100, 11: -100})

    app = create_app(tmp_path / "pulse.sqlite3", hosts=["testserver"], pvpc_fetcher=fetch)
    with TestClient(app) as client:
        app.state.store._clock = lambda: datetime(2026, 10, 8, tzinfo=timezone.utc)
        response = client.post("/api/charges", json={"started_at": "2026-10-01T10:00:00+02:00",
            "ended_at": "2026-10-01T12:00:00+02:00", "kwh": 10.0, "pricing_method": "pvpc"}, headers=auth(client))
        assert response.status_code == 200, response.text
        assert response.json()["charge"]["cost_eur"] == -1
        assert client.get("/api/analytics/energy").json()["manual"]["cost_eur"] == -1


@pytest.mark.parametrize("sign", [1, -1])
def test_half_cent_rounding_survives_persistence_and_restore(tmp_path, sign):
    async def fetch(day):
        return fake_prices(day, {10: 10 * sign, 11: 10 * sign, 12: 595 * sign})

    app = create_app(tmp_path / "pulse.sqlite3", hosts=["testserver"], pvpc_fetcher=fetch)
    with TestClient(app) as client:
        headers = auth(client)
        response = client.post("/api/charges", json={"started_at": "2025-10-01T10:00:00+02:00",
            "ended_at": "2025-10-01T13:00:00+02:00", "kwh": 1.0, "pricing_method": "pvpc"}, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["charge"]["cost_eur"] == .21 * sign
        document = client.get("/api/export").json()
        restore = client.post("/api/import", json={"confirmed": True, "data": document}, headers=headers)
        assert restore.status_code == 200, restore.text
        assert client.get("/api/charges").json()["items"][0]["cost_eur"] == .21 * sign
        document["charges"][0]["cost_eur"] = .20 * sign
        assert client.post("/api/import", json={"confirmed": True, "data": document}, headers=headers).status_code == 400
