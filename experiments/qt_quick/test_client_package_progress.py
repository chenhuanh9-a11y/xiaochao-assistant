"""Actual offscreen QML component, with controlled service states and no I/O."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import unittest
from PySide6.QtCore import QObject, Property, Signal, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickWindow
from PySide6.QtQml import QQmlEngine, QQmlComponent

APP = QGuiApplication.instance() or QGuiApplication([])


class Local(QObject):
    changed = Signal()
    def __init__(self):
        super().__init__()
        self.active, self.stage = False, ''
    busy = Property(bool, lambda s: s.active, notify=changed)
    operationStage = Property(str, lambda s: s.stage, notify=changed)
    message = Property(str, lambda s: 'fixture status', notify=changed)
    def update(self, active, stage):
        self.active, self.stage = active, stage
        self.changed.emit()


class PackageProgressTests(unittest.TestCase):
    def setUp(self):
        self.engine = QQmlEngine()
        self.component = QQmlComponent(self.engine, QUrl.fromLocalFile(str(Path(__file__).with_name('ClientPackageProgress.qml'))))
        self.assertFalse(self.component.isError(), str(self.component.errors()))
        self.window = QQuickWindow()
        self.window.show()
        self.local = Local()
        self.card = self.component.createWithInitialProperties({'localService': self.local, 'width': 350})
        self.assertIsNotNone(self.card, str(self.component.errors()))
        self.card.setParentItem(self.window.contentItem())
        self.bar = self.card.findChild(QObject, 'packageProgressBar')
    def tearDown(self):
        self.card.setProperty('localService', None)
        self.card.deleteLater()
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()
    def online(self, received=None, total=None, **fields):
        self.card.setProperty('onlineState', dict(busy=True, stage='downloading', progress=dict(receivedBytes=received, totalBytes=total), **fields))
    def test_byte_percentage_is_download_only(self):
        self.online(25, 100)
        self.assertTrue(self.card.property('visible'))
        self.assertEqual(self.card.property('percent'), 25)
        self.assertFalse(self.bar.property('indeterminate'))
        self.online(100, 100)
        self.assertTrue(self.bar.property('indeterminate'))
        self.assertIn('正在校验', self.card.property('phase'))
        self.assertNotIn('100%', self.card.property('phase'))
    def test_unknown_size_and_cancel_do_not_invent_percentage(self):
        self.online(4096, None)
        self.assertTrue(self.bar.property('indeterminate'))
        self.assertNotIn('%', self.card.property('phase'))
        self.online(25, 100, cancelling=True)
        self.assertTrue(self.bar.property('indeterminate'))
        self.assertEqual(self.card.property('phase'), '正在取消下载')
    def test_local_handoff_wins_over_stale_download_and_terminal_hides(self):
        self.online(100, 100)
        for stage, title in [('inspect', '校验图包'), ('install', '解压并导入'), ('refresh-after-write', '核对安装结果'), ('cancelling', '正在取消')]:
            self.local.update(True, stage)
            self.assertEqual(self.card.property('phase'), title)
            self.assertTrue(self.bar.property('indeterminate'))
            self.assertEqual(self.card.property('percent'), 0)
        self.card.setProperty('onlineState', dict(busy=False, stage='error'))
        self.local.update(False, '')
        self.assertFalse(self.card.property('visible'))
        self.assertFalse(self.bar.property('indeterminate'))
        self.assertEqual(self.card.property('implicitHeight'), 0)
    def test_repeated_state_and_reentry_keep_same_progress_item(self):
        self.online(20, 100)
        for _ in range(5):
            self.online(20, 100)
            self.assertEqual(self.card.findChild(QObject, 'packageProgressBar'), self.bar)
        self.card.setProperty('onlineState', dict(busy=False))
        self.online(0, 200)
        self.assertEqual(self.card.property('percent'), 0)
        self.assertTrue(self.card.property('visible'))
    def test_online_snapshot_carries_local_handoff_without_context_dependency(self):
        self.card.setProperty('localService', None)
        self.card.setProperty('onlineState', dict(busy=False, stage='handoff', localBusy=True,
            localStage='install', localMessage='fixture import', progress=dict(receivedBytes=100, totalBytes=100)))
        self.assertTrue(self.card.property('visible'))
        self.assertEqual(self.card.property('phase'), '解压并导入')
        self.assertTrue(self.bar.property('indeterminate'))

    def test_index_stage_is_not_download(self):
        self.card.setProperty('onlineState', dict(busy=True, stage='checking'))
        self.assertEqual(self.card.property('phase'), '读取在线列表')
        self.assertTrue(self.bar.property('indeterminate'))


if __name__ == '__main__':
    unittest.main()
