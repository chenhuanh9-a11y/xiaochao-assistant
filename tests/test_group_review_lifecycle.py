"""Shared established-family scope; no retired Tk worker fixture."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from test_native_similarity_runtime import IDS
import test_native_group_display as display_fixture
from map_identity_engine import MapIdentityEngine

class GroupReviewTests(unittest.TestCase):
    setUp = display_fixture.NativeGroupDisplayTests.setUp

    def test_prior_controls_full_group_scope_and_bad_binding_is_rejected(self):
        h=self.host;engine=MapIdentityEngine.__new__(MapIdentityEngine)
        engine.pack=self.f.pack;engine.native_similarity_index=self.f.index
        engine.entry_shared_reference_provider=self.f.provider;calls=[]
        engine.rank=lambda *a,**kw:(calls.append(kw) or [])
        prior=h.native_similarity_state.context_snapshot()
        engine.rank_established_group(h.current_frame,'1',prior=prior,budget_seconds=.2)
        self.assertEqual(set(calls[0]['_established_members']),set(IDS))
        calls.clear()
        for bad in (None,('difficult',*prior[1:]),(prior[0],prior[1],'wrong',prior[3]),
                    (*prior[:3],(('unknown',(0,)),))):
            self.assertEqual(engine.rank_established_group(h.current_frame,'1',prior=bad,budget_seconds=.2),[])
        self.assertEqual(calls,[])

    def test_two_established_cohorts_keep_every_rival_in_original_rank_path(self):
        h=self.host
        for expanded,expected in [(False,IDS),(True,IDS[:2])]:
            state=type(h.native_similarity_state)()
            proposal=self.f.propose_condition(np.zeros((128,128),np.uint8),
                front=self.f.front_rows(IDS[:2] if expanded else IDS,expanded=expanded))
            state.accept(proposal,('fixture',),1)
            prior=state.context_snapshot()
            self.assertIsNotNone(prior)
            engine=MapIdentityEngine.__new__(MapIdentityEngine)
            engine.pack=self.f.pack;engine.identity_floors=('1','2');engine.identity_profile_id='zhan10-speed'
            engine.native_similarity_index=self.f.index;engine.entry_shared_reference_provider=self.f.provider
            calls=[]
            def produce(frame,floor,**kw):
                calls.append(set(kw['candidate_map_ids']))
                return [],dict(complete=True,verificationComplete=True,reason='fixture-empty',elapsedMs=0)
            engine.entry_identity=SimpleNamespace(rank=produce,geometry=lambda _:None)
            engine.side_entry_identity=SimpleNamespace(rank=produce)
            engine.side_entry_error=None;engine._shared_entry_inventory=lambda _:None
            engine.native_similarity_priority_provider=None
            engine._rank_source_structure_candidates=lambda *a,**kw:self.fail('global source discovery')
            engine.entry_advisory=lambda *a:self.fail('global icon advice')
            engine.front_gate_prefilter=SimpleNamespace(select=lambda *a:self.fail('must not narrow group again'))
            rows=engine.rank_established_group(h.current_frame,'1',prior=prior,budget_seconds=1)
            self.assertEqual({r['mapId'] for r in rows},set(expected))
            self.assertTrue(all(scope==set(expected) for scope in calls))
            self.assertTrue(all(not row['identityHardGatePassed'] for row in rows))

    def test_difficult_uses_same_scoped_rank_and_keeps_missing_inventory_suppression(self):
        from pathlib import Path
        from test_native_similarity_runtime import ConditionFixture
        from difficult_native_identity import NativeDifficultCandidateEngine
        f=ConditionFixture(Path(self.temp.name)/'difficult')
        f.pack.mode='difficult'
        for row in f.payload['references']:
            row['mode']='difficult';row['referenceId']=row['referenceId'].replace('nightmare/','difficult/')
        for row in f.payload['poses']:
            row['target']=row['target'].replace('nightmare/','difficult/')
        for row in f.payload['domains']:
            row['source']=row['source'].replace('nightmare/','difficult/')
        for row in f.payload['conditions']:row['mode']='difficult'
        f.write()
        state=type(self.host.native_similarity_state)()
        state.accept(f.propose_condition(np.zeros((128,128),np.uint8)),('fixture',),1)
        prior=state.context_snapshot();self.assertEqual(prior[0],'difficult')
        engine=NativeDifficultCandidateEngine.__new__(NativeDifficultCandidateEngine)
        engine.pack=f.pack;engine.native_similarity_index=f.index;engine.entry_shared_reference_provider=f.provider
        captured=[]
        def original(self,frame,floor,limit,**kw):
            captured.append(kw['_established_members'])
            return [dict(mapId=IDS[0],identityHardGatePassed=True)]
        with patch.object(MapIdentityEngine,'rank',original):
            rows=engine.rank_established_group(np.zeros((2,2,3),np.uint8),'1',prior=prior,budget_seconds=1)
            self.assertTrue(rows[0]['identityHardGatePassed'])
            f.pack.by_id[IDS[2]].layers['1']=None
            rows=engine.rank_established_group(np.zeros((2,2,3),np.uint8),'1',prior=prior,budget_seconds=1)
            self.assertFalse(rows[0]['identityHardGatePassed'])
            self.assertEqual(rows[0]['nativeInventory']['missingMapIds'],[IDS[2]])
        self.assertTrue(all(set(scope)==set(IDS) for scope in captured))
