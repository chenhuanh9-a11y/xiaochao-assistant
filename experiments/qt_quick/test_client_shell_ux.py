"""Shell feedback, page keys and close-dialog focus. Offscreen, no game or install."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
from PySide6.QtCore import QObject, Qt
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from test_client_home_announcement import APP, fixture_client, wait


def item(client, name):
    return client.main.findChild(QObject, name)


def dismiss_notice(client):
    dialog = item(client, "startupUpdateAnnouncement")
    if dialog is None or not dialog.property("visible"):
        return
    button = client.main.findChild(QQuickItem, "startupUpdateLater")
    QTest.mouseClick(client.main, Qt.LeftButton, Qt.NoModifier,
                     button.mapToScene(button.boundingRect().center()).toPoint())
    wait(lambda: not dialog.property("visible"))


def press(client, key, modifiers=Qt.NoModifier):
    client.main.requestActivate()
    QTest.qWait(30)
    QTest.keyClick(client.main, key, modifiers)
    QTest.qWait(40)


class ShellUxTests(unittest.TestCase):
    def test_page_keys_status_and_close_focus(self):
        with fixture_client(probe=False) as (client, _calls):
            dismiss_notice(client)
            self.assertEqual(client.warnings, [])
            window = client.main
            window.setWidth(1000)
            window.setHeight(720)
            QTest.qWait(30)
            self.assertEqual(item(client, "connectionStatusText").property("text"), "还没连上游戏")
            window.setWidth(700)
            QTest.qWait(30)
            self.assertEqual(item(client, "connectionStatusText").property("text"), "未连接")
            self.assertTrue(item(client, "connectionStatusChip").property("visible"))
            window.setWidth(1000)

            press(client, Qt.Key_2, Qt.ControlModifier)
            self.assertEqual(window.property("page"), "gallery")
            self.assertEqual(window.activeFocusItem().objectName(), "nav_gallery")
            press(client, Qt.Key_1, Qt.ControlModifier)
            self.assertEqual(window.property("page"), "home")
            self.assertEqual(window.activeFocusItem().objectName(), "nav_home")

            client.coordinator.navigate("resources")
            wait(lambda: window.property("page") == "resources")
            press(client, Qt.Key_Left, Qt.AltModifier)
            self.assertEqual(window.property("page"), "gallery")

            pages = item(client, "clientPages")
            client.settings.setValue("reduceMotion", True)
            QTest.qWait(20)
            client.coordinator.navigate("help")
            QTest.qWait(20)
            self.assertEqual(window.property("page"), "help")
            self.assertEqual(pages.property("opacity"), 1)
            client.settings.setValue("reduceMotion", False)
            QTest.qWait(20)
            client.coordinator.navigate("home")
            QTest.qWait(20)
            self.assertLess(pages.property("opacity"), 1)
            wait(lambda: pages.property("opacity") >= 0.999)

            window.close()
            wait(lambda: item(client, "clientCloseChoice").property("opened")
                 and window.activeFocusItem() is not None
                 and window.activeFocusItem().objectName() == "cancelClose")
            self.assertIn("快捷键仍然可用", item(client, "closeChoiceHint").property("text"))
            self.assertEqual(window.activeFocusItem().objectName(), "cancelClose")
            press(client, Qt.Key_2, Qt.ControlModifier)
            self.assertEqual(window.property("page"), "home")
            QTest.keyClick(window, Qt.Key_Escape)
            QTest.qWait(40)
            self.assertFalse(item(client, "clientCloseChoice").property("visible"))
            self.assertEqual(client.warnings, [])


if __name__ == "__main__":
    unittest.main()
