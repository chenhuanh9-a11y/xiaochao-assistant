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

def proposal(winner=None, mode="nightmare", floor="1"):
    return dict(status="conditions", mode=mode, floor=floor, resourceKey="fixture-index-"+floor,
        complete=True, candidateMemberId=winner, originalIdentityHardGatePassed=bool(winner),
        conditionObservations=[dict(domainId="entrance", conditionId="family",
            completeForeignMapCompetition=True, currentCompetitionComplete=True,
            ambiguousViews=[], outsideCompetitors=[], activeViews=["a", "b"], expectedPoseIds=["a", "b"],
            memberIds=list(IDS), poseMaps={"a": IDS[0], "b": IDS[1]}, pairs=[])])

def rows(winner=None, mode="nightmare", floor="1"):
    result = [dict(mapId=mid, identityHardGatePassed=mid == winner, strongSuggestion=False,
        difficultEntry=dict(mapId=mid, floor=floor, verification=dict(success=True,
            matrixSourceToClient=np.eye(3).tolist()))) for mid in IDS]
    result[0]["nativeSimilarity"] = proposal(winner,mode,floor)
    return result

def explicit_registry(mode="nightmare", member_sets=(IDS,)):
    """Test-only schema1 registrations; never a production membership table."""
    declarations=[dict(groupId="fixture-reviewed-"+str(i),memberIds=list(members))
                  for i,members in enumerate(member_sets)]
    return SimpleNamespace(mode=mode,error=None,index_digest="explicit-fixture-registry",condition_index=None,
        group_for=lambda mid:next((g for g in declarations if mid in g["memberIds"]),None),validate=Mock())


class SimilarityProtocolTests(unittest.TestCase):
    setUp = fixtures.LiveRuntimeContracts.setUp
    dispatch = fixtures.LiveRuntimeContracts.dispatch

    def test_group_evidence_summary_preserves_failed_observation_counts_without_samples(self):
        import json
        from client_engine import similarity_evidence_summary
        native=proposal();native["complete"]=False
        observation=native["conditionObservations"][0]
        observation.update(ambiguousViews=["a"],conflictingViews=True,outsideCompetitors=["outside"],
                           currentCompetitionComplete=False)
        state=NativeSimilarityState()
        evidence=similarity_evidence_summary(native,state)
        self.assertEqual(evidence,dict(status="conditions",complete=False,observationCount=1,
            foreignCompetitionCompleteCount=1,currentCompetitionCompleteCount=0,
            ambiguousCount=1,conflictCount=1,outsideCount=1,eligibleMembers=[]))
        state.member_ids=tuple(f"map-{i:02}" for i in range(100))
        self.assertEqual(len(similarity_evidence_summary(native,state)["eligibleMembers"]),16)
        self.assertLess(len(json.dumps(evidence).encode()),1024)

    def prepare_group(self):
        self.capture.frame.fill(0)
        second = SimpleNamespace(id=IDS[1])
        self.engine.pack.maps.append(second); self.engine.pack.by_id[second.id] = second
        self.engine.qt_logical_group_registry=explicit_registry(self.engine.pack.mode)
        self.engine.rank = Mock(return_value=rows(mode=self.engine.pack.mode))
        self.engine.rank_established_group = Mock(return_value=rows(mode=self.engine.pack.mode))
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

    def test_both_modes_hard_unique_empty_conditions_use_original_strong_top_fallback(self):
        from client_live_runtime import LiveRuntime
        for mode in ("nightmare","difficult"):
            with self.subTest(mode=mode):
                self.engine=fixtures.fixture_engine(mode)
                native=dict(status="conditions",mode=mode,floor="1",resourceKey="empty-fixture-index",
                    conditionObservations=[],candidateMemberId=IDS[0],memberIds=[IDS[0]],
                    originalIdentityHardGatePassed=True,complete=True)
                self.engine.rank=Mock(return_value=[dict(mapId=IDS[0],strongSuggestion=True,
                    identityHardGatePassed=True,nativeSimilarity=native)])
                self.runtime=LiveRuntime(engine_factories={mode:lambda:self.engine},capture_provider=self.capture,
                    evidence_kind="synthetic",clock=lambda:1.)
                self.dispatch("set_context",mode=mode)
                events=[];self.runtime.set_event_callback(events.append)
                self.dispatch("start_round")
                result=self.dispatch("capture_now")
                self.assertTrue(result["success"],result)
                self.assertTrue(result.get("renderToken"))
                state=self.runtime.snapshot()
                self.assertEqual(state["mapId"],IDS[0]);self.assertTrue(state["identityLocked"])
                self.assertEqual(self.runtime._identity_native.member_ids,())  # no fabricated bindings
                self.assertEqual(state["identitySource"],"automatic-hard-gate")
                self.assertFalse(self.runtime.render_payload(result["renderToken"])["liveAuthorization"])
                verdict=next(e["rankVerdict"] for e in events if "rankVerdict" in e)
                self.assertEqual(verdict["establishedMemberIds"],[])
                self.assertTrue(verdict["topStrongSuggestion"])
                self.dispatch("end_round")

    def test_empty_native_group_does_not_bypass_top_strong(self):
        native=dict(status="conditions",mode="nightmare",floor="1",resourceKey="empty-index",
            conditionObservations=[],candidateMemberId=IDS[0],memberIds=[IDS[0]],
            originalIdentityHardGatePassed=True,complete=True)
        cases=[
            [dict(mapId=IDS[0],strongSuggestion=False,identityHardGatePassed=True,nativeSimilarity=native)],
            [dict(mapId=IDS[0],identityHardGatePassed=True,nativeSimilarity=native)],
            [dict(mapId=IDS[1],strongSuggestion=True,identityHardGatePassed=False,nativeSimilarity=native),
             dict(mapId=IDS[0],strongSuggestion=True,identityHardGatePassed=True)]]
        second=SimpleNamespace(id=IDS[1]);self.engine.pack.maps.append(second);self.engine.pack.by_id[second.id]=second
        for ranked in cases:
            with self.subTest(ranked=ranked):
                self.engine.rank=Mock(return_value=ranked)
                self.dispatch("start_round")
                with patch.object(_AlignmentHost,"fit",side_effect=AssertionError("non-strong/non-top must not fit")):
                    self.dispatch("capture_now")
                self.assertFalse(self.runtime.snapshot()["identityLocked"])
                self.assertFalse(self.runtime.snapshot()["displayAuthorized"])
                self.dispatch("end_round")

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
        self.engine.rank_established_group.assert_not_called()
        self.assertIsNone(self.engine.native_similarity_context_provider())
        # Repeated pixels do not manufacture an extra temporal observation.
        self.assertEqual(len(self.runtime._identity_native.observations), 0)

    def test_established_logical_group_never_runs_internal_member_competition(self):
        self.capture_group()
        self.engine.rank_established_group.side_effect=AssertionError("member competition forbidden")
        self.engine.rank.return_value=rows(IDS[1])
        initial_calls=self.engine.rank.call_count
        for action in ("capture_now","refresh_map"):
            self.now+=2.;self.capture.frame[0,0,0]+=1
            with patch.object(_AlignmentHost,"_canonical_geometry_current_frame_residual",return_value=(self.fit["selectedRouteCurrentFrameResidual"],None)):
                self.assertTrue(self.dispatch(action)["success"])
        self.assertEqual(self.engine.rank.call_count,initial_calls+1)
        self.engine.rank_established_group.assert_not_called()
        state=self.runtime.snapshot();self.assertEqual(len(state["automaticCandidateRows"]),1)
        candidate=state["automaticCandidateRows"][0]
        self.assertEqual(candidate["identityKind"],"group");self.assertEqual(candidate["groupId"],state["groupId"])
        self.assertEqual(set(candidate["memberIds"]),set(IDS));self.assertFalse(candidate["mapId"])
        self.assertEqual(state["identitySource"],"group-default");self.assertFalse(state["identityLocked"])

    def test_explicit_map_open_rediscovers_family_without_reselecting_its_member(self):
        self.capture_group()
        count=self.engine.rank.call_count
        self.engine.rank.return_value=rows(IDS[1])
        self.engine.rank_established_group.side_effect=AssertionError("no member review")
        result=self.dispatch("map",openIntent=True)
        self.assertTrue(result["success"],result)
        self.assertEqual(self.engine.rank.call_count,count+1)
        self.assertEqual(self.runtime.snapshot()["provisionalMapId"],IDS[0])
        self.assertFalse(self.runtime.snapshot()["manualGroupMemberHeld"])

    def test_sibling_material_must_commit_before_cache_receipt_and_worker_remember(self):
        token = self.pending_sibling()["pendingSiblingToken"]
        events = []; self.runtime.set_event_callback(events.append)
        self.assertFalse(self.runtime.acknowledge_render_material(token)["success"])
        with patch.object(self.runtime._steady, "remember_committed", return_value=True) as remember:
            self.assertTrue(self.runtime.acknowledge_sibling(token, True)["success"])
            remember.assert_not_called()
            self.assertEqual(sum(e["event"] == "render-material-accepted" for e in events), 1)
            result = self.runtime.dispatch("remember_render_material", {"renderToken": token})
            self.assertTrue(result["success"], result)
            remember.assert_called_once()
            self.assertIs(remember.call_args.args[0].group, self.runtime._group)

    def group_choice(self, target=None, action="select_group_member", **changes):
        state=self.runtime.snapshot()
        payload={key:state[key] for key in ("groupId","groupGeneration","cycleId","floor")}
        if target is not None:payload["mapId"]=target
        payload.update(changes)
        return self.dispatch(action,**payload)

    def test_manual_group_member_commits_only_after_material_and_holds_until_end(self):
        source=self.capture_group()["renderToken"]
        with patch.object(self.runtime,"_learn_confirmation",side_effect=AssertionError("new chooser must not train")):
            result=self.group_choice(IDS[1])
            self.assertTrue(result["success"],result)
            target=result["pendingSiblingToken"]
            self.assertTrue(self.runtime.snapshot()["groupMemberSelectionPending"])
            self.assertFalse(self.runtime.snapshot()["manualGroupMemberHeld"])
            self.assertEqual(self.runtime.render_payload(source)["mapId"],IDS[0])
            self.assertTrue(self.runtime.acknowledge_sibling(target,True)["success"])
        state=self.runtime.snapshot()
        self.assertEqual((state["identityPhase"],state["identitySource"],state["mapId"]),
            ("MANUAL_MEMBER_CONFIRMED","manual-group-member",IDS[1]))
        self.assertTrue(state["manualGroupMemberHeld"])
        self.assertEqual(self.runtime._identity_native.state,"GROUP_LOCKED")
        self.assertEqual(self.runtime.reference_payload()["mapId"],IDS[1])
        with patch.object(_AlignmentHost,"fit",side_effect=AssertionError("same manual choice must be idempotent")):
            self.assertEqual(self.group_choice(IDS[1])["reason"],"already-current")
        self.engine.rank_established_group.reset_mock()
        self.now=8.;self.capture.frame[0,0,0]=8
        self.assertTrue(self.dispatch("capture_now")["success"])
        self.engine.rank_established_group.assert_not_called()
        self.dispatch("set_route_profile",profileId="another-author")
        self.assertTrue(self.runtime.snapshot()["manualGroupMemberHeld"])
        self.dispatch("close_map")
        self.assertTrue(self.runtime.snapshot()["manualGroupMemberHeld"])
        self.assertFalse(self.group_choice(action="resume_group_automatic")["success"])
        self.assertTrue(self.runtime.snapshot()["manualGroupMemberHeld"])
        self.assertNotIn("resume_group_automatic", self.runtime.snapshot()["availableCommands"])
        self.dispatch("end_round")
        self.assertFalse(self.runtime.snapshot()["manualGroupMemberHeld"])

    def test_manual_group_hold_survives_floor_change_but_new_floor_requires_own_pose(self):
        self.capture_group()
        pending=self.group_choice(IDS[1])["pendingSiblingToken"]
        self.assertTrue(self.runtime.acknowledge_sibling(pending,True)["success"])
        result=self.dispatch("set_floor",floor="2")
        self.assertEqual(result["reason"],"current-frame-floor-conflict")
        self.assertTrue(self.runtime.snapshot()["manualGroupMemberHeld"])
        self.assertFalse(self.runtime.snapshot()["displayAuthorized"])
        self.engine.rank.return_value=rows(IDS[1],floor="2")
        with patch("map_interface_observation.detect_selected_floor",return_value=dict(selectedFloor=2)):
            result=self.dispatch("capture_now")
            self.assertTrue(result["success"],result)
        state=self.runtime.snapshot()
        self.assertEqual((state["mapId"],state["floor"]),(IDS[1],"2"))
        self.assertEqual(self.runtime.reference_payload()["floor"],"2")
        self.assertTrue(state["manualGroupMemberHeld"])

    def test_new_manual_intent_immediately_rejects_old_auto_ack_during_target_fit(self):
        self.capture_group()
        legacy=clone_similarity_state(self.runtime._identity_native)
        legacy.pending_identity=(IDS[0],"1",self.runtime._identity_context())
        old=self.runtime._stage_current_member_material("legacy-test",{},legacy);old_token=old["pendingSiblingToken"]
        source=old["renderToken"];entered,release=threading.Event(),threading.Event()
        fit=copy.deepcopy(self.fit);output=[]
        def blocked(*a,**kw):entered.set();release.wait(2);return fit
        with patch.object(_AlignmentHost,"fit",side_effect=blocked):
            worker=threading.Thread(target=lambda:output.append(self.group_choice(IDS[1])))
            worker.start();self.assertTrue(entered.wait(2))
            self.assertTrue(self.runtime.snapshot()["groupMemberSelectionPending"])
            self.assertFalse(self.runtime.acknowledge_sibling(old_token,True)["success"])
            self.assertEqual(self.runtime.render_payload(source)["mapId"],IDS[0])
            release.set();worker.join(2)
        self.assertFalse(worker.is_alive());self.assertTrue(output[0]["success"],output)
        target=output[0]["pendingSiblingToken"]
        self.assertIsNotNone(self.runtime.pending_sibling_payload(target))
        generation=self.runtime.snapshot()["generation"]
        self.assertTrue(self.dispatch("refresh_map").get("coalesced"))
        self.assertEqual(self.runtime.snapshot()["generation"],generation)
        self.assertIsNotNone(self.runtime.pending_sibling_payload(target))
        self.assertTrue(self.runtime.acknowledge_sibling(target,True)["success"])
        self.assertEqual(self.runtime.snapshot()["identityPhase"],"MANUAL_MEMBER_CONFIRMED")

    def test_passive_refresh_does_not_launch_member_review(self):
        self.capture_group()
        self.engine.rank_established_group.side_effect=AssertionError("routine member review removed")
        self.now=3.;self.capture.frame[0,0,0]=3
        with patch.object(_AlignmentHost,"_canonical_geometry_current_frame_residual",return_value=(self.fit["selectedRouteCurrentFrameResidual"],None)):
            self.assertTrue(self.dispatch("refresh_map")["success"])
        self.assertEqual(self.runtime.snapshot()["provisionalMapId"],IDS[0])
        self.assertFalse(self.runtime.snapshot()["manualGroupMemberHeld"])

    def test_reselect_acceptance_clears_manual_hold_and_old_map_authority(self):
        self.capture_group()
        token=self.group_choice(IDS[1])["pendingSiblingToken"]
        self.assertTrue(self.runtime.acknowledge_sibling(token,True)["success"])
        self.assertTrue(self.dispatch("reselect_map")["success"])
        state=self.runtime.snapshot()
        self.assertFalse(state["manualGroupMemberHeld"])
        self.assertFalse(state["identityLocked"])
        self.assertFalse(state["displayAuthorized"])
        self.assertTrue(state["candidateEditMode"])
        self.assertEqual(state["selectedCandidate"]["mapId"],IDS[1])
        self.assertIsNone(self.runtime.reference_payload())

    def test_manual_same_provisional_uses_current_pose_but_still_waits_material(self):
        self.capture_group()
        with patch.object(_AlignmentHost,"fit",side_effect=AssertionError("same current member must reuse fit")):
            result=self.group_choice(IDS[0])
        self.assertEqual(self.runtime.snapshot()["identityPhase"],"GROUP_LOCKED")
        self.assertTrue(self.runtime.acknowledge_sibling(result["pendingSiblingToken"],True)["success"])
        self.assertEqual(self.runtime.snapshot()["identityPhase"],"MANUAL_MEMBER_CONFIRMED")

    def test_manual_group_failure_or_rejected_material_keeps_source_and_rejects_stale_scope(self):
        source=self.capture_group()["renderToken"]
        self.fit=dict(success=False,reason="controlled-target-pose-failure")
        deferred=self.group_choice(IDS[1]);self.assertTrue(deferred["success"])
        self.assertEqual(deferred["reason"],"group-member-awaiting-geometry")
        self.assertEqual(self.runtime.snapshot()["desiredGroupMemberId"],IDS[1])
        self.assertEqual(self.runtime.snapshot()["groupMemberChoiceStatus"],"awaiting-geometry")
        self.assertEqual(self.runtime.render_payload(source)["mapId"],IDS[0])
        self.assertFalse(self.runtime.snapshot()["manualGroupMemberHeld"])
        with patch.object(_AlignmentHost,"fit",side_effect=AssertionError("invalid choice reached fit")):
            self.assertFalse(self.group_choice("outside")["success"])
            self.assertFalse(self.group_choice(IDS[1],floor="2")["success"])
        self.fit=dict(
            success=True,matrixSourceToClient=np.array([[.5,0,40],[0,.5,60],[0,0,1]]),
            selectedRouteCurrentFrameResidual=dict(verified=True,poseVerifiedAgainstCanonicalGeometry=True,
                profileRenderDerivedFromCanonical=True,coordinatePackId="fixture-pack",
                validationBasis="shared-canonical-geometry-current-frame"))
        pending=self.group_choice(IDS[1])["pendingSiblingToken"]
        self.assertFalse(self.runtime.acknowledge_sibling(pending,False,"Image.Error")["success"])
        self.assertEqual(self.runtime.render_payload(source)["mapId"],IDS[0])
        self.assertFalse(self.runtime.snapshot()["manualGroupMemberHeld"])

    def test_manual_group_target_can_be_verified_without_existing_source_pose(self):
        good=copy.deepcopy(self.fit)
        self.prepare_group();self.fit=dict(success=False,reason="source-current-pose-failed")
        self.assertFalse(self.dispatch("capture_now")["success"])
        self.assertIsNone(self.runtime._render)
        self.fit=good
        result=self.group_choice(IDS[1])
        self.assertTrue(result["success"],result)
        target=result["pendingSiblingToken"]
        pending=self.runtime.pending_sibling_payload(target)
        self.assertEqual(pending["sourceToken"],"")
        self.assertTrue(self.runtime.acknowledge_sibling(target,True)["success"])
        self.assertEqual(self.runtime.snapshot()["mapId"],IDS[1])
        self.assertTrue(self.runtime.snapshot()["manualGroupMemberHeld"])

    def test_manual_group_late_target_fit_after_end_never_replaces_source_or_sets_hold(self):
        self.capture_group()
        entered,release=threading.Event(),threading.Event();good=copy.deepcopy(self.fit)
        def blocked(*a,**kw):entered.set();release.wait(2);return good
        output=[]
        with patch.object(_AlignmentHost,"fit",side_effect=blocked):
            worker=threading.Thread(target=lambda:output.append(self.group_choice(IDS[1])))
            worker.start();self.assertTrue(entered.wait(2))
            self.dispatch("end_round");release.set();worker.join(2)
        self.assertFalse(worker.is_alive());self.assertFalse(output[0]["success"])
        self.assertFalse(self.runtime.snapshot()["manualGroupMemberHeld"])
        self.assertIsNone(self.runtime._render)

    def test_shared_both_modes_sticky_group_survives_winner_author_and_floor_index(self):
        from client_live_runtime import LiveRuntime
        for mode in ("nightmare","difficult"):
            with self.subTest(mode=mode):
                self.engine=fixtures.fixture_engine(mode)
                self.runtime=LiveRuntime(engine_factories={mode:lambda:self.engine},capture_provider=self.capture,evidence_kind="synthetic",clock=lambda:1.)
                self.dispatch("set_context",mode=mode)
                self.capture_group()
                self.engine.rank_established_group.return_value=rows(IDS[1],mode)
                self.capture.frame[0,0,0]=9
                result=self.dispatch("capture_now");self.assertTrue(result["success"],result)
                self.assertNotIn("pendingSiblingToken",result)
                self.assertEqual(self.runtime.snapshot()["provisionalMapId"],IDS[0])
                self.assertFalse(self.runtime.snapshot()["manualGroupMemberHeld"])
                self.assertTrue(self.dispatch("set_route_profile",profileId="another-author")["success"])
                self.assertEqual(self.runtime.snapshot()["provisionalMapId"],IDS[0])
                self.engine.rank.return_value=rows(IDS[1],mode,"2")
                self.engine.qt_map_ui.observe=lambda *a:dict(mapOpen=True,floor="2")
                with patch("map_interface_observation.detect_selected_floor",return_value=dict(selectedFloor=2)):
                    self.assertTrue(self.dispatch("set_floor",floor="2")["success"])
                    result=self.dispatch("capture_now");self.assertTrue(result["success"],result)
                self.assertEqual(self.runtime.snapshot()["provisionalMapId"],IDS[0])
                for layer in (self.runtime.render_payload(result["renderToken"]),self.runtime.reference_payload()):
                    self.assertEqual((layer["mapId"],layer["floor"]),(IDS[0],"2"))
                self.dispatch("end_round")
                self.assertEqual(self.runtime.snapshot()["identityPhase"],"UNLOCKED")

    def test_different_discovered_group_can_initially_select_and_resource_reset_clears_it(self):
        self.capture_group()
        new_ids=("third-map","fourth-map")
        self.engine.qt_logical_group_registry=explicit_registry(member_sets=(IDS,new_ids))
        for mid in new_ids:
            item=SimpleNamespace(id=mid);self.engine.pack.maps.append(item);self.engine.pack.by_id[mid]=item
        ranked=rows()
        for row,mid in zip(ranked,new_ids):
            row["mapId"]=mid;row["difficultEntry"]["mapId"]=mid
        proposal=ranked[0]["nativeSimilarity"];proposal["resourceKey"]="another-logical-group"
        condition=proposal["conditionObservations"][0];condition["memberIds"]=list(new_ids);condition["poseMaps"]={"a":new_ids[0],"b":new_ids[1]}
        self.runtime.invalidate("resources-changed")
        self.engine.rank.return_value=ranked
        self.dispatch("start_round")
        self.capture.frame[0,0,0]=5
        result=self.dispatch("capture_now");self.assertTrue(result["success"],result)
        state=self.runtime.snapshot();self.assertEqual(set(state["groupMemberIds"]),set(new_ids))
        self.assertIn(state["provisionalMapId"],new_ids);self.assertFalse(state["manualGroupMemberHeld"])
        self.runtime.invalidate("resources-changed")
        state=self.runtime.snapshot();self.assertFalse(state["roundActive"]);self.assertFalse(state["groupMemberIds"])
        self.assertIsNone(self.runtime.reference_payload())

    def test_established_group_does_not_rerank_none_or_alternate_winner(self):
        self.capture_group()
        self.engine.rank_established_group.return_value=[]
        self.engine.rank.return_value=rows(IDS[1])
        result=self.dispatch("capture_now");self.assertTrue(result["success"],result)
        self.engine.rank_established_group.assert_not_called()
        self.assertEqual(self.runtime.snapshot()["provisionalMapId"],IDS[0])

    def test_initial_hard_member_winner_is_only_group_evidence_not_member_confirmation(self):
        self.prepare_group();self.engine.rank.return_value=rows(IDS[1])
        for row in self.engine.rank.return_value:row["strongSuggestion"]=True
        result=self.dispatch("capture_now");self.assertTrue(result["success"],result)
        state=self.runtime.snapshot();self.assertEqual(state["identityPhase"],"GROUP_LOCKED")
        self.assertFalse(state["identityLocked"]);self.assertFalse(state["manualGroupMemberHeld"])
        self.assertIsNone(self.runtime._identity_native.pending_identity)
        self.assertEqual(state["provisionalMapId"],IDS[1])
        self.assertEqual(len(state["automaticCandidateRows"]),1)

    def test_both_modes_initial_verified_witness_is_provisional_then_choice_stays_sticky(self):
        from client_live_runtime import LiveRuntime
        for mode in ("nightmare","difficult"):
            with self.subTest(mode=mode):
                self.engine=fixtures.fixture_engine(mode)
                self.runtime=LiveRuntime(engine_factories={mode:lambda:self.engine},capture_provider=self.capture,
                    evidence_kind="synthetic",clock=lambda:1.)
                self.dispatch("set_context",mode=mode);self.prepare_group()
                initial=rows(IDS[1],mode)
                initial[0]["difficultEntry"]["verification"]["success"]=False
                self.engine.rank.return_value=initial
                result=self.dispatch("capture_now");self.assertTrue(result["success"],result)
                state=self.runtime.snapshot()
                self.assertEqual(state["provisionalMapId"],IDS[1])
                self.assertFalse(state["identityLocked"]);self.assertFalse(state["manualGroupMemberHeld"])
                self.assertIsNone(self.runtime._identity_native.pending_identity)
                self.assertEqual(self.runtime.render_payload(result["renderToken"])["mapId"],IDS[1])
                # A subsequent automatic witness is not permission to change
                # the initial display member, even before a player confirms it.
                self.engine.rank.return_value=rows(IDS[0],mode)
                result=self.dispatch("capture_now");self.assertTrue(result["success"],result)
                self.assertEqual(self.runtime.snapshot()["currentGroupMemberId"],IDS[1])
                target=self.group_choice(IDS[0])["pendingSiblingToken"]
                self.assertTrue(self.runtime.acknowledge_sibling(target,True)["success"])
                self.engine.rank.return_value=rows(IDS[1],mode,"2")
                self.engine.qt_map_ui.observe=lambda *a:dict(mapOpen=True,floor="2")
                with patch("map_interface_observation.detect_selected_floor",return_value=dict(selectedFloor=2)):
                    self.dispatch("set_floor",floor="2")
                    result=self.dispatch("capture_now");self.assertTrue(result["success"],result)
                self.assertEqual(self.runtime.snapshot()["currentGroupMemberId"],IDS[0])
                self.assertTrue(self.runtime.snapshot()["manualGroupMemberHeld"])
                self.assertFalse(self.runtime.acknowledge_sibling(target,True)["success"])
                for surface in (self.runtime.render_payload(result["renderToken"]),self.runtime.reference_payload()):
                    self.assertEqual((surface["mapId"],surface["floor"]),(IDS[0],"2"))
                self.dispatch("end_round")

    def test_current_confirmed_member_cancels_other_desire_and_late_material_without_fit(self):
        self.capture_group()
        token=self.group_choice(IDS[0])["pendingSiblingToken"]
        self.assertTrue(self.runtime.acknowledge_sibling(token,True)["success"])
        good=copy.deepcopy(self.fit);self.fit=dict(success=False,reason="initial-structure-ambiguous")
        self.assertEqual(self.group_choice(IDS[1])["reason"],"group-member-awaiting-geometry")
        with patch.object(_AlignmentHost,"fit",side_effect=AssertionError("cancel does not fit")):
            self.assertEqual(self.group_choice(IDS[0])["reason"],"already-current")
        self.assertEqual(self.runtime.snapshot()["desiredGroupMemberId"],IDS[0])
        self.assertEqual(self.runtime.snapshot()["groupMemberChoiceStatus"],"confirmed")
        self.fit=good
        pending=self.group_choice(IDS[1])["pendingSiblingToken"]
        with patch.object(_AlignmentHost,"fit",side_effect=AssertionError("pending cancel does not fit")):
            self.assertTrue(self.group_choice(IDS[0])["success"])
        self.assertFalse(self.runtime.acknowledge_sibling(pending,True)["success"])
        self.assertEqual(self.runtime.snapshot()["mapId"],IDS[0])
        self.assertEqual(self.runtime.reference_payload()["mapId"],IDS[0])

    def test_late_failed_other_member_cannot_undo_cancel_to_current_confirmed(self):
        self.capture_group()
        token=self.group_choice(IDS[0])["pendingSiblingToken"]
        self.assertTrue(self.runtime.acknowledge_sibling(token,True)["success"])
        entered,release=threading.Event(),threading.Event();output=[]
        def blocked(*a,**k):
            entered.set();release.wait(2);return dict(success=False,reason="initial-structure-ambiguous")
        with patch.object(_AlignmentHost,"fit",side_effect=blocked):
            worker=threading.Thread(target=lambda:output.append(self.group_choice(IDS[1])))
            worker.start();self.assertTrue(entered.wait(2))
            self.assertTrue(self.group_choice(IDS[0])["success"])
            release.set();worker.join(2)
        self.assertFalse(worker.is_alive());self.assertTrue(output[0]["stale"])
        state=self.runtime.snapshot();self.assertEqual(state["desiredGroupMemberId"],IDS[0])
        self.assertEqual(state["groupMemberChoiceStatus"],"confirmed")
        self.assertEqual(state["mapId"],IDS[0])

    def test_cancelled_return_to_current_member_does_not_discard_desired_or_pending(self):
        self.capture_group()
        source=self.group_choice(IDS[0])["pendingSiblingToken"]
        self.assertTrue(self.runtime.acknowledge_sibling(source,True)["success"])
        pending=self.group_choice(IDS[1])["pendingSiblingToken"]
        before=self.runtime.snapshot();intent=self.runtime._group_intent_generation
        request={k:before[k] for k in ("groupId","groupGeneration","cycleId","floor")};request["mapId"]=IDS[0]
        cancelled=threading.Event();cancelled.set()
        result=self.runtime.dispatch("select_group_member",request,cancelled)
        self.assertFalse(result["success"]);self.assertEqual(result["reason"],"cancelled")
        after=self.runtime.snapshot();self.assertEqual(after["desiredGroupMemberId"],IDS[1])
        self.assertEqual(after["groupMemberChoiceStatus"],before["groupMemberChoiceStatus"])
        self.assertEqual(self.runtime._group_intent_generation,intent)
        self.assertIsNotNone(self.runtime.pending_sibling_payload(pending))

    def test_round_started_event_is_once_after_capture_activation_only(self):
        events=[];self.runtime.set_event_callback(events.append)
        self.dispatch("start_round")
        self.assertFalse(any(e["event"]=="round-started" for e in events))
        self.assertTrue(self.dispatch("capture_now")["success"])
        self.dispatch("capture_now")
        started=[e for e in events if e["event"]=="round-started"]
        self.assertEqual(len(started),1)
        self.assertEqual((started[0]["mode"],started[0]["roundId"]),("nightmare",1))
        self.dispatch("end_round");self.dispatch("start_round")
        with patch.object(self.capture,"enable",return_value=False):
            self.assertFalse(self.dispatch("capture_now")["success"])
        self.assertEqual(len([e for e in events if e["event"]=="round-started"]),1)

    def test_current_group_winner_does_not_promote_or_replace_initial_member(self):
        self.capture_group()
        self.engine.rank_established_group.return_value=rows(IDS[1])
        self.now=3.;self.capture.frame[0,0,0]=1
        result=self.dispatch("capture_now")
        self.assertTrue(result["success"],result)
        self.assertNotIn("pendingSiblingToken", result)
        state=self.runtime.snapshot()
        self.assertEqual((state["identityPhase"],state["mapId"],state["provisionalMapId"]),("GROUP_LOCKED","",IDS[0]))
        self.assertFalse(state["manualGroupMemberHeld"])
        for payload in (self.runtime.render_payload(result["renderToken"]),self.runtime.reference_payload()):
            self.assertEqual(payload["mapId"],IDS[0])
        self.engine.rank_established_group.reset_mock()
        with patch.object(_AlignmentHost,"_canonical_geometry_current_frame_residual",return_value=(self.fit["selectedRouteCurrentFrameResidual"],None)):
            self.assertTrue(self.dispatch("refresh_map")["success"])
        self.engine.rank_established_group.assert_not_called()

    def verify_group_member(self):
        # Exercise the retained strong-conflict consumer with an explicitly
        # staged legacy verified state, not a removed automatic producer.
        self.capture_group()
        state=clone_similarity_state(self.runtime._identity_native)
        state.pending_identity=(IDS[0], "1", self.runtime._identity_context())
        result=self.runtime._stage_current_member_material("legacy-test", {}, state)
        self.assertTrue(self.runtime.acknowledge_sibling(result["pendingSiblingToken"], True)["success"])
        self.assertEqual(self.runtime.snapshot()["identityPhase"], "MEMBER_VERIFIED")

    def test_original_strong_conflicts_require_two_distinct_current_frames(self):
        self.verify_group_member()
        prior = self.runtime._identity_prior
        published = self.runtime._identity_native
        self.fit = dict(success=False, reason="entry-reverification-failed",
                        entryCrossCheck=dict(contradicted=True))
        self.capture.frame[0, 0, 0] = 2
        first = self.dispatch("capture_now")
        self.assertFalse(first["identityReopened"])
        self.assertTrue(self.runtime.snapshot()["identityLocked"])
        saved = self.runtime._last_observation
        # Re-consuming the same failed current frame cannot count twice.
        self.assertFalse(self.runtime._consume_identity_conflict(self.fit, engine=self.engine,
            epoch=self.runtime._epoch, captured=saved["captured"],
            capture_generation=saved["capture_generation"], frame_id=saved["frame_id"],
            frame_generation=saved["generation"], cycle=saved["cycle_id"], map_id=IDS[0], floor="1"))
        self.capture.frame[0, 0, 0] = 3
        second = self.dispatch("capture_now")
        self.assertTrue(second["identityReopened"])
        state = self.runtime.snapshot()
        self.assertEqual(state["identityPhase"], "GROUP_LOCKED")
        self.assertFalse(state["identityLocked"]); self.assertFalse(state["displayAuthorized"])
        self.assertEqual(state["provisionalMapId"], IDS[0])
        self.assertEqual(self.runtime._identity_prior, prior)
        self.assertEqual(published.state, "MEMBER_VERIFIED")  # published owner was never mutated
        self.assertEqual(self.engine.rank.call_count, 1)

    def test_ordinary_failure_resets_strong_count_and_action_flags_are_not_evidence(self):
        self.verify_group_member()
        for number, strong in [(2, True), (3, False), (4, True)]:
            self.fit = dict(success=False, reason="controlled-failure",
                entryCrossCheck=dict(contradicted=strong))
            self.capture.frame[0, 0, 0] = number
            result = self.runtime.dispatch("capture_now", dict(entryCrossCheck=dict(contradicted=True)))
            self.assertFalse(result["identityReopened"])
            self.assertTrue(self.runtime.snapshot()["identityLocked"])

    def test_late_strong_fit_does_not_publish_after_end(self):
        self.verify_group_member()
        entered, release = threading.Event(), threading.Event()
        def blocked(*args, **kwargs):
            entered.set(); release.wait(2)
            return dict(success=False, reason="entry-reverification-failed",
                        entryCrossCheck=dict(contradicted=True))
        output = []
        with patch.object(_AlignmentHost, "fit", side_effect=blocked):
            worker = threading.Thread(target=lambda: output.append(self.dispatch("capture_now")))
            worker.start(); self.assertTrue(entered.wait(2))
            self.dispatch("end_round"); release.set(); worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertTrue(output[0]["stale"])
        self.assertIsNone(self.runtime._native_group_conflicts)
        self.assertEqual(self.runtime.snapshot()["identityPhase"], "UNLOCKED")

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

    def test_provisional_reference_requires_current_pose_or_prior_verified_closed_context(self):
        self.capture_group()
        reference = self.runtime.reference_payload()
        self.assertTrue(reference["provisional"])
        self.runtime._revoke_render("fixture-current-pose-revoked")
        self.assertTrue(self.runtime.snapshot()["mapOpen"])
        self.assertIsNone(self.runtime.reference_payload())
        self.dispatch("close_map")
        remembered = self.runtime.reference_payload()
        self.assertEqual(remembered["token"], reference["token"])
        self.assertTrue(remembered["provisional"])
        self.dispatch("end_round")
        self.assertIsNone(self.runtime.reference_payload())
        self.fit = dict(success=False, reason="fixture-new-round-pose-failed")
        self.assertTrue(self.dispatch("start_round")["success"])
        self.assertFalse(self.dispatch("capture_now")["success"])
        self.assertIsNone(self.runtime.reference_payload())
        self.dispatch("close_map")
        self.assertIsNone(self.runtime.reference_payload())

    def test_group_floor_transition_keeps_member_but_needs_current_pose(self):
        self.capture_group()
        self.dispatch("set_floor", floor="2")
        state = self.runtime.snapshot()
        self.assertEqual(state["mapId"], "")
        self.assertFalse(state["identityLocked"])
        self.assertEqual(state["provisionalMapId"], IDS[0])
        self.assertFalse(state["displayAuthorized"])
        self.assertIsNone(self.runtime.reference_payload())
        self.engine.rank.return_value=rows(IDS[1],floor="2")
        with patch("map_interface_observation.detect_selected_floor",return_value=dict(selectedFloor=2)):
            result=self.dispatch("capture_now")
            self.assertTrue(result["success"],result)
        self.assertEqual(self.runtime.snapshot()["provisionalMapId"],IDS[0])
        self.assertEqual(self.runtime.render_payload(self.runtime.snapshot()["render"]["token"])["floor"],"2")

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
        result = self.group_choice(IDS[1])
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
        self.assertEqual((state["identityPhase"], state["mapId"]), ("MANUAL_MEMBER_CONFIRMED", IDS[1]))
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
        with patch.object(type(self.runtime._gate), "confirm", return_value=False):
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

    def test_explicit_member_winner_does_not_create_automatic_material(self):
        self.capture_group();self.engine.rank_established_group.return_value=rows(IDS[1])
        self.now=3.;self.capture.frame[0,0,0]=4
        result=self.dispatch("capture_now")
        self.assertTrue(result["success"],result)
        self.assertNotIn("pendingSiblingToken",result)
        self.assertEqual(self.runtime.snapshot()["provisionalMapId"],IDS[0])
        self.assertIsNone(self.runtime._pending_sibling)

    def test_initial_solver_receives_original_index_candidates_only_when_enabled(self):
        host = _AlignmentHost(self.engine, settings=dict(poseIndexCandidatesEnabled=True))
        matrices = [np.eye(3)]
        self.engine.pose_index_candidates = Mock(return_value=(matrices, dict(attempted=True, reason="fixture-index")))
        with patch("fast_structure_alignment.try_alignment", return_value=None) as fast, \
                patch("single_map_structure.current_geometry", return_value={}), \
                patch("single_map_structure.solve_initial_structure", return_value=dict(copy.deepcopy(self.fit),
                    coordinateSpace="author-render-to-client", routeProfileId="shared-structure")) as solve, \
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
        self.engine.rank.side_effect = review
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

    def test_initial_none_still_cannot_authorize_a_logical_group(self):
        self.prepare_group();self.engine.rank.return_value=[]
        self.assertFalse(self.dispatch("capture_now")["success"])
        self.assertEqual(self.runtime.snapshot()["identityPhase"],"UNLOCKED")
        self.assertFalse(self.runtime.snapshot()["displayAuthorized"])

    def test_passive_review_none_keeps_established_group(self):
        self.capture_group()
        prior=self.runtime._identity_prior
        self.engine.rank_established_group.return_value=[]
        self.now=3.; self.capture.frame[0,0,0]=9
        with patch.object(_AlignmentHost,"_canonical_geometry_current_frame_residual",
                return_value=(self.fit["selectedRouteCurrentFrameResidual"],None)):
            result=self.dispatch("refresh_map")
        self.assertTrue(result["success"],result)
        self.assertEqual(self.runtime.snapshot()["identityPhase"],"GROUP_LOCKED")
        self.assertEqual(self.runtime._identity_prior,prior)
        self.assertEqual(len(self.runtime._identity_native.observations),0)

    def test_late_explicit_none_cannot_reset_a_newer_context(self):
        self.prepare_group()
        entered,release=threading.Event(),threading.Event()
        def blocked(*a,**k):
            entered.set(); self.assertTrue(release.wait(4)); return []
        self.engine.rank.side_effect=blocked
        result=[]
        worker=threading.Thread(target=lambda:result.append(self.dispatch("capture_now")))
        worker.start(); self.assertTrue(entered.wait(4))
        self.dispatch("end_round"); self.dispatch("set_game_mode",mode="difficult")
        release.set();worker.join(4)
        self.assertFalse(worker.is_alive());self.assertTrue(result[0]["stale"])
        self.assertEqual((self.runtime.snapshot()["mode"],self.runtime.snapshot()["identityPhase"]),
                         ("difficult","UNLOCKED"))

    def test_explicit_context_mismatch_resets_only_worker_owned_clone(self):
        context=("nightmare",1,"1",1,0)
        old,_=stage_similarity_rank(None,rows(),context,"first")
        mismatched=rows();mismatched[0]["nativeSimilarity"]["mode"]="difficult"
        state,reply=stage_similarity_rank(old,mismatched,context,"second")
        self.assertEqual(reply["reason"],"similarity-context-mismatch")
        self.assertEqual(state.state,"UNLOCKED")
        self.assertEqual(old.state,"GROUP_LOCKED")

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
