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


def run(arguments: list[str], *, timeout: int = 60) -> str:
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        operation = arguments[2] if arguments[:2] == ["xcrun", "simctl"] else "process-check"
        raise RuntimeError(f"Apple simulator operation failed: {operation}; no raw logs collected.")
    return result.stdout.strip()


def own_device(device_id: str, name: str) -> dict:
    devices = json.loads(run(["xcrun", "simctl", "list", "devices", "--json"]))
    matches = [device for device in devices["devices"].get(RUNTIME, [])
               if device.get("udid") == device_id and device.get("name") == name]
    if len(matches) != 1:
        raise RuntimeError("Simulator ownership changed; cleanup withheld.")
    return matches[0]


def main() -> None:
    if sys.platform != "darwin" or len(sys.argv) != 2:
        raise RuntimeError("Requires macOS and one compiled App.app path.")
    operator_config = ROOT / "apps/mobile/ios/NativeConfig.xcconfig"
    if operator_config.exists() or operator_config.is_symlink():
        raise RuntimeError("Operator configuration present; it has not been read.")
    app = Path(sys.argv[1]).resolve(strict=True)
    with (app / "Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    if info.get("CFBundleIdentifier") != BUNDLE or info.get("CFBundleExecutable") != "App":
        raise RuntimeError("Unexpected app identity.")
    fields = ("PulseAPIOrigin", "PulseOIDCIssuer", "PulseAuthorizationEndpoint",
              "PulseTokenEndpoint", "PulseClientID", "PulseAudience", "PulseCallback")
    if any(info.get(field) != "" for field in fields):
        raise RuntimeError("Only an unconfigured app may produce CI screenshots.")
    runtimes = json.loads(run(["xcrun", "simctl", "list", "runtimes", "--json"]))
    if not any(row.get("identifier") == RUNTIME and row.get("isAvailable") is True
               for row in runtimes["runtimes"]):
        raise RuntimeError("Required iOS 26.5 runtime unavailable; nothing downloaded.")
    types = json.loads(run(["xcrun", "simctl", "list", "devicetypes", "--json"]))
    if not any(row.get("identifier") == DEVICE_TYPE for row in types["devicetypes"]):
        raise RuntimeError("Required iPhone 17 Pro Max simulator type unavailable.")
    OUTPUT.mkdir(parents=True, exist_ok=False)
    name = "Pulse-CI-" + uuid.uuid4().hex
    receipt = {"runtime": RUNTIME, "device_type": DEVICE_TYPE, "bundle_id": BUNDLE,
               "operator_configuration": False, "installed": False, "launched": False,
               "process_alive_after_seconds": 0, "screenshot_created": False,
               "visual_review_pending": True, "widget_runtime_verified": False,
               "physical_device_verified": False, "owned_simulator_removed": False}
    device_id = None
    started = time.monotonic()
    try:
        device_id = run(["xcrun", "simctl", "create", name, DEVICE_TYPE, RUNTIME])
        if not re.fullmatch(r"[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}", device_id):
            raise RuntimeError("Simulator creation returned an invalid identifier.")
        own_device(device_id, name)
        run(["xcrun", "simctl", "boot", device_id])
        run(["xcrun", "simctl", "bootstatus", device_id, "-b"], timeout=300)
        run(["xcrun", "simctl", "install", device_id, str(app)])
        receipt["installed"] = True
        result = run(["xcrun", "simctl", "launch", device_id, BUNDLE])
        match = re.fullmatch(re.escape(BUNDLE) + r": ([1-9][0-9]*)", result)
        if not match:
            raise RuntimeError("App launch did not return its process identifier.")
        receipt["launched"] = True
        pid = match.group(1)
        for _ in range(5):
            time.sleep(1)
            command = run(["/bin/ps", "-p", pid, "-o", "comm="])
            if not command.endswith("/App.app/App"):
                raise RuntimeError("Expected Pulse process did not remain alive.")
            receipt["process_alive_after_seconds"] += 1
        screenshot = OUTPUT / "startup.png"
        run(["xcrun", "simctl", "io", device_id, "screenshot", str(screenshot)])
        if not screenshot.is_file() or screenshot.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
            raise RuntimeError("Screenshot was not a PNG.")
        receipt["screenshot_created"] = True
    finally:
        try:
            if device_id:
                device = own_device(device_id, name)
                if device.get("state") != "Shutdown":
                    run(["xcrun", "simctl", "shutdown", device_id])
                run(["xcrun", "simctl", "delete", device_id])
                devices = json.loads(run(["xcrun", "simctl", "list", "devices", "--json"]))
                if any(row.get("udid") == device_id for rows in devices["devices"].values() for row in rows):
                    raise RuntimeError("Owned simulator was not removed.")
                receipt["owned_simulator_removed"] = True
        finally:
            receipt["elapsed_seconds"] = round(time.monotonic() - started, 2)
            (OUTPUT / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print("App installed, launched and alive for 5 seconds; own simulator removed. Screenshot needs review.")


if __name__ == "__main__":
    sys.tracebacklimit = 0
    main()
