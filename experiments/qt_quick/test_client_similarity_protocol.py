"""Shared native state + Qt protocol, explicit synthetic frames and no native IO."""
import copy
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from client_engine import clone_similarity_state, stage_similarity_rank
import test_client_live_runtime as fixtures
from client_live_runtime import _AlignmentHost
from native_similarity_groups import NativeSimilarityState

IDS = ("fixture-map", "second-map")

def proposal(winner=None):
    return dict(status="conditions", mode="nightmare", floor="1", resourceKey="fixture-index",
        complete=True, candidateMemberId=winner, originalIdentityHardGatePassed=bool(winner),
        conditionObservations=[dict(domainId="entrance", conditionId="family",
            completeForeignMapCompetition=True, currentCompetitionComplete=True,
            ambiguousViews=[], outsideCompetitors=[], activeViews=["a", "b"], expectedPoseIds=["a", "b"],
            memberIds=list(IDS), poseMaps={"a": IDS[0], "b": IDS[1]}, pairs=[])])

def rows(winner=None):
    result = [dict(mapId=mid, identityHardGatePassed=mid == winner, strongSuggestion=False,
        difficultEntry=dict(mapId=mid, floor="1", verification=dict(success=True,
            matrixSourceToClient=np.eye(3).tolist()))) for mid in IDS]
    result[0]["nativeSimilarity"] = proposal(winner)
    return result

class SimilarityProtocolTests(unittest.TestCase):
    setUp = fixtures.LiveRuntimeContracts.setUp
    dispatch = fixtures.LiveRuntimeContracts.dispatch

    def prepare_group(self):
        self.capture.frame.fill(0)
        second = SimpleNamespace(id=IDS[1])
        self.engine.pack.maps.append(second); self.engine.pack.by_id[second.id] = second
        self.engine.rank = Mock(return_value=rows())
        self.engine.rank_established_group = Mock(return_value=rows())
        self.now = 1.0
        self.runtime._monotonic = lambda: self.now
        self.assertTrue(self.dispatch("start_round")["success"])

    def capture_group(self):
        self.prepare_group()
        result = self.dispatch("capture_now")
        self.assertTrue(result["success"], result)
        return result

    def test_non_group_unique_hard_gate_requires_top_and_explicit_strong(self):
        second = SimpleNamespace(id=IDS[1])
        self.engine.pack.maps.append(second); self.engine.pack.by_id[second.id] = second
        cases = [
            ("not-strong", [dict(mapId=IDS[0], identityHardGatePassed=True, strongSuggestion=False)]),
            ("missing-strong", [dict(mapId=IDS[0], identityHardGatePassed=True)]),
            ("not-top", [dict(mapId=IDS[1], identityHardGatePassed=False, strongSuggestion=True),
                         dict(mapId=IDS[0], identityHardGatePassed=True, strongSuggestion=True)]),
        ]
        for label, ranked in cases:
            with self.subTest(label=label):
                self.engine.rank = Mock(return_value=ranked)
                self.assertTrue(self.dispatch("start_round")["success"])
                self.assertFalse(self.dispatch("capture_now")["success"])
                state = self.runtime.snapshot()
                self.assertFalse(state["identityLocked"]); self.assertEqual(state["mapId"], "")
                self.assertFalse(state["displayAuthorized"])
                self.dispatch("end_round")
        self.engine.rank = Mock(return_value=[dict(mapId=IDS[0], identityHardGatePassed=True, strongSuggestion=True)])
        self.assertTrue(self.dispatch("start_round")["success"])
        self.assertTrue(self.dispatch("capture_now")["success"])
        self.assertEqual(self.runtime.snapshot()["mapId"], IDS[0])

    def test_group_provisional_uses_current_pose_without_durable_identity(self):
        result = self.capture_group()
        state = self.runtime.snapshot()
        self.assertEqual((state["identityPhase"], state["mapId"], state["provisionalMapId"]),
                         ("GROUP_LOCKED", "", IDS[0]))
        self.assertFalse(state["identityLocked"])
        self.assertTrue(state["displayAuthorized"])
        self.assertEqual(state["poseResult"]["status"], "valid")
        for payload in (self.runtime.render_payload(result["renderToken"]), self.runtime.reference_payload()):
            self.assertEqual(payload["mapId"], IDS[0]); self.assertTrue(payload["provisional"])
            self.assertEqual(payload["durableMapId"], "")
            self.assertFalse(payload.get("liveAuthorization", payload.get("referenceAuthorized")))
        self.assertEqual(self.engine.rank.call_args.kwargs["budget_seconds"], 1.85)
        self.assertEqual(self.engine.rank_established_group.call_args.kwargs["budget_seconds"], .2)
        self.assertIsNotNone(self.engine.native_similarity_context_provider())
        # Repeated pixels do not manufacture an extra temporal observation.
        self.assertEqual(len(self.runtime._identity_native.observations), 1)

    def test_explicit_capture_accumulates_original_proposals_but_passive_review_does_not(self):
        self.capture_group()
        self.assertEqual(len(self.runtime._identity_native.observations), 1)
        self.now = 3.0; self.capture.frame[0, 0, 0] = 1
        self.assertTrue(self.dispatch("capture_now")["success"])
        self.assertEqual(len(self.runtime._identity_native.observations), 2)
        self.assertTrue(any(call.kwargs["budget_seconds"] == 1.85
                            for call in self.engine.rank_established_group.call_args_list))
        self.now = 5.0; self.capture.frame[0, 0, 0] = 2
        with patch.object(_AlignmentHost, "_canonical_geometry_current_frame_residual",
                return_value=(self.fit["selectedRouteCurrentFrameResidual"], None)):
            self.assertTrue(self.dispatch("refresh_map")["success"])
        self.assertEqual(len(self.runtime._identity_native.observations), 2)
        self.assertEqual(self.engine.rank_established_group.call_args.kwargs["budget_seconds"], .2)

    def test_original_unique_review_plus_current_pose_commits_identity(self):
        self.capture_group()
        self.engine.rank_established_group.return_value = rows(IDS[0])
        self.now = 3.0; self.capture.frame[0, 0, 0] = 1
        events = []; self.runtime.set_event_callback(events.append)
        result = self.dispatch("capture_now")
        self.assertTrue(result["success"], result)
        self.assertEqual(self.runtime.snapshot()["identityPhase"], "GROUP_LOCKED")
        self.assertEqual(self.runtime.snapshot()["mapId"], "")
        target = result["pendingSiblingToken"]
        source_core = self.runtime._core
        self.assertIsNot(self.runtime._pending_sibling["core"], source_core)
        self.assertTrue(self.runtime.acknowledge_sibling(target, True)["success"])
        result["renderToken"] = target
        state = self.runtime.snapshot()
        self.assertEqual((state["identityPhase"], state["mapId"]), ("MEMBER_VERIFIED", IDS[0]))
        self.assertTrue(state["identityLocked"])
        self.assertFalse(self.runtime.render_payload(result["renderToken"])["provisional"])
        self.assertEqual(sum(e["event"] == "identity-committed" for e in events), 1)

    def test_complete_group_is_not_unique_identity_and_pose_failure_does_not_grant_sheet(self):
        self.prepare_group(); self.fit = dict(success=False, reason="original-residual-failed")
        result = self.dispatch("capture_now")
        self.assertFalse(result["success"])
        state = self.runtime.snapshot()
        self.assertEqual(state["identityPhase"], "GROUP_LOCKED")
        self.assertEqual(state["poseResult"]["reason"], "original-residual-failed")
        self.assertFalse(state["identityLocked"]); self.assertFalse(state["displayAuthorized"])
        self.assertIsNone(self.runtime.reference_payload())
        self.engine.rank_established_group.assert_not_called()

    def test_group_floor_transition_does_not_carry_provisional_identity(self):
        self.capture_group()
        self.dispatch("set_floor", floor="2")
        state = self.runtime.snapshot()
        self.assertEqual(state["mapId"], "")
        self.assertFalse(state["identityLocked"])
        self.assertNotEqual(state["provisionalMapId"], IDS[0])

    def test_late_rank_after_end_cannot_publish_group_or_pose(self):
        self.prepare_group()
        entered, release = threading.Event(), threading.Event()
        def blocked(*a, **kw):
            entered.set(); self.assertTrue(release.wait(4)); return rows()
        self.engine.rank.side_effect = blocked
        result = []
        worker = threading.Thread(target=lambda: result.append(self.dispatch("capture_now")))
        worker.start(); self.assertTrue(entered.wait(4))
        self.dispatch("end_round"); release.set(); worker.join(4)
        self.assertFalse(worker.is_alive()); self.assertTrue(result[0]["stale"])
        state = self.runtime.snapshot()
        self.assertEqual(state["identityPhase"], "UNLOCKED")
        self.assertFalse(state["displayAuthorized"]); self.assertEqual(state["mapId"], "")

    def test_unique_sibling_with_rejected_pose_keeps_valid_provisional_display(self):
        self.capture_group()
        self.engine.rank_established_group.return_value = rows(IDS[1])
        self.now = 3.0; self.capture.frame[0, 0, 0] = 2
        def fit(host_frame, mid, *args):
            return (dict(success=False, reason="sibling-current-residual-failed")
                    if mid == IDS[1] else copy.deepcopy(self.fit))
        with patch.object(_AlignmentHost, "fit", side_effect=fit), patch.object(
                _AlignmentHost, "_canonical_geometry_current_frame_residual",
                return_value=(self.fit["selectedRouteCurrentFrameResidual"], None)):
            result = self.dispatch("refresh_map")
        self.assertTrue(result["success"], result)
        state = self.runtime.snapshot()
        self.assertEqual((state["identityPhase"], state["mapId"], state["provisionalMapId"]),
                         ("GROUP_LOCKED", "", IDS[0]))
        self.assertTrue(state["displayAuthorized"])
        self.assertEqual(self.runtime.render_payload(result["renderToken"])["mapId"], IDS[0])
        self.assertEqual(self.runtime.reference_payload()["mapId"], IDS[0])
        self.assertFalse(state["identityLocked"])

    def pending_sibling(self):
        self.capture_group()
        self.engine.rank_established_group.return_value = rows(IDS[1])
        self.now = 3.0; self.capture.frame[0, 0, 0] = 3
        with patch.object(_AlignmentHost, "_canonical_geometry_current_frame_residual",
                return_value=(self.fit["selectedRouteCurrentFrameResidual"], None)):
            result = self.dispatch("refresh_map")
        self.assertTrue(result["success"], result)
        self.assertEqual(result["reason"], "sibling-material-pending")
        return result

    def test_unique_sibling_commits_only_after_its_own_current_pose_and_material(self):
        result = self.pending_sibling()
        target = result["pendingSiblingToken"]
        state = self.runtime.snapshot()
        self.assertEqual((state["identityPhase"], state["mapId"]), ("GROUP_LOCKED", ""))
        self.assertEqual(self.runtime.render_payload(result["renderToken"])["mapId"], IDS[0])
        pending = self.runtime.pending_sibling_payload(target)
        self.assertEqual(pending["mapId"], IDS[1]); self.assertTrue(pending["requiresMaterialAck"])
        self.assertFalse(pending["liveAuthorization"])
        self.assertIsNone(self.runtime.render_payload(target))
        self.runtime.configure_settings(dict(sheetVisibilityEnabled=False))
        ack = self.runtime.acknowledge_sibling(target, True, reason="fixture-material-ready")
        self.assertTrue(ack["success"], ack)
        state = self.runtime.snapshot()
        self.assertEqual((state["identityPhase"], state["mapId"]), ("MEMBER_VERIFIED", IDS[1]))
        self.assertEqual(self.runtime.render_payload(target)["mapId"], IDS[1])
        self.assertFalse(self.runtime.reference_payload()["provisional"])
        self.assertFalse(state["alignedVisible"])

    def test_sibling_material_rejection_keeps_source_and_its_original_core(self):
        result = self.pending_sibling()
        source_core = self.runtime._core
        source = self.runtime.render_payload(result["renderToken"])
        ack = self.runtime.acknowledge_sibling(result["pendingSiblingToken"], False, reason="fixture-image-error")
        self.assertFalse(ack["success"]); self.assertTrue(ack["sourceRetained"])
        self.assertIs(self.runtime._core, source_core)
        self.assertEqual(self.runtime.render_payload(source["token"])["authorizationId"], source["authorizationId"])
        self.assertEqual(self.runtime.snapshot()["identityPhase"], "GROUP_LOCKED")
        self.assertEqual(self.runtime.snapshot()["mapId"], "")
        self.assertIsNone(self.runtime.pending_sibling_payload(result["pendingSiblingToken"]))

    def test_sibling_identity_rejection_after_material_keeps_source(self):
        result = self.pending_sibling()
        source_core = self.runtime._core
        with patch.object(NativeSimilarityState, "confirm", return_value=False):
            ack = self.runtime.acknowledge_sibling(result["pendingSiblingToken"], True, reason="fixture-material-ready")
        self.assertEqual(ack["reason"], "sibling-identity-commit-failed")
        self.assertTrue(ack["sourceRetained"]); self.assertIs(self.runtime._core, source_core)
        self.assertEqual(self.runtime.render_payload(result["renderToken"])["mapId"], IDS[0])
        self.assertEqual(self.runtime.reference_payload()["mapId"], IDS[0])
        self.assertFalse(self.runtime.snapshot()["identityLocked"])

    def test_sibling_ack_rejects_wrong_token_and_expired_generation_floor_or_epoch(self):
        for changed in ("generation", "floor", "epoch"):
            with self.subTest(changed=changed):
                if self.runtime.snapshot()["roundActive"]:
                    self.dispatch("end_round")
                result = self.pending_sibling()
                target = result["pendingSiblingToken"]
                self.assertFalse(self.runtime.acknowledge_sibling("wrong-token", True)["success"])
                self.assertIsNotNone(self.runtime.pending_sibling_payload(target))
                with self.runtime._lock:
                    if changed == "generation": self.runtime._frame_generation += 1
                    elif changed == "floor": self.runtime._floor = "2"
                    else: self.runtime._epoch += 1
                self.assertIsNone(self.runtime.pending_sibling_payload(target))
                ack = self.runtime.acknowledge_sibling(target, True)
                self.assertFalse(ack["success"]); self.assertFalse(ack["sourceRetained"])
                self.assertEqual(self.runtime.snapshot()["mapId"], "")

    def test_explicit_unique_sibling_rank_cannot_bypass_material_identity_ack(self):
        self.capture_group()
        self.engine.rank_established_group.return_value = rows(IDS[1])
        self.now = 3.0; self.capture.frame[0, 0, 0] = 4
        result = self.dispatch("capture_now")
        self.assertEqual(result["reason"], "sibling-material-pending")
        state = self.runtime.snapshot()
        self.assertEqual((state["identityPhase"], state["mapId"]), ("GROUP_LOCKED", ""))
        pending = self.runtime.pending_sibling_payload(result["pendingSiblingToken"])
        self.assertEqual(pending["mapId"], IDS[1])
        self.assertTrue(self.runtime.acknowledge_sibling(result["pendingSiblingToken"], True)["success"])
        self.assertEqual(self.runtime.snapshot()["mapId"], IDS[1])

    def test_initial_solver_receives_original_index_candidates_only_when_enabled(self):
        host = _AlignmentHost(self.engine, settings=dict(poseIndexCandidatesEnabled=True))
        matrices = [np.eye(3)]
        self.engine.pose_index_candidates = Mock(return_value=(matrices, dict(attempted=True, reason="fixture-index")))
        with patch("fast_structure_alignment.try_alignment", return_value=None) as fast, \
                patch("single_map_structure.current_geometry", return_value={}), \
                patch("single_map_structure.solve_initial_structure", return_value=copy.deepcopy(self.fit)) as solve, \
                patch.object(host, "_rendered_profile_current_frame_residual", return_value=self.fit["selectedRouteCurrentFrameResidual"]):
            self.assertTrue(self.original_fit(host, self.capture.frame, IDS[0], "1", lambda:False)["success"])
            self.assertIs(solve.call_args.kwargs["pose_index_candidates"], matrices)
            self.assertEqual(solve.call_args.kwargs["pose_index_diagnostics"]["reason"], "fixture-index")
            self.assertIn("entry_seed", fast.call_args.kwargs)
            host.settings = {}
            self.engine.pose_index_candidates.reset_mock()
            self.original_fit(host, self.capture.frame, IDS[0], "1", lambda:False)
            self.engine.pose_index_candidates.assert_not_called()
            self.assertEqual(solve.call_args.kwargs["pose_index_candidates"], [])

    def test_late_group_review_after_end_and_mode_change_cannot_commit(self):
        self.prepare_group()
        entered, release = threading.Event(), threading.Event()
        def review(*a, **kw):
            entered.set(); self.assertTrue(release.wait(4)); return rows(IDS[0])
        self.engine.rank_established_group.side_effect = review
        output = []
        worker = threading.Thread(target=lambda: output.append(self.dispatch("capture_now")))
        worker.start(); self.assertTrue(entered.wait(4))
        self.dispatch("end_round"); self.dispatch("set_game_mode", mode="difficult")
        release.set(); worker.join(4)
        self.assertFalse(worker.is_alive())
        self.assertTrue(output[0]["stale"])
        state = self.runtime.snapshot()
        self.assertEqual((state["mode"], state["identityPhase"], state["mapId"]), ("difficult", "UNLOCKED", ""))
        self.assertFalse(state["displayAuthorized"]); self.assertIsNone(self.runtime.reference_payload())

    def test_real_memory_store_uses_original_map_validator_and_explicit_seed_root(self):
        import tempfile
        from pathlib import Path
        from client_providers import _build_memory_store
        from nightmare_candidate import NightmareMemoryStore
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for mode in ("nightmare", "difficult"):
                pack = SimpleNamespace(mode=mode, by_id={})
                ui = SimpleNamespace(observe=Mock(return_value=dict(mapOpen=True)))
                with patch("map_interface_observation.detect_map_interface", return_value=dict(detected=True)) as detect:
                    memory = _build_memory_store(pack, root / mode, root / "resources", map_ui=ui, production=True)
                    self.assertIsInstance(memory, NightmareMemoryStore)
                    self.assertEqual(memory.portable_seed_path, root / "resources/references/nightmare-portable-layout-seeds.json")
                    self.assertTrue(memory.sample_validator(self.capture.frame))
                    if mode == "nightmare":
                        detect.return_value = dict(detected=False)
                    else:
                        ui.observe.return_value = dict(mapOpen=False)
                    self.assertFalse(memory.sample_validator(self.capture.frame))
                    isolated = _build_memory_store(pack, root / "isolated", root / "resources", map_ui=ui, production=False)
                    self.assertEqual(isolated.portable_seed_path, root / "resources/.qt-no-portable-seeds.json")
                    self.assertFalse(memory.log_path.exists())

    def test_state_clone_reowns_new_transaction_without_mutating_published_owner(self):
        context = ("nightmare", 1, "1", 1, 0)
        original, _ = stage_similarity_rank(None, rows(), context, "pixels")
        baseline = copy.deepcopy(original.snapshot()); owner = original.owner
        result = []
        def advance():
            state, _ = stage_similarity_rank(original, rows(IDS[0]), context, "pixels2")
            self.assertNotEqual(state.owner, owner)
            self.assertTrue(state.confirm(IDS[0], "1", context))
            result.append(state.snapshot())
        worker = threading.Thread(target=advance); worker.start(); worker.join(4)
        self.assertEqual(original.snapshot(), baseline)
        self.assertEqual(result[0]["state"], "MEMBER_VERIFIED")

    def test_claimed_group_hard_gate_is_sanitized_against_final_rank_rows(self):
        ranked = rows(); ranked[0]["nativeSimilarity"] = proposal(IDS[0])
        state, sanitized = stage_similarity_rank(None, ranked, ("nightmare", 1, "1", 1, 0), "pixels")
        self.assertFalse(sanitized["originalIdentityHardGatePassed"])
        self.assertIsNone(state.pending_identity)

if __name__ == "__main__":
    unittest.main()
