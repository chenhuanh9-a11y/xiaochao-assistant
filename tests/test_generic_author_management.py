"""Synthetic V2 install/removal/recovery through actual production file transactions."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import test_generic_author_package as fixtures
import author_package_management as legacy
import map_package_removal as removal
import star_map_import as importer
from resource_transaction import resource_write_lock


class GenericManagementTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.GenericPackageTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.archive = self.f.package()
        self.f.install(self.archive)

    def remove(self):
        plan = removal.inspect_package_removal(self.f.root, self.f.profile)
        return removal.remove_packages(self.f.root, self.f.profile, application_idle=True,
            expected_plan_sha256=plan['planSha256'])

    def check_base(self):
        for dep in self.f.deps:
            self.assertEqual(importer._sha(self.f.root / dep['path']), dep['sha256'])

    def test_dynamic_snapshot_remove_repeat_reimport(self):
        rows = removal.managed_packages_snapshot(self.f.root)
        self.assertEqual([(r['packageId'], r['title'], r['season'], r['mode']) for r in rows],
            [(self.f.profile, '新作者整图', 's0', 'nightmare')])
        legacy_rows = legacy.managed_author_packages_snapshot(self.f.root)
        self.assertTrue(next(r for r in legacy_rows if r['packageId'] == self.f.profile)['canRemove'])
        self.assertEqual(self.remove()['status'], 'packages-removed')
        self.assertEqual(self.remove()['status'], 'already-removed')
        self.assertFalse(removal.managed_packages_snapshot(self.f.root))
        self.f.install(self.archive)
        self.assertTrue(self.f.install(self.archive)['receiptPreserved'])
        self.check_base()

    def test_interrupted_removal_restores_original_objects(self):
        receipt = self.f.root / f'work/map-packages/{self.f.profile}.json'
        before = receipt.read_bytes()
        original = importer._rollback_published
        calls = []
        def interrupt(*args):
            result = original(*args)
            calls.append(args)
            if len(calls) == 1:
                raise KeyboardInterrupt('simulated process loss')
            return result
        with patch.object(importer, '_rollback_published', side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.remove()
        with resource_write_lock(self.f.root, wait=True):
            pending = removal.inspect_pending_removal_locked(self.f.root)
            self.assertEqual(pending['packageId'], self.f.profile)
            result = removal.recover_pending_removal_locked(self.f.root)
        self.assertEqual(result['status'], 'removal-rolled-back')
        self.assertEqual(receipt.read_bytes(), before)
        self.assertTrue(self.f.install(self.archive)['receiptPreserved'])
        self.check_base()

    def test_legacy_backup_remnants_discovered_and_removed(self):
        plan = legacy.inspect_author_package_removal(self.f.root, self.f.profile)
        result = legacy.remove_author_package_live(self.f.root, self.f.profile, application_idle=True,
            expected_receipt_sha256=plan['receiptSha256'], expected_plan_sha256=plan['planSha256'])
        backup = Path(result['backupPath'])
        rows = removal.managed_packages_snapshot(self.f.root)
        self.assertEqual([(r['packageId'], r['status']) for r in rows],
                         [(self.f.profile, 'managed-remnants')])
        self.assertEqual(self.remove()['status'], 'packages-removed')
        self.assertFalse((backup / 'installed-receipt.json').exists())
        self.check_base()
