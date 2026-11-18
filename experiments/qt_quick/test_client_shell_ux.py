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
    def test_saved_user_shortcuts_take_priority_and_navigation_recovers(self):
        with fixture_client(probe=False) as (client, _calls):
            dismiss_notice(client)
            window = client.main
            settings = client.settings
            actions = list(settings.values["actionHotkeys"])
            original = list(actions)
            actions[0] = "Ctrl+Tab"
            settings.setValue("actionHotkeys", actions)
            # A draft has not changed the live shortcut owner.
            self.assertNotIn("Ctrl+Tab", settings.reservedShellShortcuts)
            press(client, Qt.Key_Tab, Qt.ControlModifier)
            self.assertEqual(window.property("page"), "gallery")
            settings.save()
            wait(lambda: not settings.busy)
            self.assertIn("Ctrl+Tab", settings.reservedShellShortcuts)
            visibility = []
            client.coordinator.controlVisibilityChanged.connect(
                lambda visible, source: visibility.append((visible, source)))
            press(client, Qt.Key_Tab, Qt.ControlModifier)
            self.assertEqual(window.property("page"), "gallery")
            self.assertEqual(len(visibility), 1)
            self.assertEqual(visibility[0][1], "keyboard")
            wait(lambda: not settings.busy)
            # The reverse chord remains available, not globally disabled.
            press(client, Qt.Key_Tab, Qt.ControlModifier | Qt.ShiftModifier)
            self.assertEqual(window.property("page"), "home")

            # Native-only bindings must also win, without introducing a second
            # Qt action owner. Actual native delivery is tested separately.
            actions[0] = "Ctrl+2"
            actions[1] = "Ctrl+PAGEDOWN"
            settings.setValue("actionHotkeys", actions)
            settings.setValue("hotkey", "Alt+LEFT")
            settings.save()
            wait(lambda: not settings.busy)
            self.assertEqual(settings.applicationPanelShortcut, "")
            self.assertTrue({"Ctrl+2", "Ctrl+PgDown", "Alt+Left"}.issubset(
                settings.reservedShellShortcuts))
            press(client, Qt.Key_2, Qt.ControlModifier)
            press(client, Qt.Key_PageDown, Qt.ControlModifier)
            self.assertEqual(window.property("page"), "home")
            client.coordinator.navigate("resources")
            wait(lambda: window.property("page") == "resources")
            press(client, Qt.Key_Left, Qt.AltModifier)
            self.assertEqual(window.property("page"), "resources")

            settings.setValue("actionHotkeys", original)
            settings.setValue("hotkey", "G")
            settings.save()
            wait(lambda: not settings.busy)
            self.assertEqual(settings.reservedShellShortcuts, [])
            press(client, Qt.Key_2, Qt.ControlModifier)
            self.assertEqual(window.property("page"), "gallery")
            # No page switching while capturing a binding, even without the
            # capture dialog's overlay/focus being present in this fixture.
            settings.beginShortcutCapture("hotkey")
            press(client, Qt.Key_1, Qt.ControlModifier)
            self.assertEqual(window.property("page"), "gallery")
            settings.cancelShortcutCapture()
            press(client, Qt.Key_1, Qt.ControlModifier)
            self.assertEqual(window.property("page"), "home")
            self.assertEqual(client.warnings, [])

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
