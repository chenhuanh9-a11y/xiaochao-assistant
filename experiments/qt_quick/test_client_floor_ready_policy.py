"""Current-frame map-ready evidence reaches floor policy, not display authority.

Real dispatch/preparation/observation/floor-policy/core chain; capture, pixel
sensors and solver are explicit synthetic boundaries (not live-game evidence).
"""
import copy
import threading
import map_open_preparation
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_client_live_runtime as fixture
from client_live_runtime import _AlignmentHost
from s0_test_fixtures import synthetic_read


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

    def evidence(self, floor=1):
        return (dict(detected=True, reason="independent-map-proof"),
                synthetic_read(selected=floor))

    def open_with(self, observation, floor_read, **kwargs):
        with patch("map_interface_observation.detect_map_interface", return_value=observation), \
             patch("floor_control_region.locate_vertical_floor_controls", return_value=floor_read):
            return self.live.dispatch("map", **kwargs)

    def test_independent_map_and_vertical_read_reach_floor_policy_for_b1_f1_f2(self):
        for floor in (-1, 1, 2):
            with self.subTest(floor=floor):
                self.live.dispatch("end_round")
                self.live.dispatch("start_round")
                result = self.open_with(*self.evidence(floor))
                self.assertTrue(result["success"], result)
                self.assertEqual(self.runtime.snapshot()["floor"], "B1" if floor == -1 else str(floor))
                self.assertIsNotNone(self.runtime._last_observation)

    def test_prepare_independently_rejects_closed_tint_and_bare_map_ready(self):
        cases = [(dict(detected=False, reason=reason), synthetic_read(selected=floor))
                 for floor in (-1, 1, 2)
                 for reason in ("map-ui-not-observed", "map-canvas-texture-mismatch")]
        cases += [(dict(detected=False, mapReady=True), dict(selectedFloor=None)),
                  (dict(detected=True), dict(selectedFloor=None))]
        self.live.dispatch("start_round")
        for observation, floor_read in cases:
            with self.subTest(floor=floor_read, observation=observation):
                result = self.open_with(observation, floor_read)
                self.assertEqual(result["reason"], "current-floor-unavailable" if observation.get("detected") else "map-open-preparation-deadline")
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
                original = map_open_preparation.prepare_map_open_frame
                cancelled = threading.Event()
                def after_preparation(*args, **kwargs):
                    prepared = original(*args, **kwargs)
                    self.assertIsNotNone(prepared)
                    if mutation == "close": self.live.dispatch("close_map")
                    elif mutation == "floor": self.live.dispatch("set_floor", floor="1")
                    elif mutation == "map": self.live.dispatch("confirm_route", mapId="fixture-other")
                    elif mutation == "end": self.live.dispatch("end_round")
                    elif mutation == "cancel": cancelled.set()
                    elif mutation == "engine": self.runtime._engine = copy.copy(self.runtime._engine)
                    elif mutation == "group": self.runtime._group = copy.copy(self.runtime._group)
                    elif mutation == "generation": self.live.capture.generation += 1
                    elif mutation == "context": self.capture_context = fixture.ClientCaptureContext((1280, 720), (101, 200, 1381, 920), 96)
                    return prepared
                observation, floor_read = self.evidence(2)
                with patch("map_open_preparation.prepare_map_open_frame", side_effect=after_preparation), \
                     patch.object(self.runtime, "_observe_floor_policy", wraps=self.runtime._observe_floor_policy) as policy, \
                     patch("map_interface_observation.detect_map_interface", return_value=observation), \
                     patch("floor_control_region.locate_vertical_floor_controls", return_value=floor_read):
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

    def test_retired_region_setting_keeps_vertical_sensor_and_independent_map_proof(self):
        for mode in ("nightmare", "difficult"):
            with self.subTest(mode=mode):
                self.live.dispatch("end_round")
                self.live.dispatch("set_context", mode=mode, profileId="fixture")
                self.runtime.configure_settings(dict(floorControlMode="region",
                    floorControlRegion=dict(rect=[.1, .1, .2, .2], clientSize=[1280, 720])))
                self.live.dispatch("start_round")
                result = self.open_with(dict(detected=True), synthetic_read(mode))
                self.assertTrue(result["success"], result)
                self.assertEqual(self.runtime.snapshot()["floor"], "1")
                read = self.runtime._last_observation["floor_read"]
                self.assertEqual(read["layout"], "localized-s0-vertical")
                self.assertTrue(read["controlGeometryVerified"])

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
