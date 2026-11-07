"""Legacy receipts reserve old bytes without granting import/display authority.

Real base transaction and startup lease; bounded synthetic files, not game QA.
"""
import copy
import json
import unittest

import managed_base_resources as managed
import star_map_import as importer
from client_resource_startup import production_resources
import test_managed_base_resources as fixtures


class LegacyReceiptStartupRetentionTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ManagedBaseResourcesTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.project()
        self.root = self.f.target

    def receipt(self, package, *, path=None, revision=1):
        if path is None:
            if package in importer.S1_AUTHOR_PACKAGE_IDS:
                from s1_author_package import image_path, layouts
                path = image_path('nightmare', sorted(layouts('nightmare'))[0], '-1', package)
            elif package in importer.AUTHOR_PACKAGES:
                path = '_internal/assets/authors/' + importer.AUTHOR_PACKAGES[package] + '/old.png'
            elif package == 'difficult-zhan10':
                path = '_internal/assets/difficult-maps/old.png'
            else:
                path = '_internal/assets/suju/old.png'
        row = self.f.row(path, b'legacy author data')
        data = dict(packageId=package, revision=revision, archiveSha256='d' * 64,
                    files=[row], authentication='private legacy metadata')
        return data

    def save(self, receipt):
        p = self.root / ('work/map-packages/' + receipt['packageId'] + '.json')
        self.f.write(p, json.dumps(receipt).encode())
        return p

    def protected(self):
        return {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

    def unchanged(self, before):
        for p, raw in before.items():
            self.assertEqual(p.read_bytes(), raw, str(p))

    def test_every_registered_author_missing_dependencies_can_start_without_archive_allowlist(self):
        for package in sorted(importer.PACKAGE_IDS):
            receipt = self.receipt(package)
            self.save(receipt)
            self.f.write(self.root / receipt['files'][0]['path'], b'legacy author data')
        for name, data in (('work/user-routes/keep.json', b'route'),
                           ('work/nightmare-memory/ui-settings.json', b'settings')):
            self.f.write(self.root / name, data)
        before = self.protected()
        # The production startup context must reach its first reader.
        with production_resources(self.root, production=True) as outcome:
            self.assertIsNone(outcome)
            self.assertTrue((self.root / managed.STATE).is_file())
        self.unchanged(before)
        self.assertEqual(managed.repair_managed_base_resources(self.root)['status'], 'healthy')
        for package in importer.PACKAGE_IDS:
            # This change is not an exception to the strict mutation contract.
            with self.subTest(package=package), self.assertRaises(importer.ImportRejected):
                importer._installed_inventory(self.root, package)

    def test_unregistered_valid_old_author_is_preserved_without_becoming_a_supported_profile(self):
        receipt = self.receipt('old-unregistered-author', revision=3)
        receipt['files'][0]['path'] = '_internal/assets/old-author/one.png'
        p = self.save(receipt)
        before = p.read_bytes()
        with production_resources(self.root, production=True):
            pass
        self.assertEqual(p.read_bytes(), before)
        self.assertNotIn(receipt['packageId'], importer.PACKAGE_IDS)
        self.assertIn(receipt['packageId'], managed.plan_managed_base_resources(self.root)['retainedLegacyPackages'])

    def test_missing_metadata_cannot_authorize_existing_base_replacement(self):
        self.save(self.receipt('nightmare-suju'))
        managed.repair_managed_base_resources(self.root)
        before = self.protected()
        self.f.upgrade_candidate()
        with self.assertRaisesRegex(managed.ManagedBaseResourceError, 'installed-package-dependencies-unrecorded'):
            managed.repair_managed_base_resources(self.root)
        # Candidate files intentionally changed by the test; active/user files did not.
        self.unchanged({p: raw for p, raw in before.items() if managed.CANDIDATE not in p.as_posix()})
        self.assertFalse((self.root / managed.PENDING).exists())

    def test_unknown_author_claimed_missing_base_cannot_be_filled_with_different_bytes(self):
        receipt = self.receipt('old-unregistered-author', path='_internal/references/catalog.json')
        self.save(receipt)
        with self.assertRaisesRegex(managed.ManagedBaseResourceError, 'installed-package-owned-conflict'):
            managed.repair_managed_base_resources(self.root)
        self.assertFalse((self.root / '_internal/references/catalog.json').exists())

    def test_unknown_author_explicit_dependency_remains_a_constraint(self):
        receipt = self.receipt('old-unregistered-author')
        receipt['requiresFiles'] = [self.f.row('_internal/references/catalog.json', b'old dependency')]
        self.save(receipt)
        with self.assertRaisesRegex(managed.ManagedBaseResourceError, 'installed-package-dependency-conflict'):
            managed.repair_managed_base_resources(self.root)

    def test_present_but_empty_or_incomplete_dependencies_are_not_the_missing_field_format(self):
        for required in ([], [self.f.row('_internal/references/old.json', b'old')]):
            receipt = self.receipt('nightmare-suju')
            receipt['requiresFiles'] = required
            p = self.save(receipt)
            before = p.read_bytes()
            with self.subTest(required=required), self.assertRaises(managed.ManagedBaseResourceError):
                managed.repair_managed_base_resources(self.root)
            self.assertEqual(p.read_bytes(), before)
            self.assertFalse((self.root / managed.STATE).exists())

    def test_malformed_or_unsafe_records_never_gain_legacy_retention(self):
        original = self.receipt('nightmare-suju')
        variants = []
        for path in ('../escape.png', 'work/user-routes/keep.json', '_internal/assets/authors/other/old.png'):
            row = copy.deepcopy(original)
            row['files'][0]['path'] = path
            variants.append(row)
        row = copy.deepcopy(original); row['files'] *= 2; variants.append(row)
        row = copy.deepcopy(original); row['archiveSha256'] = 'not-a-hash'; variants.append(row)
        row = copy.deepcopy(original); row['revision'] = True; variants.append(row)
        for receipt in variants:
            p = self.save(receipt)
            before = p.read_bytes()
            with self.subTest(receipt=receipt), self.assertRaises(managed.ManagedBaseResourceError):
                managed.repair_managed_base_resources(self.root)
            self.assertEqual(p.read_bytes(), before)
            self.assertFalse((self.root / managed.STATE).exists())

    def test_failed_base_addition_rolls_back_and_preserves_legacy_files_and_settings(self):
        from unittest.mock import patch
        self.save(self.receipt('nightmare-suju'))
        self.f.write(self.root / 'work/user-routes/keep.json', b'route')
        before = self.protected()
        original_link = managed.os.link
        def fail_link(src, dst, *args, **kwargs):
            if str(dst).replace('\\', '/').endswith('_internal/references/catalog.json'):
                raise OSError('isolated simulated publication failure')
            return original_link(src, dst, *args, **kwargs)
        with patch.object(managed.os, 'link', side_effect=fail_link):
            with self.assertRaises(OSError):
                managed.repair_managed_base_resources(self.root)
        self.unchanged(before)
        self.assertFalse((self.root / managed.STATE).exists())
        self.assertEqual(managed.repair_managed_base_resources(self.root)['status'], 'repaired')
        self.unchanged(before)


if __name__ == '__main__':
    unittest.main()
