"""Local help/update flows; no network, installed data, native input or browser."""
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
import zipfile
from dataclasses import replace
from unittest.mock import patch
from types import SimpleNamespace
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QObject, QMetaObject, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine
from client_info import ClientInfoService, WORK_ROOT


def release(version="9.1", notes="修复地图显示\n保留已有设置"):
    return dict(schemaVersion=1, platform="windows", version=version, versionCode=91,
                releaseNotes=notes, publishedAt="2026-10-30", size=100,
                downloadPageUrl="https://pan.quark.cn/s/example", ucDownloadPageUrl="https://drive.uc.cn/s/example")


class InfoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        WORK_ROOT.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="qt-info-test-", dir=WORK_ROOT)
        self.root = Path(self.temp.name)
        (self.root / ".qt-lab-owner").write_text("qt-quick-isolated-v1", encoding="utf-8")
        self.calls, self.opened, self.services = [], [], []

    def tearDown(self):
        for service in self.services:
            service.shutdown()
        self.app.processEvents()
        self.temp.cleanup()

    def fetch(self, url):
        self.calls.append(url)
        return {"releases": [release("8.0"), release("9.1")]} if "history" in url else release()

    def service(self, fetch=None):
        service = ClientInfoService(self.root, fetch=fetch or self.fetch,
                                   open_url=lambda url: self.opened.append(url.toString()) or True)
        self.services.append(service)
        return service

    def wait(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.002)
        self.assertTrue(predicate(), "local worker timeout")

    def checked(self, service):
        service.checkUpdates()
        self.wait(lambda: not service.checking)

    def test_startup_is_idle_and_check_delivers_real_history(self):
        service = self.service()
        self.app.processEvents()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.opened, [])
        self.assertFalse((self.root / "qt-update-state.json").exists())
        self.checked(service)
        self.assertEqual(service.updatePhase, "available")
        self.assertEqual(service.history[0]["version"], "8.0")
        self.assertEqual(service.release["published_date"], "2026-10-30")
        self.assertIn("\n", service.release["notes"])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.opened, [])

    def test_failed_check_retries_and_optional_history_failure_is_visible(self):
        fail = [True]
        def fetch(url):
            if fail[0]:
                raise OSError("local offline fixture")
            if "history" in url:
                raise OSError("local history fixture")
            return release()
        service = self.service(fetch)
        self.checked(service)
        self.assertTrue(service.hasError)
        self.assertIn("offline", service.message)
        fail[0] = False
        self.checked(service)
        self.assertFalse(service.hasError)
        self.assertEqual(service.release["version"], "9.1")
        self.assertIn("重新检查", service.historyMessage)

    def test_read_skip_persist_and_browser_only_explicit_allowlisted_button(self):
        service = self.service()
        self.checked(service)
        service.markRead()
        self.wait(lambda: not service.checking)
        self.assertFalse(service.hasUnread)
        service.skipVersion()
        self.wait(lambda: not service.checking)
        stored = json.loads((self.root / "qt-update-state.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["seenNotesVersion"], "9.1")
        self.assertEqual(stored["skippedVersion"], "9.1")
        self.assertEqual(self.opened, [])
        service.openDownloadPage("uc")
        self.assertEqual(self.opened, ["https://drive.uc.cn/s/example"])
        service._service.latest = replace(service._service.latest, pages={"uc": "https://evil.example/"})
        service.openDownloadPage("uc")
        self.assertEqual(len(self.opened), 1)
        self.assertTrue(service.hasError)

    def test_swallowed_notice_save_failure_is_not_reported_as_success(self):
        service = self.service()
        self.checked(service)
        with patch.object(service._service, "_save", return_value=None):
            service.markRead()
            self.wait(lambda: not service.checking)
        self.assertTrue(service.hasError)
        self.assertTrue(service.hasUnread)

    def test_diagnostics_receipt_selected_path_and_no_overwrite(self):
        service = self.service()
        path = self.root / "chosen.zip"
        service.exportDiagnosticsTo(QUrl.fromLocalFile(str(path)).toString())
        self.wait(lambda: not service.checking)
        self.assertTrue(service.diagnosticsReceipt["success"])
        self.assertEqual(service.diagnosticsReceipt["path"], str(path))
        payload = path.read_bytes()
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(json.loads(archive.read("qt-client-summary.json"))["schema"], "xingtu-qt-client-diagnostics-v1")
            manifest = json.loads(archive.read("diagnostic-manifest.json"))
            self.assertTrue(manifest["textOnly"])
            self.assertEqual(manifest["failureEvidence"]["status"], "not-requested")
            self.assertFalse(any(name.startswith("evidence/") for name in archive.namelist()))
        service.exportDiagnosticsTo(QUrl.fromLocalFile(str(path)).toString())
        self.wait(lambda: not service.checking)
        self.assertFalse(service.diagnosticsReceipt["success"])
        self.assertEqual(path.read_bytes(), payload)
        service.exportDiagnosticsTo("https://example.test/report.zip")
        self.assertTrue(service.hasError)
        self.assertEqual(self.calls, [])

    def test_zip_uses_existing_safe_window_snapshot_and_saved_frame_opt_in(self):
        service = self.service()
        service.setProviders(
            input=SimpleNamespace(enabled=False, snapshot=dict(hwnd="123", readPermission="unknown")),
            coordinator=SimpleNamespace(boundWindow="123", gameWindows=[dict(hwnd="123", pid=456,
                title="private window title", width=1280, height=720, dpi=96, clientBounds=[0, 0, 1280, 720])]))
        service.setIncludeFailureEvidence(True)
        path = self.root / "with-existing-evidence.zip"
        service.exportDiagnosticsTo(QUrl.fromLocalFile(str(path)).toString())
        self.wait(lambda: not service.checking)
        self.assertTrue(service.diagnosticsReceipt["success"])
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read("diagnostic-manifest.json"))
            self.assertEqual(manifest["windowEnvironment"]["candidates"],
                             [dict(width=1280, height=720, dpi=96, clientBounds=[0, 0, 1280, 720])])
            self.assertEqual(manifest["failureEvidence"]["status"], "no-matching-saved-frame")
            self.assertEqual(len(manifest["failureEvidence"]["journals"]), 2)
            self.assertNotIn("private window title", json.dumps(manifest))
            self.assertEqual(manifest["windowEnvironment"]["readPermission"], "unknown")
        self.assertEqual(self.calls, [])

    def test_qml_local_check_history_and_plain_notes_no_warnings(self):
        service = self.service()
        engine = QQmlEngine()
        engine.rootContext().setContextProperty("infoService", service)
        warnings = []
        engine.warnings.connect(lambda rows: warnings.extend(str(row) for row in rows))
        component = QQmlComponent(engine)
        component.setData(b"""import QtQuick
import QtQuick.Controls.Basic
ApplicationWindow { width: 960; height: 680; visible: false
ClientHelp { objectName: "helpPanel"; anchors.fill: parent } }
""", QUrl.fromLocalFile(str(Path(__file__).with_name("__help_test__.qml"))))
        if component.isLoading():
            self.wait(lambda: not component.isLoading())
        self.assertFalse(component.isError(), str(component.errors()))
        window = component.create()
        self.assertIsNotNone(window, str(component.errors()))
        self.assertEqual(self.calls, [])
        check = window.findChild(QObject, "helpCheckUpdates")
        self.assertTrue(QMetaObject.invokeMethod(check, "clicked"))
        self.wait(lambda: not service.checking)
        toggle = window.findChild(QObject, "helpHistoryToggle")
        self.assertTrue(QMetaObject.invokeMethod(toggle, "clicked"))
        self.app.processEvents()
        self.assertTrue(window.findChild(QObject, "helpPanel").property("historyOpen"))
        self.assertEqual(self.opened, [])
        self.assertEqual(warnings, [])
        window.deleteLater()
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main(verbosity=2)
