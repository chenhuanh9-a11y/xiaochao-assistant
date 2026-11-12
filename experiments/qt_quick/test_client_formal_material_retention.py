"""Completed-material retention through real runtime/Qt worker consumers.

Native IO, pixels, floor readings and numerical fit measurements are controlled;
Session, Coordinator, core grants, publication and ACK guards remain production.
These are functional contracts, not GUI/game or timing acceptance.
"""
from copy import copy, deepcopy
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from client_live_runtime import _AlignmentHost
from client_engine import clone_similarity_state
from s0_test_fixtures import synthetic_read
import test_client_drag_probe_presentation as ingress
import test_s1_manual_floor_runtime as manual
import test_client_local_identity_commit as local_identity
import test_client_similarity_protocol as groups
from test_field_map_close_input import controls


class FormalMaterialRetention(unittest.TestCase):
    mode = "nightmare"
    setUp = ingress.DragProbePresentationContracts.setUp
    dispatch = ingress.DragProbePresentationContracts.dispatch
    _stop_timers = ingress.DragProbePresentationContracts._stop_timers
    _cleanup_input_session = ingress.DragProbePresentationContracts._cleanup_input_session
    pointer = ingress.DragProbePresentationContracts.pointer

    def settle(self):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            self.app.processEvents()
            if not self.session.busy and self.session._pool.activeThreadCount() == 0:
                self.app.processEvents()
                return
            time.sleep(.001)
        self.fail("bounded production worker did not settle")

    def completed_source(self):
        token = self.coordinator.alignedData["token"]
        row = self.runtime.render_payload(token)
        self.assertTrue(row["poseFinalVerified"])
        self.assertEqual(row["reviewStatus"], "complete")
        return row

    def assert_retained(self, source):
        row = self.runtime.render_payload(source["token"])
        self.assertIsNotNone(row)
        for key in ("frameId", "generation", "authorizationId", "matrix", "canonicalMatrix"):
            self.assertEqual(row[key], source[key])
        self.assertIs(row["pixels"], source["pixels"])
        self.assertEqual(self.coordinator.alignedData.get("token"), source["token"])
        self.assertTrue(self.runtime._core.display_authorization.active)
        self.assertTrue(self.runtime.snapshot()["mapOpen"])

    def drive(self, action):
        self.session.dispatch(action)
        self.settle()
        return self.session.snapshot["lastReceipt"]

    def test_same_opening_capture_keeps_material_while_blocked_and_after_fit_failure(self):
        source = self.completed_source()
        entered, release = threading.Event(), threading.Event()
        original = self.runtime._capture_frame
        def capture(cancelled, **kwargs):
            entered.set()
            if not release.wait(3):
                raise AssertionError("capture barrier not released")
            return original(cancelled, **kwargs)
        with patch.object(self.runtime, "_capture_frame", side_effect=capture), \
                patch.object(_AlignmentHost, "fit", return_value=dict(success=False, reason="controlled-fit-failure")):
            self.session.dispatch("capture_now")
            try:
                self.assertTrue(entered.wait(2))
                self.app.processEvents()
                self.assert_retained(source)
            finally:
                release.set()
                self.settle()
        self.assert_retained(source)
        self.assertFalse(self.session.snapshot["lastReceipt"]["success"])
        self.assertNotEqual(self.runtime._last_observation["frame_id"], source["frameId"])

    def test_failed_residual_and_full_fit_keep_old_grant_without_new_frame_ack(self):
        source = self.completed_source()
        old_core = self.runtime._core
        with patch.object(_AlignmentHost, "_canonical_geometry_current_frame_residual",
                          return_value=(dict(verified=False), None)), \
                patch.object(_AlignmentHost, "fit", return_value=dict(success=False, reason="controlled-fit-failure")):
            receipt = self.drive("refresh_map")
        self.assertFalse(receipt["success"])
        self.assert_retained(source)
        self.assertIs(self.runtime._core, old_core)
        self.assertNotEqual(self.runtime._frame_id, source["frameId"])
        self.assertEqual(self.runtime._last_observation["frame_id"], self.runtime._frame_id)
        self.assertFalse(self.runtime.acknowledge_render_material(source["token"])["success"])

    def test_completed_source_is_not_replaced_by_usable_fit_until_final_passes(self):
        source = self.completed_source()
        fit = deepcopy(self.runtime._remember_proposals[source["token"]]["fit"])
        fit["matrixSourceToClient"][0, 2] += 17
        checked = []
        def measured(host, *args, **kwargs):
            host._offer_usable_fit(fit)
            self.assertIsNotNone(self.runtime.render_payload(source["token"]))
            checked.append(self.runtime._render["token"])
            return fit
        with patch.object(_AlignmentHost, "_canonical_geometry_current_frame_residual",
                          return_value=(dict(verified=False), None)), \
                patch.object(_AlignmentHost, "fit", measured):
            receipt = self.drive("refresh_map")
        self.assertTrue(receipt["success"], receipt)
        self.assertEqual(checked, [source["token"]])
        current = self.runtime.render_payload(receipt["renderToken"])
        self.assertNotEqual(current["token"], source["token"])
        self.assertTrue(current["poseFinalVerified"])
        self.assertEqual(current["reviewStatus"], "complete")
        np.testing.assert_allclose(current["canonicalMatrix"], fit["matrixSourceToClient"])
        self.assertEqual(self.coordinator.alignedData.get("token"), current["token"])
        self.assertIsNone(self.runtime.render_payload(source["token"]))

    def test_usable_fit_then_final_peer_rejection_keeps_completed_source(self):
        source = self.completed_source()
        fit = deepcopy(self.runtime._remember_proposals[source["token"]]["fit"])
        def rejected(host, *args, **kwargs):
            host._offer_usable_fit(fit)
            return dict(success=False, reason="manual-components-inconsistent")
        with patch.object(_AlignmentHost, "_canonical_geometry_current_frame_residual",
                          return_value=(dict(verified=False), None)), \
                patch.object(_AlignmentHost, "fit", rejected):
            receipt = self.drive("refresh_map")
        self.assertFalse(receipt["success"])
        self.assertEqual(receipt["reason"], "manual-components-inconsistent")
        self.assert_retained(source)

    def test_ordinary_capture_error_keeps_owner_and_error_receipt(self):
        source = self.completed_source()
        epoch = self.runtime._epoch
        with patch.object(self.runtime, "_capture_frame", side_effect=OSError("controlled-capture-error")):
            receipt = self.drive("capture_now")
        self.assertTrue(receipt["error"])
        self.assertFalse(receipt["success"])
        self.assertEqual(self.runtime._epoch, epoch)
        self.assert_retained(source)

    def test_ordinary_display_io_failure_keeps_completed_source(self):
        source = self.completed_source()
        with patch.object(self.runtime, "_display_layer", side_effect=OSError("controlled-display-io")):
            receipt = self.drive("capture_now")
        self.assertTrue(receipt["error"])
        self.assertFalse(receipt["success"])
        self.assert_retained(source)

    def test_late_display_resource_error_after_close_cannot_end_the_round(self):
        from client_providers import DisplayResourceChanged
        source = self.completed_source()
        closed_epoch = []
        def changed(*args, **kwargs):
            self.runtime.invalidate("map-closed", source="controlled-close-during-display-read")
            closed_epoch.append(self.runtime._epoch)
            raise DisplayResourceChanged("display-resource-owner-changed")
        with patch.object(self.runtime, "_display_layer", side_effect=changed):
            self.drive("capture_now")
        self.assertEqual(self.runtime._epoch, closed_epoch[0])
        self.assertIs(self.runtime._engine, self.engine)
        self.assertTrue(self.runtime._active)
        self.assertFalse(self.runtime._map_open)
        self.assertIsNone(self.runtime.render_payload(source["token"]))

    def test_cancel_steady_verification_keeps_completed_material(self):
        source = self.completed_source()
        entered, release = threading.Event(), threading.Event()
        residual = self.runtime._remember_proposals[source["token"]]["fit"]["selectedRouteCurrentFrameResidual"]
        def blocked(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise AssertionError("residual barrier not released")
            return residual, None
        with patch.object(_AlignmentHost, "_canonical_geometry_current_frame_residual", side_effect=blocked):
            self.session.dispatch("refresh_map")
            try:
                self.assertTrue(entered.wait(2))
                self.session.stop()
            finally:
                release.set()
                self.settle()
        self.assert_retained(source)

    def test_unknown_and_first_miss_keep_material_but_confirmed_close_retires_it(self):
        source = self.completed_source()
        with patch("floor_control_region.locate_vertical_floor_controls", return_value=dict(selectedFloor=None)), \
                patch.object(_AlignmentHost, "fit", side_effect=AssertionError("unknown cannot fit")):
            self.drive("refresh_map")
        self.assert_retained(source)
        with patch("map_interface_observation.detect_map_interface", return_value=dict(detected=False)), \
                patch.object(_AlignmentHost, "fit", side_effect=AssertionError("closed cannot fit")):
            self.drive("refresh_map")
            self.assert_retained(source)
            self.drive("refresh_map")
        self.assertFalse(self.runtime.snapshot()["mapOpen"])
        self.assertIsNone(self.runtime.render_payload(source["token"]))
        self.assertFalse(self.coordinator.alignedData)

    def test_resource_context_loss_is_not_an_ordinary_capture_error(self):
        source = self.completed_source()
        def failed(*args, **kwargs):
            self.engine.qt_resource_context_current = lambda: False
            raise OSError("resource-owner-lost")
        with patch.object(self.runtime, "_capture_frame", side_effect=failed):
            self.drive("capture_now")
        self.assertIsNone(self.runtime.render_payload(source["token"]))
        self.assertFalse(self.coordinator.alignedData)

    def test_original_two_frame_identity_conflict_retires_the_completed_consumer(self):
        measured = deepcopy(self.runtime._remember_proposals[self.runtime._render["token"]]["fit"])
        self.drive("end_round")
        sibling = SimpleNamespace(id=groups.IDS[1])
        self.engine.pack.maps.append(sibling)
        self.engine.pack.by_id[sibling.id] = sibling
        self.engine.qt_logical_group_registry = groups.explicit_registry(self.mode)
        self.engine.rank = Mock(return_value=groups.rows(mode=self.mode))
        self.drive("start_round")
        with patch.object(_AlignmentHost, "fit", return_value=measured):
            opened = self.drive("capture_now")
        self.assertTrue(opened["success"], opened)
        # Retain the original conflict consumer's existing legacy verified-state
        # fixture. The material handoff and acceptance still use the Coordinator.
        identity = clone_similarity_state(self.runtime._identity_native)
        identity.pending_identity = (groups.IDS[0], "1", self.runtime._identity_context())
        pending = self.runtime._stage_current_member_material("legacy-test", {}, identity)
        self.settle()
        self.coordinator.acceptSiblingMaterial(pending["pendingSiblingToken"], True)
        self.settle()
        self.assertTrue(self.runtime.snapshot()["identityLocked"])
        source = self.completed_source()
        frame = controls()
        self.backend.capture = lambda *args: frame.copy()
        failure = dict(success=False, reason="entry-reverification-failed",
            entryCrossCheck=dict(contradicted=True))
        with patch.object(_AlignmentHost, "fit", return_value=failure):
            frame[0, 0] = 2
            first = self.drive("capture_now")
            self.assertFalse(first["identityReopened"])
            self.assert_retained(source)
            frame[0, 0] = 3
            second = self.drive("capture_now")
        self.assertTrue(second["identityReopened"])
        self.assertFalse(self.runtime.snapshot()["identityLocked"])
        self.assertIsNone(self.runtime.render_payload(source["token"]))
        self.assertFalse(self.runtime._core.display_authorization.active)
        self.assertFalse(self.coordinator.alignedData)


class DifficultFormalMaterialRetention(FormalMaterialRetention):
    mode = "difficult"


class S1FormalMaterialRetention(unittest.TestCase):
    setUp = manual.ManualS1FloorRuntime.setUp
    start_patch = manual.ManualS1FloorRuntime.start_patch
    prepare = manual.ManualS1FloorRuntime.prepare
    pick = manual.ManualS1FloorRuntime.pick

    def source(self):
        self.prepare(); self.pick()
        return self.runtime.render_payload(self.runtime._render["token"])

    def assert_retained(self, source):
        current = self.runtime.render_payload(source["token"])
        self.assertIsNotNone(current)
        for key in ("generation", "frameId", "authorizationId", "matrix"):
            self.assertEqual(current[key], source[key])
        self.assertIs(current["pixels"], source["pixels"])
        self.assertEqual(self.runtime._floor, "-1")

    def test_supported_context_unknown_retains_completed_material_without_new_fit(self):
        source = self.source()
        fits = self.fitter.call_count
        self.raw = None
        result = self.runtime.dispatch("observe_manual_floor")
        self.assertFalse(result["success"])
        self.assertEqual(self.fitter.call_count, fits)
        self.assert_retained(source)
        self.assertEqual(self.runtime._last_observation["floor"], None)
        self.assertFalse(self.runtime.acknowledge_render_material(source["token"])["success"])

    def test_supported_context_failed_residual_and_fit_keep_source(self):
        source = self.source()
        self.fitter.side_effect = lambda *a, **kw: dict(success=False, reason="controlled-s1-fit-failure")
        with patch.object(_AlignmentHost, "_canonical_geometry_current_frame_residual",
                          return_value=(dict(verified=False), None)):
            result = self.runtime.dispatch("observe_manual_floor")
        self.assertFalse(result["success"])
        self.assert_retained(source)

    def test_supported_context_capture_error_keeps_source(self):
        source = self.source()
        with patch.object(self.capture, "capture", side_effect=OSError("controlled-s1-capture-error")):
            result = self.runtime.dispatch("fit_manual_frame")
        self.assertTrue(result["error"])
        self.assert_retained(source)


class NativeDisplayResourceRetention(unittest.TestCase):
    """Actual decoded mixed native pool, guard and local identity transaction.

    The reused fixture substitutes OS pixels/floor reads and numerical probe,
    verification and fit measurements. Display/provider/cache guards are real.
    """
    setUpClass = local_identity.LocalIdentityCommitTests.__dict__["setUpClass"]
    setUp = local_identity.LocalIdentityCommitTests.setUp
    opening = local_identity.LocalIdentityCommitTests.opening

    def assert_cache_replacement_retires_material(self, *, publication):
        import shared_structure_display as display
        result = self.opening()
        self.assertTrue(result["success"], result)
        source = self.runtime.render_payload(result["renderToken"])
        self.assertTrue(source["poseFinalVerified"])
        provider = self.engine.qt_shared_provider
        actual_identity = display.structure_display_identity
        changed, fitting = [], []
        fit = self.fit.return_value
        def measured_fit(*args, **kwargs):
            fitting.append(True)
            return deepcopy(fit)
        self.fit.side_effect = measured_fit
        def replace_cache(reference):
            identity = actual_identity(reference)
            if not changed and (not publication or fitting):
                # Same decoded references and metadata, different actual cache
                # owner while the production display consumer is using it.
                provider._cache = copy(provider._cache)
                changed.append(True)
            return identity
        with patch.object(display, "structure_display_identity", side_effect=replace_cache):
            receipt = self.runtime.dispatch("capture_now")
        self.assertEqual(changed, [True])
        self.assertEqual(bool(fitting), publication)
        self.assertFalse(receipt["success"], receipt)
        self.assertTrue(receipt["error"], receipt)
        self.assertIn("DisplayResourceChanged", receipt["reason"])
        self.assertIsNone(self.runtime.render_payload(source["token"]))
        self.assertIsNone(self.runtime._render)
        self.assertFalse(self.runtime._active)

    def test_actual_cache_replacement_during_prepare_cannot_be_retained_as_io(self):
        self.assert_cache_replacement_retires_material(publication=False)

    def test_actual_cache_replacement_during_publication_cannot_be_retained_as_io(self):
        self.assert_cache_replacement_retires_material(publication=True)
