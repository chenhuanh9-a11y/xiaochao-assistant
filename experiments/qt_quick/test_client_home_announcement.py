"""Actual Client.qml/home mouse + keyboard flows with offline/native fixtures.

These are source/offscreen composition checks, not a Windows package or game
acceptance test. Production/private identities use the same startup call.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from PySide6.QtCore import QObject, QPointF, Qt, QCoreApplication, QEvent
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from client import QtClient
from client_paths import WORK_ROOT
from client_providers import EngineFactories
from client_route_plaza import RoutePlazaService
from test_client_input import Native
from test_client_live_runtime import FixtureCapture, fixture_engine
from test_client_announcement import notice
from update_service import HISTORY_URL

APP = QGuiApplication.instance() or QGuiApplication([])


def wait(predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(5)
    assert predicate(), 'Full client announcement did not settle'


@contextmanager
def fixture_client(*, probe):
    WORK_ROOT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='qt-home-notice-', dir=WORK_ROOT) as directory:
        root = Path(directory)
        (root / '.qt-lab-owner').write_text('qt-quick-isolated-v1')
        (root / 'ui-settings.json').write_text(json.dumps({'onboardingCompleted': True}))
        calls = []
        def fetch(url):
            calls.append(url)
            return {'releases': []} if url == HISTORY_URL else notice('Offline home announcement fixture')
        def offline_plaza(*args, **kwargs):
            return RoutePlazaService(*args, **kwargs, listing=lambda query: [])
        capture = FixtureCapture()
        capture.is_current = lambda *a, **k: True
        client = None
        with patch('urllib.request.urlopen', side_effect=AssertionError('Real network forbidden')), \
             patch('client_input.NativeWindowAPI', return_value=Native()), \
             patch('client_capture.ClientCaptureProvider', return_value=capture), \
             patch('client_coordinator.another_assistant_running', return_value=False), \
             patch('client_route_plaza.RoutePlazaService', side_effect=offline_plaza), \
             patch.object(EngineFactories, '_build', lambda self, mode, **kw: fixture_engine(mode)):
            try:
                client = QtClient(APP, root, probe_output=root / 'unused.json' if probe else None,
                                  info_fetch=fetch)
                wait(lambda: not client.session.busy and client._initialized)
                client.onboarding.dismiss()
                yield client, calls
            finally:
                if client is not None:
                    client.shutdown()
                    client.engine.deleteLater()
                    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                    APP.processEvents()


class HomeAnnouncementTests(unittest.TestCase):
    def wait_for_announcement(self, client, dialog):
        notes = client.main.findChild(QObject, 'startupAnnouncementNotes')
        expected = 'Offline home announcement fixture'
        def consumed_and_saved():
            release = dialog.property('releaseData')
            if hasattr(release, 'toVariant'):  # Initial QML object literal.
                release = release.toVariant()
            identity = release.get('announcementId')
            return (dialog.property('opened') and dialog.property('presented')
                    and bool(identity) and release.get('notes') == expected
                    and notes.property('text') == expected
                    and not client.info.checking and not client.info.hasUnread
                    and identity in client.info._service.seen_announcement_ids)
        # Fetch completion precedes the deferred QML consumer and its read write.
        wait(consumed_and_saved)

    def test_probe_never_schedules_and_home_entry_click_and_keyboard_reread(self):
        with fixture_client(probe=True) as (client, calls):
            self.assertFalse(client.info._startup_requested)
            self.assertFalse(client.info._startup_timer.isActive())
            # Cross the normal startup delay, not just construction time.
            QTest.qWait(2600)
            self.assertEqual(calls, [])
            summary = client.main.findChild(QObject, 'homeAnnouncementSummary')
            self.assertFalse(summary.property('visible'))
            self.assertEqual(summary.property('font').pixelSize(), 14)
            dialog = client.main.findChild(QObject, 'startupUpdateAnnouncement')
            button = client.main.findChild(QQuickItem, 'homeOpenAnnouncement')
            for width, height in ((650, 470), (1000, 720), (1440, 960)):
                client.main.resize(width, height)
                QTest.qWait(50)
                self.assertTrue(button.isVisible())
                point = button.mapToScene(QPointF(button.width()/2, button.height()/2))
                self.assertTrue(width - 220 < point.x() < width and 0 < point.y() < height)
                QTest.mouseClick(client.main, Qt.LeftButton, Qt.NoModifier, point.toPoint())
                self.wait_for_announcement(client, dialog)
                self.assertEqual(dialog.property('releaseData')['notes'], 'Offline home announcement fixture')
                self.assertFalse(client.info.hasUnread)
                self.assertTrue(summary.property('visible'))
                self.assertIn('Offline home announcement fixture', summary.property('text'))
                self.assertEqual(client.main.property('page'), 'home')
                QTest.keyClick(client.main, Qt.Key_Escape)
                wait(lambda: not dialog.property('visible'))
            self.assertEqual(len(calls), 2)
            button.forceActiveFocus()
            QTest.keyClick(client.main, Qt.Key_Space)
            self.wait_for_announcement(client, dialog)
            self.assertEqual(dialog.property('releaseData')['notes'], 'Offline home announcement fixture')
            self.assertEqual(len(calls), 2)
            self.assertEqual(client.warnings, [])

    def test_fetch_completion_precedes_deferred_qml_consumer(self):
        with fixture_client(probe=True) as (client, calls):
            client.info.openAnnouncement()
            client.info._timer.stop()
            dialog = client.main.findChild(QObject, 'startupUpdateAnnouncement')
            wait(lambda: dialog.property('opened') and not client.info._service._events.empty())
            # Consume the producer result without yielding to Qt.callLater.
            client.info._poll()
            self.assertTrue(dialog.property('opened') and not client.info.checking)
            self.assertNotIn('announcementId', dialog.property('releaseData'))
            self.assertTrue(client.info.hasUnread)
            self.wait_for_announcement(client, dialog)
            self.assertEqual(dialog.property('releaseData')['notes'], 'Offline home announcement fixture')
            self.assertEqual(len(calls), 2)
            self.assertEqual(client.warnings, [])

    def test_normal_source_client_schedules_shared_startup_notice_once(self):
        with fixture_client(probe=False) as (client, calls):
            self.assertFalse(client.paths.production)
            self.assertTrue(client.info._startup_requested)
            client.info._startup_timer.setInterval(1)
            dialog = client.main.findChild(QObject, 'startupUpdateAnnouncement')
            self.wait_for_announcement(client, dialog)
            self.assertEqual(dialog.property('releaseData')['notes'], 'Offline home announcement fixture')
            self.assertEqual(len(calls), 2)
            client.info._startup_check()
            QTest.qWait(20)
            self.assertEqual(len(calls), 2)
            self.assertEqual(client.warnings, [])


if __name__ == '__main__':
    unittest.main()
