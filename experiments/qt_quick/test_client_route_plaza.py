"""Offscreen Qt events + real shared services against the existing local HTTP fixture."""
import json
from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
from PySide6.QtCore import QObject, QUrl, QPoint, QPointF, Qt, Slot, QCoreApplication, QEvent
from PySide6.QtGui import QAccessible, QGuiApplication, QKeyEvent
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from client_paths import WORK_ROOT
from client_custom_gallery import CustomGalleryService
from client_route_plaza import RoutePlazaService
from custom_maps import CustomMapLibrary, GalleryStore
from test_custom_maps import write_package
from test_route_plaza import FakeServer
import route_plaza

APP = QGuiApplication.instance() or QGuiApplication([])
HERE = Path(__file__).resolve().parent


def until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        APP.processEvents()
        if predicate():
            return
        time.sleep(.002)
    raise AssertionError("Qt plaza operation did not complete")


def item(root, name):
    pending, visited = [root], set()
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        if current.objectName() == name:
            return current
        pending.extend(current.children())
        if isinstance(current, QQuickItem):
            pending.extend(current.childItems())
    return None


class Router(QObject):
    def __init__(self):
        super().__init__()
        self.pages = []
    @Slot(str)
    def navigate(self, page):
        self.pages.append(page)


class PlazaTests(unittest.TestCase):
    def setUp(self):
        WORK_ROOT.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="qt-plaza-test-", dir=WORK_ROOT)
        self.root = Path(self.temp.name)
        (self.root / ".qt-lab-owner").write_text("qt-quick-isolated-v1")
        self.server = FakeServer()
        self.env = patch.dict(os.environ, XINGTU_ACCOUNT_API_URL=self.server.url)
        self.env.start()
        write_package(self.root / "maps")
        manifest = self.root / "maps/manifest.json"
        data = json.loads(manifest.read_text())
        data["maps"][0]["templateId"] = "nightmare-layout-01"
        manifest.write_text(json.dumps(data))
        self.galleries = CustomGalleryService(self.root, library=CustomMapLibrary(self.root / "maps"),
                                               default_path=self.root / "none.json")
        until(lambda: not self.galleries.busy and bool(self.galleries.snapshot["galleries"]))
        self.service = RoutePlazaService(self.root, self.galleries)
        from client_settings import SettingsService
        self.settings = SettingsService(self.root)
        self.threads = []
        self.service.changed.connect(lambda: self.threads.append(threading.get_ident()))
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.settings.shutdown()
        self.service.shutdown()
        self.galleries.shutdown()
        APP.processEvents()
        self.env.stop()
        self.server.close()
        self.temp.cleanup()

    def load(self):
        self.service.setActive(True)
        until(lambda: not self.service.snapshot["busy"])
        self.assertEqual(self.service.snapshot["count"], 1)

    def sign_in(self):
        self.service.openAccount()
        self.service.submitAccount("alice", "", "", "", "secret1", "")
        until(lambda: not self.service.snapshot["busy"])
        self.assertEqual(self.service.snapshot["username"], "alice")

    def download(self):
        self.service.downloadRoute("ver-2")
        until(lambda: not self.service.snapshot["busy"] and not self.galleries.busy)
        self.assertFalse(self.service.snapshot["error"], self.service.snapshot)
        return next(g for g in self.galleries.snapshot["galleries"] if g.get("source"))

    def test_lazy_public_search_stable_model_and_error_retry(self):
        self.assertEqual(self.server.calls, [])
        self.load()
        mutations = self.service.model.mutations
        self.service.search("速通")
        until(lambda: not self.service.snapshot["busy"])
        self.assertIn("query=", self.server.calls[-1][1])
        self.assertEqual(self.service.model.mutations, mutations)
        self.assertEqual(set(self.threads), {threading.get_ident()})
        with patch.object(self.service, "listing", side_effect=route_plaza.AccountError("网络连接失败")):
            self.service.search("offline")
            until(lambda: not self.service.snapshot["busy"])
        self.assertTrue(self.service.snapshot["error"])
        self.assertEqual(self.service.model.rowCount(), 1)
        self.service.search("")
        until(lambda: not self.service.snapshot["busy"])
        self.assertFalse(self.service.snapshot["error"])

    def test_login_resumes_download_and_gallery_consumers_preserve_source(self):
        self.load()
        notifications = []
        self.galleries.galleriesChanged.connect(lambda: notifications.append(True))
        self.service.downloadRoute("ver-2")
        self.assertTrue(self.service.snapshot["accountOpen"])
        self.service.submitAccount("alice", "", "", "", "secret1", "")
        until(lambda: not self.service.snapshot["busy"] and not self.galleries.busy)
        self.assertFalse(self.service.snapshot["accountOpen"])
        row = self.service.model.rows[0]
        self.assertTrue(row["installed"])
        gid = next(g["id"] for g in self.galleries.snapshot["galleries"] if g.get("source"))
        self.assertTrue(self.galleries.display_snapshot(gid, "m")["points"])
        self.assertEqual(len(notifications), 1)
        self.galleries.selectGallery(gid)
        until(lambda: not self.galleries.busy)
        self.assertTrue(self.galleries.snapshot["readonly"])
        self.assertTrue(self.galleries.snapshot["canDelete"])
        self.galleries.setUseHandler(lambda target: dict(success=True, applied=True))
        self.galleries.useGallery()
        self.assertEqual(self.galleries.snapshot["inUse"], gid)
        self.service.downloadRoute("ver-2")
        until(lambda: not self.service.snapshot["busy"] and not self.galleries.busy)
        self.assertEqual(self.galleries.snapshot["inUse"], gid)
        self.assertEqual(len(self.galleries.snapshot["galleries"]), 2)
        self.galleries.copyGallery("我的副本")
        until(lambda: not self.galleries.busy)
        self.assertFalse(self.galleries.snapshot["readonly"])
        self.assertIsNone(self.galleries.snapshot["source"])
        reloaded = GalleryStore(self.galleries.path, self.root / "none.json")
        self.assertTrue(reloaded.readonly(gid))
        self.assertNotIn("source", reloaded.export(gid))
        # Mutating another gallery must keep the read-only downloaded row in the write receipt.
        self.assertEqual(len(reloaded.galleries()), 3)
        self.galleries.setActiveGallery("")
        self.galleries.selectGallery(gid)
        until(lambda: not self.galleries.busy)
        self.galleries.requestDeleteGallery()
        self.galleries.confirmOperation(True)
        until(lambda: not self.galleries.busy)
        self.assertFalse(self.service.model.rows[0]["installed"])

    def test_bad_password_register_code_and_recover_logout(self):
        self.service.openAccount()
        self.service.submitAccount("alice", "", "", "", "wrong-1", "")
        until(lambda: not self.service.snapshot["busy"])
        self.assertIn("密码错误", self.service.snapshot["accountMessage"])
        self.service.setAccountMode("注册")
        self.service.submitAccount("", "bob_1", "bob@example.com", "", "secret1", "different")
        self.assertIn("不一致", self.service.snapshot["accountMessage"])
        self.service.submitAccount("", "bob_1", "bob@example.com", "", "secret1", "secret1")
        until(lambda: not self.service.snapshot["busy"])
        self.assertTrue(self.service.snapshot["hasRecoveryCode"])
        self.assertEqual(self.service.snapshot["accountMessage"],
                         "注册成功。请保存恢复码；确认保存后，本机不再显示。")
        self.assertNotIn(self.service.recoveryCode, json.dumps(self.service.snapshot))
        self.service.closeAccount()
        self.service.openAccount()
        self.assertEqual(self.service.snapshot["accountMessage"],
                         "请保存账号恢复码；确认保存后，本机不再显示。退出软件后，本机不再保留。")
        self.service.finishSignIn()
        self.assertEqual(self.service.recoveryCode, "")
        self.service.logout()
        until(lambda: not self.service.snapshot["busy"])
        self.assertEqual(self.service.snapshot["username"], "")
        self.service.openAccount()
        self.service.setAccountMode("找回密码")
        self.service.submitAccount("bob_1", "", "", "ABCD-EFGH-JKMP-QRST", "secret2", "secret2")
        until(lambda: not self.service.snapshot["busy"])
        self.assertEqual(self.service.snapshot["accountMode"], "登录")
        self.assertIn("重置", self.service.snapshot["accountMessage"])

    def test_closed_login_never_reopens_or_resumes_download(self):
        self.load()
        gate, entered = threading.Event(), threading.Event()
        original = self.service.account._api
        def blocked(*args, **kwargs):
            entered.set()
            gate.wait(3)
            return original(*args, **kwargs)
        with patch.object(self.service.account, "_api", side_effect=blocked):
            self.service.downloadRoute("ver-2")
            self.service.submitAccount("alice", "", "", "", "secret1", "")
            self.assertTrue(entered.wait(2))
            self.service.closeAccount()
            self.service.setActive(False)
            gate.set()
            until(lambda: not self.service.snapshot["busy"])
        self.assertFalse(self.service.snapshot["accountOpen"])
        self.assertFalse(self.galleries.path.exists())
        self.assertFalse(any("/download" in call[1] for call in self.server.calls))

    def blocked_download(self, during):
        self.load()
        self.sign_in()
        gate, entered = threading.Event(), threading.Event()
        original = route_plaza.fetch_archive
        def blocked(*args):
            entered.set()
            gate.wait(3)
            return original(*args)
        with patch.object(route_plaza, "fetch_archive", side_effect=blocked):
            self.service.downloadRoute("ver-2")
            self.service.downloadRoute("ver-2")  # No second accepted writer.
            self.assertTrue(entered.wait(2))
            during()
            gate.set()
            until(lambda: not self.service.snapshot["busy"] and not self.galleries.busy)
        self.assertFalse(self.galleries.path.exists())
        self.assertFalse(self.service.model.rows[0]["installed"])
        self.assertEqual(sum("/download" in call[1] for call in self.server.calls), 1)

    @contextmanager
    def slow_download(self):
        gate, entered = threading.Event(), threading.Event()
        original = route_plaza.fetch_archive
        def blocked(*args):
            entered.set()
            if not gate.wait(4):
                raise AssertionError('download gate not released')
            return original(*args)
        with patch.object(route_plaza, 'fetch_archive', side_effect=blocked):
            self.service.downloadRoute('ver-2')
            self.assertTrue(entered.wait(2))
            try:
                yield gate
            finally:
                gate.set()
                until(lambda: not self.service.snapshot['busy'] and not self.galleries.busy)

    def test_slow_download_allows_selection_edit_use_and_preserves_undo_redo(self):
        self.load()
        self.sign_in()
        self.galleries.createGallery('编辑中')
        until(lambda: not self.galleries.busy)
        first = self.galleries.snapshot['selectedGallery']
        self.galleries.createGallery('另一套')
        until(lambda: not self.galleries.busy)
        second = self.galleries.snapshot['selectedGallery']
        used = []
        self.galleries.setUseHandler(lambda gid: used.append(gid) or dict(success=True, applied=True))
        ui_thread = threading.get_ident()
        prepare_threads, write_threads = [], []
        original_prepare, original_write = route_plaza.prepare_install, self.galleries._write
        def prepare(*args, **kwargs):
            prepare_threads.append(threading.get_ident())
            return original_prepare(*args, **kwargs)
        def write(operation):
            write_threads.append(threading.get_ident())
            return original_write(operation)
        with patch.object(route_plaza, 'prepare_install', side_effect=prepare), \
                patch.object(self.galleries, '_write', side_effect=write):
            with self.slow_download():
                self.assertFalse(self.galleries.busy)
                self.assertEqual(self.galleries.pool.activeThreadCount(), 0)
                self.galleries.useGallery()
                self.galleries.selectGallery(first)
                until(lambda: not self.galleries.busy)
                self.galleries.useGallery()
                self.assertEqual(used, [second, first])
                self.galleries.selectMap('m')
                until(lambda: not self.galleries.busy)
                self.assertTrue(self.galleries.snapshot['editing'])
                self.galleries.addPoint(45., 45.)
                until(lambda: not self.galleries.busy)
                self.galleries.addPoint(75., 15.)
                until(lambda: not self.galleries.busy)
                self.assertEqual(len(self.galleries.snapshot['points']), 2)
                self.galleries.undo()
                until(lambda: not self.galleries.busy)
                before = self.galleries.snapshot
                self.assertTrue(before['canUndo'])
                self.assertTrue(before['canRedo'])
        after = self.galleries.snapshot
        for field in ('selectedGallery', 'selectedMap', 'selectedFloor', 'editing', 'inUse',
                      'points', 'canUndo', 'canRedo'):
            self.assertEqual(after[field], before[field], field)
        self.assertTrue(self.service.model.rows[0]['installed'])
        self.galleries.redo()
        until(lambda: not self.galleries.busy)
        self.assertEqual(len(self.galleries.snapshot['points']), 2)
        self.assertNotEqual(prepare_threads[0], ui_thread)
        self.assertNotIn(prepare_threads[0], write_threads)
        self.assertNotIn(ui_thread, write_threads)

    def prepared_while_writing(self, during):
        self.load()
        self.sign_in()
        writer_entered, writer_release = threading.Event(), threading.Event()
        original = self.galleries._write
        def blocked(operation):
            writer_entered.set()
            if not writer_release.wait(4):
                raise AssertionError('writer gate not released')
            return original(operation)
        with patch.object(self.galleries, '_write', side_effect=blocked):
            with self.slow_download() as download_release:
                try:
                    self.galleries.createGallery('下载时创建')
                    self.assertTrue(writer_entered.wait(1))
                    download_release.set()
                    until(lambda: self.galleries.pendingWrites)
                    self.assertTrue(self.service.snapshot['busy'])
                    during()
                finally:
                    writer_release.set()

    def test_prepared_download_waits_for_short_writer_then_commits(self):
        self.prepared_while_writing(lambda: None)
        self.assertTrue(self.service.model.rows[0]['installed'])
        self.assertEqual(self.galleries.snapshot['galleryName'], '下载时创建')
        self.assertEqual(len(self.galleries.snapshot['galleries']), 3)
        self.assertFalse(self.galleries.pendingWrites)

    def test_refresh_while_prepared_save_is_queued_revokes_before_write(self):
        self.prepared_while_writing(self.galleries.refresh)
        self.assertFalse(self.service.model.rows[0]['installed'])
        self.assertFalse(any(row.get('source') for row in self.galleries.snapshot['galleries']))
        self.assertFalse(self.galleries._refresh_pending)
        self.assertFalse(self.galleries.pendingWrites)

    def test_cancel_while_prepared_save_is_queued_revokes_before_write(self):
        self.prepared_while_writing(self.service.cancelDownload)
        self.assertFalse(self.service.model.rows[0]['installed'])
        self.assertFalse(any(row.get('source') for row in self.galleries.snapshot['galleries']))

    def test_close_while_prepared_save_is_queued_does_not_import(self):
        self.prepared_while_writing(self.service.cancel)
        self.assertFalse(self.service.model.rows[0]['installed'])
        self.assertFalse(any(row.get('source') for row in self.galleries.snapshot['galleries']))
        self.assertFalse(self.galleries.pendingWrites)

    def test_cancelled_refresh_error_does_not_reopen_login_or_resume_download(self):
        self.load()
        self.sign_in()
        self.service.account._session.access_expires = 0
        self.server.fail_refresh = True
        entered, release = threading.Event(), threading.Event()
        original = self.service.account._api
        def blocked(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise AssertionError('refresh gate not released')
            return original(*args, **kwargs)
        with patch.object(self.service.account, '_api', side_effect=blocked):
            self.service.downloadRoute('ver-2')
            try:
                self.assertTrue(entered.wait(1))
                self.service.cancelDownload()
            finally:
                release.set()
            until(lambda: not self.service.snapshot['busy'])
        self.assertFalse(self.service.snapshot['accountOpen'])
        self.assertIsNone(self.service._pending_download)
        self.assertIn('已取消', self.service.snapshot['message'])

    def test_unexpected_worker_failure_logs_only_type(self):
        with self.assertLogs('client_route_plaza', level='WARNING') as logs:
            with patch.object(self.service, 'listing', side_effect=RuntimeError('private-request-value')):
                self.service.search('')
                until(lambda: not self.service.snapshot['busy'])
        self.assertIn('RuntimeError', logs.output[0])
        self.assertNotIn('private-request-value', ''.join(logs.output))
        self.assertNotIn('private-request-value', self.service.snapshot['message'])

    def test_cancel_then_retry_does_not_import_late_result(self):
        self.blocked_download(self.service.cancelDownload)
        self.assertTrue(self.service.snapshot["error"])
        self.download()

    def test_resource_refresh_revokes_download_and_is_drained(self):
        self.blocked_download(self.galleries.refresh)
        self.assertFalse(self.galleries._refresh_pending)

    def test_app_close_drains_without_late_ui_or_import(self):
        self.blocked_download(self.service.cancel)
        self.assertFalse(self.service.snapshot["accountOpen"])
        self.service.openAccount()
        self.assertFalse(self.service.snapshot["accountOpen"])

    def test_tampered_download_and_expired_login_show_errors(self):
        self.load()
        self.sign_in()
        self.server.served = b"tampered"
        self.service.downloadRoute("ver-2")
        until(lambda: not self.service.snapshot["busy"])
        self.assertIn("校验失败", self.service.snapshot["message"])
        self.assertFalse(self.galleries.path.exists())
        self.service.account._session.access_expires = 0
        self.server.fail_refresh = True
        self.service.downloadRoute("ver-2")
        until(lambda: not self.service.snapshot["busy"])
        self.assertEqual(self.service.snapshot["username"], "")
        self.assertIn("重新登录", self.service.snapshot["message"])
        self.assertTrue(self.service.snapshot['accountOpen'])
        self.assertIsNotNone(self.service._pending_download)
        self.server.fail_refresh = False
        self.server.served = None
        self.service.submitAccount('alice', '', '', '', 'secret1', '')
        until(lambda: not self.service.snapshot['busy'])
        self.assertTrue(self.service.model.rows[0]['installed'])

    def test_blocked_download_keeps_server_message_without_relogin_dialog(self):
        self.load()
        self.sign_in()
        original = self.service.account._api
        def blocked(method, path, body=None, **kwargs):
            if path.endswith('/download'):
                raise route_plaza.AccountError('账号被封禁，请联系管理员', 403, 'ACCOUNT_BLOCKED')
            return original(method, path, body, **kwargs)
        with patch.object(self.service.account, '_api', side_effect=blocked):
            self.service.downloadRoute('ver-2')
            until(lambda: not self.service.snapshot['busy'])
        self.assertEqual(self.service.snapshot['username'], '')
        self.assertIn('账号被封禁', self.service.snapshot['message'])
        self.assertNotIn('登录已失效', self.service.snapshot['message'])
        self.assertFalse(self.service.snapshot['accountOpen'])
        self.assertIsNone(self.service._pending_download)

    def test_store_save_failure_rolls_back_source_and_revision(self):
        store = GalleryStore(self.root / "store.json", self.root / "none.json")
        points = [dict(floor="1", x=30, y=30, kind="via")]
        source = dict(workId="work", versionId="v1", author="fixture")
        gid = store.install_plaza("first", dict(m=dict(points=points)), source)
        before = store.path.read_bytes()
        with patch.object(store, "_save", return_value=False):
            with self.assertRaises(OSError):
                store.install_plaza("second", dict(m=dict(points=points)), dict(source, versionId="v2"))
        self.assertEqual(store.revision, 1)
        self.assertEqual(store.plaza_versions(), {"work": "v1"})
        self.assertEqual(store.name(gid), "first")
        self.assertEqual(store.path.read_bytes(), before)

    def test_hidden_download_updates_global_status_then_visible_badge(self):
        self.load()
        self.sign_in()
        self.service.setActive(False)
        mutations = self.service.model.mutations
        self.download()
        self.assertEqual(self.service.model.mutations, mutations)
        self.assertIn("已下载", self.service.snapshot["message"])
        self.service.setActive(True)
        self.assertTrue(self.service.model.rows[0]["installed"])

    def test_missing_editor_resources_and_busy_writer_never_import(self):
        self.load()
        self.sign_in()
        self.galleries._pending = dict(kind="delete")
        self.service.downloadRoute("ver-2")
        self.assertIn("正忙", self.service.snapshot["message"])
        self.galleries._pending = None
        self.galleries.library = CustomMapLibrary(self.root / "missing")
        self.service.downloadRoute("ver-2")
        until(lambda: not self.service.snapshot["busy"])
        self.assertIn("未安装", self.service.snapshot["message"])
        self.assertFalse(self.galleries.path.exists())

    def test_new_version_replaces_gallery_id_and_reports_update_badge(self):
        from dataclasses import replace
        self.load()
        self.sign_in()
        installed = self.download()
        current = self.service._routes["ver-2"]
        for revision in (1, 2):
            rollback = replace(current, version_id='other-version', revision=revision)
            self.service._routes = {rollback.version_id: rollback}
            self.service._refresh_rows()
            self.assertFalse(self.service.model.rows[0]['updateAvailable'])
        newer = replace(current, version_id="ver-3", revision=3)
        self.service._routes = {newer.version_id: newer}
        self.service._refresh_rows()
        self.assertTrue(self.service.model.rows[0]["updateAvailable"])
        manifest, grant = route_plaza.download(self.service.account, current)
        manifest["revision"] = 3
        with patch.object(route_plaza, "download", return_value=(manifest, grant)):
            self.service.downloadRoute("ver-3")
            until(lambda: not self.service.snapshot["busy"])
        self.assertTrue(self.service.model.rows[0]["installed"])
        self.assertFalse(self.service.model.rows[0]["updateAvailable"])
        again = next(g for g in self.galleries.snapshot["galleries"] if g.get("source"))
        self.assertEqual(again["id"], installed["id"])
        self.assertEqual(again["source"]["versionId"], "ver-3")

    def test_qml_mouse_keyboard_login_search_and_navigation(self):
        engine = QQmlApplicationEngine()
        router = Router()
        engine.rootContext().setContextProperty("routePlazaService", self.service)
        engine.rootContext().setContextProperty("clientCoordinator", router)
        engine.rootContext().setContextProperty("settingsService", self.settings)
        warnings = []
        engine.warnings.connect(lambda rows: warnings.extend(str(row) for row in rows))
        engine.loadData(b'''import QtQuick
import QtQuick.Controls.Basic
ApplicationWindow { visible: true; width: 1000; height: 720
ClientRoutePlaza { anchors.fill: parent; anchors.margins: 24 }
ClientAccount {}
}''', QUrl.fromLocalFile(str(HERE / "plaza-test-harness.qml")))
        self.assertTrue(engine.rootObjects(), warnings)
        window = engine.rootObjects()[0]
        def click(name):
            target = item(window, name)
            self.assertIsNotNone(target, name)
            point = target.mapToScene(QPointF(target.width()/2, target.height()/2)).toPoint()
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
            APP.processEvents()
        def type_text(text):
            for char in text:
                QCoreApplication.sendEvent(window, QKeyEvent(QEvent.KeyPress, ord(char.upper()), Qt.NoModifier, char))
                QCoreApplication.sendEvent(window, QKeyEvent(QEvent.KeyRelease, ord(char.upper()), Qt.NoModifier, char))
        try:
            until(lambda: self.service.snapshot["loaded"] and not self.service.snapshot["busy"])
            click("plazaLogin")
            until(lambda: item(window, "accountDialog").property("opened"))
            self.service.setAccountMode("注册")
            APP.processEvents()
            self.assertEqual(item(window, "accountHelpText").property("text"),
                "用户名 3–32 位英文小写、数字或下划线；密码 6–128 位。注册后请保存恢复码；确认保存后，本机不再显示。")
            accessible = QAccessible.queryAccessibleInterface(item(window, "accountRecoveryCode"))
            self.assertIsNotNone(accessible)
            self.assertEqual(accessible.text(QAccessible.Name), "账号恢复码")
            self.service.setAccountMode("找回密码")
            APP.processEvents()
            self.assertEqual(item(window, "accountHelpText").property("text"), "用账号恢复码重设密码。")
            self.service.setAccountMode("登录")
            APP.processEvents()
            click("accountIdentifier")
            type_text("alice")
            click("accountPassword")
            type_text("secret1")
            click("accountSubmit")
            until(lambda: not self.service.snapshot["busy"])
            self.assertEqual(self.service.snapshot["username"], "alice", self.service.snapshot)
            self.assertEqual(item(window, "accountPassword").property("text"), "")
            until(lambda: not item(window, "accountDialog").property("visible"))
            QTest.qWait(200)
            click("plazaSearch")
            type_text("sample")
            QTest.keyClick(window, Qt.Key_Return)
            until(lambda: not self.service.snapshot["busy"])
            self.assertEqual(self.service.snapshot["query"], "sample")
            click("plazaDownload_ver-2")
            until(lambda: not self.service.snapshot["busy"] and not self.galleries.busy)
            self.assertTrue(self.service.model.rows[0]["installed"], self.service.snapshot)
            click("plazaDownload_ver-2")
            self.assertEqual(router.pages[-1], "custom")
            self.assertEqual(warnings, [])
        finally:
            window.close()
            engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            APP.processEvents()


class PlazaCompositionTests(unittest.TestCase):
    def test_actual_client_navigation_screenshots_and_normal_close(self):
        import subprocess
        import sys
        run = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--composition"],
                             env=dict(os.environ), capture_output=True, text=True, timeout=40)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        result = json.loads(run.stdout.strip().splitlines()[-1])
        self.assertTrue(result["closed"])
        self.assertTrue(result["inputGuard"])
        self.assertEqual(result["qmlErrors"], [])
        self.assertFalse(result["startupRequests"])


def composition():
    """Actual eight-window QtClient; native input/capture/resource engines are fixtures."""
    from client import QtClient
    from test_client_recapture_focus import RecaptureFocusTests
    from test_client_input import Native as InputNative
    import test_client_live_runtime as fixtures
    from client_theme import apply_light_theme
    from PySide6.QtGui import QFont

    fixture = RecaptureFocusTests("test_main_qml_click_returns_game_then_captures_via_real_guard")
    fixture.setUp()
    class Factories(dict):
        def set_custom_display(self, *args, **kwargs):
            pass
    providers = Factories(nightmare=lambda: fixtures.fixture_engine(), difficult=lambda: fixtures.fixture_engine("difficult"))
    client = None
    gate, entered = threading.Event(), threading.Event()
    server = FakeServer()
    try:
        WORK_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="qt-plaza-composition-", dir=WORK_ROOT) as directory, \
             patch.dict(os.environ, XINGTU_ACCOUNT_API_URL=server.url):
            root = Path(directory)
            (root / ".qt-lab-owner").write_text("qt-quick-isolated-v1")
            library = root / "_internal/assets/custom-maps"
            write_package(library)
            manifest = library / "manifest.json"
            data = json.loads(manifest.read_text())
            data["maps"][0]["templateId"] = "nightmare-layout-01"
            manifest.write_text(json.dumps(data))
            apply_light_theme(APP)
            APP.setFont(QFont("Noto Sans CJK SC", 10))
            with patch("client_input.NativeWindowAPI", return_value=InputNative()), \
                 patch("client_capture.ClientCaptureProvider", return_value=fixture.capture), \
                 patch("client_providers.build_engine_factories", return_value=providers):
                client = QtClient(APP, root, probe_output=root / "unused-probe", info_fetch=lambda *a, **k: {})
            until(lambda: not client.custom_galleries.busy and not client.session.busy and not client.resources.busy, 10)
            startup_requests = list(server.calls)
            def click(name):
                target = item(client.main, name)
                assert target is not None, name
                assert target.isEnabled(), name
                point = target.mapToScene(QPointF(target.width()/2, target.height()/2)).toPoint()
                QTest.mouseClick(client.main, Qt.LeftButton, Qt.NoModifier, point)
                APP.processEvents()
            def capture(name):
                evidence = os.environ.get("XINGTU_QT_PLAZA_EVIDENCE_DIR")
                if evidence:
                    folder = Path(evidence)
                    folder.mkdir(parents=True, exist_ok=True)
                    QTest.mouseMove(client.main, QPoint(client.main.width() - 20, client.main.height() - 20))
                    QTest.qWait(250)
                    pixels = client.main.grabWindow()
                    assert not pixels.isNull(), "offscreen capture missing"
                    assert pixels.save(str(folder / name))
            click("nav_plaza")
            until(lambda: client.route_plaza.snapshot["loaded"] and not client.route_plaza.snapshot["busy"])
            assert client.main.property("page") == "plaza"
            page = item(client.main, "routePlazaPage")
            click("nav_plaza")
            assert item(client.main, "routePlazaPage") is page
            # Real root wiring: the saved intent reaches display workers before
            # runtime presentation settings.
            assert not client.settings.persistedValues["routePointNumbers"]
            click("routePointNumbersSwitch")
            until(lambda: not client.settings.busy and client.settings.persistedValues["routePointNumbers"])
            assert client.custom_display._point_numbers
            assert client.runtime._settings["routePointNumbers"]
            capture("qt-route-plaza.png")
            click("routePointNumbersSwitch")
            until(lambda: not client.settings.busy and not client.settings.persistedValues["routePointNumbers"])
            assert not client.custom_display._point_numbers
            client.main.resize(650, 470)
            QTest.qWait(250)
            capture("qt-route-plaza-small.png")
            client.main.resize(1000, 720)
            QTest.qWait(250)
            click("plazaLogin")
            until(lambda: item(client.main, "accountDialog").property("opened"))
            capture("qt-account-login.png")
            click("accountIdentifier")
            guarded = client.coordinator.textEntryActive
            assert guarded, "account text field did not suspend game shortcuts"
            item(client.main, "accountIdentifier").setProperty("text", "alice")
            item(client.main, "accountPassword").setProperty("text", "secret1")
            click("accountSubmit")
            until(lambda: not client.route_plaza.snapshot["busy"])
            assert client.route_plaza.snapshot["username"] == "alice", client.route_plaza.snapshot
            until(lambda: not item(client.main, "accountDialog").property("visible"))
            QTest.qWait(250)
            original = route_plaza.fetch_archive
            def blocked(*args):
                entered.set()
                gate.wait(5)
                return original(*args)
            with patch.object(route_plaza, "fetch_archive", side_effect=blocked):
                click("plazaDownload_ver-2")
                assert entered.wait(2), "download did not start"
                client.coordinator.requestClose()
                APP.processEvents()
                assert not client.coordinator.closeReady, "writer was not drained"
                gate.set()
                until(lambda: client.coordinator.closeReady, 10)
            assert not client.custom_galleries.path.exists(), "late close-time download was imported"
            client.shutdown()
            client.engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            warnings = list(client.warnings)
            assert "tkinter" not in __import__("sys").modules, "Qt imported Tk"
            print(json.dumps(dict(closed=client._closed, qmlErrors=warnings,
                                  inputGuard=guarded, startupRequests=startup_requests)))
    finally:
        gate.set()
        if client is not None:
            client.shutdown()
        fixture.doCleanups()
        server.close()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


if __name__ == "__main__":
    import sys
    if "--composition" in sys.argv:
        composition()
    else:
        unittest.main()
