"""Real offscreen QML/event-loop notice tests; fixture network, no install/game/browser."""
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QObject, QPointF, QMetaObject, QUrl, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine, QQmlExpression
from PySide6.QtTest import QTest
from PySide6.QtQuick import QQuickItem
from client_info import ClientInfoService, WORK_ROOT
from update_service import HISTORY_URL


def notice(notes='Current release notice', version='1.1.1'):
    return dict(schemaVersion=1, platform='windows', version=version, versionCode=23,
                releaseNotes=notes, publishedAt='2026-11-07',
                downloadPageUrl='https://pan.quark.cn/s/fixture')


class AnnouncementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        WORK_ROOT.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='qt-announcement-', dir=WORK_ROOT)
        self.root = Path(self.temp.name)
        (self.root / '.qt-lab-owner').write_text('qt-quick-isolated-v1')
        self.services, self.views, self.calls, self.opened = [], [], [], []
        self.payload = notice()
        guard = patch('urllib.request.urlopen', side_effect=AssertionError('Real network forbidden'))
        guard.start()
        self.addCleanup(guard.stop)

    def tearDown(self):
        for service in self.services:
            service.shutdown()
        for window, component, engine in self.views:
            window.hide()
            window.deleteLater()
        self.app.processEvents()
        self.views.clear()
        self.temp.cleanup()

    def wait(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.002)
        self.assertTrue(predicate(), 'local notice worker timeout')

    def fetch(self, url):
        self.calls.append(url)
        return {'releases': []} if url == HISTORY_URL else dict(self.payload)

    def service(self, fetch=None):
        service = ClientInfoService(self.root, fetch=fetch or self.fetch,
                                   open_url=lambda url: self.opened.append(url.toString()) or True)
        self.services.append(service)
        return service

    def startup(self, service):
        self.assertTrue(service.scheduleStartupCheck())
        service._startup_timer.stop()
        service._startup_check()

    def view(self, service, *, visible=True):
        engine = QQmlEngine()
        engine.rootContext().setContextProperty('infoService', service)
        warnings = []
        engine.warnings.connect(lambda rows: warnings.extend(str(row) for row in rows))
        component = QQmlComponent(engine)
        component.setData(b'''import QtQuick
import QtQuick.Controls.Basic
ApplicationWindow {
    id: shell; width: 960; height: 680; visible: false
    property bool noticeBlocked: false
    property string selectedPage: "home"
    AppButton { objectName: "openAnnouncement"; text: "Notices"; onClicked: infoService.openAnnouncement() }
    ClientAnnouncement { hostWindow: shell; blocked: shell.noticeBlocked }
}''', QUrl.fromLocalFile(str(Path(__file__).with_name('__notice_test__.qml'))))
        self.wait(lambda: not component.isLoading())
        self.assertFalse(component.isError(), str(component.errors()))
        window = component.create()
        self.assertIsNotNone(window, str(component.errors()))
        self.views.append((window, component, engine))
        window.setVisible(visible)
        return window, window.findChild(QObject, 'startupUpdateAnnouncement'), warnings

    def click(self, window, name):
        QTest.qWait(20)  # Let the scene polish controls whose visibility just changed.
        button = window.findChild(QQuickItem, name)
        self.assertTrue(button.isVisible())
        self.assertTrue(button.isEnabled())
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier,
                         button.mapToScene(QPointF(button.width()/2, button.height()/2)).toPoint())

    def test_manual_cold_open_loading_failure_retry_and_read_again(self):
        entered, finish = threading.Event(), threading.Event()
        failing = [True]
        def fetch(url):
            if failing[0]:
                entered.set(); finish.wait(2)
                raise OSError('fixture offline')
            return self.fetch(url)
        self.addCleanup(finish.set)
        service = self.service(fetch)
        window, dialog, warnings = self.view(service)
        self.wait(lambda: window.isVisible())
        QTest.qWait(20)
        self.click(window, 'openAnnouncement')
        self.wait(lambda: entered.is_set() and dialog.property('opened'))
        self.assertTrue(window.findChild(QObject, 'announcementLoading').property('visible'))
        self.assertIn('正在读取', window.findChild(QObject, 'announcementLoadStatus').property('text'))
        self.assertFalse(service._service.state_path.exists())
        finish.set()
        self.wait(lambda: service.updatePhase == 'error')
        self.assertIn('读取失败', window.findChild(QObject, 'announcementLoadStatus').property('text'))
        failing[0] = False
        self.click(window, 'announcementRetry')
        self.wait(lambda: dialog.property('presented') and not service.checking)
        self.assertEqual(dialog.property('releaseData')['notes'], self.payload['releaseNotes'])
        identity = service.release['announcementId']
        self.assertIn(identity, service._service.seen_announcement_ids)
        QTest.keyClick(window, Qt.Key_Escape)
        self.wait(lambda: not dialog.property('visible'))
        calls = list(self.calls)
        button = window.findChild(QQuickItem, 'openAnnouncement')
        button.forceActiveFocus()
        QTest.keyClick(window, Qt.Key_Space)
        self.wait(lambda: dialog.property('opened') and not service.checking)
        self.assertEqual(dialog.property('releaseData')['announcementId'], identity)
        self.assertEqual(self.calls, calls, 'Rereading must use the cached validated snapshot')
        self.assertEqual(warnings, [])

    def test_closing_cold_request_does_not_reopen_on_late_result_or_startup_timer(self):
        entered, finish = threading.Event(), threading.Event()
        def fetch(url):
            if url != HISTORY_URL:
                entered.set(); finish.wait(2)
            return self.fetch(url)
        self.addCleanup(finish.set)
        service = self.service(fetch)
        window, dialog, warnings = self.view(service)
        self.assertTrue(service.scheduleStartupCheck())
        service._startup_timer.stop()
        self.click(window, 'openAnnouncement')
        self.wait(lambda: entered.is_set() and dialog.property('opened'))
        # A repeated request joins the same network operation.
        service.openAnnouncement()
        QTest.keyClick(window, Qt.Key_Escape)
        self.wait(lambda: not dialog.property('visible'))
        finish.set()
        self.wait(lambda: not service.checking)
        service._startup_check()
        service.changed.emit(); QTest.qWait(30)
        self.assertFalse(dialog.property('visible'))
        self.assertTrue(service.hasUnread)
        self.assertFalse(service._service.state_path.exists())
        self.assertEqual(len(self.calls), 2)
        service.openAnnouncement()
        self.wait(lambda: dialog.property('opened') and not service.checking)
        self.assertFalse(service.hasUnread)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(warnings, [])

    def test_manual_join_of_startup_check_hidden_result_does_not_restore_window(self):
        entered, finish = threading.Event(), threading.Event()
        def fetch(url):
            if url != HISTORY_URL:
                entered.set(); finish.wait(2)
            return self.fetch(url)
        self.addCleanup(finish.set)
        service = self.service(fetch)
        window, dialog, warnings = self.view(service)
        self.startup(service)
        self.wait(entered.is_set)
        service.openAnnouncement()
        self.wait(lambda: dialog.property('opened'))
        window.hide()
        self.wait(lambda: not dialog.property('visible'))
        finish.set()
        self.wait(lambda: not service.checking)
        self.assertFalse(window.isVisible())
        self.assertFalse(service._service.state_path.exists())
        window.show(); QTest.qWait(30)
        self.assertFalse(dialog.property('visible'))
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(service.hasUnread)
        self.assertEqual(warnings, [])

    def test_closed_failed_manual_view_does_not_revive_when_retry_succeeds(self):
        failing = [True]
        def fetch(url):
            if failing[0]:
                raise OSError('fixture offline')
            return self.fetch(url)
        service = self.service(fetch)
        window, dialog, warnings = self.view(service)
        self.startup(service)
        self.wait(lambda: service.updatePhase == 'error')
        self.click(window, 'openAnnouncement')
        self.wait(lambda: dialog.property('opened') and service.updatePhase == 'error')
        QTest.keyClick(window, Qt.Key_Escape)
        self.wait(lambda: not dialog.property('visible'))
        failing[0] = False
        service._service.next_check_at = 1
        self.wait(lambda: bool(service.release))
        QTest.qWait(30)
        self.assertFalse(dialog.property('visible'))
        self.assertTrue(service.hasUnread)
        self.assertFalse(service._service.state_path.exists())
        self.assertEqual(warnings, [])

    def test_cold_manual_request_waits_for_serialized_writer(self):
        entered, finish = threading.Event(), threading.Event()
        self.addCleanup(finish.set)
        service = self.service()
        window, dialog, warnings = self.view(service)
        service._write(lambda: (entered.set(), finish.wait(2)), 'fixture write')
        self.wait(entered.is_set)
        service.openAnnouncement()
        self.wait(lambda: dialog.property('opened'))
        self.assertEqual(self.calls, [])
        self.assertFalse(dialog.property('presented'))
        finish.set()
        self.wait(lambda: dialog.property('presented') and not service.checking)
        self.assertEqual(len(self.calls), 2)
        self.assertFalse(service.hasUnread)
        self.assertEqual(warnings, [])

    def test_current_version_opens_once_and_persists_only_after_visible(self):
        service = self.service()
        service._service.current_version = service._version = '1.1.1'
        window, dialog, warnings = self.view(service, visible=False)
        self.startup(service)
        self.wait(lambda: service.updatePhase == 'latest')
        self.assertFalse(dialog.property('visible'))
        self.assertFalse(service._service.state_path.exists())
        window.setProperty('selectedPage', 'settings')
        window.show()
        self.wait(lambda: dialog.property('opened') and not service.checking)
        identity = service.release['announcementId']
        self.assertIn(identity, service._service.seen_announcement_ids)
        self.assertEqual(window.property('selectedPage'), 'settings')
        self.assertEqual(dialog.property('title'), '更新公告 v1.1.1')
        QMetaObject.invokeMethod(dialog, 'close')
        self.wait(lambda: not dialog.property('visible'))
        window.hide(); window.show()
        self.app.processEvents()
        self.assertFalse(dialog.property('visible'))
        restarted = self.service()
        announced = []
        restarted.startupAnnouncement.connect(announced.append)
        self.startup(restarted)
        self.wait(lambda: restarted.updatePhase == 'latest')
        self.assertEqual(announced, [])
        self.assertEqual(self.opened, [])
        self.assertEqual(warnings, [])

    def test_same_version_new_notice_queues_without_replacing_open_content(self):
        service = self.service()
        self.payload = notice('Original notice line\n' * 80)
        window, dialog, warnings = self.view(service)
        self.startup(service)
        self.wait(lambda: dialog.property('opened') and not service.checking)
        original = service.release['announcementId']
        body = window.findChild(QObject, 'startupAnnouncementBody')
        flickable = next(child for child in body.findChildren(QObject) if child.inherits('QQuickFlickable'))
        flickable.setProperty('contentY', 500)
        self.assertEqual(flickable.property('contentY'), 500)
        self.payload = notice('<b>Revised notice</b>\n' * 80)
        service.checkUpdates()
        self.wait(lambda: not service.checking)
        self.assertEqual(dialog.property('releaseData')['announcementId'], original)
        current = service.release['announcementId']
        self.assertNotIn(current, service._service.seen_announcement_ids)
        QMetaObject.invokeMethod(dialog, 'close')
        self.wait(lambda: dialog.property('opened') and not service.checking
                  and current in service._service.seen_announcement_ids)
        notes = window.findChild(QObject, 'startupAnnouncementNotes')
        self.assertEqual(notes.property('text'), self.payload['releaseNotes'].strip())
        self.assertEqual(flickable.property('contentY'), 0)
        expression = QQmlExpression(QQmlEngine.contextForObject(notes), notes, 'textFormat === Text.PlainText')
        self.assertEqual(expression.evaluate(), (True, False))
        self.assertEqual(warnings, [])

    def test_minimized_waits_and_escape_dismisses_without_reopening(self):
        service = self.service()
        window, dialog, warnings = self.view(service)
        window.showMinimized()
        self.startup(service)
        self.wait(lambda: not service.checking)
        self.assertFalse(dialog.property('visible'))
        self.assertFalse(service._service.state_path.exists())
        window.showNormal()
        self.wait(lambda: dialog.property('opened') and not service.checking)
        QTest.keyClick(window, Qt.Key_Escape)
        self.wait(lambda: not dialog.property('visible'))
        service.changed.emit(); self.app.processEvents()
        self.assertFalse(dialog.property('visible'))
        self.assertEqual(warnings, [])

    def test_blocked_and_hidden_during_check_never_consume_notice(self):
        entered, finish = threading.Event(), threading.Event()
        def fetch(url):
            if url != HISTORY_URL:
                entered.set(); finish.wait(2)
            return self.fetch(url)
        self.addCleanup(finish.set)
        service = self.service(fetch)
        window, dialog, warnings = self.view(service)
        window.setProperty('noticeBlocked', True)
        self.startup(service)
        self.wait(entered.is_set)
        window.hide(); finish.set()
        self.wait(lambda: not service.checking)
        self.assertFalse(dialog.property('visible'))
        self.assertTrue(service.hasUnread)
        window.show(); self.app.processEvents()
        self.assertFalse(dialog.property('visible'))
        window.setProperty('noticeBlocked', False)
        self.wait(lambda: dialog.property('opened') and not service.checking)
        self.assertFalse(service.hasUnread)
        self.assertEqual(warnings, [])

    def test_shutdown_discards_late_result_without_writing_or_announcing(self):
        entered, finish = threading.Event(), threading.Event()
        def fetch(url):
            if url != HISTORY_URL:
                entered.set(); finish.wait(2)
            return self.fetch(url)
        self.addCleanup(finish.set)
        service = self.service(fetch)
        announced = []
        service.startupAnnouncement.connect(announced.append)
        self.startup(service)
        self.wait(entered.is_set)
        service.shutdown(); finish.set()
        self.wait(lambda: not service._service._events.empty())
        service._poll()
        self.assertEqual(announced, [])
        self.assertFalse(service._service.state_path.exists())

    def test_automatic_retry_survives_error_and_recovers_same_session(self):
        fail = [True]
        def fetch(url):
            if fail[0]:
                raise OSError('fixture offline')
            return self.fetch(url)
        service = self.service(fetch)
        announced = []
        service.startupAnnouncement.connect(announced.append)
        self.startup(service)
        self.wait(lambda: service.hasError)
        self.assertTrue(service._timer.isActive())
        self.assertEqual(announced, [])
        fail[0] = False
        service._service.next_check_at = 1
        self.wait(lambda: len(announced) == 1)
        self.assertFalse(service.hasError)
        self.assertFalse(service._service.state_path.exists())

    def test_current_installed_version_still_emits_new_notice(self):
        service = self.service()
        service._service.current_version = service._version = self.payload['version']
        announced = []
        service.startupAnnouncement.connect(announced.append)
        self.startup(service)
        self.wait(lambda: not service.checking)
        self.assertEqual(service.updatePhase, 'latest')
        self.assertEqual(len(announced), 1)
        self.assertFalse(service._service.state_path.exists())

    def test_shown_ack_waits_for_unrelated_write_and_keeps_exact_content(self):
        service = self.service()
        announced = []
        service.startupAnnouncement.connect(announced.append)
        self.startup(service)
        self.wait(lambda: len(announced) == 1)
        first_id = announced[0]['announcementId']
        entered, finish = threading.Event(), threading.Event()
        self.addCleanup(finish.set)
        def write():
            entered.set(); finish.wait(2)
        service._write(write, 'fixture write')
        self.wait(entered.is_set)
        service.announcementShown(first_id)
        self.assertFalse(service._service.state_path.exists())
        # A different release arriving must not be marked on behalf of old pixels.
        from update_service import parse_release
        service._service.latest = parse_release(notice('Later revision'))
        finish.set()
        self.wait(lambda: not service.checking and not service._pending_notice_reads)
        stored = json.loads(service._service.state_path.read_text())
        self.assertIn(first_id, stored['seenAnnouncementIds'])
        self.assertNotIn(service.release['announcementId'], stored['seenAnnouncementIds'])

    def test_startup_waits_for_busy_writer_and_manual_check_does_not_swallow_notice(self):
        service = self.service()
        announced = []
        service.startupAnnouncement.connect(announced.append)
        service._writing = True
        self.startup(service)
        self.assertFalse(service._startup_checked)
        self.assertEqual(self.calls, [])
        service._writing = False
        service.checkUpdates()
        service._startup_check()
        self.wait(lambda: len(announced) == 1)
        self.assertEqual(len(self.calls), 2)

    def test_startup_waits_for_pending_settings_save_then_checks(self):
        class DelayedSettings(QObject):
            changed = Signal()
            busy = False
            loadFailed = False
            persistedValues = {'automaticFailureEvidence': True}
            message = ''
            def setValue(self, key, value):
                self.desired = value
                return True
            def saveFields(self, fields):
                self.busy = True
                self.changed.emit()
                return True
            def finish(self):
                self.persistedValues = {'automaticFailureEvidence': self.desired}
                self.busy = False
                self.changed.emit()
        service = self.service()
        settings = DelayedSettings()
        service.setProviders(settings=settings)
        self.assertTrue(service.setAutomaticFailureSampling(False))
        self.startup(service)
        self.assertFalse(service._startup_checked)
        self.assertEqual(self.calls, [])
        settings.finish()
        self.wait(lambda: len(self.calls) == 2 and not service.checking)
        self.assertTrue(service._startup_checked)

    def test_notice_title_and_notes_never_interpret_manifest_markup(self):
        service = self.service()
        self.payload = notice('<b>NOT MARKUP</b>', version='9.1 <b>NOT MARKUP</b>')
        window, dialog, warnings = self.view(service)
        self.startup(service)
        self.wait(lambda: dialog.property('opened') and not service.checking)
        for name in ('startupAnnouncementTitle', 'startupAnnouncementNotes'):
            label = window.findChild(QObject, name)
            expression = QQmlExpression(QQmlEngine.contextForObject(label), label, 'textFormat === Text.PlainText')
            self.assertEqual(expression.evaluate(), (True, False))
            self.assertIn('<b>NOT MARKUP</b>', label.property('text'))
        self.assertEqual(self.opened, [])
        self.assertEqual(warnings, [])

    def test_download_click_uses_displayed_notice_not_later_check(self):
        service = self.service()
        self.payload = notice('First version', version='9.1')
        window, dialog, warnings = self.view(service)
        self.startup(service)
        self.wait(lambda: dialog.property('opened') and not service.checking)
        self.payload = dict(notice('Later version', version='9.2'),
                            downloadPageUrl='https://pan.quark.cn/s/new-fixture')
        service.checkUpdates()
        self.wait(lambda: not service.checking)
        button = window.findChild(QObject, 'startupAnnouncementQuark')
        self.assertTrue(button.property('enabled'))
        self.assertTrue(QMetaObject.invokeMethod(button, 'clicked'))
        self.assertEqual(self.opened, ['https://pan.quark.cn/s/fixture'])
        self.assertEqual(warnings, [])


if __name__ == '__main__':
    unittest.main()
