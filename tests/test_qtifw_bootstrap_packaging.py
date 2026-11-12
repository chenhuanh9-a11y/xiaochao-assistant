"""Compile and inspect the thin QtIFW release wrapper without running Setup."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_qtifw_trial as build


def inno_compiler():
    configured = os.environ.get("INNO_SETUP_ISCC")
    candidates = [Path(configured)] if configured else []
    candidates.append(Path(r"E:\codex\tools\inno-setup-6.7.3\compiler\ISCC.exe"))
    candidates.extend(Path(base) / "Inno Setup 6/ISCC.exe"
                      for key in ("ProgramFiles", "ProgramFiles(x86)")
                      if (base := os.environ.get(key)))
    return next((path for path in candidates if path.is_file()), candidates[0])


def brand_icon():
    candidates = [ROOT / "assets/brand/xingtu.ico",
        ROOT / "work/qt-setup/38a68f45f25b-production/freeze/星图/_internal/assets/brand/xingtu.ico"]
    return next((path for path in candidates if path.is_file()
                 and build.build.sha256(path).lower() == build.build.BRAND_ICON_SHA256.lower()), None)


class QtIfwBootstrapPackagingTests(unittest.TestCase):
    def test_upgrade_boot_files_are_staged_from_synthetic_inputs(self):
        with tempfile.TemporaryDirectory(prefix="xingtu-qtifw-boot-") as temporary:
            root = Path(temporary)
            helper = root / "helper"
            packages = root / "packages"
            config = root / "config"
            ifw = root / "ifw"
            helper.mkdir()
            packages.mkdir()
            config.mkdir()
            ifw.mkdir()
            icon = brand_icon()
            if icon is None:
                self.skipTest("A verified 星图 brand icon is unavailable for the wrapper fixture")
            native = root / "native-setup.exe"
            native.write_bytes(b"synthetic native setup")
            (ifw / "installerbase.exe").write_bytes(b"synthetic installerbase")
            (config / "controller.js").write_text("function Controller() {}\n", encoding="utf-8")
            (config / "config.xml").write_text("<Installer />", encoding="utf-8")
            shutil.copy2(icon, config / "xingtu.ico")
            calls = []

            def fake_run(command, *, check=False, cwd=None, **kwargs):
                calls.append((command, cwd))
                if Path(command[0]).name.lower() == "repogen.exe":
                    repository = Path(command[-1])
                    repository.mkdir(parents=True)
                    (repository / "Updates.xml").write_text("<Updates />", encoding="utf-8")
                else:
                    (Path(cwd) / "update.rcc").write_bytes(b"synthetic update RCC")
                return subprocess.CompletedProcess(command, 0)

            with patch.object(build.subprocess, "run", side_effect=fake_run):
                result = build.build_upgrade_bootstrap(helper, packages, config, native, ifw)

            boot = helper / "boot"
            self.assertEqual((boot / "native-setup.exe").read_bytes(), native.read_bytes())
            self.assertEqual((boot / "installerbase.exe").read_bytes(),
                             (ifw / "installerbase.exe").read_bytes())
            self.assertEqual((boot / "upgrade-controller.js").read_text(encoding="utf-8"),
                             (config / "controller.js").read_text(encoding="utf-8"))
            self.assertEqual(build.build.sha256(boot / "xingtu.ico"), build.build.BRAND_ICON_SHA256)
            self.assertEqual(result["sha256"], hashlib.sha256(native.read_bytes()).hexdigest())
            self.assertEqual([Path(call[0][0]).name.lower() for call in calls],
                             ["repogen.exe", "binarycreator.exe"])
            self.assertEqual(calls[1][1], boot)
            self.assertIn("-rcc", calls[1][0])
            self.assertNotIn("--resources", calls[1][0])

    def test_wrapper_compiles_with_complete_synthetic_dontcopy_tree(self):
        compiler = inno_compiler()
        if not compiler.is_file():
            self.skipTest(f"Inno Setup 6.7.3 compiler is unavailable: {compiler}")

        with tempfile.TemporaryDirectory(prefix="星图 bootstrap compile ") as temporary:
            root = Path(temporary)
            helper = root / "helper root"
            output = root / "final setup"
            (helper / "_internal").mkdir(parents=True)
            (helper / "boot" / "repository").mkdir(parents=True)
            output.mkdir()
            icon = brand_icon()
            if icon is None:
                self.skipTest("A verified 星图 brand icon is unavailable for the wrapper fixture")
            (helper / "qtifw-trial-helper.exe").write_bytes(b"synthetic helper fixture")
            (helper / "_internal" / "example.dat").write_bytes(b"synthetic runtime fixture")
            (helper / "boot" / "native-setup.exe").write_bytes(b"synthetic QtIFW Setup fixture")
            (helper / "boot" / "installerbase.exe").write_bytes(b"synthetic QtIFW base fixture")
            (helper / "boot" / "update.rcc").write_bytes(b"synthetic RCC fixture")
            shutil.copy2(icon, helper / "boot" / "xingtu.ico")
            (helper / "boot" / "upgrade-controller.js").write_text("function Controller() {}\n", encoding="utf-8")
            (helper / "boot" / "repository" / "Updates.xml").write_text("<Updates />", encoding="utf-8")

            setup = build.compile_upgrade_wrapper(compiler, helper, output,
                {"version": "1.1.3", "displayVersion": "1.1.3", "numericVersion": "1.0.0.25"})

            self.assertTrue(setup.is_file())
            self.assertGreater(setup.stat().st_size, 0)
            source = (ROOT / "tools/qtifw-bootstrap.iss").read_text(encoding="utf-8")
            self.assertIn("CreateAppDir=no", source)
            self.assertIn("Uninstallable=no", source)
            self.assertIn("CreateUninstallRegKey=no", source)
            self.assertIn("SetupIconFile={#HelperRoot}\\boot\\xingtu.ico", source)
            self.assertIn("ExtractTemporaryFiles('{app}\\*')", source)
            self.assertNotIn("[Run]", source)


if __name__ == "__main__":
    unittest.main()
