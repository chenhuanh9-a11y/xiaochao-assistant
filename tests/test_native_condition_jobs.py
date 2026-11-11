"""Native similarity proposals use the shared sequential work contract."""
import sys
import tempfile
import time
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from entry_evidence_work import EntryEvidenceWork, ObservationWorkScheduler
from native_similarity_groups import NativeSimilarityState
from test_native_similarity_runtime import ConditionFixture, Fixture, IDS


class NativeConditionJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = ConditionFixture(Path(self.temp.name))
        self.assertIsNone(self.fixture.index.error)

    def make_work(self, *, front=None, side=(), prior=None, required=None,
                  geometry=None, deadline=None):
        q = np.zeros((40, 40), np.uint8)
        if geometry is None:
            geometry = lambda: dict(query=q, observationBounds=[0, 0, 40, 40])
        return self.fixture.index.prepare_proposal((),
            self.fixture.front_rows() if front is None else front,
            dict(complete=True, entryInventoryComplete=True), '1',
            self.fixture.provider, geometry, side_rows=side,
            side_diagnostics=dict(complete=True), deadline=deadline,
            prior=prior, required_map_ids=(tuple(required)
                if required is not None else None))

    def test_real_feature_and_sample_jobs_finish_with_legacy_consumer_positive(self):
        geometry_calls = []
        geometry = lambda: (geometry_calls.append('called') or dict(
            query=np.zeros((40, 40), np.uint8), observationBounds=[0, 0, 40, 40]))
        work = self.make_work(geometry=geometry, required=IDS)
        self.assertIsInstance(work, EntryEvidenceWork)
        self.assertIsNone(work.frame)
        phases = []
        while True:
            job = work.next_job()
            if job is None:
                break
            phases.append(job.phase)
            job.run()
        proposal = work.finish()
        work.close()
        self.assertTrue(proposal['complete'])
        self.assertEqual(proposal['reason'], 'finite-conditions-evaluated')
        self.assertEqual(geometry_calls, ['called'])
        for phase in ('condition-match-row', 'condition-local-witness',
                      'condition-current-geometry', 'condition-sample-bundle-load',
                      'condition-domain-binding-validation',
                      'condition-comparison-a-view-support',
                      'condition-comparison-b-view-support'):
            self.assertIn(phase, phases)

        # The ordinary state consumer still receives only advisory positive
        # evidence; it does not gain an identity or pose authorization.
        state = NativeSimilarityState()
        state.accept(proposal, ('nightmare', 10, '1', 1, 2), 'jobs-positive')
        self.assertIsNotNone(state.provisional_member_id)
        self.assertFalse(proposal['authorizesIdentity'])
        self.assertFalse(proposal['authorizesPose'])

        # The compatibility collector drives the same work and retains the
        # original proposal payload shape.
        legacy = self.fixture.propose_condition(np.zeros((40, 40), np.uint8))
        self.assertEqual(legacy['status'], 'conditions')
        self.assertTrue(legacy['complete'])

    def test_fresh_prior_continuation_is_a_separate_job_and_keeps_full_rivals(self):
        first = self.fixture.propose_condition(np.zeros((40, 40), np.uint8))
        state = NativeSimilarityState()
        state.accept(first, ('nightmare', 10, '1', 1, 2), 'prior-source')
        prior = state.context_snapshot()
        query = np.zeros((96, 96), np.uint8)
        query[70, 1] = 1
        work = self.fixture.index.prepare_proposal((), [],
            dict(complete=True, entryInventoryComplete=True), '1',
            self.fixture.provider,
            lambda:dict(query=query, observationBounds=[0, 0, 96, 96]),
            side_rows=self.fixture.current_side(), side_diagnostics=dict(complete=True),
            deadline=time.perf_counter() + 2, prior=prior,
            required_map_ids=tuple(IDS))
        phases = []
        scheduler_status = ObservationWorkScheduler(clock=time.perf_counter).drive(
            work, deadline=time.perf_counter() + 2,
            on_job_finished=lambda job:(phases.append(job.phase), None)[1])
        proposal = work.finish()
        self.assertEqual(scheduler_status, 'complete')
        self.assertTrue(proposal['complete'])
        self.assertIn('condition-continuation-pose', phases)

        full_work = self.make_work(required=(*IDS, 'nightmare-foreign'))
        ObservationWorkScheduler(clock=time.perf_counter).drive(
            full_work, deadline=time.perf_counter() + 2)
        full_proposal = full_work.finish()
        self.assertTrue(full_proposal['complete'])
        # This registered pair is only A/B. C remains a required competitor
        # outside it, alongside the complete original cohort's extra member.
        nested = next(observation for observation in full_proposal['conditionObservations']
            if observation['conditionId'] == '1')
        self.assertEqual(set(nested['outsideCompetitors']),
            {IDS[2], 'nightmare-foreign'})

    def test_budget_and_close_leave_competition_unfinished(self):
        work = self.make_work(required=IDS)
        clock = [0.0]
        original = self.fixture.index.condition_index.match

        def expire_after_first_row(*args, **kwargs):
            result = original(*args, **kwargs)
            clock[0] = 2.0
            return result

        with patch.object(self.fixture.index.condition_index, 'match',
                          side_effect=expire_after_first_row):
            status = ObservationWorkScheduler(clock=lambda:clock[0]).drive(
                work, deadline=1.0)
        proposal = work.finish()
        self.assertEqual(status, 'budget-unresolved')
        self.assertFalse(proposal['complete'])
        self.assertEqual(proposal['reason'], 'condition-budget-incomplete')
        self.assertEqual(proposal['conditionObservations'], [])

        closed = self.make_work(required=IDS)
        self.assertIsNotNone(closed.next_job())
        closed.close()
        self.assertFalse(closed.finish()['complete'])
        self.assertEqual(closed.finish()['reason'], 'condition-budget-incomplete')

    def test_changed_sample_resource_is_reported_as_resource_failure(self):
        index = self.fixture.index.condition_index
        sample_path = self.fixture.root / index.sample_spec['resourcePath']
        sample_path.write_bytes(sample_path.read_bytes() + b'changed')
        result = self.fixture.propose_condition(np.zeros((40, 40), np.uint8))
        self.assertFalse(result['complete'])
        self.assertEqual(result['reason'], 'similarity-samples-changed')
        self.assertNotEqual(result['reason'], 'condition-budget-incomplete')

    def test_finish_retires_unrun_job_and_cannot_resume_source_work(self):
        work = self.make_work(required=IDS)
        pending = work.next_job()
        self.assertIsNotNone(pending)
        report = work.finish()
        self.assertFalse(report['complete'])
        self.assertIsNone(work.next_job())
        with self.assertRaisesRegex(ValueError, 'entry-job-not-current'):
            pending.run()

    def test_duplicate_views_remain_unexamined_for_conflict(self):
        front = self.fixture.front_rows()
        work = self.make_work(front=[*front, dict(front[0])])
        ObservationWorkScheduler(clock=time.perf_counter).drive(work, deadline=time.perf_counter()+2)
        observations = work.finish()['conditionObservations']
        ambiguous = [row for row in observations if row['ambiguousViews']]
        self.assertTrue(ambiguous)
        self.assertTrue(all('conflictingViews' not in row for row in ambiguous))

    def test_unexpected_kernel_failure_is_not_reported_as_budget_exhaustion(self):
        work = self.make_work()
        with patch.object(self.fixture.index, '_support', side_effect=MemoryError('support allocation')):
            with self.assertRaisesRegex(MemoryError, 'support allocation'):
                ObservationWorkScheduler(clock=time.perf_counter).drive(work, deadline=time.perf_counter()+2)
        report = work.finish()
        self.assertFalse(report['complete'])
        self.assertEqual(report['reason'], 'native-condition-work-failed')

    def test_explicit_entry_contradiction_stays_negative_in_existing_consumer(self):
        front = self.fixture.front_rows()
        for row in front:
            row['entryCrossCheck'] = dict(contradicted=True)
        proposal = self.fixture.propose_condition(np.zeros((40, 40), np.uint8),
            front=front)
        state = NativeSimilarityState()
        state.accept(proposal, ('nightmare', 10, '1', 1, 2), 'jobs-negative')
        self.assertTrue(proposal['complete'])
        self.assertEqual(proposal['conditionObservations'], [])
        self.assertEqual(state.state, 'UNLOCKED')
        self.assertIsNone(state.provisional_member_id)


class NativeLegacyGroupJobTests(unittest.TestCase):
    def test_missing_shared_provider_is_not_replaced_by_binding_metadata_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            fixture.index.binding_provider = fixture.provider
            rows = [dict(mapId=mid, identityHardGatePassed=False) for mid in IDS]
            entries = [dict(mapId=mid, score=40-i, verification=dict(
                regions=dict(regionalEdgeVerified=True), residual=dict(verified=True),
                matrixSourceToClient=np.eye(3).tolist())) for i, mid in enumerate(IDS)]
            calls = []
            def geometry():
                calls.append('geometry')
                return dict(query=np.zeros((6, 8), np.uint8), observationBounds=[0, 0, 8, 6])
            result = fixture.index.propose(rows, entries,
                dict(complete=True, entryInventoryComplete=True), '1', None, geometry,
                deadline=time.perf_counter()+2)
            self.assertEqual(result['status'], 'unavailable')
            self.assertEqual(result['reason'], 'similarity-shared-provider-unavailable')
            self.assertEqual(calls, [])

    def test_schema1_difference_kernel_is_scheduled_and_still_advisory(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            query = np.zeros((6, 8), np.uint8)
            query[:, 3] = 1
            rows = [dict(mapId=mid, identityHardGatePassed=False) for mid in IDS]
            entries = [dict(mapId=mid, score=40-i, verification=dict(
                regions=dict(regionalEdgeVerified=True), residual=dict(verified=True),
                matrixSourceToClient=np.eye(3).tolist())) for i, mid in enumerate(IDS)]
            work = fixture.index.prepare_proposal(rows, entries,
                dict(complete=True, entryInventoryComplete=True), '1', fixture.provider,
                lambda:dict(query=query, observationBounds=[0, 0, 8, 6]),
                deadline=time.perf_counter() + 2)
            self.assertIsInstance(work, EntryEvidenceWork)
            phases = []
            while True:
                job = work.next_job()
                if job is None:
                    break
                phases.append(job.phase)
                job.run()
            proposal = work.finish()
            work.close()
            self.assertEqual(proposal['status'], 'group-candidate')
            self.assertNotIn('reason', proposal)
            self.assertFalse(proposal['authorizesIdentity'])
            self.assertFalse(proposal['authorizesPose'])
            self.assertIn('similarity-difference-arrays-load', phases)
            self.assertIn('similarity-difference-a-view-support', phases)
            self.assertIn('similarity-difference-b-view-support', phases)
            winner = fixture.index.propose([dict(mapId=IDS[0], identityHardGatePassed=True)], (),
                dict(complete=True), '1', fixture.provider, lambda: None,
                deadline=time.perf_counter()+2)
            self.assertEqual(winner['status'], 'identity-pending')
            self.assertNotIn('reason', winner)


if __name__ == '__main__':
    unittest.main()
