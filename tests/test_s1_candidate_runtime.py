"""Synthetic control/lifecycle seams. Real pixel replay is a separate harness."""
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from client_live_runtime import LiveRuntime
from test_client_live_runtime import FixtureCapture,fixture_engine
from s1_structure_selection import StructureCatalog,StructureCandidateScanner


class Registry(dict):
    def __init__(self):
        super().__init__()
        self.engine=fixture_engine()
        self.engine.pack.floors=self.engine.identity_floors=("-1","-2","-3","-4")
        self.engine.pack.default_entry_floor="-1"
        self.engine.pack.maps=[SimpleNamespace(id=k,name=k) for k in ("a","b")]
        self.engine.pack.by_id={x.id:x for x in self.engine.pack.maps}
        self.capability=dict(candidateReady=True,observedFloors=(-1,-2),liveValidated=False)
        self.engine.qt_native_context=SimpleNamespace(structure_candidate_capability=self.capability)
        self.engine.qt_live_ready=False
        self.engine.qt_live_gaps=("s1-floor-controls-unregistered",)
        self.catalog=StructureCatalog("s1","nightmare","fixture",("a","b"),self.engine.identity_floors)
        def scan(frame,**kwargs):
            def fit(*args,**kw):
                return dict(success=True,coordinateSpace="shared-structure-to-client",matrixSourceToClient=np.eye(3),
                    selectedRouteCurrentFrameResidual=dict(verified=True,poseVerifiedAgainstSharedStructure=True,withinThreePixels=1.))
            return StructureCandidateScanner(self.catalog,fit).scan(frame,**kwargs)
        self.engine.qt_s1_structure_scan=scan
    def capabilities(self,season="s0"):
        return {mode:dict(available=True,prepared=True,reason="fixture",liveReady=False,
            liveGaps=["s1-floor-controls-unregistered"],candidateObservation=self.capability)
            for mode in ("nightmare","difficult","difficulty2")}
    def get_engine(self,mode,season):
        assert season=="s1";return self.engine
    def prepare(self,*args,**kwargs):return self.capabilities()


class CandidateRuntimeContracts(unittest.TestCase):
    def setUp(self):
        self.registry=Registry();self.capture=FixtureCapture()
        self.runtime=LiveRuntime(engine_factories=self.registry,capture_provider=self.capture,evidence_kind="recorded")
        self.runtime.configure_settings(dict(mapSelectionMode="auto"))
        self.addCleanup(self.runtime.shutdown)
        patched=patch("s1_structure_selection.StructureCatalog.from_engine",return_value=self.registry.catalog)
        patched.start();self.addCleanup(patched.stop)
        self.floor="-1"
        observation=patch.object(self.runtime,"_observe",side_effect=lambda *args:(dict(detected=True,mapOpen=True),self.floor,
            dict(selectedFloor=int(self.floor) if self.floor is not None else None,mapOpen=True,season="s1",mode="nightmare")))
        observation.start();self.addCleanup(observation.stop)
        self.assertTrue(self.runtime.dispatch("set_context",dict(season="s1",mode="nightmare"))["success"])
        self.assertTrue(self.runtime.dispatch("start_round")["success"])

    def capture_candidates(self):
        receipt=self.runtime.dispatch("capture_now");self.assertTrue(receipt["success"],receipt)
        return self.runtime.snapshot()

    def payload(self,state,mid="a"):
        index=next(i for i,row in enumerate(state["candidateRows"]) if row["mapId"]==mid)
        row=state["candidateRows"][index]
        return dict(index=index,**{k:row[k] for k in ("mapId","structureFrameId","structureGeneration","structureRoundId")})

    def test_two_candidate_choices_are_current_without_identity_or_group(self):
        state=self.capture_candidates()
        self.assertEqual(self.runtime._core.floors,("-1","-2","-3","-4"))
        for mid in ("a","b"):
            result=self.runtime.dispatch("select_candidate",self.payload(state,mid))
            self.assertTrue(result["success"],result)
            latest=self.runtime.snapshot()
            self.assertFalse(latest["identityLocked"])
            self.assertFalse(latest["displayAuthorized"])
            self.assertFalse(latest["groupLocked"])
            self.assertEqual(latest["selectedCandidate"]["mapId"],mid)

    def test_map_identity_not_unstable_list_index_controls_choice(self):
        state=self.capture_candidates();payload=self.payload(state,"b");payload["index"]=0
        self.assertTrue(self.runtime.dispatch("select_candidate",payload)["success"])
        self.assertEqual(self.runtime.snapshot()["selectedCandidate"]["mapId"],"b")

    def test_stale_new_frame_and_each_context_field_are_rejected(self):
        state=self.capture_candidates();old=self.payload(state)
        self.capture_candidates()
        self.assertFalse(self.runtime.dispatch("select_candidate",old)["success"])
        current=self.payload(self.runtime.snapshot())
        for key in ("structureFrameId","structureGeneration","structureRoundId"):
            bad=dict(current);bad[key]="wrong"
            self.assertFalse(self.runtime.dispatch("select_candidate",bad)["success"])
        bad=dict(current,mapId="foreign")
        self.assertFalse(self.runtime.dispatch("select_candidate",bad)["success"])

    def test_old_candidate_is_withdrawn_before_blocked_capture(self):
        state=self.capture_candidates();payload=self.payload(state)
        entered,release=threading.Event(),threading.Event()
        original=self.capture.capture
        def blocked(cancelled=None):
            entered.set();self.assertTrue(release.wait(3));return original(cancelled)
        result=[]
        with patch.object(self.capture,"capture",side_effect=blocked):
            worker=threading.Thread(target=lambda:result.append(self.runtime.dispatch("capture_now")))
            worker.start()
            try:
                self.assertTrue(entered.wait(3))
                current=self.runtime.snapshot()
                self.assertFalse(current["automaticCandidateRows"])
                self.assertFalse(current["structureEvidence"]["currentSupportedMapIds"])
                self.assertFalse(self.runtime.dispatch("select_candidate",payload)["success"])
            finally:
                release.set();worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertTrue(result[0]["success"],result)
        self.assertFalse(self.runtime.snapshot()["manualSelectionPending"])

    def test_unknown_current_floor_withdraws_old_support_but_not_history(self):
        self.capture_candidates();self.floor=None
        result=self.runtime.dispatch("capture_now")
        self.assertFalse(result["success"])
        state=self.runtime.snapshot()
        self.assertFalse(state["automaticCandidateRows"])
        self.assertFalse(state["structureEvidence"]["currentSupportedMapIds"])
        self.assertEqual(state["structureEvidence"]["observationCount"],1)

    def test_manual_s1_choice_uses_stable_map_id_after_reordering(self):
        self.capture_candidates()
        result=self.runtime.dispatch("select_candidate",dict(index=0,candidateMapId="b"))
        self.assertTrue(result["success"])
        self.assertEqual(self.runtime.snapshot()["selectedCandidate"]["mapId"],"b")
        self.assertFalse(self.runtime.snapshot()["identityLocked"])

    def test_close_revokes_candidate_presentation_and_old_click(self):
        state=self.capture_candidates();payload=self.payload(state)
        self.runtime.dispatch("close_map")
        snapshot=self.runtime.snapshot()
        self.assertFalse(snapshot["automaticCandidateRows"])
        self.assertTrue(all(not r["structureSupported"] for r in snapshot["candidateRows"]))
        self.assertFalse(self.runtime.dispatch("select_candidate",payload)["success"])

    def test_new_floor_keeps_only_diagnostic_history_not_old_click_pose(self):
        state=self.capture_candidates();payload=self.payload(state)
        self.floor="-2";new=self.capture_candidates()
        self.assertEqual(new["observedFloor"],"-2")
        self.assertEqual(new["structureEvidence"]["floors"],["-1","-2"])
        self.assertFalse(self.runtime.dispatch("select_candidate",payload)["success"])
        self.assertFalse(new["identityLocked"])

    def test_end_round_during_scanner_cannot_restore_candidates(self):
        entered,release=threading.Event(),threading.Event()
        original=self.registry.engine.qt_s1_structure_scan
        def delayed(*args,**kwargs):
            entered.set();self.assertTrue(release.wait(3));return original(*args,**kwargs)
        self.registry.engine.qt_s1_structure_scan=delayed
        result=[]
        worker=threading.Thread(target=lambda:result.append(self.runtime.dispatch("capture_now")))
        worker.start()
        try:
            self.assertTrue(entered.wait(3));self.runtime.dispatch("end_round")
        finally:
            release.set();worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertFalse(result[0]["success"])
        state=self.runtime.snapshot()
        self.assertFalse(state["roundActive"])
        self.assertFalse(state["automaticCandidateRows"])
        self.assertFalse(state["structureEvidence"])

    def test_evidence_flag_cannot_open_actual_live_runtime(self):
        live=LiveRuntime(engine_factories=self.registry,capture_provider=FixtureCapture(),evidence_kind="live-client-capture")
        try:
            live.dispatch("set_context",dict(season="s1",mode="nightmare"))
            receipt=live.dispatch("start_round")
            self.assertFalse(receipt["success"])
            self.assertTrue(receipt["unavailable"])
            self.assertFalse(live.snapshot()["recordedStructureCandidateAvailable"])
        finally:live.shutdown()

    def test_unverified_b3_and_b4_cannot_become_runtime_observed_floors(self):
        for floor in (-3,-4):
            reading=dict(selectedFloor=floor,observedFloorValidated=False,mapOpen=True,
                         mapObservation=dict(detected=True),reason="synthetic")
            with patch("s1_floor_observation.detect_s1_floor",return_value=reading):
                # Explicitly bypass only this test's synthetic _observe fixture.
                result,selected,read=LiveRuntime._observe(self.runtime,self.registry.engine,self.capture.frame)
            self.assertIsNone(selected)
            self.assertIsNone(read["selectedFloor"])
            self.assertTrue(result["mapOpen"])


if __name__=="__main__":unittest.main()
