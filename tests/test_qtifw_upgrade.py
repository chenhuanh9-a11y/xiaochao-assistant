"""Actual filesystem adapter tests; native QtIFW execution is separate."""
import hashlib
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import sys
import unittest
from unittest.mock import MagicMock, patch

import qtifw_trial_helper as helper
import qtifw_upgrade as upgrade
import build_qtifw_trial as builder
from resource_transaction import assert_resources_ready, resource_write_lock, resource_runtime_lease


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
        local = Path(self.temporary.name) / "LocalAppData"
        local.mkdir()
        environment = patch.dict(os.environ, LOCALAPPDATA=str(local))
        environment.start()
        self.addCleanup(environment.stop)
        self.legacy = local / "Xingtu/resources"
        self.resources = self.program / "resources"
        self.protected = {}
        instance = dict(id="production", appId="D04394A0-DACE-4FF9-874D-3DCB43EE53D1")
        self.old_values = {
            "星图.exe": b"synthetic-old-program-never-run",
            "_internal/installation-profile.json": json.dumps(dict(product="星图", sourceCommit="a"*40,
                installerInstance=instance,
                resourceInstallationPolicy=dict(existingBasePlane="user-imported-catalog-only"))).encode(),
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
        for root, names in ((self.program, ("_internal/assets/legacy-author.png", "work/map-packages/author.receipt.json",
                                            "work/user-routes/personal.json")),
                            (self.resources, ("catalog.json", "packages/base/1.0/recognition.bin",
                                             "packages/author/1.0/route.png", "original-archives/author.zip"))):
            for name in names:
                path = root / name
                write(path, ("private-fixture:" + name).encode())
                self.protected[path] = (path.read_bytes(), path.stat().st_ino)
        self.new_values = dict(self.old_values, **{
            "星图.exe": b"synthetic-new-program-never-run",
            "_internal/csharp-grid/StarMap.Grid.exe": b"synthetic-grid-never-run",
            "_internal/installation-profile.json": json.dumps(dict(product="星图", sourceCommit="b"*40,
                installerInstance=instance)).encode(),
            "release-report.json": json.dumps(dict(sourceCommit="b"*40, displayVersion="1.1.5")).encode(),
        })
        self.policy = dict(instance="b"*32, payloadSource="b"*40, portable=True, fullUninstall=True,
            allowNewTargets=True, upgradeCapable=True, targetVersion=dict(version="1.1.5", displayVersion="1.1.5"),
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

    def assert_protected(self):
        for path, expected in self.protected.items():
            self.assertEqual((path.read_bytes(), path.stat().st_ino), expected)

    def native_process(self, *, exit_code=0):
        process = MagicMock()
        process.__enter__.return_value = process
        process.wait.return_value = exit_code
        process.returncode = exit_code
        process.communicate.return_value = ("XINGTU_MAINTENANCE_SOURCE_VERIFIED: " + self.policy["instance"], "")
        return process

    def support(self):
        support = Path(self.temporary.name) / "support"
        write(support/"boot/installerbase.exe", b"synthetic base, never run")
        write(support/"boot/update.rcc", b"synthetic RCC")
        return support

    def perform_update_with_native_substitute(self):
        def native(command, **kwargs):
            if "--script" in command:
                # Only QtIFW's native program extraction/component metadata is
                # substituted. Admission, locks, real backup, marker, source
                # verification and forward adoption execute the actual adapter.
                for name in self.old_values.keys() - self.new_values.keys():
                    (self.program/name).unlink(missing_ok=True)
                for name, data in self.new_values.items():
                    write(self.program/name, data)
                self.components(upgrade._component_version(self.policy))
                write(self.target/"_installer-support/_internal/qtifw-trial-policy.json",
                      json.dumps(self.policy).encode())
            return self.native_process()
        with patch("game_startup_watcher.control", return_value=0), \
                patch("qtifw_upgrade._native_start", side_effect=native):
            return upgrade.run(self.policy, self.target, support=self.support())

    def test_update_and_persisted_controller_verification_use_separate_native_caches(self):
        commands = []
        def native(command, **kwargs):
            commands.append(command)
            if "--script" in command:
                for name, data in self.new_values.items():
                    write(self.program/name, data)
                self.components("1.1.5")
            return self.native_process()
        with patch("game_startup_watcher.control", return_value=0), patch("qtifw_upgrade._native_start", side_effect=native):
            self.assertTrue(upgrade.run(self.policy, self.target, support=self.support())["success"])
        self.assertEqual(len(commands), 2)
        caches = [Path(command[command.index("--cache-path") + 1]) for command in commands]
        self.assertNotEqual(caches[0], caches[1])
        self.assertTrue(all(path.is_relative_to(self.target / "recovery-files") for path in caches))

    def test_program_only_112_upgrade_preserves_independent_packages_and_user_bytes(self):
        result = self.perform_update_with_native_substitute()
        self.assertTrue(result["success"])
        self.assert_protected()
        self.assertFalse((self.program/"_internal/managed-base-resources").exists())
        self.assertFalse((self.program/upgrade.PENDING).exists())
        self.assertEqual(helper.bound_owner(self.policy, self.target)[1]["phase"], "complete")

    def full_managed_layout(self):
        from managed_base_resources import manifest_for_rows
        profile = json.loads(self.old_values["_internal/installation-profile.json"])
        profile["resourceInstallationPolicy"] = dict(existingBasePlane="manifest-owned-transaction-before-runtime")
        self.old_values["_internal/installation-profile.json"] = json.dumps(profile).encode()
        self.brand_values = {"_internal/assets/brand/xingtu.ico": b"known old official icon fixture",
                             "_internal/assets/brand/xingtu-round.png": b"known old official round fixture"}
        candidates = dict(self.brand_values, **{"_internal/references/base.json": b"old managed candidate"})
        manifest = manifest_for_rows([dict(path=name, **row) for name, row in inventory(candidates).items()], "fixture")
        self.old_values["_internal/managed-base-resources/manifest.json"] = json.dumps(manifest).encode()
        for name, raw in candidates.items():
            self.old_values["_internal/managed-base-resources/payload/" + name] = raw
        for name, raw in self.brand_values.items():
            write(self.program/name, raw)  # Old startup's active plane, outside old QtIFW ownership.
        self.new_values.update(self.brand_values)
        self.policy["files"] = inventory(self.new_values)
        for name, raw in self.old_values.items():
            write(self.program/name, raw)
        self.old["files"] = inventory(self.old_values)
        write(self.target/"_installer-support/_internal/qtifw-trial-policy.json", json.dumps(self.old).encode())
        active = self.program/"_internal/references/base.json"
        write(active, b"retained active base, distinct from candidate")
        self.protected[active] = (active.read_bytes(), active.stat().st_ino)

    def test_full_managed_112_upgrade_retires_candidate_without_touching_active_or_imported_bytes(self):
        self.full_managed_layout()
        result = self.perform_update_with_native_substitute()
        self.assertTrue(result["success"])
        self.assert_protected()
        for name, raw in self.old_values.items():
            self.assertEqual((self.target/result["backup"]/"program"/name).read_bytes(), raw)
        for name, raw in self.brand_values.items():
            self.assertEqual((self.target/result["backup"]/"program"/name).read_bytes(), raw)
        self.assertFalse((self.program/"_internal/managed-base-resources/manifest.json").exists())
        self.assertFalse((self.program/upgrade.PENDING).exists())

    def old_113_source(self):
        previous = "d7063612fc847067f58b71940071228721d79fe0"
        self.old["payloadSource"] = self.owner["payloadSource"] = previous
        for name in ("_internal/installation-profile.json", "release-report.json"):
            metadata = json.loads(self.old_values[name])
            metadata["sourceCommit"] = previous
            if name == "release-report.json":
                metadata["displayVersion"] = "1.1.3"
            self.old_values[name] = json.dumps(metadata).encode()
            write(self.program/name, self.old_values[name])
        self.old["files"] = inventory(self.old_values)
        helper.write_owner(self.target, self.owner)
        write(self.target/"_installer-support/_internal/qtifw-trial-policy.json", json.dumps(self.old).encode())
        self.components("1.1.3")

    def test_existing_113_production_source_upgrades_to_114_with_user_data_retained(self):
        self.full_managed_layout()
        self.old_113_source()
        self.assertEqual(upgrade.check(self.policy, self.target)["mode"], "update")
        result = self.perform_update_with_native_substitute()
        self.assertTrue(result["success"])
        self.assert_protected()
        self.assertEqual(upgrade._versions(self.target)[upgrade.PACKAGE], "1.1.5")
        self.assertFalse((self.program/upgrade.PENDING).exists())

    def test_program_only_113_upgrade_preserves_independent_packages_and_user_bytes(self):
        self.old_113_source()
        result = self.perform_update_with_native_substitute()
        self.assertTrue(result["success"])
        self.assert_protected()

    def test_native_115_upgrade_to_116_preserves_independent_packages_and_user_bytes(self):
        previous = "6e5792dd5cae3dde2a2c68de320d433700743ca0"
        self.old["payloadSource"] = self.owner["payloadSource"] = previous
        for name in ("_internal/installation-profile.json", "release-report.json"):
            metadata = json.loads(self.old_values[name])
            metadata["sourceCommit"] = previous
            if name == "release-report.json":
                metadata["displayVersion"] = "1.1.5"
            self.old_values[name] = json.dumps(metadata).encode()
            write(self.program/name, self.old_values[name])
        self.old["files"] = inventory(self.old_values)
        helper.write_owner(self.target, self.owner)
        write(self.target/"_installer-support/_internal/qtifw-trial-policy.json", json.dumps(self.old).encode())
        self.components("1.1.5")
        report = json.loads(self.new_values["release-report.json"])
        report["displayVersion"] = "1.1.6"
        self.new_values["release-report.json"] = json.dumps(report).encode()
        self.policy.update(targetVersion=dict(version="1.1.6", displayVersion="1.1.6"),
                           files=inventory(self.new_values))
        self.assertEqual(upgrade.check(self.policy, self.target)["mode"], "update")
        result = self.perform_update_with_native_substitute()
        self.assertTrue(result["success"])
        self.assert_protected()
        self.assertEqual(upgrade._versions(self.target)[upgrade.PACKAGE], "1.1.6")

    def test_native_116_upgrade_to_117_keeps_native_owner_database_and_user_bytes(self):
        self.assert_native_owner_upgrade("03e1622e43603a7e3f2fc98bf97386406adc7945", "1.1.6", "1.1.7")

    def test_native_117_upgrade_to_118_keeps_native_owner_database_and_user_bytes(self):
        self.assert_native_owner_upgrade("aa6b4384c296c5fbf3f2d808062236a0ccb40449", "1.1.7", "1.1.8")

    def test_previous_112_through_117_sources_upgrade_to_the_118_package_revision(self):
        report = json.loads(self.new_values["release-report.json"])
        report["displayVersion"] = "1.1.8"
        self.new_values["release-report.json"] = json.dumps(report).encode()
        self.policy.update(targetVersion=dict(version="1.1.8", displayVersion="1.1.8"),
                           packageVersion="1.1.8.1", files=inventory(self.new_values))
        previous = (
            ("1.1.2", "a" * 40),
            ("1.1.3", "d7063612fc847067f58b71940071228721d79fe0"),
            ("1.1.4", "4" * 40),
            ("1.1.5", "6e5792dd5cae3dde2a2c68de320d433700743ca0"),
            ("1.1.6", "03e1622e43603a7e3f2fc98bf97386406adc7945"),
            ("1.1.7", "aa6b4384c296c5fbf3f2d808062236a0ccb40449"),
        )
        for version, source in previous:
            with self.subTest(previous=version):
                self.old_values = dict(self.new_values)
                self.old["payloadSource"] = self.owner["payloadSource"] = source
                for name, data in self.old_values.items():
                    write(self.program/name, data)
                for name in ("_internal/installation-profile.json", "release-report.json"):
                    metadata = json.loads(self.old_values[name])
                    metadata["sourceCommit"] = source
                    if name == "release-report.json":
                        metadata["displayVersion"] = version
                    self.old_values[name] = json.dumps(metadata).encode()
                    write(self.program/name, self.old_values[name])
                self.old["files"] = inventory(self.old_values)
                helper.write_owner(self.target, self.owner)
                write(self.target/"_installer-support/_internal/qtifw-trial-policy.json",
                      json.dumps(self.old).encode())
                self.components(version)
                result = self.perform_update_with_native_substitute()
                self.assertTrue(result["success"])
                self.assert_protected()
                self.assertEqual(upgrade._versions(self.target)[upgrade.PACKAGE], "1.1.8.1")
                self.assertFalse((self.program/upgrade.PENDING).exists())

    def test_same_version_hotfix_updates_only_the_exact_original_118_source_and_is_repeatable(self):
        self.configure_same_version_upgrade(upgrade.SAME_VERSION_REPAIR_SOURCE)
        self.assertEqual(upgrade.check(self.policy, self.target)["mode"], "update")
        result = self.perform_update_with_native_substitute()
        self.assertTrue(result["success"])
        self.assert_protected()
        self.assertEqual(upgrade._versions(self.target)[upgrade.PACKAGE], "1.1.8.1")
        self.assertEqual(self.policy["targetVersion"]["version"], "1.1.8")
        self.assertEqual(upgrade.check(self.policy, self.target)["mode"], "update")
        with patch("game_startup_watcher.control", return_value=0), \
                patch("qtifw_upgrade._native_start", return_value=self.native_process()) as native:
            repeated = upgrade.run(self.policy, self.target, support=self.support())
        self.assertTrue(repeated["success"])
        self.assertEqual(native.call_count, 2)  # QtIFW update command and persisted-controller check.

    def test_same_version_unrecognized_source_remains_rejected(self):
        self.configure_same_version_upgrade("e" * 40)
        with self.assertRaisesRegex(ValueError, "different-same-version-installation"):
            upgrade.check(self.policy, self.target)

    def test_same_version_controls_repair_updates_the_delivered_first_hotfix(self):
        self.configure_same_version_upgrade('1925b5cb7b874c04f35f310ad51c99a51d5c56bd')
        self.components('1.1.8.1')
        self.policy['packageVersion'] = '1.1.8.2'
        self.assertEqual(upgrade.check(self.policy, self.target)['mode'], 'update')
        result = self.perform_update_with_native_substitute()
        self.assertTrue(result['success'])
        self.assert_protected()
        self.assertEqual(upgrade._versions(self.target)[upgrade.PACKAGE], '1.1.8.2')
        self.assertEqual(self.policy['targetVersion']['version'], '1.1.8')

    def test_qtifw_package_revision_does_not_change_public_release_versions(self):
        self.assertEqual(builder.qtifw_component_version("1.1.8", release=True), "1.1.8.2")
        self.assertEqual(builder.qtifw_component_version("1.1.9", release=True), "1.1.9")
        self.assertEqual(builder.qtifw_component_version("1.1.8", release=False), "1.0.0")

    def assert_native_owner_upgrade(self, previous, old_version, target_version):
        self.old["payloadSource"] = self.owner["payloadSource"] = previous
        for name in ("_internal/installation-profile.json", "release-report.json"):
            metadata = json.loads(self.old_values[name])
            metadata["sourceCommit"] = previous
            if name == "release-report.json":
                metadata["displayVersion"] = old_version
            self.old_values[name] = json.dumps(metadata).encode()
            write(self.program/name, self.old_values[name])
        self.old["files"] = inventory(self.old_values)
        helper.write_owner(self.target, self.owner)
        write(self.target/"_installer-support/_internal/qtifw-trial-policy.json", json.dumps(self.old).encode())
        self.components(old_version)
        database = self.target / "trial-maintenance.dat"
        database_before = (database.read_bytes(), database.stat().st_ino)
        report = json.loads(self.new_values["release-report.json"])
        report["displayVersion"] = target_version
        self.new_values["release-report.json"] = json.dumps(report).encode()
        package_version = target_version + ".1" if target_version == "1.1.8" else target_version
        self.policy.update(targetVersion=dict(version=target_version, displayVersion=target_version),
                           packageVersion=package_version,
                           files=inventory(self.new_values))
        self.assertEqual(upgrade.check(self.policy, self.target)["mode"], "update")
        result = self.perform_update_with_native_substitute()
        self.assertTrue(result["success"])
        self.assert_protected()
        self.assertEqual((database.read_bytes(), database.stat().st_ino), database_before)
        self.assertEqual(upgrade._versions(self.target)[upgrade.PACKAGE], package_version)
        self.assertEqual((self.target/result["backup"]/"trial-maintenance.dat").read_bytes(), database_before[0])
        self.assertFalse((self.program/upgrade.PENDING).exists())

    def configure_same_version_upgrade(self, previous):
        self.old["payloadSource"] = self.owner["payloadSource"] = previous
        for name in ("_internal/installation-profile.json", "release-report.json"):
            metadata = json.loads(self.old_values[name])
            metadata["sourceCommit"] = previous
            if name == "release-report.json":
                metadata["displayVersion"] = "1.1.8"
            self.old_values[name] = json.dumps(metadata).encode()
            write(self.program/name, self.old_values[name])
        self.old["files"] = inventory(self.old_values)
        helper.write_owner(self.target, self.owner)
        write(self.target/"_installer-support/_internal/qtifw-trial-policy.json", json.dumps(self.old).encode())
        self.components("1.1.8")
        report = json.loads(self.new_values["release-report.json"])
        report["displayVersion"] = "1.1.8"
        self.new_values["release-report.json"] = json.dumps(report).encode()
        self.policy.update(targetVersion=dict(version="1.1.8", displayVersion="1.1.8"),
                           packageVersion="1.1.8.1", files=inventory(self.new_values))

    def cancel_native(self):
        with patch("game_startup_watcher.control", return_value=0), \
                patch("qtifw_upgrade._native_start", return_value=self.native_process(exit_code=2)):
            with self.assertRaisesRegex(ValueError, "native-update-failed:2"):
                upgrade.run(self.policy, self.target, support=self.support())
        self.assertEqual(upgrade.pending(self.policy, self.target)["stage"], "native-in-progress")
        self.assert_protected()
        self.assertEqual((self.program/"星图.exe").read_bytes(), self.old_values["星图.exe"])

    def test_native_cancel_keeps_pending_backup_and_all_user_bytes(self):
        self.cancel_native()

    def test_cancel_then_unknown_new_only_arrival_blocks_retry_before_native(self):
        self.cancel_native()
        collision = self.program/"_internal/csharp-grid/StarMap.Grid.exe"
        write(collision, b"user-arrival-after-native-cancel")
        with patch("qtifw_upgrade._native_start") as native:
            with self.assertRaisesRegex(ValueError, "upgrade-intermediate-program-byte-mismatch"):
                upgrade.run(self.policy, self.target, support=self.support())
            native.assert_not_called()
        self.assertEqual(collision.read_bytes(), b"user-arrival-after-native-cancel")
        self.assertEqual(upgrade.pending(self.policy, self.target)["stage"], "native-in-progress")

    def test_native_partial_old_new_bytes_and_absent_old_candidate_can_resume(self):
        self.full_managed_layout()
        self.cancel_native()
        for name in self.old_values.keys() - self.new_values.keys():
            (self.program/name).unlink()
        write(self.program/"星图.exe", self.new_values["星图.exe"])
        write(self.program/"_internal/csharp-grid/StarMap.Grid.exe", self.new_values["_internal/csharp-grid/StarMap.Grid.exe"])
        (self.program/"release-report.json").unlink()
        self.assertEqual(upgrade.check(self.policy, self.target)["mode"], "update")
        result = self.perform_update_with_native_substitute()
        self.assertTrue(result["success"])
        self.assert_protected()
        for name, raw in self.brand_values.items():
            self.assertEqual((self.target/result["backup"]/"program"/name).read_bytes(), raw)

    def test_complete_native_payload_retry_only_verifies_and_finalizes(self):
        self.cancel_native()
        for name, raw in self.new_values.items():
            write(self.program/name, raw)
        self.components("1.1.5")
        with patch("qtifw_upgrade._native_start", return_value=self.native_process()) as native:
            result = upgrade.run(self.policy, self.target, support=self.support())
        self.assertTrue(result["success"])
        self.assertEqual(native.call_count, 1)
        self.assertIn("list", native.call_args.args[0])
        self.assertNotIn("--script", native.call_args.args[0])
        self.assert_protected()

    def test_retry_rejects_unknown_bytes_on_previously_owned_path(self):
        self.cancel_native()
        write(self.program/"星图.exe", b"unrecognized partial or user modification")
        with patch("qtifw_upgrade._native_start") as native:
            with self.assertRaisesRegex(ValueError, "upgrade-intermediate-program-byte-mismatch"):
                upgrade.run(self.policy, self.target, support=self.support())
            native.assert_not_called()
        self.assertEqual((self.program/"星图.exe").read_bytes(), b"unrecognized partial or user modification")

    def test_retry_rejects_new_payload_hardlink_even_when_bytes_match(self):
        self.cancel_native()
        name = "_internal/csharp-grid/StarMap.Grid.exe"
        outside = Path(self.temporary.name)/"external-owned-file"
        write(outside, self.new_values[name])
        (self.program/name).parent.mkdir(parents=True)
        os.link(outside, self.program/name)
        with patch("qtifw_upgrade._native_start") as native:
            with self.assertRaisesRegex(ValueError, "program-object-not-exclusive-file"):
                upgrade.run(self.policy, self.target, support=self.support())
            native.assert_not_called()
        self.assertEqual(outside.read_bytes(), self.new_values[name])

    def test_retry_rejects_file_at_new_program_parent_without_replacing_it(self):
        self.cancel_native()
        collision = self.program/"_internal/csharp-grid"
        write(collision, b"user-owned file where new payload needs a directory")
        with patch("qtifw_upgrade._native_start") as native:
            with self.assertRaisesRegex(ValueError, "program-parent-not-directory"):
                upgrade.run(self.policy, self.target, support=self.support())
            native.assert_not_called()
        self.assertEqual(collision.read_bytes(), b"user-owned file where new payload needs a directory")

    def replace_old_receipt(self):
        for name, raw in self.old_values.items():
            write(self.program/name, raw)
        self.old["files"] = inventory(self.old_values)
        write(self.target/"_installer-support/_internal/qtifw-trial-policy.json", json.dumps(self.old).encode())

    def test_active_brand_with_changed_bytes_is_not_adopted(self):
        self.full_managed_layout()
        name = "_internal/assets/brand/xingtu.ico"
        write(self.program/name, b"user-customized-icon")
        with self.assertRaisesRegex(ValueError, "unowned-upgrade-program-collision"):
            upgrade.check(self.policy, self.target)
        self.assertEqual((self.program/name).read_bytes(), b"user-customized-icon")
        self.assertFalse((self.program/upgrade.PENDING).exists())

    def test_active_brand_requires_owned_manifest_and_candidate(self):
        self.full_managed_layout()
        for name in ("_internal/managed-base-resources/manifest.json",
                     "_internal/managed-base-resources/payload/_internal/assets/brand/xingtu.ico"):
            with self.subTest(name=name):
                row = self.old["files"].pop(name)
                write(self.target/"_installer-support/_internal/qtifw-trial-policy.json", json.dumps(self.old).encode())
                with self.assertRaisesRegex(ValueError, "unowned-upgrade-program-collision"):
                    upgrade.check(self.policy, self.target)
                self.old["files"][name] = row
        self.assertFalse((self.program/upgrade.PENDING).exists())

    def test_candidate_inventory_cannot_disagree_with_old_manifest(self):
        self.full_managed_layout()
        candidate = "_internal/managed-base-resources/payload/_internal/assets/brand/xingtu.ico"
        self.old_values[candidate] = b"candidate changed with otherwise matching QtIFW receipt"
        self.replace_old_receipt()
        with self.assertRaisesRegex(ValueError, "unowned-upgrade-program-collision"):
            upgrade.check(self.policy, self.target)
        self.assertFalse((self.program/upgrade.PENDING).exists())

    def test_active_brand_requires_exact_manifest_declaration(self):
        from managed_base_resources import manifest_for_rows
        self.full_managed_layout()
        manifest_name = "_internal/managed-base-resources/manifest.json"
        manifest = json.loads(self.old_values[manifest_name])
        rows = [row for row in manifest["files"] if row["path"] != "_internal/assets/brand/xingtu.ico"]
        self.old_values[manifest_name] = json.dumps(manifest_for_rows(rows, "fixture")).encode()
        self.replace_old_receipt()
        with self.assertRaisesRegex(ValueError, "unowned-upgrade-program-collision"):
            upgrade.check(self.policy, self.target)
        self.assertFalse((self.program/upgrade.PENDING).exists())

    def test_installed_catalog_and_removal_pending_block_before_native_and_survive_retry(self):
        self.prepare()
        for name, reason in (("pending-catalog.json", "resource-catalog-recovery-required"),
                             ("pending-removal.json", "resource-removal-recovery-required")):
            with self.subTest(name=name):
                marker = self.resources/"transactions"/name
                write(marker, b"private pending transaction")
                try:
                    with patch("qtifw_upgrade._native_start") as native:
                        with self.assertRaisesRegex(ValueError, reason):
                            upgrade.run(self.policy, self.target, support=self.support())
                        native.assert_not_called()
                    self.assertEqual(marker.read_bytes(), b"private pending transaction")
                    self.assertEqual(upgrade.pending(self.policy, self.target)["stage"], "prepared")
                    self.assert_protected()
                finally:
                    marker.unlink()

    def test_foreign_installer_marker_is_not_an_upgrade_permit(self):
        marker = self.program/upgrade.PENDING
        write(marker, json.dumps(dict(schema="xingtu-installer-upgrade-v1", status="prepared")).encode())
        with patch("qtifw_upgrade._native_start") as native:
            with self.assertRaisesRegex(ValueError, "upgrade-pending-context-mismatch"):
                upgrade.run(self.policy, self.target, support=self.support())
            native.assert_not_called()
        self.assert_protected()

    def test_active_installed_resource_writer_blocks_before_program_backup(self):
        ready, release = threading.Event(), threading.Event()
        def writer():
            with resource_write_lock(self.resources):
                ready.set()
                release.wait(10)
        thread = threading.Thread(target=writer)
        thread.start()
        try:
            self.assertTrue(ready.wait(5))
            with patch("qtifw_upgrade._native_start") as native:
                with self.assertRaisesRegex(ValueError, "resource-busy"):
                    upgrade.run(self.policy, self.target, support=self.support())
                native.assert_not_called()
            self.assertFalse((self.program/upgrade.PENDING).exists())
            self.assertFalse((self.target/"recovery-files").exists())
            self.assert_protected()
        finally:
            release.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())

    def test_old_global_catalog_pending_does_not_block_or_change_program_upgrade(self):
        markers = []
        for name in ("pending-catalog.json", "pending-removal.json"):
            marker = self.legacy / "transactions" / name
            write(marker, b"unrelated legacy transaction")
            markers.append(marker)
        self.assertEqual(upgrade.check(self.policy, self.target)["mode"], "update")
        result = self.perform_update_with_native_substitute()
        self.assertTrue(result["success"])
        self.assert_protected()
        for marker in markers:
            self.assertEqual(marker.read_bytes(), b"unrelated legacy transaction")

    def test_program_upgrade_needs_no_local_app_data(self):
        with patch.dict(os.environ):
            os.environ.pop("LOCALAPPDATA", None)
            self.assertTrue(self.perform_update_with_native_substitute()["success"])
        self.assert_protected()

    def test_installation_a_upgrade_isolated_from_installation_b_resources(self):
        other = self.target.parent / "安装 B"
        shutil.copytree(self.target, other)
        helper.write_owner(other, dict(self.owner, target=str(other)))
        marker = other / "program/resources/transactions/pending-catalog.json"
        write(marker, b"only installation B is pending")
        before = {path.relative_to(other): path.read_bytes() for path in other.rglob("*") if path.is_file()}
        with self.assertRaisesRegex(ValueError, "resource-catalog-recovery-required"):
            upgrade.check(self.policy, other)
        self.assertTrue(self.perform_update_with_native_substitute()["success"])
        self.assert_protected()
        self.assertEqual({path.relative_to(other): path.read_bytes() for path in other.rglob("*") if path.is_file()}, before)
        self.assertFalse(any(name.startswith("resources/") for name in self.policy["files"]))

    def test_separate_resource_writer_process_blocks_update_before_backup(self):
        root = Path(__file__).resolve().parents[1]
        code = ("import sys;sys.path.insert(0,sys.argv[1]);"
                "from resource_transaction import resource_write_lock;"
                "lock=resource_write_lock(sys.argv[2]);lock.__enter__();"
                "print('locked',flush=True);sys.stdin.read(1);lock.__exit__(None,None,None)")
        # This child is a real resource writer, without the program/runtime
        # lease. Only native QtIFW extraction is absent from this rejection path.
        with subprocess.Popen([sys.executable, "-B", "-c", code, str(root / "app"), str(self.resources)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0) as process:
            try:
                self.assertEqual(process.stdout.readline().strip(), "locked")
                with patch("qtifw_upgrade._native_start") as native:
                    with self.assertRaisesRegex(ValueError, "resource-busy"):
                        upgrade.run(self.policy, self.target, support=self.support())
                    native.assert_not_called()
                self.assertFalse((self.program / upgrade.PENDING).exists())
                self.assertFalse((self.target / "recovery-files").exists())
                self.assert_protected()
            finally:
                process.communicate("x", timeout=10)
        self.assertEqual(process.returncode, 0)

    def test_resource_created_before_program_lock_and_held_by_writer_blocks_update(self):
        preserved = self.target.parent / "resources-before-race"
        self.resources.rename(preserved)
        self.protected = {
            preserved / path.relative_to(self.resources) if path.is_relative_to(self.resources) else path: expected
            for path, expected in self.protected.items()
        }
        self.assertFalse(self.resources.exists())
        root = Path(__file__).resolve().parents[1]
        code = ("import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);"
                "from resource_transaction import resource_write_lock;"
                "root=Path(sys.argv[2]);root.mkdir();"
                "lock=resource_write_lock(root);lock.__enter__();"
                "(root/'catalog.json').write_bytes(b'writer-created-catalog');"
                "print('locked',flush=True);sys.stdin.read(1);lock.__exit__(None,None,None)")
        processes = []
        real_program_lock = upgrade.resource_write_lock

        @contextlib.contextmanager
        def race_before_program_lock(program):
            # Interleave an actual independent writer exactly where the old
            # adapter had already skipped the absent resource plane. Keep all
            # program/resource locks and native adapter checks unchanged.
            process = subprocess.Popen([sys.executable, "-B", "-c", code,
                    str(root / "app"), str(self.resources)], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            processes.append(process)
            self.assertEqual(process.stdout.readline().strip(), "locked")
            with real_program_lock(program):
                yield

        try:
            with patch.object(upgrade, "resource_write_lock", side_effect=race_before_program_lock), \
                    patch.object(upgrade, "_prepare", wraps=upgrade._prepare) as prepare:
                with self.assertRaisesRegex(ValueError, "resource-busy"):
                    self.perform_update_with_native_substitute()
                prepare.assert_not_called()
            self.assertEqual((self.resources / "catalog.json").read_bytes(), b"writer-created-catalog")
            self.assertFalse((self.program / upgrade.PENDING).exists())
            self.assertFalse((self.target / "recovery-files").exists())
            self.assert_protected()
        finally:
            for process in processes:
                process.communicate("x", timeout=10)
        self.assertEqual(processes[0].returncode, 0)

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
        self.components("1.1.5")
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
        self.components("1.1.5")
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
    def test_post_start_mode_restore_failure_drains_owned_child_before_result_and_unlock(self):
        import ctypes
        support = self.support()
        write(support / "qtifw-trial-policy.json", json.dumps(self.policy).encode())
        result_file = support / "update-attempt-post-start.json"
        original_run, original_start = upgrade.run, upgrade._native_start
        real_popen = upgrade.subprocess.Popen
        children, observers, observed = [], [], {}

        def observe(child):
            time.sleep(.1)
            observed["childAlive"] = child.poll() is None
            observed["resultPublished"] = result_file.exists()
            try:
                lease = resource_runtime_lease(self.program)
            except (ValueError, OSError) as error:
                observed["leaseBlocked"] = "resource-busy" in str(error)
            else:
                lease.close()
                observed["leaseBlocked"] = False

        def finite_child(command, **kwargs):
            # Only native maintenance work is substituted. The actual Popen,
            # mode-restoration failure, adapter, leases and publisher run here.
            # Captured output also covers the verification caller: a plain
            # wait without draining these pipes would leave the child stuck.
            kwargs.update(stdout=upgrade.subprocess.PIPE, stderr=upgrade.subprocess.PIPE)
            child = real_popen([sys.executable, "-B", "-c",
                "import sys,time;sys.stdout.write('x'*131072);sys.stderr.write('y'*131072);time.sleep(.6)"], **kwargs)
            children.append(child)
            watcher = threading.Thread(target=observe, args=(child,))
            observers.append(watcher)
            watcher.start()
            return child

        def fail_restore(command, **kwargs):
            api = MagicMock()
            api.GetThreadErrorMode.return_value = 0
            api.SetThreadErrorMode.side_effect = [1, 0]
            with patch.object(ctypes, "WinDLL", return_value=api), \
                    patch.object(upgrade.subprocess, "Popen", side_effect=finite_child):
                return original_start(command, **kwargs)

        arguments = ["helper", "bootstrap-run", str(self.target), "--result-file", str(result_file)]
        try:
            with patch.object(helper, "__file__", str(support / "qtifw_trial_helper.py")), \
                    patch.object(sys, "argv", arguments), patch("game_startup_watcher.control", return_value=0), \
                    patch.object(upgrade, "run", side_effect=lambda policy, target, **kwargs:
                                 original_run(policy, target, support=support, **kwargs)), \
                    patch.object(upgrade, "_native_start", side_effect=fail_restore), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(helper.main(), 2)
            self.assertIsNotNone(children[0].poll(), "A terminal failure must not leave the owned native child running")
            for watcher in observers:
                watcher.join(timeout=5)
            self.assertTrue(observed["childAlive"])
            self.assertFalse(observed["resultPublished"], "Do not publish a retryable failure while native work is alive")
            self.assertTrue(observed["leaseBlocked"], "Keep the resource lease until the native child naturally exits")
            outcome = json.loads(result_file.read_text(encoding="utf-8"))
            self.assertFalse(outcome["success"])
            self.assertEqual(outcome["reason"], "native-launch-error-mode-restoration-failed")
            self.assertEqual(upgrade.pending(self.policy, self.target)["stage"], "native-in-progress")
            self.assert_protected()
        finally:
            for child in children:
                child.communicate(timeout=5)
            for watcher in observers:
                watcher.join(timeout=5)

    @unittest.skipUnless(upgrade.os.name == "nt", "Windows loader error mode")
    def test_mode_restore_failure_without_started_child_returns_promptly(self):
        import ctypes
        api = MagicMock()
        api.GetThreadErrorMode.return_value = 0
        api.SetThreadErrorMode.side_effect = [1, 0]
        missing = Path(self.temporary.name) / "never-created.exe"
        started = time.perf_counter()
        with patch.object(ctypes, "WinDLL", return_value=api):
            with self.assertRaisesRegex(ValueError, "native-launch-error-mode-restoration-failed"):
                upgrade._native_start([str(missing)], creationflags=upgrade.subprocess.CREATE_NO_WINDOW)
        self.assertLess(time.perf_counter() - started, 1)

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
            started = time.perf_counter()
            with self.assertRaises(OSError):
                upgrade._native_start([str(bad)], creationflags=upgrade.subprocess.CREATE_NO_WINDOW)
            self.assertLess(time.perf_counter() - started, 1)
            self.assertEqual(api.GetThreadErrorMode(), 0)
        finally:
            api.SetThreadErrorMode(previous, None)

    def test_program_resource_transaction_is_not_ignored_on_retry(self):
        record = self.prepare()
        self.assertEqual(record["stage"], "prepared")
        write(self.program/"work/map-packages/pending-removal.json", b"{}")
        with resource_write_lock(self.program):
            with self.assertRaisesRegex(ValueError, "removal-recovery-required"):
                from resource_transaction import _assert_resource_transactions_ready
                _assert_resource_transactions_ready(self.program)


if __name__ == "__main__":
    unittest.main()
