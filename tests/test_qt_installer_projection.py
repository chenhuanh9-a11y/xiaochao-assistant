"""Synthetic stage -> actual generated Inno projection; no compiler/install/EXE."""
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_qt_client as build


class InstallerProjectionTests(unittest.TestCase):
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

    def apply_generated_projection(self, destination):
        # Interpret the generated concrete file rows with the documented cached
        # Inno checks. This does not execute Pascal or assert native acceptance.
        existing_base = any((destination / name).exists() for name in (
            "_internal/assets", "_internal/references", "_internal/recognition",
            "_internal/difficult-native-manifest.json"))
        existing_editor = any((destination / name).exists() for name in (
            "assets/custom-maps", "_internal/assets/custom-maps"))
        decisions = dict(InstallBaseResourcePlane=not existing_base,
                         InstallEditorResourceFamily=not existing_editor)
        copied = []
        for line in self.include.read_text("utf-8-sig").splitlines():
            if not line.startswith("Source:"):
                continue
            check = re.search(r"; Check: (\w+)", line)
            if check and not decisions[check[1]]:
                continue
            relative = re.search(r'Source: "\{#StageDir\}\\([^"]+)"', line)[1].replace("\\", "/")
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.stage / relative, target)
            copied.append(relative)
        return copied

    def test_production_filename_is_formal_while_private_instances_keep_fingerprint(self):
        commit = "a" * 40
        production = build.INSTANCES["production"]
        self.assertEqual(production["versionSuffix"], "")
        self.assertEqual(build.setup_basename(production, "1.0.5", commit), "星图-1.0.5-Setup")
        private = build.INSTANCES["client"]
        self.assertEqual(build.setup_basename(private, "1.0.5-qt-test", commit),
                         "XingtuQtTest-Setup-1.0.5-qt-test-aaaaaaaaaaaa")

    def test_fresh_install_copies_complete_planes_with_one_cached_decision(self):
        destination = self.folder / "fresh"
        copied = self.apply_generated_projection(destination)
        expected = {p.relative_to(self.stage).as_posix() for p in self.stage.rglob("*") if p.is_file()}
        self.assertEqual(set(copied), expected)
        for name in self.resources:
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

    def test_partial_plane_is_preserved_without_filling_missing_coupled_manifest(self):
        destination = self.folder / "partial"
        self.write(destination / "_internal/references/one-existing.json", b"keep")
        copied = self.apply_generated_projection(destination)
        self.assertFalse(set(copied) & set(self.report["families"]["base"]))
        self.assertFalse((destination / "_internal/difficult-native-manifest.json").exists())
        self.assertTrue((destination / "_internal/assets/custom-maps/manifest.json").exists())

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
        self.assertEqual(len(families["base"]), 82)
        members = sum(families.values(), [])
        self.assertEqual(len(members), len(set(members)))
        self.assertIn("_internal/references/build-fingerprint.txt", families["program"])
        self.assertIn("_internal/difficult-native-manifest.json", families["base"])
        self.write(self.stage / "_internal/references/undeclared.json", b"stray")
        with self.assertRaisesRegex(ValueError, "exact inventory"):
            build.production_file_projection(self.stage, self.resources)


if __name__ == "__main__":
    unittest.main()
