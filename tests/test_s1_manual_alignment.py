"""Manual ingress contracts with synthetic pixels; no game or native IO."""
import threading
import unittest
from unittest.mock import Mock, patch

import numpy as np
from client_live_runtime import LiveRuntime
from test_client_live_runtime import FixtureCapture, fixture_engine
from season_context import modes_for_season


class Registry(dict):
    def __init__(self):
        super().__init__()
        self.engine = fixture_engine()
        self.engine.pack.floors = self.engine.identity_floors = ("-1", "-2", "-3", "-4")
        self.engine.pack.default_entry_floor = "-1"
        self.engine.qt_live_ready = False

    def capabilities(self, season="s0"):
        return {mode: dict(available=True, prepared=True, reason="saved-frame-rank-ready",
            liveReady=season == "s0", liveGaps=[] if season == "s0" else ["s1-floor-controls-unregistered"])
            for mode in modes_for_season(season)}

    def get_engine(self, mode, season):
        assert season == "s1"
        return self.engine

    def prepare(self, *args, season="s0"):
        return self.capabilities(season)


class ManualAlignmentContracts(unittest.TestCase):
    def setUp(self):
        self.registry, self.capture = Registry(), FixtureCapture()
        self.runtime = LiveRuntime(engine_factories=self.registry, capture_provider=self.capture,
                                   evidence_kind="synthetic")
        self.runtime.configure_settings(dict(mapSelectionMode="auto"))
        self.runtime.dispatch("set_context", dict(season="s1", mode="nightmare"))
        self.addCleanup(self.runtime.shutdown)

    def prepare(self):
        result = self.runtime.dispatch("prepare_manual_alignment")
        self.assertTrue(result["success"], result)

    def test_manual_session_does_not_enable_auto_or_change_preference(self):
        self.assertIn("prepare_manual_alignment", self.runtime.snapshot()["availableCommands"])
        self.prepare()
        state = self.runtime.snapshot()
        self.assertTrue(state["manualAlignmentSession"])
        self.assertTrue(state["manualMapPickAvailable"])
        self.assertFalse(state["liveEngineAvailable"])
        self.assertEqual(state["mapSelectionMode"], "auto")
        self.assertEqual(self.capture.count, 0)
        for command in ("start_round", "map", "capture_now", "refresh_map"):
            self.assertFalse(self.runtime.dispatch(command)["success"])
        self.assertEqual(self.registry.engine.rank_count, 0)

    def test_pick_captures_once_without_detector_or_identity_rank(self):
        self.prepare()
        with patch.object(self.runtime, "_observe", side_effect=AssertionError("automatic detector")), \
             patch.object(self.runtime, "_solve_observation", return_value=dict(success=False, reason="geometry-rejected")) as fit:
            result = self.runtime.dispatch("confirm_route", dict(mapId="fixture-map"))
        self.assertEqual(result["reason"], "geometry-rejected")
        self.assertEqual(self.capture.count, 1)
        self.assertEqual(fit.call_args.args[-1], "fixture-map")
        state = self.runtime.snapshot()
        self.assertEqual((state["floor"], state["observedFloor"]), ("-1", ""))
        self.assertFalse(state["displayAuthorized"])
        self.assertFalse(state["liveAuthorization"])
        self.assertEqual(self.registry.engine.rank_count, 0)
        self.assertEqual(self.runtime._last_observation["floor_read"]["selectedFloor"], None)

    def test_software_floor_change_recaptures_and_discards_old_group(self):
        self.prepare()
        with patch.object(self.runtime, "_solve_observation", return_value=dict(success=False, reason="geometry-rejected")):
            self.runtime.dispatch("confirm_route", dict(mapId="fixture-map"))
            self.runtime._group = object()
            self.runtime.dispatch("set_floor", dict(floor="-2"))
        self.assertIsNone(self.runtime._group)
        self.assertEqual(self.capture.count, 2)
        self.assertEqual(self.runtime.snapshot()["manualFloor"], "-2")
        for floor in ("1", "B1", "-5", "auto"):
            self.assertFalse(self.runtime.dispatch("set_floor", dict(floor=floor))["success"])
        self.assertEqual(self.capture.count, 2)

    def test_late_capture_after_floor_change_cannot_fit(self):
        self.prepare()
        entered, release = threading.Event(), threading.Event()
        original = self.capture.capture
        def capture(cancelled=None):
            entered.set()
            self.assertTrue(release.wait(3))
            return original(cancelled)
        result = []
        with patch.object(self.capture, "capture", side_effect=capture), \
             patch.object(self.runtime, "_solve_observation") as fit:
            worker = threading.Thread(target=lambda: result.append(
                self.runtime.dispatch("confirm_route", dict(mapId="fixture-map"))))
            worker.start()
            try:
                self.assertTrue(entered.wait(3))
                self.runtime.invalidate("floor-changed")
                self.runtime._manual_floor = self.runtime._floor = "-2"
            finally:
                release.set(); worker.join(3)
            self.assertFalse(worker.is_alive())
            fit.assert_not_called()
        self.assertTrue(result[0]["stale"])
        self.assertFalse(self.runtime.snapshot()["displayAuthorized"])

    def test_full_client_domain_is_explicit_and_threshold_is_unchanged(self):
        from single_map_structure import current_geometry
        frame = np.zeros((720, 1280, 3), np.uint8)
        frame[40:140, 20:120] = 80
        self.assertIsNone(current_geometry(frame))
        self.assertIsNotNone(current_geometry(frame, observation_bounds=(0, 0, 1280, 720)))
        frame[40:140, 20:120] = 74
        self.assertIsNone(current_geometry(frame, observation_bounds=(0, 0, 1280, 720)))

    def test_negative_floor_core_contract_is_local_and_s0_default_unchanged(self):
        from alignment_core_v2 import AlignmentCoreV2, RoundPose
        from round_layer_group import RoundLayerGroup
        for cls, field in ((RoundPose, "root_matrix"), (RoundLayerGroup, "root_pose")):
            with self.assertRaises(ValueError):
                cls("map", "shared-structure", "-1", np.eye(3))
            item = cls("map", "shared-structure", "-1", np.eye(3), floors=("-1", "-2", "-3", "-4"))
            self.assertEqual(item.anchor_floor, "-1")
        with self.assertRaises(ValueError):
            AlignmentCoreV2().begin_observation(cycle_id=1, map_id="map", floor="-1", frame_generation=1)

    def test_empty_manual_geometry_cannot_fall_back_to_legacy_extractor(self):
        from client_live_runtime import _AlignmentHost
        from nightmare_candidate import visible_map_component
        frame = np.zeros((720, 1280, 3), np.uint8)
        def explicit(frame, **kwargs):
            self.assertEqual(kwargs["observation_bounds"], (0, 0, 1280, 720))
            return visible_map_component(frame, **kwargs)
        with patch("map_observation.visible_map_component", side_effect=explicit), \
             patch("nightmare_candidate.visible_map_component", side_effect=AssertionError("legacy extraction")), \
             patch("fast_structure_alignment.try_alignment") as fast:
            result = _AlignmentHost(self.registry.engine).fit(frame, "fixture-map", "-1", lambda: False,
                observation_bounds=(0, 0, 1280, 720))
        self.assertEqual(result["reason"], "manual-current-geometry-unavailable")
        fast.assert_not_called()

    def test_rejected_capture_enable_leaves_a_retryable_idle_session(self):
        self.capture.enable = Mock(return_value=False)
        self.assertFalse(self.runtime.dispatch("prepare_manual_alignment")["success"])
        state = self.runtime.snapshot()
        self.assertFalse(state["manualAlignmentSession"])
        self.assertFalse(state["roundPreparing"])
        self.assertIn("prepare_manual_alignment", state["availableCommands"])

    def test_after_fit_boundaries_cannot_commit_a_manual_pose(self):
        from client_live_runtime import _AlignmentHost
        for boundary in ("cancel", "resource", "window", "closed"):
            with self.subTest(boundary=boundary):
                registry, capture = Registry(), FixtureCapture()
                capture.generation = 1
                capture.is_current = lambda context, *, generation: generation == capture.generation
                runtime = LiveRuntime(engine_factories=registry, capture_provider=capture, evidence_kind="synthetic")
                runtime.dispatch("set_context", dict(season="s1", mode="nightmare"))
                self.assertTrue(runtime.dispatch("prepare_manual_alignment")["success"])
                cancelled = threading.Event()
                display = dict(pixels=np.zeros((20, 24, 4), np.uint8), resourceIdentity="fixture",
                    displayKind="shared-structure", displayLabel="fixture", routeProfileId="shared-structure",
                    matrixDisplayToReference=np.eye(3))
                def finish_fit(*args, **kwargs):
                    if boundary == "cancel":
                        cancelled.set()
                    elif boundary == "resource":
                        runtime.invalidate("resources-changed")
                    elif boundary == "window":
                        capture.generation += 1
                    else:
                        runtime.invalidate("map-closed")
                    return dict(success=True, matrixSourceToClient=np.eye(3),
                        observationComponent=dict(bounds=(0, 0, 1280, 720), center=(640, 360),
                            x=0, y=0, width=1280, height=720, area=921600),
                        selectedRouteCurrentFrameResidual=dict(verified=True,
                            poseVerifiedAgainstCanonicalGeometry=True, profileRenderDerivedFromCanonical=True,
                            coordinatePackId="fixture-pack", validationBasis="shared-canonical-geometry-current-frame"))
                try:
                    with patch.object(runtime, "_display_layer", return_value=display), \
                         patch.object(_AlignmentHost, "fit", side_effect=finish_fit):
                        result = runtime.dispatch("confirm_route", dict(mapId="fixture-map"), cancelled)
                    self.assertFalse(result["success"], result)
                    self.assertIsNone(runtime._render)
                    self.assertIsNone(runtime._group)
                    self.assertIsNone(runtime._core.active_pose)
                    self.assertFalse(runtime.snapshot()["displayAuthorized"])
                finally:
                    runtime.shutdown()


if __name__ == "__main__":
    unittest.main()
