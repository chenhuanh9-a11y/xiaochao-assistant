"""Own offscreen Qt windows: accepted close, reopen, selection and main usability."""
import os
from pathlib import Path
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
from PySide6.QtCore import QObject, Property, QPointF, Qt, QUrl, Signal, QCoreApplication, QEvent
from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication, QImage
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest

from client_preview import PreviewService
from client_windows import WindowController
from client_paths import WORK_ROOT

APP = QGuiApplication.instance() or QGuiApplication([])
if Path("C:/Windows/Fonts/msyh.ttc").is_file():
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
APP.setFont(QFont("Microsoft YaHei UI", 10))


def until(predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        APP.processEvents()
        if predicate():
            return
        QTest.qWait(2)
    raise AssertionError("Preview UI condition timed out")


class Selection(QObject):
    changed = Signal()
    overlayRequested = Signal()

    def __init__(self):
        super().__init__()
        self.image, self.title, self.floor = "", "", ""

    selectedImage = Property(str, lambda self: self.image, notify=changed)
    selectedTitle = Property(str, lambda self: self.title, notify=changed)
    selectedFloor = Property(str, lambda self: self.floor, notify=changed)

    def choose(self, image, title, floor):
        self.image, self.title, self.floor = image, title, floor
        self.changed.emit()


class PreviewTests(unittest.TestCase):
    def setUp(self):
        WORK_ROOT.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="qt-preview-test-", dir=WORK_ROOT)
        self.folder = Path(self.temp.name)
        self.backend = Selection()
        self.windows = WindowController()
        self.service = PreviewService(self.backend, self.windows)
        self.backend.overlayRequested.connect(self.service.show)
        self.engine = QQmlApplicationEngine()
        self.warnings = []
        self.engine.warnings.connect(lambda rows: self.warnings.extend(r.toString() for r in rows))
        self.engine.rootContext().setContextProperty("previewService", self.service)
        here = Path(__file__).parent
        self.engine.loadData(b'import QtQuick\nimport QtQuick.Controls.Basic\nWindow { id: main; width: 500; height: 300; visible: true; property int actions: 0; Button { objectName: "mainAction"; text: "Main remains usable"; anchors.centerIn: parent; onClicked: main.actions += 1 } }', QUrl.fromLocalFile(str(here / "PreviewMainEvidence.qml")))
        self.main = self.engine.rootObjects()[0]
        self.windows.bindWindow("main", self.main)
        self.engine.load(QUrl.fromLocalFile(str(here / "ClientPreview.qml")))
        self.assertEqual(len(self.engine.rootObjects()), 2, self.warnings)
        self.preview = self.engine.rootObjects()[1]
        self.windows.bindWindow("preview", self.preview)
        self.reference = QQuickWindow()
        self.windows.bindWindow("reference", self.reference)
        self.windows.show("reference")
        self.reference_state = self.windows.snapshot["reference"]
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.service.shutdown()
        self.windows.shutdown()
        self.engine.deleteLater()
        self.reference.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()
        self.temp.cleanup()

    def choose(self, name="synthetic-map", floor="1", color=0xffb8d5ed):
        path = self.folder / (name + ".png")
        image = QImage(320, 240, QImage.Format_RGBA8888)
        image.fill(color)
        self.assertTrue(image.save(str(path)))
        self.backend.choose(QUrl.fromLocalFile(str(path)).toString(), name, floor)

    def click(self, window, name):
        item = window.findChild(QQuickItem, name)
        self.assertIsNotNone(item)
        point = item.mapToScene(QPointF(item.width()/2, item.height()/2))
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point.toPoint())
        APP.processEvents()

    def test_actual_close_reopen_and_selection_keep_main_and_reference_independent(self):
        self.choose()
        self.backend.overlayRequested.emit()
        until(lambda: self.preview.isVisible())
        image = self.preview.findChild(QQuickItem, "previewMapImage")
        until(lambda: image.property("progress") == 1 and image.property("paintedWidth") > 0)
        self.assertEqual(self.preview.flags() & Qt.WindowType_Mask, Qt.Window)
        self.assertFalse(self.preview.flags() & (Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput))
        self.assertTrue(self.preview.flags() & Qt.WindowCloseButtonHint)
        self.assertEqual(self.preview.modality(), Qt.NonModal)
        self.assertIn("synthetic-map", self.preview.title())
        self.click(self.preview, "previewZoomIn")
        self.assertGreater(self.preview.property("zoom"), 1)
        self.backend.changed.emit()
        self.assertGreater(self.preview.property("zoom"), 1)
        self.choose("second-map", "B1", 0xffcde6be)
        until(lambda: image.property("progress") == 1 and "second-map" in self.preview.title())
        self.assertIn("B1 地下室", self.preview.title())
        self.assertEqual(self.preview.property("zoom"), 1)

        # QWindow.close() traverses the real Qt close event used by title-bar X.
        self.assertTrue(self.preview.close())
        until(lambda: not self.preview.isVisible())
        self.assertFalse(self.service.visible)
        self.assertTrue(self.main.isVisible())
        self.assertEqual(self.windows.snapshot["reference"], self.reference_state)
        self.click(self.main, "mainAction")
        self.assertEqual(self.main.property("actions"), 1)
        self.assertTrue(self.service.show())
        until(lambda: image.property("progress") == 1)
        self.assertEqual(len(self.engine.rootObjects()), 2)
        self.click(self.preview, "previewCloseButton")
        until(lambda: not self.preview.isVisible())
        self.assertTrue(self.main.isVisible())
        self.assertTrue(self.service.show())
        until(lambda: image.property("progress") == 1)
        self.assertTrue(self.service.close())
        until(lambda: not self.preview.isVisible())
        self.assertFalse(self.warnings, "\n".join(self.warnings))

    def test_missing_selection_and_shutdown_do_not_change_other_window_lifecycles(self):
        self.assertFalse(self.service.show())
        self.assertIn("可预览", self.service.message)
        self.choose()
        self.assertTrue(self.service.show())
        self.windows.setClickThrough("preview", True)
        self.assertFalse(self.windows.snapshot["preview"]["clickThrough"])
        self.service.shutdown()
        self.assertFalse(self.service.show())
        self.backend.choose("", "cleared", "")
        self.assertTrue(self.main.isVisible())
        self.assertEqual(self.windows.snapshot["reference"], self.reference_state)


if __name__ == "__main__":
    unittest.main()
