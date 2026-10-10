"""Private ledger HTTP contract; synthetic data and transports only."""

from fastapi.testclient import TestClient
import httpx2

from pulse.app import create_app
from pulse.config import Config
from pulse.evcc import EvccConfig
from test_charger_pricing_history import quote_for, scope, session


def test_history_reads_preserved_calculations_without_upstream_requests(tmp_path):
    calls = []

    def transport(request):
        calls.append(request.url.path)
        if request.url.path == "/api/state":
            return httpx2.Response(200, json={"loadpoints": []})
        return httpx2.Response(200, json=[])

    selected = Config(hosts=("testserver",), evcc=EvccConfig(
        True, "http://127.0.0.1:7070", "Casa", history_months=1
    ))
    app = create_app(tmp_path / "pulse.sqlite3", config=selected,
                     evcc_transport=httpx2.MockTransport(transport))
    with TestClient(app) as client:
        store = app.state.coordinator.store
        record = session(source_scope=selected.evcc.scope)
        store.ingest_charger(selected.evcc.scope, sessions=[record])
        store.update_settings({"pricing_method": "pvpc"})
        store.save_charger_pricing(selected.evcc.scope, record, quote_for(record))
        corrected = {**record, "kwh": 11, "received_at": "2026-10-08T14:05:00Z"}
        store.ingest_charger(selected.evcc.scope, sessions=[corrected])
        store.save_charger_pricing(selected.evcc.scope, corrected, quote_for(corrected))
        route = f'/api/charger/pricing-history?session_id={record["id"]}'
        before = list(calls)
        page = client.get(route + "&limit=2")
        assert page.status_code == 200
        assert page.headers["cache-control"] == "no-store"
        body = page.json()
        assert body["total"] == 3
        assert [item["kind"] for item in body["items"]] == ["priced", "invalidated"]
        assert body["items"][0]["session"]["kwh"] == 11
        assert body["items"][1]["session"]["kwh"] == 10
        assert body["items"][1]["corrected_session"]["kwh"] == 11
        assert client.get(route + "&limit=2&offset=2").json()["items"][0]["kind"] == "priced"
        assert calls == before
        assert selected.evcc.scope not in page.text
        assert "upstream_id" not in page.text
        assert "127.0.0.1:7070" not in page.text

        other = session(2, source_scope=scope(2))
        store.ingest_charger(scope(2), sessions=[other])
        assert client.get(f'/api/charger/pricing-history?session_id={other["id"]}').status_code == 404
        assert client.get('/api/charger/pricing-history?session_id=' + 'f' * 32).status_code == 404
        for suffix in ("&limit=0", "&limit=21", "&offset=-1", "&offset=2049"):
            assert client.get(route + suffix).status_code == 422
        assert client.get('/api/charger/pricing-history?session_id=invalid').status_code == 422
        assert client.get('/api/charger/pricing-history').status_code == 422
        assert client.post(route).status_code in (403, 405)


def test_history_requires_authenticated_oidc_session_and_remains_uncached(tmp_path):
    def unexpected(_request):
        raise AssertionError("Disabled integration requested a network call")

    selected = Config(auth_mode="oidc", public_url="https://pulse.example.org",
                      oidc_issuer="https://id.example.org", oidc_client_id="pulse-web",
                      oidc_client_secret="synthetic-web-secret", allowed_users=("fixture",),
                      hosts=("pulse.example.org",))
    app = create_app(tmp_path / "pulse.sqlite3", config=selected,
                     evcc_transport=httpx2.MockTransport(unexpected))
    route = '/api/charger/pricing-history?session_id=' + 'a' * 32
    with TestClient(app, base_url=selected.public_url) as client:
        refused = client.get(route)
        assert refused.status_code == 401
        assert refused.headers["cache-control"] == "no-store"
        sid, _ = app.state.sessions.create({"id": "fixture", "name": "Fixture"})
        client.cookies.set(selected.cookie_name, sid, domain="pulse.example.org", path="/")
        page = client.get(route)
        assert page.status_code == 200
        assert page.json()["total"] == 0
        assert page.headers["cache-control"] == "no-store"
        assert app.state.charger.task is None
