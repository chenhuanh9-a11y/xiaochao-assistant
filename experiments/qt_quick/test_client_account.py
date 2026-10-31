"""Offline Qt account consumer tests. No real credentials, sockets or game calls."""
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QObject, QUrl, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickView
from PySide6.QtTest import QTest
from client_paths import WORK_ROOT
from client_account import ClientAccountService
from account_service import AccountError, SessionVault

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
from test_account_service_next import tokens

APP = QGuiApplication.instance() or QGuiApplication([])


def until(condition):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        APP.processEvents()
        if condition():
            return
        time.sleep(.002)
    raise AssertionError("account worker did not reach expected state")


class ClientAccountTests(unittest.TestCase):
    def setUp(self):
        WORK_ROOT.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="account-qt-", dir=WORK_ROOT)
        self.root = Path(self.temp.name)
        (self.root / ".qt-lab-owner").write_text("qt-quick-isolated-v1")
        self.vault = SessionVault(self.root / "fixture.bin", protect=lambda b: b[::-1], unprotect=lambda b: b[::-1])
        self.entered, self.release = threading.Event(), threading.Event()
        self.release.set()
        self.calls = []
        self.failure = None
        def api(method, path, body=None, **kwargs):
            self.calls.append((path, threading.get_ident()))
            self.entered.set()
            if not self.release.wait(3):
                raise AssertionError("test gate timed out")
            if self.failure:
                raise self.failure
            return tokens() if path != "/v1/auth/logout" else None
        self.service = ClientAccountService(self.root, vault=self.vault, api=api)
        self.notifications = []
        self.service.changed.connect(lambda: self.notifications.append(threading.get_ident()))

    def tearDown(self):
        self.release.set()
        self.service.shutdown()
        until(self.service.is_drained)
        self.temp.cleanup()

    def login(self):
        self.service.login("tester", "synthetic-password")
        until(lambda: not self.service.busy)
        self.assertTrue(self.service.loggedIn)

    def test_network_runs_off_ui_and_notifications_on_ui(self):
        self.assertEqual(self.calls, [])
        self.login()
        ui = threading.get_ident()
        self.assertTrue(all(thread != ui for _, thread in self.calls))
        self.assertTrue(all(thread == ui for thread in self.notifications))
        self.assertEqual(self.service.username, "tester")
        self.assertTrue(self.service.sessionSaved)

    def test_duplicate_submit_is_ignored_and_cancel_drains_without_login(self):
        self.release.clear()
        self.service.login("tester", "synthetic-password")
        until(self.entered.is_set)
        self.service.login("another", "synthetic-password")
        self.service.cancel()
        self.assertTrue(self.service.busy)
        self.assertFalse(self.service.canCancel)
        self.release.set()
        until(lambda: not self.service.busy)
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(self.service.loggedIn)
        self.assertIn("取消", self.service.message)
        self.assertFalse(self.vault.path.exists())

    def test_logout_while_login_pending_drops_late_result(self):
        self.release.clear()
        self.service.login("tester", "synthetic-password")
        until(self.entered.is_set)
        self.service.logout()
        self.assertFalse(self.service.loggedIn)
        self.release.set()
        until(lambda: not self.service.busy)
        self.assertFalse(self.service.loggedIn)
        self.assertEqual(self.service.message, "已退出登录")

    def test_restore_and_external_expiry_reach_actual_qt_consumer(self):
        self.login()
        self.service.shutdown()
        until(self.service.is_drained)
        def api(method, path, body=None, **kwargs):
            if path == "/v1/auth/refresh":
                return tokens()
            raise AccountError("登录已失效，请重新登录", 403, "SESSION_INVALID")
        self.service = ClientAccountService(self.root, vault=self.vault, api=api)
        self.assertFalse(self.service.loggedIn)
        self.assertTrue(self.service.canRestore)
        self.service.restoreSession()
        until(lambda: not self.service.busy)
        self.assertTrue(self.service.loggedIn)
        # Mimic another worker (the route plaza) discovering a revoked account.
        def request():
            try:
                self.service.service.authorized("GET", "/v1/fixture")
            except AccountError:
                pass
        worker = threading.Thread(target=request)
        worker.start(); worker.join(2)
        self.assertFalse(worker.is_alive())
        until(lambda: not self.service.loggedIn)
        self.assertIn("失效", self.service.message)

    def test_logout_network_failure_keeps_local_session_removed(self):
        self.login()
        self.failure = AccountError("网络连接失败，请检查网络后重试")
        self.service.logout()
        self.assertFalse(self.service.loggedIn)
        self.assertFalse(self.vault.path.exists())
        until(lambda: not self.service.busy)
        self.assertIn("服务器退出请求未完成", self.service.message)

    def test_logout_during_saved_session_restore_rejects_late_refresh(self):
        self.login()
        self.service.shutdown()
        until(self.service.is_drained)
        self.entered.clear(); self.release.clear()
        api = self.service.service._api
        self.service = ClientAccountService(self.root, vault=self.vault, api=api)
        self.service.restoreSession()
        until(self.entered.is_set)
        self.service.logout()
        self.assertFalse(self.service.canRestore)
        self.release.set()
        until(lambda: not self.service.busy)
        self.assertFalse(self.service.loggedIn)
        self.assertFalse(self.vault.path.exists())

    def test_failure_can_retry_and_shutdown_is_nonblocking_then_drained(self):
        self.failure = RuntimeError("synthetic-password must not surface")
        self.service.login("tester", "synthetic-password")
        until(lambda: not self.service.busy)
        self.assertNotIn("synthetic-password", self.service.message)
        self.failure = None
        self.entered.clear(); self.release.clear()
        self.service.login("tester", "synthetic-password")
        until(self.entered.is_set)
        start = time.monotonic()
        self.service.shutdown()
        self.assertLess(time.monotonic() - start, .25)
        self.assertFalse(self.service.is_drained())
        before = len(self.notifications)
        self.release.set()
        until(self.service.is_drained)
        APP.processEvents()
        self.assertEqual(len(self.notifications), before)
        self.assertFalse(self.vault.path.exists())

    def test_qml_keyboard_login_clears_password_and_logout_updates_identity(self):
        view = QQuickView()
        view.rootContext().setContextProperty("accountService", self.service)
        view.setResizeMode(QQuickView.SizeRootObjectToView)
        view.setSource(QUrl.fromLocalFile(str(Path(__file__).with_name("ClientAccount.qml"))))
        self.assertEqual(view.status(), QQuickView.Ready, [e.toString() for e in view.errors()])
        view.resize(900, 650); view.show()
        try:
            root = view.rootObject()
            identifier = root.findChild(QObject, "accountIdentifier")
            password = root.findChild(QObject, "accountPassword")
            identity = root.findChild(QObject, "accountIdentity")
            identifier.setProperty("text", "tester")
            password.setProperty("text", "synthetic-password")
            root.setVisible(False)
            self.assertEqual(password.property("text"), "")
            root.setVisible(True)
            password.setProperty("text", "synthetic-password")
            password.forceActiveFocus()
            QTest.keyClick(view, Qt.Key_Return)
            until(lambda: self.service.loggedIn and not self.service.busy)
            self.assertEqual(password.property("text"), "")
            self.assertIn("tester", identity.property("text"))
            logout = root.findChild(QObject, "accountLogout")
            QTest.qWait(50)  # Let Qt polish the Flow after login hides the form.
            center = logout.mapToScene(logout.boundingRect().center()).toPoint()
            QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, center)
            until(lambda: not self.service.busy and not self.service.loggedIn)
            self.assertFalse(identity.property("visible"))
        finally:
            view.close()
            view.deleteLater()
            APP.processEvents()


if __name__ == "__main__":
    unittest.main()
