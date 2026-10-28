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
            expected_receipt_sha256=plan['receiptSha256'], **kwargs)

    def test_four_packages_repeat_remove_reimport_preserve_core_and_custom(self):
        payloads = {'nightmare-suju': {SUJU:b'suju'},
            'difficult-zhan10': {NATIVE:b'zhan',LEGACY:b'legacy',CORE:self.f.core_bytes},
            'nightmare-wang-qingxin': {WANG:b'wang'},
            'nightmare-nightingale': {NIGHTINGALE:b'ai'}}
        for package, author in importer.AUTHOR_PACKAGES.items():
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
                backup = Path(removed['backupPath'])
                self.assertTrue((backup/'installed-receipt.json').is_file())
                for path, data in payload.items():
                    if importer._live_author_path(package,path):
                        self.assertFalse((self.f.root/path).exists())
                        self.assertEqual((backup/path).read_bytes(),data)
                    else:
                        self.assertEqual((self.f.root/path).read_bytes(),data)
                self.assertEqual(importer.verified_installed_package(self.f.root,package)['status'],'not-installed')
                self.assertEqual(self.f.live(archive)['status'],'author-installed-live')
                self.f.assert_base_preserved()

    def test_unknown_files_and_modified_owned_files_are_not_deleted(self):
        self.f.write(SUJU,b'unmanaged')
        with self.assertRaisesRegex(importer.ImportRejected,'not-managed'):
            self.plan()
        state={r['packageId']:r for r in manager.managed_author_packages_snapshot(self.f.root)}
        self.assertEqual(state['nightmare-suju']['status'],'unmanaged')
        self.assertFalse(state['nightmare-suju']['canRemove'])
        archive=self.f.package('nightmare-suju',{SUJU:b'unmanaged'})
        self.f.live(archive)  # Only exact archive bytes can adopt existing files.
        self.f.write(SUJU,b'user-modified')
        with self.assertRaisesRegex(importer.ImportRejected,'modified-author-file'):
            self.plan()
        self.assertEqual((self.f.root/SUJU).read_bytes(),b'user-modified')

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
        real=manager.os.replace
        calls=[]
        def move(source,target):
            real(source,target); calls.append(str(source))
        with patch.object(manager.os,'replace',side_effect=move):
            with self.assertRaisesRegex(importer.ImportRejected,'import-cancelled'):
                self.remove(cancelled=lambda: bool(calls))
        self.assertEqual((self.f.root/SUJU).read_bytes(),b'first')
        self.assertEqual((self.f.root/second).read_bytes(),b'second')
        self.assertEqual(self.f.receipt().read_bytes(),original)

    def test_io_failure_rolls_back_and_no_recursive_delete(self):
        second='_internal/assets/suju/second.png'
        self.f.live(self.f.package('nightmare-suju',{SUJU:b'first',second:b'second'}))
        real=manager.os.replace
        def move(source,target):
            if str(source).endswith('second.png'):raise OSError('fixture-locked')
            return real(source,target)
        with patch.object(manager.os,'replace',side_effect=move):
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
                expected_receipt_sha256=plan['receiptSha256'])
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
        for idle,digest,reason in [(False,'0'*64,'requires-idle'),(True,None,'confirmation'),(True,'0'*64,'receipt-changed')]:
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(importer.ImportRejected,reason):
                    manager.remove_author_package_live(self.f.root,'nightmare-suju',application_idle=idle,
                                                      expected_receipt_sha256=digest)
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
