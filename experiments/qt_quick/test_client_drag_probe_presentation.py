"""Mouse-up observation must not blank a still-authorized drag presentation.

The actual source mailbox, Qt queued input, coordinator, Session QRunnable,
runtime/core guards and final alignedData/window calls run here. Native hooks,
OS capture pixels, solver measurements, DIP projection and window/image sinks
are fixtures; this is not a game/GUI acceptance test. Holding pool.start only
controls the worker schedule, without substituting a receipt or guard result.
"""
from copy import deepcopy
import time
import unittest
from unittest.mock import Mock, patch

import numpy as np

from client_coordinator import ClientCoordinator
from client_input import NativeShortcutBackend
from client_session import SessionService
import test8f8b_stale_presentation as presentation_fixtures
from test_client_game_floor_input import Source
from test_field_map_close_input import controls


class DragProbePresentationContracts(unittest.TestCase):
    mode = "nightmare"

    def setUp(self):
        # Reuse the real ClientCaptureProvider and live runtime/core guards.
        presentation_fixtures.StalePresentationContracts.setUp(self)
        self.backend.capture = lambda hwnd, region: controls()
        self.assertTrue(self.dispatch("capture_now")["success"])
        self.coordinator._closed = True
        self.source = Source()  # Only start/stop are replaced: no native hook.
        self.keys = NativeShortcutBackend(self.native, source=self.source)
        self.keys.configure({"map": "G"})
        self.keys.bindWindow(42)
        self.keys.enable()
        self.session = SessionService(self.runtime)
        self.ui.settings.persistedValues.update(gameMode=self.mode,
            routeProfileId=self.runtime.snapshot()["profileId"])
        self.ui.settings.values = deepcopy(self.ui.settings.persistedValues)
        with patch("client_input.NativeWindowAPI", return_value=self.native):
            self.coordinator = ClientCoordinator(backend=self.ui.backend,
                settings=self.ui.settings, resources=self.ui.resources,
                session=self.session, windows=self.ui.windows, native_input=self.keys,
                capture=self.capture, runtime=self.runtime, images=self.ui.images,
                window_catalog=Mock(return_value=[]))
        self.coordinator._bound = "42"
        self._stop_timers()
        geometry = patch.object(self.coordinator, "_convert_client_geometry",
            side_effect=lambda row: row.update(deviceScale=1.0))
        geometry.start()
        self.addCleanup(geometry.stop)
        self.app = self.ui.app
        self.traces, self.consumer_tokens = [], []
        self.coordinator.traceReported.connect(self.traces.append)
        self.coordinator.changed.connect(lambda: self.consumer_tokens.append(
            self.coordinator.alignedData.get("token")))
        self.coordinator._present_runtime_state(self.runtime.snapshot(), trigger="initial")
        self.app.processEvents()
        self.addCleanup(self._cleanup_input_session)

    def dispatch(self, action, **payload):
        return presentation_fixtures.StalePresentationContracts.dispatch(self, action, **payload)

    def _stop_timers(self):
        for name in ("_foreground_timer", "_tracking_timer", "_context_timer",
                "_material_timer", "_close_timer", "_discovery_timer", "_startup_timer"):
            timer = getattr(self.coordinator, name, None)
            if timer is not None:
                timer.stop()

    def _cleanup_input_session(self):
        self.coordinator._closed = True
        self._stop_timers()
        self.keys.shutdown()
        self.session.stop()
        self.session._pool.waitForDone(3000)
        self.session.shutdown()
        self.app.processEvents()

    def pointer(self, phase, x, y):
        self.source._receive_pointer(x, y, 42, phase=phase, created_at=1.)
        self.app.processEvents()

    def queued_probe_after_gesture(self, start=(500, 500), end=(540, 520)):
        self.pointer("down", *start)
        self.assertIsNotNone(self.coordinator._pointer_drag)
        self.pointer("move", end[0] - 10, end[1] - 5)
        pending = []
        with patch.object(self.session._pool, "start", side_effect=pending.append):
            self.pointer("up", *end)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].action, "probe_map_after_drag")
        self.assertTrue(self.session.busy)
        self.assertIsNone(self.coordinator._pointer_drag)
        return pending[0]

    def finish_worker(self, work):
        self.session._pool.start(work)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            self.app.processEvents()
            if not self.session.busy and self.session._pool.activeThreadCount() == 0:
                self.app.processEvents()
                return
            time.sleep(.001)
        self.fail("post-drag worker did not settle")

    def assert_withdrawn(self, token):
        self.assertIsNone(self.runtime.render_payload(token))
        self.assertFalse(self.runtime.snapshot()["core"]["displayAuthorized"])
        self.assertFalse(self.coordinator.alignedData)
        self.assertTrue(any(call.args == ("aligned",)
            for call in self.ui.windows.hide.call_args_list))

    def test_full_gesture_keeps_authorized_consumer_while_probe_queued_and_completed(self):
        before = np.array(self.coordinator.alignedData["matrix"])
        self.consumer_tokens.clear()
        self.traces.clear()
        self.ui.windows.hide.reset_mock()
        work = self.queued_probe_after_gesture()
        token = self.runtime.snapshot()["render"]["token"]
        self.assertIsNotNone(self.runtime.render_payload(token))
        self.assertEqual(self.coordinator.alignedData.get("token"), token,
            "mouse-up queued an observation but cleared its current authorized display")
        np.testing.assert_allclose(np.array(self.coordinator.alignedData["matrix"])[:2, 2]
            - before[:2, 2], [40, 20])
        self.finish_worker(work)
        receipt = self.session.snapshot["lastReceipt"]
        self.assertEqual(receipt["reason"], "post-drag-observation-current")
        self.assertTrue(receipt["success"])
        self.assertEqual(receipt["poseCommits"], 0)
        self.assertEqual(self.coordinator.alignedData.get("token"), token)
        self.assertTrue(self.consumer_tokens)
        self.assertNotIn(None, self.consumer_tokens)
        self.assertFalse(any(row["stage"] == "clear" for row in self.traces))
        self.assertFalse(any(call.args == ("aligned",)
            for call in self.ui.windows.hide.call_args_list))

    def test_two_negative_post_drag_observations_still_revoke_display(self):
        token = self.runtime.snapshot()["render"]["token"]
        work = self.queued_probe_after_gesture()
        # Independent current-map evidence is negative in both modes;
        # the production two-miss policy and final presentation path still run.
        with patch("map_interface_observation.detect_map_interface", return_value={"detected": False}):
            self.finish_worker(work)
            self.assertEqual(self.session.snapshot["lastReceipt"]["reason"], "map-not-observed")
            self.assertTrue(self.runtime.snapshot()["mapOpen"])
            # Missing current map evidence withdraws pixels immediately; only
            # the second passive miss confirms a physical close.
            self.assert_withdrawn(token)
            pending = []
            with patch.object(self.session._pool, "start", side_effect=pending.append):
                self.session.dispatch("refresh_map")
            self.assertEqual(len(pending), 1)
            self.finish_worker(pending[0])
        self.assertFalse(self.runtime.snapshot()["mapOpen"])
        self.assert_withdrawn(token)

    def test_foreground_loss_while_probe_queued_revokes_and_late_worker_cannot_restore(self):
        token = self.runtime.snapshot()["render"]["token"]
        work = self.queued_probe_after_gesture()
        self.ui.windows.hide.reset_mock()
        self.native.foreground_window = lambda: 99
        self.coordinator._watch_foreground()
        self.assert_withdrawn(token)
        self.finish_worker(work)
        self.assert_withdrawn(token)
        self.assertGreaterEqual(self.session.stale_results, 1)

    def test_map_close_before_queued_success_delivery_cannot_restore(self):
        token = self.runtime.snapshot()["render"]["token"]
        work = self.queued_probe_after_gesture()
        # A real successful worker result is now queued but not UI-consumed.
        work.run()
        self.ui.windows.hide.reset_mock()
        self.keys._dispatch_action("escape", {"createdAt": 1.})
        self.assert_withdrawn(token)
        self.app.processEvents()
        self.assert_withdrawn(token)
        self.assertFalse(self.runtime.snapshot()["mapOpen"])
        self.assertGreaterEqual(self.session.stale_results, 1)

    def test_round_end_before_queued_success_delivery_cannot_restore(self):
        token = self.runtime.snapshot()["render"]["token"]
        work = self.queued_probe_after_gesture()
        work.run()
        self.ui.windows.hide.reset_mock()
        self.session.dispatch("end_round")
        self.app.processEvents()
        deadline = time.monotonic() + 3
        while self.session.busy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.001)
        self.assertFalse(self.session.busy)
        self.assertFalse(self.runtime.snapshot()["roundActive"])
        self.assert_withdrawn(token)
        self.assertGreaterEqual(self.session.stale_results, 1)


class DifficultDragProbePresentationContracts(DragProbePresentationContracts):
    mode = "difficult"


if __name__ == "__main__":
    unittest.main()
