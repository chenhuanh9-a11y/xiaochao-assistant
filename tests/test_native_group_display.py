"""Shared current-frame group recovery and stable provisional preference."""
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
import numpy as np
from test_native_similarity_runtime import ConditionFixture, IDS
from native_similarity_groups import NativeSimilarityState
from map_identity_engine import MapIdentityEngine

class NativeGroupDisplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.f = ConditionFixture(Path(self.temp.name))
        state = NativeSimilarityState()
        state.accept(self.f.propose_condition(np.zeros((40, 40), np.uint8)),
                     ('nightmare', 10, '1', 1, 2), 'shared-fixture')
        self.host = SimpleNamespace(native_similarity_state=state,
                                    current_frame=np.zeros((40, 40, 3), np.uint8))

    def test_existing_fitted_member_stays_selected_and_evidence_preference_is_preserved(self):
        self.host.native_similarity_state.prefer_display_member(IDS[1:])
        state=self.host.native_similarity_state
        state.prefer_display_member(IDS)
        self.assertEqual(state.provisional_member_id,IDS[1])
        state.condition_priority=('nightmare',state.resource_key,'1',(),(IDS[1],IDS[0],IDS[2]))
        state.prefer_display_member((IDS[0],))
        self.assertEqual(state.provisional_member_id,IDS[1])

    def test_recovery_keeps_verified_current_member_pose_without_promoting_identity(self):
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
