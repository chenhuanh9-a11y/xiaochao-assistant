"""V2 and pre-V2 preservation constraints through the real startup parser.

Synthetic installed packages; no actual user data or game access.
"""
import copy
import json
import unittest

import managed_base_resources as managed
import star_map_import as importer
import test_generic_author_package as fixtures


class GenericAuthorStartupTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.GenericPackageTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.install(self.f.package())
        self.receipt = self.f.root / f'work/map-packages/{self.f.profile}.json'
        self.original = self.receipt.read_bytes()

    def test_valid_v2_constrains_base_without_legacy_fallback(self):
        owned, dependencies, unrecorded = managed._package_constraints(self.f.root)
        self.assertEqual(set(owned), set(importer._installed_inventory(self.f.root, self.f.profile)[0]))
        self.assertEqual({row['path'] for _, row in dependencies}, {r['path'] for r in self.f.deps})
        self.assertEqual(unrecorded, [])
        self.assertEqual(self.receipt.read_bytes(), self.original)

    def test_rejected_v2_cannot_downgrade_into_preservation_envelope(self):
        original = json.loads(self.original)
        variants = []
        row = copy.deepcopy(original); row.pop('requiresFiles'); variants.append(row)
        row = copy.deepcopy(original); row['requiresFiles'] = []; variants.append(row)
        row = copy.deepcopy(original); row['requiresFiles'] = row['requiresFiles'][:-1]; variants.append(row)
        row = copy.deepcopy(original); row['revision'] = True; variants.append(row)
        row = copy.deepcopy(original); row['season'] = 's1'; variants.append(row)
        row = copy.deepcopy(original); row['files'][0]['path'] = '_internal/assets/foreign.png'; variants.append(row)
        for row in variants:
            raw = json.dumps(row).encode()
            self.receipt.write_bytes(raw)
            with self.subTest(row=row), self.assertRaises(managed.ManagedBaseResourceError):
                managed._package_constraints(self.f.root)
            self.assertEqual(self.receipt.read_bytes(), raw)

    def test_pre_v2_unknown_author_prefix_stays_preservation_only(self):
        row = json.loads(self.original)
        row.pop('schema'); row.pop('requiresFiles')
        self.receipt.write_text(json.dumps(row), encoding='utf8')
        before = self.receipt.read_bytes()
        owned, dependencies, unrecorded = managed._package_constraints(self.f.root)
        self.assertTrue(owned)
        self.assertFalse(dependencies)
        self.assertEqual(unrecorded, [self.f.profile])
        with self.assertRaises(importer.ImportRejected):
            importer._installed_inventory(self.f.root, self.f.profile)
        self.assertEqual(self.receipt.read_bytes(), before)

    def test_shortened_receipt_cannot_release_cross_package_dependency(self):
        row = json.loads(self.original)
        dependency = row['requiresFiles'].pop()
        self.receipt.write_text(json.dumps(row), encoding='utf8')
        incoming = dict(dependency, sha256='0' * 64)
        with self.assertRaises(importer.ImportRejected):
            importer._check_other_package_constraints(self.f.root, 'nightmare-suju',
                {incoming['path'].casefold(): incoming}, [incoming])
        import map_package_removal
        with self.assertRaises(importer.ImportRejected):
            map_package_removal.inspect_package_removal(self.f.root, self.f.profile)

    def candidate(self):
        directory = self.f.root / managed.CANDIDATE
        for row in self.f.deps:
            target = directory / 'payload' / row['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((self.f.root / row['path']).read_bytes())
        (directory / 'manifest.json').write_text(json.dumps(
            managed.manifest_for_rows(self.f.deps, 'fixture-v2')), encoding='utf8')
        return directory

    def test_verified_prospective_base_repairs_every_missing_dependency(self):
        self.candidate()
        for row in self.f.deps:
            path = self.f.root / row['path']
            original = path.read_bytes()
            path.unlink()
            with self.subTest(path=row['path']):
                result = managed.repair_managed_base_resources(self.f.root)
                self.assertEqual(result['status'], 'repaired')
                self.assertEqual(result['changed'], 1)
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(self.receipt.read_bytes(), self.original)

    def test_corrupt_candidate_cannot_supply_missing_dependency_authority(self):
        directory = self.candidate()
        row = self.f.deps[0]
        target = self.f.root / row['path']
        target.unlink()
        (directory / 'payload' / row['path']).write_bytes(b'corrupt')
        with self.assertRaises(managed.ManagedBaseResourceError):
            managed.repair_managed_base_resources(self.f.root)
        self.assertFalse(target.exists())
        self.assertEqual(self.receipt.read_bytes(), self.original)

    def test_prospective_base_does_not_allow_shortened_v2_receipt(self):
        self.candidate()
        row = json.loads(self.original)
        row['requiresFiles'].pop()
        self.receipt.write_text(json.dumps(row), encoding='utf8')
        before = self.receipt.read_bytes()
        with self.assertRaises(managed.ManagedBaseResourceError):
            managed.repair_managed_base_resources(self.f.root)
        self.assertEqual(self.receipt.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
