"""Real ZIPs/Windows writer lock; deletion is restricted to temporary fixtures."""
import json
from pathlib import Path
import sys
import unittest
import zipfile
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "app"), str(Path(__file__).parent)]
import package_lifecycle as lifecycle
import map_package_upgrade as upgrade
import star_map_import as importer
import author_package_management as manager
from test_live_author_import import LiveAuthorImportTests, SUJU, NATIVE, CORE, row


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.f = LiveAuthorImportTests("runTest")
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.root = self.f.root

    def install(self, payload=None, name="old.zip"):
        return self.f.live(self.f.package("nightmare-suju", payload or {SUJU: b"old"}, name))

    def cleanup(self, package=None, **kw):
        plan = lifecycle.inspect_cleanup(self.root, package)
        return lifecycle.cleanup_live(self.root, package, application_idle=True,
            expected_plan_sha256=plan["planSha256"], **kw)

    def assert_no_backups(self):
        self.assertFalse(list((self.root / lifecycle.STATE).glob("*/journal.json")))
        self.assertFalse(list((self.root / "work/map-package-removals").glob("*/installed-receipt.json")))

    def test_single_uninstall_keeps_edits_neighbours_core_and_other_mode(self):
        edited = "_internal/assets/suju/edited.png"
        self.install({SUJU:b"old", edited:b"original"})
        self.f.write(edited, b"user-art")
        self.f.write("_internal/assets/suju/user.png", b"unknown")
        self.f.live(self.f.package("difficult-zhan10", {NATIVE:b"other-mode", CORE:self.f.core_bytes}, "difficult.zip"))
        result = self.cleanup("nightmare-suju")
        self.assertFalse((self.root / SUJU).exists())
        self.assertFalse(self.f.receipt().exists())
        self.assertEqual((self.root / edited).read_bytes(), b"user-art")
        self.assertEqual((self.root / NATIVE).read_bytes(), b"other-mode")
        self.assertEqual({r["path"] for r in result["residuals"]}, {edited, "_internal/assets/suju/user.png"})
        self.f.assert_base_preserved()
        self.assert_no_backups()

    def test_update_replaces_same_scope_retires_old_files_and_clears_temporary_objects(self):
        obsolete = "_internal/assets/suju/old-only.png"
        self.install({SUJU:b"old", obsolete:b"old-only"})
        result = self.install({SUJU:b"new"}, "new.zip")
        self.assertEqual(result["replaced"], 1)
        self.assertEqual(result["removed"], 1)
        self.assertEqual((self.root / SUJU).read_bytes(), b"new")
        self.assertFalse((self.root / obsolete).exists())
        self.assert_no_backups()
        self.f.assert_base_preserved()

    def test_update_does_not_delete_edited_obsolete_file(self):
        obsolete = "_internal/assets/suju/old-only.png"
        self.install({SUJU:b"old", obsolete:b"original"})
        self.f.write(obsolete, b"player-edit")
        with self.assertRaisesRegex(importer.ImportRejected, "existing-data-conflict"):
            self.install({SUJU:b"new"}, "new.zip")
        self.assertEqual((self.root / SUJU).read_bytes(), b"old")
        self.assertEqual((self.root / obsolete).read_bytes(), b"player-edit")

    def test_cancel_after_move_restores_exact_files_and_receipt(self):
        self.install()
        old_receipt = self.f.receipt().read_bytes()
        moved = []
        real = upgrade._move_owned
        def move(*args):
            real(*args)
            moved.append(True)
        with patch.object(upgrade, "_move_owned", side_effect=move):
            with self.assertRaisesRegex(importer.ImportRejected, "import-cancelled"):
                self.cleanup("nightmare-suju", cancelled=lambda: bool(moved))
        self.assertEqual((self.root / SUJU).read_bytes(), b"old")
        self.assertEqual(self.f.receipt().read_bytes(), old_receipt)
        self.assert_no_backups()

    def test_purge_failure_can_retry_but_never_restore_committed_uninstall(self):
        self.install()
        real = importer._rollback_published
        def fail_old(path, *args):
            if "/lifecycle/" in Path(path).as_posix() and "/old/" in Path(path).as_posix():
                raise OSError("fixture held cleanup object")
            return real(path, *args)
        with patch.object(importer, "_rollback_published", side_effect=fail_old):
            result = self.cleanup("nightmare-suju")
        self.assertTrue(result["cleanupPending"])
        self.assertFalse((self.root / SUJU).exists())
        with self.assertRaisesRegex(importer.ImportRejected, "package-cleanup-pending"):
            importer.recover_package_upgrade(self.root, application_stopped=True)
        self.assertFalse(self.cleanup()["cleanupPending"])
        self.assertFalse((self.root / SUJU).exists())
        self.assert_no_backups()

    def test_confirmation_and_shared_ownership_do_not_broaden(self):
        self.install()
        plan = lifecycle.inspect_cleanup(self.root, "nightmare-suju")
        self.f.write(SUJU, b"changed-after-confirmation")
        with self.assertRaisesRegex(importer.ImportRejected, "reinspect"):
            lifecycle.cleanup_live(self.root, "nightmare-suju", application_idle=True,
                expected_plan_sha256=plan["planSha256"])
        self.assertEqual((self.root / SUJU).read_bytes(), b"changed-after-confirmation")

    def test_all_cleanup_uses_receipts_and_legacy_hashes_for_mixed_history(self):
        self.install()
        receipt = json.loads(self.f.receipt().read_text())
        folder = "work/map-package-removals/nightmare-suju-old"
        self.f.write(folder + "/installed-receipt.json", json.dumps(receipt).encode())
        self.f.write(folder + "/" + SUJU, b"old")
        legacy = "_internal/assets/suju/hidden-old.png"
        self.f.write(legacy, b"known-legacy")
        source = "work/installer-backups/old"
        # Inno's ANSI index mixes program files, resource bytes and settings.
        self.f.write(source + "/existing.txt", ("program.exe\r\n"+SUJU.replace("/", "\\")+"\r\nwork\\nightmare-memory\\ui-settings.json\r\n").encode("ascii"))
        self.f.write(source + "/files/0", b"program")
        self.f.write(source + "/files/1", b"old")
        self.f.write(source + "/files/2", b"settings")
        package = {"nightmare-suju": dict(archiveSha256="a"*64, files=[row(legacy, b"known-legacy")])}
        with patch.object(lifecycle, "LEGACY_AUTHOR_INVENTORY", package):
            result = self.cleanup()
        for target in [SUJU, legacy, folder + "/" + SUJU, folder + "/installed-receipt.json", source + "/files/1"]:
            self.assertFalse((self.root / target).exists(), target)
        self.assertEqual((self.root / source / "files/0").read_bytes(), b"program")
        self.assertEqual((self.root / source / "files/2").read_bytes(), b"settings")
        self.assertTrue((self.root / source / "existing.txt").is_file())
        self.assertFalse(result["cleanupPending"])
        self.f.assert_base_preserved()

    def test_old_pending_author_upgrade_is_removed_without_future_resurrection(self):
        self.install()
        archive = self.f.package("nightmare-suju", {SUJU:b"new"}, "new.zip")
        real = upgrade.os.link
        def interrupt(source, target, *args, **kwargs):
            if Path(target) == self.f.receipt():
                raise KeyboardInterrupt("fixture process loss before receipt")
            return real(source, target, *args, **kwargs)
        def legacy(root, result, stage, *, cancelled=None):
            return upgrade.commit_upgrade(root, result, stage, live=True, cancelled=cancelled)
        with patch.object(lifecycle, "replace_author_locked", side_effect=legacy), patch.object(upgrade.os, "link", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.f.live(archive)
        self.assertTrue((self.root / upgrade.PENDING).exists())
        self.cleanup()
        self.assertFalse((self.root / upgrade.PENDING).exists())
        self.assertFalse((self.root / SUJU).exists())
        self.assertEqual(importer.recover_package_upgrade(self.root, application_stopped=True)["status"], "no-pending-upgrade")

    def test_unindexed_pending_upgrade_blocks_cleanup(self):
        self.install()
        self.f.write(upgrade.PENDING, b'{"unknown":true}')
        with self.assertRaisesRegex(importer.ImportRejected, "pending-upgrade-unverified"):
            lifecycle.inspect_cleanup(self.root)
        self.assertEqual((self.root / SUJU).read_bytes(), b"old")

    def test_first_journal_write_failure_leaves_no_import_blocker(self):
        self.install()
        with patch.object(lifecycle, "_save_journal", side_effect=OSError("fixture disk full")):
            with self.assertRaisesRegex(OSError, "fixture disk full"):
                self.cleanup("nightmare-suju")
        lifecycle.assert_no_pending(self.root)
        self.assertEqual((self.root / SUJU).read_bytes(), b"old")
        self.install({SUJU:b"new"}, "new.zip")
        self.assert_no_backups()

    def test_final_directory_failure_is_retryable_without_journal(self):
        self.install()
        original = Path.rmdir
        failed = []
        def fail_final(path):
            if path.parent == self.root / lifecycle.STATE and not failed:
                failed.append(True)
                raise OSError("fixture final directory busy")
            return original(path)
        with patch.object(Path, "rmdir", fail_final):
            self.assertTrue(self.cleanup("nightmare-suju")["cleanupPending"])
        directory = next((self.root / lifecycle.STATE).iterdir())
        self.assertFalse((directory / "journal.json").exists())
        plan = lifecycle.inspect_cleanup(self.root, pending_only=True)
        result = lifecycle.cleanup_live(self.root, application_idle=True, pending_only=True,
                                        expected_plan_sha256=plan["planSha256"])
        self.assertFalse(result["cleanupPending"])
        self.assertFalse((self.root / SUJU).exists())
        self.assert_no_backups()

    def test_misfiled_receipts_never_contribute_ownership(self):
        self.install()
        data = self.f.receipt().read_bytes()
        self.f.receipt().unlink()
        self.f.write("work/map-packages/difficult-zhan10.json", data)
        self.f.write("work/map-package-removals/difficult-zhan10-old/installed-receipt.json", data)
        plan = lifecycle.inspect_cleanup(self.root)
        self.assertNotIn(SUJU, {r["path"] for r in plan["files"]})
        self.assertTrue(any("package-mismatch" in r["reason"] for r in plan["residuals"]))
        self.cleanup()
        self.assertEqual((self.root / SUJU).read_bytes(), b"old")

    def test_unknown_nonempty_transaction_without_journal_is_not_swept(self):
        import uuid
        path = lifecycle.STATE + "/" + str(uuid.uuid4()) + "/old/user-art.png"
        self.f.write(path, b"unknown")
        with self.assertRaisesRegex(importer.ImportRejected, "unverified-lifecycle-initialization"):
            lifecycle.inspect_cleanup(self.root)
        self.assertEqual((self.root / path).read_bytes(), b"unknown")

    def test_interrupted_initial_journal_publication_can_be_finished_safely(self):
        import uuid
        self.install()
        original_receipt = self.f.receipt().read_bytes()
        def interrupt(directory, journal):
            (directory / ("journal-" + str(uuid.uuid4()) + ".tmp")).write_bytes(lifecycle._encoded(journal))
            raise KeyboardInterrupt("fixture process loss before first journal rename")
        with patch.object(lifecycle, "_save_journal", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.cleanup("nightmare-suju")
        plan = lifecycle.inspect_cleanup(self.root, pending_only=True)
        result = lifecycle.cleanup_live(self.root, application_idle=True, pending_only=True,
                                        expected_plan_sha256=plan["planSha256"])
        self.assertFalse(result["cleanupPending"])
        self.assertEqual((self.root / SUJU).read_bytes(), b"old")
        self.assertEqual(self.f.receipt().read_bytes(), original_receipt)
        self.assert_no_backups()

    def test_stable_scope_and_content_version_are_distinct_from_revision_one(self):
        def archive(name, data, **fields):
            target = self.f.package("nightmare-suju", {SUJU:data}, name)
            with zipfile.ZipFile(target) as handle:
                payload = {info.filename: handle.read(info) for info in handle.infolist()}
            manifest = json.loads(payload[importer.MANIFEST])
            manifest.update(fields)
            payload[importer.MANIFEST] = json.dumps(manifest).encode()
            with zipfile.ZipFile(target, "w") as handle:
                for path, value in payload.items():
                    handle.writestr(path, value)
            return target
        self.f.live(archive("v2.zip", b"v2", contentVersion=2))
        for name, fields, reason in [
            ("v1.zip", dict(contentVersion=1), "downgrade"),
            ("legacy.zip", {}, "version-required"),
            ("same-version.zip", dict(contentVersion=2), "version-conflict"),
            ("wrong-mode.zip", dict(contentVersion=3, mode="difficult"), "scope-mismatch"),
            ("wrong-profile.zip", dict(contentVersion=3, profileId="somebody-else"), "scope-mismatch")]:
            with self.subTest(name=name):
                with self.assertRaisesRegex(importer.ImportRejected, reason):
                    self.f.live(archive(name, b"changed", **fields))
                self.assertEqual((self.root / SUJU).read_bytes(), b"v2")
        self.f.live(archive("v3.zip", b"v3", contentVersion=3))
        receipt = json.loads(self.f.receipt().read_text())
        self.assertEqual((receipt["revision"], receipt["contentVersion"]), (1, 3))
        self.assert_no_backups()

    def test_receipt_published_before_commit_marker_failure_never_rolls_back_update(self):
        self.install()
        original = lifecycle._save_journal
        def fail_commit(directory, journal):
            if journal["status"] == "committed":
                raise OSError("fixture commit marker failure")
            original(directory, journal)
        with patch.object(lifecycle, "_save_journal", side_effect=fail_commit):
            result = self.install({SUJU:b"new"}, "new.zip")
        self.assertTrue(result["cleanupPending"])
        self.assertEqual((self.root / SUJU).read_bytes(), b"new")
        plan = lifecycle.inspect_cleanup(self.root, pending_only=True)
        result = lifecycle.cleanup_live(self.root, application_idle=True, pending_only=True,
                                        expected_plan_sha256=plan["planSha256"])
        self.assertFalse(result["cleanupPending"])
        self.assertEqual((self.root / SUJU).read_bytes(), b"new")
        self.assert_no_backups()


if __name__ == "__main__":
    unittest.main()
