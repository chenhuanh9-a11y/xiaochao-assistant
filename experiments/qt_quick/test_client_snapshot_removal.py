"""Real manager/lease and versioned import, isolated synthetic files only."""
from pathlib import Path
import shutil
import tempfile
import time
import unittest

from PySide6.QtGui import QGuiApplication
APP = QGuiApplication.instance() or QGuiApplication([])

import test_author_resource_snapshots as fixture
from client_paths import WORK_ROOT
from client_resources import ResourceService
from client_package_manager import PackageManagerService
from client_session import SessionService
import author_resource_snapshots as snapshots


class ClientSnapshotRemovalTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.AuthorSnapshotTests('runTest'); self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        WORK_ROOT.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=WORK_ROOT, prefix='snapshot-removal-')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / 'install'
        shutil.copytree(self.f.root, root)
        (root / '.qt-lab-owner').write_text('qt-quick-isolated-v1')
        self.f.root = self.f.fixture.root = self.root = root.resolve()
        self.f.install(self.f.package())
        self.resources = ResourceService(self.root)
        self.session = SessionService()
        self.service = PackageManagerService(self.root, self.resources)
        self.resources.setLeaseProvider(self.session)
        self.service.setLeaseProvider(self.session)
        self.confirmations, self.completions, self.problems, self.changes = [], [], [], []
        self.service.confirmationRequested.connect(lambda *args: self.confirmations.append(args))
        self.service.removalCompleted.connect(lambda *args: self.completions.append(args))
        self.service.errorReported.connect(self.problems.append)
        self.service.resourcesChanged.connect(lambda: self.changes.append(True))
        self.addCleanup(self.close)

    def close(self):
        self.service.shutdown(); self.resources.shutdown()
        self.service.pool.waitForDone(15000); self.resources.pool.waitForDone(15000)
        self.session.shutdown(); APP.processEvents()

    def wait(self):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            APP.processEvents()
            if not self.service.busy:
                return
            time.sleep(.002)
        self.fail('manager worker timeout')

    def test_single_snapshot_delete_uses_real_scope_not_deactivation(self):
        self.service.requestRemove(self.f.profile); self.wait()
        self.assertEqual(self.service.pendingKind, 'remove')
        self.assertIn('删除', self.confirmations[-1][0])
        self.assertIn('成功后不保留', self.confirmations[-1][1])
        self.assertTrue(self.session.snapshot['resourceLocked'])
        self.service.confirmOperation(True); self.wait()
        self.assertFalse(self.problems, self.problems)
        self.assertEqual(len(self.completions), 1)
        self.assertFalse(self.session.snapshot['resourceLocked'])
        self.assertEqual(self.service.packageRows, [])
        self.assertFalse(snapshots.managed(self.root, self.f.profile))
        self.assertEqual(self.changes, [True])

    def test_clear_all_cancel_repeated_request_and_confirm(self):
        self.service.requestClearAll(); self.wait()
        self.assertEqual(self.service.pendingKind, 'remove')
        self.assertIn('清理全部', self.confirmations[-1][0])
        self.service.requestClearAll()
        self.assertEqual(len(self.confirmations), 1)
        self.service.confirmOperation(False)
        self.assertTrue(snapshots.managed(self.root, self.f.profile))
        self.assertFalse(self.session.snapshot['resourceLocked'])
        self.assertEqual(self.changes, [])
        self.service.requestClearAll(); self.wait()
        self.service.confirmOperation(True); self.wait()
        self.assertFalse(self.problems, self.problems)
        self.assertEqual(self.service.packageRows, [])
        self.assertFalse(snapshots.managed(self.root, self.f.profile))
        self.assertEqual(len(self.completions), 1)

    def test_changed_confirmation_target_fails_and_releases_lease(self):
        self.service.requestClearAll(); self.wait()
        image = next((self.root / snapshots.STORE / 'versions').rglob('*.png'))
        image.write_bytes(b'user-changed-after-confirmation')
        self.service.confirmOperation(True); self.wait()
        self.assertTrue(self.problems)
        self.assertFalse(self.completions)
        self.assertFalse(self.session.snapshot['resourceLocked'])
        self.assertTrue(snapshots.managed(self.root, self.f.profile))
        self.assertEqual(image.read_bytes(), b'user-changed-after-confirmation')
