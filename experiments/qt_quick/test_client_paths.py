"""Synthetic source/test/production layouts; never inspect an installed tree."""
import importlib.util
import errno
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app"))


class RuntimePathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Match product paths after Windows expands short temporary-directory names.
        self.container = Path(self.temp.name).resolve()
        self.root = self.container / "install-a"
        self.root.mkdir()
        self.internal = self.root / "_internal"
        self.internal.mkdir()
        self.local_app_data = self.container / "user-profile" / "LocalAppData"
        self.local_app_data.mkdir(parents=True)
        self.profile = dict(product="星图", installerInstance=dict(
            id="production", product="星图", appId="D04394A0-DACE-4FF9-874D-3DCB43EE53D1"))
        self.isolated = dict(product="XingtuQtClientTest", installerInstance=dict(
            id="client", product="XingtuQtClientTest", appId="AF19AFD9-3016-44C9-9D20-6F216C5D8C51"))

    def create_directory_symlink(self, target, link):
        try:
            os.symlink(target, link, target_is_directory=True)
        except OSError as exc:
            if (exc.errno in (errno.EACCES, errno.EPERM)
                    or getattr(exc, "winerror", None) in (5, 1314)):
                self.skipTest(f"directory symlink creation requires unavailable permission: {exc}")
            raise

    def load(self, frozen=True, profile=None, installation_root=None):
        installation_root = Path(installation_root or self.root).resolve()
        internal = installation_root / "_internal"
        internal.mkdir(parents=True, exist_ok=True)
        (internal / "installation-profile.json").write_text(json.dumps(
            self.profile if profile is None else profile), encoding="utf-8")
        spec = importlib.util.spec_from_file_location("synthetic_client_paths", Path(__file__).with_name("client_paths.py"))
        module = importlib.util.module_from_spec(spec)
        with patch.object(sys, "frozen", frozen, create=True), patch.object(sys, "_MEIPASS", str(internal), create=True), patch.object(sys, "executable", str(installation_root / "星图.exe")), patch.dict(sys.modules, {spec.name: module}):
            spec.loader.exec_module(module)
        return module

    def test_production_uses_existing_data_without_writing(self):
        state = self.root / "work/nightmare-memory"
        state.mkdir(parents=True)
        legacy = state / "ui-settings.json"
        legacy.write_text('{"unknown":42}', encoding="utf-8")
        module = self.load()
        before = sorted(p.relative_to(self.container).as_posix() for p in self.container.rglob("*"))
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.local_app_data)}):
            paths = module.runtime_paths()
            imported_root = paths.imported_resource_root
        self.assertTrue(paths.production)
        self.assertEqual(paths.settings_path, legacy)
        self.assertEqual(paths.custom_galleries_path, state / "custom-galleries.json")
        self.assertEqual(paths.update_state_path, state / "update-state.json")
        self.assertEqual(paths.tutorial_state_path, state / "tutorial-state.json")
        self.assertEqual(paths.memory_root("difficult"), self.root / "work/difficult-memory")
        self.assertEqual(paths.resource_root, self.internal)
        self.assertEqual(paths.program_root, self.internal)
        self.assertEqual(imported_root, self.local_app_data / "Xingtu" / "resources")
        self.assertFalse(imported_root.exists())
        self.assertEqual(legacy.read_text(encoding="utf-8"), '{"unknown":42}')
        self.assertEqual(before, sorted(p.relative_to(self.container).as_posix() for p in self.container.rglob("*")))
        with self.assertRaises(ValueError):
            module.runtime_paths(self.root / "arbitrary")

    def test_production_imported_root_is_independent_of_program_installation(self):
        imported_root = self.local_app_data / "Xingtu" / "resources"
        imported_root.mkdir(parents=True)
        resource_bytes = b"existing imported resource bytes"
        resource_file = imported_root / "reference.dat"
        resource_file.write_bytes(resource_bytes)
        module_a = self.load(installation_root=self.root)
        install_b = self.container / "install-b"
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.local_app_data)}):
            paths_a = module_a.runtime_paths()
            module_b = self.load(installation_root=install_b)
            paths_b = module_b.runtime_paths()
            imported_a = paths_a.imported_resource_root
            imported_b = paths_b.imported_resource_root
        self.assertNotEqual(paths_a.program_root, paths_b.program_root)
        expected = self.local_app_data / "Xingtu" / "resources"
        self.assertEqual(imported_a, expected)
        self.assertEqual(imported_b, expected)
        self.assertEqual(resource_file.read_bytes(), resource_bytes)

    def test_production_requires_valid_local_app_data(self):
        module = self.load()
        with patch.dict(os.environ):
            os.environ.pop("LOCALAPPDATA", None)
            with self.assertRaisesRegex(ValueError, "LOCALAPPDATA"):
                module.runtime_paths()
        for value in ("", "relative-profile", str(self.container / "missing")):
            with self.subTest(value=value), patch.dict(os.environ, {"LOCALAPPDATA": value}):
                with self.assertRaisesRegex(ValueError, "LOCALAPPDATA"):
                    module.runtime_paths()

    def test_production_rejects_linked_local_app_data_parent(self):
        target = self.container / "real-profile"
        target.mkdir()
        (target / "LocalAppData").mkdir()
        linked_parent = self.container / "profile-link"
        self.create_directory_symlink(target, linked_parent)
        module = self.load()
        with patch.dict(os.environ, {"LOCALAPPDATA": str(linked_parent / "LocalAppData")}):
            with self.assertRaisesRegex(ValueError, "links or reparse points"):
                module.runtime_paths()

    def test_production_rejects_imported_root_file(self):
        module = self.load()
        imported_parent = self.local_app_data / "Xingtu"
        imported_parent.mkdir()
        imported_root = imported_parent / "resources"
        imported_root.write_bytes(b"not a directory")
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.local_app_data)}):
            with self.assertRaisesRegex(ValueError, "Imported resource root must be a directory"):
                module.runtime_paths()

    def test_production_rejects_linked_imported_root(self):
        module = self.load()
        imported_parent = self.local_app_data / "Xingtu"
        imported_parent.mkdir()
        target = self.container / "external-resources"
        target.mkdir()
        self.create_directory_symlink(target, imported_parent / "resources")
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.local_app_data)}):
            with self.assertRaisesRegex(ValueError, "links or reparse points"):
                module.runtime_paths()

    def test_production_rejects_file_in_imported_root_ancestry(self):
        module = self.load()
        (self.local_app_data / "Xingtu").write_text("not a directory", encoding="utf-8")
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.local_app_data)}):
            with self.assertRaisesRegex(ValueError, "parent must be a directory"):
                module.runtime_paths()

    def test_source_profile_cannot_select_production(self):
        module = self.load(frozen=False)
        self.assertFalse(module.PRODUCTION)
        with self.assertRaises(ValueError):
            module.runtime_paths(self.root)

    def test_wrong_or_adjacent_profile_cannot_select_production(self):
        for profile in ({}, dict(self.profile, installerInstance=dict(self.profile["installerInstance"], appId="other"))):
            with self.assertRaisesRegex(ValueError, "Invalid baked"):
                self.load(profile=profile)
        (self.root / "installation-profile.json").write_text(json.dumps(self.profile), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Invalid baked"):
            self.load(profile={})

    def test_marked_test_layout_keeps_old_paths(self):
        module = self.load(profile=self.isolated)
        sandbox = module.DEFAULT_SANDBOX
        sandbox.mkdir(parents=True)
        (sandbox / ".qt-lab-owner").write_text("qt-quick-isolated-v1", encoding="utf-8")
        paths = module.runtime_paths()
        self.assertEqual(paths.settings_path, sandbox / "ui-settings.json")
        self.assertEqual(paths.update_state_path, sandbox / "qt-update-state.json")
        self.assertEqual(paths.tutorial_state_path, sandbox / "qt-tutorial-state.json")
        self.assertEqual(paths.resource_root, sandbox / "_internal")
        self.assertEqual(paths.program_root, self.internal)
        self.assertEqual(paths.imported_resource_root, sandbox / "work" / "imported-resources")
        self.assertFalse(paths.imported_resource_root.exists())

    def test_editor_path_lookup_defers_validation_to_worker(self):
        module = self.load()
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.local_app_data)}):
            self.assertEqual(module.runtime_paths().editor_base_root, self.internal / "qt-editor-base")
        outer = self.root / "assets/custom-maps"
        outer.mkdir(parents=True)
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.local_app_data)}):
            self.assertEqual(module.runtime_paths().editor_base_root, outer)

    def test_existing_test_sandbox_can_read_program_editor_default_without_reseeding(self):
        module = self.load(profile=self.isolated)
        sandbox = module.DEFAULT_SANDBOX
        sandbox.mkdir(parents=True)
        (sandbox/'.qt-lab-owner').write_text('qt-quick-isolated-v1', encoding='utf8')
        settings = sandbox/'ui-settings.json'
        settings.write_bytes(b'{"selected":"custom:mine"}')
        baseline = self.internal/'qt-editor-base'
        baseline.mkdir()
        paths = module.runtime_paths()
        self.assertEqual(paths.resource_root, sandbox/'_internal')
        self.assertEqual(paths.program_root, self.internal)
        self.assertEqual(paths.imported_resource_root, sandbox/'work'/'imported-resources')
        self.assertIn(baseline, paths.editor_base_candidates)
        self.assertEqual(paths.editor_base_root, baseline)
        self.assertEqual(settings.read_bytes(), b'{"selected":"custom:mine"}')


if __name__ == "__main__":
    unittest.main()
