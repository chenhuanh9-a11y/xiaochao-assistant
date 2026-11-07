"""Real queued service and transaction over synthetic author/base resources."""
import threading
import unittest
from unittest.mock import patch

import test_client_author_snapshots as fixtures
import author_resource_snapshots as snapshots
import author_snapshot_history as history


class ClientAuthorHistoryTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.ClientAuthorSnapshotsTests(); self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.case.install(); self.first = snapshots.SnapshotPackage(self.case.root,self.case.profile)
        self.case.install(fmt='jpeg'); self.second = snapshots.SnapshotPackage(self.case.root,self.case.profile)
        self.manager = self.case.manager
        self.changes = []
        self.manager.resourcesChanged.connect(lambda: self.changes.append(True))

    def row(self):
        self.manager.requestHistory(self.case.profile); self.case.settle(self.manager)
        return next(row for row in self.manager.historyRows if row['snapshotId']==self.first.activation['snapshotId'])

    def inspect(self):
        row = self.row()
        self.manager.requestRestoreVersion(self.case.profile,row['sourceTransactionId'])
        self.case.settle(self.manager)
        self.assertEqual(self.manager.pendingKind,'restore-author-version')

    def test_preview_cancel_then_confirm_refreshes_and_invalidates(self):
        self.inspect()
        self.assertTrue(self.case.lease.active)
        self.assertIn('仅切换作者显示版本',self.case.prompts[-1][1])
        self.manager.confirmOperation(False)
        self.second.assert_active(); self.assertFalse(self.case.lease.active)
        self.assertEqual(self.changes,[])
        self.inspect(); self.manager.confirmOperation(True); self.case.settle(self.manager)
        self.assertEqual(self.case.errors,[])
        current=snapshots.SnapshotPackage(self.case.root,self.case.profile)
        self.assertEqual(current.activation['snapshotId'],self.first.activation['snapshotId'])
        with self.assertRaises(ValueError):self.second.assert_active()
        self.assertEqual(self.changes,[True]);self.assertFalse(self.case.lease.active)
        self.assertEqual(self.case.completed[-1][0],'作者图已回退')
        self.assertTrue(next(row for row in self.manager.historyRows if row['snapshotId']==current.activation['snapshotId'])['current'])

    def test_modified_historical_image_fails_before_confirmation(self):
        row=self.row(); path=self.first.directory/'files/0000.png'
        # The stale view cannot read after update; use only the physical path
        # of this synthetic fixture for the deliberate mutation.
        path.write_bytes(b'user-modified')
        self.manager.requestRestoreVersion(self.case.profile,row['sourceTransactionId']);self.case.settle(self.manager)
        self.assertEqual(self.manager.pendingKind,'');self.assertTrue(self.case.errors)
        self.assertFalse(self.case.lease.active);self.second.assert_active()
        self.assertEqual(path.read_bytes(),b'user-modified')

    def test_restore_cleanup_pending_keeps_lease_and_reports_uncertain_state(self):
        self.inspect(); previous=len(self.case.completed)
        with patch.object(snapshots,'_clear_pointer',side_effect=OSError('cleanup')):
            self.manager.confirmOperation(True);self.case.settle(self.manager)
        self.assertTrue(self.manager.recoveryRequired);self.assertTrue(self.case.lease.active)
        self.assertIn('需要恢复核对',self.manager.message)
        self.assertNotIn('图包删除',self.manager.message)
        self.assertEqual(len(self.case.completed),previous)
        self.assertEqual(self.manager.historyRows,[])
        self.assertTrue((self.case.root/snapshots.PENDING).exists())

    def test_cancel_and_shutdown_ignore_late_history_results(self):
        real=history.list_versions
        for shutdown in (False,True):
            entered,release=threading.Event(),threading.Event()
            def delayed(*args,**kwargs):
                result=real(*args,**kwargs);entered.set();release.wait(5);return result
            ready=[];self.manager.historyReady.connect(ready.append)
            with patch.object(history,'list_versions',side_effect=delayed):
                self.manager.requestHistory(self.case.profile)
                self.assertTrue(entered.wait(5))
                if shutdown:self.manager.shutdown()
                else:self.manager.cancel()
                release.set();self.manager.pool.waitForDone(10000)
                fixtures.APP.processEvents()
            self.assertEqual(ready,[]);self.assertEqual(self.manager.historyRows,[])
            self.assertFalse(self.case.lease.active);self.second.assert_active()

    def test_busy_resource_or_session_disallows_restore_inspection(self):
        self.case.resources._busy=True;self.manager.requestHistory(self.case.profile)
        self.assertFalse(self.manager.busy);self.case.resources._busy=False
        self.case.lease.allowed=False;self.manager.requestHistory(self.case.profile)
        self.assertFalse(self.manager.busy);self.assertEqual(self.manager.historyRows,[])
        self.assertIn('停止后再管理',self.manager.message)

    def test_import_event_invalidates_history_without_changing_selection(self):
        self.row();self.assertTrue(self.manager.historyRows)
        self.case.resources.resourcesChanged.emit()
        self.assertEqual(self.manager.historyRows,[])
        self.assertEqual(self.manager.historyPackageId,self.case.profile)
        self.second.assert_active()

    def test_real_client_roundtrip_preserves_verified_history_and_success(self):
        # These are the real client.py connections, not present in the basic
        # fixture. The source event must still invalidate runtime consumers.
        self.manager.resourcesChanged.connect(self.case.resources.resourcesChanged.emit)
        self.case.resources.resourcesChanged.connect(self.manager.requestRefresh)
        external=[];self.case.resources.resourcesChanged.connect(lambda:external.append(True))
        self.inspect();self.manager.confirmOperation(True);self.case.settle(self.manager)
        self.assertEqual(external,[True]);self.assertTrue(self.manager.historyRows)
        self.assertEqual(self.case.completed[-1][0],'作者图已回退')
        self.assertIn('当前启用 1 层',self.case.completed[-1][1])
        self.assertIn('当前启用 1 层',self.manager.message)
        self.assertFalse(self.case.lease.active)

    def test_cancel_postcommit_inventory_read_does_not_claim_restore_cancelled(self):
        self.manager.resourcesChanged.connect(self.case.resources.resourcesChanged.emit)
        self.case.resources.resourcesChanged.connect(self.manager.requestRefresh)
        self.inspect();real=history.list_versions
        def cancelled(*args,**kwargs):
            self.manager._cancel.set()
            return real(*args,**kwargs)
        with patch.object(history,'list_versions',side_effect=cancelled):
            self.manager.confirmOperation(True);self.case.settle(self.manager)
        self.assertEqual(snapshots.SnapshotPackage(self.case.root,self.case.profile).activation['snapshotId'],
                         self.first.activation['snapshotId'])
        self.assertIn('作者版本已切换',self.manager.message)
        self.assertIn('已取消列表核对',self.manager.message)
        self.assertFalse(self.case.lease.active)

    def test_close_history_read_cancels_late_confirmation_not_accepted_write(self):
        row=self.row();real=history.inspect_restore
        entered,release=threading.Event(),threading.Event()
        def delayed(*args,**kwargs):
            result=real(*args,**kwargs);entered.set();release.wait(5);return result
        before=len(self.case.prompts)
        with patch.object(history,'inspect_restore',side_effect=delayed):
            self.manager.requestRestoreVersion(self.case.profile,row['sourceTransactionId'])
            self.assertTrue(entered.wait(5));self.manager.dismissHistory();release.set()
            self.case.settle(self.manager)
        self.assertEqual(len(self.case.prompts),before)
        self.assertEqual(self.manager.pendingKind,'');self.second.assert_active()
        self.assertFalse(self.case.lease.active)
        self.inspect();self.manager.confirmOperation(True);self.manager.dismissHistory()
        self.case.settle(self.manager)
        self.assertEqual(snapshots.SnapshotPackage(self.case.root,self.case.profile).activation['snapshotId'],
                         self.first.activation['snapshotId'])
