#!/usr/bin/env bash
set -euo pipefail

# Source locks only: no package installation, call analysis or private config.
if [[ $(uname -s) != Linux || $(uname -m) != x86_64 ]]; then
  echo "This checker requires Linux x86_64." >&2
  exit 2
fi
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
umask 077
report_dir=''
if (( $# )); then
  if (( $# != 2 )) || [[ $1 != --report-dir || -z $2 ]]; then
    echo "Usage: bash scripts/check-dependencies.sh [--report-dir NEW_DIRECTORY]" >&2
    exit 2
  fi
  # Never mix this run with old evidence or overwrite an existing directory.
  mkdir --mode=700 -- "$2"
  report_dir=$(cd -- "$2" && pwd -P)
fi
scratch=$(mktemp -d)
trap 'rm -rf -- "$scratch"' EXIT
report_dir=${report_dir:-$scratch}
python3 scripts/check-dependency-report.py prepare "$report_dir"

download_status=0
curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
  --connect-timeout 15 --max-time 120 \
  https://github.com/google/osv-scanner/releases/download/v2.6.0/osv-scanner_linux_amd64 \
  --output "$scratch/osv-scanner" 2> "$report_dir/download.log" || download_status=$?
if (( download_status )); then
  cat -- "$report_dir/download.log" >&2
  python3 scripts/check-dependency-report.py fail "$report_dir" download "$download_status" || exit "$download_status"
  exit "$download_status"
fi
integrity_status=0
printf '%s  %s\n' \
  ca69b3d3cd08f889a49dc0a383122f71cc528b83803671df5fd874d97485b108 \
  "$scratch/osv-scanner" | sha256sum --check --status || integrity_status=$?
if (( integrity_status )); then
  echo "Downloaded scanner failed SHA256 verification; it was not executed." >> "$report_dir/download.log"
  python3 scripts/check-dependency-report.py fail "$report_dir" integrity "$integrity_status" || exit "$integrity_status"
  exit "$integrity_status"
fi
chmod 700 "$scratch/osv-scanner"
: > "$scratch/empty-config.toml"

scan_status=0
"$scratch/osv-scanner" scan source \
  --config "$scratch/empty-config.toml" \
  --no-resolve --no-call-analysis=go --no-call-analysis=rust \
  --all-vulns --all-packages --format=json --output-file "$report_dir/report.json" \
  --lockfile requirements.txt:requirements-lock.txt \
  --lockfile requirements.txt:requirements-dev-lock.txt \
  --lockfile requirements.txt:requirements-build-lock.txt \
  --lockfile apps/web/pnpm-lock.yaml \
  --lockfile apps/mobile/pnpm-lock.yaml \
  --lockfile apps/mobile/android/app/gradle.lockfile \
  > "$report_dir/scan.log" 2>&1 || scan_status=$?
cat -- "$report_dir/scan.log"

# A successful check must include all six inputs, not just an empty report.
python3 scripts/check-dependency-report.py check "$report_dir" "$scan_status"
