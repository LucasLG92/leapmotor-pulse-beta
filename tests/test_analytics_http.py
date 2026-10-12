"""El análisis describe los registros y sus límites, sin convertir demo en lectura."""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from pulse.app import create_app


def mutate(client, path, data):
    csrf = client.get("/api/session").json()["csrf_token"]
    response = client.post(path, json=data, headers={"X-Pulse-CSRF": csrf})
    assert response.status_code == 200, response.text
    return response.json()


def test_analysis_empty_is_unknown_and_personal_api_is_not_cached(tmp_path):
    with TestClient(create_app(tmp_path / "pulse.sqlite3", hosts=["testserver"])) as client:
        response = client.get("/api/analytics/energy?days=30")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        data = response.json()
        assert data["manual"]["sessions"] == 0
        assert data["manual"]["effective_tariff_eur_kwh"] is None
        assert data["estimates"]["consumption_kwh_100km"] is None
        assert data["observations"]["coverage_percent"] is None
        assert data["observations"]["odometer_delta_km"] is None
        assert data["observations"]["validation"] == "not_qualified"
        assert data["bucket_timezone"] == "UTC"
        for days in ("0", "366", "true", "not-a-number"):
            assert client.get(f"/api/analytics/energy?days={days}").status_code == 422
        assert client.get("/api/analytics/energy", headers={"Remote-User": "lucas"}).status_code == 403


def test_analysis_includes_all_manual_records_and_keeps_demo_separate(tmp_path):
    app = create_app(tmp_path / "pulse.sqlite3", hosts=["testserver"])
    with TestClient(app) as client:
        app.state.store._clock = lambda: datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
        mutate(client, "/api/charges", {"started_at": "2026-10-01T10:00:00Z", "kwh": 10})
        csrf = client.get("/api/session").json()["csrf_token"]
        assert client.put("/api/settings", json={"tariff_eur_kwh": .3},
                          headers={"X-Pulse-CSRF": csrf}).status_code == 200
        mutate(client, "/api/charges", {"started_at": "2026-10-02T10:00:00Z", "kwh": 30})
        mutate(client, "/api/charges", {"started_at": "2026-12-01T10:00:00Z", "kwh": 100})
        data = client.get("/api/analytics/energy?days=30").json()
        assert data["manual"]["sessions"] == 2
        assert data["manual"]["kwh"] == 40
        assert data["manual"]["cost_eur"] == 10.5
        assert abs(data["manual"]["effective_tariff_eur_kwh"] - .2625) < 1e-9
        mutate(client, "/api/demo", {"enabled": True})
        with_demo = client.get("/api/analytics/energy?days=30").json()
        assert with_demo["manual"] == data["manual"]
        assert with_demo["demo"]["sessions"] > 0
        assert with_demo["observations"]["sample_count"] == 0
        mutate(client, "/api/demo", {"enabled": False})
        without = client.get("/api/analytics/energy?days=30").json()
        assert without["manual"] == data["manual"]
        assert without["demo"]["sessions"] == 0
