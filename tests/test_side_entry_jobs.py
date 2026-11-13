"""Real decoded turn search/refinement; supplied observations/final validator.

Prepared arrays use the production builder; their inventory binding is covered
separately by resource-I/O tests. No real-frame or game accuracy acceptance.
"""
from contextlib import ExitStack
from copy import deepcopy
import unittest
from unittest.mock import patch

import numpy as np

import test_entry_resource_io as fixture_io
from map_identity_engine import verified_side_candidate
from side_entry_search import prepare_arrays


class SideEntryJobTests(unittest.TestCase):
    def setUp(self):
        fixture = self.fixture = fixture_io.EntryResourceIOTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.frame = np.zeros((720, 1280, 3), np.uint8)

    def source(self, ids=('map-a', 'map-b'), *, prepared=False):
        f = self.fixture
        f.side['entries'] = [dict(deepcopy(f.side['entries'][0]), mapId=mid) for mid in ids]
        f.files[fixture_io.ENTRY_ROOT + '/side-entries.json'] = fixture_io.encoded(f.side)
        f.package()
        self.engine = f.local()
        self.engine.side_search = {}
        if prepared:
            self.engine.side_doors = {mid: dict(boundaryCenter=[30, 30] if mid == 'map-a' else [-500, -500],
                outwardDirection='E') for mid in ids}
            self.engine.side_search = {(item['mapId'], item['floor']): prepare_arrays(
                item, points, rays, pairs, self.engine.side_doors[item['mapId']])
                for item, points, rays, pairs in self.engine.entries}
        return self.engine

    def observe(self, *, accepted=('map-a', 'map-b')):
        stack = ExitStack()
        self.addCleanup(stack.close)
        f = self.fixture
        nodes = [dict(point=p * .5 + [200, 100], rays=r) for p, r in zip(f.points, f.rays)]
        stack.enter_context(patch.object(self.engine, '_observe', return_value=(
            dict(query=np.ones(self.frame.shape[:2], np.uint8)), nodes, f.pairs)))
        stack.enter_context(patch.object(self.engine, '_entry_observations', return_value=[dict(
            reliable=True, icon=dict(score=.9), direction=dict(reliable=True, direction='右',
                localRectangleAccepted=True, rectangleBounds=[185, 100, 215, 130]))]))
        return stack.enter_context(patch.object(self.engine, 'verify', side_effect=lambda frame, row, **kw:
            dict(success=row['mapId'] in accepted, reason='isolated-final-image-validator',
                 residual=dict(verified=row['mapId'] in accepted), matrixSourceToClient=row['matrixSourceToClient'])))

    def drain(self, work):
        phases = []
        while (job := work.next_job()) is not None:
            phases.append((job.phase, job.map_id))
            job.run()
        return phases, work.finish()

    def test_actual_rank_finds_pose_and_keeps_real_conservative_consumer(self):
        engine = self.source(('map-a',))
        self.observe()
        rows, meta = engine.rank(self.frame, '2', budget_seconds=10.)
        self.assertTrue(meta['complete'])
        self.assertTrue(meta['verificationComplete'])
        self.assertEqual(meta['competitiveHypotheses'], 1)
        winner = verified_side_candidate(rows, meta, '2', [], self.frame.shape, profile_id='native')
        self.assertTrue(winner['poseVerified'])
        np.testing.assert_allclose(winner['matrixSourceToClient'], [[.5, 0, 200], [0, .5, 100], [0, 0, 1]], atol=1e-8)

    def ranked_side(self):
        engine = self.source(('map-a',))
        self.observe()
        return engine.rank(self.frame, '2', budget_seconds=10.)

    @staticmethod
    def main_proposal(side, *, shift=0., map_id='map-a'):
        matrix = np.asarray(side['verification']['matrixSourceToClient']).copy()
        matrix[0, 2] += shift
        return dict(mapId=map_id, proposalOnly=True, authorizesPose=False,
            authorizesIdentity=False, verification=dict(success=True,
                matrixSourceToClient=matrix.tolist()))

    def test_same_map_main_pose_conflict_keeps_only_complete_side_identity(self):
        rows, meta = self.ranked_side()
        main = self.main_proposal(rows[0], shift=4.)
        before = deepcopy(main)
        winner = verified_side_candidate(rows, meta, '2', [main], self.frame.shape, profile_id='native')
        self.assertIsNotNone(winner)
        self.assertTrue(winner['identityVerified'])
        self.assertTrue(winner['identityOnly'])
        self.assertFalse(winner['poseVerified'])
        self.assertEqual(winner['identityEvidence'], 'complete-same-map-side-competition-pose-unresolved')
        self.assertEqual(main, before, 'the main proposal must retain its source purpose and evidence')

    def test_same_map_main_pose_within_original_threshold_keeps_pose_qualification(self):
        rows, meta = self.ranked_side()
        for shift in (0., 3.):
            with self.subTest(shift=shift):
                winner = verified_side_candidate(rows, meta, '2',
                    [self.main_proposal(rows[0], shift=shift)], self.frame.shape, profile_id='native')
                self.assertTrue(winner['identityVerified'])
                self.assertTrue(winner['poseVerified'])
                self.assertFalse(winner['identityOnly'])

    def test_main_pose_qualification_retains_original_float32_inverse_boundary(self):
        rows, meta = self.ranked_side()
        side = np.eye(3, dtype=np.float64)
        rows[0]['matrixSourceToClient'] = side
        rows[0]['verification']['matrixSourceToClient'] = side
        scale = np.float32(1 - 2**-24)
        main32 = np.array([[scale, 0., np.float32(-2.9999)],
                           [0., scale, 0.], [0., 0., 1.]], dtype=np.float32)
        height, width = self.frame.shape[:2]
        extent = np.array([[0, 0, 1], [width, 0, 1], [0, height, 1], [width, height, 1]])
        # The pre-change production expression is the independent oracle. Its
        # inverse keeps the input dtype; converting before inv crosses 3px.
        original = side @ np.linalg.inv(np.asarray(main32)) - np.eye(3)
        converted = side @ np.linalg.inv(np.asarray(main32, dtype=float)) - np.eye(3)
        original_max = np.linalg.norm((extent @ original.T)[:, :2], axis=1).max()
        converted_max = np.linalg.norm((extent @ converted.T)[:, :2], axis=1).max()
        self.assertGreater(original_max, 3.)
        self.assertLess(converted_max, 3.)
        for matrix, maximum in ((main32, original_max), (main32.astype(float), converted_max)):
            with self.subTest(dtype=matrix.dtype, original_max=maximum):
                main = self.main_proposal(rows[0])
                main['verification']['matrixSourceToClient'] = matrix
                winner = verified_side_candidate(rows, meta, '2', [main], self.frame.shape, profile_id='native')
                self.assertTrue(winner['identityVerified'])
                self.assertEqual(winner['poseVerified'], maximum <= 3.)
                self.assertEqual(winner['identityOnly'], maximum > 3.)

    def test_main_pose_conflict_does_not_hide_later_foreign_identity_or_invalid_matrix(self):
        rows, meta = self.ranked_side()
        conflict = self.main_proposal(rows[0], shift=4.)
        invalid = (None, 'invalid', [[1., 0.], [0., 1.]], np.zeros((3, 3)).tolist(),
                   [[float('nan'), 0., 0.], [0., 1., 0.], [0., 0., 1.]])
        rivals = [self.main_proposal(rows[0], map_id='map-b')]
        for matrix in invalid:
            rival = self.main_proposal(rows[0])
            rival['verification']['matrixSourceToClient'] = matrix
            rivals.append(rival)
        for index, rival in enumerate(rivals):
            for mains in ([conflict, rival], [rival, conflict]):
                with self.subTest(rival=index, conflict_first=mains[0] is conflict):
                    self.assertIsNone(verified_side_candidate(rows, meta, '2', mains,
                        self.frame.shape, profile_id='native'))

    def test_main_pose_conflict_cannot_bypass_original_side_qualification(self):
        rows, meta = self.ranked_side()
        main = self.main_proposal(rows[0], shift=4.)
        cases = ((dict(complete=False), {}), (dict(verificationComplete=False), {}),
                 (dict(competitiveHypotheses=2), {}), ({}, dict(profileId='wrong')),
                 ({}, dict(competitionComplete=False)), ({}, dict(poseAmbiguous=True)),
                 ({}, dict(hypothesis=dict(chainCount=5, extentGrid=[4., 4.]))),
                 ({}, dict(hypothesis=dict(chainCount=8, extentGrid=[1., 4.]))),
                 ({}, dict(verification=dict(success=False))),
                 ({}, dict(verification=dict(success=True, residual=dict(verified=False)))),
                 ({}, dict(entryCrossCheck=dict(contradicted=True))))
        for diagnostic, candidate in cases:
            with self.subTest(diagnostic=diagnostic, candidate=candidate):
                self.assertIsNone(verified_side_candidate([dict(rows[0], **candidate)],
                    dict(meta, **diagnostic), '2', [main], self.frame.shape, profile_id='native'))

    def test_all_sources_probe_before_any_verify_and_scope_is_preserved(self):
        engine = self.source()
        self.observe()
        phases, (rows, meta) = self.drain(engine.prepare_rank(self.frame, '2'))
        self.assertEqual(phases, [('observe', None), ('probe', 'map-a'), ('probe', 'map-b'),
                                  ('verify', 'map-a'), ('verify', 'map-b')])
        self.assertTrue(meta['complete'])
        self.assertTrue(meta['identityAmbiguous'])
        self.assertIsNone(verified_side_candidate(rows, meta, '2', [], self.frame.shape, profile_id='native'))
        phases, (rows, meta) = self.drain(engine.prepare_rank(self.frame, '2', candidate_map_ids={'map-b'}))
        self.assertEqual([mid for phase, mid in phases if phase == 'probe'], ['map-b'])
        self.assertEqual({r['mapId'] for r in rows}, {'map-b'})

    def test_closed_partial_search_retains_unknown_rival_and_cannot_resume(self):
        engine = self.source()
        checked = self.observe()
        work = engine.prepare_rank(self.frame, '2')
        work.next_job().run()
        work.next_job().run()
        pending = work.next_job()
        self.assertEqual(pending.map_id, 'map-b')
        rows, meta = work.finish()
        checked.assert_not_called()
        self.assertFalse(meta['complete'])
        self.assertFalse(meta['verificationComplete'])
        self.assertEqual({r['verification']['reason'] for r in rows}, {'side-search-incomplete-unverified'})
        self.assertIsNone(verified_side_candidate(rows, meta, '2', [], self.frame.shape, profile_id='native'))
        with self.assertRaisesRegex(ValueError, 'entry-job-not-current'):
            pending.run()

    def test_failed_optional_door_restores_original_competitors_and_existing_checks(self):
        engine = self.source(prepared=True)
        checked = self.observe(accepted=('map-b',))
        phases, (rows, meta) = self.drain(engine.prepare_rank(self.frame, '2'))
        self.assertIn(('door-cross-check', None), phases)
        self.assertEqual([c.args[1]['mapId'] for c in checked.call_args_list], ['map-a', 'map-b'])
        self.assertFalse(meta['sideOptionalDoorUsed'])
        self.assertEqual(meta['competitiveHypotheses'], 2)
        self.assertTrue(meta['identityAmbiguous'])
        self.assertTrue(meta['verificationComplete'])
        self.assertFalse(any('sideDoorConstraint' in r for r in rows))
        self.assertTrue(next(r for r in rows if r['mapId'] == 'map-b')['verification']['success'])
        self.assertIsNone(verified_side_candidate(rows, meta, '2', [], self.frame.shape, profile_id='native'))

    def test_more_than_three_rivals_remain_unverified_without_authority(self):
        engine = self.source(('map-a', 'map-b', 'map-c', 'map-d'))
        checked = self.observe()
        _, (rows, meta) = self.drain(engine.prepare_rank(self.frame, '2'))
        checked.assert_not_called()
        self.assertTrue(meta['complete'])
        self.assertFalse(meta['verificationComplete'])
        self.assertEqual(meta['competitiveHypotheses'], 4)
        self.assertEqual({r['verification']['reason'] for r in rows}, {'side-competition-limit-unverified'})

    def test_verifier_exception_terminates_without_replaying_or_confirming(self):
        engine = self.source()
        checked = self.observe()
        checked.side_effect = OSError('source gone')
        work = engine.prepare_rank(self.frame, '2')
        for phase in ('observe', 'probe', 'probe'):
            job = work.next_job()
            self.assertEqual(job.phase, phase)
            job.run()
        job = work.next_job()
        with self.assertRaisesRegex(OSError, 'source gone'):
            job.run()
        self.assertIsNone(work.next_job())
        with self.assertRaisesRegex(ValueError, 'entry-job-not-current'):
            job.run()
        rows, meta = work.finish()
        self.assertFalse(meta['complete'])
        self.assertFalse(meta['verificationComplete'])
        self.assertEqual(meta['reason'], 'side-work-failed')
        self.assertIsNone(verified_side_candidate(rows, meta, '2', [], self.frame.shape, profile_id='native'))

    def test_legacy_rank_uses_jobs_and_one_deadline(self):
        import side_entry_evidence as side
        engine = self.source()
        checked = self.observe()
        clock = [0.]
        probe = side.SideEntryEvidence._probe
        def slow_probe(work, source):
            probe(work, source)
            clock[0] = 2.
        with patch.object(engine, 'prepare_rank', wraps=engine.prepare_rank) as prepared, \
             patch.object(side.SideEntryEvidence, '_probe', slow_probe), \
             patch.object(side.time, 'perf_counter', side_effect=lambda: clock[0]):
            rows, meta = engine.rank(self.frame, '2', budget_seconds=1.)
        prepared.assert_called_once()
        checked.assert_not_called()
        self.assertFalse(meta['complete'])
        self.assertEqual(meta['reason'], 'side-budget-incomplete')
        self.assertEqual({r['mapId'] for r in rows}, {'map-a'})

    def test_immutable_frame_and_repeated_job_are_shared_lifecycle(self):
        engine = self.source()
        self.observe()
        work = engine.prepare_rank(self.frame, '2')
        self.frame[:] = 255
        job = work.next_job()
        self.assertIs(work.next_job(), job)
        job.run()
        self.assertFalse(work.frame.any())
        with self.assertRaises(ValueError):
            work.frame.setflags(write=True)
        with self.assertRaisesRegex(ValueError, 'entry-job-not-current'):
            job.run()
        work.finish()

    def test_real_black_frame_and_unsupported_floor_keep_failure_semantics(self):
        engine = self.source()
        rows, meta = engine.rank(self.frame, '2', budget_seconds=10.)
        self.assertEqual((rows, meta['reason']), ([], 'side-visible-structure-insufficient'))
        rows, meta = engine.rank(self.frame, '-3', budget_seconds=0.)
        self.assertEqual((rows, meta['reason']), ([], 'unsupported-floor'))


if __name__ == '__main__':
    unittest.main()
