"""Real sequential work lifecycle and scanner use; no game IO or timing claims."""
import unittest
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from entry_evidence_work import EntryEvidenceWork, FrameSnapshot, ObservationWorkScheduler
from s1_structure_selection import StructureCandidateScanner, StructureCatalog, StructureEvidenceSession
from map_identity_engine import MapIdentityEngine
from client_region_capture import ClientCaptureContext, RegionCapture
from test_s1_structure_selection import catalog, pose
import test_main_entry_jobs as main_fixture
import test_side_entry_jobs as side_fixture


class CounterWork(EntryEvidenceWork):
    def __init__(self, frame, costs, clock, *, error_at=None):
        self.costs, self.clock, self.error_at = costs, clock, error_at
        self.ran, self.exits, self.jobs = [], 0, []
        super().__init__(frame)

    def _run(self, index):
        if index == self.error_at:
            raise OSError('source read failed')
        self.ran.append(index)
        self.clock[0] += self.costs[index]

    def _jobs(self):
        try:
            for index in range(len(self.costs)):
                job = self._job(str(index), 'source-probe', lambda index=index: self._run(index))
                self.jobs.append(job)
                yield job
        finally:
            self.exits += 1


class ObservationSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.clock = [0.]
        self.driver = ObservationWorkScheduler(clock=lambda: self.clock[0])
        self.frame = np.arange(60, dtype=np.uint8).reshape(4, 5, 3)

    def work(self, costs=(.6, .6, .6), **kwargs):
        return CounterWork(self.frame, costs, self.clock, **kwargs)

    def test_snapshot_is_owned_once_and_shared_with_source_work(self):
        expected = self.frame.copy()
        snapshot = FrameSnapshot(self.frame)
        a = CounterWork(snapshot, (), self.clock)
        b = CounterWork(snapshot, (), self.clock)
        self.frame[:] = 0
        self.assertIs(a.frame, snapshot.pixels)
        self.assertIs(b.frame, snapshot.pixels)
        np.testing.assert_array_equal(a.frame, expected)
        with self.assertRaises(ValueError):
            a.frame.setflags(write=True)

    def test_capture_admission_and_actual_scanner_share_one_immutable_owner(self):
        from priority_admission import FrameSnapshot as AdmissionSnapshot
        self.assertIs(AdmissionSnapshot, FrameSnapshot)
        expected = self.frame.copy()
        captured = RegionCapture(self.frame, (0, 0, 5, 4), (0, 0, 5, 4),
            ClientCaptureContext((5, 4), (0, 0, 5, 4), 96), 'controlled-backend')
        self.frame[:] = 0
        seen = []
        def prepare(frame, bounds):
            self.assertIs(frame, captured.frame_bgr)
            np.testing.assert_array_equal(frame, expected)
            def fit(actual, mid, floor, cancelled, **kwargs):
                self.assertIs(actual, frame)
                seen.append(mid)
                return pose()
            return fit
        report = StructureCandidateScanner(catalog(), None, prepare_fit=prepare).scan(
            captured.frame_bgr, floor='-1', floor_source='operator-declared',
            frame_snapshot=captured.frame_snapshot)
        self.assertEqual(seen, ['a', 'b', 'c'])
        self.assertEqual(report['frameKey'], captured.frame_snapshot.frame_key)
        with self.assertRaises(ValueError):
            captured.frame_bgr.setflags(write=True)

    def test_foreign_snapshot_never_reaches_a_fit(self):
        snapshot = FrameSnapshot(self.frame)
        fit = Mock(side_effect=AssertionError('foreign frame ran'))
        with self.assertRaisesRegex(ValueError, 'frame-snapshot-mismatch'):
            StructureCandidateScanner(catalog(), fit).scan(
                snapshot.pixels.copy(), floor='-1', floor_source='operator-declared',
                frame_snapshot=snapshot)
        fit.assert_not_called()

    def test_deadline_stops_between_jobs_and_closes_unrun_job(self):
        work = self.work()
        self.assertEqual(self.driver.drive(work, deadline=1.), 'budget-unresolved')
        self.assertEqual(work.ran, [0, 1])
        self.assertEqual(work.exits, 1)
        self.assertIsNone(work.next_job())
        with self.assertRaisesRegex(ValueError, 'entry-job-not-current'):
            work.jobs[-1].run()

    def test_cancelled_or_zero_budget_never_runs_first_kernel(self):
        for expected, options in (('cancelled', dict(cancelled=lambda: True)),
                                  ('budget-unresolved', dict(deadline=0.))):
            with self.subTest(status=expected):
                work = self.work()
                self.assertEqual(self.driver.drive(work, **options), expected)
                self.assertEqual(work.ran, [])
                self.assertEqual(work.exits, 1)
                self.assertIsNone(work.next_job())

    def test_last_inflight_job_can_finish_the_plan_after_deadline(self):
        work = self.work((2.,))
        self.assertEqual(self.driver.drive(work, deadline=1.), 'complete')
        self.assertEqual(work.ran, [0])
        self.assertTrue(work._complete)

    def test_cancellation_after_kernel_does_not_publish_its_result(self):
        work, updates = self.work(), []
        status = self.driver.drive(work, cancelled=lambda: bool(work.ran),
                                   on_job_finished=lambda job: updates.append(job.map_id))
        self.assertEqual(status, 'cancelled')
        self.assertEqual(work.ran, [0])
        self.assertEqual(updates, [])
        self.assertEqual(work.exits, 1)

    def test_material_stop_keeps_completed_result_and_closes_remaining_work(self):
        work = self.work()
        status = self.driver.drive(work, on_job_finished=lambda job: 'first-material-committed')
        self.assertEqual(status, 'first-material-committed')
        self.assertEqual(work.ran, [0])
        self.assertIsNone(work.next_job())
        self.assertEqual(work.exits, 1)

    def test_source_and_consumer_exceptions_close_without_replaying_jobs(self):
        for source_error in (True, False):
            with self.subTest(source_error=source_error):
                work = self.work(error_at=0 if source_error else None)
                def fail(job):
                    raise OSError('consumer failed')
                with self.assertRaises(OSError):
                    self.driver.drive(work, on_job_finished=fail)
                self.assertIsNone(work.next_job())
                self.assertEqual(work.exits, 1)
                with self.assertRaisesRegex(ValueError, 'entry-job-not-current'):
                    work.jobs[0].run()

    def test_existing_real_main_entry_work_is_driven_without_legacy_rank(self):
        fixture = main_fixture.MainEntryJobTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        front = fixture.source()
        fixture.observations(front)
        snapshot = FrameSnapshot(fixture.frame)
        work = front.prepare_rank(snapshot, '2')
        with patch.object(front, 'rank', side_effect=AssertionError('legacy rank')):
            self.assertEqual(self.driver.drive(work), 'complete')
        rows, diagnostics = work.finish()
        self.assertIs(work.frame, snapshot.pixels)
        self.assertTrue(diagnostics['complete'])
        self.assertEqual(diagnostics['evaluatedMapIds'], ['map-a', 'map-b'])
        self.assertEqual({row['mapId'] for row in rows}, {'map-a', 'map-b'})

    def test_actual_scanner_uses_driver_and_one_prepared_frame(self):
        prepared, calls, drives = [], [], []
        def prepare(frame, bounds):
            prepared.append(frame)
            def fit(actual, mid, floor, cancelled, **kwargs):
                self.assertIs(actual, frame)
                calls.append(mid)
                return pose()
            return fit
        original = ObservationWorkScheduler.drive
        def drive(driver, work, **kwargs):
            drives.append(work)
            return original(driver, work, **kwargs)
        with patch.object(ObservationWorkScheduler, 'drive', drive):
            report = StructureCandidateScanner(catalog(), None, prepare_fit=prepare).scan(
                self.frame, floor='-1', floor_source='operator-declared')
        self.assertEqual(len(drives), 1)
        self.assertEqual(len(prepared), 1)
        self.assertEqual(calls, ['a', 'b', 'c'])
        self.assertEqual(report['supportedMapIds'], ['a', 'b', 'c'])
        self.assertFalse(report['identityHardGatePassed'])


class EntryObservationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = main_fixture.MainEntryJobTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def engine(self, *, unique=False):
        front = self.fixture.source(rotated=('map-b',) if unique else ())
        self.fixture.observations(front)
        engine = MapIdentityEngine.__new__(MapIdentityEngine)
        engine.entry_identity, engine.side_entry_identity = front, None
        engine.identity_floors, engine.identity_profile_id = ('2',), 'shared-structure'
        engine.side_entry_error, engine.entry_advisory = None, None
        engine.native_similarity_index = engine.entry_shared_reference_provider = None
        maps = [SimpleNamespace(id=mid, name=mid, layers={'2': None}) for mid in ('map-a', 'map-b')]
        engine.pack = SimpleNamespace(maps=maps, by_id={m.id: m for m in maps}, mode='nightmare')
        return engine

    def scan(self, engine, **options):
        c = StructureCatalog('s0', 'nightmare', 'real-fixture-base', ('map-a', 'map-b'), ('2',))
        scanner = StructureCandidateScanner(c, lambda *a, **kw: dict(success=False, reason='unknown'),
            clock=time.perf_counter)
        with patch.object(engine, 'rank', side_effect=AssertionError('old rank')), \
             patch.object(engine.entry_identity, 'rank', side_effect=AssertionError('old main rank')):
            return scanner.scan(self.fixture.frame, floor='2', floor_source='operator-declared',
                prepare_entry=engine.prepare_entry_observation, **options)

    def test_real_entry_plan_keeps_matching_rival_separate_from_canonical_poses(self):
        report = self.scan(self.engine())
        self.assertEqual(report['checkedMapIds'], ['map-a', 'map-b'])
        self.assertEqual(report['supportedMapIds'], [])
        entries = report['entryEvidence']['rows']
        self.assertEqual({r['mapId'] for r in entries}, {'map-a', 'map-b'})
        self.assertFalse(any(r['identityHardGatePassed'] for r in entries))
        self.assertFalse(report['identityHardGatePassed'])

    def test_unique_real_main_evidence_can_stop_without_running_canonical_fit(self):
        updates = []
        def accept(evidence):
            updates.append(evidence)
            return any(row['identityHardGatePassed'] for row in evidence['rows'])
        report = self.scan(self.engine(unique=True), on_entry_evidence=accept)
        self.assertEqual(len(updates), 1)
        self.assertEqual([r['mapId'] for r in updates[0]['rows'] if r['identityHardGatePassed']], ['map-a'])
        self.assertEqual(report['status'], 'subset-unresolved')
        self.assertEqual(report['checkedMapIds'], [])
        self.assertEqual(report['unscannedMapIds'], ['map-a', 'map-b'])
        self.assertFalse(report['identityHardGatePassed'])

    def test_closing_before_competing_probe_keeps_both_maps_unknown(self):
        engine = self.engine(unique=True)
        work = engine.prepare_entry_observation(self.fixture.frame, '2', deadline=float('inf'))
        phases = []
        for _ in range(3):
            job = work.next_job()
            phases.append(job.phase)
            job.run()
        self.assertEqual(phases, ['entry-scope', 'main:observe', 'main:probe'])
        pending = work.next_job()
        self.assertEqual(pending.map_id, 'map-b')
        rows = work.finish()
        self.assertEqual({r['mapId'] for r in rows}, {'map-a', 'map-b'})
        self.assertFalse(any(r['identityHardGatePassed'] for r in rows))
        with self.assertRaisesRegex(ValueError, 'entry-job-not-current'):
            pending.run()

    def test_entry_ticket_cannot_change_pose_or_be_used_by_s1_or_late_frame(self):
        c = StructureCatalog('s0', 'nightmare', 'base', ('a', 'b'), ('1',))
        s = StructureEvidenceSession(c, 'round')
        ticket = s.begin_observation('1')
        evidence = dict(role='identity-entry', frameKey='1'*64, resourceKey='base', floor='1',
            rows=[dict(mapId='a', identityHardGatePassed=True)])
        self.assertTrue(s.commit_entry_evidence(evidence, ticket))
        self.assertFalse(s.snapshot()['uniqueIdentityVerified'])
        self.assertEqual(s.snapshot()['currentSupportedMapIds'], [])
        s.revoke_current()
        self.assertFalse(s.commit_entry_evidence(evidence, ticket))
        s1 = StructureEvidenceSession(catalog(), 'round')
        s1ticket = s1.begin_observation('-1')
        self.assertFalse(s1.commit_entry_evidence(dict(evidence, floor='-1', resourceKey='base'), s1ticket))

    def test_source_preparation_keeps_native_rank_and_s1_role_limits(self):
        from difficult_native_identity import NativeDifficultCandidateEngine
        engine = NativeDifficultCandidateEngine.__new__(NativeDifficultCandidateEngine)
        engine.qt_rank_ready = False
        engine.qt_rank_gaps = ('native-base-rank-unavailable',)
        with self.assertRaisesRegex(ValueError, 'native-base-rank-unavailable'):
            engine.prepare_entry_observation(self.fixture.frame, '2', deadline=1.)
        engine.qt_rank_ready, engine.qt_season = True, 's1'
        with self.assertRaisesRegex(ValueError, 'proposal-only-entry-evidence'):
            engine.prepare_entry_observation(self.fixture.frame, '-1', deadline=1.)

    def test_main_rival_resource_error_cannot_fall_through_to_side_identity(self):
        engine = self.engine()
        engine.identity_profile_id = 'native'
        side = side_fixture.SideEntryJobTests('runTest')
        side.setUp()
        self.addCleanup(side.doCleanups)
        locator = engine.side_entry_identity = side.source(('map-a',))
        side.observe()

        class NoExit(EntryEvidenceWork):
            def _jobs(self):
                yield from ()
            def finish(self, **kwargs):
                self.close()
                return [], dict(reason='entry-no-reliable-exit', complete=True, entryObserved=False)

        original = engine.entry_identity._probe_entry
        def fail_rival(frame, entry, *args):
            if entry[0]['mapId'] == 'map-b':
                raise OSError('missing required main rival')
            return original(frame, entry, *args)
        with patch.object(locator, 'prepare_rank_entry', side_effect=lambda frame, *a, **kw: NoExit(frame)), \
             patch.object(locator, 'check_entry_evidence', return_value={}), \
             patch.object(engine.entry_identity, '_probe_entry', side_effect=fail_rival):
            work = engine.prepare_entry_observation(self.fixture.frame, '2', deadline=float('inf'))
            with self.assertRaisesRegex(OSError, 'missing required main rival'):
                ObservationWorkScheduler().drive(work)
            self.assertFalse(any(row['identityHardGatePassed'] for row in work.finish()))


if __name__ == '__main__':
    unittest.main()
