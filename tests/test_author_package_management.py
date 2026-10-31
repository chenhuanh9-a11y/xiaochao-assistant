"""Managed display deletion uses real receipts/locks and temporary installations."""
import json
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import author_package_management as manager
import star_map_import as importer
import map_package_upgrade as transaction
from test_live_author_import import LiveAuthorImportTests, CORE, SUJU, NATIVE, LEGACY, WANG, NIGHTINGALE


class ManagedAuthorTests(unittest.TestCase):
    def setUp(self):
        self.f = LiveAuthorImportTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def plan(self, package='nightmare-suju'):
        return manager.inspect_author_package_removal(self.f.root, package)

    def remove(self, package='nightmare-suju', **kwargs):
        plan = self.plan(package)
        return manager.remove_author_package_live(self.f.root, package, application_idle=True,
            expected_receipt_sha256=plan['receiptSha256'], expected_plan_sha256=plan['planSha256'], **kwargs)

    def test_four_packages_repeat_remove_reimport_preserve_core_and_custom(self):
        payloads = {'nightmare-suju': {SUJU:b'suju'},
            'difficult-zhan10': {NATIVE:b'zhan',LEGACY:b'legacy',CORE:self.f.core_bytes},
            'nightmare-wang-qingxin': {WANG:b'wang'},
            'nightmare-nightingale': {NIGHTINGALE:b'ai'},
            'nightmare-lianghapi': {'_internal/assets/authors/lianghapi-single/map-03-2.png':b'liang'}}
        for package, author in importer.AUTHOR_PACKAGES.items():
            if package not in payloads:
                continue  # New difficult-author lifecycle is covered with its native dependency fixture.
            for name in importer.AUTHOR_SIDECARS:
                payloads[package][f'_internal/references/author-packs/{author}/{name}'] = b'{}'
        for package, payload in payloads.items():
            with self.subTest(package=package):
                archive = self.f.package(package, payload, package+'.zip')
                self.f.live(archive)
                repeat = self.f.live(archive)
                self.assertEqual(repeat['status'], 'author-already-installed')
                with patch.object(importer, '_check_closed', side_effect=AssertionError('live deletion checked EXE')):
                    removed = self.remove(package)
                self.assertFalse(removed['restartRequired'])
                self.assertNotIn('backupPath', removed)
                for path, data in payload.items():
                    if importer._live_author_path(package,path):
                        self.assertFalse((self.f.root/path).exists())
                    else:
                        self.assertEqual((self.f.root/path).read_bytes(),data)
                self.assertEqual(importer.verified_installed_package(self.f.root,package)['status'],'not-installed')
                self.assertEqual(self.f.live(archive)['status'],'author-installed-live')
                self.f.assert_base_preserved()

    def test_unknown_and_modified_owned_bytes_are_preserved(self):
        self.f.write(SUJU,b'unmanaged')
        with self.assertRaisesRegex(importer.ImportRejected,'not-managed'):
            self.plan()
        state={r['packageId']:r for r in manager.managed_author_packages_snapshot(self.f.root)}
        self.assertEqual(state['nightmare-suju']['status'],'unmanaged')
        self.assertFalse(state['nightmare-suju']['canRemove'])
        archive=self.f.package('nightmare-suju',{SUJU:b'unmanaged'})
        self.f.live(archive)  # Only exact archive bytes can adopt existing files.
        self.f.write(SUJU,b'user-modified')
        plan = self.plan()
        self.assertEqual(plan['modifiedFiles'], [SUJU])
        self.assertNotIn(SUJU, {row['path'] for row in plan['files']})
        state={r['packageId']:r for r in manager.managed_author_packages_snapshot(self.f.root)}
        self.assertEqual(state['nightmare-suju']['status'], 'incomplete')
        self.assertEqual(state['nightmare-suju']['modifiedFiles'], 1)
        self.assertEqual(state['nightmare-suju']['fileCount'], 0)
        self.assertTrue(state['nightmare-suju']['canRemove'])
        removed = self.remove()
        self.assertNotIn('backupPath', removed)
        with self.assertRaisesRegex(importer.ImportRejected, 'existing-data-conflict'):
            self.f.live(archive)
        self.assertEqual((self.f.root/SUJU).read_bytes(), b'user-modified')
        self.f.assert_base_preserved()

    def test_modified_file_is_untouched_while_receipt_removal_cancels(self):
        self.f.live(self.f.package('nightmare-suju', {SUJU: b'original'}))
        self.f.write(SUJU, b'modified')
        original_receipt = self.f.receipt().read_bytes()
        real = transaction._move_owned
        moved = []
        def move(source, target, *args):
            real(source, target, *args)
            moved.append(str(source))
        with patch.object(transaction, '_move_owned', side_effect=move):
            with self.assertRaisesRegex(importer.ImportRejected, 'import-cancelled'):
                self.remove(cancelled=lambda: bool(moved))
        self.assertEqual((self.f.root/SUJU).read_bytes(), b'modified')
        self.assertEqual(self.f.receipt().read_bytes(), original_receipt)

    def test_non_file_owned_path_remains_ineligible(self):
        self.f.live(self.f.package('nightmare-suju', {SUJU: b'original'}))
        target = self.f.root/SUJU
        target.unlink()
        target.mkdir()
        plan = self.plan()
        self.assertNotIn(SUJU, {row['path'] for row in plan['files']})
        self.assertTrue(target.is_dir())

    def test_confirmation_rejects_new_or_changed_bytes_without_moving_them(self):
        self.f.live(self.f.package('nightmare-suju', {SUJU: b'original'}))
        for missing in (False, True):
            with self.subTest(previouslyMissing=missing):
                target = self.f.root/SUJU
                if missing:
                    target.unlink()
                plan = self.plan()
                self.f.write(SUJU, b'new-after-confirmation')
                with self.assertRaisesRegex(importer.ImportRejected, 'author-files-changed-reinspect'):
                    manager.remove_author_package_live(self.f.root, 'nightmare-suju', application_idle=True,
                        expected_receipt_sha256=plan['receiptSha256'], expected_plan_sha256=plan['planSha256'])
                self.assertEqual(target.read_bytes(), b'new-after-confirmation')
                self.assertTrue(self.f.receipt().is_file())

    def test_changed_images_in_all_four_roles_are_preserved_on_uninstall(self):
        images = {'nightmare-suju': SUJU, 'difficult-zhan10': NATIVE,
                  'nightmare-wang-qingxin': WANG, 'nightmare-nightingale': NIGHTINGALE}
        for package_id, image in images.items():
            with self.subTest(package=package_id):
                payload = {image: b'original-image'}
                if package_id == 'difficult-zhan10':
                    payload.update({LEGACY: b'legacy-image', CORE: self.f.core_bytes})
                if package_id in importer.AUTHOR_PACKAGES:
                    author = importer.AUTHOR_PACKAGES[package_id]
                    payload.update({f'_internal/references/author-packs/{author}/{name}': b'{}'
                                    for name in importer.AUTHOR_SIDECARS})
                archive = self.f.package(package_id, payload, package_id+'-recover.zip')
                self.f.live(archive)
                self.f.write(image, b'locally-changed-image')
                removed = self.remove(package_id)
                self.assertNotIn('backupPath', removed)
                with self.assertRaisesRegex(importer.ImportRejected, 'existing-data-conflict'):
                    self.f.live(archive)
                self.assertEqual((self.f.root/image).read_bytes(), b'locally-changed-image')
                self.f.assert_base_preserved()

    def test_delete_ignores_unlisted_neighbours_and_missing_owned_file(self):
        other='_internal/assets/suju/user-note.png'
        package=self.f.package('nightmare-suju',{SUJU:b'image'})
        self.f.live(package); self.f.write(other,b'keep')
        (self.f.root/SUJU).unlink()  # Fixture only: an already missing managed file.
        result=self.remove()
        self.assertEqual(result['removedFiles'],0)
        self.assertEqual((self.f.root/other).read_bytes(),b'keep')
        self.f.assert_base_preserved()

    def test_cancel_after_move_rolls_back_files_and_receipt(self):
        second='_internal/assets/suju/second.png'
        package=self.f.package('nightmare-suju',{SUJU:b'first',second:b'second'})
        self.f.live(package); original=self.f.receipt().read_bytes()
        real=transaction._move_owned
        calls=[]
        def move(source,target,*args):
            real(source,target,*args); calls.append(str(source))
        with patch.object(transaction,'_move_owned',side_effect=move):
            with self.assertRaisesRegex(importer.ImportRejected,'import-cancelled'):
                self.remove(cancelled=lambda: bool(calls))
        self.assertEqual((self.f.root/SUJU).read_bytes(),b'first')
        self.assertEqual((self.f.root/second).read_bytes(),b'second')
        self.assertEqual(self.f.receipt().read_bytes(),original)

    def test_io_failure_rolls_back_and_no_recursive_delete(self):
        second='_internal/assets/suju/second.png'
        self.f.live(self.f.package('nightmare-suju',{SUJU:b'first',second:b'second'}))
        real=transaction._move_owned
        def move(source,target,*args):
            if str(source).endswith('second.png'):raise OSError('fixture-locked')
            return real(source,target,*args)
        with patch.object(transaction,'_move_owned',side_effect=move):
            with self.assertRaisesRegex(OSError,'fixture-locked'):self.remove()
        self.assertEqual((self.f.root/SUJU).read_bytes(),b'first')
        self.assertTrue(self.f.receipt().is_file())
        state={r['packageId']:r for r in manager.managed_author_packages_snapshot(self.f.root)}
        self.assertEqual(state['nightmare-suju']['status'],'installed')
        self.assertTrue(state['nightmare-suju']['canRemove'])

    def test_confirmation_receipt_swap_and_junction_target_fail_closed(self):
        self.f.live(self.f.package('nightmare-suju',{SUJU:b'image'}))
        plan=self.plan()
        receipt=self.f.receipt();original=receipt.read_bytes()
        receipt.write_bytes(original+b'\n')
        with self.assertRaisesRegex(importer.ImportRejected,'installed-receipt-changed'):
            manager.remove_author_package_live(self.f.root,'nightmare-suju',application_idle=True,
                expected_receipt_sha256=plan['receiptSha256'], expected_plan_sha256=plan['planSha256'])
        receipt.write_bytes(original)
        real=Path.lstat
        junction=self.f.root/'_internal/assets/suju'
        def lstat(path, *args, **kwargs):
            result=real(path,*args,**kwargs)
            if path==junction:
                return SimpleNamespace(st_mode=result.st_mode,st_file_attributes=0x400)
            return result
        # Windows reparse attribute seam, no admin-created real junction required.
        with patch.object(Path,'lstat',lstat):
            with self.assertRaisesRegex(importer.ImportRejected,'linked-target'):
                self.plan()
        self.assertEqual((self.f.root/SUJU).read_bytes(),b'image')
        self.assertEqual(receipt.read_bytes(),original)

    def test_receipt_change_and_nonidle_rejected_before_mutation(self):
        self.f.live(self.f.package('nightmare-suju',{SUJU:b'image'}))
        plan = self.plan()
        for idle,digest,reason in [(False,'0'*64,'requires-idle'),(True,None,'confirmation'),(True,'0'*64,'receipt-changed')]:
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(importer.ImportRejected,reason):
                    manager.remove_author_package_live(self.f.root,'nightmare-suju',application_idle=idle,
                        expected_receipt_sha256=digest, expected_plan_sha256=plan['planSha256'])
        self.assertEqual((self.f.root/SUJU).read_bytes(),b'image')

    def test_malicious_receipt_cannot_claim_core_or_outside_paths(self):
        self.f.live(self.f.package('nightmare-suju',{SUJU:b'image'}))
        receipt=self.f.receipt();original=receipt.read_bytes()
        for path in ['../outside.png','_internal/references/fast-structure/x.npz','_internal/assets/suju/file.png.']:
            doc=json.loads(original);doc['files'][0]['path']=path;receipt.write_text(json.dumps(doc))
            with self.assertRaises(importer.ImportRejected):self.plan()
        receipt.write_bytes(original)
        self.assertEqual((self.f.root/SUJU).read_bytes(),b'image')

    def test_status_and_local_error_do_not_depend_on_catalog(self):
        self.f.live(self.f.package('nightmare-suju',{SUJU:b'image'}))
        result={r['packageId']:r for r in manager.managed_author_packages_snapshot(self.f.root)}
        self.assertEqual(result['nightmare-suju']['status'],'installed')
        self.assertTrue(result['nightmare-suju']['canRemove'])
        self.assertIn('本地已有不同版本',manager.package_inspection_message('ImportRejected: existing-data-conflict: x.png'))
        self.assertIn('离线',manager.package_inspection_message('BadZipFile: broken'))


if __name__=='__main__':unittest.main()
