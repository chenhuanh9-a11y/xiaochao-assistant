"""Native Windows namespace negatives using task-owned temporary packages."""
import os
import subprocess
import unittest
from pathlib import Path

import generic_author_base as base
import generic_author_package as generic
import star_map_import as importer
import test_generic_author_base as base_fixture
import test_generic_author_package as fixtures


@unittest.skipUnless(os.name == 'nt', 'Windows filesystem semantics required')
class GenericWindowsPathTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.GenericPackageTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.install(self.f.package())
        self.receipt = self.f.root / f'work/map-packages/{self.f.profile}.json'

    def assert_dependency_protected(self):
        dependency = self.f.deps[0]
        incoming = dict(dependency, sha256='0' * 64)
        with self.assertRaisesRegex(importer.ImportRejected, 'shared-package-dependency-conflict'):
            importer._check_other_package_constraints(self.f.root, 'nightmare-suju',
                {incoming['path'].casefold(): incoming}, [incoming])

    def test_case_only_receipt_rename_retains_dependency_constraints(self):
        self.receipt.rename(self.receipt.with_name(self.receipt.name.upper()))
        self.assertTrue(self.receipt.exists())
        self.assertIn(self.f.profile, importer.package_ids(self.f.root))
        self.assert_dependency_protected()
        import managed_base_resources
        self.assertEqual(managed_base_resources._package_constraints(self.f.root)[2], [])

    def test_linked_receipt_cannot_disappear_from_mutation_guards(self):
        original = self.receipt.read_bytes()
        target = self.f.root.parent / 'receipt-target.json'
        target.write_bytes(original)
        self.receipt.unlink()
        self.receipt.symlink_to(target)
        self.assertNotIn(self.f.profile, generic.profile_ids(self.f.root / '_internal'))
        with self.assertRaises(importer.ImportRejected):
            importer.package_ids(self.f.root)

    def test_internal_directory_junction_is_rejected_before_resolve(self):
        root = self.f.root.parent / 'junction-base'
        internal = base_fixture.fixture(root)
        reference = internal / 'references'
        target = internal / 'actual-references'
        reference.rename(target)
        process = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(reference), str(target)],
            capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        with self.assertRaises(ValueError):
            base.resolve_layer(root, 's0', 'nightmare', 'map', 'B1')


if __name__ == '__main__':
    unittest.main()
