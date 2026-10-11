"""Launch only an unconfigured Pulse build in a new, disposable Apple simulator.

The screenshot needs visual review. This does not test OAuth or WidgetKit runtime.
"""
from __future__ import annotations

import json
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "apps/mobile/ios/App/output/ci-smoke"
RUNTIME = "com.apple.CoreSimulator.SimRuntime.iOS-26-5"
DEVICE_TYPE = "com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro-Max"
BUNDLE = "com.lucas.pulse"


class SmokeFailure(RuntimeError):
    def __init__(self, stage: str, failure_type: str):
        super().__init__(f"{stage}:{failure_type}")
        self.stage = stage
        self.failure_type = failure_type


def progress(stage: str) -> None:
    print(f"iOS simulator: {stage}", flush=True)


def run(arguments: list[str], *, stage: str, timeout: int = 60) -> str:
    progress(stage)
    try:
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise SmokeFailure(stage, "timeout") from None
    except OSError:
        raise SmokeFailure(stage, "operation") from None
    if result.returncode:
        raise SmokeFailure(stage, "operation")
    return result.stdout.strip()


def own_device(device_id: str, name: str) -> dict:
    try:
        devices = json.loads(run(["xcrun", "simctl", "list", "devices", "--json"], stage="ownership-check"))
        matches = [device for device in devices["devices"].get(RUNTIME, [])
                   if device.get("udid") == device_id and device.get("name") == name]
    except (ValueError, KeyError, TypeError, AttributeError):
        raise SmokeFailure("ownership-check", "response") from None
    if len(matches) != 1:
        raise SmokeFailure("ownership-check", "ownership")
    return matches[0]


def main() -> None:
    stage = "preflight"
    if sys.platform != "darwin" or len(sys.argv) != 2:
        raise SmokeFailure(stage, "precondition")
    operator_config = ROOT / "apps/mobile/ios/NativeConfig.xcconfig"
    if operator_config.exists() or operator_config.is_symlink():
        raise SmokeFailure(stage, "precondition")
    try:
        app = Path(sys.argv[1]).resolve(strict=True)
        with (app / "Info.plist").open("rb") as stream:
            info = plistlib.load(stream)
    except (OSError, ValueError, plistlib.InvalidFileException):
        raise SmokeFailure(stage, "app-metadata") from None
    if info.get("CFBundleIdentifier") != BUNDLE or info.get("CFBundleExecutable") != "App":
        raise SmokeFailure(stage, "app-identity")
    fields = ("PulseAPIOrigin", "PulseOIDCIssuer", "PulseAuthorizationEndpoint",
              "PulseTokenEndpoint", "PulseClientID", "PulseAudience", "PulseCallback")
    if any(info.get(field) != "" for field in fields):
        raise SmokeFailure(stage, "app-configured")
    try:
        runtimes = json.loads(run(["xcrun", "simctl", "list", "runtimes", "--json"], stage="runtime-check"))
    except (ValueError, KeyError, TypeError):
        raise SmokeFailure("runtime-check", "response") from None
    if not any(row.get("identifier") == RUNTIME and row.get("isAvailable") is True
               for row in runtimes["runtimes"]):
        raise SmokeFailure("runtime-check", "unavailable")
    try:
        types = json.loads(run(["xcrun", "simctl", "list", "devicetypes", "--json"], stage="device-type-check"))
    except (ValueError, KeyError, TypeError):
        raise SmokeFailure("device-type-check", "response") from None
    if not any(row.get("identifier") == DEVICE_TYPE for row in types["devicetypes"]):
        raise SmokeFailure("device-type-check", "unavailable")
    OUTPUT.mkdir(parents=True, exist_ok=False)
    name = "Pulse-CI-" + uuid.uuid4().hex
    receipt = {"runtime": RUNTIME, "device_type": DEVICE_TYPE, "bundle_id": BUNDLE,
               "operator_configuration": False, "installed": False, "launched": False,
               "process_alive_after_seconds": 0, "screenshot_created": False,
               "visual_review_pending": True, "widget_runtime_verified": False,
               "physical_device_verified": False, "owned_simulator_removed": False,
               "failure": None, "cleanup_failure": None}
    device_id = None
    started = time.monotonic()
    operation_failure = None
    cleanup_failure = None
    try:
        stage = "create"
        device_id = run(["xcrun", "simctl", "create", name, DEVICE_TYPE, RUNTIME], stage="create")
        if not re.fullmatch(r"[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}", device_id):
            raise SmokeFailure("create", "identifier")
        stage = "ownership-check"
        own_device(device_id, name)
        stage = "boot"
        run(["xcrun", "simctl", "boot", device_id], stage="boot")
        stage = "bootstatus"
        run(["xcrun", "simctl", "bootstatus", device_id, "-b"], stage="bootstatus", timeout=300)
        stage = "install"
        run(["xcrun", "simctl", "install", device_id, str(app)], stage="install", timeout=180)
        receipt["installed"] = True
        stage = "launch"
        result = run(["xcrun", "simctl", "launch", device_id, BUNDLE], stage="launch")
        match = re.fullmatch(re.escape(BUNDLE) + r": ([1-9][0-9]*)", result)
        if not match:
            raise SmokeFailure("launch", "response")
        receipt["launched"] = True
        pid = match.group(1)
        stage = "process-check"
        for _ in range(5):
            time.sleep(1)
            command = run(["/bin/ps", "-p", pid, "-o", "comm="], stage="process-check")
            if not command.endswith("/App.app/App"):
                raise SmokeFailure("process-check", "not-alive")
            receipt["process_alive_after_seconds"] += 1
        stage = "screenshot"
        screenshot = OUTPUT / "startup.png"
        run(["xcrun", "simctl", "io", device_id, "screenshot", str(screenshot)],
            stage="screenshot", timeout=120)
        if not screenshot.is_file() or screenshot.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
            raise SmokeFailure("screenshot", "invalid-png")
        receipt["screenshot_created"] = True
    except SmokeFailure as exc:
        operation_failure = {"stage": exc.stage, "type": exc.failure_type}
    except Exception:
        operation_failure = {"stage": stage, "type": "operation"}
    finally:
        try:
            if device_id:
                device = own_device(device_id, name)
                if device.get("state") != "Shutdown":
                    run(["xcrun", "simctl", "shutdown", device_id], stage="shutdown")
                run(["xcrun", "simctl", "delete", device_id], stage="delete")
                devices = json.loads(run(["xcrun", "simctl", "list", "devices", "--json"], stage="cleanup-check"))
                if any(row.get("udid") == device_id for rows in devices["devices"].values() for row in rows):
                    raise SmokeFailure("cleanup-check", "not-removed")
                receipt["owned_simulator_removed"] = True
        except SmokeFailure as exc:
            cleanup_failure = {"stage": exc.stage, "type": exc.failure_type}
        except Exception:
            cleanup_failure = {"stage": "cleanup", "type": "operation"}
        finally:
            receipt["elapsed_seconds"] = round(time.monotonic() - started, 2)
            receipt["failure"] = operation_failure or (
                {"stage": "cleanup", "type": "failed"} if cleanup_failure else None)
            receipt["cleanup_failure"] = cleanup_failure
            (OUTPUT / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    if operation_failure or cleanup_failure:
        failure = operation_failure or receipt["failure"]
        raise SmokeFailure(failure["stage"], failure["type"])
    print("App installed, launched and alive for 5 seconds; own simulator removed. Screenshot needs review.")


if __name__ == "__main__":
    sys.tracebacklimit = 0
    main()
