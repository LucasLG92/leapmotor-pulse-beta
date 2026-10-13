"""Synthetic packaging fixtures, not proof of Xcode/device compatibility."""

import hashlib
import importlib.util
import json
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch
import zipfile


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/package-ios-device.py"
SPEC = importlib.util.spec_from_file_location("ios_device_package", SCRIPT)
packager = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packager)
COMMIT = "a" * 40


class IOSDevicePackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root / "products/App.app"
        self.widget = self.app / "PlugIns/PulseWidget.appex"
        for path, identifier, executable in [
            (self.app, "com.lucas.pulse", "App"),
            (self.widget, "com.lucas.pulse.widget", "PulseWidget"),
        ]:
            path.mkdir(parents=True, exist_ok=True)
            self.write_info(path, {
                "CFBundleIdentifier": identifier, "CFBundleExecutable": executable,
                "CFBundleSupportedPlatforms": ["iPhoneOS"],
                "CFBundleShortVersionString": "0.1.1", "CFBundleVersion": "2",
                **{field: "" for field in packager.CONNECTION_FIELDS},
            })
            binary = path / executable
            binary.write_bytes(b"synthetic executable; not Mach-O")
            binary.chmod(0o755)
        (self.app / "public").mkdir()
        (self.app / "public/index.html").write_text("<title>Synthetic Pulse</title>")
        self.output = self.root / "export"
        self.addCleanup(patch.stopall)
        patch.object(packager, "ROOT", self.root).start()

    def write_info(self, path, values):
        (path / "Info.plist").write_bytes(plistlib.dumps(values))

    def change_info(self, path, **values):
        info = plistlib.loads((path / "Info.plist").read_bytes())
        info.update(values)
        self.write_info(path, info)

    def run_package(self):
        return packager.package(self.app, self.output, COMMIT)

    def test_archive_layout_hashes_receipt_and_executable_mode(self):
        receipt = self.run_package()
        archive = self.output / "unsigned.ipa"
        self.assertEqual(receipt, json.loads((self.output / "receipt.json").read_text()))
        self.assertEqual(receipt["source_commit"], COMMIT)
        self.assertFalse(receipt["signed"])
        self.assertFalse(receipt["physical_installation_verified"])
        self.assertFalse(receipt["widget_runtime_verified"])
        self.assertEqual(receipt["ipa_sha256"], hashlib.sha256(archive.read_bytes()).hexdigest())
        with zipfile.ZipFile(archive) as zipped:
            self.assertIn("Payload/App.app/PlugIns/PulseWidget.appex/PulseWidget", zipped.namelist())
            self.assertEqual(set(zipped.namelist()), set(receipt["file_sha256"]))
            for name, digest in receipt["file_sha256"].items():
                self.assertEqual(hashlib.sha256(zipped.read(name)).hexdigest(), digest)
            self.assertTrue((zipped.getinfo("Payload/App.app/App").external_attr >> 16) & 0o111)

    def test_simulator_and_wrong_bundle_identity_are_rejected(self):
        for path, values in [
            (self.app, {"CFBundleSupportedPlatforms": ["iPhoneSimulator"]}),
            (self.widget, {"CFBundleSupportedPlatforms": ["iPhoneSimulator"]}),
            (self.app, {"CFBundleIdentifier": "com.example.other"}),
            (self.widget, {"CFBundleIdentifier": "com.example.other.widget"}),
        ]:
            with self.subTest(values=values, bundle=path.name):
                original = (path / "Info.plist").read_bytes()
                self.change_info(path, **values)
                with self.assertRaises(packager.PackageError):
                    self.run_package()
                self.assertFalse(self.output.exists())
                (path / "Info.plist").write_bytes(original)

    def test_configured_or_missing_connection_fields_are_rejected(self):
        original = (self.app / "Info.plist").read_bytes()
        for field in packager.CONNECTION_FIELDS:
            for value in ["https://example.invalid/fixture", None]:
                with self.subTest(field=field, missing=value is None):
                    info = plistlib.loads(original)
                    if value is None:
                        del info[field]
                    else:
                        info[field] = value
                    self.write_info(self.app, info)
                    with self.assertRaises(packager.PackageError):
                        self.run_package()
                    self.assertFalse(self.output.exists())
        (self.app / "Info.plist").write_bytes(original)

    def test_private_config_presence_blocks_export_without_reading_it(self):
        config = self.root / "apps/mobile/ios/NativeConfig.xcconfig"
        config.parent.mkdir(parents=True)
        config.symlink_to(self.root / "missing-private-file")
        with self.assertRaisesRegex(packager.PackageError, "Private native configuration"):
            self.run_package()
        self.assertFalse(self.output.exists())

    def test_bundle_symlinks_and_signing_material_are_rejected(self):
        link = self.app / "public/foreign"
        link.symlink_to(self.root / "does-not-exist")
        with self.assertRaisesRegex(packager.PackageError, "links"):
            self.run_package()
        link.unlink()
        for name in ["embedded.mobileprovision", "certificate.p12", "private.key", "_CodeSignature"]:
            with self.subTest(name=name):
                bad = self.app / name
                bad.write_bytes(b"synthetic marker, not signing material")
                with self.assertRaisesRegex(packager.PackageError, "Signing material"):
                    self.run_package()
                bad.unlink()

    def test_existing_output_and_overlapping_output_are_preserved(self):
        self.output.mkdir()
        sentinel = self.output / "previous"
        sentinel.write_text("preserve previous artifact")
        with self.assertRaisesRegex(packager.PackageError, "already exists"):
            self.run_package()
        self.assertEqual(sentinel.read_text(), "preserve previous artifact")
        with self.assertRaisesRegex(packager.PackageError, "separate"):
            packager.package(self.app, self.app / "export", COMMIT)

    def test_bad_versions_missing_products_and_limits_fail_before_output(self):
        original = (self.widget / "Info.plist").read_bytes()
        for values in [{"CFBundleVersion": "3"}, {"CFBundleShortVersionString": "invalid"}]:
            self.change_info(self.widget, **values)
            with self.assertRaises(packager.PackageError):
                self.run_package()
            (self.widget / "Info.plist").write_bytes(original)
        for name, value in [("MAX_FILES", 1), ("MAX_BYTES", 1)]:
            with patch.object(packager, name, value), self.assertRaisesRegex(packager.PackageError, "size limit"):
                self.run_package()
        (self.widget / "PulseWidget").unlink()
        with self.assertRaisesRegex(packager.PackageError, "Missing native executable"):
            self.run_package()
        self.assertFalse(self.output.exists())

    def test_revision_and_atomic_failure_do_not_publish_partial_artifact(self):
        with self.assertRaisesRegex(packager.PackageError, "exact Git commit"):
            packager.package(self.app, self.output, "main")
        with patch.object(zipfile.ZipFile, "write", side_effect=OSError("synthetic write failure")):
            with self.assertRaises(OSError):
                self.run_package()
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob(".pulse-unsigned-*")))


if __name__ == "__main__":
    unittest.main()
