"""Actual Session queue contracts, controlled capture/fit, no game or native input."""
import os,copy,threading,unittest
from types import SimpleNamespace
from unittest.mock import Mock
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
from client_live_runtime import LiveRuntime
from client_session import SessionService
import test_client_live_runtime as fixtures
import test_client_similarity_protocol as groups
from test_client_session import APP,until

class GroupQueueTests(unittest.TestCase):
    def test_both_modes_reopening_preserves_choice_and_new_round_expires_old_domain(self):
        for mode in ("nightmare", "difficult"):
            with self.subTest(mode=mode):
                fx=fixtures.LiveRuntimeContracts();fx.setUp()
                try:
                    engine=fixtures.fixture_engine(mode)
                    new_ids=("third-map","fourth-map")
                    for mid in (groups.IDS[1], *new_ids):
                        item=SimpleNamespace(id=mid);engine.pack.maps.append(item);engine.pack.by_id[mid]=item
                    engine.qt_logical_group_registry=groups.explicit_registry(mode,(groups.IDS,new_ids))
                    engine.rank=Mock(return_value=groups.rows(mode=mode))
                    engine.rank_established_group=Mock(side_effect=AssertionError("no internal member review"))
                    fx.runtime=LiveRuntime(engine_factories={mode:lambda:engine},capture_provider=fx.capture,
                        evidence_kind="synthetic",clock=lambda:1.)
                    fx.runtime.dispatch("set_context",dict(mode=mode));session=SessionService(fx.runtime)
                    def drive(action, **payload):
                        session.dispatchWithPayload(action,payload);until(lambda:not session.busy)
                        receipt=session.snapshot["lastReceipt"];self.assertEqual(receipt.get("action"),action)
                        return receipt
                    def choose(mid):
                        state=session.snapshot
                        return drive("select_group_member",mapId=mid,
                            **{k:state[k] for k in ("groupId","groupGeneration","cycleId","floor")})
                    drive("start_round");self.assertTrue(drive("capture_now")["success"])
                    token=choose(groups.IDS[1])["pendingSiblingToken"]
                    self.assertTrue(fx.runtime.acknowledge_sibling(token,True)["success"]);session.refreshRuntime()
                    engine.rank.return_value=groups.rows(groups.IDS[0],mode)
                    result=drive("capture_now");self.assertTrue(result["success"],result)
                    self.assertEqual(session.snapshot["currentGroupMemberId"],groups.IDS[1])
                    self.assertEqual(session.snapshot["desiredGroupMemberId"],groups.IDS[1])
                    self.assertTrue(session.snapshot["manualGroupMemberHeld"])
                    self.assertEqual(session.snapshot["identitySource"],"manual-group-member")
                    count=engine.rank.call_count;drive("refresh_map")
                    self.assertEqual(engine.rank.call_count,count)
                    # Reopening fits the chosen member. It does not turn a
                    # different visible entrance into an automatic re-selection.
                    engine.rank.return_value=[]
                    self.assertTrue(drive("capture_now")["success"])
                    self.assertTrue(session.snapshot["groupLocked"])
                    self.assertEqual(engine.rank.call_count,count)
                    self.assertEqual(session.snapshot["desiredGroupMemberId"],groups.IDS[1])
                    self.assertEqual(session.snapshot["currentGroupMemberId"],groups.IDS[1])
                    old_pending=choose(groups.IDS[0])["pendingSiblingToken"]
                    old_scope={k:session.snapshot[k] for k in ("groupId","groupGeneration","cycleId","floor")}
                    ranked=groups.rows(mode=mode)
                    for row,mid in zip(ranked,new_ids):
                        row["mapId"]=mid;row["difficultEntry"]["mapId"]=mid
                    native=ranked[0]["nativeSimilarity"];native["resourceKey"]="different-family"
                    observation=native["conditionObservations"][0]
                    observation["memberIds"]=list(new_ids);observation["poseMaps"]={"a":new_ids[0],"b":new_ids[1]}
                    engine.rank.return_value=ranked
                    drive("end_round");drive("start_round")
                    result=drive("capture_now");self.assertTrue(result["success"],result)
                    self.assertEqual(set(session.snapshot["groupMemberIds"]),set(new_ids))
                    self.assertFalse(session.snapshot["desiredGroupMemberId"])
                    self.assertFalse(session.snapshot["manualGroupMemberHeld"])
                    self.assertFalse(fx.runtime.acknowledge_sibling(old_pending,True)["success"])
                    self.assertFalse(drive("select_group_member",mapId=groups.IDS[0],**old_scope)["success"])
                    for surface in (fx.runtime.render_payload(result["renderToken"]),fx.runtime.reference_payload()):
                        self.assertIn(surface["mapId"],new_ids);self.assertEqual(surface["floor"],"1")
                    engine.rank_established_group.assert_not_called()
                    drive("end_round");session.shutdown()
                finally:fx.doCleanups()

    def test_both_modes_failed_desire_delay_material_ack_and_currentness_retry(self):
        for mode in ("nightmare","difficult"):
            with self.subTest(mode=mode):
                fx=fixtures.LiveRuntimeContracts();fx.setUp()
                try:
                    engine=fixtures.fixture_engine(mode);item=SimpleNamespace(id=groups.IDS[1]);engine.pack.maps.append(item);engine.pack.by_id[item.id]=item
                    engine.qt_logical_group_registry=groups.explicit_registry(mode)
                    engine.rank=Mock(return_value=groups.rows(mode=mode));engine.rank_established_group=Mock(side_effect=AssertionError("no member competition"))
                    fx.runtime=LiveRuntime(engine_factories={mode:lambda:engine},capture_provider=fx.capture,evidence_kind="synthetic",clock=lambda:1.)
                    fx.runtime.dispatch("set_context",dict(mode=mode));session=SessionService(fx.runtime)
                    def drive(action,**payload):
                        session.dispatchWithPayload(action,payload);until(lambda:not session.busy)
                        receipt=session.snapshot["lastReceipt"];self.assertEqual(receipt.get("action"),action,session.snapshot)
                        return receipt
                    drive("start_round");self.assertTrue(drive("capture_now")["success"])
                    good=copy.deepcopy(fx.fit);fx.fit=dict(success=False,reason="initial-structure-ambiguous")
                    state=session.snapshot;request={k:state[k] for k in ("groupId","groupGeneration","cycleId","floor")}
                    result=drive("select_group_member",mapId=item.id,**request)
                    self.assertTrue(result["success"]);self.assertFalse(result["choiceApplied"])
                    self.assertEqual(session.snapshot["desiredGroupMemberId"],item.id)
                    self.assertEqual(session.snapshot["currentGroupMemberId"],groups.IDS[0])
                    self.assertEqual(session.snapshot["groupMemberChoiceStatus"],"awaiting-geometry")
                    fx.fit=good
                    result=drive("refresh_map");pending=result["pendingSiblingToken"]
                    self.assertEqual(session.snapshot["currentGroupMemberId"],groups.IDS[0])
                    count=fx.capture.count
                    self.assertTrue(drive("refresh_map").get("coalesced"))
                    self.assertEqual(fx.capture.count,count)
                    self.assertIsNotNone(fx.runtime.pending_sibling_payload(pending))
                    # A genuinely changed frame context rejects rather than weakens ACK.
                    fx.runtime.invalidate("foreground-lost")
                    self.assertFalse(fx.runtime.acknowledge_sibling(pending,True)["success"])
                    self.assertEqual(fx.runtime.snapshot()["desiredGroupMemberId"],item.id)
                    session.refreshRuntime();result=drive("capture_now");target=result["pendingSiblingToken"]
                    self.assertTrue(fx.runtime.acknowledge_sibling(target,True)["success"])
                    session.refreshRuntime();self.assertEqual(session.snapshot["currentGroupMemberId"],item.id)
                    self.assertEqual(session.snapshot["groupMemberChoiceStatus"],"confirmed")
                    drive("end_round");self.assertFalse(session.snapshot["desiredGroupMemberId"])
                    session.shutdown()
                finally:fx.doCleanups()

    def test_actual_queue_steady_full_solve_has_new_tokens_and_each_current_material_ack(self):
        fx=fixtures.LiveRuntimeContracts();fx.setUp()
        try:
            session=SessionService(fx.runtime);workers=[];original=fx.runtime.dispatch
            def observed(*a,**k):workers.append(threading.get_ident());return original(*a,**k)
            fx.runtime.dispatch=observed
            def drive(action):
                session.dispatch(action);until(lambda:not session.busy)
                receipt=session.snapshot["lastReceipt"];self.assertEqual(receipt.get("action"),action)
                self.assertTrue(receipt["success"],receipt);return receipt
            drive("start_round");first=drive("capture_now")["renderToken"]
            self.assertTrue(fx.runtime.acknowledge_render_material(first)["success"])
            fx.runtime._steady_material_ready=True
            tokens=[first]
            for number in (1,2):
                fx.capture.frame[0,0,0]=number
                fx.fit["matrixSourceToClient"][0,2]+=1
                result=drive("refresh_map");token=result["renderToken"]
                self.assertNotIn(token,tokens);self.assertIsNone(fx.runtime.render_payload(tokens[-1]))
                payload=fx.runtime.render_payload(token);self.assertIsNotNone(payload)
                self.assertEqual(payload["mapId"],"fixture-map")
                self.assertTrue(fx.runtime.acknowledge_render_material(token)["success"])
                self.assertFalse(fx.runtime.acknowledge_render_material(token)["success"])
                tokens.append(token)
            self.assertEqual(len(set(workers)),1);self.assertNotEqual(workers[0],threading.get_ident())
            drive("end_round");session.shutdown()
        finally:fx.doCleanups()
