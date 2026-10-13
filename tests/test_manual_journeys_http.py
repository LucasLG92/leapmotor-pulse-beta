"""Authenticated HTTP contract for private, manually entered journeys."""

import pytest
from fastapi.testclient import TestClient

from pulse.app import create_app
from pulse.config import Config


@pytest.fixture
def clients(tmp_path):
    config = Config(
        auth_mode="oidc",
        public_url="https://pulse.example.org",
        oidc_issuer="https://id.example.org",
        oidc_client_id="pulse-test",
        oidc_client_secret="synthetic-fixture",
        allowed_users=("lucas",),
        hosts=("pulse.example.org",),
    )
    app = create_app(tmp_path / "pulse.sqlite3", config=config)
    with TestClient(app, base_url=config.public_url) as first:
        second = TestClient(app, base_url=config.public_url)
        for client, subject in ((first, "journey-owner-a"), (second, "journey-owner-b")):
            sid, record = app.state.sessions.create({"id": subject, "name": "Test"})
            client.cookies.set(config.cookie_name, sid, domain="pulse.example.org", path="/")
            client.headers["X-Pulse-CSRF"] = record.csrf
        yield first, second
        second.close()


def payload(**changes):
    return {
        "started_at": "2026-10-12T09:00:00+02:00",
        "ended_at": "2026-10-12T10:00:00+02:00",
        "from_label": "Casa",
        "to_label": "Trabajo",
        "distance_km": 30,
        "energy_kwh": 6,
        "stops": [],
    } | changes


def reserve(client):
    response = client.post("/api/journeys/requests", json={})
    assert response.status_code == 200, response.text
    return response.json()


def test_journey_endpoints_require_verified_session_and_csrf(tmp_path, clients):
    config = Config(
        auth_mode="oidc",
        public_url="https://pulse.example.org",
        oidc_issuer="https://id.example.org",
        oidc_client_id="pulse-test",
        oidc_client_secret="synthetic-fixture",
        allowed_users=("lucas",),
        hosts=("pulse.example.org",),
    )
    with TestClient(
        create_app(tmp_path / "anonymous.sqlite3", config=config),
        base_url=config.public_url,
    ) as client:
        assert client.get("/api/journeys").status_code == 401
        assert client.get("/api/journeys/report").status_code == 401
        assert client.post("/api/journeys/requests", json={}).status_code == 401
        assert client.post("/api/journeys", json={}).status_code == 401

    first, _ = clients
    request = reserve(first)
    body = {"request_id": request["id"], **payload()}
    assert first.post("/api/journeys", json=body, headers={"X-Pulse-CSRF": "invalid"}).status_code == 403
    assert first.get("/api/journeys").json() == {"items": [], "total": 0}


def test_journey_http_crud_is_owner_scoped_and_revision_safe(clients):
    first, second = clients
    request = reserve(first)
    response = first.post("/api/journeys", json={"request_id": request["id"], **payload()})
    assert response.status_code == 200, response.text
    journey = response.json()
    assert journey["source"] == "manual" and journey["coverage"] == "unknown"
    assert journey["started_at"] == "2026-10-12T07:00:00.000000Z"
    assert "owner_id" not in journey
    journey_path = f'/api/journeys/{journey["id"]}'

    assert first.get("/api/journeys?limit=1&offset=0").json() == {
        "items": [journey], "total": 1,
    }
    assert second.get("/api/journeys").json() == {"items": [], "total": 0}
    assert second.put(journey_path, json={"revision": 1, **payload()}).status_code == 404
    assert second.request("DELETE", journey_path, json={"revision": 1}).status_code == 404

    updated_response = first.put(
        journey_path, json={"revision": 1, **payload(to_label="Oficina", distance_km=31)}
    )
    assert updated_response.status_code == 200, updated_response.text
    updated = updated_response.json()
    assert updated["revision"] == 2 and updated["to_label"] == "Oficina"
    assert first.put(
        journey_path, json={"revision": 1, **payload(to_label="Oficina", distance_km=31)}
    ).json() == updated
    assert first.put(
        journey_path, json={"revision": 1, **payload(to_label="Stale")}
    ).status_code == 409
    assert first.request("DELETE", journey_path, json={"revision": 1}).status_code == 409
    assert first.request("DELETE", journey_path, json={"revision": 2}).status_code == 200
    assert first.get("/api/journeys").json() == {"items": [], "total": 0}
    assert first.post("/api/journeys", json={"request_id": request["id"], **payload()}).status_code == 409


def test_journey_http_payload_report_and_query_validation(clients):
    first, _ = clients
    assert first.post("/api/journeys/requests", json={"journey_id": "foreign"}).status_code == 422
    request = reserve(first)
    invalid_payloads = [
        {"request_id": request["id"], **payload(), "owner_id": "attacker"},
        {"request_id": request["id"], **payload(distance_km=True)},
        {"request_id": request["id"], **payload(stops=[{"started_at": "2026-10-12T09:20:00Z"}])},
    ]
    for body in invalid_payloads:
        assert first.post("/api/journeys", json=body).status_code == 422
    created = first.post("/api/journeys", json={"request_id": request["id"], **payload()})
    assert created.status_code == 200, created.text

    assert first.get("/api/journeys?limit=0").status_code == 422
    assert first.get("/api/journeys?offset=-1").status_code == 422
    assert first.get("/api/journeys/report?days=31").status_code == 422
    report = first.get("/api/journeys/report?days=30")
    assert report.status_code == 200, report.text
    assert report.json()["source"] == "manual"
    assert report.json()["coverage"] == "unknown"
    assert report.json()["period"]["timezone"] == "UTC"
    assert report.json()["months"]
