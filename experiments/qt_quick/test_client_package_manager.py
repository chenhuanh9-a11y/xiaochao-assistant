"""Real deletion service and actual resource-page buttons over synthetic data."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from client_package_manager import PackageManagerService
import test_client_resources as fixtures
import map_package_removal as removal


class PackageManagerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ResourceServiceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.install()
        self.service = PackageManagerService(self.fixture.root, self.fixture.service)
        self.service.setLeaseProvider(self.fixture.lease)
        self.problems, self.changes, self.completions, self.confirmations = [], [], [], []
        self.service.errorReported.connect(self.problems.append)
        self.service.resourcesChanged.connect(lambda: self.changes.append(True))
        self.service.removalCompleted.connect(lambda *args: self.completions.append(args))
        self.service.confirmationRequested.connect(lambda *args: self.confirmations.append(args))
        def cleanup():
            self.service.shutdown()
            self.service.pool.waitForDone(15000)
            fixtures.APP.processEvents()
        self.addCleanup(cleanup)

    def settle(self, condition=None):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            fixtures.APP.processEvents()
            if not self.service.busy and (condition is None or condition()):
                fixtures.APP.processEvents()
                return
            time.sleep(.005)
        self.fail("package service did not settle")

    def confirm(self):
        self.service.requestRemove("nightmare-suju")
        self.settle()
        self.assertEqual(self.service.pendingKind, "remove")
        self.assertTrue(self.fixture.lease.active)
        self.service.confirmOperation(True)
        self.settle()

    def test_actual_delete_refresh_and_final_success_only_after_no_temp_copy(self):
        self.confirm()
        self.assertEqual(self.changes, [True])
        self.assertEqual(len(self.completions), 1)
        self.assertIn("临时文件已清理完", self.completions[0][1])
        self.assertEqual(self.service.packageRows, [])
        self.assertFalse((self.fixture.root / fixtures.IMAGE).exists())
        self.assertFalse(self.fixture.lease.active)
        self.assertEqual(list((self.fixture.root / "work/map-packages").glob("removals/*/objects/*")), [])

    def test_confirmation_cancel_retains_files_and_releases_lease(self):
        self.service.requestClearAll()
        self.settle()
        self.service.confirmOperation(False)
        self.assertTrue((self.fixture.root / fixtures.IMAGE).exists())
        self.assertFalse(self.fixture.lease.active)
        self.assertEqual(self.changes, [])

    def test_modified_file_keeps_receipt_and_never_announces_complete_delete(self):
        self.fixture.write(fixtures.IMAGE, b"modified-by-user")
        self.confirm()
        self.assertTrue((self.fixture.root / "work/map-packages/nightmare-suju.json").exists())
        self.assertEqual((self.fixture.root / fixtures.IMAGE).read_bytes(), b"modified-by-user")
        self.assertEqual(self.completions, [])
        self.assertIn("未能全部删除", self.service.message)
        self.assertEqual(self.problems[-1]["kind"], "warning")

    def test_committed_cleanup_error_keeps_lease_and_never_announces_success(self):
        original = removal.files._rollback_published
        def fail_cleanup(path, identity, digest):
            if path.parent.name == "objects":
                return False
            return original(path, identity, digest)
        with patch.object(removal.files, "_rollback_published", side_effect=fail_cleanup):
            self.confirm()
        self.assertEqual(self.completions, [])
        self.assertIn("已删除", self.service.message)
        self.assertIn("清理结束后才会提示完成", self.service.message)
        self.assertTrue(self.service.recoveryRequired)
        self.assertTrue(self.fixture.lease.active)
        self.assertFalse((self.fixture.root / fixtures.IMAGE).exists())

    def test_stale_completion_cannot_republish_and_busy_resource_refuses_delete(self):
        self.fixture.service._busy = True
        self.service.requestClearAll()
        self.assertFalse(self.service.busy)
        self.fixture.service._busy = False
        self.confirm()
        self.service._completed(self.service._token - 1, {}, None)
        self.assertEqual(len(self.completions), 1)
        self.assertEqual(self.changes, [True])

    def test_failed_or_cancelled_remove_invalidates_only_after_safe_rollback(self):
        for cancel in (False, True):
            observed = []
            callback = lambda: observed.append(((self.fixture.root / fixtures.IMAGE).exists(), self.fixture.lease.active))
            self.service.resourcesChanged.connect(callback)
            original = removal.files._rollback_published
            def interrupt(path, identity, digest):
                result = original(path, identity, digest)
                if path == self.fixture.root / fixtures.IMAGE:
                    if cancel:
                        self.service._cancel.set()
                    else:
                        raise OSError("injected after first deletion")
                return result
            with patch.object(removal.files, "_rollback_published", side_effect=interrupt):
                self.confirm()
            self.assertEqual(observed, [(True, False)])
            self.assertEqual(self.completions, [])
            self.assertFalse(self.service.recoveryRequired)
            self.service.resourcesChanged.disconnect(callback)

    def test_main_import_signal_queues_refresh_until_real_session_lease_is_released(self):
        from client_session import SessionService
        session = SessionService()
        self.addCleanup(session.shutdown)
        self.fixture.service.setLeaseProvider(session)
        self.service.setLeaseProvider(session)
        self.fixture.service.resourcesChanged.connect(self.service.requestRefresh)
        self.service.requestRefresh()
        self.settle(lambda: len(self.service.packageRows) == 1)
        self.service.requestClearAll()
        self.settle()
        self.service.confirmOperation(True)
        self.settle(lambda: self.service.packageRows == [])
        self.fixture.install(self.fixture.package("reimport.zip"))
        self.settle(lambda: not self.fixture.service.busy and len(self.service.packageRows) == 1)
        self.assertEqual(self.service.packageRows[0]["packageId"], "nightmare-suju")
        self.assertFalse(self.service._refresh_requested)
        self.assertFalse(session.snapshot["resourceLocked"])


class PackageManagerQmlTests(unittest.TestCase):
    def test_actual_resource_page_delete_cancel_confirm_and_clear_all_buttons(self):
        code = r'''
import hashlib,json,sys,time,zipfile
from pathlib import Path
from unittest.mock import patch
from PySide6.QtGui import QGuiApplication
from PySide6.QtCore import QUrl, QPointF, Qt, QObject, Property
from PySide6.QtQuick import QQuickView
from PySide6.QtTest import QTest
app=QGuiApplication([])
from test_client_package_manager import PackageManagerTests
from client_resources import importer
case=PackageManagerTests();case.setUp()
second='_internal/assets/authors/wang-qingxin/map-03-2.png'
data=b'second-synthetic'
archive=case.fixture.folder/'second.zip'
manifest=dict(schemaVersion=1,revision=1,packageId='nightmare-wang-qingxin',
    clientCompatibility=importer.CLIENT_COMPATIBILITY,requiresFiles=case.fixture.dependencies,
    files=[dict(path=second,bytes=len(data),sha256=hashlib.sha256(data).hexdigest())])
with zipfile.ZipFile(archive,'w') as z:
    z.writestr('map-package.json',json.dumps(manifest));z.writestr('data/'+second,data)
importer.install_author_package_live(archive,case.fixture.root,application_idle=True)
case.fixture.service.recordImportReceipt(dict(success=True,requestId='fixture-helper-result',
    packageId='nightmare-suju',added=1,alreadyPresent=0))
default=case.fixture.folder/'bundled-default'
relative='assets/zhan10-native/fixture.png'
image=default/relative;image.parent.mkdir(parents=True);image.write_bytes(b'bundled-fixture')
(default/'inventory.json').write_text(json.dumps(dict(schema='xingtu-bundled-author-v1',packageId='difficult-zhan10',
    files={relative:dict(bytes=image.stat().st_size,sha256=hashlib.sha256(image.read_bytes()).hexdigest())})))
case.enterContext(patch('client_resources.bundled_author_root',return_value=default))
case.fixture.service.refresh();case.settle()
view=QQuickView();warnings=[]
view.engine().warnings.connect(lambda rows:warnings.extend(str(r) for r in rows))
view.rootContext().setContextProperty('resourceService',case.fixture.service)
view.rootContext().setContextProperty('packageManagerService',case.service)
class BrowseContext(QObject):
    season=Property(str,lambda self:'s0',constant=True)
browse=BrowseContext()
view.rootContext().setContextProperty('backend',browse)
view.setSource(QUrl.fromLocalFile(str(Path('experiments/qt_quick/ClientResources.qml').resolve())))
assert view.status()==QQuickView.Ready, view.errors()
view.rootObject().setProperty("active", True)  # Explicitly open the management page.
view.setResizeMode(QQuickView.SizeRootObjectToView);view.resize(1100,680);view.show()
case.settle(lambda:len(case.service.packageRows)==2);QTest.qWait(60)
assert len(case.fixture.completions)==2,case.fixture.completions
def find(item,name):
    if item.objectName()==name:return item
    for child in item.childItems():
        result=find(child,name)
        if result is not None:return result
def click(name):
    item=find(view.contentItem(),name)
    assert item is not None,name
    assert item.isVisible() and item.isEnabled(),name
    point=item.mapToScene(QPointF(item.width()/2,item.height()/2))
    assert 0<=point.x()<view.width() and 0<=point.y()<view.height(),(name,point)
    QTest.mouseClick(view,Qt.LeftButton,Qt.NoModifier,point.toPoint());QTest.qWait(30)
assert find(view.contentItem(),'managedPackageList').property('count')==3
assert not find(view.contentItem(),'removePackageButton-builtin-difficult-zhan10').isEnabled()
click('removePackageButton-nightmare-suju');case.settle();QTest.qWait(30)
assert case.service.pendingKind=='remove'
click('resourceConfirmCancelButton')
assert (case.fixture.root/'_internal/assets/suju/map-01-1.png').exists()
click('removePackageButton-nightmare-suju');case.settle();QTest.qWait(30)
click('resourceConfirmAcceptButton');case.settle(lambda:len(case.service.packageRows)==1);QTest.qWait(30)
assert not (case.fixture.root/'_internal/assets/suju/map-01-1.png').exists()
click('clearManagedPackagesButton');case.settle();QTest.qWait(30)
click('resourceConfirmAcceptButton');case.settle(lambda:len(case.service.packageRows)==0)
assert not (case.fixture.root/second).exists()
QTest.qWait(30)
assert find(view.contentItem(),'managedPackageList').property('count')==1
assert image.read_bytes()==b'bundled-fixture'
assert len(case.completions)==2 and case.changes==[True,True],(case.completions,case.changes)
assert not warnings,warnings
view.setSource(QUrl());case.doCleanups()
print(json.dumps(dict(actualButtons=True,deleteAndClearAll=True,qmlWarnings=warnings)))
'''
        root = Path(__file__).resolve().parents[2]
        result = subprocess.run([sys.executable, "-c", code], cwd=root,
            env=dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software",
                     PYTHONPATH=os.pathsep.join(str(root / path) for path in ("experiments/qt_quick", "app", "tests"))),
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('"actualButtons": true', result.stdout)


if __name__ == "__main__":
    unittest.main()
