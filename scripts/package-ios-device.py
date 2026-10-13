"""Package only a generic unsigned App/WidgetKit iPhone build, never sign it."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import re
import stat
import tempfile
import zipfile


CONNECTION_FIELDS = (
    "PulseAPIOrigin", "PulseOIDCIssuer", "PulseAuthorizationEndpoint",
    "PulseTokenEndpoint", "PulseClientID", "PulseAudience", "PulseCallback",
)
MAX_FILES = 4096
MAX_BYTES = 256 * 1024 * 1024
ROOT = Path(__file__).resolve().parents[1]


class PackageError(ValueError):
    """Fixed errors must not include paths, configuration or file contents."""


def bundle_info(path: Path, identifier: str, executable: str) -> dict:
    try:
        info = plistlib.loads((path / "Info.plist").read_bytes())
    except (OSError, ValueError, plistlib.InvalidFileException):
        raise PackageError("Invalid bundle metadata.") from None
    if not isinstance(info, dict) or info.get("CFBundleIdentifier") != identifier:
        raise PackageError("Unexpected bundle identity.")
    if info.get("CFBundleExecutable") != executable or info.get("CFBundleSupportedPlatforms") != ["iPhoneOS"]:
        raise PackageError("An unsigned iPhone build is required.")
    if not (path / executable).is_file():
        raise PackageError("Missing native executable.")
    version, build = info.get("CFBundleShortVersionString"), info.get("CFBundleVersion")
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,2}", version):
        raise PackageError("Invalid bundle version.")
    if not isinstance(build, str) or not re.fullmatch(r"[1-9][0-9]*", build):
        raise PackageError("Invalid bundle build number.")
    return info


def package(app: Path, output: Path, commit: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise PackageError("An exact Git commit is required.")
    private_config = ROOT / "apps/mobile/ios/NativeConfig.xcconfig"
    if private_config.exists() or private_config.is_symlink():
        raise PackageError("Private native configuration is not allowed.")
    if app.is_symlink() or app.name != "App.app" or not app.is_dir():
        raise PackageError("Expected a generated App.app directory.")
    # Validate all entries before reading bundle metadata or packaging anything.
    files: list[Path] = []
    total = 0
    for entry in app.rglob("*"):
        mode = entry.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise PackageError("Bundle links or special files are not allowed.")
        if entry.name == "_CodeSignature" or entry.name == "embedded.mobileprovision" or entry.suffix.lower() in {".p12", ".pfx", ".pem", ".key", ".mobileprovision"}:
            raise PackageError("Signing material is not allowed.")
        if stat.S_ISREG(mode):
            size = entry.stat().st_size
            total += size
            files.append(entry)
            if len(files) > MAX_FILES or total > MAX_BYTES:
                raise PackageError("Bundle size limit exceeded.")
    info = bundle_info(app, "com.lucas.pulse", "App")
    widget = bundle_info(app / "PlugIns/PulseWidget.appex", "com.lucas.pulse.widget", "PulseWidget")
    if any(info.get(field) != "" for field in CONNECTION_FIELDS):
        raise PackageError("Only an unconfigured generic app can be exported.")
    version = (info["CFBundleShortVersionString"], info["CFBundleVersion"])
    if version != (widget["CFBundleShortVersionString"], widget["CFBundleVersion"]):
        raise PackageError("App and widget versions must match.")
    app_real = app.resolve()
    output_real = output.resolve()
    if output_real.is_relative_to(app_real) or app_real.is_relative_to(output_real):
        raise PackageError("Output must be separate from the bundle.")
    if output.exists() or output.is_symlink():
        raise PackageError("Output already exists; preserve previous artifacts.")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Stage next to the output, then publish the complete directory once.
    with tempfile.TemporaryDirectory(prefix=".pulse-unsigned-", dir=output.parent) as temporary:
        stage = Path(temporary)
        archive = stage / "unsigned.ipa"
        hashes = {}
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
            for path in sorted(files):
                name = "Payload/App.app/" + path.relative_to(app).as_posix()
                hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
                zipped.write(path, name)
        receipt = {
            "schema_version": 1, "source_commit": commit,
            "classification": "generic_unsigned_device_package",
            "bundle_id": info["CFBundleIdentifier"], "widget_bundle_id": widget["CFBundleIdentifier"],
            "version": version[0], "build_number": version[1],
            "platform": "iPhoneOS", "signed": False, "configured": False,
            "physical_installation_verified": False, "widget_runtime_verified": False,
            "requires_personal_or_distribution_signing": True,
            "ipa_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "ipa_bytes": archive.stat().st_size, "file_sha256": hashes,
        }
        (stage / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        stage.rename(output)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--git-commit", required=True)
    args = parser.parse_args()
    try:
        package(args.app, args.output, args.git_commit)
    except (PackageError, OSError) as error:
        raise SystemExit(str(error) if isinstance(error, PackageError) else "Device package could not be created.") from None
    print("Generic unsigned iPhone package created; signing and physical acceptance remain pending.")


if __name__ == "__main__":
    main()
