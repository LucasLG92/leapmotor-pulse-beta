"""Security and preservation checks for the local unsigned iOS configurator."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import plistlib
import stat
import struct
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/configure-ios-ipa.py"
SPEC = importlib.util.spec_from_file_location("ios_ipa_configurator", SCRIPT)
configurator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(configurator)

PUBLIC_CONFIG = {
    "pulse.apiOrigin": "https://pulse.example.test",
    "pulse.issuer": "https://auth.example.test:8443/tenant",
    "pulse.authorizationEndpoint": "https://auth.example.test:8443/tenant/oauth/authorize",
    "pulse.tokenEndpoint": "https://auth.example.test:8443/tenant/oauth/token",
    "pulse.clientId": "pulse-public-example",
    "pulse.audience": "https://pulse.example.test/api",
}


def macho(*, signed: bool = False, cpu_type: int = configurator.ARM64, platform: int = 2) -> bytes:
    commands = [struct.pack("<IIIIII", configurator.LC_BUILD_VERSION, 24, platform, 0x110000, 0x120000, 0)]
    if signed:
        commands.append(struct.pack("<IIII", configurator.LC_CODE_SIGNATURE, 16, 0, 0))
    command_bytes = b"".join(commands)
    header = struct.pack(
        "<IiiIIIII", 0xFEEDFACF, cpu_type, 0, 2, len(commands), len(command_bytes), 0, 0
    )
    return header + command_bytes + b"synthetic-arm64-code"


def bundle_info(identifier: str, executable: str) -> dict:
    info = {
        "CFBundleIdentifier": identifier,
        "CFBundleExecutable": executable,
        "CFBundleSupportedPlatforms": ["iPhoneOS"],
        "CFBundleShortVersionString": "0.1.1",
        "CFBundleVersion": "2",
        "PulseAppGroup": configurator.DEFAULT_APP_GROUP,
    }
    if identifier == configurator.APP_ID:
        info.update({field: "" for field in configurator.APP_INFO_FIELDS})
    return info


class IOSConfigureIPATests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input_ipa = self.root / "source.ipa"
        self.config_path = self.root / "native.properties"
        self.output = self.root / "configured.ipa"
        self.write_config(PUBLIC_CONFIG)
        self.write_ipa()
        self.source_digest = hashlib.sha256(self.input_ipa.read_bytes()).hexdigest()

    def write_config(self, values: dict[str, str]) -> None:
        lines = [f"{key}={values[key]}" for key in configurator.CONFIG_KEYS if key in values]
        for key, value in values.items():
            if key not in configurator.CONFIG_KEYS:
                lines.append(f"{key}={value}")
        self.config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def standard_members(self, *, signed_app: bool = False) -> list[tuple[str, bytes, int | None]]:
        return [
            (configurator.APP_INFO, plistlib.dumps(bundle_info(configurator.APP_ID, "App")), None),
            (configurator.APP_EXECUTABLE, macho(signed=signed_app), 0o100755),
            (configurator.WIDGET_INFO, plistlib.dumps(bundle_info(configurator.WIDGET_ID, "PulseWidget")), None),
            (configurator.WIDGET_EXECUTABLE, macho(), 0o100755),
            ("Payload/App.app/public/index.html", b"<html>fixture</html>", None),
            ("Payload/App.app/public/cordova.js", b"", None),
        ]

    def write_ipa(self, members: list[tuple[str, bytes, int | None]] | None = None) -> None:
        with zipfile.ZipFile(self.input_ipa, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data, mode in members if members is not None else self.standard_members():
                info = zipfile.ZipInfo(name)
                info.compress_type = zipfile.ZIP_DEFLATED
                if mode is not None:
                    info.external_attr = mode << 16
                archive.writestr(info, data)

    def configure(self, *, group: str = configurator.DEFAULT_APP_GROUP):
        with patch.object(configurator, "AUTHORIZED_SOURCE_SHA256", self.source_digest):
            return configurator.configure_ipa(
                self.input_ipa,
                self.source_digest,
                self.config_path,
                self.output,
                group,
            )

    def test_configures_app_and_widget_and_preserves_every_other_file(self):
        original = self.input_ipa.read_bytes()
        with zipfile.ZipFile(self.input_ipa) as source:
            original_payload = {item.filename: source.read(item) for item in source.infolist()}

        receipt = self.configure(group="group.example.pulse")
        receipt_path = Path(str(self.output) + ".receipt.json")
        self.assertTrue(self.output.is_file())
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(receipt_path.stat().st_mode), 0o600)
        self.assertEqual(hashlib.sha256(self.input_ipa.read_bytes()).hexdigest(), self.source_digest)
        self.assertEqual(self.input_ipa.read_bytes(), original)
        self.assertFalse(receipt["signed"])
        self.assertFalse(receipt["installed"])
        self.assertFalse(receipt["app_group_entitlement_and_profile_match_verified"])

        with zipfile.ZipFile(self.output) as configured:
            self.assertEqual(set(configured.namelist()), set(original_payload))
            for name, before in original_payload.items():
                if name not in {configurator.APP_INFO, configurator.WIDGET_INFO}:
                    self.assertEqual(configured.read(name), before, name)
            app = plistlib.loads(configured.read(configurator.APP_INFO))
            widget = plistlib.loads(configured.read(configurator.WIDGET_INFO))
        self.assertEqual(app["PulseAPIOrigin"], PUBLIC_CONFIG["pulse.apiOrigin"])
        self.assertEqual(app["PulseOIDCIssuer"], PUBLIC_CONFIG["pulse.issuer"])
        self.assertEqual(app["PulseAuthorizationEndpoint"], PUBLIC_CONFIG["pulse.authorizationEndpoint"])
        self.assertEqual(app["PulseTokenEndpoint"], PUBLIC_CONFIG["pulse.tokenEndpoint"])
        self.assertEqual(app["PulseClientID"], PUBLIC_CONFIG["pulse.clientId"])
        self.assertEqual(app["PulseAudience"], PUBLIC_CONFIG["pulse.audience"])
        self.assertEqual(app["PulseCallback"], "https://pulse.example.test/native/oauth/callback")
        self.assertEqual(app["PulseAppGroup"], "group.example.pulse")
        self.assertEqual(widget["PulseAppGroup"], "group.example.pulse")

        raw_receipt = receipt_path.read_text(encoding="utf-8")
        self.assertEqual(json.loads(raw_receipt), receipt)
        self.assertNotIn(PUBLIC_CONFIG["pulse.apiOrigin"], raw_receipt)
        self.assertNotIn(PUBLIC_CONFIG["pulse.issuer"], raw_receipt)

    def test_rejects_a_different_expected_digest_and_source_without_output(self):
        with self.assertRaisesRegex(configurator.PackageError, "audited generic IPA"):
            configurator.configure_ipa(
                self.input_ipa, "0" * 64, self.config_path, self.output
            )
        self.assertFalse(self.output.exists())

        self.input_ipa.write_bytes(self.input_ipa.read_bytes() + b"changed")
        with patch.object(configurator, "AUTHORIZED_SOURCE_SHA256", self.source_digest):
            with self.assertRaisesRegex(configurator.PackageError, "does not match"):
                configurator.configure_ipa(
                    self.input_ipa, self.source_digest, self.config_path, self.output
                )
        self.assertFalse(self.output.exists())

    def test_rejects_credentials_unknown_keys_duplicates_and_invalid_urls(self):
        configs = [
            {**PUBLIC_CONFIG, "pulse.clientSecret": "should-never-be-read"},
            {**PUBLIC_CONFIG, "pulse.apiOrigin": "https://user:password@pulse.example.test"},
            {**PUBLIC_CONFIG, "pulse.apiOrigin": "https://pulse.example.test/path"},
            {**PUBLIC_CONFIG, "pulse.tokenEndpoint": "https://auth.example.test:8443/tenant/../token"},
            {**PUBLIC_CONFIG, "pulse.authorizationEndpoint": "https://auth.example.test:8443/tenant/oauth/authorize?next=x"},
            {**PUBLIC_CONFIG, "pulse.tokenEndpoint": "http://auth.example.test/tenant/oauth/token"},
            {**PUBLIC_CONFIG, "pulse.clientId": "client with space"},
            {
                **PUBLIC_CONFIG,
                "pulse.authorizationEndpoint": "https://auth.example.test:8443/tenant/oauth/<authorize",
            },
            {
                **PUBLIC_CONFIG,
                "pulse.authorizationEndpoint": 'https://auth.example.test:8443/tenant/oauth/"authorize',
            },
            {**PUBLIC_CONFIG, "pulse.authorizationEndpoint": "https://other.example.test/tenant/oauth/authorize"},
            {**PUBLIC_CONFIG, "pulse.clientId": "pulse\u200bpublic"},
            {
                **PUBLIC_CONFIG,
                "pulse.apiOrigin": "https://" + "a" * 240 + "." + "b" * 240 + ".example",
            },
        ]
        for values in configs:
            with self.subTest(config=values):
                self.write_config(values)
                with self.assertRaises(configurator.PackageError) as raised:
                    self.configure()
                self.assertNotIn("should-never-be-read", str(raised.exception))
                self.assertFalse(self.output.exists())

        self.config_path.write_text(
            "\n".join(f"{key}={PUBLIC_CONFIG[key]}" for key in configurator.CONFIG_KEYS)
            + f"\n{configurator.CONFIG_KEYS[0]}=https://duplicate.example.test\n",
            encoding="utf-8",
        )
        with self.assertRaises(configurator.PackageError):
            self.configure()
        self.assertFalse(self.output.exists())

    def test_rejects_invalid_group_and_mismatched_input_bundles(self):
        for value in ["com.example.pulse", "group.example..pulse", "group.example/../../x", "group.example.pulse?x"]:
            with self.subTest(group=value), self.assertRaises(configurator.PackageError):
                self.configure(group=value)
        self.assertFalse(self.output.exists())

        members = self.standard_members()
        widget_info = plistlib.loads(members[2][1])
        widget_info["PulseAppGroup"] = "group.other.pulse"
        members[2] = (members[2][0], plistlib.dumps(widget_info), members[2][2])
        self.write_ipa(members)
        self._refresh_pinned_hash()
        with self.assertRaisesRegex(configurator.PackageError, "default App Group"):
            self.configure()
        self.assertFalse(self.output.exists())

    def test_rejects_signed_macho_provisioning_material_zip_paths_duplicates_links_and_bombs(self):
        signed = self.standard_members(signed_app=True)
        self.write_ipa(signed)
        self._refresh_pinned_hash()
        with self.assertRaisesRegex(configurator.PackageError, "already signed"):
            self.configure()

        for extra_name in [
            "Payload/App.app/embedded.mobileprovision",
            "Payload/App.app/certificate.p12",
            "Payload/App.app/private.keystore",
            "Payload/App.app/_CodeSignature/CodeResources",
        ]:
            with self.subTest(extra_name=extra_name):
                members = self.standard_members() + [(extra_name, b"not signing material", None)]
                self.write_ipa(members)
                self._refresh_pinned_hash()
                with self.assertRaises(configurator.PackageError):
                    self.configure()
                self.assertFalse(self.output.exists())

        for unsafe_name in ["Payload/App.app/../escape", "Payload/App.app//double", "Payload/App.app/foreign\\file"]:
            with self.subTest(unsafe_name=unsafe_name):
                self.write_ipa(self.standard_members() + [(unsafe_name, b"x", None)])
                self._refresh_pinned_hash()
                with self.assertRaises(configurator.PackageError):
                    self.configure()
                self.assertFalse(self.output.exists())

        duplicate_members = self.standard_members() + [(configurator.APP_INFO, b"duplicate", None)]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            self.write_ipa(duplicate_members)
        self._refresh_pinned_hash()
        with self.assertRaisesRegex(configurator.PackageError, "duplicate"):
            self.configure()
        self.assertFalse(self.output.exists())

        symlink_info = zipfile.ZipInfo("Payload/App.app/public/link")
        symlink_info.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(self.input_ipa, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data, mode in self.standard_members():
                info = zipfile.ZipInfo(name)
                info.compress_type = zipfile.ZIP_DEFLATED
                if mode is not None:
                    info.external_attr = mode << 16
                archive.writestr(info, data)
            archive.writestr(symlink_info, "outside")
        self._refresh_pinned_hash()
        with self.assertRaisesRegex(configurator.PackageError, "links"):
            self.configure()
        self.assertFalse(self.output.exists())

        with patch.object(configurator, "MAX_COMPRESSION_RATIO", 2):
            self.write_ipa(self.standard_members() + [("Payload/App.app/public/zeros", b"0" * 10000, None)])
            self._refresh_pinned_hash()
            with self.assertRaisesRegex(configurator.PackageError, "compression ratio"):
                self.configure()
        self.assertFalse(self.output.exists())

    def test_existing_output_or_receipt_is_never_replaced(self):
        self.output.write_bytes(b"preserve old IPA")
        with self.assertRaisesRegex(configurator.PackageError, "already exists"):
            self.configure()
        self.assertEqual(self.output.read_bytes(), b"preserve old IPA")
        self.output.unlink()

        receipt_path = Path(str(self.output) + ".receipt.json")
        receipt_path.write_bytes(b"preserve old receipt")
        with self.assertRaisesRegex(configurator.PackageError, "already exists"):
            self.configure()
        self.assertFalse(self.output.exists())
        self.assertEqual(receipt_path.read_bytes(), b"preserve old receipt")

    def test_publish_failure_removes_the_new_pair_and_temporary_files(self):
        real_link = configurator.os.link
        call_count = 0

        def fail_on_second_link(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise OSError("synthetic publish failure")
            return real_link(*args, **kwargs)

        with patch.object(configurator.os, "link", side_effect=fail_on_second_link):
            with self.assertRaisesRegex(configurator.PackageError, "could not be published"):
                self.configure()
        self.assertFalse(self.output.exists())
        self.assertFalse(Path(str(self.output) + ".receipt.json").exists())
        self.assertEqual(list(self.root.glob(".pulse-ios-configure-*")), [])

    def _refresh_pinned_hash(self):
        self.source_digest = hashlib.sha256(self.input_ipa.read_bytes()).hexdigest()


if __name__ == "__main__":
    unittest.main()
