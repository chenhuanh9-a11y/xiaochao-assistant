"""Real receipts and transactions; listing never grants deletion authority."""
import json
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import test_generic_author_package as fixtures
import map_package_summary as summary
import map_package_removal as removal
import author_package_management as management
import star_map_import as importer

class PackageSummaryTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.GenericPackageTests()
        self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.f.install(self.f.package())

    def test_list_reads_no_payload_hashes_decodes_or_delete_plan(self):
        from PIL import Image
        import generic_author_base as base
        import s1_author_displays as displays
        with patch.object(importer, '_sha', side_effect=AssertionError('payload hash')), \
             patch.object(Image.Image, 'load', side_effect=AssertionError('image decode')), \
             patch.object(base, 'resolve_layer', side_effect=AssertionError('base resolve')), \
             patch.object(removal, '_plan_locked', side_effect=AssertionError('delete preflight')), \
             patch.object(displays, 'InstalledAuthorPackage', side_effect=AssertionError('package decode')):
            rows = summary.declared_packages(self.f.root)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['title'], '新作者整图')
        self.assertEqual(rows[0]['status'], 'declared')
        self.assertFalse(rows[0]['verified']); self.assertFalse(rows[0]['canRemove'])
        self.assertTrue(rows[0]['canInspectRemoval'])
        self.assertNotIn('fileCount', rows[0])

    def test_modified_after_listing_is_preserved_by_real_removal(self):
        summary.declared_packages(self.f.root)
        image = next(self.f.root.glob('_internal/assets/authors/**/*.png'))
        image.write_bytes(b'user-modified')
        plan = removal.inspect_package_removal(self.f.root, self.f.profile)
        self.assertTrue(plan['preserved'])
        result = removal.remove_packages(self.f.root, self.f.profile, application_idle=True,
            expected_plan_sha256=plan['planSha256'])
        self.assertEqual(image.read_bytes(), b'user-modified')
        self.assertGreater(result['unverifiedPreservedCount'], 0)

    def test_corrupt_receipt_is_not_marked_verified_or_deletable(self):
        (self.f.root / f'work/map-packages/{self.f.profile}.json').write_text('{}')
        row, = summary.declared_packages(self.f.root)
        self.assertEqual(row['status'], 'unverified')
        self.assertFalse(row['canInspectRemoval'])
        with self.assertRaises(ValueError):
            removal.inspect_package_removal(self.f.root, self.f.profile)

    def test_pending_transaction_still_blocks_listing(self):
        (self.f.root / 'work/map-packages/pending-removal.json').write_text('{}')
        with self.assertRaises(ValueError): summary.declared_packages(self.f.root)

    def test_full_check_can_skip_future_deletion_preflight(self):
        with patch.object(management, 'inspect_author_package_removal', side_effect=AssertionError('premature deletion')):
            rows = management.managed_author_packages_snapshot(self.f.root, inspect_removal=False)
        self.assertEqual(next(r for r in rows if r['packageId']==self.f.profile)['status'], 'installed')
        image = next(self.f.root.glob('_internal/assets/authors/**/*.png'))
        image.write_bytes(b'bad')
        rows = management.managed_author_packages_snapshot(self.f.root, inspect_removal=False)
        self.assertNotEqual(next(r for r in rows if r['packageId']==self.f.profile)['status'], 'installed')

    def test_native_declarations_do_not_hash_all_images(self):
        import test_s1_builtin_resources as native
        native.fixture(self.f.root / '_internal', 'nightmare')
        with patch.object(importer, '_sha', side_effect=AssertionError('payload hash')):
            rows = summary.declared_builtins(self.f.root / '_internal')
        self.assertEqual(rows[0]['status'], 'declared')
        self.assertFalse(rows[0]['verified'])
        self.assertNotIn('mapCount', rows[0])

    def test_real_fixture_work_count_comparison(self):
        observations = {}
        for name, call in (('old', lambda: removal.managed_packages_snapshot(self.f.root)),
                           ('new', lambda: summary.declared_packages(self.f.root))):
            with patch.object(importer, '_sha', wraps=importer._sha) as hashes:
                start=time.perf_counter(); call()
                observations[name] = dict(ms=round((time.perf_counter()-start)*1000,3), hashes=hashes.call_count)
        self.assertGreater(observations['old']['hashes'], 0)
        self.assertEqual(observations['new']['hashes'], 0)
        print('MANAGEMENT_DECLARATION_BENCHMARK', json.dumps(observations))

if __name__=='__main__': unittest.main()
