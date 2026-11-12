"""Actual filesystem adapter tests; native QtIFW execution is separate."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import qtifw_trial_helper as helper
import qtifw_upgrade as upgrade
from resource_transaction import assert_resources_ready, resource_write_lock


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def inventory(values):
    return {name: dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest()) for name, data in values.items()}


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.target = Path(self.temporary.name) / "安装 中文"
        self.target.mkdir()
        self.program = self.target / "program"
        instance = dict(id="production", appId="D04394A0-DACE-4FF9-874D-3DCB43EE53D1")
        self.old_values = {
            "星图.exe": b"synthetic-old-program-never-run",
            "_internal/installation-profile.json": json.dumps(dict(product="星图", sourceCommit="a"*40,
                installerInstance=instance)).encode(),
            "release-report.json": json.dumps(dict(sourceCommit="a"*40, displayVersion="1.1.2")).encode(),
        }
        self.old = dict(instance="a"*32, payloadSource="a"*40, portable=True, fullUninstall=True,
            allowNewTargets=True, files=inventory(self.old_values))
        self.owner = dict(instance=self.old["instance"], payloadSource=self.old["payloadSource"],
            phase="complete", target=str(self.target))
        for name, data in self.old_values.items():
            write(self.program/name, data)
        helper.write_owner(self.target, self.owner, new=True)
        write(self.target/"_installer-support/_internal/qtifw-trial-policy.json", json.dumps(self.old).encode())
        write(self.target/"trial-maintenance.exe", b"synthetic-native-maintenance-never-run")
        write(self.target/"trial-maintenance.dat", b"synthetic-native-data")
        self.components("1.0.0")
        write(self.program/"work/ui-settings.json", b'{"automaticFailureEvidence":false}')
        write(self.program/"assets/custom-maps/route.json", b"user-route")
        write(self.program/"_internal/references/user-resource.bin", b"user-resource")
        self.new_values = dict(self.old_values, **{
            "星图.exe": b"synthetic-new-program-never-run",
            "_internal/csharp-grid/StarMap.Grid.exe": b"synthetic-grid-never-run",
            "_internal/installation-profile.json": json.dumps(dict(product="星图", sourceCommit="b"*40,
                installerInstance=instance)).encode(),
            "release-report.json": json.dumps(dict(sourceCommit="b"*40, displayVersion="1.1.3")).encode(),
        })
        self.policy = dict(instance="b"*32, payloadSource="b"*40, portable=True, fullUninstall=True,
            allowNewTargets=True, upgradeCapable=True, targetVersion=dict(version="1.1.3", displayVersion="1.1.3"),
            files=inventory(self.new_values))

    def components(self, version):
        content = "<Packages>" + "".join(f"<Package><Name>{name}</Name><Version>{version}</Version></Package>"
            for name in (upgrade.PACKAGE, upgrade.DATA_PACKAGE)) + "</Packages>"
        write(self.target/"components.xml", content.encode())

    def prepare(self):
        # Only watcher opt-in observation is replaced; all ownership, backup,
        # pending, filesystem checks and copies use their real implementation.
        with patch("game_startup_watcher.control", return_value=0):
            return upgrade._prepare(self.policy, self.target)

    def test_real_old_layout_is_recognized_without_writes(self):
        before = {p: p.read_bytes() for p in self.target.rglob("*") if p.is_file()}
        self.assertEqual(upgrade.check(self.policy, self.target)["mode"], "update")
        self.assertEqual({p: p.read_bytes() for p in self.target.rglob("*") if p.is_file()}, before)

    def test_fresh_target_and_unowned_nonempty_target(self):
        fresh = Path(self.temporary.name)/"fresh"
        self.assertEqual(upgrade.check(self.policy, fresh)["mode"], "fresh")
        self.assertFalse(fresh.exists())
        fresh.mkdir()
        write(fresh/"personal.txt", b"retain")
        with self.assertRaisesRegex(ValueError, "install-directory-not-empty"):
            upgrade.check(self.policy, fresh)

    def test_interrupted_same_setup_fresh_install_reuses_original_prepare_path(self):
        fresh = Path(self.temporary.name)/"fresh-interrupted"
        helper.prepare(self.policy, fresh)
        write(fresh/"program/星图.exe", b"interrupted-native-partial-file")
        self.assertTrue(helper.check_target(self.policy, fresh)["checked"])
        result = upgrade.check(self.policy, fresh)
        self.assertEqual(result["mode"], "fresh")
        self.assertTrue(result["recovery"])

    def test_interrupted_foreign_setup_cannot_enter_fresh_retry(self):
        fresh = Path(self.temporary.name)/"foreign-interrupted"
        helper.prepare(self.old, fresh)
        with self.assertRaises(ValueError):
            upgrade.check(self.policy, fresh)

    def test_unknown_new_program_name_is_not_overwritten(self):
        write(self.program/"_internal/csharp-grid/StarMap.Grid.exe", b"personal-collision")
        with self.assertRaisesRegex(ValueError, "unowned-upgrade-program-collision"):
            upgrade.check(self.policy, self.target)
        self.assertFalse((self.program/upgrade.PENDING).exists())

    def test_modified_owned_program_rejects_before_pending_or_backup(self):
        write(self.program/"星图.exe", b"changed")
        with self.assertRaisesRegex(ValueError, "program-byte-mismatch"):
            upgrade.check(self.policy, self.target)
        self.assertFalse((self.target/"recovery-files").exists())

    def test_copied_owner_cannot_adopt_another_root(self):
        self.owner["target"] = str(Path(self.temporary.name)/"elsewhere")
        helper.write_owner(self.target, self.owner)
        with self.assertRaisesRegex(ValueError, "owner-target-mismatch"):
            upgrade.check(self.policy, self.target)

    def test_backup_is_actual_old_program_and_never_user_data(self):
        record = self.prepare()
        self.assertEqual(record["stage"], "prepared")
        backup = self.target/record["backup"]
        for name, data in self.old_values.items():
            self.assertEqual((backup/"program"/name).read_bytes(), data)
        self.assertFalse((backup/"program/work").exists())
        self.assertFalse((backup/"program/assets").exists())
        self.assertEqual((self.program/"assets/custom-maps/route.json").read_bytes(), b"user-route")

    def test_backup_publish_failure_can_retry_without_adopting_torn_file(self):
        with patch("qtifw_upgrade.rename_no_replace", side_effect=OSError("interrupted-before-publication")):
            with self.assertRaisesRegex(OSError, "interrupted-before-publication"):
                self.prepare()
        record = upgrade.pending(self.policy, self.target)
        self.assertEqual(record["stage"], "backing-up")
        self.assertEqual((self.program/"星图.exe").read_bytes(), self.old_values["星图.exe"])
        self.assertFalse((self.target/record["backup"]/"program/星图.exe").exists())
        record = self.prepare()
        self.assertEqual(record["stage"], "prepared")
        for name, data in self.old_values.items():
            self.assertEqual((self.target/record["backup"]/"program"/name).read_bytes(), data)

    def test_pending_old_owner_only_authorizes_this_new_policy_and_root(self):
        self.prepare()
        self.assertEqual(helper.bound_owner(self.policy, self.target)[1], self.owner)
        with self.assertRaisesRegex(ValueError, "pending-context-mismatch"):
            helper.bound_owner(dict(self.policy, instance="c"*32), self.target)

    def test_pre_native_retry_rejects_new_user_collision_before_native_call(self):
        record = self.prepare()
        collision = self.program/"_internal/csharp-grid/StarMap.Grid.exe"
        write(collision, b"arrived-after-backup-user-file")
        for stage in ("backing-up", "prepared"):
            with self.subTest(stage=stage):
                record["stage"] = stage
                upgrade._write(self.program/upgrade.PENDING, record)
                with self.assertRaisesRegex(ValueError, "unowned-upgrade-program-collision"):
                    upgrade.check(self.policy, self.target)
                with patch("qtifw_upgrade._native_start") as native:
                    with self.assertRaisesRegex(ValueError, "unowned-upgrade-program-collision"):
                        upgrade.run(self.policy, self.target, support=Path(self.temporary.name)/"support")
                    native.assert_not_called()
                self.assertEqual(collision.read_bytes(), b"arrived-after-backup-user-file")

    def test_pre_native_retry_revalidates_old_owned_bytes(self):
        record = self.prepare()
        write(self.program/"星图.exe", b"changed-after-backup")
        for stage in ("backing-up", "prepared"):
            with self.subTest(stage=stage):
                record["stage"] = stage
                upgrade._write(self.program/upgrade.PENDING, record)
                with self.assertRaisesRegex(ValueError, "program-byte-mismatch"):
                    upgrade.check(self.policy, self.target)

    def test_pending_blocks_actual_resource_ready_and_startup_base_repair(self):
        self.prepare()
        with resource_write_lock(self.program):
            with self.assertRaisesRegex(ValueError, "installer-upgrade-recovery-required"):
                assert_resources_ready(self.program)
        from managed_base_resources import repair_managed_base_resources
        with self.assertRaisesRegex(ValueError, "installer-upgrade-recovery-required"):
            repair_managed_base_resources(self.program)

    def test_partial_native_payload_cannot_complete_or_delete_backup(self):
        record = self.prepare()
        write(self.program/"星图.exe", self.new_values["星图.exe"])
        with self.assertRaises(ValueError):
            upgrade._finish(self.policy, self.target, record)
        self.assertEqual(json.loads((self.target/helper.OWNER).read_bytes()), self.owner)
        self.assertTrue((self.program/upgrade.PENDING).exists())
        self.assertEqual((self.target/record["backup"]/"program/星图.exe").read_bytes(), self.old_values["星图.exe"])

    def test_native_loader_failure_keeps_pre_native_retry_checks(self):
        support = Path(self.temporary.name)/"support"
        write(support/"boot/installerbase.exe", b"synthetic base; native creation is substituted")
        write(support/"boot/update.rcc", b"synthetic controller resource")
        with patch("game_startup_watcher.control", return_value=0), \
                patch("qtifw_upgrade._native_start", side_effect=OSError(216, "loader rejected image")):
            with self.assertRaisesRegex(ValueError, "native-update-failed:launch"):
                upgrade.run(self.policy, self.target, support=support)
        self.assertEqual(upgrade.pending(self.policy, self.target)["stage"], "prepared")
        write(self.program/"_internal/csharp-grid/StarMap.Grid.exe", b"new user arrival after failed spawn")
        with self.assertRaisesRegex(ValueError, "unowned-upgrade-program-collision"):
            upgrade.check(self.policy, self.target)

    def test_new_files_without_native_component_commit_cannot_complete(self):
        record = self.prepare()
        for name, data in self.new_values.items():
            write(self.program/name, data)
        with self.assertRaisesRegex(ValueError, "component-version-incomplete"):
            upgrade._finish(self.policy, self.target, record)
        self.assertTrue((self.program/upgrade.PENDING).exists())

    def test_verified_forward_completion_keeps_userdata_and_backup(self):
        record = self.prepare()
        for name, data in self.new_values.items():
            write(self.program/name, data)
        self.components("1.1.3")
        verified = MagicMock()
        verified.__enter__.return_value = verified
        verified.returncode = 0
        verified.communicate.return_value = ("XINGTU_MAINTENANCE_SOURCE_VERIFIED: " + self.policy["instance"], "")
        # The external native maintenance process alone is substituted here;
        # the separate Windows fixture executes the actual persisted QRC.
        with resource_write_lock(self.program), patch("qtifw_upgrade._native_start", return_value=verified):
            result = upgrade._finish(self.policy, self.target, record)
        self.assertTrue(result["success"])
        self.assertFalse((self.program/upgrade.PENDING).exists())
        self.assertEqual(helper.bound_owner(self.policy, self.target)[1]["phase"], "complete")
        self.assertEqual((self.program/"assets/custom-maps/route.json").read_bytes(), b"user-route")
        self.assertTrue((self.target/record["backup"]/"upgrade-result.json").is_file())

    def test_stale_native_controller_cannot_adopt_owner_or_clear_pending(self):
        record = self.prepare()
        for name, data in self.new_values.items():
            write(self.program/name, data)
        self.components("1.1.3")
        stale = MagicMock()
        stale.__enter__.return_value = stale
        stale.returncode = 2
        stale.communicate.return_value = ("", "resource-busy")
        with patch("qtifw_upgrade._native_start", return_value=stale):
            with self.assertRaisesRegex(ValueError, "persisted-controller-verification-failed"):
                upgrade._finish(self.policy, self.target, record)
        self.assertEqual(json.loads((self.target/helper.OWNER).read_bytes()), self.owner)
        self.assertTrue((self.program/upgrade.PENDING).exists())

    @unittest.skipUnless(upgrade.os.name == "nt", "Windows loader error mode")
    def test_bad_native_image_returns_error_and_restores_thread_mode(self):
        import ctypes
        from ctypes import wintypes as wt
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.GetThreadErrorMode.restype = wt.DWORD
        api.SetThreadErrorMode.argtypes = [wt.DWORD, ctypes.POINTER(wt.DWORD)]
        api.SetThreadErrorMode.restype = wt.BOOL
        previous = api.GetThreadErrorMode()
        bad = Path(self.temporary.name)/"bad-native.exe"
        bad.write_bytes(b"NOT-A-NATIVE-SETUP")
        try:
            self.assertTrue(api.SetThreadErrorMode(0, None))
            with self.assertRaises(OSError):
                upgrade._native_start([str(bad)], creationflags=upgrade.subprocess.CREATE_NO_WINDOW)
            self.assertEqual(api.GetThreadErrorMode(), 0)
        finally:
            api.SetThreadErrorMode(previous, None)

    def test_external_resource_transaction_is_not_ignored_on_retry(self):
        record = self.prepare()
        self.assertEqual(record["stage"], "prepared")
        write(self.program/"work/map-packages/pending-removal.json", b"{}")
        with resource_write_lock(self.program):
            with self.assertRaisesRegex(ValueError, "removal-recovery-required"):
                from resource_transaction import _assert_resource_transactions_ready
                _assert_resource_transactions_ready(self.program)


if __name__ == "__main__":
    unittest.main()
