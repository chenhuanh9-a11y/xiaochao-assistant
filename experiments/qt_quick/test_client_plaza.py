"""Real Qt queued workers/QML with isolated fake API callbacks; no service login."""
from copy import deepcopy
import os
from pathlib import Path
import sys
import threading
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from client_plaza import PlazaService
from route_plaza import BATCH_SCHEMA, PlazaRoute

APP = QGuiApplication.instance() or QGuiApplication([])
ROUTE = PlazaRoute("v1", "w1", 1, "测试路线", "测试描述", "测试作者", 1, 2, "2026-10-31")
BATCH = dict(schema=BATCH_SCHEMA, name="测试路线", manifest={}, source=dict(workId="w1", versionId="v1", revision=1))


def until(predicate, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        APP.processEvents()
        if predicate():
            return
        time.sleep(.002)
    raise AssertionError("Qt plaza operation did not finish")


class Account(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.service = self
        self.revision = 1
        self.loggedIn = True

    def logout(self):
        self.revision += 1
        self.loggedIn = False
        self.changed.emit()


class ClientPlazaTests(unittest.TestCase):
    def setUp(self):
        self.account = Account()
        self.calls, self.received = [], []
        self.release = threading.Event()
        self.service = PlazaService(self.account, list_function=self.list_routes,
            download_function=lambda *_: deepcopy(BATCH))
        self.service.downloadReady.connect(self.received.append)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.release.set()
        self.service.shutdown()
        APP.processEvents()

    def list_routes(self, query):
        self.calls.append((query, threading.get_ident()))
        return [ROUTE]

    def browse(self):
        self.service.search("地图")
        until(lambda: not self.service.busy)

    def test_no_startup_network_and_guest_can_browse(self):
        self.assertEqual(self.calls, [])
        self.account.logout()
        self.browse()
        self.assertNotEqual(self.calls[0][1], threading.get_ident())
        self.assertEqual(self.service.snapshot["rows"][0]["versionId"], "v1")
        logins = []
        self.service.loginRequested.connect(lambda: logins.append(True))
        self.service.downloadRoute("v1")
        self.assertEqual(logins, [True])
        self.assertEqual(self.received, [])

    def test_download_waits_for_gallery_ack_and_duplicate_click_is_ignored(self):
        self.browse()
        self.service.downloadRoute("v1")
        self.service.downloadRoute("v1")
        until(lambda: len(self.received) == 1)
        batch = self.received[0]
        self.assertTrue(self.service.busy)
        self.assertTrue(self.service.isCurrentDownload(batch["requestId"], batch["accountRevision"]))
        self.service.finishImport(batch["requestId"] + 1, True, "wrong")
        self.assertTrue(self.service.busy)
        self.service.finishImport(batch["requestId"], True, "保存完成")
        self.assertEqual(self.service.snapshot["stage"], "installed")
        self.assertFalse(self.service.busy)
        self.assertFalse(self.service.isCurrentDownload(batch["requestId"], batch["accountRevision"]))

    def test_cancel_or_logout_discards_late_download(self):
        self.browse()
        self.service._download = lambda *_: (self.release.wait(2), deepcopy(BATCH))[1]
        self.service.downloadRoute("v1")
        self.service.cancel()
        self.release.set()
        until(lambda: not self.service.busy)
        self.assertEqual(self.received, [])
        self.release.clear()
        self.service.downloadRoute("v1")
        self.account.logout()
        self.release.set()
        until(lambda: not self.service.busy)
        self.assertEqual(self.received, [])

    def test_logout_invalidates_pending_import_and_late_ack(self):
        self.browse()
        self.service.downloadRoute("v1")
        until(lambda: bool(self.received))
        batch = self.received[0]
        self.account.logout()
        self.assertFalse(self.service.isCurrentDownload(batch["requestId"], batch["accountRevision"]))
        self.service.finishImport(batch["requestId"], True, "stale success")
        self.assertNotEqual(self.service.snapshot["message"], "stale success")
        self.assertFalse(self.service.busy)
        self.assertFalse(self.service.beginImport(batch["requestId"], batch["accountRevision"]))

    def test_accepted_import_is_one_shot_and_reports_writer_failure_after_logout(self):
        self.browse()
        self.service.downloadRoute("v1")
        until(lambda: bool(self.received))
        batch = self.received[0]
        request, revision = batch["requestId"], batch["accountRevision"]
        self.assertFalse(self.service.beginImport(request + 1, revision))
        self.assertFalse(self.service.beginImport(request, revision + 1))
        self.assertTrue(self.service.beginImport(request, revision))
        self.assertFalse(self.service.beginImport(request, revision))
        self.account.logout()
        self.assertTrue(self.service.busy)
        self.service.finishImport(request + 1, True, "wrong")
        self.assertTrue(self.service.busy)
        self.service.finishImport(request, False, "隔离写入失败")
        self.assertFalse(self.service.busy)
        self.assertEqual(self.service.snapshot["stage"], "error")
        self.assertEqual(self.service.snapshot["message"], "隔离写入失败")
        self.assertFalse(self.service.beginImport(request, revision))

    def test_shutdown_discards_queued_result(self):
        self.browse()
        self.service.downloadRoute("v1")
        self.service.shutdown()
        APP.processEvents()
        self.assertEqual(self.received, [])

    def test_error_and_retry_and_gallery_failure_are_visible(self):
        def fail(_query):
            raise ValueError("隔离服务不可用")
        self.service._list = fail
        self.browse()
        self.assertEqual(self.service.snapshot["stage"], "error")
        self.assertIn("不可用", self.service.snapshot["message"])
        self.service._list = self.list_routes
        self.browse()
        self.service.downloadRoute("v1")
        until(lambda: bool(self.received))
        self.service.finishImport(self.received[0]["requestId"], False, "图库正在保存，请稍后重试")
        self.assertEqual(self.service.snapshot["stage"], "error")
        self.assertFalse(self.service.busy)

    def test_qml_surface_loads_and_displays_fake_list(self):
        self.browse()
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("plazaService", self.service)
        engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name("ClientPlaza.qml"))))
        self.assertTrue(engine.rootObjects())
        root = engine.rootObjects()[0]
        root.setProperty("width", 720)
        root.setProperty("height", 520)
        APP.processEvents()
        self.assertIsNotNone(root.findChild(QObject, "plazaQuery"))
        self.assertEqual(root.findChild(QObject, "plazaRoutes").property("count"), 1)
        engine.deleteLater()
        APP.processEvents()


if __name__ == "__main__":
    unittest.main()
