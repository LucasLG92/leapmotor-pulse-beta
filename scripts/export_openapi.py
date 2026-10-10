"""Genera el contrato sin iniciar SQLite, leer credenciales o consultar la nube."""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pulse.app import create_app
from pulse.config import Config

target = ROOT / "docs/openapi.json"
target.write_text(json.dumps(create_app(config=Config()).openapi(), ensure_ascii=False, indent=2) + "\n")
print("Contrato generado: docs/openapi.json")
