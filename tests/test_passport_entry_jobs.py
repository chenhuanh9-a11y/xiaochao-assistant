"""Passport source jobs with real decoded walls and local continuous fitting.

Exit observations and final image/other-exit validators are isolated. Component
reads, wall correspondence, refinement, room/port evidence and consumers run.
These synthetic observations do not establish real-frame recognition quality.
"""
from contextlib import ExitStack
import unittest
from unittest.mock import patch

import numpy as np

import test_entry_resource_io as fixture_io
from map_identity_engine import verified_entry_candidate


class PassportEntryJobTests(unittest.TestCase):
    def setUp(self):
        fixture = self.fixture = fixture_io.EntryResourceIOTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.walls = [dict(axis=axis, sign=sign, normal=normal, lo=-30., hi=30., edgeId=i)
                      for i, (axis, sign, normal) in enumerate(
                          [(0, -1, -30.), (0, 1, 30.), (1, -1, -30.), (1, 1, 30.)])]
        room = dict(kind='room', instance='room-1', material='stone', roomTypeId='square',
                    rotation=0, outline=[[-30, -30], [30, -30], [30, 30], [-30, 30]],
                    connections=['entrance', 'room-1'], knownWallLines=self.walls,
                    connectingPort='entry', ports=[dict(name='entry', points=[[0, 0], [0, 0]])])
        fixture.passport.update(entries=[dict(mapId=mid, floor='2', sourceEntryId=mid + '-entry',
            family='side', directionKnown=True, outwardDirections=['E'], center=[0, 0],
            unit=60., region='fixture', sourceProduct='source.npz',
            knownWallLines=self.walls, localInstances=[room]) for mid in ('map-a', 'map-b')],
            coverage=[dict(mapId=mid, floor='2', complete=True) for mid in ('map-a', 'map-b')])
        fixture.files[fixture_io.ENTRY_ROOT + '/entry-passports.json'] = fixture_io.encoded(fixture.passport)
        fixture.package()
        self.engine = fixture.local()
        self.assertIsNone(self.engine.passport_error)
        self.frame = np.zeros((300, 400, 3), np.uint8)
        self.lines = [dict(w, normal=w['normal'] + (190 if w['axis'] == 0 else 130),
                           lo=w['lo'] + (130 if w['axis'] == 0 else 190),
                           hi=w['hi'] + (130 if w['axis'] == 0 else 190)) for w in self.walls]
        self.exit = dict(reliable=True, icon=dict(center=[190, 130]),
                         direction=dict(reliable=True, direction='右'),
                         rectangle=dict(success=True, bounds=[100, 100, 220, 160]))

    def observe(self, *, accepted=True):
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(self.engine, '_entry_observations', return_value=[self.exit]))
        stack.enter_context(patch.object(self.engine, '_geometry', return_value=dict(
            query=np.ones(self.frame.shape[:2], np.uint8), observationBounds=(0, 0, 400, 300))))
        stack.enter_context(patch('single_map_structure.current_part', return_value=self.lines))
        checked = stack.enter_context(patch.object(self.engine, 'verify', side_effect=lambda frame, row, **kw:
            dict(success=accepted, reason='isolated-final-image-validator',
                 validationPolicy='local-fit-and-author', residual=dict(verified=accepted),
                 matrixSourceToClient=row['matrixSourceToClient'])))
        stack.enter_context(patch.object(self.engine, 'check_entry_evidence', return_value=dict(contradicted=False)))
        return checked

    def drain(self, work):
        phases = []
        while (job := work.next_job()) is not None:
            phases.append((job.phase, job.map_id))
            job.run()
        return phases, work.finish()

    def test_actual_entry_keeps_first_verified_pose_and_unknown_competitors(self):
        self.observe()
        rows, meta = self.engine.rank_entry(self.frame, '2', budget_seconds=10.)
        self.assertEqual(meta['reason'], 'entry-first-verified')
        self.assertFalse(meta['complete'])
        self.assertFalse(meta['verificationComplete'])
        self.assertEqual([r['mapId'] for r in meta['examined']], ['map-a'])
        candidate = verified_entry_candidate(rows, meta, '2', profile_id='native')
        self.assertTrue(candidate['poseVerified'])
        self.assertFalse(candidate['competitionComplete'])
        np.testing.assert_allclose(candidate['matrixSourceToClient'], [[1, 0, 190], [0, 1, 130], [0, 0, 1]], atol=1e-8)

    def test_steps_keep_filtered_scope_and_first_verified_policy(self):
        self.observe()
        work = self.engine.prepare_rank_entry(self.frame, '2', candidate_map_ids={'map-b'})
        phases, (rows, meta) = self.drain(work)
        self.assertEqual(phases, [('observe', None), ('geometry', None), ('probe', 'map-b'), ('verify', 'map-b')])
        self.assertEqual([r['mapId'] for r in meta['examined']], ['map-b'])
        self.assertEqual(meta['selectionPolicy'], 'first-verified-entry')
        self.assertTrue(verified_entry_candidate(rows, meta, '2', profile_id='native')['poseVerified'])
        self.assertIs(work.finish()[0], rows)

    def test_stopping_after_probe_cannot_verify_or_resume_closed_work(self):
        checked = self.observe()
        work = self.engine.prepare_rank_entry(self.frame, '2')
        for phase in ('observe', 'geometry', 'probe'):
            job = work.next_job()
            self.assertIs(work.next_job(), job)
            self.assertEqual(job.phase, phase)
            job.run()
        pending = work.next_job()
        self.assertEqual(pending.phase, 'verify')
        rows, meta = work.finish()
        self.assertFalse(meta['complete'])
        self.assertEqual(meta['reason'], 'entry-budget-incomplete')
        self.assertIsNone(verified_entry_candidate(rows, meta, '2', profile_id='native'))
        checked.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'entry-job-not-current'):
            pending.run()

    def test_rejected_verifiers_leave_all_candidates_and_room_evidence(self):
        self.observe(accepted=False)
        _, (rows, meta) = self.drain(self.engine.prepare_rank_entry(self.frame, '2'))
        self.assertTrue(meta['complete'])
        self.assertTrue(meta['verificationComplete'])
        self.assertTrue(meta['entryObserved'])
        self.assertEqual(meta['reason'], 'entry-local-verification-failed')
        self.assertEqual({r['mapId'] for r in rows}, {'map-a', 'map-b'})
        self.assertTrue(all(r['entryEvidence']['roomGeometry']['verified'] for r in rows))
        self.assertIsNone(verified_entry_candidate(rows, meta, '2', profile_id='native'))

    def test_missing_coverage_and_unresolved_direction_remain_explicit(self):
        self.observe()
        self.engine.entry_coverage[0]['complete'] = False
        phases, (rows, meta) = self.drain(self.engine.prepare_rank_entry(self.frame, '2'))
        self.assertEqual(phases, [('observe', None)])
        self.assertEqual(meta['reason'], 'entry-coverage-incomplete')
        self.assertFalse(meta['coverageComplete'])
        self.assertEqual(rows, [])
        self.engine.entry_coverage[0]['complete'] = True
        self.exit['direction']['reliable'] = False
        phases, (rows, meta) = self.drain(self.engine.prepare_rank_entry(self.frame, '2'))
        self.assertEqual(phases, [('observe', None), ('geometry', None)])
        self.assertFalse(meta['complete'])
        self.assertEqual(meta['examined'][0]['reason'], 'exit-direction-or-local-width-unresolved')
        self.assertEqual(rows, [])

    def test_probe_exception_is_terminal_without_skipping_failed_candidate(self):
        self.observe()
        work = self.engine.prepare_rank_entry(self.frame, '2')
        work.next_job().run()
        work.next_job().run()
        job = work.next_job()
        with patch.object(self.engine, '_entry_wall_arrays', side_effect=RuntimeError('wall boom')):
            with self.assertRaisesRegex(RuntimeError, 'wall boom'):
                job.run()
        self.assertIsNone(work.next_job())
        with self.assertRaisesRegex(ValueError, 'entry-job-not-current'):
            job.run()
        rows, meta = work.finish()
        self.assertEqual(meta['reason'], 'entry-work-failed')
        self.assertFalse(meta['complete'])
        self.assertIsNone(verified_entry_candidate(rows, meta, '2', profile_id='native'))

    def test_legacy_rank_drives_jobs_and_stops_at_shared_deadline(self):
        import entry_passport_evidence as passport
        checked = self.observe()
        clock = [0.]
        fitting = self.engine._fit_entry_local_walls
        def slow_fit(*args):
            result = fitting(*args)
            clock[0] = 2.
            return result
        with patch.object(self.engine, 'prepare_rank_entry', wraps=self.engine.prepare_rank_entry) as prepared, \
             patch.object(self.engine, '_fit_entry_local_walls', side_effect=slow_fit), \
             patch.object(passport.time, 'perf_counter', side_effect=lambda: clock[0]):
            rows, meta = self.engine.rank_entry(self.frame, '2', budget_seconds=1.)
        prepared.assert_called_once()
        checked.assert_not_called()
        self.assertEqual(meta['reason'], 'entry-budget-incomplete')
        self.assertFalse(meta['complete'])
        self.assertEqual([r['mapId'] for r in meta['examined']], ['map-a'])
        self.assertEqual(rows, [])

    def test_reduction_exception_terminates_partial_result(self):
        self.observe(accepted=False)
        work = self.engine.prepare_rank_entry(self.frame, '2')
        with patch.object(self.engine, '_verified_entry_pose_groups', side_effect=RuntimeError('reduce boom')):
            with self.assertRaisesRegex(RuntimeError, 'reduce boom'):
                self.drain(work)
        self.assertIsNone(work.next_job())
        rows, meta = work.finish()
        self.assertFalse(meta['complete'])
        self.assertEqual(meta['reason'], 'entry-work-failed')
        self.assertIsNone(verified_entry_candidate(rows, meta, '2', profile_id='native'))

    def test_jobs_own_frame_and_cannot_repeat_completed_observation(self):
        self.observe()
        work = self.engine.prepare_rank_entry(self.frame, '2')
        self.frame[:] = 255
        job = work.next_job()
        job.run()
        self.assertFalse(work.frame.any())
        with self.assertRaises(ValueError):
            work.frame.setflags(write=True)
        with self.assertRaisesRegex(ValueError, 'entry-job-not-current'):
            job.run()
        work.finish()

    def test_actual_black_frame_and_unsupported_floor_preserve_failure_reasons(self):
        rows, meta = self.engine.rank_entry(self.frame, '2', budget_seconds=10.)
        self.assertEqual((rows, meta['reason']), ([], 'entry-no-reliable-exit'))
        rows, meta = self.engine.rank_entry(self.frame, '-3', budget_seconds=0.)
        self.assertEqual((rows, meta['reason']), ([], 'entry-unsupported-floor'))


if __name__ == '__main__':
    unittest.main()
