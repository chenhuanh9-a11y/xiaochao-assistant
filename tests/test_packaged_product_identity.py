"""Synthetic baked identities; no installed EXE, registry, GUI or build."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "tools")]
import packaged_product_identity as identity
import star_map_import as importer
import build_qt_client as build


class PackagedIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = self.root / "_helper_runtime"
        self.runtime.mkdir()
        self.enterContext(patch.object(sys, "frozen", True, create=True))
        self.enterContext(patch.object(sys, "_MEIPASS", str(self.runtime), create=True))
        self.profile = dict(product="星图", installerInstance=dict(id="production", product="星图",
                            appId=identity.PRODUCTION_APP_ID))

    def bake(self, profile):
        (self.runtime / "installation-profile.json").write_text(json.dumps(profile), encoding="utf-8")

    def test_only_baked_qt_profile_selects_new_exact_executable(self):
        # Caller-root metadata never chooses the helper's parent identity.
        (self.root / "installation-profile.json").write_text(json.dumps(self.profile), encoding="utf-8")
        self.assertEqual(identity.expected_application_executable(), "第五人格小抄助手.exe")
        self.bake(self.profile)
        self.assertEqual(identity.expected_application_executable(), "星图.exe")
        with patch.object(sys, "frozen", False):
            self.assertEqual(identity.expected_application_executable(), "第五人格小抄助手.exe")

    def test_existing_invalid_baked_profiles_never_fall_back(self):
        wrong = dict(self.profile, installerInstance=dict(self.profile["installerInstance"], appId="other"))
        for profile in ({}, [], wrong, dict(self.profile, product="第五人格小抄助手")):
            with self.subTest(profile=profile):
                self.bake(profile)
                with self.assertRaises(ValueError):
                    identity.expected_application_executable()
        (self.runtime / "installation-profile.json").write_text("{", encoding="utf-8")
        with self.assertRaises(ValueError):
            identity.expected_application_executable()

    def test_isolated_profile_cannot_select_production_helper(self):
        self.bake(dict(product="XingtuQtClientTest", installerInstance=dict(id="client",
            product="XingtuQtClientTest", appId="AF19AFD9-3016-44C9-9D20-6F216C5D8C51")))
        with self.assertRaisesRegex(ValueError, "Isolated"):
            identity.expected_application_executable()

    def test_closed_gate_checks_both_fixed_migration_names(self):
        with patch.object(importer, "_check_executable_closed") as check:
            importer._check_closed(self.root)
        self.assertEqual([call.args[0] for call in check.call_args_list],
                         [self.root / "第五人格小抄助手.exe", self.root / "星图.exe"])
        with patch.object(importer, "_check_executable_closed", side_effect=importer.ImportRejected("busy")):
            with self.assertRaisesRegex(importer.ImportRejected, "busy"):
                importer._check_closed(self.root)

    def test_production_pe_brand_and_appid_contract(self):
        product = build.INSTANCES["production"]
        self.assertEqual(product["product"], "星图")
        self.assertEqual(product["appId"], identity.PRODUCTION_APP_ID)
        self.assertEqual(product["installFolder"], "XiaochaoAssistant")
        from PyInstaller.utils.win32.versioninfo import VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct, VarFileInfo, VarStruct
        info = eval(build.version_info_text("星图", "1.0.0.249"), {
            "VSVersionInfo": VSVersionInfo, "FixedFileInfo": FixedFileInfo,
            "StringFileInfo": StringFileInfo, "StringTable": StringTable, "StringStruct": StringStruct,
            "VarFileInfo": VarFileInfo, "VarStruct": VarStruct})
        strings = {row.name: row.val for row in info.kids[0].kids[0].kids}
        self.assertEqual(strings["ProductName"], "星图")
        self.assertEqual(strings["FileDescription"], "星图")
        self.assertEqual(strings["OriginalFilename"], "星图.exe")


if __name__ == "__main__":
    unittest.main()
