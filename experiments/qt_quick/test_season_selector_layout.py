"""Responsive selector geometry uses real QML and never suppresses Qt warnings."""
import os
from pathlib import Path
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QUrl, QPointF, Qt
from PySide6.QtGui import QGuiApplication, QFont
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest


class SeasonSelectorLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def test_nested_layout_resize_font_and_visibility_keep_geometry_stable(self):
        engine = QQmlEngine()
        warnings = []
        engine.warnings.connect(lambda rows: warnings.extend(str(row) for row in rows))
        component = QQmlComponent(engine)
        component.setData(b'''import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
ApplicationWindow {
    id: window; visible: true; width: 600; height: 260
    ColumnLayout {
        anchors.fill: parent
        RowLayout {
            Layout.fillWidth: true
            SeasonSelector {
                objectName: "selector"
                seasons: [{id:"s0", label:"S0", capabilities:{}}, {id:"s1", label:"S1", capabilities:{}}]
                season: "s0"; mode: "nightmare"
            }
            Item { Layout.fillWidth: true }
        }
        Item { Layout.fillHeight: true }
    }
}''', QUrl.fromLocalFile(str(Path(__file__).with_name('__season_layout_test__.qml'))))
        self.assertFalse(component.isError(), str(component.errors()))
        window = component.create()
        self.assertIsNotNone(window, str(component.errors()))
        try:
            selector = window.findChild(QQuickItem, 'selector')
            picker = window.findChild(QQuickItem, 'selectorSeason')
            difficult = window.findChild(QQuickItem, 'selectorDifficult')
            nightmare = window.findChild(QQuickItem, 'selectorNightmare')
            for size in (9, 12, 18):
                picker.setProperty('font', QFont('Sans Serif', size))
                for season in ('s0', 's1', 's0'):
                    selector.setProperty('season', season)
                    for width in (600, 240, 600):
                        selector.setVisible(False)
                        window.setWidth(width)
                        selector.setVisible(True)
                        QTest.qWait(20)
                        self.assertEqual(selector.property('stacked'), width == 240, (size, season, width, window.width(), selector.width(), selector.property('singleRowWidth')))
                        geometries = [(item.x(), item.y(), item.width(), item.height())
                                      for item in (selector, picker, nightmare, difficult)]
                        QTest.qWait(20)
                        self.assertEqual(geometries, [(item.x(), item.y(), item.width(), item.height())
                                                     for item in (selector, picker, nightmare, difficult)])
                        for item in (picker, nightmare, difficult):
                            pos = item.mapToItem(selector, QPointF())
                            self.assertGreaterEqual(pos.x(), 0)
                            self.assertGreaterEqual(pos.y(), 0)
                            self.assertLessEqual(pos.x() + item.width(), selector.width() + .01)
                            self.assertLessEqual(pos.y() + item.height(), selector.height() + .01)
                        self.assertLessEqual(selector.width(), window.width())
            self.assertEqual(warnings, [])
            requested = []
            selector.contextRequested.connect(lambda season, mode: requested.append((season, mode)))
            point = difficult.mapToScene(difficult.boundingRect().center()).toPoint()
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
            self.assertEqual(requested, [('s0', 'difficult')])
            self.assertEqual(warnings, [])
        finally:
            window.close()
            window.deleteLater()
            self.app.processEvents()

    def test_missing_base_disables_mode_and_imported_base_enables_only_its_mode(self):
        engine = QQmlEngine()
        component = QQmlComponent(engine)
        component.setData(b'''import QtQuick
import QtQuick.Controls.Basic
ApplicationWindow {
    visible: true; width: 440; height: 150
    SeasonSelector {
        objectName: "availabilitySelector"; width: parent.width
        seasons: [{id:"s0", label:"S0", capabilities:{
            nightmare:{available:false}, difficult:{available:false}}}]
        season: "s0"; mode: "nightmare"
    }
}''', QUrl.fromLocalFile(str(Path(__file__).with_name('__season_availability_test__.qml'))))
        self.assertFalse(component.isError(), str(component.errors()))
        window = component.create()
        self.assertIsNotNone(window, str(component.errors()))
        try:
            selector = window.findChild(QQuickItem, 'availabilitySelector')
            nightmare = window.findChild(QQuickItem, 'availabilitySelectorNightmare')
            difficult = window.findChild(QQuickItem, 'availabilitySelectorDifficult')
            requested = []
            selector.contextRequested.connect(lambda season, mode: requested.append((season, mode)))
            self.assertFalse(nightmare.isEnabled())
            self.assertFalse(difficult.isEnabled())
            QTest.qWait(20)
            point = difficult.mapToScene(difficult.boundingRect().center()).toPoint()
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
            self.assertEqual(requested, [])
            selector.setProperty('seasons', [dict(id='s0', label='S0', capabilities=dict(
                nightmare=dict(available=False), difficult=dict(available=True)))])
            QTest.qWait(20)
            self.assertFalse(nightmare.isEnabled())
            self.assertTrue(difficult.isEnabled())
            point = difficult.mapToScene(difficult.boundingRect().center()).toPoint()
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
            self.assertEqual(requested, [('s0', 'difficult')])
        finally:
            window.close()
            window.deleteLater()
            self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
