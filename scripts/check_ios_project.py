"""Check source integrity of the iOS project; this never claims an Xcode build."""
from __future__ import annotations

import json
from pathlib import Path
import plistlib
import re
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
IOS = ROOT / "apps/mobile/ios"


class OpenStepReader:
    """Read the dictionaries, lists and strings used in an Xcode pbxproj."""

    def __init__(self, text: str):
        pattern = r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|[{}()=;,]|[^\s{}()=;,]+'
        self.tokens = [m.group() for m in re.finditer(pattern, text, re.S)
                       if not m.group().startswith(("/*", "//"))]
        self.position = 0

    def take(self, expected: str | None = None) -> str:
        token = self.tokens[self.position]
        self.position += 1
        if expected is not None and token != expected:
            raise ValueError(f"Expected {expected!r}, received {token!r}")
        return token

    def read(self):
        token = self.take()
        if token == "{":
            value = {}
            while self.tokens[self.position] != "}":
                key = self.read()
                if not isinstance(key, str) or key in value:
                    raise ValueError("Invalid or duplicate dictionary key")
                self.take("=")
                value[key] = self.read()
                self.take(";")
            self.take("}")
            return value
        if token == "(":
            value = []
            while self.tokens[self.position] != ")":
                value.append(self.read())
                if self.tokens[self.position] != ")":
                    self.take(",")
            self.take(")")
            return value
        if token in "}=;,":
            raise ValueError(f"Unexpected token {token!r}")
        return json.loads(token) if token.startswith('"') else token


def check_shared_scheme(path: Path, expected: dict[str, tuple[str, str]]) -> None:
    """Resolve the checked-in scheme against the project's actual target IDs."""
    scheme = ET.parse(path).getroot()
    assert scheme.tag == "Scheme" and scheme.get("version") == "1.3"
    assert not scheme.findall(".//PreActions") and not scheme.findall(".//PostActions")
    build = scheme.find("BuildAction")
    assert build is not None and build.get("buildImplicitDependencies") == "YES"
    entries = build.findall("./BuildActionEntries/BuildActionEntry")
    assert len(entries) == len(expected) == 2
    seen = set()

    def check_reference(reference, name: str) -> None:
        assert reference is not None, "Missing shared scheme reference"
        identifier, product = expected[name]
        assert reference.attrib == {
            "BuildableIdentifier": "primary", "BlueprintIdentifier": identifier,
            "BuildableName": product, "BlueprintName": name,
            "ReferencedContainer": "container:App.xcodeproj",
        }, "Shared scheme reference does not match the Xcode target"

    for entry in entries:
        references = entry.findall("BuildableReference")
        assert len(references) == 1
        reference = references[0]
        name = reference.get("BlueprintName")
        assert name in expected and name not in seen
        seen.add(name)
        check_reference(reference, name)
        for action in ("buildForRunning", "buildForProfiling", "buildForArchiving", "buildForAnalyzing"):
            assert entry.get(action) == "YES", "Scheme omits a product from a build action"
    assert seen == set(expected)
    for action, configuration in (
        ("TestAction", "Debug"), ("LaunchAction", "Debug"),
        ("ProfileAction", "Release"), ("AnalyzeAction", "Debug"), ("ArchiveAction", "Release"),
    ):
        elements = scheme.findall(action)
        assert len(elements) == 1 and elements[0].get("buildConfiguration") == configuration
        if action in {"LaunchAction", "ProfileAction"}:
            check_reference(elements[0].find("./BuildableProductRunnable/BuildableReference"), "App")


def check_startup_ci_scheme(path: Path, expected: dict[str, tuple[str, str]]) -> None:
    """The CI scheme tests all products but never archives or skips its UI test."""
    scheme = ET.parse(path).getroot()
    assert scheme.tag == "Scheme" and scheme.get("version") == "1.3"
    assert not scheme.findall(".//PreActions") and not scheme.findall(".//PostActions")
    build = scheme.find("BuildAction")
    assert build is not None and build.get("buildImplicitDependencies") == "YES"
    entries = build.findall("./BuildActionEntries/BuildActionEntry")
    assert len(entries) == len(expected) == 3
    seen = set()
    for entry in entries:
        references = entry.findall("BuildableReference")
        assert len(references) == 1
        ref = references[0]
        name = ref.get("BlueprintName")
        assert name in expected and name not in seen
        identifier, product = expected[name]
        assert ref.attrib == {
            "BuildableIdentifier": "primary", "BlueprintIdentifier": identifier,
            "BuildableName": product, "BlueprintName": name,
            "ReferencedContainer": "container:App.xcodeproj",
        }
        assert entry.get("buildForTesting") == "YES"
        assert all(entry.get(action) == "NO" for action in (
            "buildForRunning", "buildForProfiling", "buildForArchiving", "buildForAnalyzing"))
        seen.add(name)
    assert seen == set(expected)
    actions = scheme.findall("TestAction")
    assert len(actions) == 1 and actions[0].get("buildConfiguration") == "Debug"
    testables = actions[0].findall("./Testables/TestableReference")
    assert len(testables) == 1
    testable = testables[0]
    assert testable.get("skipped") == "NO" and testable.get("parallelizable") == "NO"
    references = testable.findall("BuildableReference")
    assert len(references) == 1
    identifier, product = expected["PulseStartupUITests"]
    assert references[0].attrib == {
        "BuildableIdentifier": "primary", "BlueprintIdentifier": identifier,
        "BuildableName": product, "BlueprintName": "PulseStartupUITests",
        "ReferencedContainer": "container:App.xcodeproj",
    }


def check() -> dict:
    reader = OpenStepReader((IOS / "App/App.xcodeproj/project.pbxproj").read_text())
    project = reader.read()
    assert reader.position == len(reader.tokens), "Trailing Xcode project data"
    objects = project["objects"]
    assert all(re.fullmatch(r"[A-F0-9]{24}", key) for key in objects)

    def references(value):
        if isinstance(value, dict):
            for item in value.values():
                references(item)
        elif isinstance(value, list):
            for item in value:
                references(item)
        elif re.fullmatch(r"[A-F0-9]{24}", value):
            assert value in objects, "Unresolved project reference"

    references(project)
    targets = {item["name"]: item for item in objects.values() if item["isa"] == "PBXNativeTarget"}
    assert set(targets) == {"App", "PulseWidget", "PulseStartupUITests"}
    assert targets["App"]["productType"] == "com.apple.product-type.application"
    assert targets["PulseWidget"]["productType"] == "com.apple.product-type.app-extension"
    assert targets["PulseStartupUITests"]["productType"] == "com.apple.product-type.bundle.ui-testing"
    paths = {}

    def walk(identifier: str, parent: Path):
        item = objects[identifier]
        if item.get("sourceTree") == "BUILT_PRODUCTS_DIR":
            return
        base = IOS / "App" if item.get("sourceTree") == "SOURCE_ROOT" else parent
        path = (base / item.get("path", "")).resolve()
        paths[identifier] = path
        if "children" in item:
            for child in item["children"]:
                walk(child, path)
        else:
            generated = {IOS / "App/App/public", IOS / "App/App/config.xml",
                         IOS / "App/App/capacitor.config.json"}
            assert path.exists() or path in generated, "Missing referenced project source file"

    walk(objects[project["rootObject"]]["mainGroup"], IOS / "App")
    cache = (IOS / "App/AppGroup/PulseWidgetCache.swift").resolve()
    sources = {}
    for name, target in targets.items():
        phases = [objects[identifier] for identifier in target["buildPhases"]]
        source = next(phase for phase in phases if phase["isa"] == "PBXSourcesBuildPhase")
        sources[name] = {paths[objects[identifier]["fileRef"]] for identifier in source["files"]}
        if name in {"App", "PulseWidget"}:
            assert cache in sources[name]
        framework = next(phase for phase in phases if phase["isa"] == "PBXFrameworksBuildPhase")
        products = {objects[objects[identifier]["productRef"]]["productName"] for identifier in framework["files"]}
        assert products == ({"PulseCore", "CapApp-SPM"} if name == "App"
                            else {"PulseCore"} if name == "PulseWidget" else set())
        configs = objects[target["buildConfigurationList"]]["buildConfigurations"]
        for identifier in configs:
            config = objects[identifier]
            settings = config["buildSettings"]
            assert settings["IPHONEOS_DEPLOYMENT_TARGET"] == "17.4"
            if name == "PulseStartupUITests":
                assert "baseConfigurationReference" not in config
                assert settings["TEST_TARGET_NAME"] == "App"
                assert settings["GENERATE_INFOPLIST_FILE"] == "YES"
                assert settings["SKIP_INSTALL"] == "YES"
                assert settings["SUPPORTED_PLATFORMS"] == "iphonesimulator"
                assert settings["PRODUCT_BUNDLE_IDENTIFIER"] == "com.lucas.pulse.PulseStartupUITests"
                assert settings["MARKETING_VERSION"] == "0.1.1" and settings["CURRENT_PROJECT_VERSION"] == "2"
                assert "CODE_SIGN_ENTITLEMENTS" not in settings and "DEVELOPMENT_TEAM" not in settings
                continue
            assert paths[config["baseConfigurationReference"]] == (IOS / "Pulse.xcconfig").resolve()
            for key in ["INFOPLIST_FILE", "CODE_SIGN_ENTITLEMENTS"]:
                assert (IOS / "App" / settings[key]).is_file()
            if name == "PulseWidget":
                assert settings["APPLICATION_EXTENSION_API_ONLY"] == "YES"
    native = {(IOS / "App/App" / filename).resolve() for filename in
              ["AppDelegate.swift", "SceneDelegate.swift", "PulseBridgeViewController.swift"]}
    native |= {path.resolve() for path in (IOS / "App/App/NativeBridge").glob("*.swift")}
    assert sources["App"] == native | {cache}
    assert sources["PulseWidget"] == {(IOS / "App/PulseWidget/PulseWidget.swift").resolve(), cache}
    assert sources["PulseStartupUITests"] == {(ROOT / "tests/ios/PulseStartupUITests.swift").resolve()}
    assert not targets["PulseWidget"]["dependencies"]
    assert {objects[key]["target"] for key in targets["App"]["dependencies"]} == {
        next(key for key, value in objects.items() if value is targets["PulseWidget"])}
    assert {objects[key]["target"] for key in targets["PulseStartupUITests"]["dependencies"]} == {
        next(key for key, value in objects.items() if value is targets["App"])}
    embed = next(objects[key] for key in targets["App"]["buildPhases"]
                 if objects[key]["isa"] == "PBXCopyFilesBuildPhase")
    assert embed["dstSubfolderSpec"] == "13" and len(embed["files"]) == 1
    embedded = objects[embed["files"][0]]
    assert embedded["fileRef"] == targets["PulseWidget"]["productReference"]
    assert "CodeSignOnCopy" in embedded["settings"]["ATTRIBUTES"]
    assert any(objects[key]["target"] == next(key for key, value in objects.items()
               if value is targets["PulseWidget"]) for key in targets["App"]["dependencies"])
    for folder in ["App/App", "App/PulseWidget"]:
        for path in (IOS / folder).glob("*"):
            if path.suffix in {".plist", ".entitlements", ".xcprivacy"}:
                plistlib.loads(path.read_bytes())
    for path in (IOS / "App/App/Base.lproj").glob("*.storyboard"):
        ET.parse(path)
    app_ent = plistlib.loads((IOS / "App/App/App.entitlements").read_bytes())
    widget_ent = plistlib.loads((IOS / "App/PulseWidget/Widget.entitlements").read_bytes())
    assert app_ent["com.apple.security.application-groups"] == widget_ent["com.apple.security.application-groups"]
    assert "keychain-access-groups" not in widget_ent
    info = plistlib.loads((IOS / "App/App/Info.plist").read_bytes())
    assert "com.lucas.pulse.widget.refresh" in info["BGTaskSchedulerPermittedIdentifiers"]
    assert "fetch" in info["UIBackgroundModes"]
    spm = (IOS / "App/CapApp-SPM/Package.swift").read_text()
    assert 'exact: "8.5.3"' in spm
    expected = {
        name: (next(key for key, item in objects.items() if item is target),
               objects[target["productReference"]]["path"])
        for name, target in targets.items()
    }
    check_shared_scheme(IOS / "App/App.xcodeproj/xcshareddata/xcschemes/App.xcscheme",
                        {name: expected[name] for name in ("App", "PulseWidget")})
    check_startup_ci_scheme(IOS / "App/App.xcodeproj/xcshareddata/xcschemes/PulseStartupCI.xcscheme", expected)
    return {"objects": len(objects), "targets": sorted(targets),
            "sources": {name: len(value) for name, value in sources.items()},
            "shared_scheme": "App",
            "startup_ci_scheme": "PulseStartupCI",
            "xcode_compiled": False, "signed": False}


if __name__ == "__main__":
    print(json.dumps(check(), ensure_ascii=False))
