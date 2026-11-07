"""Actual QML history controls with mouse/accessibility in an offscreen window."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


class AuthorHistoryPageTests(unittest.TestCase):
    def test_history_preview_cancel_restore_and_current_button_state(self):
        code = r'''
from pathlib import Path
import threading
from unittest.mock import patch
from PySide6.QtGui import QGuiApplication, QAccessible
from PySide6.QtCore import QUrl, QObject, Property, QPointF, Qt
from PySide6.QtQuick import QQuickView
from PySide6.QtTest import QTest
app = QGuiApplication([])
from test_client_author_snapshots import ClientAuthorSnapshotsTests
import author_resource_snapshots as snapshots
import author_snapshot_history as history
case = ClientAuthorSnapshotsTests(); case.setUp(); case.install()
first = snapshots.SnapshotPackage(case.root, case.profile)
case.install(fmt='jpeg'); second = snapshots.SnapshotPackage(case.root, case.profile)
view = QQuickView(); warnings = []
view.engine().warnings.connect(lambda rows: warnings.extend(str(row) for row in rows))
view.rootContext().setContextProperty('resourceService', case.resources)
view.rootContext().setContextProperty('packageManagerService', case.manager)
class Context(QObject):
    season = Property(str, lambda self: 's0', constant=True)
context = Context(); view.rootContext().setContextProperty('backend', context)
view.setSource(QUrl.fromLocalFile(str(Path('experiments/qt_quick/ClientResources.qml').resolve())))
assert view.status() == QQuickView.Ready, view.errors()
view.setResizeMode(QQuickView.SizeRootObjectToView); view.resize(760, 680); view.show()
view.rootObject().setProperty('active', True)
QTest.qWait(160); case.settle(case.resources); case.settle(case.manager); QTest.qWait(60)
def find(item, name):
    if item.objectName() == name: return item
    for child in item.childItems():
        found = find(child, name)
        if found is not None: return found
def click(name):
    item = find(view.contentItem(), name)
    assert item is not None and item.isVisible() and item.isEnabled(), name
    point = item.mapToScene(QPointF(item.width()/2, item.height()/2))
    assert 0 <= point.x() < view.width() and 0 <= point.y() < view.height(), (name, point)
    QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, point.toPoint()); QTest.qWait(40)
click('packageHistoryButton-' + case.profile); case.settle(case.manager); QTest.qWait(80)
old_name = 'restoreAuthorVersion-' + first.activation['snapshotId']
current_name = 'restoreAuthorVersion-' + second.activation['snapshotId']
assert find(view.contentItem(), old_name).isEnabled()
assert not find(view.contentItem(), current_name).isEnabled()
assert QAccessible.queryAccessibleInterface(find(view.contentItem(), old_name)).text(QAccessible.Name).startswith('核对并回退作者版本 ')
click(old_name); case.settle(case.manager); QTest.qWait(40)
assert case.manager.pendingKind == 'restore-author-version'
assert '不回退程序、识别底模或设置' in case.prompts[-1][1]
click('resourceConfirmCancelButton'); second.assert_active()
assert not case.lease.active
click(old_name); case.settle(case.manager); QTest.qWait(40)
click('resourceConfirmAcceptButton'); case.settle(case.manager); QTest.qWait(80)
current = snapshots.SnapshotPackage(case.root, case.profile)
assert current.activation['snapshotId'] == first.activation['snapshotId']
assert first.directory.exists() and second.directory.exists()
assert not find(view.contentItem(), old_name).isEnabled()
assert find(view.contentItem(), current_name).isEnabled()
assert '作者图已回退' in case.manager.message
assert not case.errors, case.errors
# Closing a slow history refresh must not reopen the dialog on a late result.
entered, release = threading.Event(), threading.Event()
real = history.list_versions
def delayed(*args, **kwargs):
    result = real(*args, **kwargs); entered.set(); release.wait(5); return result
with patch.object(history, 'list_versions', side_effect=delayed):
    click('refreshAuthorHistoryButton'); assert entered.wait(5)
    old_button = find(view.contentItem(), old_name)
    click('closeAuthorHistoryButton'); QTest.qWait(150)
    assert not old_button.isVisible()
    release.set(); case.settle(case.manager); QTest.qWait(150)
    assert not old_button.isVisible()
# The same close rule suppresses a late unaccepted confirmation. An accepted
# write follows its own lifecycle and is tested separately by the service.
click('packageHistoryButton-' + case.profile); case.settle(case.manager); QTest.qWait(80)
entered, release = threading.Event(), threading.Event()
real_inspect = history.inspect_restore
def delayed_inspect(*args, **kwargs):
    result = real_inspect(*args, **kwargs); entered.set(); release.wait(5); return result
before = len(case.prompts)
with patch.object(history, 'inspect_restore', side_effect=delayed_inspect):
    click(current_name); assert entered.wait(5)
    click('closeAuthorHistoryButton'); release.set(); case.settle(case.manager); QTest.qWait(150)
assert len(case.prompts) == before
assert case.manager.pendingKind == ''
current.assert_active()
assert view.rootObject().property('active')
# Navigation closes only this still-unaccepted history confirmation, not an
# unrelated import/remove confirmation and not an accepted restore write.
click('packageHistoryButton-' + case.profile); case.settle(case.manager); QTest.qWait(80)
click(current_name); case.settle(case.manager); QTest.qWait(40)
accept = find(view.contentItem(), 'resourceConfirmAcceptButton')
assert accept.isVisible() and case.manager.pendingKind == 'restore-author-version'
view.rootObject().setProperty('active', False); QTest.qWait(180)
assert not accept.isVisible()
assert case.manager.pendingKind == '' and not case.lease.active
current.assert_active()
assert not warnings, warnings
view.setSource(QUrl()); case.doCleanups()
print('author history actual controls passed')
'''
        root = Path(__file__).resolve().parents[2]
        run = subprocess.run([sys.executable,'-B','-c',code],cwd=root,
            env=dict(os.environ,QT_QPA_PLATFORM='offscreen',QT_QUICK_BACKEND='software',
                PYTHONPATH=os.pathsep.join(str(root/path) for path in ('experiments/qt_quick','app','tests'))),
            capture_output=True,text=True,timeout=40)
        self.assertEqual(run.returncode,0,run.stdout+run.stderr)
        self.assertIn('author history actual controls passed',run.stdout)
