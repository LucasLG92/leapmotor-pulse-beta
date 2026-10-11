import importlib.util
import json
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/ios-simulator-smoke.py"
SPEC = importlib.util.spec_from_file_location("ios_simulator_smoke", SCRIPT)
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


class IOSSimulatorSmokeTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.app = self.root / "App.app"
        self.app.mkdir()
        with (self.app / "Info.plist").open("wb") as stream:
            plistlib.dump({
                "CFBundleIdentifier": smoke.BUNDLE,
                "CFBundleExecutable": "App",
                **{field: "" for field in (
                    "PulseAPIOrigin", "PulseOIDCIssuer", "PulseAuthorizationEndpoint",
                    "PulseTokenEndpoint", "PulseClientID", "PulseAudience", "PulseCallback",
                )},
            }, stream)
        self.output = self.root / "output"
        self.device_id = "12345678-1234-1234-1234-123456789abc"

    def tearDown(self):
        self.tempdir.cleanup()

    def run_main(self, run_side_effect, ownership_side_effect=None):
        ownership = ownership_side_effect or mock.Mock(return_value={"state": "Booted"})

        def dispatched(arguments, *, stage, timeout=60):
            if stage == "runtime-check":
                return json.dumps({"runtimes": [{"identifier": smoke.RUNTIME, "isAvailable": True}]})
            if stage == "device-type-check":
                return json.dumps({"devicetypes": [{"identifier": smoke.DEVICE_TYPE}]})
            if stage == "cleanup-check":
                return json.dumps({"devices": {smoke.RUNTIME: []}})
            return run_side_effect(arguments, stage=stage, timeout=timeout)

        with (
            mock.patch.object(smoke, "ROOT", self.root),
            mock.patch.object(smoke, "OUTPUT", self.output),
            mock.patch.object(smoke.sys, "platform", "darwin"),
            mock.patch.object(smoke.sys, "argv", [str(SCRIPT), str(self.app)]),
            mock.patch.object(smoke, "run", side_effect=dispatched) as run_mock,
            mock.patch.object(smoke, "own_device", side_effect=ownership),
            mock.patch.object(smoke.time, "sleep"),
        ):
            return smoke.main(), run_mock, ownership

    def successful_run(self, arguments, *, stage, timeout=60):
        if stage == "create":
            return self.device_id
        if stage == "launch":
            return f"{smoke.BUNDLE}: 42"
        if stage == "process-check":
            return "/tmp/App.app/App"
        if stage == "screenshot":
            Path(arguments[-1]).write_bytes(b"\x89PNG\r\n\x1a\nimage")
        return ""

    def receipt(self):
        return json.loads((self.output / "receipt.json").read_text())

    def test_success_preserves_smoke_invariants_and_operation_timeouts(self):
        _, run_mock, _ = self.run_main(self.successful_run)
        receipt = self.receipt()
        self.assertTrue(receipt["installed"])
        self.assertTrue(receipt["launched"])
        self.assertEqual(receipt["process_alive_after_seconds"], 5)
        self.assertTrue(receipt["screenshot_created"])
        self.assertTrue(receipt["owned_simulator_removed"])
        self.assertIsNone(receipt["failure"])
        self.assertIsNone(receipt["cleanup_failure"])
        calls = {call.kwargs["stage"]: call.kwargs.get("timeout", 60) for call in run_mock.call_args_list}
        self.assertEqual(calls["bootstatus"], 300)
        self.assertEqual(calls["install"], 180)
        self.assertEqual(calls["screenshot"], 120)
        self.assertEqual(calls["launch"], 60)

    def test_install_timeout_records_failure_and_still_cleans_owned_device(self):
        def run_with_install_timeout(arguments, *, stage, timeout=60):
            if stage == "create":
                return self.device_id
            if stage == "install":
                self.assertEqual(timeout, 180)
                raise smoke.SmokeFailure(stage, "timeout")
            return ""

        with self.assertRaises(smoke.SmokeFailure):
            self.run_main(run_with_install_timeout)
        receipt = self.receipt()
        self.assertEqual(receipt["failure"], {"stage": "install", "type": "timeout"})
        self.assertIsNone(receipt["cleanup_failure"])
        self.assertTrue(receipt["owned_simulator_removed"])
        self.assertFalse(receipt["installed"])
        self.assertFalse(receipt["screenshot_created"])

    def test_screenshot_timeout_is_not_reported_as_success(self):
        def run_with_screenshot_timeout(arguments, *, stage, timeout=60):
            if stage == "create":
                return self.device_id
            if stage == "launch":
                return f"{smoke.BUNDLE}: 42"
            if stage == "process-check":
                return "/tmp/App.app/App"
            if stage == "screenshot":
                self.assertEqual(timeout, 120)
                raise smoke.SmokeFailure(stage, "timeout")
            return ""

        with self.assertRaises(smoke.SmokeFailure):
            self.run_main(run_with_screenshot_timeout)
        receipt = self.receipt()
        self.assertEqual(receipt["failure"], {"stage": "screenshot", "type": "timeout"})
        self.assertTrue(receipt["installed"])
        self.assertTrue(receipt["launched"])
        self.assertFalse(receipt["screenshot_created"])
        self.assertTrue(receipt["owned_simulator_removed"])

    def test_changed_ownership_withholds_delete_and_records_cleanup_failure(self):
        ownership = mock.Mock(side_effect=[{"state": "Booted"}, smoke.SmokeFailure("ownership-check", "ownership")])
        stages = []

        def timeout_at_screenshot(arguments, *, stage, timeout=60):
            stages.append(stage)
            if stage == "create":
                return self.device_id
            if stage == "launch":
                return f"{smoke.BUNDLE}: 42"
            if stage == "process-check":
                return "/tmp/App.app/App"
            if stage == "screenshot":
                raise smoke.SmokeFailure(stage, "timeout")
            return ""

        with self.assertRaises(smoke.SmokeFailure):
            self.run_main(timeout_at_screenshot, ownership)
        receipt = self.receipt()
        self.assertEqual(receipt["failure"], {"stage": "screenshot", "type": "timeout"})
        self.assertEqual(receipt["cleanup_failure"], {"stage": "ownership-check", "type": "ownership"})
        self.assertFalse(receipt["owned_simulator_removed"])
        self.assertNotIn("delete", stages)

    def test_run_timeout_error_does_not_include_raw_command(self):
        secret_path = "/private/operator/path"
        with mock.patch.object(smoke.subprocess, "run", side_effect=smoke.subprocess.TimeoutExpired(
            ["xcrun", "simctl", "install", "DEVICE", secret_path], 180
        )):
            with self.assertRaises(smoke.SmokeFailure) as caught:
                smoke.run(["xcrun", "simctl", "install", "DEVICE", secret_path],
                          stage="install", timeout=180)
        self.assertEqual(caught.exception.stage, "install")
        self.assertEqual(caught.exception.failure_type, "timeout")
        self.assertNotIn(secret_path, str(caught.exception))

    def test_operator_config_is_rejected_before_reading_app_metadata(self):
        config = self.root / "apps/mobile/ios/NativeConfig.xcconfig"
        config.parent.mkdir(parents=True)
        config.touch()
        with mock.patch.object(smoke.plistlib, "load", side_effect=AssertionError("metadata read")):
            with self.assertRaises(smoke.SmokeFailure) as caught:
                self.run_main(self.successful_run)
        self.assertEqual(str(caught.exception), "preflight:precondition")
        self.assertFalse(self.output.exists())

    def test_configured_app_cannot_produce_screenshot_or_receipt(self):
        metadata = self.app / "Info.plist"
        with metadata.open("rb") as stream:
            info = plistlib.load(stream)
        info["PulseAPIOrigin"] = "https://example.invalid"
        with metadata.open("wb") as stream:
            plistlib.dump(info, stream)
        with self.assertRaises(smoke.SmokeFailure) as caught:
            self.run_main(self.successful_run)
        self.assertEqual(str(caught.exception), "preflight:app-configured")
        self.assertFalse(self.output.exists())

    def test_screenshot_file_error_is_attributed_to_screenshot(self):
        original_read_bytes = Path.read_bytes

        def read_bytes(path):
            if path == self.output / "startup.png":
                raise OSError("synthetic file failure")
            return original_read_bytes(path)

        with mock.patch.object(Path, "read_bytes", read_bytes):
            with self.assertRaises(smoke.SmokeFailure) as caught:
                self.run_main(self.successful_run)
        self.assertEqual(str(caught.exception), "screenshot:operation")
        self.assertEqual(self.receipt()["failure"], {"stage": "screenshot", "type": "operation"})
        self.assertTrue(self.receipt()["owned_simulator_removed"])

    def test_invalid_ownership_response_fails_without_raw_data(self):
        with mock.patch.object(smoke, "run", return_value="not-json"):
            with self.assertRaises(smoke.SmokeFailure) as caught:
                smoke.own_device(self.device_id, "synthetic-name")
        self.assertEqual(str(caught.exception), "ownership-check:response")

    def test_run_os_error_keeps_its_operation_stage(self):
        with mock.patch.object(smoke.subprocess, "run", side_effect=OSError("synthetic path")):
            with self.assertRaises(smoke.SmokeFailure) as caught:
                smoke.run(["synthetic-command"], stage="install", timeout=180)
        self.assertEqual(str(caught.exception), "install:operation")


if __name__ == "__main__":
    unittest.main()
