"""Recovery orchestration with actual registered fixture groups/consumers.

Source proposals, verify/refine actions and observed points are controlled here;
search/fitting are covered separately. Original recovery selection, validated
priority, required-rival accounting, pose equivalence and engine rank run.
"""
from copy import deepcopy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

import test_native_similarity_runtime as native_fixture
from map_identity_engine import MapIdentityEngine
from entry_work_fixture import FixtureEntryWork


IDS = native_fixture.IDS


class SidePoseRecoveryJobTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.fixture = native_fixture.Fixture(Path(temp.name))
        e = self.engine = MapIdentityEngine.__new__(MapIdentityEngine)
        e.pack, e.identity_floors, e.identity_profile_id = self.fixture.pack, ('1', '2'), 'zhan10-speed'
        e.native_similarity_index, e.entry_shared_reference_provider = self.fixture.index, self.fixture.provider
        self.hint = ('nightmare', self.fixture.index.validate(self.fixture.group, self.fixture.provider),
                     '1', 'three', (IDS[2], IDS[0], IDS[1]))
        e.native_similarity_priority_provider = lambda: self.hint
        e.entry_identity = SimpleNamespace(prepare_rank=lambda frame, *a, **kw: FixtureEntryWork(frame,
            lambda: ([], dict(complete=True, reason='controlled-main-source', elapsedMs=0))))
        e._shared_entry_inventory = lambda floor: None
        e.side_entry_error, e.entry_advisory = None, None
        self.source = [self.row(IDS[0], 25), self.row(IDS[1], 25),
                       self.row(IDS[2], 20, evaluated=False), self.row(IDS[2], 19, evaluated=False, pose=1)]
        self.diagnostics = dict(complete=True, verificationComplete=True, competitiveHypotheses=2,
                                identityAmbiguous=True, reason='controlled-side-source')
        self.outcomes = {(IDS[2], 0): True}
        self.refinements = []
        self.side = e.side_entry_identity = SimpleNamespace(
            prepare_rank=lambda frame, *a, **kw: FixtureEntryWork(frame,
                lambda: (deepcopy(self.source), dict(self.diagnostics))),
            verify=self.verify, refine_candidate=self.refine,
            _observe=lambda frame: ({}, [dict(point=p) for p in ([0, 0], [40, 0], [0, 40], [40, 40])], []))
        self.frame = np.zeros((64, 64, 3), np.uint8)

    @staticmethod
    def row(mid, score, *, evaluated=True, pose=0):
        return dict(mapId=mid, score=score, floor='1', profileId='zhan10-speed', entryKind='side', pose=pose,
            competitionComplete=True, poseAmbiguous=False, hypothesis=dict(chainCount=8, extentGrid=[3, 3]),
            verification=dict(success=False, evaluated=evaluated, residual=dict(verified=False),
                              matrixSourceToClient=np.eye(3).tolist()))

    def verify(self, frame, row):
        accepted = self.outcomes.get((row['mapId'], row['pose']), False)
        return dict(row['verification'], success=accepted, evaluated=True, residual=dict(verified=accepted))

    def refine(self, frame, row):
        self.refinements.append((row['mapId'], row['pose']))
        return dict(row, verification=self.verify(frame, row), continuousRefinementReason='controlled-completed-fit')

    def prepare(self):
        return self.engine.prepare_side_recovery(self.frame, '1', deepcopy(self.source), dict(self.diagnostics),
            entry_rows=[], passport_diagnostics=None, deadline=float('inf'))

    def drain(self, work):
        phases = []
        while (job := work.next_job()) is not None:
            phases.append((job.phase, job.map_id))
            job.run()
        return phases, work.finish()

    def test_actual_rank_keeps_omitted_original_rival_until_completed_rejection(self):
        rows = self.engine.rank(self.frame, '1')
        self.assertFalse(any(r['identityHardGatePassed'] for r in rows))
        meta = self.engine.last_difficult_evidence['tilemapSide']
        self.assertIn(IDS[1], meta['groupPriorityUnverifiedCompetitors'])
        self.source[1]['continuousRefinementReason'] = 'controlled-completed-rejection'
        rows = self.engine.rank(self.frame, '1')
        self.assertEqual([r['mapId'] for r in rows if r['identityHardGatePassed']], [IDS[2]])

    def test_steps_keep_original_recovery_scope_and_unfinished_rival(self):
        phases, (rows, meta, winner, original) = self.drain(self.prepare())
        self.assertEqual(phases, [('plan-recovery', None), ('recover-pose', IDS[2]),
                                  ('recover-pose', IDS[2]), ('recover-pose', IDS[0])])
        self.assertEqual({r['mapId'] for r in original}, set(IDS))
        self.assertEqual(meta['groupPriorityUnverifiedCompetitors'], [IDS[1]])
        self.assertEqual(meta['poseRecoveryEvaluated'], 3)
        self.assertFalse(meta['poseRecoveryEquivalent'])
        self.assertIsNone(winner)

    def test_priority_cannot_drop_successful_or_contradicted_unfinished_rival(self):
        self.source[1]['verification'].update(success=True, residual=dict(verified=True))
        _, (_, meta, winner, _) = self.drain(self.prepare())
        self.assertFalse(meta['poseRecoveryEquivalent'])
        self.assertIsNone(winner)
        self.source[1]['verification']['success'] = False
        self.source[1]['entryCrossCheck'] = dict(contradicted=True)
        _, (_, meta, winner, _) = self.drain(self.prepare())
        self.assertIn(IDS[1], meta['groupPriorityUnverifiedCompetitors'])
        self.assertIsNone(winner)

    def test_stopping_after_one_recovery_keeps_unknown_scope_and_closes_job(self):
        work = self.prepare()
        work.next_job().run()
        work.next_job().run()
        pending = work.next_job()
        rows, meta, winner, original = work.finish()
        self.assertFalse(meta['complete'])
        self.assertFalse(meta['verificationComplete'])
        self.assertEqual(meta['reason'], 'side-recovery-budget-incomplete')
        self.assertEqual({r['mapId'] for r in original}, set(IDS))
        self.assertIsNone(winner)
        with self.assertRaisesRegex(ValueError, 'entry-job-not-current'):
            pending.run()

    def test_single_initial_refinement_keeps_original_early_winner(self):
        self.source = [self.row(IDS[0], 25)]
        self.diagnostics.update(competitiveHypotheses=1, identityAmbiguous=False)
        self.outcomes[(IDS[0], 0)] = True
        phases, (_, meta, winner, original) = self.drain(self.prepare())
        self.assertEqual(phases, [('initial-refine', IDS[0])])
        self.assertTrue(winner['poseVerified'])
        self.assertEqual(original, [])
        self.assertEqual(meta['continuousRefinementReason'], 'controlled-completed-fit')

    def test_same_map_recovered_poses_use_current_observed_points(self):
        self.hint = None
        self.source = [self.row(IDS[2], 25), self.row(IDS[2], 24, pose=1)]
        self.diagnostics['identityAmbiguous'] = False
        self.outcomes[(IDS[2], 1)] = True
        for shift in (0., 10.):
            with self.subTest(shift=shift):
                self.source[1]['verification']['matrixSourceToClient'][0][2] = shift
                phases, (_, meta, winner, _) = self.drain(self.prepare())
                self.assertIn(('observe-equivalence', IDS[2]), phases)
                self.assertEqual(meta['poseRecoveryEquivalent'], shift == 0.)
                self.assertEqual(winner is not None, shift == 0.)

    def test_failed_recovery_closes_without_skipping_the_failed_proposal(self):
        work = self.prepare()
        work.next_job().run()
        job = work.next_job()
        with patch.object(self.side, 'verify', side_effect=OSError('reference gone')):
            with self.assertRaisesRegex(OSError, 'reference gone'):
                job.run()
        self.assertIsNone(work.next_job())
        with self.assertRaisesRegex(ValueError, 'entry-job-not-current'):
            job.run()
        _, meta, winner, _ = work.finish()
        self.assertEqual(meta['reason'], 'side-recovery-work-failed')
        self.assertFalse(meta['complete'])
        self.assertIsNone(winner)

    def test_resource_failure_in_actual_rank_is_not_budget_exhaustion(self):
        with patch.object(self.side, 'verify', side_effect=OSError('reference gone')), \
             patch.object(self.fixture.index, 'prepare_proposal', wraps=self.fixture.index.prepare_proposal) as proposed:
            rows = self.engine.rank(self.frame, '1')
        self.assertEqual(self.engine.last_difficult_evidence['tilemapSide']['reason'], 'side-resource-unavailable')
        self.assertFalse(any(r['identityHardGatePassed'] for r in rows))
        self.assertFalse(any(r.get('identityBudgetExceeded') for r in rows))
        self.assertEqual(set(proposed.call_args.kwargs['required_map_ids']), set(IDS))

    def test_actual_rank_drives_work_and_preserves_required_group_members(self):
        self.source[1]['continuousRefinementReason'] = 'controlled-completed-rejection'
        with patch.object(self.engine, 'prepare_side_recovery', wraps=self.engine.prepare_side_recovery) as prepared, \
             patch.object(self.fixture.index, 'prepare_proposal', wraps=self.fixture.index.prepare_proposal) as proposed:
            rows = self.engine.rank(self.frame, '1')
        prepared.assert_called_once()
        self.assertEqual(set(proposed.call_args.kwargs['required_map_ids']), set(IDS))
        self.assertEqual([r['mapId'] for r in rows if r['identityHardGatePassed']], [IDS[2]])


if __name__ == '__main__':
    unittest.main()
