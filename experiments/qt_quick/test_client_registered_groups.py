"""Registered-family adapter/Runtime tests; controlled rank/fit, no native IO."""
import copy,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import numpy as np
from client_engine import registered_logical_family
import test_client_live_runtime as fixtures

A,B,C="fixture-map","second-map","third-map"

def v2_index(extra=False,bad=False,mode="nightmare"):
    refs={"a":dict(mapId=A,floor="1"),"b":dict(mapId=B,floor="1"),"c":dict(mapId=C,floor="1")}
    conditions=SimpleNamespace(references=refs,poses={k:dict(target=k) for k in refs},conditions={"ab":dict(mode=mode,domainId="d-ab",poseIds=["a","b"])},validate_domains=Mock(side_effect=ValueError("changed-registration") if bad else None))
    if extra:conditions.conditions["bc"]=dict(mode=mode,domainId="d-bc",poseIds=["b","c"])
    return SimpleNamespace(mode=mode,error=None,index_digest="registered-index",condition_index=conditions)

class RegisteredRuntimeTests(unittest.TestCase):
    dispatch=fixtures.LiveRuntimeContracts.dispatch
    begin_and_capture=fixtures.LiveRuntimeContracts.begin_and_capture
    def setUp(self):
        fixtures.LiveRuntimeContracts.setUp(self)
        for mid in (B,C):
            item=SimpleNamespace(id=mid);self.engine.pack.maps.append(item);self.engine.pack.by_id[mid]=item
        self.engine.rank=Mock(return_value=[dict(mapId=B,strongSuggestion=True,identityHardGatePassed=True,nativeSimilarity=dict(status="conditions",mode="nightmare",floor="1",resourceKey="shortcut",complete=True,memberIds=[B],candidateMemberId=B,originalIdentityHardGatePassed=True,conditionObservations=[]))])
    def test_v2_single_member_shortcut_becomes_one_validated_family_not_unique_member(self):
        self.engine.native_similarity_index=v2_index()
        result=self.begin_and_capture();state=self.runtime.snapshot()
        self.assertEqual(state["identityPhase"],"GROUP_LOCKED");self.assertEqual(state["provisionalMapId"],A)
        self.assertEqual(set(state["groupMemberIds"]),{A,B});self.assertFalse(state["identityLocked"])
        self.assertFalse(self.runtime._identity_native.condition_complete);self.assertFalse(self.runtime._identity_native.condition_domains)
        self.assertIsNone(self.runtime._identity_native.pending_identity)
        self.assertIsNone(self.engine.native_similarity_priority_provider())
        self.assertIsNone(self.engine.native_similarity_context_provider())
        self.assertEqual(self.runtime.render_payload(result["renderToken"])["mapId"],A)
        self.engine.native_similarity_index.condition_index.validate_domains.assert_called_once()
    def test_overlap_is_not_unioned_or_member_unique_fallback(self):
        self.engine.native_similarity_index=v2_index(extra=True)
        self.dispatch("start_round");result=self.dispatch("capture_now");state=self.runtime.snapshot()
        self.assertFalse(result["success"]);self.assertFalse(state["identityLocked"])
        self.assertEqual(len(state["automaticCandidateRows"]),2)
        self.assertEqual({tuple(r["memberIds"]) for r in state["automaticCandidateRows"]},{(A,B),(B,C)})
        self.assertFalse(state["displayAuthorized"])
    def test_invalid_registration_does_not_become_ordinary_unique(self):
        for index in (v2_index(bad=True),SimpleNamespace(mode="nightmare",error="bad-schema")):
            self.engine.native_similarity_index=index
            self.dispatch("start_round");self.assertFalse(self.dispatch("capture_now")["success"])
            self.assertFalse(self.runtime.snapshot()["identityLocked"]);self.dispatch("end_round")
    def test_both_modes_shortcut_family_and_invalid_registry_use_same_runtime_branch(self):
        from client_live_runtime import LiveRuntime
        for mode in ("nightmare","difficult"):
            with self.subTest(mode=mode):
                engine=fixtures.fixture_engine(mode)
                item=SimpleNamespace(id=B);engine.pack.maps.append(item);engine.pack.by_id[B]=item
                engine.rank=Mock(return_value=[dict(mapId=B,strongSuggestion=True,identityHardGatePassed=True)])
                engine.native_similarity_index=v2_index(mode=mode)
                self.runtime=LiveRuntime(engine_factories={mode:lambda:engine},capture_provider=self.capture,evidence_kind="synthetic",clock=lambda:1.)
                self.dispatch("set_context",mode=mode);self.dispatch("start_round")
                result=self.dispatch("capture_now");self.assertTrue(result["success"],result)
                self.assertEqual(self.runtime.snapshot()["identityPhase"],"GROUP_LOCKED")
                self.assertEqual(self.runtime.snapshot()["provisionalMapId"],A)
                self.dispatch("end_round");engine.native_similarity_index=v2_index(bad=True,mode=mode)
                self.dispatch("start_round");self.assertFalse(self.dispatch("capture_now")["success"])
                self.assertFalse(self.runtime.snapshot()["identityLocked"])

    def test_registered_winner_with_unsupported_floor_cannot_fall_back_unique(self):
        index=v2_index();index.condition_index.references["b"]["floor"]="99"
        self.engine.native_similarity_index=index
        self.dispatch("start_round");result=self.dispatch("capture_now")
        self.assertFalse(result["success"])
        self.assertFalse(self.runtime.snapshot()["identityLocked"])
        self.assertFalse(self.runtime.snapshot()["displayAuthorized"])

    def test_nested_views_use_existing_declared_maximal_without_constructing_union(self):
        index=v2_index()
        index.condition_index.conditions["abc"]=dict(mode="nightmare",domainId="d-abc",poseIds=["a","b","c"])
        self.engine.native_similarity_index=index
        result=self.begin_and_capture();state=self.runtime.snapshot()
        self.assertEqual(set(state["groupMemberIds"]),{A,B,C})
        self.assertEqual(state["identityPhase"],"GROUP_LOCKED")
        self.assertFalse(state["identityLocked"])
        self.assertEqual(len(state["automaticCandidateRows"]),1)

    def test_observed_native_subset_is_expanded_only_to_registered_maximal(self):
        import test_client_similarity_protocol as groups
        index=v2_index();index.condition_index.conditions["abc"]=dict(mode="nightmare",domainId="d-abc",poseIds=["a","b","c"])
        self.engine.native_similarity_index=index
        self.engine.rank.return_value=groups.rows()
        result=self.begin_and_capture();self.assertEqual(set(self.runtime.snapshot()["groupMemberIds"]),{A,B,C})
        self.assertEqual(self.runtime.render_payload(result["renderToken"])["mapId"],A)

    def test_native_subset_cannot_filter_away_incompatible_registered_overlap(self):
        import test_client_similarity_protocol as groups
        index=v2_index();index.condition_index.conditions["ac"]=dict(mode="nightmare",domainId="d-ac",poseIds=["a","c"])
        ranked=groups.rows()
        ranked[1]["mapId"]=C;ranked[1]["difficultEntry"]["mapId"]=C
        observation=ranked[0]["nativeSimilarity"]["conditionObservations"][0]
        observation["memberIds"]=[A,C];observation["poseMaps"]={"a":A,"b":C}
        self.engine.native_similarity_index=index;self.engine.rank.return_value=ranked
        self.dispatch("start_round");result=self.dispatch("capture_now")
        self.assertFalse(result["success"]);state=self.runtime.snapshot()
        self.assertFalse(state["identityLocked"]);self.assertFalse(state["displayAuthorized"])
        self.assertEqual({tuple(r["memberIds"]) for r in state["automaticCandidateRows"]},{(A,B),(A,C)})

    def test_unregistered_single_map_still_has_original_unique_path(self):
        self.engine.native_similarity_index=SimpleNamespace(mode="nightmare",error=None,index_digest="empty",condition_index=None,group_for=lambda mid:None)
        self.assertTrue(self.begin_and_capture()["success"]);self.assertEqual(self.runtime.snapshot()["mapId"],B)

class ActualV1RegistryTests(unittest.TestCase):
    def test_real_registered_hash_binding_checks_and_runtime_without_condition_domains(self):
        # Reuse the original tiny synthetic native registry fixture/validator;
        # importing its Tk baseline module does not construct an app/listener.
        sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"tests"))
        from test_native_similarity_runtime import Fixture
        from shared_structure_reference import SharedStructureReference
        from client_live_runtime import LiveRuntime
        owner=fixtures.LiveRuntimeContracts();owner.setUp()
        try:
            with tempfile.TemporaryDirectory() as temp:
                original=Fixture(Path(temp));engine=owner.engine
                engine.pack=original.pack;engine.pack.floors=("1","2");engine.native_similarity_index=original.index
                def provider(profile,mid,floor):
                    ref=original.refs[(mid,str(floor))]
                    with np.load(ref.path,allow_pickle=False) as data:mask=data["shared_mask"].copy()
                    return SharedStructureReference(mask,mask,ref.metadata,ref.path)
                engine.qt_shared_provider=provider
                winner=engine.pack.maps[-1].id
                engine.rank=lambda *a,**k:[dict(mapId=winner,strongSuggestion=True,identityHardGatePassed=True)]
                runtime=LiveRuntime(engine_factories={"nightmare":lambda:engine},capture_provider=owner.capture,evidence_kind="synthetic",clock=lambda:1.)
                runtime.dispatch("start_round",{})
                with patch("map_interface_observation.detect_selected_floor",return_value=dict(selectedFloor=2)):
                    result=runtime.dispatch("capture_now",{})
                self.assertTrue(result["success"],result);state=runtime.snapshot()
                self.assertEqual(state["identityPhase"],"GROUP_LOCKED");self.assertEqual(state["groupId"],"three");self.assertEqual(state["floorSource"],"automatic");self.assertEqual(state["floor"],"2")
                self.assertFalse(runtime._identity_native.condition_domains)
                self.assertIsNone(engine.native_similarity_priority_provider())
                self.assertIsNone(engine.native_similarity_context_provider())
                self.assertEqual(runtime.render_payload(result["renderToken"])["mapId"],sorted(original.pack.by_id)[0])
                # A changed referenced binary cannot silently downgrade to unique.
                next(iter(original.refs.values())).path.write_bytes(b"changed")
                runtime.dispatch("end_round",{});runtime.dispatch("start_round",{})
                rejected=runtime.dispatch("capture_now",{});self.assertFalse(rejected["success"])
                self.assertFalse(runtime.snapshot()["identityLocked"])
        finally:owner.doCleanups()
