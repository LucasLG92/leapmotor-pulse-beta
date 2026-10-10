#!/usr/bin/env bash
set -euo pipefail

# Source locks only: no package installation, call analysis or private config.
if [[ $(uname -s) != Linux || $(uname -m) != x86_64 ]]; then
  echo "This checker requires Linux x86_64." >&2
  exit 2
fi
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
umask 077
scratch=$(mktemp -d)
trap 'rm -rf -- "$scratch"' EXIT

curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
  --connect-timeout 15 --max-time 120 \
  https://github.com/google/osv-scanner/releases/download/v2.6.0/osv-scanner_linux_amd64 \
  --output "$scratch/osv-scanner"
printf '%s  %s\n' \
  ca69b3d3cd08f889a49dc0a383122f71cc528b83803671df5fd874d97485b108 \
  "$scratch/osv-scanner" | sha256sum --check --status
chmod 700 "$scratch/osv-scanner"
: > "$scratch/empty-config.toml"

scan_status=0
"$scratch/osv-scanner" scan source \
  --config "$scratch/empty-config.toml" \
  --no-resolve --no-call-analysis=go --no-call-analysis=rust \
  --all-vulns --all-packages --format=json --output-file "$scratch/report.json" \
  --lockfile requirements.txt:requirements-lock.txt \
  --lockfile requirements.txt:requirements-dev-lock.txt \
  --lockfile requirements.txt:requirements-build-lock.txt \
  --lockfile apps/web/pnpm-lock.yaml \
  --lockfile apps/mobile/pnpm-lock.yaml \
  --lockfile apps/mobile/android/app/gradle.lockfile || scan_status=$?

# A successful check must include all six inputs, not just an empty report.
python3 - "$scratch/report.json" <<'PY'
import json
import sys
from pathlib import Path

root = Path.cwd()
expected = {
    "requirements-lock.txt",
    "requirements-dev-lock.txt",
    "requirements-build-lock.txt",
    "apps/web/pnpm-lock.yaml",
    "apps/mobile/pnpm-lock.yaml",
    "apps/mobile/android/app/gradle.lockfile",
}
report = json.loads(Path(sys.argv[1]).read_text())
seen = set()
advisories = set()
for result in report["results"]:
    path = Path(result["source"]["path"]).resolve().relative_to(root).as_posix()
    packages = result["packages"]
    if path not in expected or path in seen or not packages:
        raise SystemExit("Incomplete or unexpected dependency inventory")
    seen.add(path)
    print(f"{path}: {len(packages)} package entries checked")
    for entry in packages:
        for vulnerability in entry.get("vulnerabilities", []):
            package = entry["package"]
            advisories.add(vulnerability["id"])
            print(f"{path}: {package['name']}@{package['version']} {vulnerability['id']}")
if seen != expected:
    raise SystemExit("Missing dependency lockfile in scan result")
print(f"Known advisory IDs found: {len(advisories)}")
if advisories:
    raise SystemExit(1)
PY
exit "$scan_status"
