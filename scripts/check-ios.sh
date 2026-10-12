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
# macOS TMPDIR can use /var, a symlink to /private/var. Keep our new
# workspace on its physical path so strict audit input checks remain useful.
tmp_root=$(cd "$tmp_root" && pwd -P)
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
"$python_bin" -m unittest discover -s tests -p test_ios_spm_audit.py
"$python_bin" -m unittest discover -s tests -p test_ios_simulator_smoke.py
swift test --package-path apps/mobile/ios/PulseCore
printf '%s\n' 'Comprobando mutaciones de consentimiento con Keychain falso.'
credential_tests="$tmp_root/WidgetCredentialTests"
mkdir -p "$credential_tests/Sources/WidgetCredentialStore" "$credential_tests/Tests/WidgetCredentialStoreTests"
cp apps/mobile/ios/App/App/NativeBridge/NativeWidgetCredentialStore.swift \
    "$credential_tests/Sources/WidgetCredentialStore/NativeWidgetCredentialStore.swift"
cp tests/ios/NativeWidgetCredentialStoreTests.swift \
    "$credential_tests/Tests/WidgetCredentialStoreTests/NativeWidgetCredentialStoreTests.swift"
cat > "$credential_tests/Package.swift" <<'SWIFT'
// swift-tools-version: 6.0
import PackageDescription
let package = Package(
    name: "WidgetCredentialTests",
    platforms: [.macOS(.v13)],
    targets: [
        .target(name: "WidgetCredentialStore"),
        .testTarget(name: "WidgetCredentialStoreTests", dependencies: ["WidgetCredentialStore"])
    ],
    swiftLanguageModes: [.v5]
)
SWIFT
swift test --package-path "$credential_tests"

project=apps/mobile/ios/App/App.xcodeproj
scheme=App
printf '%s\n' 'Resolviendo paquetes Swift en un directorio temporal propio.'
xcodebuild -resolvePackageDependencies \
    -project "$project" \
    -scheme "$scheme" \
    -clonedSourcePackagesDirPath "$spm_clones"

spm_resolution="$repo_root/$project/project.xcworkspace/xcshareddata/swiftpm/Package.resolved"
spm_checkout="$spm_clones/checkouts/capacitor-swift-pm"
spm_evidence="$repo_root/apps/mobile/ios/App/output/ci-spm"
printf '%s\n' 'Conservando la resolución Swift y consultando avisos conocidos por revisión.'
"$python_bin" scripts/ios-spm-audit.py capture \
    --lockfile "$spm_resolution" \
    --checkout "$spm_checkout" \
    --output "$spm_evidence"

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

printf '%s\n' 'Comprobando que los builds conservaron la resolución Swift revisada.'
"$python_bin" scripts/ios-spm-audit.py verify \
    --lockfile "$spm_resolution" \
    --checkout "$spm_checkout" \
    --output "$spm_evidence"

printf '%s\n' 'Comprobación iOS de Apple completada para Debug y Release.'
printf '%s\n' 'Instalando y arrancando la app genérica en un simulador propio.'
"$python_bin" scripts/ios-simulator-smoke.py \
    "$derived_data/Build/Products/Debug-iphonesimulator/App.app"

if [ "${PULSE_CHECK_IOS_DEVICE_IPA:-0}" = '1' ]; then
    if [[ ! "${GITHUB_SHA:-}" =~ ^[0-9a-f]{40}$ ]]; then
        fail 'El artefacto de dispositivo requiere la revisión Git exacta de CI.'
    fi
    printf '%s\n' 'Construyendo App y PulseWidget para iPhone, sin firma.'
    xcodebuild build \
        -project "$project" \
        -scheme "$scheme" \
        -configuration Release \
        -destination 'generic/platform=iOS' \
        -derivedDataPath "$derived_data" \
        -clonedSourcePackagesDirPath "$spm_clones" \
        CODE_SIGNING_ALLOWED=NO \
        CODE_SIGNING_REQUIRED=NO \
        CODE_SIGN_IDENTITY=
    "$python_bin" scripts/package-ios-device.py \
        --app "$derived_data/Build/Products/Release-iphoneos/App.app" \
        --output "$repo_root/apps/mobile/ios/App/output/ci-device" \
        --git-commit "$GITHUB_SHA"
    "$python_bin" scripts/ios-spm-audit.py verify \
        --lockfile "$spm_resolution" \
        --checkout "$spm_checkout" \
        --output "$spm_evidence"
elif [ "${PULSE_CHECK_IOS_DEVICE_IPA:-0}" != '0' ]; then
    fail 'PULSE_CHECK_IOS_DEVICE_IPA solo admite 0 o 1.'
fi
