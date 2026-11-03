"""Synthetic approved legacy bytes, real ZIP/locks/upgrade/removal transactions."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import author_package_management as manager
import map_package_removal as removal
import star_map_import as importer
import test_live_author_import as fixture


class LegacyImportRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.LiveAuthorImportTests("runTest")
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.package = "nightmare-suju"
        self.path = fixture.SUJU
        self.inventory = {self.package: dict(archiveSha256="a" * 64, archiveName="approved-old.zip",
            files=[fixture.row(self.path, b"approved-old")])}
        stub = patch.object(manager, "LEGACY_AUTHOR_INVENTORY", self.inventory)
        stub.start()
        self.addCleanup(stub.stop)
        self.f.write(self.path, b"approved-old")
        self.archive = self.f.package(self.package, {self.path: b"new"})

    def test_direct_inspection_recognizes_approved_legacy_without_registering_or_writing(self):
        plan = importer.inspect_package(self.archive, self.f.root)
        self.assertEqual(plan["replaceFiles"], [fixture.row(self.path, b"approved-old")])
        self.assertRegex(plan["legacyReceiptSha256"], "^[0-9a-f]{64}$")
        self.assertIsNone(plan["previousReceiptSha256"])
        self.assertFalse(self.f.receipt().exists())
        self.assertEqual((self.f.root / self.path).read_bytes(), b"approved-old")

    def test_direct_import_upgrades_then_deletes_all_managed_copies(self):
        result = self.f.live(self.archive)
        self.assertEqual(result["status"], "author-upgraded-live")
        self.assertEqual((self.f.root / self.path).read_bytes(), b"new")
        self.assertEqual(list((self.f.root / "work/map-packages").glob("staging-*")), [])
        history = self.f.root / "work/map-packages/upgrades" / result["upgradeTransactionId"]
        self.assertFalse((history / "backup").exists())
        plan = removal.inspect_package_removal(self.f.root, self.package)
        result = removal.remove_packages(self.f.root, self.package, application_idle=True,
            expected_plan_sha256=plan["planSha256"])
        self.assertEqual(result["status"], "packages-removed")
        self.assertFalse((self.f.root / self.path).exists())
        self.assertFalse(self.f.receipt().exists())
        self.assertFalse(history.exists())
        self.f.assert_base_preserved()

    def test_incomplete_legacy_set_still_conflicts(self):
        self.inventory[self.package]["files"].append(fixture.row("_internal/assets/suju/other.png", b"other"))
        with self.assertRaisesRegex(importer.ImportRejected, "existing-data-conflict"):
            self.f.live(self.archive)
        self.assertFalse(self.f.receipt().exists())
        self.assertEqual((self.f.root / self.path).read_bytes(), b"approved-old")

    def test_partial_incoming_package_does_not_orphan_approved_old_files(self):
        second = "_internal/assets/suju/other.png"
        self.inventory[self.package]["files"].append(fixture.row(second, b"other"))
        self.f.write(second, b"other")
        with self.assertRaisesRegex(importer.ImportRejected, "existing-data-conflict"):
            self.f.live(self.archive)
        self.assertFalse(self.f.receipt().exists())
        self.assertEqual((self.f.root / second).read_bytes(), b"other")

    def test_difficult_legacy_uses_same_shared_upgrade(self):
        package, path = "difficult-zhan10", fixture.NATIVE
        self.inventory[package] = dict(archiveSha256="b" * 64, archiveName="difficult-old.zip",
            files=[fixture.row(path, b"approved-difficult")])
        self.f.write(path, b"approved-difficult")
        archive = self.f.package(package, {path: b"new-difficult"}, "difficult-new.zip")
        self.assertEqual(importer.inspect_package(archive, self.f.root)["replaceFiles"],
                         [fixture.row(path, b"approved-difficult")])
        result = self.f.live(archive)
        self.assertEqual(result["status"], "author-upgraded-live")
        self.assertEqual((self.f.root / path).read_bytes(), b"new-difficult")
        self.f.assert_base_preserved()

    def test_raced_receipt_after_validation_is_preserved(self):
        real = importer.preflight_hardlinks
        def raced(*args, **kwargs):
            real(*args, **kwargs)
            self.f.write("work/map-packages/nightmare-suju.json", b"unknown-raced-receipt")
        with patch.object(importer, "preflight_hardlinks", side_effect=raced):
            with self.assertRaises(importer.ImportRejected):
                self.f.live(self.archive)
        self.assertEqual(self.f.receipt().read_bytes(), b"unknown-raced-receipt")
        self.assertEqual((self.f.root / self.path).read_bytes(), b"approved-old")

    def test_modified_or_unknown_legacy_bytes_still_conflict(self):
        for value in (b"user-edit", b"unrecognized-release"):
            self.f.write(self.path, value)
            with self.assertRaisesRegex(importer.ImportRejected, "existing-data-conflict"):
                self.f.live(self.archive)
            self.assertEqual((self.f.root / self.path).read_bytes(), value)
            self.assertFalse(self.f.receipt().exists())

    def test_existing_receipt_never_falls_back_to_legacy_authority(self):
        self.f.write("work/map-packages/nightmare-suju.json", json.dumps(dict(packageId=self.package,
            revision=1, archiveSha256="b" * 64, files=[fixture.row(self.path, b"different-version")])).encode())
        before = self.f.receipt().read_bytes()
        with self.assertRaisesRegex(importer.ImportRejected, "existing-data-conflict"):
            self.f.live(self.archive)
        self.assertEqual(self.f.receipt().read_bytes(), before)
        self.assertEqual((self.f.root / self.path).read_bytes(), b"approved-old")

    def test_bad_archive_never_registers_legacy_receipt(self):
        archive = self.f.package(self.package, {self.path: b"new"}, "bad.zip", corrupt_path=self.path)
        with self.assertRaisesRegex(importer.ImportRejected, "integrity-mismatch"):
            self.f.live(archive)
        self.assertFalse(self.f.receipt().exists())

    def test_cancellation_before_adoption_keeps_legacy_files_unregistered(self):
        with self.assertRaisesRegex(importer.ImportRejected, "cancelled"):
            self.f.live(self.archive, cancelled=lambda: True)
        self.assertFalse(self.f.receipt().exists())

    def test_no_hardlink_support_does_not_register_before_preflight(self):
        with patch.object(importer, "preflight_hardlinks", side_effect=OSError("fixture-preflight-denied")):
            with self.assertRaisesRegex(OSError, "preflight-denied"):
                self.f.live(self.archive)
        self.assertFalse(self.f.receipt().exists())

    def test_unknown_neighbour_is_not_claimed_or_cleaned(self):
        unknown = "_internal/assets/suju/user-note.png"
        self.f.write(unknown, b"private-user-note")
        self.f.live(self.archive)
        self.assertEqual((self.f.root / unknown).read_bytes(), b"private-user-note")
        self.assertNotIn(unknown, [r["path"] for r in json.loads(self.f.receipt().read_bytes())["files"]])

    def test_legacy_bytes_changed_after_validation_are_not_registered_or_overwritten(self):
        real = importer.preflight_hardlinks
        def changed(*args, **kwargs):
            real(*args, **kwargs)
            self.f.write(self.path, b"changed-after-validation")
        with patch.object(importer, "preflight_hardlinks", side_effect=changed):
            with self.assertRaisesRegex(importer.ImportRejected, "legacy.*changed"):
                self.f.live(self.archive)
        self.assertFalse(self.f.receipt().exists())
        self.assertEqual((self.f.root / self.path).read_bytes(), b"changed-after-validation")

    def test_receipt_swapped_after_adoption_does_not_expand_upgrade_authority(self):
        real = manager._adopt_legacy_author_package_locked
        replacement = None
        def raced(*args, **kwargs):
            nonlocal replacement
            result = real(*args, **kwargs)
            replacement = self.f.receipt().read_bytes() + b"\n"
            self.f.receipt().write_bytes(replacement)
            return result
        with patch.object(manager, "_adopt_legacy_author_package_locked", side_effect=raced):
            with self.assertRaisesRegex(importer.ImportRejected, "upgrade-old-receipt-changed"):
                self.f.live(self.archive)
        self.assertEqual(self.f.receipt().read_bytes(), replacement)
        self.assertEqual((self.f.root / self.path).read_bytes(), b"approved-old")

    def test_failed_upgrade_restores_original_legacy_bytes(self):
        import map_package_upgrade as upgrade
        real = upgrade.os.link
        def fail(source, target, *args, **kwargs):
            if Path(target) == self.f.root / self.path and "/new/" in Path(source).as_posix():
                raise OSError("fixture-upgrade-denied")
            return real(source, target, *args, **kwargs)
        with patch.object(upgrade.os, "link", side_effect=fail):
            with self.assertRaisesRegex(OSError, "upgrade-denied"):
                self.f.live(self.archive)
        self.assertEqual((self.f.root / self.path).read_bytes(), b"approved-old")
        self.assertTrue(json.loads(self.f.receipt().read_bytes())["legacyAdoption"]["completeDisplaySet"])
        self.assertFalse((self.f.root / upgrade.PENDING).exists())


if __name__ == "__main__":
    unittest.main()
