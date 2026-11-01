"""One bounded offscreen QML flow with a fake session; no native/game IO."""
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


class RecognitionTests(unittest.TestCase):
    def setUp(self):
        self.session = FakeSession()
        self.engine = QQmlEngine()
        self.engine.rootContext().setContextProperty("session", self.session)
        self.warnings = []
        self.engine.warnings.connect(lambda errors: self.warnings.extend(error.toString() for error in errors))
        self.component = QQmlComponent(self.engine, QUrl.fromLocalFile(str(Path(__file__).with_name("ClientRecognition.qml"))))
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
        found = find(self.page)
        self.assertIsNotNone(found, name)
        return found

    def click(self, name):
        item = self.item(name)
        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
        QTest.mouseClick(self.window, Qt.LeftButton, Qt.NoModifier, QPoint(round(point.x()), round(point.y())))
        APP.processEvents()

    def test_candidate_select_previous_next_confirm_reselect_and_floor_flow(self):
        self.assertEqual(self.item("recognitionCandidateList").property("count"), 2)
        self.click("recognitionCandidate_1")
        self.assertEqual(self.session.calls[-1], ("select_candidate", {"index": 1}))
        self.click("recognitionPrevious")
        self.assertEqual(self.session.state["selectedCandidateIndex"], 0)
        self.click("recognitionNext")
        self.assertEqual(self.session.state["selectedCandidateIndex"], 1)
        self.click("recognitionConfirm")
        self.assertTrue(self.session.state["identityLocked"])
        self.assertFalse(self.item("recognitionNext").isEnabled())
        self.click("recognitionReselect")
        self.assertFalse(self.session.state["identityLocked"])
        self.click("recognitionFloor_2")
        self.assertEqual(self.session.calls[-1], ("set_floor", {"floor": "2"}))
        self.click("recognitionCycleFloor")
        self.assertEqual(self.session.state["floor"], "B1")
        self.assertEqual([action for action, payload in self.session.calls], [
            "select_candidate", "previous_candidate", "next_candidate", "confirm_route",
            "reselect_map", "set_floor", "cycle_floor"])
        self.assertEqual(self.warnings, [])

    def test_unsupported_empty_state_disables_commands_and_has_no_browser_dependency(self):
        self.session.state.update(candidateRows=[], selectedCandidateIndex=-1,
                                  selectedCandidate={}, availableFloors=[],
                                  commands={action: dict(available=False) for action in ACTIONS})
        self.session.changed.emit()
        APP.processEvents()
        self.assertEqual(self.item("recognitionCandidateList").property("count"), 0)
        for name in ("recognitionCapture", "recognitionPrevious", "recognitionNext",
                     "recognitionConfirm", "recognitionReselect", "recognitionCycleFloor"):
            self.assertFalse(self.item(name).isEnabled(), name)
        self.click("recognitionNext")
        self.assertEqual(self.session.calls, [])
        self.assertEqual(self.warnings, [])


if __name__ == "__main__":
    unittest.main()
