"""Synthetic source/test/production layouts; never inspect an installed tree."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


class RuntimePathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.internal = self.root / "_internal"
        self.internal.mkdir()
        self.profile = dict(product="第五人格小抄助手", installerInstance=dict(
            id="production", product="第五人格小抄助手", appId="D04394A0-DACE-4FF9-874D-3DCB43EE53D1"))

    def load(self, frozen=True, profile=None):
        (self.internal / "installation-profile.json").write_text(json.dumps(
            self.profile if profile is None else profile), encoding="utf-8")
        spec = importlib.util.spec_from_file_location("synthetic_client_paths", Path(__file__).with_name("client_paths.py"))
        module = importlib.util.module_from_spec(spec)
        with patch.object(sys, "frozen", frozen, create=True), patch.object(sys, "_MEIPASS", str(self.internal), create=True), patch.object(sys, "executable", str(self.root / "第五人格小抄助手.exe")), patch.dict(sys.modules, {spec.name: module}):
            spec.loader.exec_module(module)
        return module

    def test_production_uses_existing_data_without_writing(self):
        state = self.root / "work/nightmare-memory"
        state.mkdir(parents=True)
        legacy = state / "ui-settings.json"
        legacy.write_text('{"unknown":42}', encoding="utf-8")
        module = self.load()
        before = sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*"))
        paths = module.runtime_paths()
        self.assertTrue(paths.production)
        self.assertEqual(paths.settings_path, legacy)
        self.assertEqual(paths.custom_galleries_path, state / "custom-galleries.json")
        self.assertEqual(paths.update_state_path, state / "update-state.json")
        self.assertEqual(paths.memory_root("difficult"), self.root / "work/difficult-memory")
        self.assertEqual(paths.resource_root, self.internal)
        self.assertEqual(legacy.read_text(encoding="utf-8"), '{"unknown":42}')
        self.assertEqual(before, sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*")))
        with self.assertRaises(ValueError):
            module.runtime_paths(self.root / "arbitrary")

    def test_source_profile_cannot_select_production(self):
        module = self.load(frozen=False)
        self.assertFalse(module.PRODUCTION)
        with self.assertRaises(ValueError):
            module.runtime_paths(self.root)

    def test_wrong_or_adjacent_profile_cannot_select_production(self):
        for profile in ({}, dict(self.profile, installerInstance=dict(self.profile["installerInstance"], appId="other"))):
            module = self.load(profile=profile)
            self.assertFalse(module.PRODUCTION)
            with self.assertRaises(ValueError):
                module.runtime_paths(self.root)
        (self.root / "installation-profile.json").write_text(json.dumps(self.profile), encoding="utf-8")
        self.assertFalse(self.load(profile={}).PRODUCTION)

    def test_marked_test_layout_keeps_old_paths(self):
        module = self.load(profile={})
        sandbox = module.DEFAULT_SANDBOX
        sandbox.mkdir(parents=True)
        (sandbox / ".qt-lab-owner").write_text("qt-quick-isolated-v1", encoding="utf-8")
        paths = module.runtime_paths()
        self.assertEqual(paths.settings_path, sandbox / "ui-settings.json")
        self.assertEqual(paths.update_state_path, sandbox / "qt-update-state.json")
        self.assertEqual(paths.resource_root, sandbox / "_internal")

    def test_existing_outer_editor_base_has_priority_even_if_invalid(self):
        module = self.load()
        self.assertEqual(module.runtime_paths().editor_base_root, self.internal / "assets/custom-maps")
        outer = self.root / "assets/custom-maps"
        outer.mkdir(parents=True)
        self.assertEqual(module.runtime_paths().editor_base_root, outer)


if __name__ == "__main__":
    unittest.main()
