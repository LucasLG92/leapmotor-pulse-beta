"""Store-level tests for PVPC charge snapshots and the local day cache.

Every price series here is synthetic and used only to test normalization and
accounting behavior; these tests make no claim about a real REE response.
"""

from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
import sqlite3
from zoneinfo import ZoneInfo

import pytest

from pulse import pvpc
from pulse.domain import DomainError, Store


UTC = timezone.utc
MADRID = ZoneInfo("Europe/Madrid")


def synthetic_day(day: date, price: float = -0.125, *, fetched_at: datetime | None = None):
    start = datetime.combine(day, time.min, tzinfo=MADRID).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=MADRID).astimezone(UTC)
    intervals = []
    cursor = start
    while cursor < end:
        next_cursor = cursor + timedelta(hours=1)
        intervals.append({
            "started_at": cursor.isoformat().replace("+00:00", "Z"),
            "ended_at": next_cursor.isoformat().replace("+00:00", "Z"),
            "price_eur_kwh": price,
        })
        cursor = next_cursor
    return pvpc.validate_day_snapshot({
        "date": day.isoformat(),
        "region": "peninsula",
        "timezone": "Europe/Madrid",
        "source": "REE REData PVPC 1001",
        "fetched_at": (fetched_at or datetime(2026, 10, 8, tzinfo=UTC))
        .astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "intervals": intervals,
    })


def quote_for(*, kwh: float = 2.0, price: float = -0.125, start="2026-01-15T08:30:00Z",
              end="2026-01-15T09:30:00Z"):
    return pvpc.quote_charge(
        start,
        end,
        kwh,
        [synthetic_day(date(2026, 1, 15), price)],
        calculated_at=datetime(2026, 10, 8, 10, tzinfo=UTC),
    )


def add_pvpc_charge(store: Store, quote=None):
    quote = quote or quote_for()
    return store.add_charge(
        {
            "started_at": "2026-01-15T08:30:00Z",
            "ended_at": "2026-01-15T09:30:00Z",
            "kwh": 2.0,
            "pricing_method": "pvpc",
        },
        pricing_snapshot=quote,
    )


def test_negative_pvpc_quote_is_saved_as_a_verifiable_snapshot(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3")
    charge = add_pvpc_charge(store)

    assert charge["pricing_method"] == "pvpc"
    assert charge["cost_eur"] == -0.25
    assert charge["tariff_eur_kwh"] == pytest.approx(-0.125)
    assert charge["pricing"]["method"] == "pvpc"
    assert charge["pricing"]["accuracy"] == "estimated_uniform"
    assert len(charge["pricing"]["intervals"]) == 2
    assert store.get_charge(charge["id"]) == charge


def test_pvpc_requires_server_quote_and_never_reprices_from_global_settings(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3")
    with pytest.raises(DomainError, match="snapshot"):
        store.add_charge({
            "started_at": "2026-01-15T08:30:00Z",
            "ended_at": "2026-01-15T09:30:00Z",
            "kwh": 2,
            "pricing_method": "pvpc",
        })
    with pytest.raises(DomainError):
        store.add_charge({
            "started_at": "2026-01-15T08:30:00Z",
            "ended_at": "2026-01-15T09:30:00Z",
            "kwh": 2,
            "pricing_method": "pvpc",
            "pricing": {"cost_eur": -0.25},
        }, pricing_snapshot=quote_for())

    charge = add_pvpc_charge(store)
    store.update_settings({"pricing_method": "manual", "tariff_eur_kwh": 0.8})
    assert store.get_charge(charge["id"]) == charge


def test_charge_creation_uses_configured_method_when_method_is_omitted(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3")
    store.update_settings({"pricing_method": "pvpc"})
    with pytest.raises(DomainError, match="ended_at"):
        store.add_charge({"started_at": "2026-01-15T08:30:00Z", "kwh": 2})
    quote = quote_for()
    charge = store.add_charge(
        {
            "started_at": "2026-01-15T08:30:00Z",
            "ended_at": "2026-01-15T09:30:00Z",
            "kwh": 2,
        },
        pricing_snapshot=quote,
    )
    assert charge["pricing_method"] == "pvpc"


def test_manual_end_validation_and_pvpc_to_manual_conversion_are_explicit(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3")
    with pytest.raises(DomainError, match="posterior"):
        store.add_charge({
            "started_at": "2026-01-15T08:30:00Z",
            "ended_at": "2026-01-15T08:30:00Z",
            "kwh": 2,
        })
    with pytest.raises(DomainError, match="72 horas"):
        store.add_charge({
            "started_at": "2026-01-15T08:30:00Z",
            "ended_at": "2026-01-18T09:00:00Z",
            "kwh": 2,
        })

    charge = add_pvpc_charge(store)
    with pytest.raises(DomainError, match="tariff_eur_kwh"):
        store.update_charge(charge["id"], {"pricing_method": "manual"})
    converted = store.update_charge(
        charge["id"], {"pricing_method": "manual", "tariff_eur_kwh": 0.2}
    )
    assert converted["pricing_method"] == "manual"
    assert converted["pricing"] is None
    assert converted["cost_eur"] == 0.4


def test_pvpc_edits_need_a_fresh_full_quote_and_reject_tampering(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3")
    charge = add_pvpc_charge(store)
    with pytest.raises(DomainError, match="snapshot"):
        store.update_charge(charge["id"], {"kwh": 3})

    changed_quote = quote_for(kwh=3.0)
    changed = store.update_charge(
        charge["id"], {"kwh": 3.0}, pricing_snapshot=changed_quote
    )
    assert changed["kwh"] == 3.0
    assert changed["cost_eur"] == -0.38

    bad = deepcopy(changed_quote)
    bad["pricing"]["intervals"][0]["cost_eur"] += 0.1
    with pytest.raises(DomainError):
        store.update_charge(charge["id"], {"kwh": 4.0}, pricing_snapshot=bad)
    assert store.get_charge(charge["id"]) == changed


def test_v4_export_import_preserves_pricing_and_rejects_bad_snapshot_atomically(tmp_path):
    source = Store(tmp_path / "source.sqlite3")
    source.update_settings({"pricing_method": "pvpc", "tariff_eur_kwh": 0.42})
    charge = add_pvpc_charge(source)
    document = source.export_data()
    assert document["schema_version"] == 17
    assert document["settings"]["pricing_method"] == "pvpc"
    assert next(item for item in document["charges"] if item["id"] == charge["id"])["pricing"] == charge["pricing"]

    destination = Store(tmp_path / "destination.sqlite3")
    original = destination.add_charge({"started_at": "2026-01-01T00:00:00Z", "kwh": 1})
    before = destination.export_data()
    invalid = deepcopy(document)
    item = next(item for item in invalid["charges"] if item["id"] == charge["id"])
    item["pricing"]["intervals"][0]["price_eur_kwh"] = 0.5
    with pytest.raises(DomainError):
        destination.validate_import(invalid)
    with pytest.raises(DomainError):
        destination.import_data(invalid)
    assert destination.get_charge(original["id"])["cost_eur"] == original["cost_eur"]
    after = destination.export_data()
    before.pop("exported_at")
    after.pop("exported_at")
    assert after == before

    assert destination.import_data(document) == {"ok": True, "mode": "unconfigured"}
    assert destination.get_charge(charge["id"]) == charge
    assert destination.get_settings()["pricing_method"] == "pvpc"


def test_pvpc_cache_validates_local_days_and_retains_400_ingestions(tmp_path):
    store = Store(tmp_path / "pulse.sqlite3")
    day = date(2025, 3, 30)
    with pytest.raises(DomainError):
        store.read_pvpc_day("2025-02-30")
    assert store.read_pvpc_day(day.isoformat()) is None

    base_time = datetime(2026, 10, 8, tzinfo=UTC)
    latest = None
    for index in range(401):
        latest = store.cache_pvpc_day(
            synthetic_day(day, 0.125, fetched_at=base_time + timedelta(seconds=index))
        )
    cached = store.read_pvpc_day(day.isoformat())
    assert cached == latest
    assert len(cached["intervals"]) == 23
    with sqlite3.connect(tmp_path / "pulse.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM pvpc_day_cache").fetchone()[0] == 400

    broken = deepcopy(latest)
    broken["intervals"].pop()
    with pytest.raises(DomainError):
        store.cache_pvpc_day(broken)
    assert store.read_pvpc_day(day.isoformat()) == cached
    assert "pvpc_day_cache" not in store.export_data()
