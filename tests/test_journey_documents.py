"""CSV and browser-print report exports for the manual journey diary."""

from copy import deepcopy
import csv
from contextlib import contextmanager
import io
import sqlite3
from datetime import datetime, timezone

import pytest

from pulse.domain import DomainError, Store
from pulse.journey_documents import journey_csv, journey_print_html


NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)


def document_with(*, notes="Nota", label="Casa", tags=None, distance=0, energy=None):
    if tags is None:
        tags = ["diario"]
    return {
        "report": {
            "source": "manual",
            "coverage": "unknown",
            "period": {
                "started_at": "2026-09-13T12:00:00.000000Z",
                "ended_at": "2026-10-13T12:00:00.000000Z",
                "days": 30,
                "timezone": "UTC",
            },
            "totals": {
                "count": 1,
                "distance_km": distance or 0,
                "energy_kwh": 0,
                "known_distance_count": 1,
                "known_energy_count": 0 if energy is None else 1,
                "paired_count": 0,
                "paired_distance_km": 0,
                "paired_energy_kwh": 0,
                "consumption_kwh_100km": None,
            },
            "months": [{
                "month_key": "2026-10",
                "count": 1,
                "distance_km": distance or 0,
                "energy_kwh": 0 if energy is None else energy,
                "known_distance_count": 1,
                "known_energy_count": 0 if energy is None else 1,
                "paired_count": 0,
                "paired_distance_km": 0,
                "paired_energy_kwh": 0,
                "consumption_kwh_100km": None,
            }],
        },
        "journeys": [{
            "id": "a" * 32,
            "revision": 1,
            "source": "manual",
            "coverage": "unknown",
            "started_at": "2026-10-12T09:00:00.000000Z",
            "ended_at": "2026-10-12T10:00:00.000000Z",
            "from_label": label,
            "to_label": "Trabajo",
            "distance_km": distance,
            "energy_kwh": energy,
            "stops": [{
                "started_at": "2026-10-12T09:15:00.000000Z",
                "ended_at": "2026-10-12T09:20:00.000000Z",
            }],
            "duration_min": 60,
            "paused_min": 0,
            "active_min": 60,
            "consumption_kwh_100km": None,
            "notes": notes,
            "tags": tags,
            "created_at": "2026-10-12T10:00:00.000000Z",
            "updated_at": "2026-10-12T10:00:00.000000Z",
        }],
    }


def test_csv_is_utf8_bom_preserves_zero_and_null_and_neutralizes_formulas():
    document = document_with(
        notes=" \t=HYPERLINK(\"https://example.invalid\")",
        label="-payload",
        tags=["+cmd", "@mention"],
    )
    csv_bytes = journey_csv(document)
    assert csv_bytes.startswith(b"\xef\xbb\xbf")
    rows = list(csv.DictReader(io.StringIO(csv_bytes.decode("utf-8-sig"))))
    journey = next(row for row in rows if row["record_type"] == "journey")
    assert journey["journey_distance_km"] == "0"
    assert journey["journey_energy_kwh"] == ""
    assert "2026-10-12T09:15:00.000000Z" in journey["stops"]
    assert journey["notes"].startswith("' \t=")
    assert journey["from_label"] == "'-payload"
    assert journey["tags"].startswith("'+cmd")


def test_print_html_escapes_untrusted_text_and_explains_coverage_and_pdf_path():
    document = document_with(
        notes='<script>alert("x")</script>',
        label='<img src=x onerror="alert(1)">',
        tags=['"><script>bad()</script>'],
    )
    rendered = journey_print_html(document)
    assert "<script>" not in rendered
    assert "<img src=x" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in rendered
    assert '<link rel="stylesheet" href="/journey-report.css">' in rendered
    assert "<style" not in rendered and "<script" not in rendered
    assert "Procedencia: trayectos guardados en el diario manual de Pulse." in rendered
    assert "Cobertura registrada: desconocida." in rendered
    assert "no completa viajes ausentes" in rendered
    assert "Guardar como PDF" in rendered
    assert "2026-09-13T12:00:00.000000Z" in rendered
    assert "Fecha de corte del informe: 2026-10-13T12:00:00.000000Z." in rendered
    assert "Intervalos de parada (UTC)" in rendered
    assert "2026-10-12T09:15:00.000000Z" in rendered
    assert "UTC" in rendered


def test_csv_and_html_reject_documents_over_row_or_byte_budget():
    sample = document_with(notes="x" * 2_000)
    too_many_rows = deepcopy(sample)
    too_many_rows["journeys"] = [{}] * 10_001
    for export in (journey_csv, journey_print_html):
        with pytest.raises(DomainError) as rejected_rows:
            export(too_many_rows)
        assert rejected_rows.value.status == 413

    oversized = deepcopy(sample)
    oversized["journeys"] = [deepcopy(sample["journeys"][0]) for _ in range(3_000)]
    for export in (journey_csv, journey_print_html):
        with pytest.raises(DomainError) as rejected_bytes:
            export(oversized)
        assert rejected_bytes.value.status == 413


def test_document_data_is_owner_scoped_and_rejects_more_than_10000_rows(
    tmp_path, monkeypatch
):
    path = tmp_path / "pulse.sqlite3"
    store = Store(path, clock=lambda: NOW)
    other_owner = store.reserve_manual_journey_creation("owner-b")
    store.add_manual_journey("owner-b", other_owner["id"], {
        "started_at": "2026-10-12T09:00:00Z",
        "ended_at": "2026-10-12T10:00:00Z",
        "from_label": "Privado",
        "to_label": "Privado",
        "distance_km": 1,
        "energy_kwh": 1,
        "stops": [],
    })
    request = store.reserve_manual_journey_creation("owner-a")
    journey = store.add_manual_journey("owner-a", request["id"], {
        "started_at": "2026-10-12T09:00:00Z",
        "ended_at": "2026-10-12T10:00:00Z",
        "from_label": "Casa",
        "to_label": "Trabajo",
        "distance_km": 0,
        "energy_kwh": None,
        "stops": [],
        "notes": "Solo owner-a",
        "tags": ["privado"],
    })

    statements = []
    read_transaction = store._read_transaction

    @contextmanager
    def traced_read_transaction():
        with read_transaction() as connection:
            connection.set_trace_callback(statements.append)
            yield connection

    monkeypatch.setattr(store, "_read_transaction", traced_read_transaction)
    data = store.journey_document_data("owner-a", days=30)
    journey_reads = [
        statement for statement in statements
        if "FROM MANUAL_JOURNEYS" in statement.upper()
    ]
    assert len(journey_reads) == 1
    assert "LIMIT 10001" in journey_reads[0].upper()
    monkeypatch.setattr(store, "_read_transaction", read_transaction)
    assert data["report"] == store.report_manual_journeys("owner-a", days=30)
    assert data["journeys"] == [journey]
    assert "owner-b" not in repr(data)

    with sqlite3.connect(path) as connection:
        connection.executemany(
            """INSERT INTO manual_journeys
               (id, owner_id, revision, started_at, ended_at, from_label, to_label,
                distance_km, energy_kwh, stops_json, notes, tags_json, created_at, updated_at)
               VALUES (?, 'owner-a', 1, '2026-10-12T09:00:00.000000Z',
                '2026-10-12T10:00:00.000000Z', NULL, NULL, NULL, NULL, '[]', NULL, '[]',
                '2026-10-12T10:00:00.000000Z', '2026-10-12T10:00:00.000000Z')""",
            [(f"{index:032x}",) for index in range(10_001)],
        )
    with pytest.raises(DomainError) as too_many:
        store.journey_document_data("owner-a", days=30)
    assert too_many.value.status == 413
