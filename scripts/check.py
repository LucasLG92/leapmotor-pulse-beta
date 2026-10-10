"""Verificación local/CI sin instalación automática, red de fabricante ni secretos."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def run(args: list[str], cwd: Path = ROOT) -> None:
    subprocess.run(args, cwd=cwd, check=True)


def main() -> None:
    from pulse.app import create_app
    from pulse.config import Config

    # Config explícita: nunca usar PULSE_* privados del entorno de despliegue.
    documented = json.loads((ROOT / "docs/openapi.json").read_text())
    actual = create_app(config=Config()).openapi()
    if documented != actual:
        raise SystemExit("OpenAPI desactualizado: ejecuta python scripts/export_openapi.py.")

    pnpm = os.environ.get("PULSE_CHECK_PNPM") or shutil.which("pnpm")
    if not pnpm:
        raise SystemExit("Falta pnpm. Instala las dependencias de desarrollo según README.md.")
    run([sys.executable, "scripts/check_ios_project.py"])
    run([sys.executable, "-m", "pytest", "-q"])
    web = ROOT / "apps/web"
    with tempfile.TemporaryDirectory(prefix="pulse-contract-") as directory:
        generated = Path(directory) / "api.generated.ts"
        run([pnpm, "exec", "openapi-typescript", str(ROOT / "docs/openapi.json"), "-o", str(generated)], web)
        if generated.read_text() != (web / "src/lib/api.generated.ts").read_text():
            raise SystemExit("Tipos HTTP desactualizados: ejecuta pnpm types:api en apps/web.")
    run([pnpm, "check"], web)
    run([pnpm, "test"], web)
    run([pnpm, "build"], web)
    mobile = ROOT / "apps/mobile"
    run([pnpm, "check"], mobile)
    run([pnpm, "test"], mobile)
    run([pnpm, "build"], mobile)
    print("Contrato, tests, tipado y build verificados. Dispositivos y nube requieren su QA separado.")


if __name__ == "__main__":
    main()
