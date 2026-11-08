"""Direct home actions after retiring diagnostic pages; fake session, no game IO."""
from __future__ import annotations

import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from copy import deepcopy
from pathlib import Path
import unittest

from PySide6.QtCore import QObject, Property, QPoint, QPointF, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest

APP = QGuiApplication.instance() or QGuiApplication([])
ACTIONS = ("capture_now", "previous_candidate", "next_candidate", "select_candidate",
           "confirm_route", "reselect_map", "set_floor", "cycle_floor")


class FakeSession(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        rows = [dict(key="map-a:1", mapId="map-a", name="候选地图甲", floor="1", score=.8, reason="待核对地图"),
                dict(key="map-b:2", mapId="map-b", name="候选地图乙", floor="2", score=.7, reason="待核对楼层")]
        self.state = dict(candidateRows=rows, selectedCandidateIndex=0,
                          selectedCandidate=rows[0], identityLocked=False,
                          availableFloors=["B1", "1", "2"], floor="1",
                          commands={action: dict(available=True) for action in ACTIONS})
        self.calls = []

    snapshot = Property("QVariantMap", lambda self: deepcopy(self.state), notify=changed)
    busy = Property(bool, lambda self: False, notify=changed)
    message = Property(str, lambda self: "测试会话：等待核对", notify=changed)

    @Slot(str)
    def dispatch(self, action):
        self.dispatchWithPayload(action, {})

    @Slot(str, "QVariantMap")
    def dispatchWithPayload(self, action, payload):
        payload = dict(payload)
        self.calls.append((action, payload))
        index = self.state["selectedCandidateIndex"]
        if action == "select_candidate":
            index = payload["index"]
        elif action == "previous_candidate":
            index = (index - 1) % len(self.state["candidateRows"])
        elif action == "next_candidate":
            index = (index + 1) % len(self.state["candidateRows"])
        elif action in ("confirm_route", "reselect_map"):
            locked = action == "confirm_route"
            self.state["identityLocked"] = locked
            for command in ("select_candidate", "previous_candidate", "next_candidate", "confirm_route"):
                self.state["commands"][command]["available"] = not locked
        elif action == "set_floor":
            self.state["floor"] = payload["floor"]
        elif action == "cycle_floor":
            floors = self.state["availableFloors"]
            self.state["floor"] = floors[(floors.index(self.state["floor"]) + 1) % len(floors)]
        if self.state["candidateRows"]:
            self.state["selectedCandidateIndex"] = index
            self.state["selectedCandidate"] = self.state["candidateRows"][index]
        self.changed.emit()


class Coordinator(QObject):
    def __init__(self):
        super().__init__()
        self.calls = []
    @Slot()
    def recaptureFromMain(self): self.calls.append("capture")
    @Slot()
    def requestManualMapSelection(self): self.calls.append("map")


class HomeMapActionTests(unittest.TestCase):
    def setUp(self):
        self.session = FakeSession()
        self.coordinator = Coordinator()
        self.engine = QQmlEngine()
        self.engine.rootContext().setContextProperty("session", self.session)
        self.engine.rootContext().setContextProperty("clientCoordinator", self.coordinator)
        self.warnings = []
        self.engine.warnings.connect(lambda errors: self.warnings.extend(error.toString() for error in errors))
        self.component = QQmlComponent(self.engine, QUrl.fromLocalFile(str(Path(__file__).with_name("HomeMapActions.qml"))))
        self.assertFalse(self.component.isError(), "\n".join(error.toString() for error in self.component.errors()))
        self.page = self.component.create()
        self.assertIsNotNone(self.page, "\n".join(error.toString() for error in self.component.errors()))
        self.window = QQuickWindow()
        self.window.resize(880, 680)
        self.page.setParentItem(self.window.contentItem())
        self.page.setWidth(880)
        self.page.setHeight(680)
        self.window.show()
        APP.processEvents()
        QTest.qWait(15)

    def tearDown(self):
        self.window.hide()
        self.page.setParentItem(None)
        self.page.deleteLater()
        self.window.deleteLater()
        APP.processEvents()

    def item(self, name):
        def find(item):
            if item.objectName() == name:
                return item
            for child in item.childItems():
                match = find(child)
                if match is not None:
                    return match
            return None
        found = find(self.window.contentItem())
        self.assertIsNotNone(found, name)
        return found

    def click(self, name):
        item = self.item(name)
        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
        QTest.mouseClick(self.window, Qt.LeftButton, Qt.NoModifier, QPoint(round(point.x()), round(point.y())))
        APP.processEvents()

    def test_direct_actions_remain_and_retired_pages_are_absent(self):
        self.click("homeSelectMap")
        self.open_menu()
        self.click("homeRecapture")
        self.assertEqual(self.coordinator.calls, ["map", "capture"])
        for name in ("ClientRecognition.qml", "ReplayCanvas.qml"):
            self.assertFalse(Path(__file__).with_name(name).exists())
        self.assertFalse(self.warnings)

    def open_menu(self):
        self.click("homeMapMore")
        QTest.qWait(180); APP.processEvents()
        self.assertTrue(self.menu().property("visible"))

    def menu(self):
        return self.page.findChild(QObject, "homeMapMoreMenu")

    def test_only_selection_and_more_remain_at_narrow_width(self):
        self.page.setWidth(250)
        APP.processEvents(); QTest.qWait(20)
        for name in ("homeSelectMap", "homeMapMore"):
            item = self.item(name)
            self.assertGreaterEqual(item.x(), 0)
            self.assertLessEqual(item.x() + item.width(), self.page.width())
        self.assertFalse(self.menu().property("visible"))

    def test_keyboard_and_outside_click_cancel_do_not_dispatch(self):
        self.open_menu()
        QTest.keyClick(self.window, Qt.Key_Escape)
        QTest.qWait(180)
        self.assertFalse(self.menu().property("visible"))
        self.open_menu()
        QTest.mouseClick(self.window, Qt.LeftButton, Qt.NoModifier, QPoint(850, 650))
        QTest.qWait(180)
        self.assertFalse(self.menu().property("visible"))
        self.assertEqual(self.coordinator.calls, [])
        self.assertEqual(self.session.calls, [])
        self.open_menu()
        self.click("homeRecapture")
        QTest.qWait(180)
        self.assertFalse(self.menu().property("visible"))
        self.assertEqual(self.coordinator.calls, ["capture"])

    def test_floor_menu_preserves_s0_and_legacy_s1_without_auto_s1_override(self):
        self.open_menu()
        self.click("homeFloor_2")
        self.assertEqual(self.session.calls, [("set_floor", {"floor": "2"})])
        self.session.state.update(season="s1", availableFloors=["-1", "-2", "-3", "-4"])
        self.session.changed.emit(); APP.processEvents()
        self.assertEqual(self.page.property("floors").toVariant(), [])
        self.session.state.update(manualAlignmentSession=True, automaticFloorObservationAvailable=False)
        self.session.changed.emit(); APP.processEvents()
        self.open_menu()
        self.click("homeFloor_-4")
        self.assertEqual(self.session.calls[-1], ("set_floor", {"floor": "-4"}))
        self.session.state.update(automaticFloorObservationAvailable=True)
        self.session.changed.emit(); APP.processEvents()
        self.open_menu()
        self.click("homeFloor_auto")
        self.assertEqual(self.session.calls[-1], ("set_floor", {"floor": "auto"}))
        self.assertEqual(len(self.session.calls), 3)

    def test_s1_auto_hides_manual_selection_and_locks_block_actions(self):
        self.session.state.update(season="s1", resourceLocked=True)
        self.session.changed.emit(); APP.processEvents()
        self.assertFalse(self.item("homeSelectMap").isVisible())
        self.open_menu()
        self.assertFalse(self.item("homeRecapture").isEnabled())
        self.session.state.update(manualAlignmentSession=True, resourceLocked=False)
        self.session.changed.emit(); APP.processEvents()
        self.assertTrue(self.item("homeSelectMap").isVisible())
        self.assertTrue(self.item("homeRecapture").isEnabled())


if __name__ == "__main__":
    unittest.main()
