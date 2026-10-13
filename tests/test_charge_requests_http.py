"""HTTP boundary for authenticated manual charge creation reservations."""

import asyncio
from datetime import datetime, timedelta, timezone
import threading
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from pulse.app import create_app
from pulse.pvpc import parse_day


def _fake_prices(day):
    madrid = ZoneInfo("Europe/Madrid")
    start = datetime(day.year, day.month, day.day, tzinfo=madrid).astimezone(timezone.utc)
    end = (datetime(day.year, day.month, day.day, tzinfo=madrid) + timedelta(days=1)).astimezone(timezone.utc)
    values = []
    cursor = start
    while cursor < end:
        values.append({"datetime": cursor.astimezone(madrid).isoformat(), "value": 100})
        cursor += timedelta(hours=1)
    return parse_day(
        {"included": [{"type": "PVPC", "id": "1001", "attributes": {"values": values}}]},
        day,
        datetime.now(timezone.utc),
    )


def _auth(client):
    return {"X-Pulse-CSRF": client.get("/api/session").json()["csrf_token"]}


def test_server_reservation_replay_skips_pvpc_and_quote_rejects_metadata(tmp_path):
    provider_days = []

    async def fetch(day):
        provider_days.append(day)
        return _fake_prices(day)

    app = create_app(tmp_path / "pulse.sqlite3", hosts=["testserver"], pvpc_fetcher=fetch)
    with TestClient(app) as client:
        headers = _auth(client)
        initial_revision = app.state.coordinator.revision
        assert client.post("/api/charges/requests", json={}).status_code == 403
        assert client.post(
            "/api/charges/requests", json={"id": "a" * 32}, headers=headers
        ).status_code == 422
        reserved = client.post("/api/charges/requests", json={}, headers=headers)
        assert reserved.status_code == 200, reserved.text
        assert app.state.coordinator.revision == initial_revision
        request = reserved.json()
        assert len(request["id"]) == 32
        assert all(character in "0123456789abcdef" for character in request["id"])
        expires_at = datetime.fromisoformat(request["expires_at"].replace("Z", "+00:00"))
        remaining = expires_at - datetime.now(timezone.utc)
        assert timedelta(days=6, hours=23) < remaining <= timedelta(days=7, seconds=5)

        quote_calls = []
        real_quote = app.state.electricity.quote

        async def tracked_quote(values):
            quote_calls.append(dict(values))
            return await real_quote(values)

        app.state.electricity.quote = tracked_quote
        payload = {
            "started_at": "2025-10-01T10:00:00+02:00",
            "ended_at": "2025-10-01T11:00:00+02:00",
            "kwh": 10,
            "pricing_method": "pvpc",
            "request_id": request["id"],
        }
        explicit_null = client.post(
            "/api/charges",
            json={**payload, "request_id": None},
            headers=headers,
        )
        assert explicit_null.status_code == 422
        invalid_window = client.post(
            "/api/charges",
            json={
                **payload,
                "ended_at": "2025-10-04T10:00:01+02:00",
            },
            headers=headers,
        )
        assert invalid_window.status_code == 400
        assert quote_calls == [] and provider_days == []

        preview = client.post("/api/charges/quote", json=payload, headers=headers)
        assert preview.status_code == 400
        assert quote_calls == []

        unknown = client.post("/api/charges", json={**payload, "request_id": "f" * 32}, headers=headers)
        assert unknown.status_code == 409
        assert quote_calls == [] and provider_days == []
        assert client.get("/api/charges").json()["total"] == 0

        created = client.post("/api/charges", json=payload, headers=headers)
        assert created.status_code == 200, created.text
        charge = created.json()["charge"]
        assert len(quote_calls) == 1
        assert len(provider_days) == 1

        replay = client.post("/api/charges", json=payload, headers=headers)
        assert replay.status_code == 200, replay.text
        assert replay.json()["charge"] == charge
        assert len(quote_calls) == 1
        assert len(provider_days) == 1
        assert client.get("/api/charges").json()["total"] == 1

        changed = client.post("/api/charges", json={**payload, "kwh": 11}, headers=headers)
        assert changed.status_code == 409
        assert len(quote_calls) == 1
        archive = client.get("/api/export").json()
        assert archive["schema_version"] == 15
        assert "manual_charge_requests" not in archive


def test_legacy_post_without_request_id_allows_distinct_identical_creates(tmp_path):
    app = create_app(tmp_path / "pulse.sqlite3", hosts=["testserver"])
    with TestClient(app) as client:
        payload = {"started_at": "2026-10-01T10:00:00Z", "kwh": 10}
        first = client.post(
            "/api/charges",
            json=payload,
            headers=_auth(client),
        )
        second = client.post("/api/charges", json=payload, headers=_auth(client))
        assert first.status_code == second.status_code == 200
        assert first.json()["ok"] is True and second.json()["ok"] is True
        assert first.json()["charge"]["id"] != second.json()["charge"]["id"]
        assert client.get("/api/charges").json()["total"] == 2
        with app.state.coordinator.store._read_transaction() as connection:
            assert connection.execute("SELECT COUNT(*) FROM manual_charge_requests").fetchone()[0] == 0


def test_import_during_pvpc_wait_invalidates_request_before_final_insert(tmp_path):
    fetch_started = threading.Event()
    release_fetch = threading.Event()

    async def fetch(day):
        fetch_started.set()
        resumed = await asyncio.to_thread(release_fetch.wait, 10)
        assert resumed, "PVPC test gate timed out"
        return _fake_prices(day)

    app = create_app(tmp_path / "pulse.sqlite3", hosts=["testserver"], pvpc_fetcher=fetch)
    with TestClient(app) as client:
        headers = _auth(client)
        archive = client.get("/api/export").json()
        reservation = client.post("/api/charges/requests", json={}, headers=headers).json()
        payload = {
            "started_at": "2025-10-01T10:00:00+02:00",
            "ended_at": "2025-10-01T11:00:00+02:00",
            "kwh": 10,
            "pricing_method": "pvpc",
            "request_id": reservation["id"],
        }
        result = {}

        def submit_charge():
            try:
                result["response"] = client.post("/api/charges", json=payload, headers=headers)
            except BaseException as error:  # Surface thread failures in the test thread.
                result["error"] = error

        worker = threading.Thread(target=submit_charge)
        worker.start()
        try:
            assert fetch_started.wait(5), "PVPC quote did not reach the test gate"
            restored = client.post(
                "/api/import", json={"confirmed": True, "data": archive}, headers=headers
            )
            assert restored.status_code == 200, restored.text
        finally:
            release_fetch.set()
            worker.join(timeout=10)

        assert not worker.is_alive()
        assert "error" not in result, result.get("error")
        assert result["response"].status_code == 409
        assert client.get("/api/charges").json()["total"] == 0
