"""Compile-only fixture for the isolated Grid Test installer identity."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ISCC = Path(os.environ.get(
    "INNO_SETUP_ISCC", r"E:\codex\tools\inno-setup-6.7.3\compiler\ISCC.exe"))

import sys
sys.path.insert(0, str(ROOT / "tools"))
import build_qt_client as build


class GridIsolatedInstallerTests(unittest.TestCase):
    def test_grid_profile_binds_to_compile_only_inno_fixture(self):
        profile = build.installer_instance_profile("grid-test")
        self.assertEqual(profile["appId"], "2D739B42-E6AC-4C37-9498-95DC9CC9AA43")
        self.assertEqual(profile["appName"], "Xingtu Grid Test")
        self.assertEqual(profile["startMenuGroup"], "Xingtu Grid Test")
        self.assertEqual(profile["installFolder"], "XingtuGridTest")
        self.assertEqual(profile["setupPrefix"], "XingtuGridTest-Setup")
        self.assertEqual(profile["versionSuffix"], "-grid-test")
        self.assertEqual(profile["product"], build.PRODUCT)
        self.assertEqual(profile["appUserModelId"], "Xingtu.GridTest")
        self.assertFalse(profile["usePreviousAppDir"])

        if not ISCC.is_file():
            self.skipTest(f"Inno Setup compiler is unavailable: {ISCC}")

        with tempfile.TemporaryDirectory(prefix="xingtu-grid-inno-") as temporary:
            root = Path(temporary)
            stage = root / "stage"
            output = root / "setup"
            stage.mkdir()
            output.mkdir()
            (stage / (profile["product"] + ".exe")).write_bytes(b"compile-fixture-only")
            (stage / "version.json").write_text("{}", encoding="utf-8")

            command = [str(ISCC), f"/DStageDir={stage}", f"/DSetupOutputDir={output}",
                       "/DSetupName=XingtuGridTest-fixture", "/DDisplayVersion=1.0.0-grid-test",
                       "/DNumericVersion=1.0.0.0", *build.inno_identity_defines(profile),
                       str(ROOT / "tools/qt-client.iss")]
            compiled = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            self.assertTrue((output / "XingtuGridTest-fixture.exe").is_file())

            mismatched = command.copy()
            mismatched.insert(-1, "/DInstallerAppName=Wrong Test Name")
            rejected = subprocess.run(mismatched, cwd=ROOT, capture_output=True, text=True)
            self.assertNotEqual(rejected.returncode, 0, rejected.stdout + rejected.stderr)
            self.assertIn("Invalid grid test installer identity", rejected.stdout + rejected.stderr)


if __name__ == "__main__":
    unittest.main()
