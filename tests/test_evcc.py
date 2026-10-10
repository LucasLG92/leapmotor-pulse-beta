"""Original adapter checks against synthetic documented evcc responses; no wallbox."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone

import httpx2
import pytest

from pulse.evcc import (ENERGY_CONTRACT_REVISION, EvccClient, EvccConfig, EvccError,
                        parse_energy_history, parse_state, parse_sessions)


NOW = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
CONFIG = EvccConfig(enabled=True, origin="http://127.0.0.1:7070", loadpoint="Casa")


def state():
    return {"loadpoints": [{"title": "Casa", "name": "lp_1", "connected": True,
        "charging": True, "chargePower": 7200, "chargedEnergy": 3.5, "chargeDuration": 1200.5,
        "vehicleName": "PRIVATE_NOT_FOR_PULSE", "vehicleSoc": 20}]}


def sessions():
    return [{"id": 17, "created": "2026-10-08T09:00:00+02:00", "finished": "2026-10-08T11:00:00+02:00",
        "loadpoint": "Casa", "chargedEnergy": 10.5, "chargeDuration": 3600500000000,
        "vehicle": "PRIVATE_NOT_FOR_PULSE", "identifier": "PRIVATE_NOT_FOR_PULSE"}]


ENERGY_CONFIG = EvccConfig(enabled=True, origin="http://127.0.0.1:7070", loadpoint="Casa",
                           energy_history_enabled=True)
ENERGY_START = datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc)
ENERGY_END = datetime(2026, 10, 8, 11, 0, tzinfo=timezone.utc)


def energy_history():
    return [{"title": "Casa", "group": "loadpoint", "data": [
        {"start": "2026-10-08T12:00:00+02:00", "end": "2026-10-08T12:15:00+02:00",
         "energy": 1.25, "returnEnergy": 0.1, "socTemp": None,
         "private": "PRIVATE_NOT_FOR_PULSE"}]}]


@pytest.mark.parametrize("origin", ["http://evcc.local", "http://8.8.8.8", "http://169.254.169.254",
    "https://u:p@evcc.example.test", "https://evcc.example.test/api/state", "https://evcc.example.test?x=1",
    "https://evcc.example.test#x", "file:///tmp/evcc", "https://evcc.example.test:0", "https://evcc.example.test\n"])
def test_origin_must_be_fixed_and_http_only_literal_local(origin):
    with pytest.raises(ValueError):
        EvccConfig(enabled=True, origin=origin, loadpoint="Casa")


def test_normalized_state_preserves_units_nulls_and_fractional_seconds_excludes_identity():
    item = parse_state(state(), CONFIG, received_at=NOW)
    assert item["power_w"] == 7200 and item["charged_kwh"] == 3.5
    assert item["active_seconds"] == 1200.5
    assert item["received_at"] == "2026-10-08T12:00:00Z"
    assert "PRIVATE" not in str(item)
    payload = state()
    del payload["loadpoints"][0]["chargedEnergy"]
    assert parse_state(payload, CONFIG, received_at=NOW)["charged_kwh"] is None


def test_current_session_energy_preserves_kwh_and_supersedes_deprecated_alias():
    payload = state()
    payload["loadpoints"][0].update(sessionEnergy=4.25, chargedEnergy=3.5)
    assert parse_state(payload, CONFIG, received_at=NOW)["charged_kwh"] == 4.25
    del payload["loadpoints"][0]["chargedEnergy"]
    assert parse_state(payload, CONFIG, received_at=NOW)["charged_kwh"] == 4.25


def test_unavailable_current_session_energy_never_revives_legacy_energy():
    payload = state()
    payload["loadpoints"][0]["sessionEnergy"] = None
    assert parse_state(payload, CONFIG, received_at=NOW)["charged_kwh"] is None


def test_identity_observation_keeps_public_state_and_omits_invalid_entity():
    async def run():
        seen = []
        async def handler(request):
            seen.append(request)
            return httpx2.Response(200, json=state())
        client = EvccClient(CONFIG, transport=httpx2.MockTransport(handler))
        result = await client.state_with_identity(received_at=NOW)
        assert result == {"observation": parse_state(state(), CONFIG, received_at=NOW),
            "identity": {"entity": "lp_1", "loadpoint": "Casa",
                         "contract_revision": ENERGY_CONTRACT_REVISION}}
        payload = state(); payload["loadpoints"][0]["name"] = "bad/name"
        async def invalid_name(_request): return httpx2.Response(200, json=payload)
        result = await EvccClient(CONFIG, transport=httpx2.MockTransport(invalid_name)).state_with_identity(
            received_at=NOW)
        assert result["identity"] is None and result["observation"] == parse_state(payload, CONFIG, received_at=NOW)
        assert len(seen) == 1
    asyncio.run(run())


def test_energy_history_normalizes_original_candidates_and_coalesces_identical_duplicates():
    payload = energy_history()
    payload[0]["data"].append(deepcopy(payload[0]["data"][0]))
    result = parse_energy_history(payload, ENERGY_CONFIG, entity="lp_1", started_at=ENERGY_START,
                                  ended_at=ENERGY_END, received_at=NOW)
    assert result == [{"started_at": "2026-10-08T10:00:00Z", "ended_at": "2026-10-08T10:15:00Z",
        "kwh": 1.25, "return_kwh": 0.1, "received_at": "2026-10-08T12:00:00Z"}]
    assert "PRIVATE" not in str(result) and "epoch_id" not in result[0]
    assert parse_energy_history([], ENERGY_CONFIG, entity="lp_1", started_at=ENERGY_START,
                                ended_at=ENERGY_END, received_at=NOW) == []


@pytest.mark.parametrize("field,value", [
    ("start", "2026-10-08T12:01:00+02:00"),
    ("end", "2026-10-08T12:16:00+02:00"),
    ("energy", True), ("energy", None), ("energy", float("nan")), ("energy", 500.1),
    ("returnEnergy", True), ("returnEnergy", None), ("returnEnergy", float("inf")),
    ("returnEnergy", -0.1), ("socTemp", float("nan")), ("socTemp", True),
    ("socTemp", "bad")])
def test_energy_history_rejects_invalid_slots_and_numbers(field, value):
    payload = energy_history(); payload[0]["data"][0][field] = value
    with pytest.raises(EvccError):
        parse_energy_history(payload, ENERGY_CONFIG, entity="lp_1", started_at=ENERGY_START,
                             ended_at=ENERGY_END, received_at=NOW)


def test_energy_history_requires_single_matching_loadpoint_series_and_exact_slots():
    base = energy_history()
    variants = [base + [{"title": "other", "group": "loadpoint", "data": []}],
        [{**base[0], "group": "grid"}], [{key: value for key, value in base[0].items() if key != "title"}],
        [{**base[0], "title": "Other"}], [{**base[0], "isTemp": True}],
        [{**base[0], "data": base[0]["data"] + [{**base[0]["data"][0], "energy": 2}]}]]
    for payload in variants:
        with pytest.raises(EvccError):
            parse_energy_history(payload, ENERGY_CONFIG, entity="lp_1", started_at=ENERGY_START,
                                 ended_at=ENERGY_END, received_at=NOW)
    assert parse_energy_history([{**base[0], "data": []}], ENERGY_CONFIG, entity="lp_1",
                                started_at=ENERGY_START, ended_at=ENERGY_END, received_at=NOW) == []


def test_energy_history_accepts_calendar_boundary_and_normalizes_dst_offset():
    start = datetime(2014, 1, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2014, 1, 1, 0, 15, tzinfo=timezone.utc)
    payload = [{"title": "Casa", "group": "loadpoint", "data": [
        {"start": "2014-01-01T02:00:00+02:00", "end": "2014-01-01T02:15:00+02:00",
         "energy": 0, "returnEnergy": 0}]}]
    assert parse_energy_history(payload, ENERGY_CONFIG, entity="lp_1", started_at=start, ended_at=end,
        received_at=NOW)[0]["started_at"] == "2014-01-01T00:00:00Z"
    payload[0]["data"][0]["start"] = "2013-12-31T23:45:00Z"
    payload[0]["data"][0]["end"] = "2014-01-01T00:00:00Z"
    with pytest.raises(EvccError):
        parse_energy_history(payload, ENERGY_CONFIG, entity="lp_1", started_at=start, ended_at=end,
                             received_at=NOW)


def test_energy_history_rejects_more_than_one_hundred_slots():
    payload = energy_history()
    payload[0]["data"] *= 101
    with pytest.raises(EvccError) as caught:
        parse_energy_history(payload, ENERGY_CONFIG, entity="lp_1", started_at=ENERGY_START,
                             ended_at=ENERGY_END, received_at=NOW)
    assert caught.value.code == "large"


@pytest.mark.parametrize("entity", ["", "x" * 81, "énergie", "a/b", " lp_1"])
def test_energy_history_rejects_invalid_entity_names(entity):
    with pytest.raises(EvccError):
        parse_energy_history(energy_history(), ENERGY_CONFIG, entity=entity, started_at=ENERGY_START,
                             ended_at=ENERGY_END, received_at=NOW)


@pytest.mark.parametrize("start,end,received", [
    (datetime(2026, 10, 8, 10, 1, tzinfo=timezone.utc), ENERGY_END, NOW),
    (ENERGY_START, datetime(2026, 10, 8, 11, 1, tzinfo=timezone.utc), NOW),
    (ENERGY_END, ENERGY_START, NOW),
    (datetime(2026, 10, 7, 10, 0, tzinfo=timezone.utc), datetime(2026, 10, 8, 10, 15, tzinfo=timezone.utc), NOW),
    (ENERGY_START, ENERGY_END, datetime(2026, 10, 8, 10, 59, tzinfo=timezone.utc)),
    (datetime(2026, 10, 8, 10, 0), ENERGY_END, NOW)])
def test_energy_history_rejects_invalid_query_windows(start, end, received):
    with pytest.raises(EvccError):
        parse_energy_history(energy_history(), ENERGY_CONFIG, entity="lp_1", started_at=start,
                             ended_at=end, received_at=received)


def test_energy_config_is_strict_and_feature_disabled_by_default(monkeypatch):
    assert EvccConfig().energy_history_enabled is False
    monkeypatch.setenv("PULSE_EVCC_ENERGY_HISTORY_ENABLED", "yes")
    with pytest.raises(ValueError): EvccConfig.from_env()
    monkeypatch.setenv("PULSE_EVCC_ENERGY_HISTORY_ENABLED", "true")
    with pytest.raises(ValueError): EvccConfig.from_env()
    with pytest.raises(ValueError): EvccConfig(energy_history_enabled=1)
    with pytest.raises(ValueError): EvccConfig(energy_history_enabled=True)
    monkeypatch.setenv("PULSE_EVCC_ENABLED", "true")
    with pytest.raises(ValueError): EvccConfig.from_env()


def test_energy_history_uses_only_fixed_query_and_disabled_path_makes_no_extra_call():
    async def run():
        seen = []
        async def handler(request):
            seen.append(request)
            assert request.method == "GET" and request.url.host == "127.0.0.1"
            assert "authorization" not in request.headers and "cookie" not in request.headers
            if request.url.path == "/api/state": return httpx2.Response(200, json=state())
            assert request.url.path == "/api/history/energy"
            assert dict(request.url.params) == {"from": "2026-10-08T10:00:00Z", "to": "2026-10-08T11:00:00Z",
                "aggregate": "15m", "grouped": "false", "group": "loadpoint", "name": "lp_1", "format": "json"}
            return httpx2.Response(200, json=energy_history())
        client = EvccClient(ENERGY_CONFIG, transport=httpx2.MockTransport(handler))
        identity = await client.state_with_identity(received_at=NOW)
        assert identity["identity"]["entity"] == "lp_1"
        result = await client.energy_history(entity="lp_1", started_at=ENERGY_START, ended_at=ENERGY_END,
                                             received_at=NOW)
        assert result[0]["kwh"] == 1.25 and len(seen) == 2
        disabled = EvccClient(CONFIG, transport=httpx2.MockTransport(handler))
        with pytest.raises(EvccError):
            await disabled.energy_history(entity="lp_1", started_at=ENERGY_START, ended_at=ENERGY_END,
                                          received_at=NOW)
        with pytest.raises(EvccError): await disabled._read("/api/history/energy")
        assert len(seen) == 2
    asyncio.run(run())


def test_energy_transport_failure_stays_bounded_and_redacted():
    async def run():
        transport = httpx2.MockTransport(lambda _request: httpx2.Response(302, content=b"PRIVATE_BODY",
            headers={"Location": "https://secret.invalid/"}))
        with pytest.raises(EvccError) as caught:
            await EvccClient(ENERGY_CONFIG, transport=transport).energy_history(entity="lp_1",
                started_at=ENERGY_START, ended_at=ENERGY_END, received_at=NOW)
        assert caught.value.code == "network"
        assert "PRIVATE" not in str(caught.value) and "secret.invalid" not in str(caught.value)
    asyncio.run(run())


@pytest.mark.parametrize("value", [True, -0.1, 500.1, "4.25", float("nan"), float("inf")])
def test_invalid_current_session_energy_never_falls_back_to_valid_legacy_value(value):
    payload = state()
    payload["loadpoints"][0]["sessionEnergy"] = value
    with pytest.raises(EvccError):
        parse_state(payload, CONFIG, received_at=NOW)


@pytest.mark.parametrize("field,value", [("connected", "true"), ("charging", 1), ("chargePower", True),
    ("chargePower", -1), ("chargedEnergy", float("nan")), ("chargeDuration", float("inf")),
    ("chargedEnergy", 10**400), ("chargeDuration", -1)])
def test_invalid_selected_state_fails_closed(field, value):
    payload = state(); payload["loadpoints"][0][field] = value
    with pytest.raises(EvccError): parse_state(payload, CONFIG, received_at=NOW)


def test_duplicate_title_and_incoherent_charging_fail_closed():
    payload = state(); payload["loadpoints"].append(deepcopy(payload["loadpoints"][0]))
    with pytest.raises(EvccError): parse_state(payload, CONFIG, received_at=NOW)
    payload = state(); payload["loadpoints"][0]["connected"] = False
    with pytest.raises(EvccError): parse_state(payload, CONFIG, received_at=NOW)


def test_history_composite_id_not_isolated_upstream_id_and_no_identity():
    item = parse_sessions(sessions(), CONFIG, received_at=NOW)[0]
    assert item["started_at"] == "2026-10-08T07:00:00Z"
    assert item["active_seconds"] == 3600.5 and item["kwh"] == 10.5
    assert len(item["id"]) == 32 and "PRIVATE" not in str(item)
    other = sessions(); other[0]["created"] = "2026-10-08T08:00:00+02:00"
    assert parse_sessions(other, CONFIG, received_at=NOW)[0]["id"] != item["id"]
    assert len(parse_sessions(sessions() + sessions(), CONFIG, received_at=NOW)) == 1
    other = sessions(); other[0]["chargedEnergy"] = 11
    with pytest.raises(EvccError): parse_sessions(sessions() + other, CONFIG, received_at=NOW)


@pytest.mark.parametrize("field,value", [("id", True), ("id", 0), ("chargedEnergy", -1),
    ("created", "2026-10-08T07:00:00"), ("finished", "2026-10-09T09:00:00Z"),
    ("finished", "2026-10-08T07:00:00Z"), ("chargeDuration", 10000 * 1_000_000_000),
    ("chargeDuration", 3600.5), ("chargeDuration", True)])
def test_history_does_not_invent_open_future_or_inconsistent_sessions(field, value):
    payload = sessions(); payload[0][field] = value
    with pytest.raises(EvccError): parse_sessions(payload, CONFIG, received_at=NOW)


def test_transport_get_fixed_destination_bounded_and_no_redirects_or_secrets():
    async def run():
        seen = []
        async def handler(request):
            seen.append(request)
            assert request.method == "GET" and request.url.host == "127.0.0.1"
            assert "authorization" not in request.headers and "cookie" not in request.headers
            if request.url.path == "/api/state": return httpx2.Response(200, json=state())
            assert request.url.path == "/api/sessions"
            assert dict(request.url.params) == {"year": "2026", "month": "10"}
            return httpx2.Response(200, json=sessions())
        client = EvccClient(CONFIG, transport=httpx2.MockTransport(handler))
        await client.state(received_at=NOW)
        await client.sessions(year=2026, month=10, received_at=NOW)
        assert len(seen) == 2
        with pytest.raises(EvccError): await client._read("/api/db/backup")
        assert len(seen) == 2
        disabled = EvccClient(EvccConfig(), transport=httpx2.MockTransport(handler))
        with pytest.raises(EvccError): await disabled.state(received_at=NOW)
        assert len(seen) == 2
    asyncio.run(run())


@pytest.mark.parametrize("status,content,content_type,code", [
    (302, b"{}", "application/json", "network"), (401, b"{}", "application/json", "access"),
    (200, b'{"loadpoints":[],"loadpoints":[]}', "application/json", "contract"),
    (200, b'{"loadpoints":NaN}', "application/json", "contract"),
    (200, b"{}", "text/html", "contract"), (200, b"x" * (1024*1024+1), "application/json", "large")])
def test_transport_rejects_bad_payloads_without_leaking_body(status, content, content_type, code):
    async def run():
        transport = httpx2.MockTransport(lambda _request: httpx2.Response(status, content=content,
            headers={"Content-Type":content_type,"Location":"http://private.invalid/"}))
        with pytest.raises(EvccError) as caught:
            await EvccClient(CONFIG, transport=transport).state(received_at=NOW)
        assert caught.value.code == code
        assert "private.invalid" not in str(caught.value)
    asyncio.run(run())
