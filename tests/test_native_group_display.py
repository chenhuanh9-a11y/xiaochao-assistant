"""Current-frame pose transport for provisional similarity-group display."""
import copy
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest

import numpy as np

from test_native_similarity_runtime import ConditionFixture, IDS, runtime, settle_resource_validation
from native_similarity_groups import NativeSimilarityState
from map_identity_engine import MapIdentityEngine


class NativeGroupDisplayTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.f=ConditionFixture(Path(self.temp.name))
        self.host=host=runtime.DailyAssistant.__new__(runtime.DailyAssistant)
        host.native_similarity_state=NativeSimilarityState()
        host.engine=SimpleNamespace(native_similarity_index=self.f.index,
            entry_shared_reference_provider=self.f.provider)
        host.gate=runtime.RouteDisplayGate(self.f.pack)
        host.game_mode='nightmare';host.current_floor='1';host.floor_required=False
        host.map_is_open=True;host.capture_generation=10;host.active_map_cycle_id=10
        host.current_frame_generation=1;host.surface_visibility_generation=2
        host.current_frame=np.zeros((40,40,3),np.uint8)
        host._log=lambda *a,**kw:None
        host._start_alignment_v2_first_pose_worker=lambda *a,**kw:dict(reason="v2-sensor-pending")
        # Resource-coordinate adapter only; rank, group state and seed consumer
        # are real. Pixel/fit validation is exercised by saved-frame replay.
        host._shared_structure_mode_enabled=lambda:True
        host._shared_structure_pose_context=lambda mid,floor:dict(mapId=mid,floor=floor)

    def consume(self, event):
        self.host._consume_native_similarity_ranked(event)
        settle_resource_validation(self.host)

    def tearDown(self):
        lane = getattr(self.host, '_native_resource_lane', None)
        if lane:
            lane.cancel()
            deadline = time.monotonic() + 3
            while lane.snapshot()['running'] and time.monotonic() < deadline:
                time.sleep(.002)

    def event(self, members=IDS):
        proposal=self.f.propose_condition(np.zeros((40,40),np.uint8))
        rows=[dict(mapId=r['mapId'],difficultEntry=r,identityHardGatePassed=False)
              for r in self.f.current_side(members)]
        rows[0]['nativeSimilarity']=proposal
        return dict(token=10,frame=self.host.current_frame,rankSourceFrameGeneration=1,
                    rankSourceSurfaceGeneration=2,ranked=rows)

    def test_group_displays_fitted_member_without_unique_identity_or_difference(self):
        event=self.event(IDS[1:])
        self.consume(event)
        self.assertEqual(self.host.gate.confirmed_id,IDS[1])
        state=self.host.similarity_group_snapshot()
        self.assertEqual(state['state'],'GROUP_LOCKED')
        self.assertIsNone(state['verifiedMemberId'])
        self.assertEqual(state['positiveSampleCount'],0)
        seed=self.host.auto_lock_pending['poseSeed']
        self.assertEqual(seed['mapId'],IDS[1]);self.assertEqual(seed['lockGeneration'],10)
        np.testing.assert_array_equal(seed['matrixSourceToClient'],np.eye(3))
        self.assertTrue(seed['currentFrameRouteResidualRequired'])
        self.assertFalse(seed['authorizesDirectDisplay'])
        self.assertTrue(all(not row['identityHardGatePassed'] for row in event['ranked']))

    def test_unverified_ambiguous_mismatched_or_malformed_pose_cannot_seed_display(self):
        event=self.event()
        for change in ('rejected','ambiguous','map','floor','matrix','contradiction'):
            with self.subTest(change=change):
                changed=copy.deepcopy(event);entry=changed['ranked'][0]['difficultEntry']
                if change=='rejected':entry['verification']['success']=False
                if change=='ambiguous':entry['poseAmbiguous']=True
                if change=='map':entry['mapId']=IDS[1]
                if change=='floor':entry['floor']='2'
                if change=='matrix':entry['verification']['matrixSourceToClient']='broken'
                if change=='contradiction':entry['entryCrossCheck']=dict(contradicted=True)
                self.assertIsNone(self.host._native_similarity_display_seed(changed,IDS[0]))

    def test_stale_frame_or_surface_cannot_select_member(self):
        for key,value in [('token',9),('frame',self.host.current_frame.copy()),
                          ('rankSourceFrameGeneration',0),('rankSourceSurfaceGeneration',1)]:
            event=self.event();event[key]=value
            self.consume(event)
            self.assertIsNone(self.host.gate.confirmed_id)
            self.assertEqual(self.host.native_similarity_state.state,'UNLOCKED')

    def test_existing_fitted_member_stays_selected_and_evidence_preference_is_preserved(self):
        self.consume(self.event(IDS[1:]))
        state=self.host.native_similarity_state
        state.prefer_display_member(IDS)
        self.assertEqual(state.provisional_member_id,IDS[1])
        state.condition_priority=('nightmare',state.resource_key,'1',(),(IDS[1],IDS[0],IDS[2]))
        state.prefer_display_member((IDS[0],))
        self.assertEqual(state.provisional_member_id,IDS[1])

    def test_recovery_keeps_verified_current_member_pose_without_promoting_identity(self):
        self.consume(self.event())
        engine=MapIdentityEngine.__new__(MapIdentityEngine)
        engine.native_similarity_index=self.f.index
        engine.entry_shared_reference_provider=self.f.provider
        engine.entry_identity=SimpleNamespace(geometry=lambda _:dict(query=np.zeros((128,128),np.uint8),
            observationBounds=[0,0,128,128]))
        calls=[]
        def local(frame,floor,*,map_id,budget_seconds):
            calls.append(map_id)
            return self.f.current_side((map_id,),verified=map_id==IDS[1]),dict(
                complete=True,verificationComplete=True)
        engine.side_entry_identity=SimpleNamespace(rank=local)
        proposal=engine.native_similarity_recover_observation(self.host.current_frame,'1',
            prior=self.host.native_similarity_state.context_snapshot(),deadline=time.perf_counter()+1)
        self.assertEqual(calls,list(IDS))
        self.assertEqual([r['mapId'] for r in proposal['currentFrameCandidates']],[IDS[1]])
        self.assertEqual(proposal['currentFrameCandidates'][0]['locatorKind'],'side')
        self.assertFalse(proposal['originalIdentityHardGatePassed'])
        self.assertFalse(proposal['authorizesPose'])
