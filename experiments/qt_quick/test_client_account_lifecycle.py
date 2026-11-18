"""Actual QML dialog lifecycle with gated, synthetic account transport."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPointF, Qt, QUrl, Signal
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest
import test_client_route_plaza as fixtures
from test_client_route_plaza import APP, HERE, Router, item, until
from account_service import AccountService, SessionVault
from client_paths import WORK_ROOT
from client_route_plaza import RoutePlazaService
from client_settings import SettingsService
from test_account_concurrency import tokens


class AccountLifecycleTests(unittest.TestCase):
    setUp = fixtures.PlazaTests.setUp
    cleanup = fixtures.PlazaTests.cleanup
    load = fixtures.PlazaTests.load
    sign_in = fixtures.PlazaTests.sign_in

    def harness(self):
        engine = QQmlApplicationEngine()
        router = Router()
        engine.rootContext().setContextProperty("routePlazaService", self.service)
        engine.rootContext().setContextProperty("settingsService", self.settings)
        engine.rootContext().setContextProperty("clientCoordinator", router)
        engine.loadData(b'''import QtQuick
import QtQuick.Controls.Basic
ApplicationWindow { visible: true; width: 1000; height: 720
ClientRoutePlaza { anchors.fill: parent; anchors.margins: 24 }
ClientAccount {}
}''', QUrl.fromLocalFile(str(HERE / "account-lifecycle-harness.qml")))
        self.assertTrue(engine.rootObjects())
        window = engine.rootObjects()[0]
        def cleanup():
            window.close()
            engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            APP.processEvents()
        self.addCleanup(cleanup)
        # Hold the context object for the lifetime of the engine.
        self._router = router
        until(lambda: self.service.snapshot["loaded"] and not self.service.snapshot["busy"])
        return window

    def click(self, window, name):
        QTest.qWait(50)  # Let Qt Quick polish layout after conditional controls change.
        target = item(window, name)
        self.assertIsNotNone(target, name)
        point = target.mapToScene(QPointF(target.width()/2, target.height()/2)).toPoint()
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
        APP.processEvents()

    def register(self, window):
        self.click(window, "plazaLogin")
        until(lambda: item(window, "accountDialog").property("opened"))
        self.service.setAccountMode("注册")
        APP.processEvents()
        for name, value in [("accountUsername", "bob_1"), ("accountEmail", "bob@example.com"),
                            ("accountPassword", "secret1"), ("accountPasswordConfirm", "secret1")]:
            item(window, name).setProperty("text", value)
        self.click(window, "accountSubmit")

    def closed_registration(self, dismissal):
        window = self.harness()
        gate, entered = threading.Event(), threading.Event()
        original = self.service.account._api
        def blocked(*args, **kwargs):
            if args[1] == "/v1/auth/register":
                entered.set()
                gate.wait(3)
            return original(*args, **kwargs)
        try:
            with patch.object(self.service.account, "_api", side_effect=blocked):
                self.register(window)
                self.assertTrue(entered.wait(2))
                if dismissal == "escape":
                    QTest.keyClick(window, Qt.Key_Escape)
                else:
                    self.click(window, "accountCancel")
                until(lambda: not item(window, "accountDialog").property("visible"))
                gate.set()
                until(lambda: not self.service.snapshot["busy"])
            self.assertFalse(self.service.snapshot["accountOpen"])
            self.assertEqual(self.service.recoveryCode, "")
            self.assertFalse(item(window, "accountDialog").property("visible"))
            self.assertTrue(item(window, "plazaRecovery").isVisible())
            self.click(window, "plazaRecovery")
            until(lambda: item(window, "accountDialog").property("opened"))
            self.assertTrue(self.service.snapshot["hasRecoveryCode"])
            code = self.service.recoveryCode
            self.assertTrue(code)
            self.assertEqual(item(window, "accountRecoveryCode").property("text"), code)
            self.assertNotIn(code, json.dumps(self.service.snapshot))
            # Dismissal is not acknowledgement, even after the code became visible.
            QTest.keyClick(window, Qt.Key_Escape)
            until(lambda: not item(window, "accountDialog").property("visible"))
            self.assertTrue(item(window, "plazaRecovery").isVisible())
            self.click(window, "plazaRecovery")
            until(lambda: item(window, "accountDialog").property("opened"))
            self.assertEqual(self.service.recoveryCode, code)
            self.click(window, "accountSubmit")  # "I saved it" acknowledges.
            until(lambda: not item(window, "accountDialog").property("visible"))
            self.service.openAccount()
            self.assertFalse(self.service.snapshot["hasRecoveryCode"])
        finally:
            gate.set()

    def test_escape_late_registration_can_be_claimed_on_reopen(self):
        self.closed_registration("escape")

    def test_cancel_late_registration_can_be_claimed_on_reopen(self):
        self.closed_registration("cancel")

    def test_shutdown_revokes_inflight_registration_without_reopening(self):
        window = self.harness()
        gate, entered = threading.Event(), threading.Event()
        original = self.service.account._api
        def blocked(*args, **kwargs):
            entered.set()
            gate.wait(3)
            return original(*args, **kwargs)
        try:
            with patch.object(self.service.account, "_api", side_effect=blocked):
                self.register(window)
                self.assertTrue(entered.wait(2))
                self.service.cancel()
                gate.set()
                until(lambda: not self.service._busy)
            self.assertIsNone(self.service.account.username)
            self.assertEqual(self.service.recoveryCode, "")
            self.assertFalse(item(window, "accountDialog").property("visible"))
            self.service.openAccount()
            self.assertFalse(self.service.snapshot["accountOpen"])
        finally:
            gate.set()

    def test_slow_refresh_keeps_qml_snapshot_cancel_navigation_and_exit_responsive(self):
        window = self.harness()
        self.sign_in()
        self.service.account._session.access_expires = 0
        gate, entered = threading.Event(), threading.Event()
        original = self.service.account._api
        def blocked(*args, **kwargs):
            if args[1] == "/v1/auth/refresh":
                entered.set()
                gate.wait(2)
            return original(*args, **kwargs)
        try:
            with patch.object(self.service.account, "_api", side_effect=blocked):
                started = time.monotonic()
                self.click(window, "plazaDownload_ver-2")
                self.assertTrue(entered.wait(2))
                # changed synchronously evaluates the real QML snapshot bindings.
                self.service.cancelDownload()
                self.service.setActive(False)
                self.service.cancel()
                APP.processEvents()
                elapsed = time.monotonic() - started
                gate.set()
                until(lambda: not self.service._busy and not self.galleries.busy)
            self.assertLess(elapsed, .5, "UI waited for blocked refresh HTTP")
            self.assertFalse(self.galleries.path.exists())
        finally:
            gate.set()

    def test_logout_or_account_change_revokes_visible_recovery_code(self):
        window = self.harness()
        self.register(window)
        until(lambda: not self.service._busy)
        self.assertTrue(self.service.recoveryCode)
        # Another account consumer changes session; a stale UI copy must not expose it.
        self.service.account.login("alice", "secret1")
        self.assertEqual(self.service.recoveryCode, "")
        self.assertFalse(self.service.snapshot["hasRecoveryCode"])
        self.service.closeAccount()
        self.service.openAccount()
        self.assertFalse(self.service.snapshot["hasRecoveryCode"])


class _EmptyGalleries(QObject):
    galleriesChanged = Signal()

    def plazaSources(self):
        return {}


class AccountPersistenceUiTests(unittest.TestCase):
    """Real QML/workers; synthetic transport, cipher and empty galleries. No sockets."""

    harness = AccountLifecycleTests.harness

    def setUp(self):
        WORK_ROOT.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='qt-account-save-', dir=WORK_ROOT)
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        (root / '.qt-lab-owner').write_text('qt-quick-isolated-v1')
        vault = SessionVault(root / 'session', protect=lambda data: data[::-1], unprotect=lambda data: data[::-1])
        def api(method, path, body=None, **kwargs):
            if path == '/v1/auth/login':
                return tokens('initial')
            if path == '/v1/auth/refresh':
                return tokens('rotated')
            if path == '/v1/community/my/map-galleries':
                self.assertEqual(kwargs['token'], 'rotated')
                return []
            if path == '/v1/auth/logout':
                return None
            raise AssertionError('unexpected synthetic account request')
        account = AccountService(root, vault=vault, api=api)
        self.galleries = _EmptyGalleries()
        self.service = RoutePlazaService(root, self.galleries, account=account, listing=lambda _query: [])
        self.addCleanup(self.service.shutdown)
        self.settings = SettingsService(root)
        self.addCleanup(self.settings.shutdown)

    def login(self):
        self.service.openAccount()
        self.service.submitAccount('alice', '', '', '', 'secret1', '')
        until(lambda: not self.service.snapshot['busy'])

    def test_failed_refresh_save_is_visible_on_return_and_in_account_dialog(self):
        window = self.harness()
        self.login()
        page = item(window, 'routePlazaPage')
        page.setProperty('visible', False)
        self.service.account._session.access_expires = 0
        with patch('account_service.os.replace', side_effect=OSError('private-path/rotated-refresh/secret1')):
            self.service.selectTab('mine')
            until(lambda: not self.service.snapshot['busy'] and self.service.snapshot['mineLoaded'])
        page.setProperty('visible', True)
        status = item(window, 'plazaStatus')
        warning = '这次没写入，重启后可能要重新登录'
        until(lambda: warning in status.property('text'))
        self.assertTrue(status.isVisible())
        self.assertFalse(self.service.snapshot['error'])
        self.assertIn('已登录：alice', item(window, 'plazaAccountStatus').property('text'))
        self.service.openAccount()
        until(lambda: item(window, 'accountDialog').property('opened'))
        message = item(window, 'accountMessage')
        self.assertTrue(message.isVisible())
        self.assertEqual(message.property('text'), warning)
        self.assertNotIn('rotated-refresh', json.dumps(self.service.snapshot))
        self.assertNotIn('private-path', json.dumps(self.service.snapshot))
        self.assertNotIn('secret1', json.dumps(self.service.snapshot))
        self.service.closeAccount()
        # Another successful operation must not overwrite an outstanding warning.
        self.service.loadMine()
        until(lambda: not self.service.snapshot['busy'])
        self.assertEqual(status.property('text').count(warning), 1)
        self.service.account._session.access_expires = 0
        self.service.loadMine()
        until(lambda: not self.service.snapshot['busy'])
        self.assertNotIn(warning, status.property('text'))

    def test_failed_login_save_stays_logged_in_and_logout_clears_visible_warning(self):
        window = self.harness()
        with patch('account_service.os.replace', side_effect=OSError('private-path/initial-refresh')):
            self.login()
        self.assertEqual(self.service.snapshot['username'], 'alice')
        self.assertFalse(self.service.snapshot['accountOpen'])
        status = item(window, 'plazaStatus')
        self.assertIn('这次没写入，重启后可能要重新登录', status.property('text'))
        self.service.logout()
        until(lambda: not self.service.snapshot['busy'])
        self.assertEqual(self.service.snapshot['username'], '')
        self.assertNotIn('这次没写入', status.property('text'))


if __name__ == "__main__":
    unittest.main()
