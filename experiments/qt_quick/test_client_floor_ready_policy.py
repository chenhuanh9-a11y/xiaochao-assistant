"""Current-frame map-ready evidence reaches floor policy, not display authority.

Real dispatch/preparation/observation/floor-policy/core chain; capture, pixel
sensors and solver are explicit synthetic boundaries (not live-game evidence).
"""
import copy
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_client_live_runtime as fixture
from client_live_runtime import _AlignmentHost


class FloorReadyPolicyTests(unittest.TestCase):
    def setUp(self):
        self.live = fixture.LiveRuntimeContracts()
        self.live.setUp()
        self.addCleanup(self.live.doCleanups)
        self.runtime = self.live.runtime
        other = SimpleNamespace(id="fixture-other")
        self.live.engine.pack.maps.append(other)
        self.live.engine.pack.by_id[other.id] = other
        self.live.capture.generation = 1
        self.live.capture.is_current = lambda context, generation=None: (
            generation == self.live.capture.generation and context == self.capture_context)
        self.capture_context = fixture.ClientCaptureContext((1280, 720), (100, 200, 1380, 920), 96)

    def evidence(self, floor=1, margin=8., backdrop=.80):
        return (dict(detected=False, reason="map-canvas-texture-mismatch", flatMapCanvas=False,
                     shapeAccepted=True, layoutFamily="wide", matchedAnchors=4,
                     neutralMatchedAnchors=4, backdropRatio=backdrop),
                dict(selectedFloor=floor, layout="localized-triplet", margin=margin,
                     centers=[.45, .50, .55], scores={str(floor): 40.}))

    def open_with(self, observation, floor_read, **kwargs):
        with patch("map_interface_observation.detect_map_interface", return_value=observation), \
             patch("map_interface_observation.detect_selected_floor", return_value=floor_read):
            return self.live.dispatch("map", **kwargs)

    def test_existing_ready_boundaries_reach_floor_policy_for_b1_f1_f2(self):
        for floor in (-1, 1, 2):
            for margin, backdrop in ((8., .80), (12., .72)):
                with self.subTest(floor=floor, margin=margin):
                    self.live.dispatch("end_round")
                    self.live.dispatch("start_round")
                    result = self.open_with(*self.evidence(floor, margin, backdrop))
                    self.assertTrue(result["success"], result)
                    self.assertEqual(self.runtime.snapshot()["floor"], "B1" if floor == -1 else str(floor))
                    self.assertIsNotNone(self.runtime._last_observation)

    def test_prepare_independently_rejects_weak_floor_and_bare_map_ready(self):
        cases = [self.evidence(floor, margin, backdrop)
                 for floor in (-1, 1, 2)
                 for margin, backdrop in ((7.99, .80), (11.99, .72))]
        cases += [(dict(detected=False, mapReady=True), dict(selectedFloor=None)),
                  (dict(self.evidence()[0], mapReady=True), dict(self.evidence()[1], margin=7.99))]
        self.live.dispatch("start_round")
        for observation, floor_read in cases:
            with self.subTest(floor=floor_read, observation=observation):
                result = self.open_with(observation, floor_read)
                self.assertEqual(result["reason"], "map-open-preparation-deadline")
                self.assertFalse(self.runtime.snapshot()["displayAuthorized"])
        self.assertEqual(self.live.engine.rank_count, 0)

    def test_ready_does_not_bypass_current_frame_solver_rejection(self):
        self.live.dispatch("start_round")
        with patch.object(_AlignmentHost, "fit", return_value=dict(success=False, reason="current-geometry-failed")):
            result = self.open_with(*self.evidence())
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "current-geometry-failed")
        self.assertFalse(self.runtime.snapshot()["displayAuthorized"])
        self.assertIsNone(self.runtime._render)

    def test_superseded_prepare_never_mutates_floor_policy_or_authorizes_pixels(self):
        for mutation in ("close", "floor", "map", "end", "cancel", "engine", "group", "generation", "context"):
            with self.subTest(mutation=mutation):
                self.live.dispatch("end_round")
                self.live.begin_and_capture()
                self.live.dispatch("close_map")
                original = self.runtime._full_frame
                calls = 0
                cancelled = threading.Event()
                def after_preparation(captured):
                    nonlocal calls
                    frame = original(captured)
                    calls += 1
                    if calls == 2:  # Original prepare consumed this exact first full frame.
                        if mutation == "close": self.live.dispatch("close_map")
                        elif mutation == "floor": self.live.dispatch("set_floor", floor="1")
                        elif mutation == "map": self.live.dispatch("confirm_route", mapId="fixture-other")
                        elif mutation == "end": self.live.dispatch("end_round")
                        elif mutation == "cancel": cancelled.set()
                        elif mutation == "engine": self.runtime._engine = copy.copy(self.runtime._engine)
                        elif mutation == "group": self.runtime._group = copy.copy(self.runtime._group)
                        elif mutation == "generation": self.live.capture.generation += 1
                        elif mutation == "context": self.capture_context = fixture.ClientCaptureContext((1280, 720), (101, 200, 1381, 920), 96)
                    return frame
                observation, floor_read = self.evidence(2)
                with patch.object(self.runtime, "_full_frame", side_effect=after_preparation), \
                     patch.object(self.runtime, "_observe_floor_policy", wraps=self.runtime._observe_floor_policy) as policy, \
                     patch("map_interface_observation.detect_map_interface", return_value=observation), \
                     patch("map_interface_observation.detect_selected_floor", return_value=floor_read):
                    result = self.runtime.dispatch("map", {}, cancelled)
                self.assertFalse(result["success"], result)
                self.assertTrue(result.get("stale"), result)
                policy.assert_not_called()
                self.assertFalse(self.runtime.snapshot()["displayAuthorized"])
                if mutation == "map":
                    self.assertEqual(self.runtime.snapshot()["mapId"], "fixture-other")
                if mutation == "floor":
                    self.assertEqual(self.runtime.snapshot()["floor"], "1")
                self.capture_context = fixture.ClientCaptureContext((1280, 720), (100, 200, 1380, 920), 96)

    def test_helper_ready_late_solver_result_cannot_reopen_after_close_or_end(self):
        for action in ("close_map", "end_round"):
            with self.subTest(action=action):
                self.live.dispatch("start_round")
                def late_fit(*args, **kwargs):
                    self.live.dispatch(action)
                    return copy.deepcopy(self.live.fit)
                with patch.object(_AlignmentHost, "fit", side_effect=late_fit):
                    result = self.open_with(*self.evidence())
                self.assertTrue(result.get("stale"), result)
                self.assertFalse(self.runtime.snapshot()["displayAuthorized"])
                self.assertIsNone(self.runtime._render)

    def test_region_remains_a_floor_reader_only_after_generic_map_proof_in_both_modes(self):
        from test_client_floor_region import hud, REGION
        for mode, floors in (("nightmare", (-1, 1, 2)), ("difficult", (1, 2))):
            with self.subTest(mode=mode):
                self.live.dispatch("end_round")
                self.live.dispatch("set_context", mode=mode, profileId="fixture")
                self.runtime.configure_settings(dict(floorControlMode="region",
                    floorControlRegion=dict(rect=REGION, clientSize=[1920, 1080])))
                self.capture_context = fixture.ClientCaptureContext((1920, 1080), (0, 0, 1920, 1080), 96)
                frame = hud(floors, 1)
                self.live.capture.capture = lambda cancelled=None: fixture.RegionCapture(frame.copy(),
                    (0, 0, 1920, 1080), (0, 0, 1920, 1080), self.capture_context, "synthetic-fixture")
                self.live.dispatch("start_round")
                if mode == "difficult":
                    self.runtime._engine.qt_map_ui.observe = lambda frame: dict(mapOpen=True, floor=None, glyphs=[], reason="fixture-map-open")
                result = self.open_with(dict(detected=True), dict(selectedFloor=None, layout="unlocalized"))
                self.assertTrue(result["success"], result)
                self.assertEqual(self.runtime.snapshot()["floor"], "1")
                self.assertEqual(self.runtime._last_observation["floor_read"]["floorSource"], "region")

    def test_normal_both_modes_and_manual_selection_keep_existing_behavior(self):
        for mode in ("nightmare", "difficult"):
            for manual in (False, True):
                with self.subTest(mode=mode, manual=manual):
                    self.live.dispatch("end_round")
                    self.live.dispatch("set_context", mode=mode, profileId="fixture")
                    self.runtime.configure_settings(dict(mapSelectionMode="manual" if manual else "auto"))
                    self.live.dispatch("start_round")
                    result = self.live.dispatch("map")
                    self.assertTrue(result["success"], result)
                    self.assertEqual(result["reason"], "manual-map-selection-pending" if manual else "current-frame-pose-authorized")
                    self.assertEqual(self.runtime.snapshot()["displayAuthorized"], not manual)


if __name__ == "__main__":
    unittest.main()
