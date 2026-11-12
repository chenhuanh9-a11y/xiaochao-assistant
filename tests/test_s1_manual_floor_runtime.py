"""Manual S1 consumer contracts with stub pixels, plus one synthetic glyph replay.

The solver/display fixtures exercise real runtime/core transactions. None of
these fixtures are source screenshots, native capture, or live-game evidence.
"""
import copy
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from client_live_runtime import LiveRuntime, _AlignmentHost
from client_region_capture import ClientCaptureContext, RegionCapture
from test_s1_manual_alignment import Registry
from test_client_live_runtime import FixtureCapture
from test_s1_floor_observation import mathematical_frame


class CurrentFixtureCapture(FixtureCapture):
    """Synthetic full-client metadata agrees with the actual fixture dimensions."""
    def __init__(self):
        super().__init__()
        self.generation = 1

    def capture(self, cancelled=None):
        assert self.enabled
        self.count += 1
        h, w = self.frame.shape[:2]
        roi = (0, 0, w, h)
        return RegionCapture(self.frame.copy(), roi, roi,
            ClientCaptureContext((w, h), (100, 200, 100+w, 200+h), 96), "synthetic-fixture")

    def is_current(self, context, *, generation):
        h, w = self.frame.shape[:2]
        return generation == self.generation and context.full_client_size == (w, h)


class FloorRegistry(Registry):
    def __init__(self, mode="nightmare", *, capability=True):
        super().__init__()
        self.engine.pack.mode = mode
        if capability:
            self.engine.qt_native_context = SimpleNamespace(floor_observation_capability=dict(
                contract="s1-floor-only-manual-map-use", manualMapObservation=True,
                automaticFloors=[-1, -2, -3, -4]))

    def invalidate(self):
        # The runtime's offline adapter requires this registry lifecycle hook.
        # The fixture's immutable resources are intentionally reused.
        pass


def stub_pixel_read(raw=-1, *, opened=True, reason="stub-pixel-floor"):
    return dict(season="s1", mode="nightmare", selectedFloor=raw,
        observedFloorValidated=raw in (-1, -2), automaticFloorEligible=raw in (-1, -2, -3, -4), mapOpen=opened,
        mapObservation=dict(detected=opened is True), reason=reason,
        capability="s1-floor-only-manual-map-use", floorUse="manual-map-only",
        controlAxis="vertical", observedOrder=[-1, -2, -3, -4], labelEvidence=[])


class ManualS1FloorRuntime(unittest.TestCase):
    def setUp(self):
        self.registry = FloorRegistry()
        self.capture = CurrentFixtureCapture()
        self.now = 100.
        self.runtime = LiveRuntime(engine_factories=self.registry, capture_provider=self.capture,
            evidence_kind="synthetic", clock=lambda: self.now)
        self.runtime.configure_settings(dict(mapSelectionMode="auto"))
        self.runtime.dispatch("set_context", dict(season="s1", mode="nightmare"))
        self.addCleanup(self.runtime.shutdown)
        self.raw, self.opened = -1, True
        from s1_floor_observation import detect_s1_floor
        self.original_detector = detect_s1_floor
        self.pixel = self.start_patch(patch("s1_floor_observation.detect_s1_floor",
            side_effect=lambda *a, **kw: stub_pixel_read(self.raw, opened=self.opened)))
        self.display = dict(pixels=np.zeros((20, 24, 4), np.uint8), resourceIdentity="fixture",
            displayKind="shared-structure", displayLabel="fixture", routeProfileId="shared-structure",
            matrixDisplayToReference=np.eye(3))
        self.fit_result = dict(success=True, matrixSourceToClient=np.eye(3),
            observationComponent=dict(bounds=(0, 0, 1280, 720), center=(640, 360),
                x=0, y=0, width=1280, height=720, area=921600),
            selectedRouteCurrentFrameResidual=dict(verified=True,
                poseVerifiedAgainstCanonicalGeometry=True, profileRenderDerivedFromCanonical=True,
                coordinatePackId="fixture-pack", validationBasis="shared-canonical-geometry-current-frame"))
        self.start_patch(patch.object(self.runtime, "_display_layer", return_value=self.display))
        self.fitter = self.start_patch(patch.object(_AlignmentHost, "fit", side_effect=lambda *a, **kw: copy.deepcopy(self.fit_result)))

    def start_patch(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def prepare(self):
        result = self.runtime.dispatch("prepare_manual_alignment")
        self.assertTrue(result["success"], result)

    def pick(self):
        result = self.runtime.dispatch("confirm_route", dict(mapId="fixture-map"))
        self.assertTrue(result["success"], result)
        self.assertIsNotNone(self.runtime._core.active_pose)
        self.assertIsNotNone(self.runtime._render)
        return result

    def assert_no_live_or_rank(self):
        state = self.runtime.snapshot()
        self.assertFalse(state["liveEngineAvailable"])
        self.assertFalse(state["liveAuthorization"])
        self.assertEqual(self.registry.engine.rank_count, 0)

    def test_stubpixel_prepare_has_no_default_floor_and_pick_reads_fresh_b1(self):
        self.prepare()
        state = self.runtime.snapshot()
        self.assertEqual((state["floor"], state["observedFloor"], state["manualFloor"]), ("", "", None))
        self.assertEqual(self.capture.count, 0)
        self.pick()
        state = self.runtime.snapshot()
        self.assertEqual((state["floor"], state["observedFloor"], state["manualFloor"]), ("-1", "-1", None))
        self.assertEqual(state["floorSource"], "automatic-current-frame")
        self.assertEqual(self.capture.count, 1)
        self.pixel.assert_called_once()
        self.assertEqual(self.pixel.call_args.kwargs, dict(season="s1", mode="nightmare", controls=None))
        self.assert_no_live_or_rank()

    def test_stubpixel_b2_change_retires_group_and_core_before_new_fit(self):
        self.prepare(); self.pick()
        old_group, old_pose = self.runtime._group, self.runtime._core.active_pose
        self.raw = -2
        def fit(*args, **kwargs):
            self.assertIsNone(self.runtime._group)
            self.assertIsNone(self.runtime._core.active_pose)
            self.assertIsNone(self.runtime._render)
            self.assertEqual(args[2], "-2")
            return copy.deepcopy(self.fit_result)
        self.fitter.side_effect = fit
        result = self.runtime.dispatch("observe_manual_floor")
        self.assertTrue(result["success"], result)
        self.assertIsNot(self.runtime._group, old_group)
        self.assertIsNot(self.runtime._core.active_pose, old_pose)
        self.assertEqual(self.runtime._group.anchor_floor, "-2")
        self.assertEqual(self.runtime.snapshot()["observedFloor"], "-2")
        self.assert_no_live_or_rank()

    def test_stubpixel_close_forbids_passive_reopen_explicit_fit_and_open_edge_recover(self):
        self.prepare(); self.pick()
        self.runtime.dispatch("close_map", dict(inputSequence=4))
        count = self.capture.count
        result = self.runtime.dispatch("observe_manual_floor")
        self.assertFalse(result["success"])
        self.assertEqual(self.capture.count, count)
        self.assertFalse(self.runtime._map_open)
        self.assertIsNone(self.runtime._render)
        self.assertTrue(self.runtime.dispatch("fit_manual_frame")["success"])
        self.runtime.dispatch("close_map", dict(inputSequence=5))
        def prepared(host, hwnd, token, deadline, cancelled, **kwargs):
            self.assertTrue(kwargs["round_identity_locked"])
            self.assertIs(host.runtime, self.runtime)
            return host._capture_map_open_full_probe(hwnd, token, host._map_ready_context(), None,
                deadline, cancelled)[0]
        with patch("map_open_preparation.prepare_map_open_frame", side_effect=prepared) as preparation:
            result = self.runtime.dispatch("fit_manual_frame", dict(s1OpenEdge=True, inputSequence=6))
        self.assertTrue(result["success"], result)
        preparation.assert_called_once()
        self.assertTrue(self.runtime._map_open)
        self.assert_no_live_or_rank()

    def test_stubpixel_b3_b4_automatically_follow_current_frame_without_claiming_real_validation(self):
        self.prepare(); self.pick()
        for floor in (-3, -4):
            with self.subTest(floor=floor):
                self.raw = floor
                result = self.runtime.dispatch("observe_manual_floor")
                self.assertTrue(result["success"], result)
                state = self.runtime.snapshot()
                self.assertEqual((state["floor"], state["observedFloor"]), (str(floor), str(floor)))
                self.assertIsNone(state["manualFloor"])
                self.assertEqual(state["floorSource"], "automatic-current-frame")
                self.assertFalse(self.runtime._last_observation["floor_read"]["observedFloorValidated"])
                self.assertIsNotNone(self.runtime._render)
                self.raw = -1 if floor == -3 else -2
                self.assertTrue(self.runtime.dispatch("observe_manual_floor")["success"])
                state = self.runtime.snapshot()
                self.assertEqual(state["floor"], str(self.raw))
                self.assertIsNone(state["manualFloor"])
                self.assertEqual(state["floorSource"], "automatic-current-frame")
        self.assert_no_live_or_rank()

    def test_stubpixel_missing_and_ambiguous_keep_completed_pose_until_closed(self):
        for reason, opened in (("s1-floor-signed-labels-incomplete", True),
                               ("s1-floor-column-ambiguous", True), ("s1-floor-map-not-open", False)):
            with self.subTest(reason=reason):
                self.runtime.dispatch("end_round"); self.raw, self.opened = -1, True
                self.prepare(); self.pick()
                source = self.runtime._render
                with patch("s1_floor_observation.detect_s1_floor", return_value=stub_pixel_read(None, opened=opened, reason=reason)):
                    result = self.runtime.dispatch("observe_manual_floor")
                self.assertFalse(result["success"], result)
                if opened:
                    self.assertIs(self.runtime._render, source)
                    self.assertIsNotNone(self.runtime.render_payload(source["token"]))
                    self.assertTrue(self.runtime._core.snapshot()["displayAuthorized"])
                else:
                    self.assertIsNone(self.runtime._render)
                    self.assertFalse(self.runtime._core.snapshot()["displayAuthorized"])
                self.assertEqual(self.runtime.snapshot()["observedFloor"], "")

    def test_four_floor_private_eligibility_never_comes_from_the_real_validation_flag(self):
        for floor in (-1, -2, -3, -4):
            with self.subTest(floor=floor):
                self.runtime.dispatch("end_round"); self.raw = -1
                self.prepare(); self.pick()
                source = self.runtime._render
                fits = self.fitter.call_count
                rejected = dict(stub_pixel_read(floor), automaticFloorEligible=False,
                                observedFloorValidated=True)
                with patch("s1_floor_observation.detect_s1_floor", return_value=rejected):
                    result = self.runtime.dispatch("observe_manual_floor")
                self.assertFalse(result["success"], result)
                self.assertEqual(self.runtime.snapshot()["observedFloor"], "")
                self.assertIs(self.runtime._render, source)
                self.assertIsNotNone(self.runtime.render_payload(source["token"]))
                self.assertEqual(self.fitter.call_count, fits)
                self.assert_no_live_or_rank()

    def test_stubpixel_cancellation_while_waiting_for_work_does_not_acquire_or_revoke(self):
        self.prepare(); self.pick()
        old_render, old_group, count = self.runtime._render, self.runtime._group, self.capture.count
        entered, released, cancelled = threading.Event(), threading.Event(), threading.Event()
        original_work = self.runtime._work
        class WorkGate:
            def __enter__(self):
                entered.set()
                if not released.wait(3):
                    raise AssertionError("work gate not released")
                original_work.acquire()
            def __exit__(self, *args):
                original_work.release()
        result = []
        self.runtime._work = WorkGate()
        worker = threading.Thread(target=lambda: result.append(self.runtime.dispatch("fit_manual_frame", cancelled=cancelled)))
        worker.start()
        try:
            self.assertTrue(entered.wait(3)); cancelled.set()
        finally:
            released.set(); worker.join(3); self.runtime._work = original_work
        self.assertFalse(worker.is_alive())
        self.assertFalse(result[0]["success"], result)
        self.assertEqual(self.capture.count, count)
        self.assertIs(self.runtime._render, old_render)
        self.assertIs(self.runtime._group, old_group)

    def test_stubpixel_stale_after_capture_observer_or_fitter_never_commits(self):
        for stage in ("capture", "observer", "fitter"):
            with self.subTest(stage=stage):
                self.runtime.dispatch("end_round"); self.prepare(); self.pick()
                source = self.runtime._render
                cancelled = threading.Event()
                original_capture = self.capture.capture
                def capture(*a, **kw):
                    value = original_capture(*a, **kw); cancelled.set(); return value
                def observe(*a, **kw):
                    cancelled.set(); return stub_pixel_read(-2)
                def fit(*a, **kw):
                    cancelled.set(); return copy.deepcopy(self.fit_result)
                manager = (patch.object(self.capture, "capture", side_effect=capture) if stage == "capture"
                    else patch("s1_floor_observation.detect_s1_floor", side_effect=observe) if stage == "observer"
                    else patch.object(_AlignmentHost, "fit", side_effect=fit))
                with manager:
                    result = self.runtime.dispatch("fit_manual_frame", cancelled=cancelled)
                self.assertFalse(result["success"], result)
                self.assertIs(self.runtime._render, source)
                self.assertIsNotNone(self.runtime.render_payload(source["token"]))
                self.assertTrue(self.runtime._core.snapshot()["displayAuthorized"])

    def test_stubpixel_old_capture_exception_cannot_invalidate_a_new_mode_scope(self):
        for action in ("fit_manual_frame", "confirm_route"):
            with self.subTest(action=action):
                self.runtime.dispatch("end_round")
                self.runtime.dispatch("set_context", dict(season="s1", mode="nightmare"))
                self.registry.engine.pack.mode = "nightmare"
                self.prepare(); self.pick()
                if action == "confirm_route":
                    self.assertTrue(self.runtime.dispatch("reselect_map")["success"])
                new_scope = []
                def capture(*a, **kw):
                    self.runtime.dispatch("end_round")
                    self.runtime.dispatch("set_context", dict(season="s1", mode="difficult"))
                    self.registry.engine.pack.mode = "difficult"
                    self.prepare()
                    self.runtime._message = "new-mode-owned"
                    new_scope.append((self.runtime._epoch, self.runtime._round, self.runtime._engine))
                    raise OSError("old synthetic capture failed after scope replacement")
                with patch.object(self.capture, "capture", side_effect=capture):
                    result = self.runtime.dispatch(action, dict(mapId="fixture-map"))
                self.assertFalse(result["success"], result)
                self.assertEqual((self.runtime._epoch, self.runtime._round, self.runtime._engine), new_scope[0])
                self.assertEqual(self.runtime.snapshot()["mode"], "difficult")
                self.assertTrue(self.runtime.snapshot()["manualAlignmentSession"])
                self.assertEqual(self.runtime._message, "new-mode-owned")

    def test_stubpixel_resource_end_mode_and_window_boundaries_preserve_new_scope(self):
        for boundary in ("resource", "end", "mode", "window"):
            with self.subTest(boundary=boundary):
                self.runtime.dispatch("end_round")
                self.runtime.dispatch("set_context", dict(season="s1", mode="nightmare"))
                self.registry.engine.pack.mode = "nightmare"
                self.prepare(); self.pick()
                self.raw = -2
                expected = []
                def observe(*a, **kw):
                    if boundary == "window":
                        self.capture.generation += 1
                    else:
                        if boundary == "resource":
                            self.runtime.invalidate("resources-changed")
                        else:
                            self.runtime.dispatch("end_round")
                        if boundary == "mode":
                            self.runtime.dispatch("set_context", dict(season="s1", mode="difficult"))
                            self.registry.engine.pack.mode = "difficult"
                        self.prepare()
                    expected.append((self.runtime._epoch, self.runtime._round, self.runtime._mode,
                                     self.runtime._engine, self.runtime._floor))
                    return stub_pixel_read(-2)
                with patch("s1_floor_observation.detect_s1_floor", side_effect=observe):
                    result = self.runtime.dispatch("fit_manual_frame")
                self.assertFalse(result["success"], result)
                self.assertTrue(expected, result)
                self.assertEqual((self.runtime._epoch, self.runtime._round, self.runtime._mode,
                                  self.runtime._engine, self.runtime._floor), expected[0])
                self.assertIsNone(self.runtime._render)
                self.assertFalse(self.runtime._core.snapshot()["displayAuthorized"])
                if boundary != "window":
                    self.assertEqual(self.runtime.snapshot()["observedFloor"], "")
                self.raw = -1

    def test_stubpixel_fitter_return_after_resource_end_mode_or_window_cannot_commit(self):
        for boundary in ("resource", "end", "mode", "window"):
            with self.subTest(boundary=boundary):
                self.runtime.dispatch("end_round")
                self.runtime.dispatch("set_context", dict(season="s1", mode="nightmare"))
                self.registry.engine.pack.mode = "nightmare"
                self.prepare(); self.pick()
                expected = []
                def fit(*a, **kw):
                    if boundary == "window":
                        self.capture.generation += 1
                    else:
                        if boundary == "resource":
                            self.runtime.invalidate("resources-changed")
                        else:
                            self.runtime.dispatch("end_round")
                        if boundary == "mode":
                            self.runtime.dispatch("set_context", dict(season="s1", mode="difficult"))
                            self.registry.engine.pack.mode = "difficult"
                        self.prepare()
                    expected.append((self.runtime._epoch, self.runtime._round, self.runtime._mode,
                                     self.runtime._engine, self.runtime._floor))
                    return copy.deepcopy(self.fit_result)
                with patch.object(_AlignmentHost, "fit", side_effect=fit):
                    result = self.runtime.dispatch("fit_manual_frame")
                self.assertFalse(result["success"], result)
                # A generation-only change may explicitly invalidate the old
                # capture scope; a replaced session must remain untouched.
                if boundary != "window":
                    self.assertEqual((self.runtime._epoch, self.runtime._round, self.runtime._mode,
                                      self.runtime._engine, self.runtime._floor), expected[0])
                self.assertIsNone(self.runtime._render)
                self.assertFalse(self.runtime._core.snapshot()["displayAuthorized"])

    def test_unknown_source_capability_never_calls_new_observer_or_promotes_global_readiness(self):
        self.registry.engine.qt_native_context.floor_observation_capability = dict(
            contract="unknown", manualMapObservation=True, automaticFloors=[-1, -2])
        self.prepare()
        state = self.runtime.snapshot()
        self.assertNotIn("observe_manual_floor", state["availableCommands"])
        self.pick()
        self.pixel.assert_not_called()
        self.assertEqual(self.runtime.snapshot()["observedFloor"], "")
        self.assert_no_live_or_rank()

    def test_stubpixel_same_floor_renews_only_with_current_full_client_residual(self):
        from map_observation import visible_map_component
        self.prepare(); self.pick()
        old = dict(self.runtime._render)
        fit_count = self.fitter.call_count
        self.capture.frame[40:140, 20:120] = 80
        def explicit(frame, **kwargs):
            self.assertEqual(kwargs["observation_bounds"], (0, 0, 1280, 720))
            return visible_map_component(frame, **kwargs)
        residual = copy.deepcopy(self.fit_result["selectedRouteCurrentFrameResidual"])
        with patch("map_observation.visible_map_component", side_effect=explicit), \
             patch("nightmare_candidate.visible_map_component", side_effect=AssertionError("legacy extractor")), \
             patch.object(_AlignmentHost, "_canonical_geometry_current_frame_residual", return_value=(residual, None)) as check:
            result = self.runtime.dispatch("observe_manual_floor")
        self.assertTrue(result["success"], result)
        self.assertEqual(result["reason"], "current-pose-refreshed")
        self.assertEqual(self.fitter.call_count, fit_count)
        self.assertEqual(self.runtime._render["token"], old["token"])
        self.assertNotEqual(self.runtime._render["frameId"], old["frameId"])
        self.assertIsNotNone(check.call_args.kwargs["visible_override"])
        self.assert_no_live_or_rank()

    def test_stubpixel_floor_provenance_agrees_in_snapshot_first_fit_and_same_floor_refresh(self):
        self.capture.frame[40:140, 20:120] = 80
        residual = copy.deepcopy(self.fit_result["selectedRouteCurrentFrameResidual"])
        for floor in (-1, -2, -3, -4):
            with self.subTest(floor=floor):
                self.runtime.dispatch("end_round")
                self.raw, self.opened = floor, True
                self.prepare()
                self.pick()
                expected = "automatic-current-frame"
                self.assertEqual(self.runtime.snapshot()["floorSource"], expected)
                self.assertEqual(self.runtime._render["floorSource"], expected)
                first_token = self.runtime._render["token"]
                with patch.object(_AlignmentHost, "_canonical_geometry_current_frame_residual", return_value=(residual, None)):
                    result = self.runtime.dispatch("observe_manual_floor")
                self.assertTrue(result["success"], result)
                self.assertEqual(result["reason"], "current-pose-refreshed")
                self.assertEqual(self.runtime._render["token"], first_token)
                self.assertEqual(self.runtime.snapshot()["floorSource"], expected)
                self.assertEqual(self.runtime._render["floorSource"], expected)
                self.assert_no_live_or_rank()

    def one_probe_preparation(self, host, hwnd, token, deadline, cancelled, **kwargs):
        """Run the real coordinator and host once without real-time sleeping."""
        host.CAPTURE_VISUAL_DEADLINE_SECONDS = host.CAPTURE_READY_PROBE_SECONDS = .05
        return self.real_prepare(host, hwnd, token, time.perf_counter()+1, cancelled, **kwargs)

    def binding_resume(self, opened, sequence=None):
        from map_open_preparation import prepare_map_open_frame
        self.real_prepare = prepare_map_open_frame
        self.raw, self.opened = None, opened
        sequence = self.runtime._last_map_input_sequence+1 if sequence is None else sequence
        with patch("map_open_preparation.prepare_map_open_frame", side_effect=self.one_probe_preparation):
            return self.runtime.dispatch("fit_manual_frame", dict(s1OpenEdge=True,
                inputSequence=sequence, bindingResume=True))

    def test_stubpixel_binding_resume_bounded_timeout_has_unknown_visual_receipt(self):
        for opened in (None, False):
            with self.subTest(opened=opened):
                self.runtime.dispatch("end_round"); self.raw, self.opened = -1, True
                self.prepare(); self.pick()
                before = self.capture.count
                result = self.binding_resume(opened)
                self.assertFalse(result["success"], result)
                self.assertEqual(self.capture.count, before+1)
                visual = result["mapInputObservation"]
                # During preparation even False can be a transient dark/fade
                # frame. The bounded absence is explicitly unknown.
                self.assertIsNone(visual["mapOpen"])
                self.assertTrue(self.runtime.map_input_observation_is_current(visual))
                self.assertIsNone(self.runtime._render)
                self.assertFalse(self.runtime._core.snapshot()["displayAuthorized"])
                self.assertTrue(result["recoveryPending"])
                self.assertTrue(self.runtime.map_open_recovery_status()["pending"])

    def test_stubpixel_direct_closed_observation_returns_current_false_visual_receipt(self):
        self.prepare(); self.pick()
        self.raw, self.opened = None, False
        result = self.runtime.dispatch("observe_manual_floor")
        self.assertFalse(result["success"], result)
        self.assertEqual(result["reason"], "s1-map-closed")
        self.assertIs(result["mapInputObservation"]["mapOpen"], False)
        self.assertTrue(self.runtime.map_input_observation_is_current(result["mapInputObservation"]))
        self.assertFalse(self.runtime.map_open_recovery_status()["pending"])
        self.assertIsNone(self.runtime._render)

    def test_stubpixel_recovery_cancel_and_expiry_after_acquisition_cannot_authorize(self):
        for boundary in ("cancel-before", "cancel-after-capture", "expiry-after-capture", "expiry-after-observer"):
            with self.subTest(boundary=boundary):
                self.runtime.dispatch("end_round"); self.raw, self.opened = -1, True
                self.prepare(); self.pick()
                self.assertTrue(self.binding_resume(None)["recoveryPending"])
                row = self.runtime._map_recovery
                self.now = row["nextAt"]+.001
                self.raw, self.opened = -2, True
                before = self.capture.count
                cancelled = threading.Event()
                capture_original = self.capture.capture
                def captured(*a, **kw):
                    value = capture_original(*a, **kw)
                    if boundary == "cancel-after-capture":
                        self.runtime.cancel_map_open_recovery("test-cancelled")
                    elif boundary == "expiry-after-capture":
                        self.now = row["expiresAt"]+.001
                    return value
                def observe(*a, **kw):
                    self.now = row["expiresAt"]+.001
                    return stub_pixel_read(-2)
                if boundary == "cancel-before":
                    cancelled.set()
                with patch.object(self.capture, "capture", side_effect=captured), \
                     patch("s1_floor_observation.detect_s1_floor", side_effect=observe if boundary == "expiry-after-observer" else
                           lambda *a, **kw: stub_pixel_read(-2)):
                    result = self.runtime.dispatch("observe_manual_floor", dict(recoveryId=row["id"]), cancelled)
                self.assertFalse(result["success"], result)
                self.assertEqual(self.capture.count, before+(boundary != "cancel-before"))
                self.assertIsNone(self.runtime._render)
                self.assertEqual(self.runtime.snapshot()["observedFloor"], "")
                self.assertFalse(self.runtime._core.snapshot()["displayAuthorized"])

    def test_stubpixel_consumed_recovery_ticket_does_not_expire_an_already_proven_fit(self):
        self.prepare(); self.pick()
        self.assertTrue(self.binding_resume(None)["recoveryPending"])
        row = self.runtime._map_recovery
        self.now = row["nextAt"]+.001
        self.raw, self.opened = -2, True
        def fit(*a, **kw):
            self.assertIsNone(self.runtime._map_recovery)
            self.now = row["expiresAt"]+.001
            return copy.deepcopy(self.fit_result)
        with patch.object(_AlignmentHost, "fit", side_effect=fit):
            result = self.runtime.dispatch("observe_manual_floor", dict(recoveryId=row["id"]))
        self.assertTrue(result["success"], result)
        self.assertEqual(self.runtime.snapshot()["observedFloor"], "-2")
        self.assert_no_live_or_rank()

    def test_synthetic_glyph_and_independent_canvas_gate_reach_real_manual_consumer(self):
        self.prepare()
        self.fit_result["observationComponent"].update(bounds=(0, 0, 1920, 1200), center=(960, 600),
            width=1920, height=1200, area=2304000)
        with patch("s1_floor_observation.detect_s1_floor", side_effect=self.original_detector):
            for floor in (-1, -2, -3, -4):
                with self.subTest(floor=floor):
                    self.capture.frame = mathematical_frame((floor,))
                    if floor == -1:
                        self.pick()
                    else:
                        result = self.runtime.dispatch("observe_manual_floor")
                        self.assertTrue(result["success"], result)
                    state = self.runtime.snapshot()
                    self.assertEqual((state["floor"], state["observedFloor"]), (str(floor), str(floor)))
                    read = self.runtime._last_observation["floor_read"]
                    self.assertTrue(read["mapObservation"]["detected"])
                    self.assertTrue(read["observedFloorValidated"])
                    self.assertTrue(read["automaticFloorEligible"])
                    self.assertEqual(self.runtime._render["clientWidth"], 1920)
                    self.assertEqual(self.runtime._render["clientHeight"], 1200)
                    self.assert_no_live_or_rank()


if __name__ == "__main__":
    unittest.main()
