import base64
import importlib.util
import json
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stdout
from io import StringIO


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/ios-simulator-smoke.py"
SPEC = importlib.util.spec_from_file_location("ios_simulator_smoke", SCRIPT)
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


class IOSSimulatorSmokeTests(unittest.TestCase):
    def test_ui_failure_diagnostic_has_only_fixed_words_and_no_free_text(self):
        secret = "CANARY_PRIVATE_TOKEN_20261013"
        result = subprocess.CompletedProcess([], 70, stdout=smoke.UI_PNG_PREFIX + secret,
            stderr="xcodebuild: error: The scheme private-scheme cannot test: missing bundles at /private/" + secret + " https://example.invalid/?token=" + secret)
        diagnostic = smoke.ui_failure_diagnostic(result)
        self.assertEqual(diagnostic["return_code"], 70)
        self.assertTrue(diagnostic["xcodebuild_error_detected"])
        self.assertEqual(diagnostic["known_error_words"], ["bundles", "cannot", "missing", "scheme", "test"])
        self.assertNotIn(secret, json.dumps(diagnostic))
        self.assertNotIn("private", json.dumps(diagnostic))
        self.assertNotIn("https", json.dumps(diagnostic))

    def test_run_ui_nonzero_never_accepts_readiness_or_prints_raw_output(self):
        secret = "CANARY_PRIVATE_TOKEN_20261013"
        output = smoke.UI_READY_MARKER + "\n" + smoke.UI_PNG_PREFIX + secret
        captured = StringIO()
        with mock.patch.object(smoke.subprocess, "run", return_value=subprocess.CompletedProcess([], 64, output, "xcodebuild: error: invalid option " + secret)), redirect_stdout(captured):
            with self.assertRaises(smoke.SmokeFailure) as raised:
                smoke.run(["xcodebuild", "test-without-building"], stage="ui-readiness")
        self.assertEqual(raised.exception.failure_type, "operation")
        self.assertEqual(raised.exception.diagnostic["known_error_words"], ["invalid", "option"])
        self.assertNotIn(secret, captured.getvalue())
        self.assertNotIn(smoke.UI_READY_MARKER, captured.getvalue())

    def test_non_ui_failure_does_not_collect_error_text(self):
        result = subprocess.CompletedProcess([], 1, "secret", "xcodebuild: error: secret")
        with mock.patch.object(smoke.subprocess, "run", return_value=result):
            with self.assertRaises(smoke.SmokeFailure) as raised:
                smoke.run(["xcrun"], stage="install")
        self.assertIsNone(raised.exception.diagnostic)

    def test_unknown_ui_error_keeps_only_return_code(self):
        diagnostic = smoke.ui_failure_diagnostic(subprocess.CompletedProcess([], 65, "secret", "secret"))
        self.assertEqual(diagnostic, {"return_code": 65, "xcodebuild_error_detected": False, "known_error_words": []})

    def test_ui_failure_diagnostic_does_not_replace_original_failure_or_cleanup(self):
        diagnostic = {"return_code": 70, "xcodebuild_error_detected": True, "known_error_words": ["test"]}
        def failed_ui(arguments, *, stage, timeout=60):
            if stage == "ui-readiness":
                raise smoke.SmokeFailure(stage, "operation", diagnostic)
            return self.successful_run(arguments, stage=stage, timeout=timeout)
        with self.assertRaises(smoke.SmokeFailure):
            self.run_main(failed_ui)
        receipt = self.receipt()
        self.assertEqual(receipt["failure"], {"stage": "ui-readiness", "type": "operation"})
        self.assertEqual(receipt["ui_readiness_failure_diagnostic"], diagnostic)
        self.assertFalse(receipt["rendered_startup_verified"])
        self.assertTrue(receipt["initial_screenshot_created"])
        self.assertTrue(receipt["owned_simulator_removed"])
        self.assertIsNone(receipt["cleanup_failure"])

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.derived_data = self.root / "DerivedData"
        self.spm_clones = self.root / "SourcePackages"
        self.spm_clones.mkdir()
        self.app = self.derived_data / "Build/Products/Debug-iphonesimulator/App.app"
        self.app.mkdir(parents=True)
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
            mock.patch.object(smoke.sys, "argv", [str(SCRIPT), str(self.app),
                                                str(self.derived_data), str(self.spm_clones)]),
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
        if stage in {"screenshot", "initial-screenshot", "failure-screenshot"}:
            Path(arguments[-1]).write_bytes(b"\x89PNG\r\n\x1a\nimage")
        if stage == "ui-readiness":
            return smoke.UI_PNG_PREFIX + base64.b64encode(b"\x89PNG\r\n\x1a\nui-image").decode() + "\n" + smoke.UI_READY_MARKER
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
        self.assertTrue(receipt["initial_screenshot_created"])
        self.assertTrue(receipt["rendered_startup_verified"])
        self.assertEqual((self.output / "startup.png").read_bytes(), b"\x89PNG\r\n\x1a\nui-image")
        self.assertEqual((self.output / "startup-initial.png").read_bytes(), b"\x89PNG\r\n\x1a\nimage")
        self.assertTrue(receipt["owned_simulator_removed"])
        self.assertIsNone(receipt["failure"])
        self.assertIsNone(receipt["cleanup_failure"])
        calls = {call.kwargs["stage"]: call.kwargs.get("timeout", 60) for call in run_mock.call_args_list}
        self.assertEqual(calls["bootstatus"], 300)
        self.assertEqual(calls["install"], 180)
        self.assertEqual(calls["initial-screenshot"], 120)
        self.assertEqual(calls["launch"], 60)
        self.assertEqual(calls["ui-readiness"], 240)
        ui_call = next(call for call in run_mock.call_args_list if call.kwargs["stage"] == "ui-readiness")
        self.assertIn("test-without-building", ui_call.args[0])
        self.assertIn("-only-testing:" + smoke.UI_TEST, ui_call.args[0])
        self.assertIn(f"platform=iOS Simulator,id={self.device_id}", ui_call.args[0])
        self.assertNotIn("-allowProvisioningUpdates", ui_call.args[0])
        self.assertNotIn("-retry-tests-on-failure", ui_call.args[0])
        self.assertNotIn("-run-tests-until-failure", ui_call.args[0])
        self.assertEqual(ui_call.args[0][ui_call.args[0].index("-test-iterations") + 1], "1")
        self.assertEqual(ui_call.args[0][ui_call.args[0].index("-parallel-testing-enabled") + 1], "NO")

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
            if stage in {"initial-screenshot", "failure-screenshot"}:
                self.assertEqual(timeout, 120)
                raise smoke.SmokeFailure(stage, "timeout")
            return ""

        with self.assertRaises(smoke.SmokeFailure):
            self.run_main(run_with_screenshot_timeout)
        receipt = self.receipt()
        self.assertEqual(receipt["failure"], {"stage": "initial-screenshot", "type": "timeout"})
        self.assertTrue(receipt["installed"])
        self.assertTrue(receipt["launched"])
        self.assertFalse(receipt["screenshot_created"])
        self.assertTrue(receipt["owned_simulator_removed"])

    def test_changed_ownership_withholds_delete_and_records_cleanup_failure(self):
        ownership = mock.Mock(side_effect=[{"state": "Booted"},
            smoke.SmokeFailure("ownership-check", "ownership"),
            smoke.SmokeFailure("ownership-check", "ownership")])
        stages = []

        def timeout_at_screenshot(arguments, *, stage, timeout=60):
            stages.append(stage)
            if stage == "create":
                return self.device_id
            if stage == "launch":
                return f"{smoke.BUNDLE}: 42"
            if stage == "process-check":
                return "/tmp/App.app/App"
            if stage == "initial-screenshot":
                raise smoke.SmokeFailure(stage, "timeout")
            return ""

        with self.assertRaises(smoke.SmokeFailure):
            self.run_main(timeout_at_screenshot, ownership)
        receipt = self.receipt()
        self.assertEqual(receipt["failure"], {"stage": "initial-screenshot", "type": "timeout"})
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
            if path.name in {"startup.png", "startup-initial.png"}:
                raise OSError("synthetic file failure")
            return original_read_bytes(path)

        with mock.patch.object(Path, "read_bytes", read_bytes):
            with self.assertRaises(smoke.SmokeFailure) as caught:
                self.run_main(self.successful_run)
        self.assertEqual(str(caught.exception), "initial-screenshot:operation")
        self.assertEqual(self.receipt()["failure"], {"stage": "initial-screenshot", "type": "operation"})
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

    def test_live_process_without_ui_observation_fails_and_preserves_initial_png(self):
        def no_ui_marker(arguments, *, stage, timeout=60):
            if stage == "ui-readiness":
                return "** TEST SUCCEEDED **"
            return self.successful_run(arguments, stage=stage, timeout=timeout)
        with self.assertRaises(smoke.SmokeFailure) as caught:
            self.run_main(no_ui_marker)
        self.assertEqual(str(caught.exception), "ui-readiness:readiness-not-reported")
        receipt = self.receipt()
        self.assertFalse(receipt["rendered_startup_verified"])
        self.assertTrue(receipt["initial_screenshot_created"])
        self.assertTrue(receipt["owned_simulator_removed"])
        self.assertEqual((self.output / "startup-initial.png").read_bytes(), b"\x89PNG\r\n\x1a\nimage")

    def test_ui_timeout_and_diagnostic_capture_failure_do_not_mask_each_other(self):
        def failures(arguments, *, stage, timeout=60):
            if stage == "ui-readiness":
                raise smoke.SmokeFailure(stage, "timeout")
            if stage == "failure-screenshot":
                raise smoke.SmokeFailure(stage, "operation")
            return self.successful_run(arguments, stage=stage, timeout=timeout)
        with self.assertRaises(smoke.SmokeFailure) as caught:
            self.run_main(failures)
        self.assertEqual(str(caught.exception), "ui-readiness:timeout")
        receipt = self.receipt()
        self.assertEqual(receipt["diagnostic_capture_failure"], {"stage": "failure-screenshot", "type": "operation"})
        self.assertTrue(receipt["owned_simulator_removed"])
        self.assertFalse(receipt["rendered_startup_verified"])

    def test_ui_transport_rejects_missing_duplicate_malformed_and_non_png_records(self):
        image = smoke.UI_PNG_PREFIX + base64.b64encode(b"\x89PNG\r\n\x1a\nimage").decode()
        for text in (smoke.UI_READY_MARKER, image,
                     image + "\n" + smoke.UI_READY_MARKER + "\n" + smoke.UI_READY_MARKER,
                     image + "\n" + image + "\n" + smoke.UI_READY_MARKER,
                     smoke.UI_PNG_PREFIX + "!!!\n" + smoke.UI_READY_MARKER,
                     smoke.UI_PNG_PREFIX + base64.b64encode(b"not-png").decode() + "\n" + smoke.UI_READY_MARKER):
            with self.subTest(record=text[:30]), mock.patch.object(smoke, "OUTPUT", self.output):
                self.output.mkdir(exist_ok=True)
                with self.assertRaises(smoke.SmokeFailure):
                    smoke.retain_ui_screenshot(text)
                self.assertFalse((self.output / "startup.png").exists())

    def test_ui_transport_bounds_encoded_and_decoded_image_size(self):
        with mock.patch.object(smoke, "MAX_PNG_BYTES", 8), mock.patch.object(smoke, "OUTPUT", self.output):
            for data in (b"\x89PNG\r\n\x1a\nX", b"\x89PNG\r\n\x1a\n" + b"x" * 100):
                with self.subTest(size=len(data)), self.assertRaises(smoke.SmokeFailure):
                    smoke.retain_ui_screenshot(smoke.UI_PNG_PREFIX + base64.b64encode(data).decode() + "\n" + smoke.UI_READY_MARKER)

    def test_ui_build_input_must_match_the_preflight_app(self):
        with mock.patch.object(smoke.sys, "platform", "darwin"), mock.patch.object(smoke, "ROOT", self.root), \
             mock.patch.object(smoke.sys, "argv", [str(SCRIPT), str(self.app), str(self.root), str(self.spm_clones)]):
            with self.assertRaises(smoke.SmokeFailure) as caught:
                smoke.main()
        self.assertEqual(str(caught.exception), "preflight:build-inputs")
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
