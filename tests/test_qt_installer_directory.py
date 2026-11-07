"""Installer source contracts only; native wizard/registry checks run on Windows."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class InstallerDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.source = (ROOT / "tools/qt-client.iss").read_text(encoding="utf-8-sig")

    def test_private_directory_is_selectable_and_previous_location_is_adopted(self):
        private = self.source.split("AppPublisher=Xingtu project (private Qt test)", 1)[1].split("#endif", 1)[0]
        self.assertIn("UsePreviousAppDir=yes", private)
        self.assertIn("DisableDirPage=no", private)
        build = (ROOT / "tools/build_qt_client.py").read_text(encoding="utf-8")
        self.assertIn("usePreviousAppDir=True,", build)

    def test_private_guard_rechecks_selection_and_final_install_boundary(self):
        self.assertIn("; Private directory selection", self.source)
        private = self.source.split("; Private directory selection", 1)[1]
        self.assertIn("function NextButtonClick(CurPageID: Integer): Boolean;", private)
        self.assertIn("CurPageID = wpSelectDir", private)
        self.assertIn("function PrepareToInstall(var NeedsRestart: Boolean): String;", private)
        self.assertEqual(private.count("PrivateDirectoryError(ExpandConstant('{app}'))"), 2)
        for marker in ("第五人格小抄助手.exe", "星图.exe", "HKCU64", "HKLM64", "HKCU32", "HKLM32"):
            self.assertIn(marker, private)
        self.assertIn("D04394A0-DACE-4FF9-874D-3DCB43EE53D1", private)

    def test_profile_text_never_authorizes_an_unknown_nonempty_target(self):
        # Malformed JSON, foreign appId plus own GUID in unrelated fields, and
        # absent profiles are all irrelevant: only native registration grants
        # an existing nonempty directory permission to upgrade.
        private = self.source.split("; Private directory selection", 1)[1]
        self.assertNotIn("LoadStringFromFile", private)
        self.assertNotIn("String(Contents)", private)
        self.assertIn("(Entries > 0) and not RegisteredPrivateDirectory(Target)", private)
        self.assertIn("RegQueryStringValue(HKLM64,", private)
        self.assertIn("'{#InstallerAppId}_is1'", private)

    def test_tree_scan_is_bounded_and_never_follows_child_links(self):
        private = self.source.split("; Private directory selection", 1)[1]
        scan = private.split("function SafePrivateTree", 1)[1].split("function PrivateDirectoryError", 1)[0]
        self.assertIn("Depth > 32", scan)
        self.assertIn("Entries > 50000", scan)
        self.assertLess(scan.index("Find.Attributes and $400"), scan.index("SafePrivateTree(Path +"))
        self.assertIn("Error = 2", scan)
        self.assertIn("Error = 18", scan)
        self.assertIn("finally", scan)
        self.assertIn("FindClose(Find)", scan)
        self.assertIn("Attributes and $400", private)
        self.assertIn("(Error <> 2) and (Error <> 3)", private)

    def test_production_upgrade_contract_is_not_replaced(self):
        production = self.source.split("; Original per-user installer", 1)[1].split("; Private directory selection", 1)[0]
        for marker in ("UpgradePathAllowed", "InstalledExecutableAvailable", "BeginUpgrade",
                       "UPGRADE_ROLLBACK_RESTORED", '#include "qt-uninstall-data.iss"',
                       "UninstallHoldingDirectory"):
            self.assertIn(marker, production)


if __name__ == "__main__":
    unittest.main()
