"""Bounded old-state delivery counterexample; no native/game IO.

The injected Qt delivery order is a controlled schedule. Field logs show
pointer-drag publication followed by session-completed payload-expired clearing
the new token (5408 previous log lines 949-955; 6148 previous lines 1157-1160).
They do not expose the requested stale token or the exact worker schedule.
Runtime/core authorization,
ClientCaptureProvider geometry/generation/foreground checks and material ACK
are production code. Only OS/pixels, solver measurements and window/image sinks
are fixtures. No guard is replaced by an unconditional success function.
"""
from copy import deepcopy
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PySide6.QtCore import QObject, Qt, Signal

from client_capture import ClientCaptureProvider
from client_live_runtime import LiveRuntime, _AlignmentHost
from client_region_capture import ClientCaptureContext
import test_client_capture_full as capture_fixtures
import test_client_coordinator as coordinator_fixtures
import test_client_live_runtime as runtime_fixtures


class QueuedPresentation(QObject):
    state = Signal(object)


class StalePresentationContracts(unittest.TestCase):
    mode = "nightmare"

    def setUp(self):
        coordinator_fixtures.CoordinatorTests.setUpClass()
        self.ui = coordinator_fixtures.CoordinatorTests("runTest")
        self.ui.setUp()
        self.addCleanup(self.ui.tearDown)
        self.coordinator = self.ui.coordinator
        self.native, self.backend = capture_fixtures.Native(), capture_fixtures.Backend()
        self.backend.current = ClientCaptureContext((1280, 720), (100, 200, 1380, 920), 96)
        self.capture = ClientCaptureProvider(self.native, self.backend)
        self.addCleanup(self.capture.shutdown)
        self.engine = runtime_fixtures.fixture_engine(self.mode)
        # This evidence-kind selects production live guards, not real capture.
        self.runtime = LiveRuntime(engine_factories={self.mode: lambda: self.engine},
            capture_provider=self.capture, evidence_kind="live-client-capture", clock=lambda: 1)
        self.events = []
        self.runtime.set_event_callback(self.events.append)
        self.coordinator.runtime = self.runtime
        self.coordinator._bound = "42"
        self.coordinator._native_windows = self.native
        self.ui.images.publish = Mock(side_effect=lambda token, pixels, **kw: "image://fixture/" + token)
        self.ui.images.clear_aligned = Mock()
        # The fake HWND has no OS monitor. This replaces only DIP projection;
        # actual capture-context validation above still owns window identity.
        geometry = patch.object(self.coordinator, "_convert_client_geometry",
            side_effect=lambda row: row.update(deviceScale=1.0))
        geometry.start(); self.addCleanup(geometry.stop)
        fit = dict(success=True,
            matrixSourceToClient=np.array([[.5, 0, 40], [0, .5, 60], [0, 0, 1]]),
            selectedRouteCurrentFrameResidual=dict(verified=True,
                poseVerifiedAgainstCanonicalGeometry=True, profileRenderDerivedFromCanonical=True,
                coordinatePackId="fixture-pack", validationBasis="shared-canonical-geometry-current-frame"))
        for p in (patch("map_interface_observation.detect_map_interface", return_value=dict(detected=True)),
                  patch("map_interface_observation.detect_selected_floor", return_value=dict(selectedFloor=1)),
                  patch.object(_AlignmentHost, "fit", side_effect=lambda *a: deepcopy(fit))):
            p.start(); self.addCleanup(p.stop)
        self.traces = []
        self.coordinator.traceReported.connect(self.traces.append)
        self.delivery = QueuedPresentation()
        self.delivery.state.connect(lambda state: self.coordinator._present_runtime_state(
            state, trigger="queued-old-session-state"), Qt.QueuedConnection)
        if self.mode != "nightmare":
            self.assertTrue(self.dispatch("set_context", mode=self.mode)["success"])
        for action, payload in (("bind_capture", {"hwnd": 42}), ("start_round", {}), ("capture_now", {})):
            receipt = self.dispatch(action, **payload)
            self.assertTrue(receipt["success"], receipt)

    def dispatch(self, action, **payload):
        state = self.runtime.snapshot()
        context = {key: state[key] for key in ("mode", "profileId", "roundId", "frameId")}
        return self.runtime.dispatch(action, dict(context, generation=22, **payload))

    def test_old_queued_token_cannot_hide_acknowledged_current_refresh(self):
        old = self.runtime.snapshot()
        old_token = old["render"]["token"]
        self.coordinator._present_runtime_state(old, trigger="first-render-ready")
        # A rejected cheap residual falls back to the regular current-frame fit.
        # The unchanged input pixels still acquire a fresh frame/generation.
        with patch.object(_AlignmentHost, "_canonical_geometry_current_frame_residual", return_value=({}, None)):
            result = self.dispatch("refresh_map")
        self.assertTrue(result["success"], result)
        current = self.runtime.snapshot()
        token = current["render"]["token"]
        self.assertNotEqual(old_token, token)
        for key in ("epoch", "mode", "profileId", "mapId", "floor", "roundId", "cycleId", "captureGeneration"):
            self.assertEqual(old["render"][key], current["render"][key], key)
        self.assertGreater(current["render"]["generation"], old["render"]["generation"])
        self.assertIsNone(self.runtime.render_payload(old_token))
        self.assertIsNotNone(self.runtime.render_payload(token))
        self.coordinator._present_runtime_state(current, trigger="new-render-ready")
        self.coordinator.reportPresentation("aligned", dict(token=token, visible=True,
            materialReady=True, sourceMatchesPayload=True, validPayload=True,
            geometry=[100, 200, 1280, 720], imageStatus="ready"))
        self.assertTrue(self.runtime._remember_proposals[token]["accepted"])
        self.assertTrue(any(e["event"] == "render-material-accepted" and e["token"] == token for e in self.events))
        self.ui.windows.hide.reset_mock()
        self.delivery.state.emit(old)
        self.ui.app.processEvents()
        # This remains authorized even if the consumer incorrectly hid it.
        self.assertIsNotNone(self.runtime.render_payload(token))
        cleared = [row for row in self.traces if row["stage"] == "clear" and row["reason"] == "runtime-payload-expired"]
        hidden = any(call.args == ("aligned",) for call in self.ui.windows.hide.call_args_list)
        retained = self.coordinator.alignedData.get("token") == token
        # Restore through a fresh presentation trigger, then show that real
        # foreground loss revokes this very grant. This is not an all-true guard.
        self.coordinator._present_runtime_state(current, trigger="fresh-recovery-trigger")
        self.assertEqual(self.coordinator.alignedData.get("token"), token)
        self.native.foreground_window = lambda: 99
        self.assertIsNone(self.runtime.render_payload(token))
        self.assertFalse(self.runtime.snapshot()["core"]["displayAuthorized"])
        self.assertTrue(retained and not hidden and not cleared,
            f"old token hid current ACKed grant: retained={retained}, hidden={hidden}, "
            f"clear_count={len(cleared)}, old_generation={old['render']['generation']}, "
            f"current_generation={current['render']['generation']}")

    def test_current_grant_lost_during_stale_token_recheck_withdraws_image(self):
        old = self.runtime.snapshot()
        self.coordinator._present_runtime_state(old, trigger="first-render-ready")
        with patch.object(_AlignmentHost, "_canonical_geometry_current_frame_residual", return_value=({}, None)):
            result = self.dispatch("refresh_map")
        self.assertTrue(result["success"], result)
        current = self.runtime.snapshot()
        token = current["render"]["token"]
        self.assertNotEqual(old["render"]["token"], token)
        self.coordinator._present_runtime_state(current, trigger="new-render-ready")
        self.coordinator.reportPresentation("aligned", dict(token=token, visible=True,
            materialReady=True, sourceMatchesPayload=True, validPayload=True,
            geometry=[100, 200, 1280, 720], imageStatus="ready"))
        self.assertTrue(self.runtime._remember_proposals[token]["accepted"])
        self.assertEqual(self.coordinator.alignedData.get("token"), token)
        original = self.runtime.render_payload
        current_reads = 0

        def lose_focus_on_recheck(requested):
            nonlocal current_reads
            if requested == token:
                current_reads += 1
                if current_reads == 2:
                    # An adversarial OS change between checks. The production
                    # capture/runtime guards still decide whether to revoke it.
                    self.native.foreground_window = lambda: 99
            return original(requested)

        self.ui.windows.hide.reset_mock()
        with patch.object(self.runtime, "render_payload", side_effect=lose_focus_on_recheck):
            self.coordinator._present_runtime_state(old, trigger="expiry-between-current-checks")
        self.assertEqual(current_reads, 2)
        self.assertIsNone(original(token))
        self.assertFalse(self.runtime.snapshot()["core"]["displayAuthorized"])
        self.assertFalse(self.coordinator.alignedData)
        self.assertTrue(any(call.args == ("aligned",) for call in self.ui.windows.hide.call_args_list))
        self.assertTrue(any(row["stage"] == "clear" and row["reason"] == "current-grant-lost"
                            for row in self.traces))

    def test_same_token_foreground_loss_withdraws_final_consumer(self):
        state = self.runtime.snapshot()
        token = state["render"]["token"]
        self.coordinator._present_runtime_state(state, trigger="initial")
        self.assertEqual(self.coordinator.alignedData["token"], token)
        self.ui.windows.hide.reset_mock()
        self.native.foreground_window = lambda: 99
        self.coordinator._present_runtime_state(state, trigger="same-token-foreground-loss")
        self.assertIsNone(self.runtime.render_payload(token))
        self.assertFalse(self.runtime.snapshot()["core"]["displayAuthorized"])
        self.assertFalse(self.coordinator.alignedData,
                         "expired same-token material remains in the final consumer")
        self.assertTrue(any(call.args == ("aligned",) for call in self.ui.windows.hide.call_args_list))

    def test_cancelled_session_completion_cannot_withdraw_new_grant(self):
        from client_session import SessionService
        old = self.runtime.snapshot()
        session = SessionService(self.runtime)
        self.addCleanup(session.shutdown)
        self.coordinator.session = session
        self.coordinator._applied_context = (old["mode"], old["profileId"])
        session.changed.connect(self.coordinator._session_changed)
        self.coordinator._present_runtime_state(old, trigger="initial")
        pending = []
        # Scheduling seam only: retain a real QRunnable until cancellation.
        with patch.object(session._pool, "start", side_effect=pending.append):
            session.dispatch("refresh_map")
        self.assertEqual(len(pending), 1)
        with patch.object(_AlignmentHost, "_canonical_geometry_current_frame_residual", return_value=({}, None)):
            result = self.dispatch("refresh_map")
        self.assertTrue(result["success"], result)
        current = self.runtime.snapshot()
        token = current["render"]["token"]
        self.assertNotEqual(token, old["render"]["token"])
        session.cancelPending("mouse-drag-motion")
        self.coordinator._present_runtime_state(current, trigger="pointer-drag-move")
        self.assertEqual(self.coordinator.alignedData["token"], token)
        self.ui.windows.hide.reset_mock()
        # Real worker -> queued done -> cancellation branch -> Session.changed.
        pending[0].run()
        self.ui.app.processEvents()
        self.assertEqual(session.stale_results, 1)
        self.assertIsNotNone(self.runtime.render_payload(token))
        self.assertEqual(self.coordinator.alignedData.get("token"), token,
                         "cancelled completion withdrew a newer current grant")
        self.assertFalse(any(call.args == ("aligned",) for call in self.ui.windows.hide.call_args_list))

    def test_duplicate_new_and_old_deliveries_keep_current_material(self):
        old = self.runtime.snapshot()
        self.coordinator._present_runtime_state(old, trigger="initial")
        with patch.object(_AlignmentHost, "_canonical_geometry_current_frame_residual", return_value=({}, None)):
            result = self.dispatch("refresh_map")
        self.assertTrue(result["success"], result)
        current = self.runtime.snapshot()
        token = current["render"]["token"]
        self.assertNotEqual(token, old["render"]["token"])
        self.coordinator._present_runtime_state(current, trigger="new-render-ready")
        self.ui.images.publish.reset_mock()
        self.ui.windows.hide.reset_mock()
        for state in (current, old, current, old):
            self.coordinator._present_runtime_state(state, trigger="duplicate-delivery")
            self.assertEqual(self.coordinator.alignedData.get("token"), token)
            self.assertIsNotNone(self.runtime.render_payload(token))
        self.ui.images.publish.assert_not_called()
        self.assertFalse(any(call.args == ("aligned",) for call in self.ui.windows.hide.call_args_list))


class DifficultStalePresentationContracts(StalePresentationContracts):
    mode = "difficult"


if __name__ == "__main__":
    unittest.main()
