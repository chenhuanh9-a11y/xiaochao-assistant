"""Exact production relocation and upgrade preservation; no real Setup/PE run."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import build_qt_client as build
import test_qt_installer_projection as fixtures


class ProductionAuthorDefaultsTests(unittest.TestCase):
    write = staticmethod(fixtures.InstallerProjectionTests.write)
    apply_generated_projection = fixtures.InstallerProjectionTests.apply_generated_projection
    # Reuse the real generated-projection interpreter, not a second installer implementation.
    def setUp(self):
        fixtures.InstallerProjectionTests.setUp(self)
        self.names = ["assets/zhan10-native/difficult-map-01/floor-1.lossless.webp", "assets/zhan10-legacy-clean/difficult-map-29/floor-1.png"]
        for name in self.names:
            data = ("bundled-display:" + name).encode()
            self.write(self.stage / build.BUNDLED_AUTHOR_ROOT / name, data)
            self.resources[name] = dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        self.defaults = dict(schema="xingtu-bundled-author-v1", packageId="difficult-zhan10",
                             files={name: self.resources[name] for name in self.names})
        self.manifest = self.stage / build.BUNDLED_AUTHOR_ROOT / "inventory.json"
        self.manifest.write_text(json.dumps(self.defaults), encoding="utf-8")

    def project(self):
        self.include, self.report = build.write_production_files_include(self.stage, self.resources, self.output,
            resource_version="locked-input", bundled_author_paths=self.names)
        return self.report

    def test_explicit_production_and_client_selection_only(self):
        for instance in ("production", "client"):
            self.assertEqual(build.selected_bundled_author_paths(self.resources, ["difficult-zhan10"], instance), sorted(self.names))
        for packages, instance in (([], "production"), (["other"], "production"), (["difficult-zhan10"], "layout-preview")):
            with self.assertRaises(ValueError):
                build.selected_bundled_author_paths(self.resources, packages, instance)
        with self.assertRaises(ValueError):
            build.selected_bundled_author_paths({}, ["difficult-zhan10"], "production")

    def test_projection_maps_defaults_to_program_only_not_active_base_or_receipts(self):
        report = self.project()
        manifest = json.loads((self.stage / report["managedBaseManifest"]).read_bytes())
        for name in self.names:
            target = build.BUNDLED_AUTHOR_ROOT + name
            self.assertIn(target, report["families"]["program"])
            self.assertIn(target, report["installedFiles"])
            self.assertNotIn("_internal/" + name, report["families"]["base"])
            self.assertNotIn("_internal/" + name, {row["path"] for row in manifest["files"]})
            self.assertEqual(report["bundledAuthorDefaults"]["mappings"][name], target)
        self.assertEqual(report["bundledAuthorDefaults"]["manifestSha256"], build.sha256(self.manifest))
        self.assertFalse(any("work/" in path for path in report["installedFiles"]))

    def test_generated_upgrade_keeps_user_assets_settings_receipts_and_choice(self):
        self.project()
        destination = self.folder / "existing-with-author"
        originals = {"_internal/" + name: b"imported-author" for name in self.names}
        receipt = dict(packageId="difficult-zhan10", revision=1, archiveSha256="a" * 64,
            files=[dict(path=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest()) for name, data in originals.items()],
            requiresFiles=[])
        originals.update({"work/map-packages/difficult-zhan10.json": json.dumps(receipt).encode(),
            "work/nightmare-memory/ui-settings.json": b"custom-choice",
            "assets/custom-maps/manifest.json": b"custom-gallery"})
        for name, data in originals.items():
            self.write(destination / name, data)
        copied = self.apply_generated_projection(destination)
        for name, data in originals.items():
            self.assertEqual((destination / name).read_bytes(), data)
        for name in self.names:
            target = build.BUNDLED_AUTHOR_ROOT + name
            self.assertIn(target, copied)
            self.assertEqual((destination / target).read_bytes(), (self.stage / target).read_bytes())
        from managed_base_resources import repair_managed_base_resources
        repair_managed_base_resources(destination)
        for name, data in originals.items():
            self.assertEqual((destination / name).read_bytes(), data)

    def test_new_install_defaults_read_only_fallback_does_not_create_import_receipt(self):
        self.project()
        destination = self.folder / "fresh-author"
        self.apply_generated_projection(destination)
        from bundled_author_resources import display_path, bundled_summary
        fallback = destination / build.BUNDLED_AUTHOR_ROOT
        self.assertEqual(bundled_summary(fallback)["bundledFiles"], len(self.names))
        for name in self.names:
            original = destination / "_internal" / name
            row = self.resources[name]
            self.assertFalse(original.exists())
            self.assertEqual(display_path(original, destination / "_internal", fallback, row["sha256"], row["bytes"]), fallback / name)
        self.assertFalse((destination / "work/map-packages").exists())

    def test_hash_and_size_tampering_rejected(self):
        target = self.stage / build.BUNDLED_AUTHOR_ROOT / self.names[0]
        original = target.read_bytes()
        for data in (b"!" * len(original), original + b"!"):
            target.write_bytes(data)
            with self.assertRaisesRegex(ValueError, "verified inventory"):
                self.project()
        target.write_bytes(original)
        self.project()

    def test_manifest_mismatch_rejected(self):
        self.defaults["files"][self.names[0]] = dict(bytes=1, sha256="0" * 64)
        self.manifest.write_text(json.dumps(self.defaults), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "manifest differs"):
            self.project()

    def test_extra_default_file_and_missing_selection_rejected(self):
        with self.assertRaisesRegex(ValueError, "exact inventory"):
            build.production_file_projection(self.stage, self.resources)
        self.write(self.stage / build.BUNDLED_AUTHOR_ROOT / "extra.webp", b"undeclared")
        with self.assertRaisesRegex(ValueError, "exact inventory"):
            self.project()

    def test_duplicate_or_wrong_family_mapping_rejected(self):
        for names in (self.names * 2, ["references/build-fingerprint.txt"], ["assets/zhan10-native/../outside"]):
            with self.assertRaises(ValueError):
                build.production_file_projection(self.stage, self.resources, bundled_author_paths=names)

    def test_active_asset_copy_not_allowed_in_addition_to_relocated_default(self):
        name = self.names[0]
        self.write(self.stage / "_internal" / name, (self.stage / build.BUNDLED_AUTHOR_ROOT / name).read_bytes())
        with self.assertRaisesRegex(ValueError, "exact inventory"):
            self.project()

    def test_linked_default_is_rejected(self):
        target = self.stage / build.BUNDLED_AUTHOR_ROOT / self.names[0]
        external = self.folder / "external.webp"
        external.write_bytes(target.read_bytes())
        target.unlink()
        try:
            target.symlink_to(external)
        except OSError as error:
            self.skipTest(str(error))
        with self.assertRaisesRegex(ValueError, "links"):
            self.project()


if __name__ == "__main__":
    unittest.main()
