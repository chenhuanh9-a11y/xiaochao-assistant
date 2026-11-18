"""Real entry competition jobs; observation and final pixel validator supplied.

The direct work must finish independently of source-sheet/advisory work. This
does not test the future runtime race or grant S1 live identity permission.
"""
import unittest
from unittest.mock import Mock

from entry_evidence_work import FrameSnapshot, ObservationWorkScheduler
import map_observation_work
import test_observation_work_scheduler as fixture


class LocalStructureWorkTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.EntryObservationTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def make(self, *, unique=True):
        engine = self.fixture.engine(unique=unique)
        engine.prepare_source_structure_candidates = Mock(
            side_effect=AssertionError('direct structure waited for source sheets'))
        engine.entry_advisory = Mock(side_effect=AssertionError('direct structure ran icon advisory'))
        return engine

    def run_work(self, engine):
        snapshot = FrameSnapshot(self.fixture.fixture.frame)
        work = map_observation_work.LocalStructureObservationWork(
            engine, snapshot, '2', deadline=float('inf'))
        self.assertIs(work.frame_snapshot, snapshot)
        phases = []
        ObservationWorkScheduler().drive(work, on_job_finished=lambda job: phases.append(job.phase))
        return work, phases, work.finish()

    def test_identity_only_driver_keeps_the_same_complete_identity_without_preview(self):
        class EmptySourceWork:
            def next_job(self):
                return None
            def finish(self, **kwargs):
                return dict(candidates=[], candidateCount=0, complete=True, evaluatedEntryCount=0)

        def run(work_type):
            engine = self.fixture.engine(unique=True)
            engine.prepare_source_structure_candidates = Mock(return_value=EmptySourceWork())
            snapshot = FrameSnapshot(self.fixture.fixture.frame)
            work = work_type(engine, snapshot, '2', deadline=float('inf'))
            phases = []
            ObservationWorkScheduler().drive(work, on_job_finished=lambda job: phases.append(job.phase))
            return work, phases, work.finish()

        candidate, candidate_phases, candidate_rows = run(map_observation_work.MapObservationWork)
        direct, direct_phases, direct_rows = run(map_observation_work.LocalStructureObservationWork)

        summary = lambda rows: {row['mapId']: row['identityHardGatePassed'] for row in rows}
        self.assertEqual(set(summary(direct_rows)), {'map-a', 'map-b'})
        self.assertEqual(summary(direct_rows), summary(candidate_rows))
        self.assertIn('entry-pose-proposals', candidate_phases)
        self.assertNotIn('entry-pose-proposals', direct_phases)
        self.assertTrue(direct.entry_complete)

    def test_complete_source_competition_finishes_without_auxiliary_tail(self):
        engine = self.make()
        work, phases, rows = self.run_work(engine)
        self.assertTrue(work.entry_complete)
        self.assertEqual({row['mapId'] for row in rows}, {'map-a', 'map-b'})
        self.assertEqual([row['mapId'] for row in rows if row['identityHardGatePassed']], ['map-a'])
        self.assertEqual([phase for phase in phases if phase == 'main:probe'], ['main:probe'] * 2)
        self.assertNotIn('entry-pose-proposals', phases)
        engine.prepare_source_structure_candidates.assert_not_called()
        engine.entry_advisory.assert_not_called()

    def test_front_gate_candidate_scope_does_not_shrink_direct_competition(self):
        engine = self.make(unique=False)
        engine.front_gate_prefilter = Mock()
        engine.front_gate_prefilter.select.return_value = ({'map-a'}, {})
        work, _, rows = self.run_work(engine)
        self.assertIsNone(work.allowed)
        self.assertEqual({row['mapId'] for row in rows}, {'map-a', 'map-b'})
        self.assertFalse(any(row['identityHardGatePassed'] for row in rows))
        engine.front_gate_prefilter.select.assert_not_called()

    def test_partial_competition_cannot_submit_a_unique_identity(self):
        engine = self.make()
        work = map_observation_work.LocalStructureObservationWork(
            engine, self.fixture.fixture.frame, '2', deadline=float('inf'))
        while (job := work.next_job()) is not None:
            if job.phase == 'main:probe' and job.map_id == 'map-b':
                break
            job.run()
        rows = work.finish()
        self.assertFalse(work.entry_complete)
        self.assertEqual({row['mapId'] for row in rows}, {'map-a', 'map-b'})
        self.assertFalse(any(row['identityHardGatePassed'] for row in rows))

    def test_raw_local_work_preserves_native_s1_entry_purpose_refusal(self):
        from difficult_native_identity import NativeDifficultCandidateEngine
        engine = self.make()
        engine.qt_season, engine.qt_rank_ready = 's1', True
        with self.assertRaisesRegex(ValueError, 'proposal-only-entry-evidence'):
            NativeDifficultCandidateEngine.prepare_entry_observation(
                engine, self.fixture.fixture.frame, '2', deadline=float('inf'))
        work = map_observation_work.LocalStructureObservationWork(
            engine, self.fixture.fixture.frame, '2', deadline=float('inf'))
        with self.assertRaisesRegex(ValueError, 'proposal-only-entry-evidence'):
            ObservationWorkScheduler().drive(work)
        self.assertFalse(work.entry_complete)
        self.assertFalse(any(row['identityHardGatePassed'] for row in work.finish()))

    def test_explicit_index_reaches_actual_local_locator_and_probe(self):
        from local_structure_index import LocalStructureIndex
        source = fixture.side_fixture.SideEntryJobTests('runTest')
        source.setUp()
        self.addCleanup(source.doCleanups)
        side = source.source(('map-a',))
        source.observe(accepted=('map-a',))
        index = LocalStructureIndex(side.entries, competition_domain=(('map-a', '2'),))
        work = map_observation_work.LocalStructureObservationWork(
            self.make(), source.frame, '2', deadline=float('inf'), prepared_index=index)
        actual = work._prepare_local_rank(side, {})
        self.assertIs(actual.prepared_index, index)
        ObservationWorkScheduler().drive(actual)
        rows, diagnostic = actual.finish()
        self.assertTrue(diagnostic['complete'])
        self.assertTrue(diagnostic['verificationComplete'])
        self.assertEqual({row['mapId'] for row in rows}, {'map-a'})
        self.assertTrue(rows[0]['verification']['success'])


if __name__ == '__main__':
    unittest.main()
