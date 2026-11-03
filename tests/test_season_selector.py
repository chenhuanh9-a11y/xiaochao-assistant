"""Real offscreen selector controls; synthetic catalog only, no game/native IO."""
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'app'), str(ROOT/'experiments/qt_quick')]
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
try:
    from PySide6.QtCore import QUrl, QPoint, QPointF, Qt, QCoreApplication, QEvent
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlEngine, QQmlComponent
    from PySide6.QtQuick import QQuickWindow
    from PySide6.QtTest import QTest
    QT = True
except ImportError:
    QT = False


@unittest.skipUnless(QT, 'PySide6 required')
class SeasonSelectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        self.engine = QQmlEngine()
        self.warnings = []
        self.engine.warnings.connect(lambda rows: self.warnings.extend(map(str, rows)))
        self.component = QQmlComponent(self.engine, QUrl.fromLocalFile(str(ROOT/'experiments/qt_quick/SeasonSelector.qml')))
        self.root = self.component.create()
        self.assertIsNotNone(self.root, [str(e) for e in self.component.errors()])
        self.window = QQuickWindow()
        self.window.resize(360, 300)
        self.root.setParentItem(self.window.contentItem())
        self.root.setObjectName('selector')
        self.root.setWidth(360)
        self.root.setHeight(44)
        self.rows = [{'id':'s0', 'number':0, 'name':'', 'label':'S0'},
                     {'id':'s1', 'number':1, 'name':'', 'label':'S1'}]
        self.root.setProperty('seasons', self.rows)
        self.root.setProperty('season', 's0')
        self.root.setProperty('mode', 'nightmare')
        self.requests = []
        def accept(season, mode):
            self.requests.append((season, mode))
            self.root.setProperty('season', season)
            self.root.setProperty('mode', mode)
        self.root.contextRequested.connect(accept)
        self.window.show()
        QTest.qWait(30)

    def tearDown(self):
        self.window.close()
        self.root.setParentItem(None)
        self.root.deleteLater()
        self.window.deleteLater()
        self.engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def item(self, name):
        def walk(item):
            yield item
            for child in item.childItems():
                yield from walk(child)
        return next(item for item in walk(self.window.contentItem()) if item.objectName() == name)

    def click(self, item):
        point = item.mapToScene(QPointF(item.width()/2, item.height()/2))
        self.assertGreaterEqual(point.x(), 0)
        self.assertGreaterEqual(point.y(), 0)
        self.assertLess(point.x(), self.window.width())
        self.assertLess(point.y(), self.window.height())
        QTest.mouseClick(self.window, Qt.LeftButton, Qt.NoModifier, QPoint(round(point.x()), round(point.y())))
        QTest.qWait(20)

    def select_season(self, identity):
        self.click(self.item('selectorSeason'))
        self.click(self.item('selectorSeasonOption_'+identity))

    def test_dropdown_then_difficulty_repeat_and_disabled_requests(self):
        self.assertEqual(self.item('selectorSeason').property('count'), 2)
        self.select_season('s1')
        self.assertEqual(self.requests, [('s1','nightmare')])
        self.click(self.item('selectorDifficult'))
        self.assertEqual(self.requests[-1], ('s1','difficult'))
        count = len(self.requests)
        self.click(self.item('selectorDifficult'))
        self.select_season('s1')
        self.assertEqual(len(self.requests), count)
        self.root.setProperty('enabled', False)
        self.click(self.item('selectorNightmare'))
        self.assertEqual(len(self.requests), count)
        self.assertFalse(self.item('selectorSeason').isEnabled())
        self.assertFalse(self.warnings, '\n'.join(self.warnings))

    def test_rejected_or_pending_request_keeps_authoritative_season_visible(self):
        self.root.contextRequested.disconnect()
        self.root.contextRequested.connect(lambda season, mode: self.requests.append((season, mode)))
        self.select_season('s1')
        self.assertEqual(self.requests, [('s1','nightmare')])
        self.assertEqual(self.root.property('season'), 's0')
        self.assertEqual(self.item('selectorSeason').property('currentIndex'), 0)
        self.assertEqual(self.item('selectorSeason').property('displayText'), 'S0')
        self.root.setProperty('season', 's1')
        self.assertEqual(self.item('selectorSeason').property('displayText'), 'S1')

    def test_new_s2_catalog_row_needs_no_ui_edit_and_layout_fits(self):
        # The name is a synthetic fixture, not a production season name.
        self.root.setProperty('seasons', self.rows + [{'id':'s2','number':2,'name':'测试名','label':'S2 测试名'}])
        self.assertEqual(self.item('selectorSeason').property('count'), 3)
        self.select_season('s2')
        self.assertEqual(self.requests[-1], ('s2','nightmare'))
        self.assertEqual(self.item('selectorSeason').property('displayText'), 'S2 测试名')
        self.click(self.item('selectorDifficult'))
        self.assertEqual(self.requests[-1], ('s2','difficult'))
        for width in (240, 320, 600):
            self.window.resize(width, 300)
            self.root.setWidth(width)
            QTest.qWait(20)
            for name in ('selectorSeason','selectorNightmare','selectorDifficult'):
                item = self.item(name)
                self.assertTrue(item.isVisible())
                left = item.mapToScene(QPointF()).x()
                self.assertGreaterEqual(left, 0)
                self.assertLessEqual(left+item.width(), width)
                self.assertGreater(item.height(), 0)
            self.assertFalse(self.window.grabWindow().isNull())
        self.assertFalse(self.warnings, '\n'.join(self.warnings))
