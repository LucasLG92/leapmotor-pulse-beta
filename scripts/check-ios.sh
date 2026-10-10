#!/bin/bash
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
cd "$repo_root"

fail() {
    printf 'Error: %s\n' "$1" >&2
    exit 1
}

if [ "$(uname -s)" != "Darwin" ]; then
    fail 'Esta comprobación requiere macOS y el SDK de Apple.'
fi

python_bin=${PULSE_CHECK_PYTHON:-python}
for executable in "$python_bin" node corepack xcodebuild xcrun swift; do
    if ! command -v "$executable" >/dev/null 2>&1; then
        fail "Falta el ejecutable requerido: $executable"
    fi
done

node_version=$(node --version)
if [ "$node_version" != 'v24.19.0' ]; then
    fail "Se requiere Node.js v24.19.0; detectado $node_version."
fi

"$python_bin" - <<'PY'
import sys

expected = (3, 14, 8)
if sys.version_info[:3] != expected:
    raise SystemExit(
        f"Se requiere Python {expected!r}; detectado {sys.version.split()[0]}."
    )
print(f"Python {sys.version.split()[0]}")
PY

xcode_version=$(xcodebuild -version)
xcode_first_line=$(printf '%s\n' "$xcode_version" | sed -n '1p')
if [ "$xcode_first_line" != 'Xcode 26.6' ]; then
    fail "Se requiere Xcode 26.6; detectado ${xcode_first_line:-sin versión}."
fi

simulator_sdk=$(xcrun --sdk iphonesimulator --show-sdk-path)
if [ -z "$simulator_sdk" ] || [ ! -d "$simulator_sdk" ]; then
    fail 'No está disponible el SDK iOS Simulator de Apple.'
fi
if ! xcrun --sdk iphonesimulator --find swift >/dev/null 2>&1; then
    fail 'El SDK iOS Simulator no ofrece la herramienta Swift de Xcode.'
fi

native_config="$repo_root/apps/mobile/ios/NativeConfig.xcconfig"
if [ -e "$native_config" ] || [ -L "$native_config" ]; then
    fail 'Retira NativeConfig.xcconfig antes de esta comprobación; no se ha leído su contenido.'
fi

printf '%s\n' 'Preparando pnpm 11.25.0 mediante Corepack.'
corepack prepare pnpm@11.25.0 --activate
pnpm_version=$(corepack pnpm --version)
if [ "$pnpm_version" != '11.25.0' ]; then
    fail "Se requiere pnpm 11.25.0; detectado $pnpm_version."
fi

tmp_root=$(mktemp -d "${TMPDIR:-/tmp}/pulse-ios-check.XXXXXX")
derived_data="$tmp_root/DerivedData"
spm_clones="$tmp_root/SourcePackages"
cleanup() {
    status=$?
    trap - EXIT
    if [ "$status" -eq 0 ]; then
        rm -rf "$tmp_root"
    else
        printf 'La comprobación falló; se conservan DerivedData y SPM en: %s\n' "$tmp_root" >&2
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

printf '%s\n' 'Instalando dependencias web y móvil desde los lockfiles.'
(cd apps/web && corepack pnpm install --frozen-lockfile)
(cd apps/mobile && corepack pnpm install --frozen-lockfile)

printf '%s\n' 'Comprobando, probando y construyendo web y móvil.'
(cd apps/web && corepack pnpm check)
(cd apps/web && corepack pnpm test)
(cd apps/web && corepack pnpm build)
(cd apps/mobile && corepack pnpm check)
(cd apps/mobile && corepack pnpm test)
(cd apps/mobile && corepack pnpm build)
(cd apps/mobile && corepack pnpm sync:ios)

printf '%s\n' 'Comprobando el proyecto Xcode y las pruebas de PulseCore.'
"$python_bin" scripts/check_ios_project.py
swift test --package-path apps/mobile/ios/PulseCore

project=apps/mobile/ios/App/App.xcodeproj
scheme=App
printf '%s\n' 'Resolviendo paquetes Swift en un directorio temporal propio.'
xcodebuild -resolvePackageDependencies \
    -project "$project" \
    -scheme "$scheme" \
    -clonedSourcePackagesDirPath "$spm_clones"

for configuration in Debug Release; do
    printf 'Construyendo App y PulseWidget para iOS Simulator (%s), sin firma.\n' "$configuration"
    xcodebuild build \
        -project "$project" \
        -scheme "$scheme" \
        -configuration "$configuration" \
        -destination 'generic/platform=iOS Simulator' \
        -derivedDataPath "$derived_data" \
        -clonedSourcePackagesDirPath "$spm_clones" \
        CODE_SIGNING_ALLOWED=NO \
        CODE_SIGNING_REQUIRED=NO \
        CODE_SIGN_IDENTITY=

    app="$derived_data/Build/Products/${configuration}-iphonesimulator/App.app"
    widget="$app/PlugIns/PulseWidget.appex"
    if [ ! -d "$app" ]; then
        fail "No se generó App.app para $configuration."
    fi
    if [ ! -d "$widget" ]; then
        fail "No se generó PulseWidget.appex dentro de App.app para $configuration."
    fi
    printf 'Productos presentes para %s: App.app y PulseWidget.appex.\n' "$configuration"
done

printf '%s\n' 'Comprobación iOS de Apple completada para Debug y Release.'
printf '%s\n' 'Instalando y arrancando la app genérica en un simulador propio.'
"$python_bin" scripts/ios-simulator-smoke.py \
    "$derived_data/Build/Products/Debug-iphonesimulator/App.app"
