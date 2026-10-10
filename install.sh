#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT="leapmotor-pulse-beta-public"
COMPOSE_ARGS=(
  --project-directory "$ROOT"
  --project-name "$PROJECT"
  --env-file /dev/null
  --file "$ROOT/compose.yaml"
  --file "$ROOT/deploy/community/compose.local.yaml"
)

usage() {
  cat <<'EOF'
Uso: bash install.sh [--check | --install | --stop | --help]

  --check    Comprueba Linux, Docker Engine local, Compose v2 y los archivos.
  --install  Construye la imagen beta y arranca Pulse en localhost:8780.
  --stop     Detiene el servicio de esta instalación sin borrar sus datos.
  --help     Muestra esta ayuda.

Sin opción, muestra la ayuda. El instalador no instala Docker ni usa sudo.
EOF
}

fail() {
  printf 'Error: %s\n' "$1" >&2
  exit 1
}

docker_clean() {
  local -a clean_env=(env -i "PATH=$PATH" "HOME=${HOME:-/}")
  if [[ -n "${DOCKER_CONFIG:-}" ]]; then
    clean_env+=("DOCKER_CONFIG=$DOCKER_CONFIG")
  fi
  "${clean_env[@]}" docker "$@"
}

compose() {
  docker_clean compose "${COMPOSE_ARGS[@]}" "$@"
}

port_responds() {
  (exec 3<>/dev/tcp/127.0.0.1/8780) >/dev/null 2>&1
}

check_install_port() {
  if ! port_responds; then
    return
  fi

  local owned_containers
  owned_containers="$(docker_clean ps \
    --filter "label=com.docker.compose.project=$PROJECT" \
    --filter 'label=com.docker.compose.service=pulse' \
    --format '{{.ID}}')" || fail "no puedo comprobar si el servicio Pulse de esta instalación posee el puerto 127.0.0.1:8780."
  [[ -n "$owned_containers" ]] || fail "127.0.0.1:8780 ya responde y no pertenece a Pulse beta de este proyecto. Detén el servicio que ocupa el puerto o libera el puerto antes de instalar."
}

check() {
  [[ "$(uname -s)" == Linux ]] || fail "esta beta requiere Linux con Docker Engine nativo; Docker Desktop en macOS/Windows no está soportado."
  command -v docker >/dev/null 2>&1 || fail "no encuentro Docker CLI. Instala Docker Engine y Compose v2 por tu cuenta y vuelve a ejecutar el instalador."

  local compose_version context_endpoint engine_info os_type operating_system
  compose_version="$(docker_clean compose version 2>/dev/null)" || fail "no puedo ejecutar Docker Compose. Comprueba que Compose v2 está instalado."
  [[ "$compose_version" =~ Docker\ Compose\ version\ v?2([.[:space:]]|$) ]] || fail "se requiere Docker Compose v2; encontrado: ${compose_version:-versión desconocida}."

  context_endpoint="$(docker_clean context inspect --format '{{.Endpoints.docker.Host}}' 2>/dev/null)" || fail "no puedo consultar el contexto local de Docker. Comprueba el daemon y el contexto activo."
  [[ "$context_endpoint" == unix://* ]] || fail "el contexto Docker activo no usa un socket Unix local; selecciona un Docker Engine local."

  engine_info="$(docker_clean info --format '{{.OSType}}|{{.OperatingSystem}}' 2>/dev/null)" || fail "Docker CLI está instalado, pero el daemon local no responde. Inicia Docker Engine y vuelve a ejecutar el instalador."
  os_type="${engine_info%%|*}"
  operating_system="${engine_info#*|}"
  [[ "$os_type" == linux ]] || fail "el daemon Docker debe usar contenedores Linux (detectado: ${os_type:-desconocido})."
  [[ "$operating_system" != *Docker\ Desktop* ]] || fail "Docker Desktop en macOS/Windows no está soportado; se requiere Docker Engine nativo en Linux."

  [[ -f "$ROOT/compose.yaml" && -f "$ROOT/deploy/community/compose.local.yaml" && -f "$ROOT/Dockerfile" ]] || fail "faltan archivos del paquete de instalación."
  compose config --quiet || fail "la configuración Compose no es válida."
  printf 'Preflight correcto: Docker Engine local para Linux y Compose v2 disponibles.\n'
}

action="${1:---help}"
[[ $# -le 1 ]] || fail "indica una sola opción. Usa --help."

case "$action" in
  --help|-h)
    usage
    ;;
  --check)
    check
    ;;
  --install)
    check
    check_install_port
    compose up --build --detach --wait --wait-timeout 90 --no-deps pulse
    printf 'Pulse beta está disponible en http://localhost:8780\n'
    printf 'Para detenerla sin borrar los datos, ejecuta: bash install.sh --stop\n'
    ;;
  --stop)
    check
    compose stop pulse
    ;;
  *)
    fail "opción desconocida: $action. Usa --help."
    ;;
esac
