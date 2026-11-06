"""Receipt contract changes must not disappear behind identical resource bytes."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import star_map_import as importer
import map_package_upgrade as upgrade
import author_package_management as manager
import test_live_author_import as fixture


class ReceiptDependencyContractTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.LiveAuthorImportTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.sidecar = '_internal/references/author-packs/wang-qingxin/catalog.json'
        self.f.live(self.f.package('nightmare-wang-qingxin',
            {fixture.WANG: b'old-image', self.sidecar: b'old-catalog'}, 'wang.zip'))
        self.original = self.f.package('nightmare-suju', {fixture.SUJU: b'suju'}, 'old.zip')
        self.f.live(self.original)
        self.before = self.f.receipt().read_bytes()
        self.f.dependencies.append(fixture.row(self.sidecar, b'old-catalog'))
        self.updated = self.f.package('nightmare-suju', {fixture.SUJU: b'suju'}, 'new.zip')
        self.f.dependencies.pop()

    def test_same_files_new_dependencies_are_explicitly_rejected_without_mutation(self):
        for operation in (lambda: importer.inspect_package(self.updated, self.f.root),
                          lambda: self.f.live(self.updated)):
            with self.assertRaisesRegex(importer.ImportRejected, 'dependency-contract-changed'):
                operation()
            self.assertEqual(self.f.receipt().read_bytes(), self.before)
            self.assertEqual((self.f.root / fixture.SUJU).read_bytes(), b'suju')
            self.assertFalse((self.f.root / upgrade.PENDING).exists())
        self.assertEqual(list((self.f.root / 'work/map-packages').glob('staging-*')), [])

    def test_removing_dependency_from_same_files_is_also_rejected(self):
        self.f.dependencies.append(fixture.row(self.sidecar, b'old-catalog'))
        update = self.f.package('nightmare-suju', {fixture.SUJU: b'new-image'}, 'with-dependency.zip')
        self.f.live(update)
        self.f.dependencies.pop()
        before = self.f.receipt().read_bytes()
        reduced = self.f.package('nightmare-suju', {fixture.SUJU: b'new-image'}, 'without-dependency.zip')
        with self.assertRaisesRegex(importer.ImportRejected, 'dependency-contract-changed'):
            self.f.live(reduced)
        self.assertEqual(self.f.receipt().read_bytes(), before)

    def test_same_complete_contract_still_preserves_receipt_inode(self):
        identity = self.f.receipt().stat().st_ino
        result = self.f.live(self.original)
        self.assertTrue(result['receiptPreserved'])
        self.assertEqual(self.f.receipt().read_bytes(), self.before)
        self.assertEqual(self.f.receipt().stat().st_ino, identity)

    def test_rejected_metadata_change_never_starts_upgrade_or_addition_transaction(self):
        with patch.object(upgrade, 'commit_upgrade', side_effect=AssertionError('unexpected upgrade')):
            with patch.object(upgrade, 'prepare_additions', side_effect=AssertionError('unexpected additions')):
                with self.assertRaisesRegex(importer.ImportRejected, 'dependency-contract-changed'):
                    self.f.live(self.updated)
        self.assertEqual(self.f.receipt().read_bytes(), self.before)

    def test_cancelled_receipt_only_update_keeps_previous_contract(self):
        with self.assertRaisesRegex(importer.ImportRejected, 'cancelled'):
            self.f.live(self.updated, cancelled=lambda: True)
        self.assertEqual(self.f.receipt().read_bytes(), self.before)

    def test_ordinary_receipt_missing_empty_or_incomplete_dependencies_fails_closed(self):
        for dependency_value in ('missing', [], self.f.dependencies[:1]):
            with self.subTest(dependency_value=dependency_value):
                value = json.loads(self.before)
                if dependency_value == 'missing':
                    value.pop('requiresFiles')
                else:
                    value['requiresFiles'] = dependency_value
                self.f.receipt().write_text(json.dumps(value))
                corrupt = self.f.receipt().read_bytes()
                with self.assertRaises(importer.ImportRejected):
                    self.f.live(self.original)
                self.assertEqual(self.f.receipt().read_bytes(), corrupt)

    def test_other_ordinary_receipt_cannot_erase_dependency_protection(self):
        for missing in (True, False):
            self.f.receipt().write_bytes(self.before)
            value = json.loads(self.before)
            value["requiresFiles"].append(fixture.row(self.sidecar, b"old-catalog"))
            if missing:
                value.pop('requiresFiles')
            else:
                value['requiresFiles'] = []
            self.f.receipt().write_text(json.dumps(value))
            wang = self.f.package('nightmare-wang-qingxin',
                {fixture.WANG: b'new-image', self.sidecar: b'new-catalog'}, 'wang-new.zip')
            with self.assertRaises(importer.ImportRejected):
                self.f.live(wang)
            self.assertEqual((self.f.root / self.sidecar).read_bytes(), b'old-catalog')

    def test_forged_legacy_marker_does_not_exempt_normal_receipt(self):
        value = json.loads(self.before)
        value.pop('requiresFiles')
        value['legacyAdoption'] = dict(schemaVersion=1, scope='author-display-only',
            sourceArchive='anything.zip', completeDisplaySet=True)
        self.f.receipt().write_text(json.dumps(value))
        with self.assertRaises(importer.ImportRejected):
            self.f.live(self.original)

    def test_verified_legacy_adoption_still_allows_real_resource_upgrade(self):
        self.f.receipt().unlink()
        inventory = {'nightmare-suju': dict(archiveSha256='a' * 64, archiveName='approved.zip',
            files=[fixture.row(fixture.SUJU, b'suju')])}
        with patch.object(manager, 'LEGACY_AUTHOR_INVENTORY', inventory):
            manager.adopt_legacy_author_package(self.f.root, 'nightmare-suju')
            receipt = self.f.receipt().read_bytes()
            self.assertEqual(importer._installed_inventory(self.f.root, 'nightmare-suju')[1], {})
            with self.assertRaisesRegex(importer.ImportRejected, 'dependency-contract-changed'):
                self.f.live(self.original)
            self.assertEqual(self.f.receipt().read_bytes(), receipt)
            for field, changed in (('archiveSha256', 'b' * 64), ('files', [fixture.row(fixture.SUJU, b'wrong')]),
                                   ('requiresFiles', [])):
                value = json.loads(receipt)
                value[field] = changed
                self.f.receipt().write_text(json.dumps(value))
                with self.assertRaises(importer.ImportRejected):
                    importer._installed_inventory(self.f.root, 'nightmare-suju')
            self.f.receipt().write_bytes(receipt)
            updated = self.f.package('nightmare-suju', {fixture.SUJU: b'new-image'}, 'real-update.zip')
            self.assertEqual(self.f.live(updated)['status'], 'author-upgraded-live')
            self.assertEqual(len(json.loads(self.f.receipt().read_bytes())['requiresFiles']), 4)

    def test_removal_cannot_ignore_missing_or_empty_dependencies_on_other_receipt(self):
        import map_package_removal as removal
        plan = removal.inspect_package_removal(self.f.root, 'nightmare-wang-qingxin')
        for missing in (True, False):
            value = json.loads(self.before)
            if missing:
                value.pop('requiresFiles')
            else:
                value['requiresFiles'] = []
            self.f.receipt().write_text(json.dumps(value))
            before = {p.relative_to(self.f.root): p.read_bytes()
                      for p in self.f.root.rglob('*') if p.is_file()}
            with self.assertRaises(importer.ImportRejected):
                removal.inspect_package_removal(self.f.root, 'nightmare-wang-qingxin')
            with self.assertRaises(importer.ImportRejected):
                removal.remove_packages(self.f.root, 'nightmare-wang-qingxin', application_idle=True,
                                        expected_plan_sha256=plan['planSha256'])
            after = {p.relative_to(self.f.root): p.read_bytes()
                     for p in self.f.root.rglob('*') if p.is_file()}
            self.assertEqual(before, after)

    def test_helper_reports_failure_not_duplicate_for_dependency_change(self):
        import star_map_import_helper as helper
        with patch.object(helper, 'wait_for_application_exit', return_value={'exited': True}):
            code, result = helper.run_helper(archive=self.updated, install_root=self.f.root,
                parent_pid=1, parent_created='fixture', archive_sha256=importer._sha(self.updated),
                request_id='dependency-change')
        self.assertEqual(code, 2)
        self.assertFalse(result['success'])
        self.assertIn('dependency-contract-changed', result['reason'])
        self.assertEqual(self.f.receipt().read_bytes(), self.before)

    def test_upgraded_package_rejects_metadata_change_and_keeps_history_witness(self):
        updated = self.f.package('nightmare-suju', {fixture.SUJU: b'upgraded'}, 'payload-upgrade.zip')
        result = self.f.live(updated)
        before, inode = self.f.receipt().read_bytes(), self.f.receipt().stat().st_ino
        self.f.dependencies.append(fixture.row(self.sidecar, b'old-catalog'))
        metadata = self.f.package('nightmare-suju', {fixture.SUJU: b'upgraded'}, 'metadata.zip')
        self.f.dependencies.pop()
        with self.assertRaisesRegex(importer.ImportRejected, 'dependency-contract-changed'):
            self.f.live(metadata)
        self.assertEqual(self.f.receipt().read_bytes(), before)
        self.assertEqual(self.f.receipt().stat().st_ino, inode)
        again = self.f.live(updated)
        self.assertTrue(again['receiptPreserved'])
        self.assertEqual(again['upgradeTransactionId'], result['upgradeTransactionId'])


if __name__ == '__main__':
    unittest.main()
