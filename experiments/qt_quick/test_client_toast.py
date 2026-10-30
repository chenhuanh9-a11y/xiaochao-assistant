"""Event-only notification replacement/expiry in owned offscreen Qt windows."""
import os
from pathlib import Path
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
from PySide6.QtCore import QObject, Property, QUrl, Qt, Signal, QCoreApplication, QEvent
from PySide6.QtGui import QGuiApplication, QFontDatabase
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest
from client_windows import WindowController

APP = QGuiApplication.instance() or QGuiApplication([])
if Path("C:/Windows/Fonts/msyh.ttc").is_file():
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")


def until(predicate):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        APP.processEvents()
        if predicate():
            return
        QTest.qWait(2)
    raise AssertionError("Toast event did not settle")


class Coordinator(QObject):
    notificationRequested = Signal(str, str, str)

    def __init__(self):
        super().__init__()
        self.area = dict(x=-1280, y=32, width=1280, height=988)

    notificationGeometry = Property("QVariantMap", lambda self: self.area)


class ToastTests(unittest.TestCase):
    def setUp(self):
        self.coordinator = Coordinator()
        self.windows = WindowController()
        self.main = QQuickWindow()
        self.windows.bindWindow("main", self.main)
        self.windows.show("main")
        self.engine = QQmlApplicationEngine()
        self.warnings = []
        self.engine.warnings.connect(lambda rows: self.warnings.extend(r.toString() for r in rows))
        self.engine.rootContext().setContextProperty("clientCoordinator", self.coordinator)
        self.engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name("ClientToast.qml"))))
        self.assertTrue(self.engine.rootObjects(), self.warnings)
        self.toast = self.engine.rootObjects()[0]
        self.windows.bindWindow("toast", self.toast)
        self.windows.setClickThrough("toast", True)
        self.expiry = self.toast.findChild(QObject, "toastExpiry")
        self.fade = self.toast.findChild(QObject, "toastFade")
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.toast.close()
        self.windows.shutdown()
        self.engine.deleteLater()
        self.main.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def assert_in_area(self):
        area = self.coordinator.area
        self.assertGreaterEqual(self.toast.x(), area['x'])
        self.assertGreaterEqual(self.toast.y(), area['y'])
        self.assertLessEqual(self.toast.x()+self.toast.width(), area['x']+area['width'])
        self.assertLessEqual(self.toast.y()+self.toast.height(), area['y']+area['height'])

    def assert_status_icon(self, kind, name, color):
        self.assertEqual(self.toast.property("statusKind"), kind)
        self.assertEqual(self.toast.findChild(QObject, "toastStatusGlyph").property("iconName"), name)
        self.assertEqual(self.toast.findChild(QObject, "toastStatusIcon").property("color").name(), color)

    def test_event_replaces_fade_expires_and_close_clears_jobs_without_focus(self):
        self.assertFalse(self.toast.isVisible())
        self.assertEqual(self.expiry.property("interval"), 3200)
        self.assertEqual(self.fade.property("duration"), 280)
        self.assertTrue(self.toast.flags() & Qt.WindowDoesNotAcceptFocus)
        self.assertTrue(self.toast.flags() & Qt.WindowStaysOnTopHint)
        self.assertTrue(self.toast.flags() & Qt.WindowTransparentForInput)
        self.assertEqual(self.toast.flags() & Qt.WindowType_Mask, Qt.Tool)
        self.coordinator.notificationRequested.emit("地图已确认", "正在核对路线", "success")
        until(lambda: self.toast.isVisible())
        self.assert_status_icon("success", "check", "#17775a")
        self.assert_in_area()
        # Accelerate only the test clock after checking the shipped 3200 ms.
        self.expiry.setProperty("interval", 35)
        until(lambda: bool(self.fade.property("running")))
        self.main.hide()  # Notifications remain independent of main visibility.
        self.coordinator.notificationRequested.emit("新事件", "原样短文案", "warning")
        self.assertEqual(self.toast.property("eventRevision"), 2)
        self.assertEqual(self.toast.property("notificationMessage"), "原样短文案")
        self.assertEqual(self.toast.property("notificationKind"), "warning")
        self.assert_status_icon("warning", "warning", "#b7791f")
        self.assertFalse(self.fade.property("running"))
        self.assertEqual(self.toast.opacity(), 1)
        until(lambda: not self.toast.isVisible())
        self.assertFalse(self.expiry.property("running"))
        self.assertFalse(self.fade.property("running"))
        self.coordinator.notificationRequested.emit("失败事件", "失败原因原样保留", "error")
        self.assertTrue(self.toast.isVisible())
        self.assert_status_icon("error", "cross", "#b8364a")
        self.assertTrue(self.toast.close())
        self.assertFalse(self.toast.isVisible())
        self.assertFalse(self.expiry.property("running"))
        self.assertFalse(self.fade.property("running"))
        QTest.qWait(80)
        self.assertFalse(self.toast.isVisible())
        self.assertFalse(self.warnings, "\n".join(self.warnings))

    def test_geometry_is_clamped_to_small_work_area_with_negative_origin(self):
        self.coordinator.area = dict(x=-100, y=40, width=90, height=70)
        self.coordinator.notificationRequested.emit("通知", "一段需要换行的原始通知文字", "info")
        APP.processEvents()
        self.assertTrue(self.toast.isVisible())
        self.assert_status_icon("info", "info", "#526785")
        self.assert_in_area()
        self.coordinator.area = dict(x=0, y=0, width=0, height=0)
        self.coordinator.notificationRequested.emit("通知", "没有有效屏幕区域时不越界显示", "info")
        self.assertFalse(self.toast.isVisible())
        self.assertFalse(self.warnings, "\n".join(self.warnings))


if __name__ == "__main__":
    unittest.main()
