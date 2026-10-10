#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_root"

python_bin=${PULSE_CHECK_PYTHON:-python}
pnpm_bin=${PULSE_CHECK_PNPM:-pnpm}
docker_bin=${PULSE_CHECK_DOCKER:-docker}

for executable in "$python_bin" "$pnpm_bin" "$docker_bin" node; do
    if ! command -v "$executable" >/dev/null 2>&1; then
        printf 'Falta el ejecutable requerido: %s\n' "$executable" >&2
        exit 1
    fi
done

"$python_bin" - <<'PY'
import sys

expected = (3, 12, 14)
if sys.version_info[:3] != expected:
    raise SystemExit(f"Se requiere Python {expected!r}; detectado {sys.version.split()[0]}.")
print(f"Python {sys.version.split()[0]}")
PY

node_version=$(node --version)
if [ "$node_version" != "v24.19.0" ]; then
    printf 'Se requiere Node.js v24.19.0; detectado %s.\n' "$node_version" >&2
    exit 1
fi

pnpm_version=$("$pnpm_bin" --version)
if [ "$pnpm_version" != "11.25.0" ]; then
    printf 'Se requiere pnpm 11.25.0; detectado %s.\n' "$pnpm_version" >&2
    exit 1
fi

# This check is intentionally limited to a local Unix-socket Docker engine;
# the script must not send builds to an SSH/TCP remote Docker context.
if [ -n "${DOCKER_HOST:-}" ] || [ -n "${DOCKER_CONTEXT:-}" ]; then
    printf '%s\n' 'Desactiva DOCKER_HOST/DOCKER_CONTEXT; el verificador solo usa el contexto local.' >&2
    exit 1
fi
docker_context=$("$docker_bin" context show)
docker_endpoint=$("$docker_bin" context inspect "$docker_context" --format '{{ .Endpoints.docker.Host }}')
case "$docker_endpoint" in
    unix://*) ;;
    *)
        printf 'Se requiere Docker local por socket Unix; contexto actual: %s.\n' "$docker_endpoint" >&2
        exit 1
        ;;
esac
"$docker_bin" info --format 'Docker Engine {{.ServerVersion}}'

printf '%s\n' 'Instalando frontend desde el lockfile congelado.'
(cd apps/web && "$pnpm_bin" install --frozen-lockfile)
(cd apps/mobile && "$pnpm_bin" install --frozen-lockfile)
printf '%s\n' 'Generando dist antes de las pruebas de readiness.'
(cd apps/web && "$pnpm_bin" build)

container_id=
tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/pulse-sqlite-verify.XXXXXX")
cleanup() {
    status=$?
    trap - EXIT HUP INT TERM
    if [ -n "$container_id" ]; then
        "$docker_bin" rm "$container_id" >/dev/null 2>&1 || true
    fi
    rm -rf "$tmp_dir"
    exit "$status"
}
trap cleanup EXIT HUP INT TERM

printf '%s\n' 'Construyendo y extrayendo la SQLite del stage sqlite-build del Dockerfile.'
# Keep this untagged stage in Docker's build cache. Removing a temporary tag
# can also delete a reused image and its untagged ancestors from earlier builds.
# The immutable image ID avoids a mutable tag between build and extraction.
sqlite_image=$("$docker_bin" build --quiet --target sqlite-build .)
container_id=$("$docker_bin" create --label "com.leapmotor-pulse.sqlite-check=$$" "$sqlite_image")
mkdir -p "$tmp_dir/sqlite"
"$docker_bin" cp "$container_id:/sqlite-out/libsqlite3.so.0" "$tmp_dir/sqlite/libsqlite3.so.0"

export LD_LIBRARY_PATH="$tmp_dir/sqlite${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
"$python_bin" - <<'PY'
import sqlite3

expected = "3.53.4"
if sqlite3.sqlite_version != expected:
    raise SystemExit(
        f"Las pruebas deben usar SQLite {expected}; detectado {sqlite3.sqlite_version}. "
        "Comprueba que el runtime de Python enlaza libsqlite3.so.0 dinámicamente."
    )
print(f"Python usa SQLite {sqlite3.sqlite_version} del stage de producción.")
PY

export PULSE_CHECK_PYTHON="$python_bin"
export PULSE_CHECK_PNPM="$pnpm_bin"
printf '%s\n' 'Ejecutando scripts/check.py (incluye OpenAPI, pytest, tipos HTTP y Svelte).'
"$python_bin" scripts/check.py

printf '%s\n' 'Construyendo imagen de producción y ejecutando sus checks de runtime.'
sh scripts/build-container.sh
