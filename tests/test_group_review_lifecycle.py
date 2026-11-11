"""Shared established-family scope; no retired Tk worker fixture."""
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from test_native_similarity_runtime import IDS
import test_native_group_display as display_fixture
from map_identity_engine import MapIdentityEngine
from entry_work_fixture import FixtureEntryWork

class GroupReviewTests(unittest.TestCase):
    setUp = display_fixture.NativeGroupDisplayTests.setUp

    def engine(self, *, fixture=None, engine_type=MapIdentityEngine, identity_rows=()):
        fixture = fixture or self.f
        engine = engine_type.__new__(engine_type)
        engine.pack, engine.identity_floors, engine.identity_profile_id = fixture.pack, ('1', '2'), 'zhan10-speed'
        engine.native_similarity_index, engine.entry_shared_reference_provider = fixture.index, fixture.provider
        calls = []
        def produce(frame, floor, **kwargs):
            calls.append(set(kwargs['candidate_map_ids']))
            return deepcopy(list(identity_rows)), dict(complete=True, verificationComplete=True,
                reason='controlled-source', elapsedMs=0)
        def prepare(frame, floor, **kwargs):
            return FixtureEntryWork(frame, lambda: produce(frame, floor, **kwargs))
        engine.entry_identity = SimpleNamespace(prepare_rank=prepare, geometry=lambda _: None)
        engine.side_entry_identity = SimpleNamespace(prepare_rank=prepare)
        engine.side_entry_error, engine.native_similarity_priority_provider = None, None
        engine._shared_entry_inventory = lambda _: None
        engine.prepare_source_structure_candidates = lambda *a, **kw: self.fail('global source discovery')
        engine.entry_advisory = lambda *a: self.fail('global icon advice')
        engine.front_gate_prefilter = SimpleNamespace(select=lambda *a: self.fail('must not narrow bound group again'))
        return engine, calls

    def test_prior_controls_full_group_scope_and_bad_binding_is_rejected(self):
        h = self.host
        engine, calls = self.engine()
        prior = h.native_similarity_state.context_snapshot()
        self.assertEqual(set(engine.established_candidate_scope(prior=prior)), set(IDS))
        self.assertEqual(calls, [])
        with patch.object(engine, 'rank', side_effect=AssertionError('old review collector')):
            rows = engine.rank_established_group(h.current_frame, '1', prior=prior, budget_seconds=.2)
        self.assertEqual({row['mapId'] for row in rows}, set(IDS))
        self.assertTrue(calls)
        self.assertTrue(all(scope == set(IDS) for scope in calls))
        calls.clear()
        for bad in (None, ('difficult', *prior[1:]), (prior[0], prior[1], 'wrong', prior[3]),
                    (*prior[:3], (('unknown', (0,)),))):
            self.assertIsNone(engine.established_candidate_scope(prior=bad))
            self.assertEqual(engine.rank_established_group(h.current_frame, '1', prior=bad, budget_seconds=.2), [])
        self.assertEqual(calls, [])

    def test_two_established_cohorts_keep_every_rival_in_original_rank_path(self):
        h = self.host
        for expanded, expected in ((False, IDS), (True, IDS[:2])):
            state = type(h.native_similarity_state)()
            proposal = self.f.propose_condition(np.zeros((128, 128), np.uint8),
                front=self.f.front_rows(IDS[:2] if expanded else IDS, expanded=expanded))
            state.accept(proposal, ('fixture',), 1)
            prior = state.context_snapshot()
            self.assertIsNotNone(prior)
            engine, calls = self.engine()
            self.assertEqual(set(engine.established_candidate_scope(prior=prior)), set(expected))
            rows = engine.rank_established_group(h.current_frame, '1', prior=prior, budget_seconds=1)
            self.assertEqual({row['mapId'] for row in rows}, set(expected))
            self.assertTrue(all(scope == set(expected) for scope in calls))
            self.assertTrue(all(not row['identityHardGatePassed'] for row in rows))

    def test_difficult_uses_same_scoped_rank_and_keeps_missing_inventory_suppression(self):
        from pathlib import Path
        from test_native_similarity_runtime import ConditionFixture
        from difficult_native_identity import NativeDifficultCandidateEngine
        fixture = ConditionFixture(Path(self.temp.name) / 'difficult')
        fixture.pack.mode = 'difficult'
        for row in fixture.payload['references']:
            row['mode'] = 'difficult'
            row['referenceId'] = row['referenceId'].replace('nightmare/', 'difficult/')
        for row in fixture.payload['poses']:
            row['target'] = row['target'].replace('nightmare/', 'difficult/')
        for row in fixture.payload['domains']:
            row['source'] = row['source'].replace('nightmare/', 'difficult/')
        for row in fixture.payload['conditions']:
            row['mode'] = 'difficult'
        fixture.write()
        state = type(self.host.native_similarity_state)()
        state.accept(fixture.propose_condition(np.zeros((128, 128), np.uint8)), ('fixture',), 1)
        prior = state.context_snapshot()
        self.assertEqual(prior[0], 'difficult')
        identity = dict(mapId=IDS[0], floor='1', score=12., identityVerified=True,
            poseVerified=True, entryKind='main', profileId='zhan10-speed',
            verification=dict(success=True, matrixSourceToClient=np.eye(3).tolist()))
        engine, calls = self.engine(fixture=fixture, engine_type=NativeDifficultCandidateEngine,
                                    identity_rows=(identity,))
        rows = engine.rank_established_group(np.zeros((2, 2, 3), np.uint8), '1', prior=prior, budget_seconds=1)
        self.assertTrue(rows[0]['identityHardGatePassed'])
        fixture.pack.by_id[IDS[2]].layers['1'] = None
        rows = engine.rank_established_group(np.zeros((2, 2, 3), np.uint8), '1', prior=prior, budget_seconds=1)
        self.assertFalse(rows[0]['identityHardGatePassed'])
        self.assertEqual(rows[0]['nativeInventory']['missingMapIds'], [IDS[2]])
        self.assertTrue(all(scope == set(IDS) for scope in calls))
