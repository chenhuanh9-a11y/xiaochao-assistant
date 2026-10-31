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
        self.engine.rank=Mock(return_value=[dict(mapId=B,strongSuggestion=True,identityHardGatePassed=True,entryKind="main",difficultEntry=dict(mapId=B,floor="1",entryKind="main",verification=dict(success=True,matrixSourceToClient=np.eye(3).tolist())),nativeSimilarity=dict(status="conditions",mode="nightmare",floor="1",resourceKey="shortcut",complete=True,memberIds=[B],candidateMemberId=B,originalIdentityHardGatePassed=True,conditionObservations=[]))])
    def test_v2_local_cohort_does_not_replace_verified_member_with_sorted_default(self):
        self.engine.native_similarity_index=v2_index()
        result=self.begin_and_capture();state=self.runtime.snapshot()
        self.assertEqual(state["identityPhase"],"MEMBER_VERIFIED")
        self.assertEqual(state["mapId"],B)
        self.assertFalse(state["groupMemberIds"])
        self.assertEqual(self.runtime.render_payload(result["renderToken"])["mapId"],B)
        family=registered_logical_family(self.engine,B,"1",entrance_kind="front")
        self.assertEqual(family,dict(status="none",reason="no-authoritative-map-level-registration"))
        self.engine.native_similarity_index.condition_index.validate_domains.assert_not_called()

    def test_v2_unobserved_overlap_and_metadata_do_not_form_or_block_global_family(self):
        for index in (v2_index(extra=True),v2_index(bad=True)):
            with self.subTest(index=index):
                self.engine.native_similarity_index=index
                self.dispatch("start_round");result=self.dispatch("capture_now")
                self.assertTrue(result["success"],result)
                self.assertEqual(self.runtime.snapshot()["mapId"],B)
                self.assertEqual(self.runtime.snapshot()["identityPhase"],"MEMBER_VERIFIED")
                self.assertFalse(self.runtime.snapshot()["groupMemberIds"])
                index.condition_index.validate_domains.assert_not_called()
                self.dispatch("end_round")

    def test_invalid_explicit_registry_still_does_not_become_ordinary_unique(self):
        self.engine.qt_logical_group_registry=SimpleNamespace(mode="nightmare",error="bad-schema")
        self.dispatch("start_round");self.assertFalse(self.dispatch("capture_now")["success"])
        self.assertFalse(self.runtime.snapshot()["identityLocked"])

    def test_both_modes_v2_hard_shortcut_keeps_original_unique_path(self):
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
                self.assertEqual(self.runtime.snapshot()["identityPhase"],"MEMBER_VERIFIED")
                self.assertEqual(self.runtime.snapshot()["mapId"],B)
                self.assertEqual(self.runtime.render_payload(result["renderToken"])["mapId"],B)
                self.dispatch("end_round")

    def test_both_modes_dynamic_native_subset_is_evidence_not_manual_group(self):
        import test_client_similarity_protocol as groups
        from client_live_runtime import LiveRuntime
        for mode in ("nightmare","difficult"):
            with self.subTest(mode=mode):
                engine=fixtures.fixture_engine(mode)
                for mid in (B,C):
                    item=SimpleNamespace(id=mid);engine.pack.maps.append(item);engine.pack.by_id[mid]=item
                index=v2_index(mode=mode)
                index.condition_index.conditions["abc"]=dict(mode=mode,domainId="d-abc",poseIds=["a","b","c"])
                engine.native_similarity_index=index;engine.rank=Mock(return_value=groups.rows(mode=mode))
                self.runtime=LiveRuntime(engine_factories={mode:lambda:engine},capture_provider=self.capture,evidence_kind="synthetic",clock=lambda:1.)
                self.dispatch("set_context",mode=mode);self.dispatch("start_round");result=self.dispatch("capture_now")
                self.assertFalse(result["success"])
                state=self.runtime.snapshot()
                self.assertFalse(state["groupMemberIds"]);self.assertFalse(state["groupMemberRows"])
                self.assertFalse(state["groupLocked"]);self.assertIsNone(state["groupId"])
                self.assertNotIn("select_group_member",state["availableCommands"])
                self.assertEqual(set(self.runtime._identity_native.member_ids),{A,B})
                self.assertIsNotNone(engine.native_similarity_context_provider())
                self.assertFalse(self.dispatch("select_group_member",mapId=B)["success"])
                index.condition_index.validate_domains.assert_not_called()
                self.dispatch("end_round")

    def test_independent_explicit_registry_coexists_with_native_structure_conditions(self):
        import test_client_similarity_protocol as groups
        structural_index=v2_index()
        self.engine.native_similarity_index=structural_index
        self.engine.qt_logical_group_registry=groups.explicit_registry()
        ranked=groups.rows(B);original=copy.deepcopy(ranked)
        self.engine.rank.return_value=ranked
        result=self.begin_and_capture();self.assertTrue(result["success"],result)
        self.assertIs(self.engine.native_similarity_index,structural_index)
        self.assertEqual(set(self.runtime.snapshot()["groupMemberIds"]),{A,B})
        self.assertEqual(self.runtime.snapshot()["provisionalMapId"],B)
        self.assertEqual(ranked,original)
        structural_index.condition_index.validate_domains.assert_not_called()
        self.engine.qt_logical_group_registry.validate.assert_called_once()

    def test_overlapping_dynamic_native_candidates_never_become_manual_group(self):
        import test_client_similarity_protocol as groups
        index=v2_index();index.condition_index.conditions["ac"]=dict(mode="nightmare",domainId="d-ac",poseIds=["a","c"])
        ranked=groups.rows();ranked[1]["mapId"]=C;ranked[1]["difficultEntry"]["mapId"]=C
        observation=ranked[0]["nativeSimilarity"]["conditionObservations"][0]
        observation["memberIds"]=[A,C];observation["poseMaps"]={"a":A,"b":C}
        self.engine.native_similarity_index=index;self.engine.rank.return_value=ranked
        self.dispatch("start_round");result=self.dispatch("capture_now")
        self.assertFalse(result["success"])
        self.assertFalse(self.runtime.snapshot()["groupMemberIds"])
        self.assertEqual(set(self.runtime._identity_native.member_ids),{A,C})
        self.assertFalse(self.runtime.snapshot()["identityLocked"])

    def test_recorded_18_rank_verdict_is_not_replaced_by_local_04_default(self):
        # Exact neutral rank flags from the recorded18 regression; fit remains
        # controlled here. The private saved-frame numerical replay is separate.
        winner="nightmare-18";members=["nightmare-04","nightmare-12",winner,"nightmare-19"]
        for mid in members:
            self.engine.pack.maps.append(SimpleNamespace(id=mid));self.engine.pack.by_id[mid]=self.engine.pack.maps[-1]
        self.engine.native_similarity_index=v2_index()
        native=dict(status="conditions",mode="nightmare",floor="1",resourceKey="recorded-index",
                    complete=True,memberIds=[winner],candidateMemberId=winner,
                    originalIdentityHardGatePassed=True,conditionObservations=[])
        self.engine.rank.return_value=[dict(mapId=winner,score=1211.,strongSuggestion=True,
                                           identityHardGatePassed=True,nativeSimilarity=native)]
        result=self.begin_and_capture();self.assertTrue(result["success"],result)
        self.assertEqual(self.runtime.snapshot()["mapId"],winner)
        self.assertEqual(self.runtime.render_payload(result["renderToken"])["mapId"],winner)

    def test_unregistered_single_map_still_has_original_unique_path(self):
        self.engine.native_similarity_index=SimpleNamespace(mode="nightmare",error=None,index_digest="empty",condition_index=None,group_for=lambda mid:None)
        self.assertTrue(self.begin_and_capture()["success"]);self.assertEqual(self.runtime.snapshot()["mapId"],B)

    def test_explicit_v1_family_preserves_current_verified_provisional_witness(self):
        import test_client_similarity_protocol as groups
        group=dict(groupId="explicit-family",memberIds=[A,B,C])
        index=SimpleNamespace(mode="nightmare",error=None,index_digest="v1-index",condition_index=None,
                              group_for=lambda mid, **scope:group if scope==dict(floor="1",entrance_kind="front") else None,validate=Mock())
        self.engine.qt_logical_group_registry=index;self.engine.rank.return_value=groups.rows(B)
        result=self.begin_and_capture();self.assertTrue(result["success"],result)
        self.assertEqual(self.runtime.snapshot()["provisionalMapId"],B)
        self.assertFalse(self.runtime.snapshot()["identityLocked"])
        self.assertFalse(self.runtime.snapshot()["manualGroupMemberHeld"])
        self.assertEqual(self.runtime.render_payload(result["renderToken"])["mapId"],B)
        index.validate.assert_called_once()

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
                engine.qt_logical_group_registry=SimpleNamespace(mode="nightmare",error=None,index_digest=original.index.index_digest,condition_index=None,
                    group_for=lambda mid, **scope: original.index.group_for(mid) if scope==dict(floor="1",entrance_kind="front") else None,validate=original.index.validate)
                def provider(profile,mid,floor):
                    ref=original.refs[(mid,str(floor))]
                    with np.load(ref.path,allow_pickle=False) as data:mask=data["shared_mask"].copy()
                    return SharedStructureReference(mask,mask,ref.metadata,ref.path)
                engine.qt_shared_provider=provider
                winner=engine.pack.maps[-1].id
                engine.rank=lambda *a,**k:[dict(mapId=winner,strongSuggestion=True,identityHardGatePassed=True,entryKind="main",difficultEntry=dict(mapId=winner,floor="1",entryKind="main",verification=dict(success=True)))]
                runtime=LiveRuntime(engine_factories={"nightmare":lambda:engine},capture_provider=owner.capture,evidence_kind="synthetic",clock=lambda:1.)
                runtime.dispatch("start_round",{})
                with patch("map_interface_observation.detect_selected_floor",return_value=dict(selectedFloor=1)):
                    result=runtime.dispatch("capture_now",{})
                self.assertTrue(result["success"],result);state=runtime.snapshot()
                self.assertEqual(state["identityPhase"],"GROUP_LOCKED");self.assertEqual(state["groupId"],"three");self.assertEqual(state["floorSource"],"automatic");self.assertEqual(state["floor"],"1")
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
