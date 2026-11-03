"""PR84 + PR86: foreground return consumes one fresh, floor-ready frame.

The coordinator, SessionService worker, bounded preparation, floor policy and
authorization core are real. Native focus/capture pixels, pixel sensors,
resources and geometry solving are synthetic fixtures, not live-game evidence.
Nightmare uses its existing triplet helper; difficult uses its own two-button
mapOpen/floor contract. No readiness threshold or timeout is patched.
"""
import copy
from contextlib import contextmanager
import threading
import time
import unittest
from unittest.mock import patch

from client_live_runtime import _AlignmentHost
import test_client_recapture_focus as focus_fixtures


class FloorReadyForegroundReturnTests(unittest.TestCase):
    @contextmanager
    def fixture(self, mode="nightmare"):
        case = focus_fixtures.RecaptureFocusTests(
            "test_foreground_return_retries_unobserved_frames_before_fresh_authorization")
        case.setUp()
        try:
            if mode == "difficult":
                case.native.foreground = 42
                case.live.dispatch("end_round")
                case.live.dispatch("set_context", mode=mode)
                case.live.dispatch("start_round")
                self.assertTrue(case.live.dispatch("capture_now")["success"])
                case.session.refreshRuntime()
                case.coordinator._applied_context = (mode, "shared-structure")
                case.fixture.settings.values["mode"] = mode
                case.native.foreground = 77
            yield case
        finally:
            case.doCleanups()

    @staticmethod
    def nightmare_ready(floor=1, *, margin=8., backdrop=.80):
        return (dict(detected=False, reason="map-canvas-texture-mismatch",
                     shapeAccepted=True, layoutFamily="wide", matchedAnchors=4,
                     neutralMatchedAnchors=4, backdropRatio=backdrop),
                dict(selectedFloor=floor, layout="localized-triplet", margin=margin,
                     centers=[.45, .50, .55], scores={str(floor): 40.}))

    @contextmanager
    def returning_frames(self, case, floor=1, *, margin=8., backdrop=.80):
        """The first capture is unobserved; only capture two has ready proof."""
        original_capture = case.backend.capture
        captured_markers = []

        def capture(*args, **kwargs):
            frame = original_capture(*args, **kwargs)
            marker = len(captured_markers) + 1
            frame[:, :, 2] = marker
            captured_markers.append(marker)
            return frame

        if case.runtime.snapshot()["mode"] == "nightmare":
            observation, floor_read = self.nightmare_ready(
                floor, margin=margin, backdrop=backdrop)
            with patch.object(case.backend, "capture", side_effect=capture), \
                    patch("map_interface_observation.detect_selected_floor", side_effect=[
                        dict(selectedFloor=None), floor_read]), \
                    patch("map_interface_observation.detect_map_interface", side_effect=[
                        dict(detected=False), observation]):
                yield captured_markers
        else:
            # Difficult has no B1 or nightmare localized-triplet escape hatch.
            with patch.object(case.backend, "capture", side_effect=capture), \
                    patch.object(case.runtime._engine.qt_map_ui, "observe", side_effect=[
                        dict(mapOpen=False, floor=None),
                        dict(mapOpen=True, floor=str(floor))]):
                yield captured_markers

    def assert_fresh_authorization(self, case, old_token, floor, captured_markers):
        receipt = case.session.snapshot["lastReceipt"]
        self.assertTrue(receipt.get("success"), receipt)
        self.assertEqual(receipt["action"], "capture_now")
        self.assertEqual(captured_markers, [1, 2])
        state = case.runtime.snapshot()
        self.assertEqual(state["floor"], "B1" if floor == -1 else str(floor))
        self.assertTrue(state["displayAuthorized"])
        self.assertNotEqual(state["pendingRenderToken"], old_token)
        self.assertIsNone(case.runtime.render_payload(old_token))
        self.assertEqual(int(case.runtime._last_observation["frame"][0, 0, 2]), 2)
        self.assertEqual(case.runtime._last_observation["floor_read"]["selectedFloor"], floor)
        self.assertIsNotNone(case.runtime._core.display_authorization)
        case.coordinator._watch_foreground()
        case.settle()
        self.assertEqual(captured_markers, [1, 2], "return must enqueue only once")

    def test_nightmare_ready_with_generic_false_reaches_fresh_floor_and_pose(self):
        for floor in (-1, 1, 2):
            for margin, backdrop in ((8., .80), (12., .72)):
                with self.subTest(floor=floor, margin=margin), self.fixture() as case:
                    old = case.runtime.snapshot()["pendingRenderToken"]
                    requests = []
                    case.session.actionRequested.connect(lambda action, data: requests.append((action, data)))
                    with self.returning_frames(case, floor, margin=margin, backdrop=backdrop) as markers, \
                            patch.object(case.runtime, "_observe_floor_policy",
                                         wraps=case.runtime._observe_floor_policy) as policy:
                        case.return_to_game()
                        case.settle()
                        self.assert_fresh_authorization(case, old, floor, markers)
                    self.assertEqual(len(requests), 1)
                    self.assertEqual(requests[0][0], "capture_now")
                    self.assertIs(requests[0][1]["foregroundReturn"], True)
                    policy.assert_called_once()
                    self.assertEqual(policy.call_args.args[0]["selectedFloor"], floor)

    def test_difficult_return_keeps_its_two_button_floor_contract(self):
        for floor in (1, 2):
            with self.subTest(floor=floor), self.fixture("difficult") as case:
                old = case.runtime.snapshot()["pendingRenderToken"]
                with self.returning_frames(case, floor) as markers:
                    case.return_to_game()
                    case.settle()
                    self.assert_fresh_authorization(case, old, floor, markers)
                self.assertEqual(case.runtime._last_observation["floor_read"]["layout"],
                                 "difficult-two-buttons")
                self.assertNotIn("B1", case.runtime._engine.identity_floors)

    def test_ready_still_requires_current_frame_geometry_in_both_modes(self):
        for mode in ("nightmare", "difficult"):
            with self.subTest(mode=mode), self.fixture(mode) as case:
                old = case.runtime.snapshot()["pendingRenderToken"]
                with self.returning_frames(case, 2) as markers, \
                        patch.object(_AlignmentHost, "fit", return_value=dict(
                            success=False, reason="fixture-current-geometry-rejected")) as fit:
                    case.return_to_game()
                    case.settle()
                receipt = case.session.snapshot["lastReceipt"]
                self.assertEqual(markers, [1, 2])
                fit.assert_called_once()
                self.assertEqual(int(fit.call_args.args[0][0, 0, 2]), 2)
                self.assertFalse(receipt["success"], receipt)
                self.assertEqual(receipt["reason"], "fixture-current-geometry-rejected")
                self.assertIsNone(case.runtime.render_payload(old))
                self.assertFalse(case.runtime.snapshot()["displayAuthorized"])
                self.assertIsNone(case.runtime._render)

    def interrupt_after_ready(self, case, interrupt):
        """Pause after real preparation returns capture two, before policy/fit."""
        entered, release = threading.Event(), threading.Event()
        original_full_frame = case.runtime._full_frame
        calls = 0

        def full_frame(captured):
            nonlocal calls
            frame = original_full_frame(captured)
            calls += 1
            if calls == 3:  # Two full probes, then the exact prepared frame.
                entered.set()
                if not release.wait(5):
                    raise AssertionError("fixture interruption was not released")
            return frame

        old = case.runtime.snapshot()["pendingRenderToken"]
        with self.returning_frames(case, 2) as markers, \
                patch.object(case.runtime, "_full_frame", side_effect=full_frame), \
                patch.object(case.runtime, "_observe_floor_policy",
                             wraps=case.runtime._observe_floor_policy) as policy, \
                patch.object(_AlignmentHost, "fit", side_effect=lambda *args:
                             copy.deepcopy(case.live.fit)) as fit:
            try:
                case.return_to_game()
                self.assertTrue(entered.wait(3), "ready frame never left real preparation")
                interrupt()
            finally:
                release.set()
                # Cancellation clears busy before the obsolete worker drains.
                deadline = time.monotonic() + 5
                while case.session._jobs and time.monotonic() < deadline:
                    focus_fixtures.APP.processEvents()
                    time.sleep(.002)
            self.assertFalse(case.session._jobs)
            self.assertEqual(markers, [1, 2])
            fit.assert_not_called()
            if case.runtime.snapshot()["mode"] == "nightmare":
                policy.assert_not_called()
        self.assertIsNone(case.runtime.render_payload(old))
        self.assertFalse(case.runtime.snapshot()["displayAuthorized"])
        self.assertIsNone(case.runtime._render)

    def test_cancel_after_prepared_frame_never_authorizes_either_mode(self):
        for mode in ("nightmare", "difficult"):
            with self.subTest(mode=mode), self.fixture(mode) as case:
                self.interrupt_after_ready(case, lambda: case.session.cancelPending("fixture-cancel"))
                self.assertGreater(case.session.stale_results, 0)
                self.assertEqual(case.session.snapshot["lastReceipt"], {})

    def test_capture_generation_change_after_ready_never_authorizes_either_mode(self):
        for mode in ("nightmare", "difficult"):
            with self.subTest(mode=mode), self.fixture(mode) as case:
                self.interrupt_after_ready(case, lambda: case.capture.bindFullClient(42))
                receipt = case.session.snapshot["lastReceipt"]
                self.assertFalse(receipt.get("success"), receipt)
                self.assertTrue(receipt.get("stale"), receipt)

    def test_close_or_end_after_ready_never_reopens_either_mode(self):
        for mode in ("nightmare", "difficult"):
            for action in ("close_map", "end_round"):
                with self.subTest(mode=mode, action=action), self.fixture(mode) as case:
                    self.interrupt_after_ready(case, lambda: case.session.dispatch(action))
                    self.assertFalse(case.runtime.snapshot()["mapOpen"])
                    if action == "end_round":
                        self.assertFalse(case.runtime.snapshot()["roundActive"])

    def test_unobserved_return_times_out_without_resetting_physical_map_intent(self):
        for mode in ("nightmare", "difficult"):
            with self.subTest(mode=mode), self.fixture(mode) as case:
                old = case.runtime.snapshot()["pendingRenderToken"]
                events = []
                original_notify = case.runtime._notify

                def notify(name, **values):
                    events.append((name, values))
                    return original_notify(name, **values)

                sensor = (patch("map_interface_observation.detect_map_interface",
                                return_value=dict(detected=False)) if mode == "nightmare"
                          else patch.object(case.runtime._engine.qt_map_ui, "observe",
                                            return_value=dict(mapOpen=False, floor=None)))
                before = len(case.backend.captures)
                with sensor, patch("map_interface_observation.detect_selected_floor",
                                   return_value=dict(selectedFloor=None)), \
                        patch.object(case.runtime, "_notify", side_effect=notify), \
                        patch.object(_AlignmentHost, "fit") as fit:
                    case.return_to_game()
                    case.settle()
                receipt = case.session.snapshot["lastReceipt"]
                self.assertEqual(receipt["reason"], "map-open-preparation-deadline")
                self.assertFalse(receipt["success"])
                self.assertGreater(len(case.backend.captures) - before, 1)
                self.assertLessEqual(len(case.backend.captures) - before, 21)
                fit.assert_not_called()
                self.assertFalse(any(values.get("mapClosed") for _, values in events))
                case.fixture.input.resetMapInput.assert_called_with(True)
                self.assertIsNone(case.runtime.render_payload(old))
                self.assertFalse(case.runtime.snapshot()["displayAuthorized"])

    def test_helper_floor_and_backdrop_must_belong_to_same_return_frame(self):
        with self.fixture() as case:
            observation, floor_read = self.nightmare_ready(2)
            # Neither frame alone is ready: first has a selector without map
            # backdrop, later frames have backdrop without a selected floor.
            observations = [dict(detected=False), observation]
            floors = [floor_read, dict(selectedFloor=None)]

            def pop_or_last(rows):
                return rows.pop(0) if len(rows) > 1 else rows[0]

            with patch("map_interface_observation.detect_map_interface",
                       side_effect=lambda *a, **kw: pop_or_last(observations)), \
                    patch("map_interface_observation.detect_selected_floor",
                          side_effect=lambda *a, **kw: pop_or_last(floors)), \
                    patch.object(case.runtime, "_observe_floor_policy",
                                 wraps=case.runtime._observe_floor_policy) as policy, \
                    patch.object(_AlignmentHost, "fit") as fit:
                case.return_to_game()
                case.settle()
            self.assertEqual(case.session.snapshot["lastReceipt"]["reason"],
                             "map-open-preparation-deadline")
            policy.assert_not_called()
            fit.assert_not_called()
            self.assertFalse(case.runtime.snapshot()["displayAuthorized"])


if __name__ == "__main__":
    unittest.main()
