"""Real serial Qt worker ordering; controlled capture/geometry, no native IO."""
import copy
import threading
import unittest
from unittest.mock import Mock, patch

import test_client_live_runtime as fixtures
from client_live_runtime import _AlignmentHost
from client_session import SessionService
from test_client_session import until
from PySide6.QtCore import QObject, Signal, Qt


class RuntimeEvents(QObject):
    event = Signal(object)


class MaterialHandoffTests(unittest.TestCase):
    def producer(self, mode, *, display=False):
        fixture = fixtures.LiveRuntimeContracts()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        if display:
            # Controlled current-client oracle, not a real foreground window.
            fixture.runtime._evidence_kind = "live-client-capture"
            fixture.capture.generation = 1
            fixture.capture.is_current = Mock(return_value=True)
        if mode != "nightmare":
            fixture.dispatch("set_game_mode", mode=mode)
        fixture.begin_and_capture()
        session = SessionService(fixture.runtime)
        def cleanup():
            session.shutdown()
            until(lambda: not session._jobs and not session._maintenance)
        self.addCleanup(cleanup)
        return fixture, session

    def test_material_ack_during_refresh_survives_until_fifo_remember(self):
        for mode in ("nightmare", "difficult"):
            with self.subTest(mode=mode):
                fixture, session = self.producer(mode)
                runtime = fixture.runtime
                token = runtime.snapshot()["render"]["token"]
                old_frame = runtime.snapshot()["frameId"]
                entered, release = threading.Event(), threading.Event()
                def residual(*args):
                    entered.set()
                    if not release.wait(2):
                        raise AssertionError("Test refresh was not released")
                    return copy.deepcopy(fixture.fit["selectedRouteCurrentFrameResidual"]), None
                remember = Mock(wraps=runtime._steady.remember_committed)
                with patch.object(_AlignmentHost, "_canonical_geometry_current_frame_residual", side_effect=residual), \
                     patch.object(runtime._steady, "remember_committed", remember):
                    try:
                        session.dispatch("refresh_map")
                        until(entered.is_set)
                        self.assertTrue(runtime.acknowledge_render_material(token)["success"])
                        self.assertTrue(session.dispatchMaintenance("remember_render_material", {"renderToken": token}))
                    finally:
                        release.set()
                    until(lambda: not session._jobs and not session._maintenance)
                self.assertEqual(session.snapshot["lastReceipt"]["reason"], "current-pose-refreshed")
                self.assertNotEqual(runtime.snapshot()["frameId"], old_frame)
                self.assertEqual(runtime.snapshot()["render"]["token"], token)
                remember.assert_called_once()
                self.assertTrue(runtime._steady_material_ready)
                # A later close still revokes both display and delayed cache work.
                runtime.invalidate("map-closed")
                self.assertIsNone(runtime.render_payload(token))
                self.assertFalse(runtime.acknowledge_render_material(token)["success"])
                self.assertFalse(runtime.dispatch("remember_render_material", {"renderToken": token})["success"])

    def test_refresh_requeues_remember_cancelled_after_it_started(self):
        fixture, session = self.producer("nightmare")
        runtime = fixture.runtime
        token = runtime.snapshot()["render"]["token"]
        relay = RuntimeEvents()
        def on_event(event):
            if event["event"] == "render-material-accepted":
                session.dispatchMaintenance("remember_render_material", {"renderToken": event["token"]})
        relay.event.connect(on_event, Qt.QueuedConnection)
        runtime.set_event_callback(relay.event.emit)
        entered, release = threading.Event(), threading.Event()
        original = runtime._steady.remember_committed
        def remember(*args, **kwargs):
            if not entered.is_set():
                entered.set()
                if not release.wait(2):
                    raise AssertionError("Test remember was not released")
            return original(*args, **kwargs)
        residual = (fixture.fit["selectedRouteCurrentFrameResidual"], None)
        with patch.object(runtime._steady, "remember_committed", side_effect=remember) as recorded, \
             patch.object(_AlignmentHost, "_canonical_geometry_current_frame_residual", return_value=residual):
            try:
                self.assertTrue(runtime.acknowledge_render_material(token)["success"])
                until(entered.is_set)
                session.dispatch("refresh_map")
            finally:
                release.set()
            until(lambda: not session._jobs and not session._maintenance)
        self.assertEqual(recorded.call_count, 2)
        self.assertTrue(runtime._steady_material_ready)
        self.assertEqual(runtime.snapshot()["render"]["token"], token)

    def test_close_reopen_behind_running_fit_rejects_old_output_in_real_fifo(self):
        from test_client_coordinator import CoordinatorTests
        for mode in ("nightmare", "difficult"):
            with self.subTest(mode=mode):
                fixture, session = self.producer(mode, display=True)
                runtime = fixture.runtime
                CoordinatorTests.setUpClass()
                view = CoordinatorTests()
                view.setUp()
                self.addCleanup(view.tearDown)
                coordinator = view.coordinator
                view.session.changed.disconnect(coordinator._session_changed)
                view.session.actionRequested.disconnect(coordinator._action_requested)
                coordinator.runtime, coordinator.session = runtime, session
                coordinator._applied_context = (mode, runtime.snapshot()["profileId"])
                coordinator._convert_client_geometry = lambda row: row.update(deviceScale=1.)
                view.images.publish = Mock(side_effect=lambda token, *a, **kw: "image://live/" + token)
                session.changed.connect(coordinator._session_changed)
                session.actionRequested.connect(coordinator._action_requested)
                runtime.set_event_callback(coordinator.runtimeEvent.emit)
                coordinator._present_runtime_state(runtime.snapshot(), trigger="controlled-initial")
                old_token = coordinator.alignedData["token"]
                entered, release = threading.Event(), threading.Event()
                receipts = []
                original_dispatch = runtime.dispatch
                def dispatch(action, payload, cancelled=None):
                    result = original_dispatch(action, payload, cancelled)
                    receipts.append((action, result))
                    return result
                def fit(*args):
                    if not entered.is_set():
                        entered.set()
                        if not release.wait(2):
                            raise AssertionError("Test fit was not released")
                    return copy.deepcopy(fixture.fit)
                with patch.object(runtime, "dispatch", side_effect=dispatch), \
                     patch.object(_AlignmentHost, "fit", side_effect=fit):
                    try:
                        session.dispatch("capture_now")
                        until(entered.is_set)
                        view.input.mapInputRequested.emit(dict(sequence=1, openIntent=False,
                            createdAt=1., hwnd=123, source="controlled-close"))
                        self.assertFalse(coordinator.alignedData)
                        self.assertIsNone(runtime.render_payload(old_token))
                        self.assertFalse(runtime.acknowledge_render_material(old_token)["success"])
                        view.input.mapInputRequested.emit(dict(sequence=2, openIntent=True,
                            createdAt=2., hwnd=123, source="controlled-reopen"))
                    finally:
                        release.set()
                    until(lambda: not session._jobs and not session._maintenance)
                self.assertEqual([action for action, result in receipts], ["capture_now", "close_map", "map"])
                self.assertFalse(receipts[0][1]["success"])
                self.assertTrue(receipts[-1][1]["success"], receipts)
                current = runtime.snapshot()["render"]
                self.assertNotEqual(current["token"], old_token)
                self.assertEqual(coordinator.alignedData["token"], current["token"])
                # A delayed close notification cannot erase a newer valid grant.
                coordinator._on_runtime_event(dict(event="render-revoked", reason="map-closed", token=old_token))
                self.assertEqual(coordinator.alignedData["token"], current["token"])
                self.assertFalse(runtime.acknowledge_render_material(old_token)["success"])


if __name__ == "__main__":
    unittest.main()
