"""Un collector explícito alimenta snapshots; navegar no hace lecturas del proveedor."""

from datetime import datetime, timedelta, timezone
import time

from fastapi.testclient import TestClient

from pulse.app import create_app


class SyntheticReadProvider:
    def __init__(self, age_seconds=0):
        self.age_seconds = age_seconds
        self.reads = 0

    def connection_status(self):
        return {"status": "connected", "reason": "Proveedor de prueba sintético.",
                "freshness": "unknown", "commands_enabled": False}

    def fetch_snapshot(self):
        self.reads += 1
        now = datetime.now(timezone.utc)
        return {"source": "MATE-API B10ReadClient",
                "observed_at": (now - timedelta(seconds=self.age_seconds)).isoformat(),
                "provider_received_at": now.isoformat(),
                "vehicle": {"battery_pct": 53.0, "range_km": 260.0, "odometer_km": 1500.0,
                            "locked": None, "climate_on": None, "charging": None},
                "field_sources": {"battery_pct": "soc_percent", "range_km": "expected_range_km",
                                  "odometer_km": "odometer_km", "locked": None,
                                  "climate_on": None, "charging": None}}


def wait_for_snapshot(client):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        data = client.get("/api/telemetry").json()
        if data["latest"] is not None:
            return data
        time.sleep(0.01)
    raise AssertionError("El collector sintético no almacenó su snapshot.")


def test_navigation_does_not_multiply_reads_and_real_controls_stay_closed(tmp_path):
    provider = SyntheticReadProvider(age_seconds=600)
    app = create_app(tmp_path / "pulse.sqlite3", hosts=["testserver"], provider=provider)
    with TestClient(app) as client:
        info = wait_for_snapshot(client)
        assert info["quality"]["temporal_status"] == "stale"
        assert info["quality"]["freshness"] == "unknown"
        for _ in range(12):
            state = client.get("/api/state").json()
            assert state["mode"] == "telemetry_read_only"
            assert state["vehicle"]["battery_pct"] == 53.0
            assert not any(command["enabled"] for command in state["commands"])
        assert provider.reads == 1
        history = client.get("/api/telemetry/history?limit=1").json()
        assert history["total"] == 1 and len(history["items"]) == 1
        assert client.get("/api/telemetry/history?limit=101").status_code == 422
        csrf = client.get("/api/session").json()["csrf_token"]
        assert client.post("/api/commands", json={"command": "unlock", "confirmed": True},
                           headers={"X-Pulse-CSRF": csrf}).status_code == 409
        assert client.get("/api/telemetry").headers["cache-control"] == "no-store"


def test_expired_values_hidden_from_current_vehicle_but_retained_as_history(tmp_path):
    provider = SyntheticReadProvider(age_seconds=10_800)
    with TestClient(create_app(tmp_path / "pulse.sqlite3", hosts=["testserver"], provider=provider)) as client:
        info = wait_for_snapshot(client)
        assert info["quality"]["temporal_status"] == "unavailable"
        state = client.get("/api/state").json()
        assert state["vehicle"]["battery_pct"] is None
        assert state["vehicle"]["range_km"] is None
        assert state["vehicle"]["observed_at"] == info["latest"]["observed_at"]
        assert client.get("/api/telemetry/history").json()["items"][0]["vehicle"]["battery_pct"] == 53.0


def test_normal_startup_has_no_collector_or_vehicle(tmp_path):
    app = create_app(tmp_path / "pulse.sqlite3", hosts=["testserver"])
    with TestClient(app) as client:
        assert app.state.collector is None
        info = client.get("/api/telemetry").json()
        assert info["latest"] is None
        assert not info["collector"]["enabled"]
        state = client.get("/api/state").json()
        assert state["vehicle"] is None and state["connection"]["status"] == "missing_material"
