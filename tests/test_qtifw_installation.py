"""Directory reuse runs actual upgrade admission; only registry/process reads are fixtures."""
import json
from pathlib import Path
import shutil
import unittest
import uuid

import test_qtifw_upgrade as fixtures
import qtifw_installation as discovery
import qtifw_trial_helper as helper


class InstallationDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.UpgradeTests("test_program_only_112_upgrade_preserves_independent_packages_and_user_bytes")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.policy, self.target = self.fixture.policy, self.fixture.target
        self.row = dict(DisplayName="星图", Publisher="星图", DisplayVersion="1.1.2",
                        InstallLocation=str(self.target), guid="{" + str(uuid.uuid4()) + "}", hive="HKCU", view=64,
                        UninstallString='"' + str(self.target / "trial-maintenance.exe") + '" --start-uninstaller')

    def second(self):
        target = self.target.with_name("another verified installation")
        shutil.copytree(self.target, target)
        helper.write_owner(target, dict(self.fixture.owner, target=str(target)))
        return dict(self.row, InstallLocation=str(target), guid="{" + str(uuid.uuid4()) + "}",
                    UninstallString='"' + str(target / "trial-maintenance.exe") + '" --start-uninstaller')

    def test_one_verified_root_is_reused_without_any_directory_mutation(self):
        before = {p: (p.stat().st_ino, p.read_bytes()) for p in self.target.rglob("*") if p.is_file()}
        result = discovery.discover(self.policy, rows=[self.row])
        self.assertEqual((result["mode"], result["target"]), ("update", str(self.target)))
        self.assertEqual(before, {p: (p.stat().st_ino, p.read_bytes()) for p in self.target.rglob("*") if p.is_file()})

    def test_duplicate_registry_views_are_one_root(self):
        result = discovery.discover(self.policy, rows=[self.row, dict(self.row, view=32)])
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["mode"], "update")

    def test_conflicting_guids_for_same_root_require_inspection(self):
        result = discovery.discover(self.policy, rows=[self.row, dict(self.row, guid="{" + str(uuid.uuid4()) + "}")])
        self.assertEqual(result["mode"], "select")
        self.assertFalse(result["candidates"])

    def test_stale_missing_root_does_not_block_fresh_installation(self):
        stale = self.target.with_name("missing historical directory")
        row = dict(self.row, InstallLocation=str(stale),
                   UninstallString='"' + str(stale / "trial-maintenance.exe") + '" --start-uninstaller')
        self.assertEqual(discovery.discover(self.policy, rows=[row])["mode"], "fresh")
        self.assertFalse(stale.exists())

    def test_multiple_verified_roots_need_selection_without_current_process_evidence(self):
        result = discovery.discover(self.policy, rows=[self.row, self.second()], active=lambda root: False)
        self.assertEqual(result["mode"], "select")
        self.assertEqual(result["reason"], "multiple-valid-installations")

    def test_only_running_verified_root_wins_independent_of_registry_order(self):
        second = self.second()
        for rows in ([self.row, second], [second, self.row]):
            result = discovery.discover(self.policy, rows=rows, active=lambda root: root == second["InstallLocation"])
            self.assertEqual(result["target"], second["InstallLocation"])
            self.assertEqual(result["selection"], "running-production-installation")

    def test_changed_program_bytes_are_reported_instead_of_adopted(self):
        (self.target / "program/星图.exe").write_bytes(b"user replacement")
        result = discovery.discover(self.policy, rows=[self.row])
        self.assertEqual(result["mode"], "select")
        self.assertIn("program-byte-mismatch", result["rejected"][0]["reason"])
        self.assertEqual((self.target / "program/星图.exe").read_bytes(), b"user replacement")

    def test_registry_location_and_maintenance_command_must_agree(self):
        result = discovery.discover(self.policy, rows=[dict(self.row, UninstallString='"C:/download/Setup.exe" --start-uninstaller')])
        self.assertEqual(result["mode"], "select")
        self.assertEqual(result["rejected"][0]["reason"], "registered-maintenance-path-mismatch")

    def test_actual_114_profile_and_inventory_are_accepted_for_115(self):
        second = self.second()
        data = json.loads(self.fixture.old_values["release-report.json"])
        data["displayVersion"] = "1.1.4"
        self.fixture.old_values["release-report.json"] = json.dumps(data).encode()
        for name, raw in self.fixture.old_values.items():
            fixtures.write(self.target / "program" / name, raw)
        self.fixture.old["files"] = fixtures.inventory(self.fixture.old_values)
        fixtures.write(self.target / "_installer-support/_internal/qtifw-trial-policy.json", json.dumps(self.fixture.old).encode())
        self.fixture.components("1.1.4")
        self.row["DisplayVersion"] = "1.1.4"
        self.assertEqual(discovery.discover(self.policy, rows=[self.row])["mode"], "update")
        result = discovery.discover(self.policy, rows=[second, self.row], active=lambda root: False)
        self.assertEqual(result["target"], str(self.target))
        self.assertEqual(result["selection"], "latest-verified-production-release")
        self.assertTrue(self.fixture.perform_update_with_native_substitute()["success"])
        self.fixture.assert_protected()


if __name__ == "__main__":
    unittest.main()
