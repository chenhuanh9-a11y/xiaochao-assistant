"""Real build projection and repair functions, isolated byte fixtures only."""
from pathlib import Path
import hashlib
import json
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "tools"), str(ROOT / "experiments/qt_quick")]
import build_qt_client as build
import managed_base_resources as managed


class ManagedBaseResourcesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.stage, self.target, self.output = (self.folder / name for name in ("stage", "target", "output"))
        self.output.mkdir()
        self.resources = {}
        for name in ("references/difficult-native/difficult-map-01-1-shared.npz", "references/catalog.json",
                     "recognition/native/index.json", "difficult-native-manifest.json"):
            data = ("bundled:" + name).encode()
            self.write(self.stage / "_internal" / name, data)
            self.resources[name] = self.row(name, data)

    @staticmethod
    def row(name, data):
        return dict(path=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())

    @staticmethod
    def write(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def project(self):
        include, _ = build.write_production_files_include(self.stage, self.resources, self.output)
        existing_base = any((self.target / name).exists() for name in (
            "_internal/assets", "_internal/references", "_internal/recognition",
            "_internal/difficult-native-manifest.json"))
        # Copy exact generated rows only; decision/repair is the production Python
        # implementation, not a Python reimplementation of the Pascal planner.
        for line in include.read_text("utf-8-sig").splitlines():
            if not line.startswith("Source:") or "dontcopy" in line:
                continue
            if "Check: InstallBaseResourcePlane" in line and existing_base:
                continue
            source = re.search(r'Source: "\{#StageDir\}\\([^"]+)"', line)[1].replace("\\", "/")
            dest = re.search(r'DestDir: "\{app\}([^"]*)"', line)[1].replace("\\", "/").lstrip("/")
            self.write(self.target / dest / Path(source).name, (self.stage / source).read_bytes())
        return include

    def test_generated_installer_stages_candidates_without_overwriting_active_base(self):
        target = self.target / "_internal/references/catalog.json"
        self.write(target, b"unknown-author-content")
        include = self.project()
        self.assertEqual(target.read_bytes(), b"unknown-author-content",
                         "Generated installer must not overwrite an active resource before ownership/compatibility checks")
        self.assertIn("managed-base-resources", include.read_text("utf-8-sig"),
                      "Generated production entry must carry the complete candidate payload for first-run repair")

    def test_existing_empty_directory_repairs_entire_dependency_set(self):
        (self.target / "_internal/assets").mkdir(parents=True)
        self.project()
        self.assertTrue((self.target / "_internal/managed-base-resources/manifest.json").is_file(),
                        "Even an empty existing directory must receive the complete repair candidate manifest")
        result = managed.repair_managed_base_resources(self.target)
        self.assertEqual(result["status"], "repaired")
        for name in self.resources:
            self.assertEqual((self.target / "_internal" / name).read_bytes(),
                             (self.stage / "_internal" / name).read_bytes())
        self.assertEqual(managed.repair_managed_base_resources(self.target)["status"], "healthy")

    def test_only_one_missing_dependency_repaired_with_full_verification_and_idempotence(self):
        self.project()
        managed.repair_managed_base_resources(self.target)
        missing = self.target / "_internal/references/catalog.json"
        missing.unlink()
        preserved = {name: (self.target / "_internal" / name).stat().st_ino
                     for name in self.resources if name != "references/catalog.json"}
        result = managed.repair_managed_base_resources(self.target)
        self.assertEqual((result["changed"], result["checked"]), (1, 4))
        for name, inode in preserved.items():
            self.assertEqual((self.target / "_internal" / name).stat().st_ino, inode)
        state = self.target / managed.STATE
        identity = (state.stat().st_ino, state.stat().st_mtime_ns)
        managed.repair_managed_base_resources(self.target)
        self.assertEqual((state.stat().st_ino, state.stat().st_mtime_ns), identity)

    def upgrade_candidate(self):
        for name in self.resources:
            data = ("next:" + name).encode()
            self.write(self.stage / "_internal" / name, data)
            self.resources[name] = self.row(name, data)
        self.project()

    def test_known_previous_baseline_migrates_complete_set_preserving_user_data(self):
        self.project()
        managed.repair_managed_base_resources(self.target)
        user = {"assets/custom-maps/manifest.json": b"my editor",
                "work/nightmare-memory/ui-settings.json": b"my settings",
                "_internal/assets/custom-maps/route.json": b"my route"}
        for name, data in user.items():
            self.write(self.target / name, data)
        self.upgrade_candidate()
        self.assertEqual(managed.repair_managed_base_resources(self.target)["changed"], 4)
        for name, data in user.items():
            self.assertEqual((self.target / name).read_bytes(), data)
        for name in self.resources:
            self.assertEqual((self.target / "_internal" / name).read_bytes(), ("next:" + name).encode())

    def test_unknown_existing_file_blocks_whole_plan_before_any_publication(self):
        name = "_internal/references/catalog.json"
        self.write(self.target / name, b"unregistered-user-edit")
        self.project()
        with self.assertRaises(managed.ManagedBaseResourceError) as caught:
            managed.repair_managed_base_resources(self.target)
        self.assertEqual(caught.exception.issues[0]["reason"], "base-ownership-unknown")
        self.assertEqual(caught.exception.issues[0]["actual"]["sha256"], hashlib.sha256(b"unregistered-user-edit").hexdigest())
        self.assertEqual((self.target / name).read_bytes(), b"unregistered-user-edit")
        self.assertFalse((self.target / "_internal/difficult-native-manifest.json").exists())
        self.assertFalse((self.target / managed.STATE).exists())

    def receipt(self, *, dependencies=None, package="nightmare-suju", owned=None):
        name = "_internal/assets/suju/author.png"
        row = owned or self.row(name, b"author image")
        if owned is None:
            self.write(self.target / name, b"author image")
        value = dict(packageId=package, revision=1, archiveSha256="a" * 64, files=[row])
        if dependencies is not None:
            value["requiresFiles"] = dependencies
        data = json.dumps(value).encode()
        path = self.target / f"work/map-packages/{package}.json"
        self.write(path, data)
        return path, data

    def test_registered_author_receipt_and_images_preserved_when_dependencies_match(self):
        row = dict(self.resources["references/catalog.json"], path="_internal/references/catalog.json")
        path, original = self.receipt(dependencies=[row])
        self.project()
        self.assertEqual(managed.repair_managed_base_resources(self.target)["changed"], 4)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual((self.target / "_internal/assets/suju/author.png").read_bytes(), b"author image")

    def test_author_dependency_conflict_blocks_known_base_migration(self):
        self.project()
        managed.repair_managed_base_resources(self.target)
        row = dict(self.resources["references/catalog.json"], path="_internal/references/catalog.json")
        self.receipt(dependencies=[row])
        before = (self.target / row["path"]).read_bytes()
        self.upgrade_candidate()
        with self.assertRaisesRegex(managed.ManagedBaseResourceError, "installed-package-dependency-conflict"):
            managed.repair_managed_base_resources(self.target)
        self.assertEqual((self.target / row["path"]).read_bytes(), before)

    def test_old_receipt_without_dependencies_allows_missing_repair_but_blocks_replacement(self):
        self.receipt()
        self.project()
        managed.repair_managed_base_resources(self.target)
        self.upgrade_candidate()
        with self.assertRaisesRegex(managed.ManagedBaseResourceError, "installed-package-dependencies-unrecorded"):
            managed.repair_managed_base_resources(self.target)

    def test_receipt_owned_shared_resource_cannot_be_replaced_by_baseline(self):
        self.project()
        managed.repair_managed_base_resources(self.target)
        name = "references/difficult-native/difficult-map-01-1-shared.npz"
        owned = dict(self.resources[name], path="_internal/" + name)
        self.receipt(package="difficult-zhan10", owned=owned, dependencies=[])
        self.upgrade_candidate()
        with self.assertRaisesRegex(managed.ManagedBaseResourceError, "installed-package-owned-conflict"):
            managed.repair_managed_base_resources(self.target)

    def test_candidate_corruption_blocks_even_unrelated_missing_files(self):
        self.project()
        self.write(self.target / managed.CANDIDATE / "payload/_internal/references/catalog.json", b"corrupt")
        with self.assertRaisesRegex(managed.ManagedBaseResourceError, "candidate-integrity-mismatch"):
            managed.repair_managed_base_resources(self.target)
        self.assertFalse((self.target / "_internal/difficult-native-manifest.json").exists())

    def interrupt_publication(self, failure, *, after=2):
        real_link = managed.os.link
        count = 0
        def link(source, target, *args, **kwargs):
            nonlocal count
            if Path(target).is_relative_to(self.target / "_internal"):
                count += 1
                if count == after:
                    raise failure
            return real_link(source, target, *args, **kwargs)
        return mock.patch.object(managed.os, "link", side_effect=link)

    def test_mid_migration_failure_restores_all_old_files_and_state(self):
        self.project()
        managed.repair_managed_base_resources(self.target)
        before = {"_internal/" + name: (self.target / "_internal" / name).read_bytes() for name in self.resources}
        before[managed.STATE] = (self.target / managed.STATE).read_bytes()
        self.upgrade_candidate()
        with self.interrupt_publication(OSError("injected target write failure")):
            with self.assertRaisesRegex(OSError, "injected target write failure"):
                managed.repair_managed_base_resources(self.target)
        for name, data in before.items():
            self.assertEqual((self.target / name).read_bytes(), data)
        self.assertFalse((self.target / managed.PENDING).exists())

    def test_process_loss_retains_journal_then_recovery_restores_and_retry_succeeds(self):
        self.project()
        managed.repair_managed_base_resources(self.target)
        before = {name: (self.target / "_internal" / name).read_bytes() for name in self.resources}
        self.upgrade_candidate()
        with self.interrupt_publication(KeyboardInterrupt("simulated process loss")):
            with self.assertRaises(KeyboardInterrupt):
                managed.repair_managed_base_resources(self.target)
        self.assertTrue((self.target / managed.PENDING).exists())
        self.assertEqual(managed.recover_managed_base_resources(self.target)["status"], "rolled-back")
        for name, data in before.items():
            self.assertEqual((self.target / "_internal" / name).read_bytes(), data)
        self.assertEqual(managed.repair_managed_base_resources(self.target)["changed"], 4)

    def test_post_crash_unknown_edit_is_not_removed_or_overwritten(self):
        self.project()
        with self.interrupt_publication(KeyboardInterrupt("simulated process loss")):
            with self.assertRaises(KeyboardInterrupt):
                managed.repair_managed_base_resources(self.target)
        target = self.target / "_internal/difficult-native-manifest.json"
        target.unlink()
        target.write_bytes(b"external edit")
        with self.assertRaisesRegex(managed.ManagedBaseResourceError, "base-rollback-incomplete"):
            managed.recover_managed_base_resources(self.target)
        self.assertEqual(target.read_bytes(), b"external edit")
        self.assertTrue((self.target / managed.PENDING).exists())

    def test_hardlinks_unavailable_reports_before_any_active_resource_write(self):
        self.project()
        with mock.patch.object(managed.os, "link", side_effect=OSError("no hardlinks")):
            with self.assertRaisesRegex(ValueError, "resource-hardlinks-unavailable"):
                managed.repair_managed_base_resources(self.target)
        self.assertFalse((self.target / "_internal/difficult-native-manifest.json").exists())

    def test_ancestor_file_blocks_without_partial_publication(self):
        self.project()
        self.write(self.target / "_internal/references", b"not a directory")
        with self.assertRaises((OSError, managed.ManagedBaseResourceError)):
            managed.repair_managed_base_resources(self.target)
        self.assertFalse((self.target / "_internal/difficult-native-manifest.json").exists())

    def test_new_profile_missing_candidate_is_a_deployment_failure(self):
        self.write(self.target / "installation-profile.json", json.dumps(
            dict(resourceInstallationPolicy=build.RESOURCE_INSTALLATION_POLICY)).encode())
        with self.assertRaises(managed.ManagedBaseResourceError) as caught:
            managed.repair_managed_base_resources(self.target)
        self.assertEqual(caught.exception.code, "base-candidate-manifest-missing")
        self.assertEqual(caught.exception.details["issueCount"], 1)

    def test_real_handoff_and_helper_metadata_are_not_package_receipts(self):
        from client_import_handoff import ImportHandoff
        from client_resource_startup_recovery import _persist_pending
        import star_map_import_helper as helper
        import star_map_recovery_helper as recovery
        self.project()
        managed.repair_managed_base_resources(self.target)
        self.receipt(dependencies=[])
        handoff = ImportHandoff(self.target)
        with mock.patch.object(helper, "launch_import_helper", return_value=object()):
            request_id = handoff.launch(self.folder / "fixture.zip", {"archiveSha256": "a" * 64})
        result = dict(requestId=request_id, archiveSha256="a" * 64, success=True,
                      parentExit="exited-matching-process-handle", completedAtUTC="fixture", added=1)
        helper._write_receipt(self.target, result)
        self.assertTrue(handoff.read_receipt()["success"])
        plan = dict(transactionId="a" * 32, packageId="nightmare-suju", status="rolled-back", pending=False,
                    fileCount=1, blockedPaths=[], planSha256="b" * 64)
        with mock.patch.object(helper, "_launch_parent_bound_helper", return_value=object()):
            recovery.launch_recovery_helper(self.target / "star-map-recovery-helper.exe", self.target, plan,
                                            "recover", "isolated-fixture")
        _persist_pending(self.target, plan, "isolated-fixture")
        helper._write_receipt(self.target, dict(success=True), path=recovery.receipt_path(self.target))
        before = {path.name: path.read_bytes() for path in (self.target / "work/map-packages").glob("*.json")}
        self.assertEqual(managed.repair_managed_base_resources(self.target)["status"], "healthy")
        for name, data in before.items():
            self.assertEqual((self.target / "work/map-packages" / name).read_bytes(), data)

    def test_unknown_json_still_blocks_as_unrecognized_receipt(self):
        self.project()
        self.write(self.target / "work/map-packages/unknown-author.json", b"{}")
        with self.assertRaisesRegex(managed.ManagedBaseResourceError, "unrecognized-installed-package-receipt"):
            managed.repair_managed_base_resources(self.target)

    def test_real_import_completion_acknowledgement_survives_next_base_repair(self):
        from PySide6.QtCore import QCoreApplication
        from client_resources import ResourceService
        application = QCoreApplication.instance() or QCoreApplication([])
        # Use the real source-mode ownership gate, not a patched validator.
        with tempfile.TemporaryDirectory(prefix="managed-base-ack-", dir=ROOT / "work") as folder:
            self.target = Path(folder) / "installation"
            self.project()
            self.write(self.target / ".qt-lab-owner", b"qt-quick-isolated-v1")
            managed.repair_managed_base_resources(self.target)
            service = ResourceService(self.target)
            try:
                service.recordImportReceipt(dict(success=True, requestId="completed-import-fixture",
                    packageId="nightmare-suju", added=1, alreadyPresent=0))
                service.acknowledgeImportCompletion()
                path = self.target / "work/map-packages/qt-import-notice-seen.json"
                before = path.read_bytes()
                self.assertEqual(json.loads(before)["requestId"], "completed-import-fixture")
                self.assertEqual(managed.repair_managed_base_resources(self.target)["status"], "healthy")
                self.assertEqual(path.read_bytes(), before)
            finally:
                service.shutdown()
                service.pool.waitForDone(1000)
                service.deleteLater()
                application.processEvents()

    def test_legacy_tk_import_request_is_preserved_as_handoff_metadata(self):
        self.project()
        path = self.target / "work/map-packages/main-import-request.json"
        data = json.dumps(dict(requestId="legacy-fixture", archiveSha256="a" * 64)).encode()
        self.write(path, data)
        self.assertEqual(managed.repair_managed_base_resources(self.target)["status"], "repaired")
        self.assertEqual(path.read_bytes(), data)

    def test_author_pending_kinds_keep_their_distinct_recovery_codes(self):
        self.project()
        for name, code in (("pending-upgrade.json", "author-upgrade-recovery-required"),
                           ("pending-additions.json", "additions-recovery-required"),
                           ("pending-removal.json", "removal-recovery-required")):
            path = self.target / "work/map-packages" / name
            self.write(path, b"{}")
            with self.assertRaises(managed.ManagedBaseResourceError) as caught:
                managed.repair_managed_base_resources(self.target)
            self.assertEqual(caught.exception.code, code)
            path.unlink()

    def test_installer_uses_shared_os_lock_without_active_base_plane_skip(self):
        recipe = (ROOT / "tools/qt-client.iss").read_text("utf-8")
        self.assertNotIn("InstallBaseResourcePlane", recipe)
        self.assertNotIn("RESOURCE_BASE_PRESERVE_EXISTING_WHOLE_PLANE", recipe)
        self.assertIn("\\work\\map-packages\\import.lock", recipe)
        self.assertIn("CreateFileW(LockPath, $C0000000, 0, 0, 4, $80, 0)", recipe)
        self.assertIn("ReleaseResourceLock;", recipe)

    @unittest.skipUnless(os.name == "nt", "native Windows sharing flags")
    def test_installer_createfile_share_zero_and_runtime_lease_exclude_each_other(self):
        import ctypes
        from ctypes import wintypes
        from resource_transaction import resource_runtime_lease, ResourceTransactionError
        self.target.mkdir()
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        api.CreateFileW.restype = wintypes.HANDLE
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        lock = self.target / "work/map-packages/import.lock"
        def open_installer_lock():
            return api.CreateFileW(str(lock), 0xC0000000, 0, None, 4, 0x80, None)
        with resource_runtime_lease(self.target):
            self.assertEqual(open_installer_lock(), ctypes.c_void_p(-1).value)
        handle = open_installer_lock()
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
        try:
            with self.assertRaisesRegex(ResourceTransactionError, "resource-busy"):
                resource_runtime_lease(self.target)
        finally:
            api.CloseHandle(handle)
        with resource_runtime_lease(self.target):
            pass


if __name__ == "__main__":
    unittest.main()
