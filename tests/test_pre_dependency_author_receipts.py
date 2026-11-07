"""Older valid author receipts retain their reviewed archive dependencies.

Synthetic archive/receipt bytes exercise the real importer and base transaction.
No installed user files or original author images are used by these tests.
"""
import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import legacy_author_receipt_contracts as policy
import managed_base_resources as managed
import star_map_import as importer
import test_live_author_import as fixture


def inventory_digest(rows):
    return hashlib.sha256(json.dumps(sorted(rows, key=lambda row: row['path']),
        sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf8')).hexdigest()


class PreDependencyReceiptTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.LiveAuthorImportTests('runTest')
        self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.package = 'nightmare-suju'
        self.archive = self.f.package(self.package, {fixture.SUJU: b'old-display'}, 'reviewed.zip')
        self.f.live(self.archive)
        self.receipt_path = self.f.root / f'work/map-packages/{self.package}.json'
        self.receipt = json.loads(self.receipt_path.read_bytes())
        self.dependencies = self.receipt.pop('requiresFiles')
        self.save(self.receipt)
        digest = inventory_digest(self.dependencies)
        self.contract = (digest, tuple((row['path'], row['bytes'], row['sha256'])
            for row in sorted(self.dependencies, key=lambda row: row['path'])))
        self.release = (self.package, self.receipt['archiveSha256'],
                        inventory_digest(self.receipt['files']), digest)
        for item in (patch.object(policy, 'REVIEWED_PRE_DEPENDENCY_AUTHOR_RELEASES', (self.release,)),
                     patch.object(policy, 'REVIEWED_AUTHOR_DEPENDENCY_CONTRACTS', (self.contract,))):
            item.start(); self.addCleanup(item.stop)

    def save(self, value):
        self.receipt_path.write_text(json.dumps(value, sort_keys=True), encoding='utf8')

    def read(self):
        return importer._installed_inventory(self.f.root, self.package)

    def stage(self, data=b'base-v1'):
        name = '_internal/references/owned-base-fixture.json'
        row = fixture.row(name, data)
        self.f.write(managed.CANDIDATE + '/payload/' + name, data)
        self.f.write(managed.CANDIDATE + '/manifest.json', json.dumps(
            managed.manifest_for_rows([row], 'fixture-v1')).encode())
        return self.f.root / name

    def test_exact_reviewed_old_receipt_restores_full_contract_without_rewriting_it(self):
        before = self.receipt_path.read_bytes(); inode = self.receipt_path.stat().st_ino
        owned, dependencies, _ = self.read()
        self.assertEqual(owned, importer._rows(self.receipt['files']))
        self.assertEqual(dependencies, importer._rows(self.dependencies, dependencies=True))
        self.assertEqual(self.receipt_path.read_bytes(), before)
        self.assertEqual(self.receipt_path.stat().st_ino, inode)

    def test_same_archive_reimport_preserves_original_receipt_and_inode(self):
        before = self.receipt_path.read_bytes(); inode = self.receipt_path.stat().st_ino
        result = self.f.live(self.archive)
        self.assertTrue(result['receiptPreserved'])
        self.assertEqual(self.receipt_path.read_bytes(), before)
        self.assertEqual(self.receipt_path.stat().st_ino, inode)

    def test_unknown_archive_or_changed_incomplete_owned_inventory_never_gets_exception(self):
        variants = []
        wrong_archive = copy.deepcopy(self.receipt); wrong_archive['archiveSha256'] = 'f' * 64
        variants.append(wrong_archive)
        changed = copy.deepcopy(self.receipt); changed['files'][0]['sha256'] = 'e' * 64
        variants.append(changed)
        incomplete = copy.deepcopy(self.receipt); incomplete['files'] = []
        variants.append(incomplete)
        for value in variants:
            with self.subTest(value=value):
                self.save(value); before = self.receipt_path.read_bytes()
                with self.assertRaises(importer.ImportRejected): self.read()
                self.assertEqual(self.receipt_path.read_bytes(), before)

    def test_present_but_empty_or_incomplete_dependencies_still_fail_closed(self):
        for dependencies in ([], self.dependencies[:-1]):
            with self.subTest(dependencies=len(dependencies)):
                value = dict(self.receipt, requiresFiles=dependencies); self.save(value)
                with self.assertRaises(importer.ImportRejected): self.read()

    def test_base_startup_and_unrelated_upgrade_work_but_original_dependency_still_blocks(self):
        before = self.receipt_path.read_bytes(); base = self.stage()
        self.assertEqual(managed.repair_managed_base_resources(self.f.root)['changed'], 1)
        self.assertEqual(managed.repair_managed_base_resources(self.f.root)['status'], 'healthy')
        self.stage(b'base-v2')
        dependency = self.f.root / self.dependencies[0]['path']; dependency.write_bytes(b'changed-dependency')
        plan = managed.plan_managed_base_resources(self.f.root)
        self.assertIn('installed-package-dependency-conflict', [row['reason'] for row in plan['issues']])
        with self.assertRaisesRegex(managed.ManagedBaseResourceError, 'preflight-blocked'):
            managed.repair_managed_base_resources(self.f.root)
        self.assertEqual(base.read_bytes(), b'base-v1')
        self.assertEqual(dependency.read_bytes(), b'changed-dependency')
        self.assertEqual(self.receipt_path.read_bytes(), before)

    def test_incomplete_source_contract_cannot_erase_original_dependencies(self):
        reduced = self.dependencies[:-1]; digest = inventory_digest(reduced)
        contract = (digest, tuple((row['path'], row['bytes'], row['sha256']) for row in reduced))
        release = (*self.release[:3], digest)
        with patch.object(policy, 'REVIEWED_PRE_DEPENDENCY_AUTHOR_RELEASES', (release,)), \
                patch.object(policy, 'REVIEWED_AUTHOR_DEPENDENCY_CONTRACTS', (contract,)):
            with self.assertRaisesRegex(importer.ImportRejected, 'invalid-reviewed-author-receipt-policy'):
                self.read()

    def test_malformed_or_duplicate_source_policy_fails_before_match(self):
        for value in ([self.release], (self.release, self.release), (([], *self.release[1:]),)):
            with self.subTest(policy=value), patch.object(policy, 'REVIEWED_PRE_DEPENDENCY_AUTHOR_RELEASES', value):
                with self.assertRaisesRegex(importer.ImportRejected, 'invalid-reviewed-author-receipt-policy'):
                    self.read()


if __name__ == '__main__':
    unittest.main()
