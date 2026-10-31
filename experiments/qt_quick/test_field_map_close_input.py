"""Production coordinator/runtime path; fake native IO and synthetic pixels only."""
import copy
import time
import unittest
from unittest.mock import Mock, patch

import test_client_coordinator as coordinator_seams
import test_client_game_floor_click as floor_seams
from test_client_game_floor_input import Source
import cv2
import numpy as np


def controls():
    frame = np.full((720, 1280, 3), 25, np.uint8)
    cv2.line(frame, (1190, 34), (1225, 69), (160,160,160), 12)
    cv2.line(frame, (1225, 34), (1190, 69), (160,160,160), 12)
    cv2.circle(frame, (1210, 152), 26, (160,160,160), 4)
    cv2.rectangle(frame, (1199, 140), (1213, 164), (160,160,160), 3)
    cv2.arrowedLine(frame, (1225, 152), (1207, 152), (160,160,160), 3)
    return frame

from client_coordinator import ClientCoordinator
from client_input import NativeShortcutBackend
from client_session import SessionService


class ProductionMapCloseInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        coordinator_seams.CoordinatorTests.setUpClass()
        cls.app = coordinator_seams.CoordinatorTests.app

    def setUp(self):
        self.floor = floor_seams.FloorClickContracts()
        self.floor.setUp()
        self.floor.capture.frame = controls()
        self.floor.prepare()
        self.services = coordinator_seams.CoordinatorTests()
        self.services.setUp()
        self.native = Mock()
        self.native.is_window.return_value = True
        self.native.foreground_window.return_value = 42
        self.source = Source()
        self.keys = NativeShortcutBackend(self.native, source=self.source)
        self.keys.configure({"map": "G"})
        self.keys.bindWindow(42)
        self.keys.enable()
        self.session = SessionService(self.floor.runtime)
        self.services.settings.persistedValues.update(gameMode="nightmare",
            routeProfileId=self.floor.runtime.snapshot()["profileId"])
        self.services.settings.values = copy.deepcopy(self.services.settings.persistedValues)
        with patch("client_input.NativeWindowAPI", return_value=self.native):
            self.coordinator = ClientCoordinator(backend=self.services.backend, settings=self.services.settings,
                resources=self.services.resources, session=self.session, windows=self.services.windows,
                native_input=self.keys, capture=self.floor.capture, runtime=self.floor.runtime,
                images=self.services.images, window_catalog=Mock(return_value=[]))
        self.coordinator._bound = "42"
        self.coordinator._foreground_timer.stop()
        self.coordinator._tracking_timer.stop()
        self.keys._map_input.reset(True)
        self.pointer_sequence = 0

    def tearDown(self):
        self.coordinator._closed = True
        for timer in (self.coordinator._foreground_timer, self.coordinator._tracking_timer,
                self.coordinator._context_timer, self.coordinator._material_timer,
                self.coordinator._close_timer, self.coordinator._discovery_timer):
            timer.stop()
        self.keys.shutdown()
        self.session.stop()
        self.session._pool.waitForDone(3000)
        self.session.shutdown()
        self.floor.doCleanups()
        self.services.tearDown()
        self.app.processEvents()

    def settled(self):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            self.app.processEvents()
            if not self.session.busy and self.session._pool.activeThreadCount() == 0:
                self.app.processEvents()
                return
            time.sleep(.001)
        self.fail("session did not settle")

    def click(self, x, y, context=None):
        self.pointer_sequence += 1
        self.coordinator._game_pointer_event(dict(phase="down", gestureId=self.pointer_sequence,
            sequence=self.pointer_sequence, hwnd=42, x=x, y=y, createdAt=self.floor.now,
            context=context or self.floor.runtime.game_floor_click_context()))

    def test_navigation_keeps_map_intent_and_next_floor_works_through_coordinator(self):
        self.click(1310, 352)  # Physical screen coordinates; client origin is (100,200).
        self.assertTrue(self.floor.runtime.snapshot()["mapOpen"])
        self.assertTrue(self.keys._map_input.open_intent)
        self.floor.read["selectedFloor"] = 2
        self.click(*self.floor.xy(2))
        self.settled()
        self.assertEqual(self.session.snapshot["floor"], "2")
        self.assertTrue(self.session.snapshot["mapOpen"])
        self.assertTrue(self.keys._map_input.open_intent)

    def test_visible_x_closes_and_configured_map_key_reopens(self):
        old_context = self.floor.runtime.game_floor_click_context()
        traces = []
        self.coordinator.traceReported.connect(traces.append)
        self.click(1307, 252)
        close = next(row for row in traces if row["stage"] == "close-input")
        self.assertEqual(close["reason"], "mouse-close-control")
        self.assertEqual(close["cycle"], old_context["cycleId"])
        self.assertEqual(close["frame"], old_context["frameId"])
        self.assertEqual(close["diag"]["pointerSequence"], 1)
        self.assertFalse(self.floor.runtime.snapshot()["mapOpen"])
        self.assertFalse(self.keys._map_input.open_intent)
        self.settled()
        self.keys._dispatch_action("map", dict(createdAt=self.floor.now))
        self.settled()
        self.assertTrue(self.floor.runtime.snapshot()["mapOpen"])
        self.assertTrue(self.keys._map_input.open_intent)
        self.click(1307, 252, old_context)
        self.assertTrue(self.floor.runtime.snapshot()["mapOpen"])

    def test_current_visual_receipt_reconciles_intent_but_later_escape_wins(self):
        self.keys._map_input.reset(False)
        self.session.dispatch("refresh_map")
        self.settled()
        self.assertTrue(self.keys._map_input.open_intent)
        visual = self.session.snapshot["lastReceipt"]["mapInputObservation"]
        self.keys._dispatch_action("escape", dict(createdAt=self.floor.now))
        self.assertFalse(self.keys._map_input.open_intent)
        self.settled()
        self.assertFalse(self.floor.runtime.map_input_observation_is_current(visual))
        self.assertFalse(self.keys._map_input.open_intent)


if __name__ == "__main__":
    unittest.main()
