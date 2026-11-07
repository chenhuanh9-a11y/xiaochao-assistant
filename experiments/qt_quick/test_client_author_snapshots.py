"""Queued Qt service contracts with synthetic installed bases, not native GUI QA."""
import time
import unittest
from unittest.mock import patch
from PySide6.QtCore import QCoreApplication, QUrl
import test_generic_author_package as fixtures
from test_client_resources import Lease
from client_resources import ResourceService, _duplicate_import
from client_package_manager import PackageManagerService
import author_resource_snapshots as snapshots
import star_map_import as imp

APP = QCoreApplication.instance() or QCoreApplication([])


class ClientAuthorSnapshotsTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.GenericPackageTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.root=self.fixture.root;self.profile=self.fixture.profile
        # The synthetic tree is not a real installed executable. Bypass only the
        # lab-root classifier, never archive, filesystem, lease or transaction checks.
        for name in ('client_resources.validate_installation_root','client_package_manager.validate_installation_root', 'client_paths.validate_installation_root'):
            patcher=patch(name,return_value=self.root);patcher.start();self.addCleanup(patcher.stop)
        self.resources=ResourceService(self.root);self.manager=PackageManagerService(self.root,self.resources)
        self.lease=Lease();self.resources.setLeaseProvider(self.lease);self.manager.setLeaseProvider(self.lease)
        self.prompts=[];self.completed=[];self.errors=[]
        self.resources.confirmationRequested.connect(lambda *args:self.prompts.append(args))
        self.resources.importCompleted.connect(lambda *args:self.completed.append(args))
        self.resources.errorReported.connect(self.errors.append)
        self.manager.confirmationRequested.connect(lambda *args:self.prompts.append(args))
        self.manager.removalCompleted.connect(lambda *args:self.completed.append(args))
        self.manager.errorReported.connect(self.errors.append)
        def cleanup():
            for service in (self.resources,self.manager):service.shutdown();service.pool.waitForDone(10000)
            APP.processEvents()
        self.addCleanup(cleanup)

    def settle(self, service):
        end=time.monotonic()+10
        while time.monotonic()<end:
            APP.processEvents()
            if not service.busy:
                APP.processEvents();return
            time.sleep(.005)
        self.fail('service did not settle')

    def install(self, **kwargs):
        self.resources.importArchive(QUrl.fromLocalFile(str(self.fixture.package(**kwargs))).toString(),'s0');self.settle(self.resources)
        if self.resources.pendingKind:
            self.resources.confirmOperation(True);self.settle(self.resources)
        self.assertEqual(self.errors,[])

    def test_ui_defaults_to_snapshots_and_update_asks_then_invalidates(self):
        self.install();first=snapshots.SnapshotPackage(self.root,self.profile)
        self.resources.importArchive(QUrl.fromLocalFile(str(self.fixture.package(fmt='jpeg'))).toString(),'s0');self.settle(self.resources)
        self.assertEqual(self.resources.pendingKind,'overwrite');first.assert_active()
        self.assertIn('改变 1 层图片格式',self.prompts[-1][1])
        self.resources.confirmOperation(True);self.settle(self.resources)
        self.assertEqual(self.errors,[]);self.assertEqual(len(self.completed),2)
        with self.assertRaises(ValueError):first.assert_active()
        self.assertFalse(self.lease.active)

    def test_actual_management_entry_deletes_verified_snapshots(self):
        self.install();view=snapshots.SnapshotPackage(self.root,self.profile)
        self.manager.requestRemove(self.profile);self.settle(self.manager)
        self.assertEqual(self.manager.pendingKind,'remove');self.assertIn('成功后不保留',self.prompts[-1][1])
        self.manager.confirmOperation(True);self.settle(self.manager)
        self.assertEqual(self.errors,[]);self.assertEqual(imp.verified_installed_package(self.root,self.profile)['status'],'not-installed')
        self.assertFalse(view.directory.exists());self.assertIn('图包删除完成',self.manager.message)
        self.assertFalse(self.lease.active)

    def test_batch_purge_includes_snapshots_and_waits_for_confirmation(self):
        self.install();view=snapshots.SnapshotPackage(self.root,self.profile)
        self.manager.requestClearAll();self.settle(self.manager)
        self.assertIn('请确认删除范围',self.manager.message);view.assert_active()
        self.assertEqual(self.manager.pendingKind,'remove');self.assertTrue(self.lease.active)
        self.manager.confirmOperation(True);self.settle(self.manager)
        self.assertFalse(view.directory.exists());self.assertFalse(self.lease.active)

    def test_cancel_update_retains_active_generation(self):
        self.install();view=snapshots.SnapshotPackage(self.root,self.profile)
        self.resources.importArchive(QUrl.fromLocalFile(str(self.fixture.package(fmt='jpeg'))).toString(),'s0');self.settle(self.resources)
        self.resources.confirmOperation(False);view.assert_active();self.assertFalse(self.lease.active)

    def test_fixture_uses_local_file_url_and_remote_input_stays_rejected(self):
        # QFileDialog emits a file URL on every platform. A bare Windows drive
        # path is a QUrl scheme, not the UI contract; do not weaken that guard.
        with patch.object(self.resources, 'importArchive', wraps=self.resources.importArchive) as entry:
            self.install()
        self.assertTrue(QUrl(entry.call_args.args[0]).isLocalFile())
        current = snapshots.SnapshotPackage(self.root, self.profile)
        self.resources.importArchive('https://example.invalid/author.zip', 's0')
        self.assertFalse(self.resources.busy)
        self.assertEqual(self.errors[-1]['errorCode'], 'non-local-archive')
        current.assert_active()

    def test_replacement_version_not_mislabeled_duplicate(self):
        self.assertFalse(_duplicate_import(dict(storageFormat=snapshots.SNAPSHOT_SCHEMA,status='author-installed-live',
            added=0,alreadyPresent=2,replaceFiles=[],replaced=0)))
