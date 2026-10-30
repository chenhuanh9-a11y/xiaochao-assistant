"""Lifecycle/core contracts with explicit synthetic capture and solver boundaries.

The actual saved-frame algorithm chain is recorded separately in ignored work/.
These deterministic tests distribute no game/author assets and start no native IO.
"""
import copy
import json
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from client_live_runtime import LiveRuntime, _AlignmentHost
from client_region_capture import ClientCaptureContext, RegionCapture
from shared_structure_reference import SharedStructureReference


class FixtureCapture:
    def __init__(self):
        self.enabled, self.count, self.enable_count = False, 0, 0
        self.frame = np.zeros((720, 1280, 3), np.uint8)
        self.roi = (0, 0, 1280, 720)
    @property
    def snapshot(self):
        return dict(enabled=self.enabled, hwnd="synthetic-fixture", region=list(self.roi))
    def bind(self, hwnd, region):
        self.disable(); self.roi = tuple(region)
    def enable(self):
        self.enabled = True; self.enable_count += 1; return True
    def disable(self):
        self.enabled = False
    def shutdown(self):
        self.disable()
    def capture(self, cancelled=None):
        assert self.enabled
        self.count += 1
        return RegionCapture(self.frame.copy(), self.roi, self.roi,
            ClientCaptureContext((1280, 720), (100, 200, 1380, 920), 96), "synthetic-fixture")


def fixture_engine(mode="nightmare"):
    binding = dict(profile="fixture", mapId="fixture-map", floor="1", variant="variant-01")
    reference = SharedStructureReference(np.ones((20, 24), np.uint8), np.ones((20, 24), np.uint8),
        dict(binding=binding, canvas=dict(shapeHW=[20, 24], coordinateSpace="fixture-pixels"),
             structureVersionKey="fixture-version"), Path("fixture.npz"))
    engine = SimpleNamespace(pack=SimpleNamespace(mode=mode, maps=[SimpleNamespace(id="fixture-map")]),
        identity_floors=("1", "2", "B1") if mode == "nightmare" else ("1", "2"),
        qt_adapters=SimpleNamespace(canonical_pack_id="fixture-pack", matrix_profile_to_canonical=lambda *a: np.eye(3)),
        qt_shared_provider=lambda *a: reference, qt_optional_displays=SimpleNamespace(binding=lambda *a: None),
        rank_count=0)
    def rank(*args, **kwargs):
        engine.rank_count += 1
        return [dict(mapId="fixture-map", identityHardGatePassed=True)]
    engine.rank = rank
    engine.qt_map_ui = SimpleNamespace(observe=lambda *a: dict(mapOpen=True, floor="1"))
    return engine


class LiveRuntimeContracts(unittest.TestCase):
    def setUp(self):
        self.capture = FixtureCapture()
        self.engine = fixture_engine()
        self.runtime = LiveRuntime(engine_factories={"nightmare": lambda: self.engine,
            "difficult": lambda: fixture_engine("difficult")}, capture_provider=self.capture,
            evidence_kind="synthetic", clock=lambda: 1)
        self.fit = dict(success=True, matrixSourceToClient=np.array([[.5,0,40],[0,.5,60],[0,0,1]]),
            selectedRouteCurrentFrameResidual=dict(verified=True, poseVerifiedAgainstCanonicalGeometry=True,
                profileRenderDerivedFromCanonical=True, coordinatePackId="fixture-pack",
                validationBasis="shared-canonical-geometry-current-frame"))
        self.patches = [patch("map_interface_observation.detect_map_interface", return_value=dict(detected=True)),
                        patch("map_interface_observation.detect_selected_floor", return_value=dict(selectedFloor=1)),
                        patch.object(_AlignmentHost, "fit", side_effect=lambda *a: copy.deepcopy(self.fit))]
        for item in self.patches:
            item.start(); self.addCleanup(item.stop)

    def dispatch(self, action, **payload):
        state = self.runtime.snapshot()
        context = {key: state[key] for key in ("mode", "profileId", "roundId", "frameId")}
        context.update(generation=22, **payload)
        return self.runtime.dispatch(action, context)

    def begin_and_capture(self):
        self.assertTrue(self.dispatch("start_round")["success"])
        result = self.dispatch("capture_now")
        self.assertTrue(result["success"], result)
        return result

    def test_import_constructor_and_start_do_not_capture_or_load_ui(self):
        self.assertEqual(self.capture.enable_count, 0)
        self.assertEqual(self.capture.count, 0)
        self.assertTrue(self.dispatch("start_round")["success"])
        self.assertEqual(self.capture.enable_count, 1)
        self.assertEqual(self.capture.count, 0)
        code = ("import sys; from client_live_runtime import LiveRuntime; "
                "a=LiveRuntime(engine_factories={}); "
                "assert not ({'tkinter','identityv_daily_assistant','identityv_live_mvp','client_capture'} & set(sys.modules)); "
                "assert 'capture_now' not in a.snapshot()['availableCommands']")
        subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).parent,
                       check=True, text=True, capture_output=True)

    def test_request_and_result_contexts_render_binding_and_synthetic_authority(self):
        result = self.begin_and_capture()
        self.assertEqual(result["requestContext"]["generation"], 22)
        self.assertEqual(result["requestContext"]["frameId"], "")
        state = self.runtime.snapshot()
        self.assertEqual(result["resultContext"], {key: state[key] for key in ("mode", "profileId", "roundId", "frameId")})
        render = self.runtime.render_payload(result["renderToken"])
        self.assertEqual(render["originX"], 100)
        self.assertEqual(render["originY"], 200)
        self.assertEqual(render["pixels"].shape, (20, 24, 4))
        self.assertFalse(render["pixels"].flags.writeable)
        self.assertFalse(render["liveAuthorization"])
        self.assertTrue(state["core"]["displayAuthorized"])
        self.assertFalse(state["liveAuthorization"])
        self.assertNotIn("pixels", state["render"])
        json.dumps(state, allow_nan=False)

    def test_close_reopen_and_end_revoke_old_tokens_and_keep_identity_lock(self):
        first = self.begin_and_capture()
        self.assertEqual(self.engine.rank_count, 1)
        pose = self.runtime.snapshot()["core"]["poses"]
        self.dispatch("close_map")
        self.assertIsNone(self.runtime.render_payload(first["renderToken"]))
        self.assertEqual(self.runtime.snapshot()["core"]["poses"], pose)
        second = self.dispatch("capture_now")
        self.assertTrue(second["success"], second)
        self.assertEqual(self.engine.rank_count, 1)
        self.assertEqual(self.runtime.snapshot()["cycleId"], 2)
        self.dispatch("end_round")
        self.assertIsNone(self.runtime.render_payload(second["renderToken"]))
        self.assertEqual(self.runtime.snapshot()["core"]["poses"], {})
        self.assertFalse(self.capture.enabled)

    def test_profile_change_and_identical_settings_preserve_structure_only(self):
        first = self.begin_and_capture()
        before = self.runtime.snapshot()
        self.runtime.configure_settings(dict(opacity=.5))
        self.dispatch("set_context")
        self.assertIsNotNone(self.runtime.render_payload(first["renderToken"]))
        self.dispatch("set_route_profile", profileId="another-author")
        self.assertEqual(self.runtime.snapshot()["mapId"], before["mapId"])
        self.assertEqual(self.runtime.snapshot()["core"]["poses"], before["core"]["poses"])
        self.assertIsNone(self.runtime.render_payload(first["renderToken"]))
        self.assertFalse(self.runtime.snapshot()["core"]["displayAuthorized"])

    def test_mode_change_or_full_client_mismatch_fails_closed(self):
        first = self.begin_and_capture()
        self.dispatch("set_game_mode", mode="difficult")
        self.assertIsNone(self.runtime.render_payload(first["renderToken"]))
        self.assertFalse(self.runtime.snapshot()["roundActive"])
        self.assertEqual(self.runtime.snapshot()["core"]["poses"], {})
        self.assertFalse(self.dispatch("set_floor", floor="B1")["success"])
        self.assertTrue(self.dispatch("start_round")["success"])
        self.capture.roi = (0, 0, 640, 720)
        result = self.dispatch("capture_now")
        self.assertFalse(result["success"])
        self.assertIn("full-client-capture", result["reason"])

    def test_cancel_end_during_solver_cannot_commit_late_output(self):
        self.dispatch("start_round")
        entered, release = threading.Event(), threading.Event()
        def slow(*args):
            entered.set()
            if not release.wait(5):
                raise AssertionError("test release timeout")
            return copy.deepcopy(self.fit)
        output = []
        with patch.object(_AlignmentHost, "fit", side_effect=slow):
            worker = threading.Thread(target=lambda: output.append(self.dispatch("capture_now")))
            worker.start(); self.assertTrue(entered.wait(5))
            self.dispatch("end_round")
            self.assertFalse(self.runtime.snapshot()["core"]["displayAuthorized"])
            release.set(); worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertFalse(output[0]["success"])
        self.assertTrue(output[0]["stale"])
        self.assertEqual(self.runtime.snapshot()["core"]["poses"], {})

    def test_current_floor_and_residual_cannot_be_supplied_by_payload(self):
        first = self.begin_and_capture()
        self.assertEqual(self.runtime.render_payload(first["renderToken"])["floor"], "1")
        self.fit["selectedRouteCurrentFrameResidual"]["verified"] = False
        result = self.dispatch("capture_now", floor="2", verified=True, liveAuthorization=True)
        self.assertFalse(result["success"])
        self.assertFalse(self.runtime.snapshot()["displayAuthorized"])
        self.assertIsNone(self.runtime.render_payload(first["renderToken"]))

    def test_map_open_preparation_retries_fade_then_uses_settled_frame(self):
        self.dispatch("start_round")
        with patch("map_interface_observation.detect_map_interface", side_effect=[
                dict(detected=False), dict(detected=False), dict(detected=True)]):
            result = self.dispatch("map")
        self.assertTrue(result["success"], result)
        self.assertEqual(self.capture.count, 3)
        self.assertEqual(self.engine.rank_count, 1)
        closed = self.dispatch("map")
        self.assertTrue(closed["success"])
        self.assertEqual(closed["reason"], "map-closed")
        self.assertEqual(self.capture.count, 3)
        self.assertIsNone(self.runtime.render_payload(result["renderToken"]))

    def test_map_open_preparation_cancellation_stops_before_identity(self):
        self.dispatch("start_round")
        capture = self.capture.capture
        def cancel_on_probe(cancelled=None):
            frame = capture(cancelled)
            self.runtime.invalidate("foreground-lost")
            return frame
        self.capture.capture = cancel_on_probe
        with patch("map_interface_observation.detect_map_interface", return_value=dict(detected=False)):
            result = self.dispatch("map")
        self.assertFalse(result["success"])
        self.assertTrue(result["stale"])
        self.assertEqual(self.capture.count, 1)
        self.assertEqual(self.engine.rank_count, 0)
        self.assertFalse(self.runtime.snapshot()["core"]["displayAuthorized"])

    def test_capture_context_change_after_solver_rejects_commit(self):
        self.capture.generation = 5
        self.capture.is_current = lambda context, generation=None: False
        self.dispatch("start_round")
        result = self.dispatch("capture_now")
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "capture-context-changed")
        self.assertFalse(self.runtime.snapshot()["core"]["displayAuthorized"])

    def test_render_getter_rechecks_capture_binding_generation_and_revokes(self):
        self.capture.generation = 5
        valid = [True]
        self.capture.is_current = lambda context, generation=None: valid[0] and generation == 5
        result = self.begin_and_capture()
        self.assertIsNotNone(self.runtime.render_payload(result["renderToken"]))
        valid[0] = False
        self.assertIsNone(self.runtime.render_payload(result["renderToken"]))
        self.assertFalse(self.runtime.snapshot()["core"]["displayAuthorized"])
        self.assertEqual(self.runtime.snapshot()["render"], {})

    def test_live_capture_composition_requires_context_recheck_provider(self):
        runtime = LiveRuntime(engine_factories={"nightmare": lambda: self.engine},
                              capture_provider=self.capture, evidence_kind="live-client-capture")
        self.assertNotIn("start_round", runtime.snapshot()["availableCommands"])
        result = runtime.dispatch("start_round")
        self.assertFalse(result["success"])
        self.assertIn("capture-context-recheck-unavailable", result["reason"])
        self.assertEqual(self.capture.enable_count, 0)

    def test_steady_refresh_commits_fresh_authority_without_revoke_or_raster_change(self):
        first = self.begin_and_capture()
        before = self.runtime.render_payload(first["renderToken"])
        poses = self.runtime.snapshot()["core"]["poses"]
        events = []
        self.runtime.set_event_callback(events.append)
        with patch.object(_AlignmentHost, "_canonical_geometry_current_frame_residual",
                          return_value=(self.fit["selectedRouteCurrentFrameResidual"], None)), \
             patch.object(_AlignmentHost, "fit", side_effect=AssertionError("steady refresh must not solve again")):
            result = self.dispatch("refresh_map")
        after = self.runtime.render_payload(result["renderToken"])
        self.assertTrue(result["success"], result)
        self.assertTrue(result["unchanged"])
        self.assertEqual(after["token"], before["token"])
        self.assertIs(after["pixels"], before["pixels"])
        self.assertEqual(after["matrix"], before["matrix"])
        self.assertEqual(after["generation"], before["generation"] + 1)
        self.assertNotEqual(after["authorizationId"], before["authorizationId"])
        self.assertEqual(self.runtime.snapshot()["core"]["poses"], poses)
        self.assertEqual([row["event"] for row in events], ["render-ready"])
        self.assertEqual(self.runtime.snapshot()["pendingRenderToken"], first["renderToken"])
        json.dumps(events, allow_nan=False)

    def test_floor_refresh_revokes_before_blocked_solver_and_publishes_new_floor(self):
        first = self.begin_and_capture()
        events = []
        self.runtime.set_event_callback(events.append)
        entered, release = threading.Event(), threading.Event()
        output = []
        def slow(*args):
            entered.set()
            if not release.wait(5):
                raise AssertionError("test release timeout")
            return copy.deepcopy(self.fit)
        with patch("map_interface_observation.detect_selected_floor", return_value=dict(selectedFloor=2)), \
             patch.object(_AlignmentHost, "fit", side_effect=slow):
            worker = threading.Thread(target=lambda: output.append(self.dispatch("refresh_map")))
            worker.start(); self.assertTrue(entered.wait(5))
            try:
                self.assertIsNone(self.runtime.render_payload(first["renderToken"]))
                self.assertFalse(self.runtime.snapshot()["core"]["displayAuthorized"])
                self.assertEqual(events[0]["event"], "render-revoked")
                self.assertEqual(events[0]["reason"], "observed-floor-changed")
            finally:
                release.set(); worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertTrue(output[0]["success"], output)
        self.assertEqual(self.runtime.render_payload(output[0]["renderToken"])["floor"], "2")
        self.assertEqual(events[-1]["event"], "render-ready")

    def test_failed_current_residual_hides_before_recovery_and_cancellation(self):
        first = self.begin_and_capture()
        events = []
        self.runtime.set_event_callback(events.append)
        entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
        def slow(*args):
            entered.set()
            if not release.wait(5):
                raise AssertionError("test release timeout")
            return copy.deepcopy(self.fit)
        output = []
        with patch.object(_AlignmentHost, "_canonical_geometry_current_frame_residual",
                          return_value=(dict(verified=False), None)), \
             patch.object(_AlignmentHost, "fit", side_effect=slow):
            worker = threading.Thread(target=lambda: output.append(self.runtime.dispatch("refresh_map", {}, cancelled)))
            worker.start(); self.assertTrue(entered.wait(5))
            self.assertIsNone(self.runtime.render_payload(first["renderToken"]))
            self.assertEqual(events[0]["reason"], "current-frame-residual-failed")
            cancelled.set(); release.set(); worker.join(5)
        self.assertFalse(output[0]["success"])
        self.assertTrue(output[0]["stale"])
        self.assertFalse(self.runtime.snapshot()["core"]["displayAuthorized"])

    def test_unknown_floor_and_closed_frame_revoke_without_solver(self):
        first = self.begin_and_capture()
        events = []
        self.runtime.set_event_callback(events.append)
        with patch("map_interface_observation.detect_selected_floor", return_value=dict(selectedFloor=None)), \
             patch.object(_AlignmentHost, "fit", side_effect=AssertionError("unknown must not solve")):
            unknown = self.dispatch("refresh_map")
        self.assertEqual(unknown["reason"], "current-floor-unavailable")
        self.assertIsNone(self.runtime.render_payload(first["renderToken"]))
        self.assertTrue(self.runtime.snapshot()["mapOpen"], "transient unknown must keep passive observation active")
        with patch("map_interface_observation.detect_map_interface", return_value=dict(detected=False)), \
             patch.object(_AlignmentHost, "fit", side_effect=AssertionError("closed must not solve")):
            self.dispatch("refresh_map")
            self.assertFalse(self.runtime.snapshot()["displayAuthorized"])
            self.dispatch("refresh_map")
        self.assertFalse(self.runtime.snapshot()["mapOpen"])
        self.assertTrue(all(row["event"] == "render-revoked" for row in events))

    def test_cancel_steady_verification_discards_old_authority_and_coalesces_duplicates(self):
        first = self.begin_and_capture()
        events = []
        self.runtime.set_event_callback(events.append)
        entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
        def slow(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError("test release timeout")
            return self.fit["selectedRouteCurrentFrameResidual"], None
        output = []
        with patch.object(_AlignmentHost, "_canonical_geometry_current_frame_residual", side_effect=slow):
            worker = threading.Thread(target=lambda: output.append(self.runtime.dispatch("refresh_map", {}, cancelled)))
            worker.start(); self.assertTrue(entered.wait(5))
            duplicate = self.dispatch("refresh_map")
            self.assertTrue(duplicate["coalesced"])
            self.assertEqual(self.capture.count, 2)
            self.assertIsNotNone(self.runtime.render_payload(first["renderToken"]))
            cancelled.set(); release.set(); worker.join(5)
        self.assertFalse(output[0]["success"])
        self.assertTrue(output[0]["stale"])
        self.assertIsNone(self.runtime.render_payload(first["renderToken"]))
        self.assertEqual([row["event"] for row in events], ["render-revoked"])

    def test_visibility_preserves_pose_and_hidden_refresh_still_runs(self):
        first = self.begin_and_capture()
        pose = self.runtime.snapshot()["core"]["poses"]
        events = []
        self.runtime.set_event_callback(events.append)
        self.dispatch("toggle_sheet_visibility")
        self.assertFalse(self.runtime.snapshot()["alignedVisible"])
        self.assertIsNotNone(self.runtime.render_payload(first["renderToken"]))
        with patch.object(_AlignmentHost, "_canonical_geometry_current_frame_residual",
                          return_value=(self.fit["selectedRouteCurrentFrameResidual"], None)):
            result = self.dispatch("refresh_map")
        self.assertTrue(result["success"], result)
        self.assertFalse(self.runtime.snapshot()["alignedVisible"])
        self.assertEqual(self.runtime.snapshot()["core"]["poses"], pose)
        self.assertEqual(self.capture.count, 2)
        self.runtime.configure_settings(dict(sheetVisibilityEnabled=True))
        self.assertTrue(self.runtime.snapshot()["alignedVisible"])
        self.assertEqual(events[0]["event"], "aligned-visibility-changed")
        self.assertEqual(events[-1]["event"], "aligned-visibility-changed")


if __name__ == "__main__":
    unittest.main()
