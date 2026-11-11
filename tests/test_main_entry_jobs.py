"""Stepwise source evidence using actual component decoding and probe math.

Corner extraction is a supplied synthetic observation; final wall verification
is isolated in policy positives. Probe search, band reduction, job ownership,
identity adjudication and the legacy rank driver are real. No game acceptance.
"""
from copy import deepcopy
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

import difficult_entry_identity as entry
import test_entry_resource_io as fixture_io


class MainEntryJobTests(unittest.TestCase):
    def setUp(self):
        fixture = self.fixture = fixture_io.EntryResourceIOTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.points = np.array([[0, 0], [300, 0], [300, 300], [0, 300],
            [60, 60], [180, 60], [240, 90], [210, 180],
            [150, 240], [60, 240], [90, 150], [120, 210]], dtype=float)
        self.rays = np.array([[[1, 0], [0, 1]], [[-1, 0], [0, 1]],
                              [[-1, 0], [0, -1]], [[1, 0], [0, -1]]] * 3, dtype=float)
        self.frame = np.full((720, 1280, 3), 4, np.uint8)

    def source(self, *, ids=('map-a', 'map-b'), rotated=()):
        rows = []
        for mid in ids:
            matrix = (np.array([[1, -1], [1, 1]]) / np.sqrt(2)
                      if mid in rotated else np.eye(2))
            points, rays = self.points @ matrix.T, self.rays @ matrix.T
            row = dict(deepcopy(self.fixture.item), mapId=mid, center=[150, 150],
                outline=points[:4].tolist(), chains=[dict(
                    points=[(p - r[0] * 30).tolist(), p.tolist(), (p + r[1] * 30).tolist()],
                    rays=r.tolist()) for p, r in zip(points, rays)])
            rows.append(row)
        self.fixture.files[fixture_io.ENTRY_ROOT + '/entries.json'] = fixture_io.encoded(dict(schemaVersion=1, entries=rows))
        self.fixture.package()
        front = self.fixture.front()
        self.assertIsNone(front.error)
        return front

    def observations(self, front):
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(front, 'geometry', return_value={'synthetic-observation': True}))
        stack.enter_context(patch.object(entry, 'entry_corners', return_value=[
            dict(point=p * .5 + [200, 150], rays=r) for p, r in zip(self.points, self.rays)]))
        verify = stack.enter_context(patch.object(front, 'verify', return_value=dict(success=True)))
        return verify

    def drain(self, evidence):
        phases = []
        while (job := evidence.next_job()) is not None:
            phases.append((job.phase, job.map_id))
            job.run()
        return phases, evidence.finish()

    def test_pausing_after_one_real_probe_cannot_verify_or_resume_closed_work(self):
        front = self.source()
        verify = self.observations(front)
        evidence = front.prepare_rank(self.frame, '2')
        observed = evidence.next_job()
        self.assertIs(evidence.next_job(), observed)
        observed.run()
        first = evidence.next_job()
        self.assertEqual((first.phase, first.map_id), ('probe', 'map-a'))
        first.run()
        second = evidence.next_job()
        self.assertEqual((second.phase, second.map_id), ('probe', 'map-b'))
        rows, diagnostic = evidence.finish()
        self.assertEqual(diagnostic['evaluatedMapIds'], ['map-a'])
        self.assertFalse(diagnostic['complete'])
        self.assertEqual(rows[0]['score'], 12)
        self.assertFalse(rows[0]['identityVerified'])
        verify.assert_not_called()
        self.assertIsNone(evidence.next_job())
        with self.assertRaisesRegex(ValueError, 'entry-job-not-current'):
            second.run()

    def test_real_global_band_precedes_verification_and_keeps_a_matching_rival(self):
        front = self.source()
        verify = self.observations(front)
        phases, (rows, diagnostic) = self.drain(front.prepare_rank(self.frame, '2'))
        self.assertEqual(phases, [('observe', None), ('probe', 'map-a'), ('probe', 'map-b'),
                                  ('verify', 'map-a'), ('verify', 'map-b')])
        self.assertEqual(verify.call_count, 2)
        self.assertTrue(diagnostic['complete'])
        self.assertEqual([row['score'] for row in rows], [12, 12])
        self.assertFalse(any(row['identityVerified'] for row in rows))

    def test_unique_complete_source_and_known_map_keep_original_qualification(self):
        front = self.source(rotated=('map-b',))
        verify = self.observations(front)
        phases, (rows, diagnostic) = self.drain(front.prepare_rank(self.frame, '2'))
        self.assertEqual([mid for phase, mid in phases if phase == 'probe'], ['map-a', 'map-b'])
        self.assertEqual(verify.call_count, 1)
        self.assertEqual(diagnostic['reason'], 'entry-identity-verified')
        self.assertEqual(rows[0]['mapId'], 'map-a')
        self.assertTrue(rows[0]['identityVerified'])
        _, (known, diagnostic) = self.drain(front.prepare_rank(self.frame, '2', map_id='map-a'))
        self.assertTrue(known[0]['poseVerified'])
        self.assertFalse(known[0]['identityVerified'])
        self.assertEqual(diagnostic['reason'], 'entry-known-identity-pose-verified')

    def test_stopping_after_one_verification_does_not_erase_unverified_rival(self):
        front = self.source()
        self.observations(front)
        evidence = front.prepare_rank(self.frame, '2')
        for _ in range(4):  # observe, both probes, then the first verification
            evidence.next_job().run()
        pending = evidence.next_job()
        self.assertEqual((pending.phase, pending.map_id), ('verify', 'map-b'))
        rows, diagnostic = evidence.finish()
        self.assertTrue(rows[0]['verification']['success'])
        self.assertFalse(diagnostic['complete'])
        self.assertFalse(any(row['identityVerified'] for row in rows))

    def test_unavailable_rival_validation_is_unknown_instead_of_a_unique_winner(self):
        front = self.source()
        verify = self.observations(front)
        for failure in ('io-error', 'missing-shared-reference'):
            with self.subTest(failure=failure):
                def validate(frame, row, **_):
                    if row['mapId'] == 'map-a':
                        return dict(success=True)
                    if failure == 'io-error':
                        return Path(self.fixture.temp.name, 'missing-verified-resource.npz').read_bytes()
                    return dict(success=False, reason='entry-shared-structure-unavailable')
                verify.side_effect = validate
                rows, diagnostic = front.rank(self.frame, '2', budget_seconds=1.)
                self.assertEqual((diagnostic['complete'], any(row['identityVerified'] for row in rows)),
                                 (False, False), (rows, diagnostic))
                self.assertEqual(diagnostic['reason'], 'entry-verification-unavailable')
                self.assertFalse(diagnostic['verificationComplete'])

    def test_legacy_identity_consumer_does_not_call_resource_failure_budget_exhaustion(self):
        from map_identity_engine import MapIdentityEngine
        front = self.source()
        verify = self.observations(front)
        verify.side_effect = lambda frame, row, **_: (
            dict(success=True) if row['mapId'] == 'map-a'
            else dict(success=False, reason='entry-shared-structure-unavailable'))
        engine = MapIdentityEngine.__new__(MapIdentityEngine)
        maps = [SimpleNamespace(id=mid, name=mid) for mid in ('map-a', 'map-b')]
        engine.pack = SimpleNamespace(mode='nightmare', maps=maps, by_id={row.id: row for row in maps})
        engine.identity_floors, engine.identity_profile_id = ('2',), 'native'
        engine.entry_identity, engine.side_entry_identity = front, None
        engine.side_entry_error = 'side-fixture-unavailable'
        engine.entry_advisory = None
        engine.native_similarity_index = None
        engine._shared_entry_inventory = lambda _: None
        # Explicitly scoped source-policy fixture excludes optional global
        # discovery/icon advice; the real rank and main producer run normally.
        rows = engine.rank(self.frame, floor='2', budget_seconds=1.,
                           _established_members=('map-a', 'map-b'))
        self.assertFalse(any(row['identityHardGatePassed'] for row in rows))
        self.assertFalse(any(row['identityBudgetExceeded'] for row in rows))

    def test_four_competitive_maps_do_not_bypass_original_verification_limit(self):
        front = self.source(ids=('map-a', 'map-b', 'map-c', 'map-d'))
        verify = self.observations(front)
        phases, (rows, diagnostic) = self.drain(front.prepare_rank(self.frame, '2'))
        self.assertEqual(len(rows), 4)
        self.assertTrue(diagnostic['complete'])
        self.assertFalse(any(phase == 'verify' for phase, _ in phases))
        self.assertFalse(any(row['identityVerified'] for row in rows))
        verify.assert_not_called()

    def test_legacy_rank_runs_real_jobs_and_expired_budget_keeps_unprobed_rival_unknown(self):
        front = self.source(rotated=('map-b',))
        verify = self.observations(front)
        clock = [0.]
        probe = front._probe_entry
        def slow_probe(*args):
            result = probe(*args)
            clock[0] += 2.
            return result
        with patch.object(front, 'prepare_rank', wraps=front.prepare_rank) as prepared, \
             patch.object(front, '_probe_entry', side_effect=slow_probe), \
             patch.object(entry.time, 'perf_counter', side_effect=lambda: clock[0]):
            rows, diagnostic = front.rank(self.frame, '2', budget_seconds=1.)
        prepared.assert_called_once()
        self.assertEqual(diagnostic['evaluatedMapIds'], ['map-a'])
        self.assertFalse(diagnostic['complete'])
        self.assertFalse(any(row['identityVerified'] for row in rows))
        verify.assert_not_called()

    def test_job_observation_owns_pixels_and_completed_job_cannot_run_twice(self):
        front = self.source()
        self.observations(front)
        evidence = front.prepare_rank(self.frame, '2')
        self.frame[:] = 255
        job = evidence.next_job()
        job.run()
        np.testing.assert_array_equal(evidence.frame, np.full((720, 1280, 3), 4, np.uint8))
        with self.assertRaises(ValueError):
            evidence.frame.setflags(write=True)
        with self.assertRaisesRegex(ValueError, 'entry-job-not-current'):
            job.run()
        evidence.finish()

    def test_probe_exception_closes_work_and_cannot_repeat_partial_writes(self):
        front = self.source()
        self.observations(front)
        evidence = front.prepare_rank(self.frame, '2')
        evidence.next_job().run()
        job = evidence.next_job()
        with patch.object(front, '_probe_entry', side_effect=RuntimeError('probe boom')) as probe:
            with self.assertRaisesRegex(RuntimeError, 'probe boom'):
                job.run()
            self.assertIsNone(evidence.next_job())
            with self.assertRaisesRegex(ValueError, 'entry-job-not-current'):
                job.run()
        self.assertEqual(probe.call_count, 1)
        rows, diagnostic = evidence.finish()
        self.assertEqual(diagnostic['evaluatedMapIds'], ['map-a'])
        self.assertFalse(diagnostic['complete'])
        self.assertEqual(diagnostic['reason'], 'entry-work-failed')
        self.assertFalse(any(row['identityVerified'] for row in rows))

    def test_observation_interrupt_closes_work_without_observing_another_job(self):
        front = self.source()
        evidence = front.prepare_rank(self.frame, '2')
        job = evidence.next_job()
        with patch.object(front, 'geometry', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                job.run()
            self.assertIsNone(evidence.next_job())
            with self.assertRaisesRegex(ValueError, 'entry-job-not-current'):
                job.run()
        rows, diagnostic = evidence.finish()
        self.assertEqual(rows, [])
        self.assertFalse(diagnostic['complete'])
        self.assertEqual(diagnostic['reason'], 'entry-work-failed')

    def test_real_black_frame_and_zero_budget_preserve_existing_failure_results(self):
        front = self.source()
        rows, diagnostic = front.rank(np.zeros_like(self.frame), '2')
        self.assertEqual(rows, [])
        self.assertEqual(diagnostic['reason'], 'entry-visible-structure-insufficient')
        with patch.object(front, 'geometry', side_effect=AssertionError('zero budget observed pixels')):
            rows, diagnostic = front.rank(self.frame, '2', budget_seconds=0)
        self.assertEqual(rows, [])
        self.assertFalse(diagnostic['complete'])


if __name__ == '__main__':
    unittest.main()
