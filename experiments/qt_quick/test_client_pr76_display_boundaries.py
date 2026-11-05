"""Cross-version seams on real Qt workers/core; native IO/fit/pixels are fixtures."""
from contextlib import contextmanager
from copy import deepcopy
import threading
import time
import unittest
from test_s0_vertical_controls import synthetic_read
from unittest.mock import Mock, patch

from client_live_runtime import _AlignmentHost
from client_preparation_diagnostics import capture_capability, fallback_trigger
import test_client_drag_probe_presentation as ingress
import test_client_drag_group_choice as groups
from test_client_floor_ready_foreground_return import FloorReadyForegroundReturnTests


class RuntimeBoundaries(unittest.TestCase):
    mode = "nightmare"
    setUp = ingress.DragProbePresentationContracts.setUp
    dispatch = ingress.DragProbePresentationContracts.dispatch
    pointer = ingress.DragProbePresentationContracts.pointer
    _stop_timers = ingress.DragProbePresentationContracts._stop_timers
    _cleanup_input_session = ingress.DragProbePresentationContracts._cleanup_input_session

    def settle(self):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            self.app.processEvents()
            if not self.session.busy and self.session._pool.activeThreadCount() == 0:
                self.app.processEvents()
                return
            time.sleep(.001)
        self.fail("fixture worker did not settle")

    @contextmanager
    def negative(self):
        with patch("map_interface_observation.detect_map_interface", return_value=dict(detected=False)), \
                patch("floor_control_region.locate_vertical_floor_controls", return_value=dict(selectedFloor=None)):
            yield

    def refresh_tail(self, *, fail, drag):
        entered, release = threading.Event(), threading.Event()
        original = self.runtime._capture_and_solve
        published = []
        def tail(*args, **kwargs):
            result = original(*args, **kwargs)
            self.assertTrue(result["success"], result)
            published.append(self.runtime._refresh_render_owner())
            entered.set()
            if not release.wait(3):
                raise AssertionError("tail release missing")
            if fail:
                raise ValueError("controlled refresh tail failure")
            return result
        with patch.object(self.runtime, "_capture_and_solve", side_effect=tail):
            try:
                self.session.dispatch("refresh_map")
                self.assertTrue(entered.wait(2))
                self.app.processEvents()
                token = self.runtime.snapshot()["render"]["token"]
                self.assertIsNotNone(self.runtime.render_payload(token))
                if drag:
                    self.pointer("down", 500, 500)
                    self.pointer("move", 530, 515)
                    self.assertIsNotNone(self.coordinator._pointer_drag)
                    self.assertNotEqual(self.runtime._refresh_render_owner(), published[0])
                else:
                    self.session.stop()
            finally:
                release.set()
                self.settle()
        if drag:
            self.assertIsNotNone(self.runtime.render_payload(token))
            self.assertTrue(self.runtime.snapshot()["displayAuthorized"])
            self.assertEqual(self.coordinator.alignedData.get("token"), token)
        else:
            self.assertIsNone(self.runtime.render_payload(token))
            self.assertFalse(self.runtime.snapshot()["displayAuthorized"])
            self.assertFalse(self.coordinator.alignedData)

    def test_cancel_own_published_refresh_success_tail_withdraws_its_new_grant(self):
        self.refresh_tail(fail=False, drag=False)

    def test_cancel_own_published_refresh_exception_tail_withdraws_its_new_grant(self):
        self.refresh_tail(fail=True, drag=False)

    def test_cancel_refresh_exception_tail_preserves_new_drag_revision(self):
        self.refresh_tail(fail=True, drag=True)

    def assert_current_fallback_exception(self, boundary):
        original_start = self.session._pool.start
        workers, failures, checkpoints = [], [], []
        self.coordinator.errorReported.connect(failures.append)
        epoch = self.runtime._epoch
        initial_owner = self.runtime._refresh_render_owner()
        self.assertIsNotNone(initial_owner)
        def start(work):
            workers.append(work)
            return original_start(work)
        def fail(*args, **kwargs):
            # The cheap residual path already withdrew the old pixels. This
            # is the current worker's fallback, with no newer drag or cancel.
            checkpoints.append(dict(owner=self.runtime._refresh_render_owner(),
                epoch=self.runtime._epoch, cancelled=workers[0].cancelled.is_set()))
            raise ValueError("controlled current fallback " + boundary + " failure")
        target = (patch.object(_AlignmentHost, "fit", side_effect=fail) if boundary == "fit"
            else patch.object(self.runtime, "_display_layer", side_effect=fail))
        with patch.object(self.session._pool, "start", side_effect=start), \
                patch.object(_AlignmentHost, "_canonical_geometry_current_frame_residual",
                    return_value=(dict(verified=False), None)) as residual, target as failed:
            self.session.dispatch("refresh_map")
            self.settle()
        self.assertEqual(residual.call_count, 1)
        self.assertEqual(failed.call_count, 1)
        self.assertEqual(checkpoints, [dict(owner=None, epoch=epoch, cancelled=False)])
        receipt = self.session.snapshot["lastReceipt"]
        self.assertEqual(receipt["action"], "refresh_map")
        self.assertFalse(receipt["success"])
        self.assertTrue(receipt.get("error"), receipt)
        self.assertFalse(receipt.get("stale"), receipt)
        self.assertEqual(receipt.get("exceptionType"), "ValueError")
        self.assertEqual(receipt.get("exceptionOrigin"), "runtime-dispatch")
        self.assertIn("controlled current fallback " + boundary + " failure", receipt["traceback"])
        self.assertTrue(any(row.get("action") == "refresh_map" and
            row.get("exceptionType") == "ValueError" for row in failures))
        self.assertIsNone(self.runtime._refresh_render_owner())
        self.assertFalse(self.runtime.snapshot()["displayAuthorized"])
        self.assertFalse(self.coordinator.alignedData)

    def test_current_fallback_fit_exception_after_own_revoke_keeps_error_diagnostics(self):
        self.assert_current_fallback_exception("fit")

    def test_current_fallback_display_exception_after_own_revoke_keeps_error_diagnostics(self):
        self.assert_current_fallback_exception("display")

    def test_old_capture_exception_after_new_drag_is_stale_and_keeps_current_pixels(self):
        entered, release = threading.Event(), threading.Event()
        original_capture, original_start = self.runtime._capture_frame, self.session._pool.start
        workers, completions, failures = [], [], []
        self.coordinator.errorReported.connect(failures.append)
        def start(work):
            workers.append(work)
            work.signals.done.connect(lambda *args: completions.append(args))
            return original_start(work)
        def delayed_failure(cancelled, **kwargs):
            original_capture(cancelled, **kwargs)  # Real provider, controlled native/pixels.
            entered.set()
            if not release.wait(3):
                raise AssertionError("capture failure release missing")
            raise ValueError("controlled obsolete capture tail failure")
        with patch.object(self.session._pool, "start", side_effect=start), \
                patch.object(self.runtime, "_capture_frame", side_effect=delayed_failure):
            try:
                self.session.dispatch("refresh_map")
                self.assertTrue(entered.wait(2))
                old_owner = self.runtime._refresh_render_owner()
                self.pointer("down", 500, 500)
                self.pointer("move", 530, 515)
                self.assertTrue(workers[0].cancelled.is_set())
                current_owner = self.runtime._refresh_render_owner()
                self.assertNotEqual(current_owner, old_owner)
                token = self.runtime.snapshot()["render"]["token"]
            finally:
                release.set()
                self.settle()
        receipt = completions[0][2][0]
        self.assertTrue(receipt.get("stale"), receipt)
        self.assertFalse(receipt.get("error"), receipt)
        self.assertEqual(self.runtime._refresh_render_owner(), current_owner)
        self.assertIsNotNone(self.runtime.render_payload(token))
        self.assertEqual(self.coordinator.alignedData.get("token"), token)
        self.assertTrue(self.runtime.snapshot()["displayAuthorized"])
        self.assertFalse(failures)

    def test_recovery_retains_budget_while_main_floor_policy_defers_conflicting_sample(self):
        self.runtime._monotonic = lambda: self.now
        self.now = 1.
        self.runtime._floor = ""
        self.runtime._floor_observations = []
        self.assertTrue(self.runtime._arm_map_open_recovery(None, self.runtime._epoch))
        self.runtime._game_floor_intent = dict(awaitingObservation=True,
            cycleId=self.runtime._cycle + 1, floor=2, expiresAt=5.)
        ticket = self.runtime.map_open_recovery_status()["id"]
        self.now += .3
        first = self.dispatch("recover_map_open", recoveryId=ticket)
        self.assertEqual(first["reason"], "floor-policy-deferred")
        self.assertTrue(self.runtime.snapshot()["mapRecoveryPending"])
        self.assertFalse(self.runtime.snapshot()["displayAuthorized"])
        self.now += .3
        with patch("floor_control_region.locate_vertical_floor_controls",
                          side_effect=lambda frame, mode, **kw: synthetic_read(mode, 2)):
            second = self.dispatch("recover_map_open", recoveryId=ticket)
        self.assertTrue(second["success"], second)
        self.assertFalse(self.runtime.snapshot()["mapRecoveryPending"])
        self.assertTrue(self.runtime.snapshot()["displayAuthorized"])

    def test_recovery_exact_probe_interval_count_and_expiry_budget(self):
        self.now = 0.
        self.runtime._monotonic = lambda: self.now
        original_capture = self.backend.capture
        captured_at = []
        def capture(*args):
            captured_at.append(self.now)
            return original_capture(*args)
        def arm():
            self.assertTrue(self.runtime._arm_map_open_recovery(None, self.runtime._epoch))
            self.session.refreshRuntime()
            self.app.processEvents()
        def tick(moment):
            self.assertGreaterEqual(moment, self.now)
            self.now = moment
            self.coordinator._tracking_timer.timeout.emit()
            self.settle()
        with self.negative(), patch.object(self.backend, "capture", side_effect=capture):
            arm()
            self.assertEqual(self.runtime._map_recovery["remaining"], 8)
            self.assertEqual(self.runtime._map_recovery["nextAt"], .2)
            self.assertEqual(self.runtime._map_recovery["expiresAt"], 2.)
            for moment in (0., .01, .05, .10, .15, .199999):
                tick(moment)
            self.assertEqual(captured_at, [], "dense early timer ticks must not capture")
            for probe in range(8):
                due = self.runtime._map_recovery["nextAt"]
                remaining = due - self.now
                for moment in (self.now, self.now + remaining * .25, self.now + remaining * .75):
                    tick(moment)
                    self.assertEqual(len(captured_at), probe)
                tick(due)
                self.assertEqual(len(captured_at), probe + 1)
            self.assertLess(captured_at[-1], 2., "count cap must be reached before the time cap")
            self.assertTrue(all(b - a >= .2 - 1e-12 for a, b in zip(captured_at, captured_at[1:])))
            self.assertFalse(self.runtime.map_open_recovery_status()["pending"])
            for moment in (1.7, 1.8, 1.9, 2., 2.01, 3.):
                tick(moment)
            self.assertEqual(len(captured_at), 8)
            # Independently expire a ticket with seven probes still unused.
            # Exhausting eight first would not prove the wall-clock boundary.
            self.now = 10.
            arm()
            tick(10.2)
            self.assertEqual(len(captured_at), 9)
            self.assertEqual(self.runtime.map_open_recovery_status()["remaining"], 7)
            for moment in (12., 12.001, 12.2, 13.):
                tick(moment)
            self.assertEqual(len(captured_at), 9)
            self.assertFalse(self.runtime.map_open_recovery_status()["pending"])
        self.assertFalse(self.runtime.snapshot()["displayAuthorized"])
        self.assertFalse(self.coordinator.alignedData)

    def test_binding_resume_black_then_ready_uses_bounded_foreground_return(self):
        self.keys.resetMapInput(True)
        self.coordinator._suspend_game_binding("fixture-minimized")
        self.assertFalse(self.keys.enabled)
        calls = []
        original = self.backend.capture
        def capture(*args):
            calls.append(1)
            frame = original(*args).copy()
            frame[0, 0, 2] = len(calls)
            return frame
        with patch.object(self.backend, "capture", side_effect=capture), \
                patch("map_interface_observation.detect_map_interface",
                      side_effect=lambda frame, **kwargs: dict(detected=int(frame[0, 0, 2]) >= 2)), \
                patch("floor_control_region.locate_vertical_floor_controls",
                      side_effect=lambda frame, *args, **kwargs: synthetic_read(self.mode)
                      if int(frame[0, 0, 2]) >= 2 else dict(selectedFloor=None)):
            self.coordinator._resume_game_binding()
            self.assertFalse(self.keys.enabled)
            self.settle()
        self.assertEqual(len(calls), 2)
        self.assertTrue(self.keys.enabled, self.session.snapshot.get("lastReceipt"))
        self.assertTrue(self.keys._map_input.open_intent)
        self.assertFalse(self.coordinator._binding_suspended)
        self.assertTrue(self.runtime.snapshot()["displayAuthorized"])

    def test_binding_resume_all_unknown_finishes_without_confirming_close(self):
        self.keys.resetMapInput(True)
        self.coordinator._suspend_game_binding("fixture-minimized")
        with self.negative():
            self.coordinator._resume_game_binding()
            self.settle()
        receipt = self.session.snapshot["lastReceipt"]
        self.assertEqual(receipt["reason"], "map-open-preparation-deadline")
        self.assertIsNone(receipt["mapInputObservation"]["mapOpen"])
        self.assertTrue(self.keys._map_input.open_intent)
        self.assertTrue(self.keys.enabled)
        self.assertFalse(self.coordinator._binding_resume_pending)
        self.assertFalse(self.runtime.snapshot()["displayAuthorized"])

    def test_binding_resume_resource_busy_defers_then_recovers(self):
        identity = self.coordinator._binding_identity
        round_id = self.runtime.snapshot()["roundId"]
        self.coordinator._suspend_game_binding("fixture-unavailable")
        self.ui.resources.busy = True
        with patch.object(self.backend, "capture", side_effect=AssertionError("busy capture")):
            self.coordinator._resume_game_binding()
        self.assertFalse(self.keys.enabled)
        self.ui.resources.busy = False
        self.coordinator._resume_game_binding()
        self.settle()
        self.assertTrue(self.keys.enabled)
        self.assertEqual(self.coordinator._binding_identity, identity)
        self.assertEqual(self.runtime.snapshot()["roundId"], round_id)

    def test_old_unknown_resume_receipt_cannot_override_a_new_key_sequence(self):
        self.coordinator._suspend_game_binding("fixture-unavailable")
        with self.negative():
            self.coordinator._resume_game_binding()
            self.ui.resources.busy = True
            self.keys._dispatch_action("map", dict(createdAt=1.))
            self.ui.resources.busy = False
            self.settle()
        self.assertTrue(self.coordinator._binding_suspended)
        self.assertFalse(self.keys.enabled)
        self.assertFalse(self.coordinator._binding_resume_pending)
        self.coordinator._resume_game_binding()
        self.settle()
        self.assertTrue(self.keys.enabled, self.session.snapshot.get("lastReceipt"))
        self.assertTrue(self.keys._map_input.open_intent)

    def test_minimized_and_unknown_catalog_keep_binding_identity_until_current_resume(self):
        row = dict(hwnd="42", pid=7, processCreateTime=123., executable="fixture-game.exe", minimized=True)
        identity = self.coordinator._window_identity(row)
        self.coordinator._binding_identity = identity
        self.coordinator._catalog = Mock(return_value=[row])
        self.coordinator._discover_game()
        self.assertTrue(self.coordinator._binding_suspended)
        self.assertEqual(self.coordinator._binding_identity, identity)
        self.coordinator._catalog.return_value = []
        self.coordinator._binding_probe = Mock(return_value=dict(status="unknown", reason="binding-identity-unreadable"))
        self.coordinator._discover_game()
        self.assertEqual(self.coordinator._bound, "42")
        self.assertTrue(self.runtime.snapshot()["roundActive"])
        self.coordinator._catalog.return_value = [dict(row, minimized=False)]
        self.coordinator._discover_game()
        self.settle()
        self.assertFalse(self.coordinator._binding_suspended)
        self.assertEqual(self.coordinator._binding_identity, identity)
        self.assertTrue(self.keys.enabled)

    def test_confirmed_close_during_pause_is_preserved_after_unknown_resume(self):
        self.keys.resetMapInput(True)
        self.coordinator._suspend_game_binding("fixture-unavailable")
        self.coordinator._close_map_from_input(dict(inputSequence=self.coordinator._map_input_sequence))
        self.settle()
        self.assertFalse(self.keys._map_input.open_intent)
        with self.negative():
            self.coordinator._resume_game_binding()
            self.settle()
        self.assertFalse(self.keys._map_input.open_intent)
        self.assertTrue(self.keys.enabled)
        self.assertFalse(self.runtime.snapshot()["mapOpen"])


class DifficultRuntimeBoundaries(RuntimeBoundaries):
    mode = "difficult"


class GroupBoundaries(groups.DragGroupChoiceContracts):
    def test_repeated_staged_target_failure_does_not_replace_source_fit_report(self):
        self.finish_drag()
        source = deepcopy(self.runtime._fit_reporting.__dict__)
        self.target_geometry_ok = False
        self.choose_target()
        self.drive("refresh_map")
        self.drive("refresh_map")
        self.assertEqual(self.runtime._fit_reporting.__dict__, source)
        self.assertTrue(self.runtime.snapshot()["displayAuthorized"])
        self.assertFalse(self.runtime.snapshot()["fitReportingWaiting"])
        self.assertFalse(any(row.get("stage") == "map-fit-waiting" for row in self.traces))
        self.drive("close_map")
        self.assertFalse(any(row.get("stage") == "map-fit-incomplete" for row in self.traces))

    def test_helper_ready_post_drag_choice_fits_current_pixels_without_false_close(self):
        self.finish_drag()
        observation, floor_read = dict(detected=True), synthetic_read(self.mode)
        with patch("map_interface_observation.detect_map_interface", return_value=observation), \
                patch("floor_control_region.locate_vertical_floor_controls", return_value=floor_read):
            self.choose_target()
            token = self.await_target_material()
        self.assertTrue(self.runtime.snapshot()["mapOpen"])
        self.assertTrue(all(row["marker"] == 2 for row in self.fit_calls))
        self.coordinator.acceptSiblingMaterial(token, True)
        self.settled()
        self.assertTrue(self.runtime.snapshot()["displayAuthorized"])
        self.assertEqual(self.runtime.snapshot()["currentGroupMemberId"], groups.group_fixtures.IDS[1])


class DifficultGroupBoundaries(GroupBoundaries):
    mode = "difficult"


class CapabilityBoundary(unittest.TestCase):
    def test_nested_capability_and_trigger_reject_unknown_keys_and_unbounded_values(self):
        self.assertEqual(capture_capability(dict(safeFallback="unsupported", private="SECRET",
            windowsBuild=2**80, reason=["private"], fallbackRequired=True)),
            dict(safeFallback="unsupported", fallbackRequired=True))
        self.assertEqual(fallback_trigger(dict(type=["secret"], reason="client-dc-native-error",
            errno=5, winerror=2**80, private="SECRET")), dict(reason="client-dc-native-error", errno=5))

    def test_product_exception_frames_survive_main_privacy_filter_without_messages(self):
        from client_preparation_diagnostics import exception_metadata
        from diagnostic_privacy import safe_record
        try:
            exec(compile("def capture():\n    raise OSError(5, 'PRIVATE-ACCOUNT')\ncapture()", "client_live_runtime.py", "exec"), {})
        except OSError as error:
            data = safe_record(exception_metadata(error))
        self.assertTrue(any('File "client_live_runtime.py", line' in row for row in data["stack"]))
        self.assertNotIn("PRIVATE-ACCOUNT", str(data))


def load_tests(loader, tests, pattern):
    # Reuse fixtures without rerunning their inherited historical test methods.
    result = unittest.TestSuite()
    for cls, owner in ((RuntimeBoundaries, RuntimeBoundaries),
            (DifficultRuntimeBoundaries, RuntimeBoundaries), (GroupBoundaries, GroupBoundaries),
            (DifficultGroupBoundaries, GroupBoundaries), (CapabilityBoundary, CapabilityBoundary)):
        result.addTests(cls(name) for name in owner.__dict__ if name.startswith("test_"))
    return result


if __name__ == "__main__":
    unittest.main()
