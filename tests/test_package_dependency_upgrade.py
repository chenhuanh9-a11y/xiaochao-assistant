"""Real ZIP transactions with synthetic approved release bytes and dependencies."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import author_package_management as manager
import star_map_import as importer
import test_live_author_import as fixture


class PackageDependencyUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.LiveAuthorImportTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.package = 'nightmare-wang-qingxin'
        self.sidecar = '_internal/references/author-packs/wang-qingxin/catalog.json'
        self.old = {fixture.WANG: b'old-image', self.sidecar: b'old-catalog'}
        for path, data in self.old.items():
            self.f.write(path, data)
        inventory = {self.package: dict(archiveSha256='a' * 64, archiveName='approved.zip',
            files=[fixture.row(path, data) for path, data in self.old.items()])}
        stub = patch.object(manager, 'LEGACY_AUTHOR_INVENTORY', inventory)
        stub.start()
        self.addCleanup(stub.stop)
        self.f.dependencies.append(fixture.row(self.sidecar, b'old-catalog'))
        self.f.live(self.f.package('nightmare-suju', {fixture.SUJU: b'suju'}, 'suju.zip'))
        self.f.dependencies.pop()

    def archive(self, sidecar=b'new-catalog'):
        return self.f.package(self.package, {fixture.WANG: b'new-image', self.sidecar: sidecar}, 'wang.zip')

    def assert_rejected_unchanged(self, archive):
        before = {p.relative_to(self.f.root): p.read_bytes() for p in self.f.root.rglob('*') if p.is_file()}
        with self.assertRaises(importer.ImportRejected):
            self.f.live(archive)
        after = {p.relative_to(self.f.root): p.read_bytes() for p in self.f.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_legacy_upgrade_cannot_break_other_package_dependency_or_adopt(self):
        self.assert_rejected_unchanged(self.archive())
        self.assertFalse(self.f.receipt(self.package).exists())

    def test_inspection_rejects_dependency_conflict_without_adoption(self):
        with self.assertRaisesRegex(importer.ImportRejected, 'shared-package-dependency-conflict'):
            importer.inspect_package(self.archive(), self.f.root)
        self.assertFalse(self.f.receipt(self.package).exists())
        self.assertEqual((self.f.root / self.sidecar).read_bytes(), b'old-catalog')

    def test_malformed_other_receipt_fails_closed(self):
        self.f.receipt().write_bytes(b'{invalid json')
        self.assert_rejected_unchanged(self.archive())

    def test_managed_upgrade_cannot_break_other_package_dependency(self):
        manager.adopt_legacy_author_package(self.f.root, self.package)
        self.assert_rejected_unchanged(self.archive())

    def test_unchanged_dependency_does_not_block_unrelated_image_upgrade(self):
        result = self.f.live(self.archive(b'old-catalog'))
        self.assertEqual(result['status'], 'author-upgraded-live')
        self.assertEqual((self.f.root / fixture.WANG).read_bytes(), b'new-image')
        self.assertEqual((self.f.root / self.sidecar).read_bytes(), b'old-catalog')

    def test_dependency_is_not_ownership_and_other_owned_files_stay_untouched(self):
        result = self.f.live(self.archive(b'old-catalog'))
        receipt = json.loads(self.f.receipt(self.package).read_bytes())
        self.assertEqual(result['status'], 'author-upgraded-live')
        self.assertNotIn(fixture.SUJU, [row['path'] for row in receipt['files']])
        self.assertEqual((self.f.root / fixture.SUJU).read_bytes(), b'suju')

    def test_invalid_other_dependency_inventory_fails_closed(self):
        original = self.f.receipt().read_bytes()
        for invalid in (None, {}, '', [dict(path=self.sidecar)],
                        [fixture.row(self.sidecar, b'old-catalog')] * 2):
            with self.subTest(invalid=invalid):
                value = json.loads(original)
                value['requiresFiles'] = invalid
                self.f.receipt().write_text(json.dumps(value))
                self.assert_rejected_unchanged(self.archive(b'old-catalog'))

    def test_conflicting_other_dependency_receipt_fails_closed(self):
        value = json.loads(self.f.receipt().read_bytes())
        value['requiresFiles'][-1] = fixture.row(self.sidecar, b'wrong-catalog')
        self.f.receipt().write_text(json.dumps(value))
        self.assert_rejected_unchanged(self.archive(b'old-catalog'))

    def test_other_dependency_changed_after_validation_is_rechecked_before_adoption(self):
        real = importer.preflight_hardlinks
        def changed(*args, **kwargs):
            real(*args, **kwargs)
            value = json.loads(self.f.receipt().read_bytes())
            value['requiresFiles'][-1] = fixture.row(self.sidecar, b'wrong-catalog')
            self.f.receipt().write_text(json.dumps(value))
        with patch.object(importer, 'preflight_hardlinks', side_effect=changed):
            with self.assertRaises(importer.ImportRejected):
                self.f.live(self.archive(b'old-catalog'))
        self.assertFalse(self.f.receipt(self.package).exists())
        for path, data in self.old.items():
            self.assertEqual((self.f.root / path).read_bytes(), data)


if __name__ == '__main__':
    unittest.main()
