"""Real sequential work lifecycle and scanner use; no game IO or timing claims."""
import unittest
from unittest.mock import patch

import numpy as np

from entry_evidence_work import EntryEvidenceWork, FrameSnapshot, ObservationWorkScheduler
from s1_structure_selection import StructureCandidateScanner
from test_s1_structure_selection import catalog, pose
import test_main_entry_jobs as main_fixture


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


if __name__ == '__main__':
    unittest.main()
