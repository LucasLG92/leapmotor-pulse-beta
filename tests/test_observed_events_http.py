"""Private, paginated observed-state history; all vehicle values are synthetic."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from pulse.app import create_app
from pulse.config import Config
from pulse.domain import Store


def seed(path):
    now = datetime.now(timezone.utc)
    store = Store(path, clock=lambda: now)
    for back, locked, charging in [(60, True, False), (0, False, True)]:
        store.record_snapshot({
            "source": "MATE-API B10ReadClient",
            "observed_at": (now - timedelta(seconds=back)).isoformat(),
            "provider_received_at": now.isoformat(),
            "vehicle": {"battery_pct": 60.0, "range_km": 300.0, "odometer_km": 1000.0,
                        "locked": locked, "charging": charging, "climate_on": None},
            "field_sources": {"battery_pct": "soc_percent", "range_km": "expected_range_km",
                              "odometer_km": "odometer_km", "locked": None,
                              "charging": None, "climate_on": None},
        })


def test_private_read_only_pagination_persists_without_provider_reads(tmp_path):
    path = tmp_path / "pulse.sqlite3"
    seed(path)
    with TestClient(create_app(path, hosts=["testserver"])) as client:
        first = client.get("/api/protection/observations?limit=1&offset=0")
        assert first.status_code == 200 and first.headers["cache-control"] == "no-store"
        assert first.json()["total"] == 2 and len(first.json()["items"]) == 1
        assert client.get("/api/state").json()["protection"]["observed_count"] == 2
        second = client.get("/api/protection/observations?limit=1&offset=1").json()
        assert first.json()["items"][0]["id"] != second["items"][0]["id"]
        assert client.get("/api/protection/observations?offset=2").json()["items"] == []
        for query in ["limit=0", "limit=101", "offset=-1", "offset=1001"]:
            assert client.get("/api/protection/observations?" + query).status_code == 422
        assert client.app.state.collector is None
        csrf = client.get("/api/session").json()["csrf_token"]
        for method in ["POST", "PUT", "DELETE"]:
            assert client.request(method, "/api/protection/observations", json={},
                                  headers={"X-Pulse-CSRF": csrf}).status_code == 405
        assert client.post("/api/demo", json={"enabled": True}, headers={"X-Pulse-CSRF": csrf}).status_code == 200
        assert client.post("/api/protection/event", json={}, headers={"X-Pulse-CSRF": csrf}).status_code == 200
        assert client.get("/api/protection/observations").json()["total"] == 2
        assert client.get("/api/state").json()["protection"]["observed_count"] == 0
        exported = client.get("/api/export").json()
        assert len(exported["observed_events"]) == 2 and exported["protection"]["events"]


def test_oidc_requires_session_for_observed_history(tmp_path):
    config = Config(auth_mode="oidc", public_url="https://pulse.example.test",
                    oidc_issuer="https://issuer.example.test", oidc_client_id="pulse-web",
                    oidc_client_secret="synthetic-test-only", allowed_users=("qa",),
                    hosts=("testserver", "pulse.example.test"))
    with TestClient(create_app(tmp_path / "pulse.sqlite3", hosts=["testserver"], config=config)) as client:
        response = client.get("/api/protection/observations")
        assert response.status_code == 401 and response.headers["cache-control"] == "no-store"
        assert "items" not in response.json()
