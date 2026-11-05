"""Late ready map frames recover through the real passive Qt/Session path."""
from contextlib import contextmanager
import time
import threading
import unittest
from unittest.mock import patch

import test_client_drag_probe_presentation as ingress


class MapOpenRecoveryContracts(unittest.TestCase):
    mode = "nightmare"
    dispatch = ingress.DragProbePresentationContracts.dispatch
    _stop_timers = ingress.DragProbePresentationContracts._stop_timers
    _cleanup_input_session = ingress.DragProbePresentationContracts._cleanup_input_session

    def setUp(self):
        ingress.DragProbePresentationContracts.setUp(self)
        self.now = 1.
        self.runtime._monotonic = lambda: self.now
        self.dispatch("end_round")
        self.dispatch("start_round")
        self.session.refreshRuntime()
        self.app.processEvents()
        self.traces.clear()

    def settle(self):
        deadline = time.monotonic() + 4
        while (self.session.busy or self.session._pool.activeThreadCount()) and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.001)
        self.app.processEvents()
        self.assertFalse(self.session.busy)
        self.assertEqual(self.session._pool.activeThreadCount(), 0)

    @contextmanager
    def not_ready(self):
        with patch("map_interface_observation.detect_map_interface", return_value=dict(detected=False)), \
                patch("floor_control_region.locate_vertical_floor_controls", return_value=dict(selectedFloor=None)):
            yield

    def miss_initial_preparation(self):
        with self.not_ready():
            self.keys._dispatch_action("map", dict(createdAt=1.))
            self.settle()
        self.assertEqual(self.session.snapshot["lastReceipt"]["reason"], "map-open-preparation-deadline")
        self.assertFalse(self.runtime.snapshot()["displayAuthorized"])
        self.assertFalse(self.coordinator.alignedData)

    def tick(self, elapsed=.3):
        self.now += elapsed
        self.coordinator._tracking_timer.timeout.emit()
        self.settle()

    def test_ready_after_deadline_recovers_without_another_input_edge(self):
        self.miss_initial_preparation()
        sequence = self.coordinator._map_input_sequence
        before = self.runtime.snapshot()
        self.assertTrue(before.get("mapRecoveryPending"), "deadline was treated as confirmed close; no observation can resume")
        self.assertFalse(before["mapOpen"])
        self.tick()
        state = self.runtime.snapshot()
        self.assertTrue(state["mapOpen"])
        self.assertTrue(state["displayAuthorized"])
        self.assertTrue(self.coordinator.alignedData)
        self.assertFalse(state.get("mapRecoveryPending"))
        self.assertEqual(self.coordinator._map_input_sequence, sequence)
        self.assertFalse(any(row.get("stage") == "render-revoked" and row.get("reason") == "map-closed"
            for row in self.traces))

    def test_unknown_recovery_is_finite_and_never_publishes_negative_frames(self):
        self.miss_initial_preparation()
        self.assertTrue(self.runtime.snapshot().get("mapRecoveryPending"))
        calls = []
        original = self.backend.capture
        def capture(*args):
            calls.append(1)
            return original(*args)
        with self.not_ready(), patch.object(self.backend, "capture", side_effect=capture):
            for _ in range(30):
                self.tick()
            settled_count = len(calls)
            for _ in range(10):
                self.tick()
        self.assertGreater(settled_count, 0)
        self.assertLessEqual(settled_count, 10)
        self.assertEqual(len(calls), settled_count)
        self.assertFalse(self.runtime.snapshot().get("mapRecoveryPending"))
        self.assertFalse(self.coordinator.alignedData)
        self.assertFalse(any(row.get("stage") == "publish" for row in self.traces))

    def test_explicit_close_cancels_unknown_recovery(self):
        self.miss_initial_preparation()
        self.assertTrue(self.runtime.snapshot().get("mapRecoveryPending"))
        self.keys._dispatch_action("escape", dict(createdAt=1.))
        self.settle()
        self.tick()
        self.assertFalse(self.runtime.snapshot().get("mapRecoveryPending"))
        self.assertFalse(self.runtime.snapshot()["mapOpen"])
        self.assertFalse(self.coordinator.alignedData)

    def test_focus_loss_cancels_unknown_recovery_without_input(self):
        self.miss_initial_preparation()
        self.assertTrue(self.runtime.snapshot().get("mapRecoveryPending"))
        self.native.foreground_window = lambda: 99
        self.coordinator._watch_foreground()
        self.tick()
        self.assertFalse(self.runtime.snapshot().get("mapRecoveryPending"))
        self.assertFalse(self.runtime.snapshot()["displayAuthorized"])
        self.assertFalse(self.coordinator.alignedData)

    def test_close_during_running_recovery_cannot_publish_late_frame(self):
        self.miss_initial_preparation()
        entered, release = threading.Event(), threading.Event()
        original = self.backend.capture
        def blocked(*args):
            entered.set()
            if not release.wait(3):
                raise AssertionError("recovery test barrier timed out")
            return original(*args)
        with patch.object(self.backend, "capture", side_effect=blocked):
            self.now += .3
            self.coordinator._tracking_timer.timeout.emit()
            self.assertTrue(entered.wait(2))
            self.keys._dispatch_action("escape", dict(createdAt=1.))
            release.set()
            self.settle()
        self.assertFalse(self.runtime.snapshot().get("mapRecoveryPending"))
        self.assertFalse(self.runtime.snapshot()["mapOpen"])
        self.assertFalse(self.coordinator.alignedData)
        self.assertGreaterEqual(self.session.stale_results, 1)

    def test_session_stop_does_not_rearm_recovery_on_next_tick(self):
        self.miss_initial_preparation()
        self.session.stop()
        with patch.object(self.backend, "capture", side_effect=AssertionError("stopped recovery captured")):
            self.tick()
        self.assertFalse(self.runtime.snapshot().get("mapRecoveryPending"))
        self.assertFalse(self.coordinator.alignedData)

    def test_new_input_blocked_by_resources_still_retires_old_recovery(self):
        self.miss_initial_preparation()
        old_sequence = self.runtime._last_map_input_sequence
        self.ui.resources.busy = True
        self.coordinator._map_input_requested(dict(sequence=old_sequence + 1, openIntent=True,
            createdAt=1., hwnd=42, source="hotkey"))
        self.assertEqual(self.runtime._last_map_input_sequence, old_sequence)
        self.ui.resources.busy = False
        with patch.object(self.backend, "capture", side_effect=AssertionError("superseded recovery captured")):
            self.tick()
        self.assertFalse(self.runtime.snapshot().get("mapRecoveryPending"))

    def test_capture_binding_generation_change_stops_recovery(self):
        self.miss_initial_preparation()
        self.capture.disable()
        self.capture.enable()
        with patch.object(self.backend, "capture", side_effect=AssertionError("old binding recovery captured")):
            self.tick()
        self.assertFalse(self.runtime.snapshot().get("mapRecoveryPending"))
        self.assertFalse(self.coordinator.alignedData)

    def test_round_end_stops_recovery_and_no_old_ticket_enters_next_round(self):
        self.miss_initial_preparation()
        old_round = self.runtime.snapshot()["roundId"]
        self.session.dispatch("end_round")
        self.settle()
        self.session.dispatch("start_round")
        self.settle()
        self.assertGreater(self.runtime.snapshot()["roundId"], old_round)
        with patch.object(self.backend, "capture", side_effect=AssertionError("old round recovery captured")):
            self.tick()
        self.assertFalse(self.runtime.snapshot().get("mapRecoveryPending"))

    def test_same_profile_scope_change_or_stale_input_cannot_transfer_ticket(self):
        self.miss_initial_preparation()
        old_id = self.runtime.map_open_recovery_status()["id"]
        stale = self.dispatch("map", inputSequence=self.runtime._last_map_input_sequence, openIntent=True)
        self.assertTrue(stale["stale"])
        self.assertEqual(self.runtime.map_open_recovery_status()["id"], old_id)
        self.dispatch("set_context", mode=self.mode, profileId="changed-author")
        with patch.object(self.backend, "capture", side_effect=AssertionError("old author recovery captured")):
            self.tick()
        self.assertFalse(self.runtime.snapshot().get("mapRecoveryPending"))


class DifficultMapOpenRecoveryContracts(MapOpenRecoveryContracts):
    mode = "difficult"


if __name__ == "__main__":
    unittest.main()
