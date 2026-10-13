"""Launch only an unconfigured Pulse build in a new, disposable Apple simulator.

XCTest observes the existing generic login UI. Screenshots still need visual review.
This does not test OAuth or WidgetKit runtime.
"""
from __future__ import annotations

import base64
import binascii
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
UI_SCHEME = "PulseStartupCI"
UI_TEST = "PulseStartupUITests/PulseStartupUITests/testGenericLoginRenders"
UI_READY_MARKER = "PULSE_CI_UI_READY_V2"
LEGACY_UI_READY_MARKER = "PULSE_CI_UI_READY_V1"
UI_PNG_PREFIX = "PULSE_CI_UI_PNG_V2:"
UI_PIXEL_CHECKS_PREFIX = "PULSE_CI_UI_VISUAL_PIXEL_CHECKS_V2:"
MAX_PNG_BYTES = 10 * 1024 * 1024
VISUAL_SAMPLE_SIZE = 32 * 32
MIN_CONTRASTING_PIXELS = 12
MIN_LUMA_DELTA = 12
VISUAL_PIXEL_REASONS = frozenset({
    "none", "not-observed", "deadline", "foreground-changed",
    "accessibility-changed", "frames-changed", "invalid-image",
    *(f"{target}-{reason}" for target in ("heading", "notice") for reason in (
        "invalid-frame", "incoherent-scale", "frame-out-of-bounds",
        "crop-out-of-bounds", "crop-unavailable", "render-unavailable",
        "transparent-pixels", "low-distributed-contrast",
    )),
})
UI_ERROR_WORDS = frozenset({
    "action", "application", "architecture", "available", "build", "built",
    "bundle", "bundles", "cannot", "code", "codesigned", "configured",
    "destination", "disabling", "enabled", "error", "executable", "failure",
    "file", "folder", "found", "framework", "host", "identifier", "invalid",
    "iterations", "launch", "matching", "missing", "no", "not", "option",
    "count", "greater", "maximum", "minimum", "must", "positive", "than",
    "platform", "plist", "product", "products", "project", "provided",
    "required", "requires", "retry", "root", "runner", "scheme", "sdk",
    "signed", "signing", "simulator", "specifier", "target", "test",
    "testable", "tests", "unable", "unsupported", "value",
})
UI_ERROR_OPTIONS = frozenset({"-test-iterations", "-retry-tests-on-failure",
                             "-run-tests-until-failure", "-only-testing",
                             "-scheme", "-destination"})


class SmokeFailure(RuntimeError):
    def __init__(self, stage: str, failure_type: str, diagnostic: dict | None = None):
        super().__init__(f"{stage}:{failure_type}")
        self.stage = stage
        self.failure_type = failure_type
        self.diagnostic = diagnostic


def ui_failure_diagnostic(result: subprocess.CompletedProcess) -> dict:
    """Emit fixed vocabulary only; never retain error text or image transport.

    These indicators describe the failed command, not a proven root cause.
    Unknown words, identifiers, paths, URLs and credentials have no output slot.
    """
    output = result.stderr + "\n" + result.stdout
    errors = re.findall(r"(?m)^xcodebuild: error:([^\n]*)", output)
    words = set()
    options = set()
    for line in errors:
        for token in line.split():
            word = token.strip("\"'.,:;!?").lower()
            if re.fullmatch(r"[a-z]+", word) and word in UI_ERROR_WORDS:
                words.add(word)
            if word in UI_ERROR_OPTIONS:
                options.add(word)
    diagnostic = {"return_code": result.returncode, "xcodebuild_error_detected": bool(errors),
                  "known_error_words": sorted(words), "known_error_options": sorted(options)}
    pixel_checks = _visual_metrics_from_output(output, require_pass=False)
    if pixel_checks is not None:
        diagnostic["visual_pixel_checks"] = pixel_checks
    return diagnostic


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
        diagnostic = ui_failure_diagnostic(result) if stage == "ui-readiness" else None
        raise SmokeFailure(stage, "operation", diagnostic)
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


def screenshot(device_id: str, filename: str, stage: str) -> None:
    path = OUTPUT / filename
    run(["xcrun", "simctl", "io", device_id, "screenshot", str(path)],
        stage=stage, timeout=120)
    if (not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_PNG_BYTES
            or path.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n"):
        raise SmokeFailure(stage, "invalid-png")


def validate_visual_pixel_checks(value: object, *, require_pass: bool) -> dict | None:
    """Accept only the bounded numeric receipt emitted by the current XCTest."""
    expected = {
        "passed", "reason", "sample_size", "minimum_contrasting_pixels",
        "minimum_luma_delta", "heading_contrasting_pixels",
        "heading_p95_minus_p5_luma", "notice_contrasting_pixels",
        "notice_p95_minus_p5_luma", "frames_stable",
        "foreground_revalidated", "accessibility_revalidated",
    }
    if not isinstance(value, dict) or set(value) != expected:
        return None
    integer_fields = (
        "sample_size", "minimum_contrasting_pixels", "minimum_luma_delta",
        "heading_contrasting_pixels", "heading_p95_minus_p5_luma",
        "notice_contrasting_pixels", "notice_p95_minus_p5_luma",
    )
    if any(type(value[field]) is not int for field in integer_fields):
        return None
    if (value["sample_size"] != VISUAL_SAMPLE_SIZE
            or value["minimum_contrasting_pixels"] != MIN_CONTRASTING_PIXELS
            or value["minimum_luma_delta"] != MIN_LUMA_DELTA
            or not 0 <= value["heading_contrasting_pixels"] <= VISUAL_SAMPLE_SIZE
            or not 0 <= value["notice_contrasting_pixels"] <= VISUAL_SAMPLE_SIZE
            or not 0 <= value["heading_p95_minus_p5_luma"] <= 255
            or not 0 <= value["notice_p95_minus_p5_luma"] <= 255):
        return None
    if (type(value["passed"]) is not bool or not isinstance(value["reason"], str)
            or value["reason"] not in VISUAL_PIXEL_REASONS):
        return None
    boolean_fields = ("frames_stable", "foreground_revalidated", "accessibility_revalidated")
    if any(type(value[field]) is not bool for field in boolean_fields):
        return None
    if value["passed"]:
        if (value["reason"] != "none"
                or value["heading_contrasting_pixels"] < MIN_CONTRASTING_PIXELS
                or value["notice_contrasting_pixels"] < MIN_CONTRASTING_PIXELS
                or value["heading_p95_minus_p5_luma"] < MIN_LUMA_DELTA
                or value["notice_p95_minus_p5_luma"] < MIN_LUMA_DELTA
                or not all(value[field] for field in boolean_fields)):
            return None
    elif require_pass or value["reason"] == "none":
        return None
    return {field: value[field] for field in expected}


def _visual_metrics_from_output(output: str, *, require_pass: bool) -> dict | None:
    lines = [line.strip() for line in output.splitlines()]
    records = [line[len(UI_PIXEL_CHECKS_PREFIX):]
               for line in lines if line.startswith(UI_PIXEL_CHECKS_PREFIX)]
    if len(records) != 1:
        return None
    try:
        raw = json.loads(records[0], object_pairs_hook=_unique_json_object,
                         parse_constant=_reject_json_constant)
    except (ValueError, TypeError):
        return None
    return validate_visual_pixel_checks(raw, require_pass=require_pass)


def retain_ui_screenshot(output: str) -> dict:
    """Retain only the exact XCTest screenshot that passed the V2 pixel guard."""
    lines = [line.strip() for line in output.splitlines()]
    images = [line[len(UI_PNG_PREFIX):] for line in lines if line.startswith(UI_PNG_PREFIX)]
    if (lines.count(UI_READY_MARKER) != 1
            or LEGACY_UI_READY_MARKER in lines
            or len(images) != 1):
        raise SmokeFailure("ui-readiness", "readiness-not-reported")
    pixel_checks = _visual_metrics_from_output(output, require_pass=True)
    if pixel_checks is None:
        raise SmokeFailure("ui-readiness", "visual-pixel-checks")
    if len(images[0]) > ((MAX_PNG_BYTES + 2) // 3) * 4:
        raise SmokeFailure("ui-readiness", "invalid-png")
    try:
        data = base64.b64decode(images[0], validate=True)
    except (ValueError, binascii.Error):
        raise SmokeFailure("ui-readiness", "invalid-png") from None
    if len(data) > MAX_PNG_BYTES or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise SmokeFailure("ui-readiness", "invalid-png")
    (OUTPUT / "startup.png").write_bytes(data)
    return pixel_checks


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(value: str):
    raise ValueError("non-finite JSON number")


def main() -> None:
    stage = "preflight"
    if sys.platform != "darwin" or len(sys.argv) != 4:
        raise SmokeFailure(stage, "precondition")
    operator_config = ROOT / "apps/mobile/ios/NativeConfig.xcconfig"
    if operator_config.exists() or operator_config.is_symlink():
        raise SmokeFailure(stage, "precondition")
    try:
        app = Path(sys.argv[1]).resolve(strict=True)
        derived_data = Path(sys.argv[2]).resolve(strict=True)
        spm_clones = Path(sys.argv[3]).resolve(strict=True)
        if (not derived_data.is_dir() or not spm_clones.is_dir()
                or app != derived_data / "Build/Products/Debug-iphonesimulator/App.app"):
            raise SmokeFailure(stage, "build-inputs")
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
    receipt = {"receipt_version": 3,
               "runtime": RUNTIME, "device_type": DEVICE_TYPE, "bundle_id": BUNDLE,
               "operator_configuration": False, "installed": False, "launched": False,
               "process_alive_after_seconds": 0, "screenshot_created": False,
               "diagnostic_screenshot_created": False,
               "launch_observer": "XCTest",
               "process_liveness_observation": "replaced_by_semantic_XCTest",
               "initial_screenshot_created": False,
               "initial_screenshot_scope": "before_XCTest_launch_simulator_state",
               "ui_readiness_test": UI_TEST, "ui_readiness_test_started": False,
               "ui_readiness_command_invoked": False,
               "ui_readiness_method_completed": False,
               "rendered_startup_verified": False,
               "ui_readiness_command_timeout_seconds": 240,
               "test_repetition": "single_test_scheme_without_repetition_options",
               "ui_readiness_failure_diagnostic": None,
               "visual_pixel_checks": None,
               "diagnostic_capture_failure": None,
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
        stage = "ownership-check"
        if own_device(device_id, name).get("state") != "Booted":
            raise SmokeFailure(stage, "not-booted")
        stage = "initial-screenshot"
        screenshot(device_id, "startup-initial.png", stage)
        receipt["initial_screenshot_created"] = True
        stage = "ownership-check"
        if own_device(device_id, name).get("state") != "Booted":
            raise SmokeFailure(stage, "not-booted")
        stage = "ui-readiness"
        receipt["ui_readiness_command_invoked"] = True
        receipt["ui_readiness_test_started"] = True
        ui_output = run([
            "xcodebuild", "test-without-building",
            "-project", str(ROOT / "apps/mobile/ios/App/App.xcodeproj"),
            "-scheme", UI_SCHEME, "-configuration", "Debug",
            "-destination", f"platform=iOS Simulator,id={device_id}",
            "-derivedDataPath", str(derived_data),
            "-clonedSourcePackagesDirPath", str(spm_clones),
            "-resultBundlePath", str(OUTPUT / "ui-readiness.xcresult"),
            "-only-testing:" + UI_TEST,
            "-parallel-testing-enabled", "NO",
            "CODE_SIGNING_ALLOWED=NO", "CODE_SIGNING_REQUIRED=NO", "CODE_SIGN_IDENTITY=",
        ], stage=stage, timeout=240)
        receipt["visual_pixel_checks"] = _visual_metrics_from_output(ui_output, require_pass=False)
        try:
            receipt["visual_pixel_checks"] = retain_ui_screenshot(ui_output)
        except SmokeFailure as exc:
            if receipt["visual_pixel_checks"] is not None:
                exc.diagnostic = {"visual_pixel_checks": receipt["visual_pixel_checks"]}
            raise
        receipt["launched"] = True
        receipt["ui_readiness_method_completed"] = True
        receipt["rendered_startup_verified"] = True
        receipt["screenshot_created"] = True
    except SmokeFailure as exc:
        operation_failure = {"stage": exc.stage, "type": exc.failure_type}
        if exc.stage == "ui-readiness" and exc.diagnostic is not None:
            receipt["ui_readiness_failure_diagnostic"] = exc.diagnostic
            if "visual_pixel_checks" in exc.diagnostic:
                receipt["visual_pixel_checks"] = exc.diagnostic["visual_pixel_checks"]
            print("iOS UI failure diagnostic: " + json.dumps(exc.diagnostic, sort_keys=True), flush=True)
    except Exception:
        operation_failure = {"stage": stage, "type": "operation"}
    finally:
        # Preserve the initial PNG and inspect the failed state only on our own
        # unconfigured simulator. Never replace the original operation failure.
        if operation_failure and device_id and receipt["installed"]:
            try:
                if own_device(device_id, name).get("state") != "Booted":
                    raise SmokeFailure("failure-screenshot", "not-booted")
                screenshot(device_id, "startup-diagnostic.png", "failure-screenshot")
                receipt["screenshot_created"] = True
                receipt["diagnostic_screenshot_created"] = True
            except SmokeFailure as exc:
                receipt["diagnostic_capture_failure"] = {"stage": exc.stage, "type": exc.failure_type}
            except Exception:
                receipt["diagnostic_capture_failure"] = {"stage": "failure-screenshot", "type": "operation"}
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
    print("Generic login UI observed by XCTest; own simulator removed. Screenshots need visual review.")


if __name__ == "__main__":
    sys.tracebacklimit = 0
    main()
