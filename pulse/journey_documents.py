"""Bounded, private exports for the manual journey report."""

from __future__ import annotations

import csv
import html
from collections.abc import Mapping
from typing import Any

from pulse.manual_journeys import (
    MAX_MANUAL_JOURNEY_DOCUMENT_BYTES,
    MAX_MANUAL_JOURNEY_DOCUMENT_ROWS,
    _domain_error,
)


_CSV_COLUMNS = (
    "record_type", "source", "coverage", "period_started_at", "period_ended_at",
    "period_days", "period_timezone", "month_key", "journey_id", "revision",
    "count", "distance_km", "energy_kwh", "known_distance_count",
    "known_energy_count", "paired_count", "paired_distance_km", "paired_energy_kwh",
    "consumption_kwh_100km", "started_at", "ended_at", "from_label", "to_label",
    "journey_distance_km", "journey_energy_kwh", "duration_min", "paused_min",
    "active_min", "stops", "journey_consumption_kwh_100km", "notes", "tags",
    "created_at", "updated_at",
)


def _document_parts(document: Any) -> tuple[Mapping[str, Any], list[Mapping[str, Any]]]:
    if not isinstance(document, Mapping):
        raise ValueError("El documento del informe no es válido.")
    report = document.get("report")
    journeys = document.get("journeys")
    if (
        not isinstance(report, Mapping)
        or not isinstance(report.get("period"), Mapping)
        or not isinstance(report.get("totals"), Mapping)
        or not isinstance(report.get("months"), list)
        or not isinstance(journeys, list)
    ):
        raise ValueError("El documento del informe no es válido.")
    if len(journeys) > MAX_MANUAL_JOURNEY_DOCUMENT_ROWS:
        _domain_error("El informe supera 10.000 trayectos.", 413)
    if any(not isinstance(row, Mapping) for row in report["months"] + journeys):
        raise ValueError("El documento contiene una fila no válida.")
    return report, journeys


def _value(row: Mapping[str, Any], key: str) -> Any:
    value = row.get(key)
    if value is None:
        return ""
    if key == "tags" and isinstance(value, list):
        return "; ".join(str(tag) for tag in value)
    if key == "stops" and isinstance(value, list):
        return "; ".join(
            f"{stop.get('started_at', '')} – {stop.get('ended_at', '')}"
            for stop in value if isinstance(stop, Mapping)
        )
    return value


def _safe_csv_cell(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


class _LimitedTextBuffer:
    def __init__(self, initial_bytes: int = 0) -> None:
        self.parts: list[str] = []
        self.byte_count = initial_bytes

    def write(self, value: str) -> int:
        encoded_size = len(value.encode("utf-8"))
        if self.byte_count + encoded_size > MAX_MANUAL_JOURNEY_DOCUMENT_BYTES:
            _domain_error("El documento supera el límite de 4 MiB.", 413)
        self.parts.append(value)
        self.byte_count += encoded_size
        return len(value)

    def getvalue(self) -> str:
        return "".join(self.parts)


def journey_csv(document: Any) -> bytes:
    """Render an Excel-friendly UTF-8 CSV report, capped at 4 MiB."""
    report, journeys = _document_parts(document)
    period = report["period"]
    buffer = _LimitedTextBuffer(initial_bytes=3)  # UTF-8 BOM.
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(_CSV_COLUMNS)

    summary_row = {
        **report["totals"],
        "record_type": "summary",
        "source": report.get("source"),
        "coverage": report.get("coverage"),
        "period_started_at": period.get("started_at"),
        "period_ended_at": period.get("ended_at"),
        "period_days": period.get("days"),
        "period_timezone": period.get("timezone"),
    }
    writer.writerow(_safe_csv_cell(_value(summary_row, key)) for key in _CSV_COLUMNS)

    for month in report["months"]:
        month_row = {
            **month,
            "record_type": "month",
            "source": report.get("source"),
            "coverage": report.get("coverage"),
            "period_started_at": period.get("started_at"),
            "period_ended_at": period.get("ended_at"),
            "period_days": period.get("days"),
            "period_timezone": period.get("timezone"),
        }
        writer.writerow(_safe_csv_cell(_value(month_row, key)) for key in _CSV_COLUMNS)

    for journey in journeys:
        journey_row = {
            **journey,
            "journey_id": journey.get("id"),
            "journey_distance_km": journey.get("distance_km"),
            "journey_energy_kwh": journey.get("energy_kwh"),
            "journey_consumption_kwh_100km": journey.get("consumption_kwh_100km"),
            "record_type": "journey",
            "period_started_at": period.get("started_at"),
            "period_ended_at": period.get("ended_at"),
            "period_days": period.get("days"),
            "period_timezone": period.get("timezone"),
        }
        writer.writerow(_safe_csv_cell(_value(journey_row, key)) for key in _CSV_COLUMNS)

    return b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8")


def _escaped(value: Any) -> str:
    if value is None:
        return "—"
    return html.escape(str(value), quote=True)


def _coverage_text(value: Any) -> Any:
    return "desconocida" if value == "unknown" else value


def _write_cells(buffer: _LimitedTextBuffer, values: list[Any], *, heading: bool = False) -> None:
    tag = "th" if heading else "td"
    scope = ' scope="col"' if heading else ""
    buffer.write("<tr>")
    for value in values:
        buffer.write(f"<{tag}{scope}>{_escaped(value)}</{tag}>")
    buffer.write("</tr>")


def journey_print_html(document: Any) -> str:
    """Build a CSP-safe print view that browsers can save as PDF."""
    report, journeys = _document_parts(document)
    period = report["period"]
    totals = report["totals"]
    buffer = _LimitedTextBuffer()

    def write(value: str) -> None:
        buffer.write(value)

    write(
        '<!doctype html><html lang="es"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>Informe de trayectos</title>'
        '<link rel="stylesheet" href="/journey-report.css">'
        '</head><body><main><header><h1>Informe de trayectos</h1>'
        '<p>Procedencia: trayectos guardados en el diario manual de Pulse.</p>'
        f'<p>Cobertura registrada: {_escaped(_coverage_text(report.get("coverage")))}.</p>'
        f'<p>Periodo: {_escaped(period.get("started_at"))} – '
        f'{_escaped(period.get("ended_at"))} ({_escaped(period.get("days"))} días, '
        f'{_escaped(period.get("timezone"))}).</p>'
        f'<p>Fecha de corte del informe: {_escaped(period.get("ended_at"))}.</p>'
        '<p>El informe refleja los trayectos manuales almacenados en ese intervalo; '
        'no completa viajes ausentes.</p>'
        '<p>Para guardar un PDF, usa Imprimir y elige Guardar como PDF en el navegador.</p>'
        '</header><section aria-labelledby="summary-title"><h2 id="summary-title">Resumen</h2>'
        '<table><caption>Totales del periodo</caption><thead>'
    )
    _write_cells(buffer, ["Métrica", "Valor"], heading=True)
    write("</thead><tbody>")
    summary_labels = (
        ("count", "Trayectos"),
        ("distance_km", "Distancia registrada (km)"),
        ("energy_kwh", "Energía registrada (kWh)"),
        ("known_distance_count", "Trayectos con distancia"),
        ("known_energy_count", "Trayectos con energía"),
        ("paired_count", "Trayectos con distancia y energía"),
        ("paired_distance_km", "Distancia de trayectos combinables (km)"),
        ("paired_energy_kwh", "Energía de trayectos combinables (kWh)"),
        ("consumption_kwh_100km", "Consumo combinado (kWh/100 km)"),
    )
    for field, label in summary_labels:
        _write_cells(buffer, [label, totals.get(field)])
    write("</tbody></table></section>")

    write('<section aria-labelledby="months-title"><h2 id="months-title">Por mes</h2>')
    if report["months"]:
        write('<table><caption>Resumen mensual</caption><thead>')
        _write_cells(
            buffer,
            ["Mes", "Trayectos", "Distancia (km)", "Energía (kWh)", "Consumo (kWh/100 km)"],
            heading=True,
        )
        write("</thead><tbody>")
        for month in report["months"]:
            _write_cells(buffer, [
                month.get("month_key"), month.get("count"), month.get("distance_km"),
                month.get("energy_kwh"), month.get("consumption_kwh_100km"),
            ])
        write("</tbody></table>")
    else:
        write("<p>No hay meses con datos en el intervalo.</p>")
    write('</section><section aria-labelledby="journeys-title"><h2 id="journeys-title">Trayectos</h2>')
    if journeys:
        for index, journey in enumerate(journeys, 1):
            origin = journey.get("from_label") or "Origen sin indicar"
            destination = journey.get("to_label") or "Destino sin indicar"
            write(f'<article><h3>{index}. {_escaped(origin)} → {_escaped(destination)}</h3>')
            write('<dl class="journey-details">')
            for label, field in (
                ("Inicio (UTC)", "started_at"), ("Fin (UTC)", "ended_at"),
                ("Distancia (km)", "distance_km"), ("Energía (kWh)", "energy_kwh"),
                ("Duración (min)", "duration_min"), ("Paradas (min)", "paused_min"),
                ("Tiempo activo (min)", "active_min"),
                ("Consumo (kWh/100 km)", "consumption_kwh_100km"),
            ):
                write(f'<div><dt>{label}</dt><dd>{_escaped(journey.get(field))}</dd></div>')
            write('</dl>')
            stops = _value(journey, "stops") or "Sin paradas declaradas"
            tags = _value(journey, "tags") or "Sin etiquetas"
            write(f'<p><strong>Intervalos de parada (UTC):</strong> {_escaped(stops)}</p>')
            write(f'<p><strong>Etiquetas:</strong> {_escaped(tags)}</p>')
            write(f'<p><strong>Notas:</strong> {_escaped(journey.get("notes"))}</p></article>')
    else:
        write("<p>No hay trayectos registrados en este periodo.</p>")
    write(
        '</section><footer><p>Procedencia: diario manual de Pulse. '
        f'Cobertura: {_escaped(_coverage_text(report.get("coverage")))}. '
        'Los valores ausentes se muestran como “—”; los totales no estiman datos faltantes.'
        '</p></footer></main></body></html>'
    )
    return buffer.getvalue()
