import json
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from probe_qtifw_lifecycle import PACKAGE, Probe, prepare_packages, snapshot


class QtIfwProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_default_policy_preserves_unknown_data_and_has_no_product_identity(self):
        folder = prepare_packages(self.root / "package", "1.0.0")
        cfg = ET.parse(folder / "config/config.xml").getroot()
        self.assertEqual(cfg.findtext("RemoveTargetDir"), "false")
        self.assertEqual(cfg.findtext("AllowRepositoriesForOfflineInstaller"), "false")
        self.assertIsNone(cfg.find("RunProgram"))
        self.assertIsNone(cfg.find("RemoteRepositories"))
        self.assertIn("Synthetic", cfg.findtext("Name"))
        paths = set(snapshot(folder / "packages" / PACKAGE / "data"))
        self.assertEqual(paths, {"program/version.txt", "program/synthetic.txt", "program/obsolete-v1.txt"})

    def test_fixture_update_has_real_removed_file_and_stable_component(self):
        for version in ("1.0.0", "2.0.0"):
            folder = prepare_packages(self.root / version, version)
            meta = ET.parse(folder / "packages" / PACKAGE / "meta/package.xml").getroot()
            self.assertEqual(meta.findtext("Version"), version)
        self.assertFalse((self.root / "2.0.0/packages" / PACKAGE / "data/program/obsolete-v1.txt").exists())

    def test_no_existing_output_reuse(self):
        folder = self.root / "package"
        folder.mkdir()
        (folder / "unknown").write_bytes(b"preserve")
        before = snapshot(folder)
        with self.assertRaises(ValueError):
            prepare_packages(folder, "1.0.0")
        self.assertEqual(snapshot(folder), before)
        with self.assertRaises(ValueError):
            Probe(self.root, folder)
        self.assertEqual(snapshot(folder), before)

    def test_unknown_fault_or_version_cannot_inject_script(self):
        for version, fault in (("1.2.3", None), ("1.0.0", "anything")):
            target = self.root / "bad"
            with self.assertRaises(ValueError):
                prepare_packages(target, version, fault=fault)
            self.assertFalse(target.exists())

    def test_old_inno_rejection_precedes_component_operations(self):
        folder = prepare_packages(self.root / "fixture", "1.0.0")
        script = (folder / "packages" / PACKAGE / "meta/installscript.js").read_text()
        self.assertLess(script.index('throw new Error("legacy-inno'), script.index("component.createOperations()"))
        self.assertNotIn("unins000.exe\"", script[script.index("component.createOperations()") :])
        self.assertNotIn("installer.execute", script)

    def test_fault_is_a_real_standard_operation_not_preparation_throw(self):
        folder = prepare_packages(self.root / "fixture", "3.0.0", fault="fail")
        script = (folder / "packages" / PACKAGE / "meta/installscript.js").read_text()
        self.assertLess(script.index("component.createOperations()"), script.index('component.addOperation("Execute"'))
        self.assertIn('"{0}"', script)
        self.assertIn("sys.exit(47)", script)

    def test_snapshot_binds_actual_bytes(self):
        f = self.root / "file"
        f.write_bytes(b"one")
        old = snapshot(self.root)
        f.write_bytes(b"two")
        self.assertNotEqual(old, snapshot(self.root))


if __name__ == "__main__":
    unittest.main()
