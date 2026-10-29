"""Approved releases, not installed bytes, are the ownership authority."""
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
import test_live_author_import as fixture


class LegacyAdoptionTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.LiveAuthorImportTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.paths = {'nightmare-suju': fixture.SUJU, 'difficult-zhan10': fixture.NATIVE,
                      'nightmare-nightingale': fixture.NIGHTINGALE, 'nightmare-wang-qingxin': fixture.WANG}
        self.inventory = {package: dict(archiveSha256='a'*64, archiveName=package+'.zip',
            files=[fixture.row(path,b'approved')]) for package,path in self.paths.items()}
        self.stub = patch.object(manager,'LEGACY_AUTHOR_INVENTORY',self.inventory)
        self.stub.start(); self.addCleanup(self.stub.stop)

    def test_real_generated_inventory_schema_can_be_consumed_without_core(self):
        from legacy_author_inventory import LEGACY_AUTHOR_INVENTORY
        for package,release in LEGACY_AUTHOR_INVENTORY.items():
            for row in release['files']:
                self.assertIn(row['role'],{'author-image','author-sidecar'})
                self.assertTrue(importer._live_author_path(package,row['path']))
            # All real records reach file absence, not invalid-file-record.
            with patch.object(manager,'LEGACY_AUTHOR_INVENTORY',LEGACY_AUTHOR_INVENTORY):
                self.assertEqual(manager.adopt_legacy_author_package(self.f.root,package)['status'],'not-installed')

    def test_four_old_sets_automatic_snapshot_adoption_remove_reimport_idempotence(self):
        for path in self.paths.values():
            self.f.write(path,b'approved')
        snapshot = manager.managed_author_packages_snapshot(self.f.root)
        self.assertTrue(all(item['status']=='installed' and item['canRemove'] for item in snapshot))
        receipts = {package: (self.f.receipt(package).read_bytes(), self.f.receipt(package).stat().st_ino)
                    for package in self.paths}
        manager.managed_author_packages_snapshot(self.f.root)
        for package, (raw, inode) in receipts.items():
            self.assertEqual(self.f.receipt(package).read_bytes(), raw)
            self.assertEqual(self.f.receipt(package).stat().st_ino, inode)
        for package,path in self.paths.items():
            receipt = self.f.receipt(package)
            before = receipt.read_bytes()
            self.assertEqual(json.loads(before)['legacyAdoption']['scope'],'author-display-only')
            self.assertEqual(manager.adopt_legacy_author_package(self.f.root,package)['status'],'already-managed')
            self.assertEqual(receipt.read_bytes(),before)
            plan = manager.inspect_author_package_removal(self.f.root,package)
            manager.remove_author_package_live(self.f.root,package,application_idle=True,
                expected_receipt_sha256=plan['receiptSha256'], expected_plan_sha256=plan['planSha256'])
            self.assertEqual(manager.adopt_legacy_author_package(self.f.root,package)['status'],'not-installed')
            archive=self.f.package(package,{path:b'new-approved'},package+'.zip')
            self.assertEqual(self.f.live(archive)['status'],'author-installed-live')
            self.assertEqual(self.f.live(archive)['status'],'author-already-installed')
            self.f.assert_base_preserved()

    def test_partial_modified_and_extra_unknown_files(self):
        package='nightmare-suju'; second='_internal/assets/suju/other.png'
        self.inventory[package]['files'].append(fixture.row(second,b'other'))
        self.f.write(fixture.SUJU,b'approved')
        self.assertEqual(manager.adopt_legacy_author_package(self.f.root,package)['status'],'legacy-unverified')
        self.assertFalse(self.f.receipt().exists())
        self.f.write(second,b'edited')
        self.assertEqual(manager.adopt_legacy_author_package(self.f.root,package)['status'],'legacy-unverified')
        self.f.write(second,b'other'); unknown='_internal/assets/suju/personal.png'
        self.f.write(unknown,b'personal')
        manager.adopt_legacy_author_package(self.f.root,package)
        self.assertNotIn(unknown,[r['path'] for r in json.loads(self.f.receipt().read_bytes())['files']])

    def test_core_or_escaping_inventory_never_authorizes(self):
        for path in [fixture.CORE,'_internal/assets/suju/../../references/core.png']:
            with self.subTest(path=path):
                self.inventory['nightmare-suju']['files']=[fixture.row(path,b'approved')]
                with self.assertRaises(importer.ImportRejected):
                    manager.adopt_legacy_author_package(self.f.root,'nightmare-suju')
                self.assertFalse(self.f.receipt().exists())

    def test_cancel_and_raced_receipt_never_overwrite(self):
        self.f.write(fixture.SUJU,b'approved')
        with self.assertRaisesRegex(importer.ImportRejected,'cancelled'):
            manager.adopt_legacy_author_package(self.f.root,'nightmare-suju',cancelled=lambda: True)
        self.assertFalse(self.f.receipt().exists())
        real=manager.os.link
        def race(source,target):
            Path(target).write_bytes(b'other writer')
            real(source,target)
        with patch.object(manager.os,'link',side_effect=race):
            with self.assertRaises(FileExistsError):
                manager.adopt_legacy_author_package(self.f.root,'nightmare-suju')
        self.assertEqual(self.f.receipt().read_bytes(),b'other writer')
        self.assertEqual((self.f.root/fixture.SUJU).read_bytes(),b'approved')

    def test_changed_file_after_hash_is_not_adopted(self):
        self.f.write(fixture.SUJU,b'approved')
        real=manager.os.fsync
        def change(fd):
            real(fd); self.f.write(fixture.SUJU,b'changed!')
        with patch.object(manager.os,'fsync',side_effect=change):
            with self.assertRaisesRegex(importer.ImportRejected,'file-changed'):
                manager.adopt_legacy_author_package(self.f.root,'nightmare-suju')
        self.assertFalse(self.f.receipt().exists())

    def test_snapshot_does_not_swallow_cancellation_from_adoption(self):
        self.f.write(fixture.SUJU,b'approved')
        cancelled = []
        real = manager._sha
        def cancel_after_hash(path):
            result = real(path)
            cancelled.append(True)
            return result
        with patch.object(manager,'_sha',side_effect=cancel_after_hash):
            with self.assertRaisesRegex(importer.ImportRejected,'import-cancelled'):
                manager.managed_author_packages_snapshot(self.f.root,cancelled=lambda:bool(cancelled))
        self.assertFalse(self.f.receipt().exists())

    def test_link_rejected_and_existing_invalid_receipt_not_replaced(self):
        self.f.write(fixture.SUJU,b'approved')
        real=Path.lstat
        junction=self.f.root/'_internal/assets/suju'
        def lstat(path,*args,**kwargs):
            result=real(path,*args,**kwargs)
            if path==junction:
                return SimpleNamespace(st_mode=result.st_mode,st_file_attributes=0x400)
            return result
        # Real _target/_no_links with Windows junction attribute seam.
        with patch.object(Path,'lstat',lstat):
            with self.assertRaisesRegex(importer.ImportRejected,'linked-target'):
                manager.adopt_legacy_author_package(self.f.root,'nightmare-suju')
        self.assertFalse(self.f.receipt().exists())
        self.f.receipt().parent.mkdir(parents=True,exist_ok=True)
        self.f.receipt().write_bytes(b'invalid receipt')
        with self.assertRaises(importer.ImportRejected):
            manager.adopt_legacy_author_package(self.f.root,'nightmare-suju')
        self.assertEqual(self.f.receipt().read_bytes(),b'invalid receipt')


if __name__=='__main__': unittest.main()
