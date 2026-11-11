"""Synthetic stage -> actual generated Inno projection; no compiler/install/EXE."""
from pathlib import Path
import json
import re
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_qt_client as build


class InstallerProjectionTests(unittest.TestCase):
    def test_setup_numeric_and_display_versions_remain_separate(self):
        # Source contract only: this does not compile Inno or inspect a PE.
        source = (ROOT / "tools/qt-client.iss").read_text(encoding="utf-8-sig")
        fields = dict(re.findall(r"^(\w+)=([^\r\n]+)$", source, re.MULTILINE))
        version = json.loads((ROOT / "version.json").read_text(encoding="utf-8"))
        for name, instance in build.INSTANCES.items():
            with self.subTest(instance=name):
                numeric = version["numericVersion"]
                display = version["displayVersion"] + instance["versionSuffix"]
                def resolve(field):
                    return fields[field].replace("{#NumericVersion}", numeric).replace(
                        "{#DisplayVersion}", display)
                for field in ("VersionInfoVersion", "VersionInfoProductVersion"):
                    self.assertEqual(resolve(field), numeric)
                    self.assertRegex(resolve(field), r"^[0-9]+(?:\.[0-9]+){3}$")
                self.assertEqual(resolve("VersionInfoProductTextVersion"), display)
                self.assertEqual(resolve("AppVersion"), display)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.stage, self.output = self.folder / "stage", self.folder / "output"
        self.stage.mkdir()
        self.output.mkdir()
        # Synthetic author-upgrade closure has 82 overlapping resource paths.
        self.resources = {name: {} for name in [
            "difficult-native-manifest.json",
            "references/author-packs/nightingale/catalog.json",
            "references/author-packs/nightingale/display-bindings.json",
            *[f"references/difficult-native/layer-{index}.npz" for index in range(78)],
            "assets/custom-maps/manifest.json", "assets/custom-maps/map/base.png",
            "references/build-fingerprint.txt"]}
        self.resources.update({"recognition/native/index.json": {}})
        for name in self.resources:
            self.write(self.stage / "_internal" / name, b"bundled:" + name.encode())
        for name in ("第五人格小抄助手.exe", "version.json", "installation-profile.json",
                     "_internal/version.json", "_internal/installation-profile.json", "_internal/app/service.py"):
            self.write(self.stage / name, b"new-program")
        self.include, self.report = build.write_production_files_include(self.stage, self.resources, self.output)

    @staticmethod
    def write(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def test_retired_candidate_cannot_be_reclassified_as_program(self):
        self.write(self.stage / '_internal/managed-base-resources/manifest.json', b'old candidate')
        with self.assertRaisesRegex(ValueError, 'Resource projection differs from exact inventory'):
            build.write_production_files_include(self.stage, self.resources, self.output)

    def apply_generated_projection(self, destination):
        # Interpret the generated concrete file rows with the documented cached
        # Inno checks. This does not execute Pascal or assert native acceptance.
        existing_editor = any((destination / name).exists() for name in (
            "assets/custom-maps", "_internal/assets/custom-maps"))
        decisions = dict(InstallEditorResourceFamily=not existing_editor)
        copied = []
        for line in self.include.read_text("utf-8-sig").splitlines():
            if not line.startswith("Source:"):
                continue
            check = re.search(r"; Check: (\w+)", line)
            if check and not decisions[check[1]]:
                continue
            relative = re.search(r'Source: "\{#StageDir\}\\([^"]+)"', line)[1].replace("\\", "/")
            parent = re.search(r'DestDir: "\{app\}([^"]*)"', line)[1].replace("\\", "/").lstrip("/")
            target = destination / parent / Path(relative).name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.stage / relative, target)
            copied.append(target.relative_to(destination).as_posix())
        return copied

    def test_production_filename_is_formal_while_private_instances_keep_fingerprint(self):
        commit = "a" * 40
        production = build.INSTANCES["production"]
        self.assertEqual(production["versionSuffix"], "")
        self.assertEqual(build.setup_basename(production, "1.0.5", commit), "星图-1.0.5-Setup")
        private = build.INSTANCES["client"]
        self.assertEqual(build.setup_basename(private, "1.0.5-qt-test", commit),
                         "XingtuQtTest-Setup-1.0.5-qt-test-aaaaaaaaaaaa")

    def test_fresh_install_has_no_default_base_or_candidate_repair_projection(self):
        destination = self.folder / "fresh"
        copied = self.apply_generated_projection(destination)
        expected = set(self.report["installedFiles"])
        self.assertEqual(set(copied), expected)
        self.assertIsNone(self.report["managedBaseManifest"])
        self.assertFalse(any("managed-base-resources" in name for name in copied))
        self.assertFalse(any(name in copied for name in self.report["families"]["base"]))
        for name in self.resources:
            if "/author-packs/" in name:
                self.assertFalse((destination / "_internal" / name).exists())
                continue
            if name.startswith(("recognition/", "references/author-packs/", "references/difficult-native/")) or name == "difficult-native-manifest.json":
                self.assertFalse((destination / "_internal" / name).exists())
                continue
            self.assertEqual((destination / "_internal" / name).read_bytes(),
                             (self.stage / "_internal" / name).read_bytes())

    def test_existing_author_closure_receipts_and_outer_editor_are_preserved(self):
        destination = self.folder / "existing"
        original = {}
        for name in self.report["families"]["base"]:
            original[name] = b"imported-author:" + name.encode()
        original.update({"work/map-packages/nightmare-nightingale.json": b"receipt",
            "work/map-packages/difficult-zhan10.json": b"receipt-2",
            "_internal/assets/authors/nightingale/route.png": b"author-image",
            "assets/custom-maps/manifest.json": b"user-editor",
            "work/nightmare-memory/ui-settings.json": b"user-settings"})
        for name, data in original.items():
            self.write(destination / name, data)
        copied = self.apply_generated_projection(destination)
        self.assertFalse(set(copied) & set(self.report["families"]["base"]))
        self.assertFalse(set(copied) & set(self.report["families"]["editor"]))
        for name, data in original.items():
            self.assertEqual((destination / name).read_bytes(), data)
        self.assertEqual((destination / "_internal/references/build-fingerprint.txt").read_bytes(),
                         (self.stage / "_internal/references/build-fingerprint.txt").read_bytes())
        self.assertEqual((destination / "version.json").read_bytes(), b"new-program")

    def test_partial_or_missing_base_is_not_filled_by_program_upgrade(self):
        destination = self.folder / "partial"
        self.write(destination / "_internal/references/one-existing.json", b"keep")
        copied = self.apply_generated_projection(destination)
        self.assertFalse(set(copied) & set(self.report["families"]["base"]))
        self.assertFalse((destination / "_internal/difficult-native-manifest.json").exists())
        self.assertTrue((destination / "_internal/assets/custom-maps/manifest.json").exists())
        self.assertEqual((destination / "_internal/references/one-existing.json").read_bytes(), b"keep")
        self.assertFalse((destination / "_internal/difficult-native-manifest.json").exists())

    def test_production_fingerprint_uses_new_qt_commit_and_records_replaced_hash(self):
        resources = {"references/build-fingerprint.txt": dict(bytes=9, sha256="a" * 64)}
        report = build.stage_production_fingerprint(self.folder / "resource-stage", resources,
                                                    "b12c34d5" + "e" * 32)
        data = (self.folder / "resource-stage/references/build-fingerprint.txt").read_bytes()
        self.assertEqual(data, b"B12C34D5\n")
        self.assertEqual(report["fingerprintScheme"], "source-commit-prefix")
        self.assertEqual(report["replacedBaselineFingerprintSha256"], "a" * 64)
        self.assertEqual(resources["references/build-fingerprint.txt"]["sha256"], build.hashlib.sha256(data).hexdigest())

    def test_inventory_exactness_and_identity_separation_gate(self):
        families = self.report["families"]
        self.assertEqual(len(families["base"]), 80)
        members = sum(families.values(), [])
        self.assertEqual(len(members), len(set(members)))
        self.assertIn("_internal/references/build-fingerprint.txt", families["program"])
        self.assertIn("_internal/difficult-native-manifest.json", families["base"])
        self.write(self.stage / "_internal/references/undeclared.json", b"stray")
        with self.assertRaisesRegex(ValueError, "exact inventory"):
            build.production_file_projection(self.stage, self.resources)

    def test_optional_author_sidecars_are_reported_but_never_installed_as_base_or_program(self):
        import json
        sidecars = {"_internal/references/author-packs/nightingale/" + name
                    for name in ("catalog.json", "display-bindings.json")}
        self.assertEqual(set(self.report["families"]["optional-author"]), sidecars)
        self.assertIsNone(self.report["managedBaseManifest"])
        self.assertEqual(set(self.report["omittedOptionalAuthorFiles"]), sidecars)
        self.assertTrue(all(not any(path.endswith(name) for name in sidecars)
                            for path in self.report["installedFiles"]))
        destination = self.folder / "existing-optional-author"
        for name in sidecars:
            self.write(destination / name, b"local author bytes")
        self.apply_generated_projection(destination)
        for name in sidecars:
            self.assertEqual((destination / name).read_bytes(), b"local author bytes")
        # Classification is exact; unfamiliar authors or extra sidecars do not
        # gain an exemption from base integrity or declared inventory checks.
        for name in ("_internal/references/author-packs/unknown/catalog.json",
                     "_internal/references/author-packs/nightingale/other.json",
                     "_internal/references/nightmare-canonical-coordinate-pack.json"):
            self.assertEqual(build.resource_file_family(name), "base")


if __name__ == "__main__":
    unittest.main()
