#!/usr/bin/env python3
"""Valida la estructura del registro de entregas sin declarar aceptación del producto."""

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATUS_PATH = ROOT / "docs" / "DELIVERY-STATUS.json"
EXPECTED_PHASE_IDS = [f"phase-{number}" for number in range(1, 9)] + ["sentinel9final"]
PHASE_STATES = {"pending", "in_progress", "software_complete", "awaiting_real_acceptance"}
GATE_STATES = {"pending", "in_progress", "accepted"}


class InvalidStatus(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise InvalidStatus(message)


def check_version_files(data: dict) -> None:
    android = data["candidates"]["android"]
    gradle = (ROOT / "apps/mobile/android/app/build.gradle").read_text(encoding="utf-8")
    require(re.search(r'\bversionCode\s+' + re.escape(str(android["version_code"])) + r'\b', gradle) is not None,
            "La versionCode candidata no coincide con build.gradle.")
    require(re.search(r'\bversionName\s+[\"\']' + re.escape(android["version_name"]) + r'[\"\']', gradle) is not None,
            "La versionName candidata no coincide con build.gradle.")

    ios = data["candidates"]["ios"]
    project = (ROOT / "apps/mobile/ios/App/App.xcodeproj/project.pbxproj").read_text(encoding="utf-8")
    marketing_values = set(re.findall(r'MARKETING_VERSION\s*=\s*([^;]+);', project))
    build_values = set(re.findall(r'CURRENT_PROJECT_VERSION\s*=\s*([^;]+);', project))
    require(marketing_values == {ios["marketing_version"]},
            "La versión candidata iOS no coincide en todas las configuraciones del pbxproj.")
    require(build_values == {str(ios["build_number"])},
            "El número de build candidato iOS no coincide en todas las configuraciones del pbxproj.")


def main() -> int:
    try:
        data = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        require(data.get("schema_version") == 1, "schema_version debe ser 1.")
        require(data.get("objective_status") in {"active", "complete"}, "objective_status inválido.")
        require(data.get("objective_status") != "complete", "El objetivo no puede cerrarse por este checker.")

        phases = data.get("phases")
        require(isinstance(phases, list) and len(phases) == 9, "Se requieren exactamente nueve fases.")
        phase_ids = [phase.get("id") for phase in phases]
        require(phase_ids == EXPECTED_PHASE_IDS, "IDs u orden de fases inválidos; sentinel9final debe cerrar la lista.")
        for phase in phases:
            require(phase.get("status") in PHASE_STATES, f"Estado inválido en {phase.get('id')}.")
            require(isinstance(phase.get("name"), str) and phase["name"].strip(), f"Falta nombre en {phase.get('id')}.")
            for field in ("software_criteria", "real_criteria"):
                criteria = phase.get(field)
                require(isinstance(criteria, list) and criteria and all(isinstance(item, str) and item.strip() for item in criteria),
                        f"Faltan criterios {field} en {phase.get('id')}.")

        gates = data.get("seven_original_real_gates")
        require(isinstance(gates, list) and len(gates) == 7, "Deben preservarse exactamente siete puertas reales.")
        gate_ids = [gate.get("id") for gate in gates]
        require(len(set(gate_ids)) == 7, "Los IDs de las siete puertas deben ser únicos.")
        for gate in gates:
            require(gate.get("status") in GATE_STATES, f"Estado inválido en {gate.get('id')}.")
            require(isinstance(gate.get("criterion"), str) and gate["criterion"].strip(), f"Falta criterio en {gate.get('id')}.")

        acceptance = data.get("acceptance", {})
        require(acceptance.get("status") in {"pending", "in_progress", "accepted"}, "Estado de aceptación inválido.")
        require(acceptance.get("full_objective_complete") is False, "La aceptación no puede cerrar automáticamente el objetivo.")
        for platform in ("android", "ios"):
            require(data["candidates"][platform].get("status") == "local_candidate_not_delivered",
                    f"El candidato {platform} debe distinguirse de una entrega.")

        for reference in data.get("local_references", []):
            require(reference.get("kind") == "local", "Tipo de referencia local inválido.")
            path = (ROOT / reference["path"]).resolve()
            require(path.is_relative_to(ROOT.resolve()) and path.is_file(),
                    f"Referencia local ausente o fuera del repositorio: {reference.get('path')}.")
        check_version_files(data)
    except (OSError, KeyError, TypeError, json.JSONDecodeError, InvalidStatus) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print("Estructura del registro válida; esto no acredita cierre de fases ni aceptación del objetivo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
