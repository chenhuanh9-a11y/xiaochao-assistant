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
from unittest.mock import Mock, patch

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
    def bindFullClient(self, hwnd):
        self.disable(); self.roi = (0, 0, self.frame.shape[1], self.frame.shape[0])
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
    engine.pack.by_id = {item.id: item for item in engine.pack.maps}
    engine.pack.floors = engine.identity_floors
    def rank(*args, **kwargs):
        engine.rank_count += 1
        return [dict(mapId="fixture-map", identityHardGatePassed=True, strongSuggestion=True)]
    engine.rank = rank
    engine.qt_map_ui = SimpleNamespace(observe=lambda *a: dict(mapOpen=True, floor="1"))
    return engine


class LiveRuntimeContracts(unittest.TestCase):
    def setUp(self):
        self.original_fit = _AlignmentHost.fit
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

    def test_game_mode_locked_during_round_and_unlocked_after_end(self):
        self.assertTrue(self.dispatch("start_round")["success"])
        for action in ("set_game_mode", "set_context"):
            result = self.dispatch(action, mode="difficult")
            self.assertFalse(result["success"])
            self.assertEqual(result["reason"], "end-round-before-changing-mode")
        self.assertEqual(self.runtime.snapshot()["mode"], "nightmare")
        self.assertTrue(self.runtime.snapshot()["roundActive"])
        self.assertTrue(self.dispatch("end_round")["success"])
        self.assertTrue(self.dispatch("set_context", mode="difficult")["success"])

    def test_manual_map_choice_before_round_never_captures_or_authorizes_display(self):
        self.assertIn("reselect_map", self.runtime.snapshot()["availableCommands"])
        self.assertTrue(self.dispatch("reselect_map")["success"])
        self.assertTrue(self.dispatch("select_candidate", index=0)["success"])
        self.assertFalse(self.dispatch("confirm_route")["success"])
        self.assertFalse(self.runtime.snapshot()["roundActive"])
        self.assertFalse(self.runtime.snapshot()["displayAuthorized"])
        self.assertEqual(self.capture.count, 0)
        self.assertFalse(self.capture.enabled)
        self.assertTrue(self.dispatch("start_round")["success"])
        self.assertEqual(self.runtime.snapshot()["selectedCandidate"]["mapId"], "fixture-map")
        self.assertEqual(self.dispatch("capture_now")["reason"], "candidate-awaiting-confirmation")
        self.assertFalse(self.runtime.snapshot()["displayAuthorized"])
        self.assertTrue(self.dispatch("confirm_route")["success"])
        self.assertTrue(self.runtime.snapshot()["displayAuthorized"])

    def test_confirmed_reference_survives_pose_failure_and_map_close_but_not_end(self):
        self.fit = dict(success=False, reason="fixture-pose-unavailable")
        self.assertTrue(self.dispatch("start_round")["success"])
        self.assertFalse(self.dispatch("capture_now")["success"])
        sheet = self.runtime.reference_payload()
        self.assertIsNotNone(sheet)
        self.assertEqual((sheet["mapId"], sheet["floor"]), ("fixture-map", "1"))
        self.assertGreater(sheet["pixels"].size, 0)
        self.assertFalse(sheet["referenceAuthorized"])  # Explicit synthetic evidence.
        self.assertNotIn("matrix", sheet)
        self.assertFalse(self.runtime.snapshot()["displayAuthorized"])
        self.assertTrue(self.dispatch("close_map")["success"])
        self.assertEqual(self.runtime.reference_payload()["token"], sheet["token"])
        self.assertTrue(self.dispatch("end_round")["success"])
        self.assertIsNone(self.runtime.reference_payload())

    def test_static_reference_ignores_live_geometry_and_foreground_but_not_binding_lifetime(self):
        from client_capture import ClientCaptureProvider
        from test_client_capture_full import Backend, Native
        backend, native = Backend(), Native()
        backend.current = ClientCaptureContext((1280,720),(100,200,1380,920),96)
        capture = ClientCaptureProvider(native,backend); self.addCleanup(capture.shutdown)
        self.runtime._capture = capture
        self.assertTrue(self.dispatch("bind_capture",hwnd=42)["success"])
        first = self.begin_and_capture()
        reference = self.runtime.reference_payload()
        generation = capture.generation
        backend.current = ClientCaptureContext((1600,900),(300,100,1900,1000),144)
        native.foreground_window = lambda:99
        self.assertFalse(capture.is_current(self.runtime._reference_capture_context,generation))
        with patch.object(capture,"is_current",side_effect=AssertionError("static getter queried live geometry")):
            self.assertEqual(self.runtime.reference_payload()["token"],reference["token"])
            self.runtime.invalidate("foreground-lost")
            self.assertEqual(self.runtime.reference_payload()["token"],reference["token"])
            self.runtime.invalidate("capture-context-changed")
            self.assertEqual(self.runtime.reference_payload()["token"],reference["token"])
            self.assertIsNone(self.runtime.render_payload(first["renderToken"]))
        capture.bindFullClient(42)
        self.assertIsNone(self.runtime.reference_payload())

    def test_static_reference_preparation_survives_pose_geometry_recheck_rejection(self):
        from client_capture import ClientCaptureProvider
        from test_client_capture_full import Backend, Native
        backend, native = Backend(), Native()
        backend.current = ClientCaptureContext((1280,720),(100,200,1380,920),96)
        capture = ClientCaptureProvider(native,backend); self.addCleanup(capture.shutdown)
        self.runtime._capture = capture
        self.assertTrue(self.dispatch("bind_capture",hwnd=42)["success"])
        self.assertTrue(self.dispatch("start_round")["success"])
        def changed_after_reference(*args):
            backend.current = ClientCaptureContext((1600,900),(300,100,1900,1000),144)
            native.foreground_window = lambda:99
            return copy.deepcopy(self.fit)
        with patch.object(_AlignmentHost,"fit",side_effect=changed_after_reference):
            result = self.dispatch("capture_now")
        self.assertEqual(result["reason"],"capture-context-changed")
        reference = self.runtime.reference_payload()
        self.assertIsNotNone(reference)
        self.assertEqual((reference["mapId"],reference["floor"]),("fixture-map","1"))
        self.assertFalse(reference["referenceAuthorized"])
        self.assertFalse(self.runtime.snapshot()["displayAuthorized"])
        self.dispatch("end_round")
        self.assertIsNone(self.runtime.reference_payload())

    def test_static_reference_still_rejects_current_floor_conflict_and_new_resource_owner(self):
        self.begin_and_capture()
        self.assertIsNotNone(self.runtime.reference_payload())
        conflict = self.dispatch("set_floor",floor="2")
        self.assertEqual(conflict["reason"],"current-frame-floor-conflict")
        self.assertIsNone(self.runtime.reference_payload())
        self.fit = dict(success=False,reason="fixture-pose-failed")
        with patch("map_interface_observation.detect_selected_floor",return_value=dict(selectedFloor=2)):
            self.assertFalse(self.dispatch("refresh_map")["success"])
        self.assertEqual(self.runtime.reference_payload()["floor"],"2")
        self.runtime.invalidate("custom-gallery-changed")
        self.assertIsNone(self.runtime.reference_payload())
        with patch("map_interface_observation.detect_selected_floor",return_value=dict(selectedFloor=2)):
            self.dispatch("refresh_map")
        self.assertIsNotNone(self.runtime.reference_payload())
        self.runtime.invalidate("resources-changed")
        self.assertIsNone(self.runtime.reference_payload())

    def unconfirmed_candidates(self):
        second = SimpleNamespace(id="second-map", name="Second map")
        self.engine.pack.maps.append(second)
        self.engine.pack.by_id[second.id] = second
        self.engine.rank = lambda *a, **k: [dict(mapId="second-map", score=5)]
        self.assertTrue(self.dispatch("start_round")["success"])
        self.assertEqual(self.dispatch("capture_now")["reason"], "identity-not-uniquely-verified")

    def test_manual_candidate_navigation_keeps_all_maps_and_wraps_without_identity(self):
        self.unconfirmed_candidates()
        state = self.runtime.snapshot()
        self.assertEqual([r["key"] for r in state["candidateRows"]], ["second-map", "fixture-map"])
        self.assertEqual(state["selectedCandidateIndex"], -1)
        self.assertTrue(self.dispatch("previous_candidate")["success"])
        self.assertEqual(self.runtime.snapshot()["selectedCandidateIndex"], 1)
        self.dispatch("next_candidate")
        self.assertEqual(self.runtime.snapshot()["selectedCandidateIndex"], 0)
        self.assertFalse(self.runtime.snapshot()["identityLocked"])
        self.assertFalse(self.runtime.snapshot()["displayAuthorized"])
        self.assertFalse(self.dispatch("select_candidate", index=True)["success"])
        json.dumps(self.runtime.snapshot(), allow_nan=False)

    def test_manual_confirm_uses_existing_current_frame_solver_and_core(self):
        self.unconfirmed_candidates()
        self.dispatch("select_candidate", index=1)
        count = self.capture.count
        result = self.dispatch("confirm_route", matrix=np.eye(3).tolist())
        self.assertTrue(result["success"], result)
        self.assertEqual(self.capture.count, count + 1)
        self.assertTrue(self.runtime.snapshot()["identityLocked"])
        self.assertTrue(self.runtime.snapshot()["core"]["displayAuthorized"])
        self.assertFalse(self.runtime.snapshot()["liveAuthorization"])
        self.assertNotIn("previous_candidate", self.runtime.snapshot()["availableCommands"])

    def test_manual_identity_does_not_bypass_current_frame_residual(self):
        self.unconfirmed_candidates()
        self.dispatch("select_candidate", index=1)
        self.fit["success"] = False
        self.fit["reason"] = "current-frame-rejected"
        result = self.dispatch("confirm_route")
        self.assertFalse(result["success"])
        self.assertTrue(self.runtime.snapshot()["identityLocked"])
        self.assertFalse(self.runtime.snapshot()["displayAuthorized"])

    def test_refresh_cannot_overwrite_initial_manual_selection_before_confirm(self):
        self.unconfirmed_candidates()
        self.dispatch("select_candidate", index=1)
        self.engine.rank = Mock(return_value=[dict(mapId="second-map", identityHardGatePassed=True, strongSuggestion=True)])
        self.assertEqual(self.dispatch("refresh_map")["reason"], "candidate-awaiting-confirmation")
        self.engine.rank.assert_not_called()
        self.assertEqual(self.runtime.snapshot()["selectedCandidate"]["key"], "fixture-map")
        self.assertFalse(self.runtime.snapshot()["identityLocked"])

    def test_selecting_original_map_after_different_candidate_still_requires_reconfirm(self):
        self.begin_and_capture()
        item = SimpleNamespace(id="second-map", name="Second")
        self.engine.pack.maps.append(item); self.engine.pack.by_id[item.id] = item
        self.runtime._update_candidates([], "1")
        self.dispatch("reselect_map")
        self.dispatch("select_candidate", index=1)
        self.dispatch("select_candidate", index=0)
        self.assertEqual(self.dispatch("refresh_map")["reason"], "candidate-awaiting-confirmation")
        self.assertFalse(self.runtime.snapshot()["displayAuthorized"])
        self.assertTrue(self.dispatch("confirm_route")["success"])

    def test_reselection_preserves_route_until_different_selection_then_waits_confirm(self):
        first = self.begin_and_capture()
        second = SimpleNamespace(id="second-map", name="Second map")
        self.engine.pack.maps.append(second)
        self.engine.pack.by_id[second.id] = second
        self.runtime._update_candidates([], "1")
        self.assertTrue(self.dispatch("map_reselection")["success"])
        self.assertIsNotNone(self.runtime.render_payload(first["renderToken"]))
        self.assertFalse(self.dispatch("next_candidate")["success"])
        self.dispatch("select_candidate", index=1)
        self.assertEqual(self.runtime.snapshot()["mapId"], "fixture-map")
        self.assertIsNone(self.runtime.render_payload(first["renderToken"]))
        self.assertEqual(self.dispatch("refresh_map")["reason"], "candidate-awaiting-confirmation")
        self.assertFalse(self.runtime.snapshot()["displayAuthorized"])
        self.assertTrue(self.dispatch("confirm_route")["success"])
        self.assertEqual(self.runtime.snapshot()["mapId"], "second-map")
        self.assertEqual(set(self.runtime.snapshot()["core"]["poses"]), {"second-map"})

    def test_floor_target_cannot_relabel_current_known_sensor_but_unknown_allows_manual(self):
        first = self.begin_and_capture()
        self.assertEqual(self.runtime.snapshot()["availableFloors"], ["1", "2", "B1"])
        count = self.capture.count
        self.fit["success"] = False
        self.fit["reason"] = "target-current-frame-residual-rejected"
        result = self.dispatch("cycle_floor")
        self.assertFalse(result["success"])
        state = self.runtime.snapshot()
        self.assertEqual((state["manualFloor"], state["floor"], state["observedFloor"]), ("2", "2", "1"))
        self.assertEqual(self.capture.count, count + 1)
        self.assertIsNone(self.runtime.render_payload(first["renderToken"]))
        self.assertFalse(state["displayAuthorized"])
        self.fit["success"] = True
        with patch.object(_AlignmentHost, "fit", side_effect=AssertionError("known conflict reached fit")):
            conflict = self.dispatch("set_floor", floor="2")
        self.assertEqual(conflict["reason"], "current-frame-floor-conflict")
        self.assertEqual((conflict["observedFloor"], conflict["targetFloor"], conflict["fitAttempted"]), ("1", "2", False))
        self.assertEqual(self.runtime.snapshot()["poseResult"]["status"], "rejected")
        with patch("map_interface_observation.detect_selected_floor", return_value=dict(selectedFloor=None)):
            self.assertTrue(self.dispatch("set_floor", floor="2")["success"])
            state = self.runtime.snapshot()
            self.assertEqual((state["render"]["floor"], state["render"]["observedFloor"]), ("2", ""))
            self.assertEqual(state["render"]["floorSource"], "manual")
            self.assertTrue(self.dispatch("refresh_map")["success"])
        self.assertEqual(self.runtime.snapshot()["floor"], "2")
        self.dispatch("cycle_floor")
        self.assertEqual(self.runtime.snapshot()["manualFloor"], "B1")
        self.dispatch("set_floor", floor=None)
        self.assertIsNone(self.runtime.snapshot()["manualFloor"])
        self.assertEqual(self.runtime.snapshot()["floor"], "1")
        self.dispatch("close_map")
        self.assertFalse(self.dispatch("cycle_floor")["success"])

    def test_cancelled_manual_action_and_lifecycle_clear_candidate_state(self):
        self.unconfirmed_candidates()
        cancelled = threading.Event(); cancelled.set()
        self.assertFalse(self.runtime.dispatch("select_candidate", dict(index=0), cancelled)["success"])
        self.assertEqual(self.runtime.snapshot()["selectedCandidateIndex"], -1)
        self.dispatch("end_round")
        state = self.runtime.snapshot()
        self.assertEqual(state["candidateRows"], [])
        self.assertEqual(state["availableFloors"], [])
        self.assertFalse(state["identityLocked"])

    def test_unreadable_floor_keeps_map_frame_for_explicit_manual_correction_in_both_modes(self):
        for mode in ("nightmare", "difficult"):
            with self.subTest(mode=mode):
                self.dispatch("end_round")
                self.dispatch("set_game_mode", mode=mode)
                self.dispatch("start_round")
                engine = self.runtime._engine
                engine.qt_map_ui.observe = lambda *a: dict(mapOpen=True, floor=None)
                with patch("map_interface_observation.detect_selected_floor", return_value=dict(selectedFloor=None)):
                    result = self.dispatch("capture_now")
                    self.assertEqual(result["reason"], "current-floor-unavailable")
                    self.assertTrue(self.runtime.snapshot()["mapOpen"])
                    self.assertFalse(self.runtime.snapshot()["displayAuthorized"])
                    count = self.capture.count
                    result = self.dispatch("set_floor", floor="1")
                    self.assertTrue(result["success"], result)
                    self.assertEqual(self.capture.count, count + 1)
                    state = self.runtime.snapshot()
                    self.assertEqual((state["floor"], state["observedFloor"], state["floorSource"]), ("1", "", "manual"))
                    self.assertTrue(state["displayAuthorized"])
                    self.assertFalse(state["liveAuthorization"])

    def test_end_during_manual_target_fit_cannot_commit_late_output(self):
        self.begin_and_capture()
        entered, release = threading.Event(), threading.Event()
        result = []
        def slow_fit(*args):
            entered.set()
            self.assertTrue(release.wait(5))
            return copy.deepcopy(self.fit)
        with patch.object(_AlignmentHost, "fit", side_effect=slow_fit), patch(
                "map_interface_observation.detect_selected_floor", return_value=dict(selectedFloor=2)):
            worker = threading.Thread(target=lambda: result.append(self.dispatch("set_floor", floor="2")))
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                self.dispatch("end_round")
            finally:
                release.set(); worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertFalse(result[0]["success"])
        self.assertFalse(self.runtime.snapshot()["displayAuthorized"])
        self.assertEqual(self.runtime.snapshot()["candidateRows"], [])

    def test_unconfirmed_manual_floor_reranks_target_without_authorizing_manual_choice(self):
        self.unconfirmed_candidates()
        calls = []
        self.engine.rank = lambda *a, **k: calls.append(k["floor"]) or [dict(mapId="second-map", score=8)]
        self.assertFalse(self.dispatch("set_floor", floor="2")["success"])
        self.assertEqual(calls, ["2"])
        self.assertEqual(len(self.runtime.snapshot()["candidateRows"]), 2)
        self.assertFalse(self.runtime.snapshot()["identityLocked"])
        self.assertFalse(self.runtime.snapshot()["displayAuthorized"])

    def test_custom_callback_is_display_only_after_typed_reference(self):
        self.begin_and_capture()
        before = self.runtime.snapshot()
        layer = dict(pixels=np.full((20, 24, 4), 255, np.uint8), matrixDisplayToReference=np.eye(3),
            resourceIdentity="custom-fixture", displayKind="custom-route", displayLabel="Custom fixture",
            routeProfileId="custom:fixture")
        self.engine.qt_custom_display = Mock(return_value=layer)
        self.dispatch("set_route_profile", profileId="custom:fixture")
        result = self.dispatch("capture_now")
        self.assertTrue(result["success"], result)
        args = self.engine.qt_custom_display.call_args.args
        self.assertEqual(args[:3], ("custom:fixture", "fixture-map", "1"))
        self.assertIsInstance(args[3], SharedStructureReference)
        after = self.runtime.snapshot()
        self.assertEqual(after["mapId"], before["mapId"])
        self.assertEqual(after["render"]["canonicalMatrix"], before["render"]["canonicalMatrix"])
        self.assertEqual(after["render"]["resourceIdentity"], "custom-fixture")
        self.assertFalse(after["liveAuthorization"])

    def test_shared_manual_hold_restores_latest_sensor_at_two_seconds_in_both_modes(self):
        for mode in ("nightmare", "difficult"):
            with self.subTest(mode=mode):
                self.dispatch("end_round")
                self.dispatch("set_game_mode", mode=mode)
                self.now = 10.
                self.runtime._monotonic = lambda: self.now
                self.begin_and_capture()
                self.assertEqual(self.dispatch("set_floor", floor="2")["reason"], "current-frame-floor-conflict")
                self.now = 11.999
                self.assertEqual(self.dispatch("refresh_map")["reason"], "current-frame-floor-conflict")
                before = self.runtime.snapshot()
                self.assertEqual((before["floor"], before["manualFloor"], before["observedFloor"]), ("2", "2", "1"))
                self.assertEqual(before["floorPolicyReason"], "manual-hold-deferred")
                self.now = 12.
                self.assertTrue(self.dispatch("refresh_map")["success"])
                after = self.runtime.snapshot()
                self.assertEqual((after["floor"], after["manualFloor"], after["observedFloor"]), ("1", None, "1"))
                self.assertEqual(after["render"]["floorSource"], "automatic")
                self.assertEqual(after["manualFloorHoldRemaining"], 0.)
                self.assertTrue(self.dispatch("end_round")["success"])
                self.dispatch("set_game_mode", mode="difficult" if mode == "nightmare" else "nightmare")
                reset = self.runtime.snapshot()
                self.assertIsNone(reset["manualFloor"])
                self.assertEqual(reset["manualFloorHoldRemaining"], 0.)
                self.assertEqual(reset["observedFloor"], "")

    def test_cancelled_floor_intent_does_not_start_hold_or_capture(self):
        self.begin_and_capture()
        count = self.capture.count
        cancelled = threading.Event(); cancelled.set()
        self.assertFalse(self.runtime.dispatch("set_floor", dict(floor="2"), cancelled)["success"])
        self.assertEqual(self.capture.count, count)
        self.assertIsNone(self.runtime.snapshot()["manualFloor"])
        self.assertEqual(self.runtime.snapshot()["manualFloorHoldRemaining"], 0.)

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
        self.assertTrue(self.dispatch("end_round")["success"])
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
        self.assertEqual([row["event"] for row in events], ["pose-attempt", "pose-result", "render-ready"])
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
            self.assertEqual(next(row["reason"] for row in events if row["event"] == "render-revoked"), "current-frame-residual-failed")
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
        self.assertEqual([row["event"] for row in events], ["pose-attempt", "render-revoked"])

    def test_same_floor_full_fit_keeps_original_sensor_classification(self):
        self.begin_and_capture()
        self.fit["matrixSourceToClient"] = np.array([[.55,.01,44],[.02,.56,65],[0,0,1.]])
        with patch.object(self.runtime._core, "adjudicate", wraps=self.runtime._core.adjudicate) as decide:
            result = self.dispatch("capture_now")
        self.assertTrue(result["success"], result)
        candidate = decide.call_args.args[0]
        self.assertEqual(candidate.source, "initial-structure-v2")
        self.assertFalse(candidate.translation_only)
        np.testing.assert_allclose(self.runtime._core.pose_by_map["fixture-map"].pose_for_floor("1"),
                                   self.fit["matrixSourceToClient"])

    def test_actual_refresh_translation_source_keeps_core_linear_change_guards(self):
        from dataclasses import replace
        self.begin_and_capture()
        core = self.runtime._core
        with patch.object(_AlignmentHost, "_canonical_geometry_current_frame_residual",
                return_value=(self.fit["selectedRouteCurrentFrameResidual"], None)), \
                patch.object(core, "adjudicate", wraps=core.adjudicate) as decide:
            result = self.dispatch("refresh_map")
        self.assertTrue(result["success"], result)
        candidate = decide.call_args.args[0]
        self.assertEqual(candidate.source, "same-floor-reopen-verified-refresh")
        self.assertTrue(candidate.translation_only)
        changed = np.asarray(candidate.matrix_source_to_client).copy(); changed[0,0] += .1
        for offset, values, expected in (
                (1, dict(translation_only=False), "same-floor-reopen-must-be-translation-only"),
                (2, dict(matrix_source_to_client=changed), "same-floor-reopen-linear-change")):
            generation = candidate.frame_generation + offset
            core.begin_observation(cycle_id=self.runtime._cycle, map_id="fixture-map", floor="1",
                frame_generation=generation, frame_signature=("synthetic-negative-translation", offset))
            self.assertEqual(core.adjudicate(replace(candidate, frame_generation=generation, **values)).reason, expected)

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

    def test_main_entry_reuses_original_current_frame_verifier_before_fast_solver(self):
        observation = {"current-frame": True}
        verification = dict(success=True, reason="entry-current-frame-validation",
                            matrixSourceToClient=self.fit["matrixSourceToClient"])
        verify = Mock(return_value=verification)
        self.engine.entry_identity = SimpleNamespace(geometry=Mock(return_value=observation), verify=verify)
        host = _AlignmentHost(self.engine)
        candidate = dict(mapId="fixture-map", entryKind="main", hypothesis=dict(scale=.5, translation=[40,60]))
        with patch.object(_AlignmentHost, "_canonical_geometry_current_frame_residual",
                          return_value=(self.fit["selectedRouteCurrentFrameResidual"], None)), \
             patch("fast_structure_alignment.try_alignment", side_effect=AssertionError("entry seed must run first")):
            # Invoke the real method; setUp's worker-fit substitute is only for
            # lifecycle tests and must not hide this entry adapter contract.
            result = self.original_fit(host, self.capture.frame, "fixture-map", "1", lambda: False, candidate)
        self.assertTrue(result["success"])
        self.assertEqual(result["reason"], "difficult-entry-current-frame-candidate")
        self.assertEqual(result["strategy"], "verified-entry-current-frame")
        self.assertIs(verify.call_args.kwargs["context"], observation)
        self.assertEqual(verify.call_args.args[1], dict(mapId="fixture-map", hypothesis=candidate["hypothesis"]))
        np.testing.assert_array_equal(result["matrixSourceToClient"], verification["matrixSourceToClient"])

    def test_exit_anchored_set_reverifies_all_and_never_broad_falls_through(self):
        candidates = [dict(mapId="fixture-map", floor="1", poseId=i) for i in (1,2)]
        seed = dict(mapId="fixture-map", floor="1", locatorKind="side", identityOnly=True,
                    poseVerified=False, entryPoseCandidates=candidates)
        for successful_count in (0,1,2):
            with self.subTest(successful_count=successful_count):
                checks = [dict(success=i < successful_count, matrixSourceToClient=self.fit["matrixSourceToClient"])
                          for i in range(2)]
                verify = Mock(side_effect=checks)
                self.engine.side_entry_identity = SimpleNamespace(verify=verify)
                host = _AlignmentHost(self.engine)
                with patch.object(_AlignmentHost, "_canonical_geometry_current_frame_residual",
                                  return_value=(self.fit["selectedRouteCurrentFrameResidual"], None)), \
                     patch("fast_structure_alignment.try_alignment", side_effect=AssertionError("anchored must not broaden")), \
                     patch("single_map_structure.solve_initial_structure", side_effect=AssertionError("anchored must not broaden")):
                    result = self.original_fit(host, self.capture.frame, "fixture-map", "1", lambda: False, seed)
                self.assertEqual(verify.call_count, 2)
                self.assertEqual([call.args[1] for call in verify.call_args_list], candidates)
                self.assertEqual(result["success"], successful_count == 1)
                if successful_count != 1:
                    self.assertEqual(result["reason"], "difficult-entry-current-frame-rejected")
                    self.assertEqual(result["entryReverification"]["acceptedPoseCount"], successful_count)


class SharedFloorPolicyContracts(unittest.TestCase):
    def evidence(self, observed, history=(), **kwargs):
        from floor_observation_policy import read_floor_evidence
        return read_floor_evidence(dict(selectedFloor=observed), history, **kwargs)

    def decide(self, observed, **kwargs):
        from floor_observation_policy import decide_floor_transition
        values = dict(current_floor="2", manual_override=True, hold_until=2., now=0.)
        values.update(kwargs)
        return decide_floor_transition(self.evidence(observed), **values)

    def test_two_second_boundary_ends_manual_hold_only_with_valid_latest_evidence(self):
        before = self.decide(1, now=1.999)
        self.assertEqual(before.reason, "manual-hold-deferred")
        self.assertTrue(before.hold_active)
        self.assertFalse(before.apply_target)
        boundary = self.decide(1, now=2.)
        self.assertTrue(boundary.manual_hold_ended)
        self.assertFalse(boundary.manual_override)
        self.assertTrue(boundary.apply_target)
        unknown = self.decide(None, now=5.)
        self.assertEqual(unknown.reason, "insufficient-stable-evidence")
        self.assertFalse(unknown.manual_hold_ended)
        self.assertTrue(unknown.manual_override)

    def test_latest_sensor_does_not_gain_historical_vote_authority(self):
        self.assertIsNone(self.evidence(None, [2, 2, 2]).stable)
        self.assertEqual(self.evidence(1, [2, 2, 2]).stable, 1)
        evidence = self.evidence(2, [1, 1, 1, 1, 1])
        self.assertEqual(evidence.observations, (1, 1, 1, 1, 2))

    def test_pending_click_conflict_and_expiry_precede_hold_mutations(self):
        pending = dict(cycleId=7, floor=2, expiresAt=4., awaitingObservation=True)
        conflict = self.decide(1, cycle_id=7, game_intent=pending, now=3.)
        self.assertEqual(conflict.reason, "pending-click-mismatch")
        self.assertFalse(conflict.manual_hold_ended)
        expired = self.decide(1, cycle_id=7, game_intent=pending, now=5.)
        self.assertTrue(expired.clear_game_intent)
        self.assertTrue(expired.manual_hold_ended)
        self.assertTrue(expired.apply_target)

    def test_hard_lock_and_explicit_intent_keep_floor_required_separate(self):
        from floor_observation_policy import decide_floor_transition
        passive = self.decide(1, hard_locked=True, now=8.)
        self.assertEqual(passive.reason, "hard-lock-deferred")
        self.assertFalse(passive.manual_hold_ended)
        explicit = decide_floor_transition(self.evidence(1, explicit_floor=2), current_floor="1",
            floor_required=True, hard_locked=True, now=0.)
        self.assertTrue(explicit.apply_target)
        self.assertTrue(explicit.game_intent_active)
        self.assertFalse(explicit.clear_floor_required)
        same = self.decide(2, floor_required=True, now=.5)
        self.assertTrue(same.clear_floor_required)
        self.assertFalse(same.apply_target)

    def test_strong_highlight_thresholds_remain_diagnostic_without_added_frames(self):
        from floor_observation_policy import read_floor_evidence
        for margin, score, strong in [(12., 32., True), (11.99, 32., False), (12., 31.99, False)]:
            evidence = read_floor_evidence(dict(selectedFloor=1, layout="localized-triplet",
                margin=margin, scores={"1": score}), [])
            self.assertEqual(evidence.strong_latest, strong)
            self.assertEqual(evidence.stable, 1)


if __name__ == "__main__":
    unittest.main()
