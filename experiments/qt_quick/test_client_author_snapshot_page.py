"""Actual resource-page snapshot controls in an offscreen window, no real game."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


class SnapshotPageTests(unittest.TestCase):
    def test_snapshot_button_cancel_preserves_then_confirm_deletes(self):
        code = r'''
from pathlib import Path
import time
from PySide6.QtGui import QGuiApplication, QAccessible
from PySide6.QtCore import QUrl, QObject, Property, QPointF, Qt
from PySide6.QtQuick import QQuickView
from PySide6.QtTest import QTest
app = QGuiApplication([])
from test_client_author_snapshots import ClientAuthorSnapshotsTests
import author_resource_snapshots as snapshots
case = ClientAuthorSnapshotsTests(); case.setUp(); case.install()
old = snapshots.SnapshotPackage(case.root, case.profile)
view = QQuickView(); warnings = []
view.engine().warnings.connect(lambda rows: warnings.extend(str(r) for r in rows))
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
    QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, point.toPoint()); QTest.qWait(30)
button_name = 'removePackageButton-' + case.profile
button = find(view.contentItem(), button_name)
assert button.property('text') == '检查并删除'
assert QAccessible.queryAccessibleInterface(button).text(QAccessible.Name).startswith('删除图包 ')
def visible_texts(item):
    value = item.property('text')
    return ([value] if isinstance(value, str) and item.isVisible() else []) + [text for child in item.childItems() for text in visible_texts(child)]
assert not any('所有文件和历史版本都会保留' in text for text in visible_texts(view.contentItem()))
# Retention and disk consequences remain explicit in the action confirmation.
click(button_name); case.settle(case.manager); QTest.qWait(30)
assert '成功后不保留' in case.prompts[-1][1]
click('resourceConfirmCancelButton'); old.assert_active()
click(button_name); case.settle(case.manager); QTest.qWait(30)
click('resourceConfirmAcceptButton'); case.settle(case.manager); QTest.qWait(30)
assert not old.directory.exists()
assert snapshots._read_activation(case.root, case.profile)[0] is None
assert find(view.contentItem(), button_name) is None
assert '图包删除完成' in case.manager.message
assert not warnings, warnings
view.setSource(QUrl()); case.doCleanups()
print('snapshot page cancel and confirmed removal passed')
'''
        root = Path(__file__).resolve().parents[2]
        run = subprocess.run([sys.executable, '-c', code], cwd=root,
            env=dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                     PYTHONPATH=os.pathsep.join(str(root / x) for x in ('experiments/qt_quick', 'app', 'tests'))),
            capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('confirmed removal passed', run.stdout)
