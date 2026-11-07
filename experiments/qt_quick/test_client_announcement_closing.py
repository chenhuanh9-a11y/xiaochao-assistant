"""Actual Client.qml close/menu seams; native/network fixture, real Qt events.

Capture transient opened/read events as well as the final visible state: a
notice opened briefly before hide is still a regression, even if later hidden.
"""
import threading
import unittest
from unittest.mock import patch

from PySide6.QtCore import QObject, Qt
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from test_client_home_announcement import fixture_client, wait
from update_service import HISTORY_URL


def item(client, name):
    return client.main.findChild(QObject, name)


def click(client, name):
    QTest.qWait(30)
    button = client.main.findChild(QQuickItem, name)
    assert button.isVisible() and button.isEnabled(), name
    QTest.mouseClick(client.main, Qt.LeftButton, Qt.NoModifier,
                     button.mapToScene(button.boundingRect().center()).toPoint())
    QTest.qWait(80)


def pending_close_notice(client):
    client.main.close()
    wait(lambda: item(client, 'clientCloseChoice').property('opened'))
    client.info.scheduleStartupCheck()
    client.info._startup_timer.stop()
    client.info._startup_check()
    wait(lambda: not client.info.checking and bool(client.info.release))
    dialog = item(client, 'startupUpdateAnnouncement')
    assert not dialog.property('visible') and client.info.hasUnread
    return dialog


class AnnouncementClosingTests(unittest.TestCase):
    def assert_unshown(self, client, dialog, opened, read):
        self.assertEqual(opened, [], 'A transient popup is not a deferred notice')
        read.assert_not_called()
        self.assertFalse(dialog.property('visible'))
        self.assertTrue(client.info.hasUnread)
        self.assertFalse(client.info._service.state_path.exists())
        self.assertEqual(client.warnings, [])

    def test_background_decision_never_opens_or_reads_until_explicit_restore(self):
        with fixture_client(probe=True) as (client, calls):
            dialog = pending_close_notice(client)
            opened = []
            dialog.opened.connect(lambda: opened.append(True))
            released = []
            choice = item(client, 'clientCloseChoice')
            choice.visibleChanged.connect(lambda: released.append(
                (client.main.isVisible(), client.coordinator.closing)) if not choice.property('visible') else None)
            with patch.object(client.info, '_queue_notice_read', wraps=client.info._queue_notice_read) as read:
                click(client, 'closeToBackground')
                self.assertEqual(released, [(False, False)])
                self.assertFalse(client.main.isVisible())
                self.assert_unshown(client, dialog, opened, read)
                client.coordinator.navigate('home')
                wait(lambda: dialog.property('opened') and not client.info.checking)
                self.assertEqual(opened, [True])
                read.assert_called_once()
                self.assertFalse(client.info.hasUnread)
                # Closing an already-visible notice via the real main-window X
                # must not synchronously re-enter its blocked binding.
                client.main.close()
                wait(lambda: item(client, 'clientCloseChoice').property('opened')
                     and not dialog.property('visible'))
                click(client, 'cancelClose')
                self.assertEqual(opened, [True])
                self.assertFalse(dialog.property('visible'))
                self.assertEqual(client.warnings, [])

    def test_cancel_and_escape_release_unread_notice_once(self):
        for escape in (False, True):
            with self.subTest(escape=escape), fixture_client(probe=True) as (client, calls):
                dialog = pending_close_notice(client)
                opened = []
                dialog.opened.connect(lambda: opened.append(True))
                if escape:
                    QTest.keyClick(client.main, Qt.Key_Escape)
                else:
                    click(client, 'cancelClose')
                wait(lambda: dialog.property('opened') and not client.info.checking)
                self.assertEqual(opened, [True])
                self.assertTrue(client.main.isVisible())
                self.assertFalse(client.info.hasUnread)
                self.assertEqual(client.warnings, [])

    def test_exit_decision_gates_pending_and_late_notices_during_drain(self):
        with fixture_client(probe=True) as (client, calls):
            dialog = pending_close_notice(client)
            opened = []
            dialog.opened.connect(lambda: opened.append(True))
            released = []
            choice = item(client, 'clientCloseChoice')
            choice.visibleChanged.connect(lambda: released.append(
                (client.main.isVisible(), client.coordinator.closing)) if not choice.property('visible') else None)
            # Delay only the completed-drain step. The real button, requestClose,
            # closing state and input/capture/session cancellation still run.
            client.coordinator._close_timer.setInterval(10000)
            with patch.object(client.coordinator, '_finish_close'), \
                 patch.object(client.info, '_queue_notice_read', wraps=client.info._queue_notice_read) as read:
                click(client, 'closeApplication')
                self.assertTrue(client.coordinator.closing)
                self.assertEqual(released, [(True, True)])
                self.assertFalse(item(client, 'clientCloseChoice').property('visible'))
                self.assert_unshown(client, dialog, opened, read)
                client.info.checkUpdates()
                wait(lambda: not client.info.checking)
                QTest.qWait(30)
                self.assert_unshown(client, dialog, opened, read)

    def test_late_notice_result_respects_background_and_manual_dismissal(self):
        for manual in (False, True):
            with self.subTest(manual=manual), fixture_client(probe=True) as (client, calls):
                entered, finish = threading.Event(), threading.Event()
                fetch = client.info._service._fetch
                def delayed(url):
                    if url != HISTORY_URL:
                        entered.set(); finish.wait(4)
                    return fetch(url)
                client.info._service._fetch = delayed
                dialog = item(client, 'startupUpdateAnnouncement')
                opened = []
                dialog.opened.connect(lambda: opened.append(True))
                try:
                    if manual:
                        click(client, 'homeOpenAnnouncement')
                        wait(lambda: dialog.property('opened'))
                    else:
                        client.info.scheduleStartupCheck()
                        client.info._startup_timer.stop(); client.info._startup_check()
                    wait(entered.is_set)
                    client.main.close()
                    wait(lambda: item(client, 'clientCloseChoice').property('opened')
                         and not dialog.property('visible'))
                    opened.clear()  # The manual loading shell was intentionally opened.
                    click(client, 'closeToBackground')
                    with patch.object(client.info, '_queue_notice_read', wraps=client.info._queue_notice_read) as read:
                        finish.set()
                        wait(lambda: not client.info.checking)
                        QTest.qWait(30)
                        self.assertFalse(client.main.isVisible())
                        self.assert_unshown(client, dialog, opened, read)
                        client.coordinator.navigate('home')
                        if manual:
                            QTest.qWait(80)
                            self.assert_unshown(client, dialog, opened, read)
                            click(client, 'homeOpenAnnouncement')
                        wait(lambda: dialog.property('opened') and not client.info.checking)
                        self.assertEqual(opened, [True])
                        read.assert_called_once()
                finally:
                    finish.set()

    def test_menu_overlap_and_close_choice_do_not_dispatch_hidden_actions(self):
        with fixture_client(probe=True) as (client, calls):
            client.main.resize(1440, 1100)
            receipt = dict(client.session.snapshot['lastReceipt'])
            click(client, 'homeMapMore')
            menu = item(client, 'homeMapMoreMenu')
            self.assertTrue(menu.property('visible'))
            client.info.openAnnouncement()
            dialog = item(client, 'startupUpdateAnnouncement')
            wait(lambda: dialog.property('opened') and not client.info.checking)
            client.main.close()
            wait(lambda: item(client, 'clientCloseChoice').property('opened')
                 and not dialog.property('visible'))
            click(client, 'cancelClose')
            QTest.keyClick(client.main, Qt.Key_Escape)
            QTest.qWait(80)
            self.assertFalse(menu.property('visible'))
            self.assertFalse(dialog.property('visible'))
            self.assertEqual(client.session.snapshot['lastReceipt'], receipt)
            self.assertEqual(client.warnings, [])


if __name__ == '__main__':
    unittest.main()
