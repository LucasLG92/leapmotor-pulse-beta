"""The shared build scheme must keep both actual Xcode products reachable."""
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from scripts.check_ios_project import IOS, OpenStepReader, check, check_shared_scheme


@pytest.fixture
def project_scheme(tmp_path):
    reader = OpenStepReader((IOS / "App/App.xcodeproj/project.pbxproj").read_text())
    objects = reader.read()["objects"]
    targets = {item["name"]: (key, objects[item["productReference"]]["path"])
               for key, item in objects.items() if item["isa"] == "PBXNativeTarget"}
    tree = ET.parse(IOS / "App/App.xcodeproj/xcshareddata/xcschemes/App.xcscheme")
    path = tmp_path / "App.xcscheme"
    return tree, path, targets


def test_real_project_and_shared_scheme_remain_consistent():
    result = check()
    assert result["shared_scheme"] == "App"
    assert result["xcode_compiled"] is False and result["signed"] is False


@pytest.mark.parametrize("mutation", [
    "target_id", "product", "container", "duplicate", "missing_widget", "skip_release",
    "configuration", "launch_target", "pre_action",
])
def test_shared_scheme_rejects_unbuildable_or_unexpected_changes(project_scheme, mutation):
    tree, path, targets = project_scheme
    root = tree.getroot()
    entries = root.find("./BuildAction/BuildActionEntries")
    reference = entries[0].find("BuildableReference")
    if mutation == "target_id":
        reference.set("BlueprintIdentifier", "0" * 24)
    elif mutation == "product":
        reference.set("BuildableName", "Other.app")
    elif mutation == "container":
        reference.set("ReferencedContainer", "container:Other.xcodeproj")
    elif mutation == "duplicate":
        entries[1].remove(entries[1][0])
        entries[1].append(ET.fromstring(ET.tostring(reference)))
    elif mutation == "missing_widget":
        entries.remove(entries[1])
    elif mutation == "skip_release":
        entries[1].set("buildForArchiving", "NO")
    elif mutation == "configuration":
        root.find("ProfileAction").set("buildConfiguration", "Debug")
    elif mutation == "launch_target":
        launch = root.find("./LaunchAction/BuildableProductRunnable/BuildableReference")
        launch.set("BlueprintName", "PulseWidget")
    elif mutation == "pre_action":
        ET.SubElement(root.find("BuildAction"), "PreActions")
    tree.write(path)
    with pytest.raises(AssertionError):
        check_shared_scheme(path, targets)
