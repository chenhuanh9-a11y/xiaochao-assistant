"""Work ownership/scheduling with controlled coordinate and fit measurements.

The scheduler, immutable snapshot, stage boundaries and consumption guards are
real. Provider numerical measurements are supplied; these are not accuracy tests.
"""
from copy import deepcopy
from types import SimpleNamespace
import unittest

import numpy as np

from common_coordinate_observation import CommonCoordinateObservationWork
from entry_evidence_work import FrameSnapshot, ObservationWorkScheduler
from s1_structure_selection import StructureCatalog
from test_s1_structure_selection import pose


class CommonCoordinateWorkTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = FrameSnapshot(np.zeros((24,32,3), np.uint8))
        self.catalog = StructureCatalog('s1', 'difficult', 'real-fixture-resource',
            ('a', 'b'), ('-1','-2','-3','-4'))
        self.current, self.cancelled = True, False
        self.calls = []
        self.report = dict(mode='difficult', resourceKey=self.catalog.resource_key, floor='-1',
            evaluatedMapIds=['a','b'], contradictedMapIds=['b'], coordinateVerified=True)
        def prepare(frame, ids, floor, *, cancelled):
            self.assertIs(frame, self.snapshot.pixels)
            self.assertEqual((ids, floor), (('a','b'), '-1'))
            self.calls.append(('coordinate', cancelled()))
            return deepcopy(self.report)
        def fixed(frame, map_id, floor, cancelled, *, report):
            self.assertIs(frame, self.snapshot.pixels)
            self.assertEqual((map_id, floor), ('a','-1'))
            self.assertEqual(report, self.report)
            self.calls.append(('fixed', cancelled()))
            return pose()
        self.prepared = SimpleNamespace(catalog=self.catalog, current=lambda:self.current,
            prepare=prepare, fixed_fit=fixed)
        self.engine = SimpleNamespace(qt_s1_coordinate_recognition=self.prepared)

    def work(self, **options):
        return CommonCoordinateObservationWork(self.prepared, self.snapshot, '-1', ('a','b'), 3,
            cancelled=lambda:self.cancelled, **options)

    def drive(self, work, choose=True):
        def consume(job):
            if job.phase == 'common-coordinate':
                self.assertTrue(work.coordinate_complete)
                self.assertFalse(work._complete)
                self.assertIsNone(work.pose)
                work.ledger_consumed = True  # Controlled owner ledger boundary.
                if choose:
                    self.assertTrue(work.choose_owner_map(dict(mapId='a', poolRevision=4)))
        return ObservationWorkScheduler().drive(work, cancelled=lambda:self.cancelled,
            on_job_finished=consume)

    def valid(self, work, **changes):
        arguments = dict(engine=self.engine, frame=self.snapshot.pixels,
                         map_id='a', floor='-1', pool_revision=4)
        arguments.update(changes)
        return work.current_fit(**arguments)

    def test_owner_decision_sits_between_complete_report_and_one_fixed_fit(self):
        work = self.work()
        self.assertEqual(self.drive(work), 'complete')
        self.assertEqual(self.calls, [('coordinate',False), ('fixed',False)])
        self.assertTrue(self.valid(work))
        self.assertIs(work.finish(), work.report)
        self.assertTrue(self.valid(work))

    def test_unresolved_owner_does_not_run_fixed_fit_or_offer_pose(self):
        work = self.work()
        self.assertEqual(self.drive(work, choose=False), 'complete')
        self.assertEqual(self.calls, [('coordinate',False)])
        self.assertIsNone(work.pose)
        self.assertFalse(self.valid(work))

    def test_old_or_foreign_owner_context_cannot_consume_completed_pose(self):
        work = self.work()
        self.drive(work)
        for changes in (dict(frame=self.snapshot.pixels.copy()), dict(map_id='b'),
                dict(floor='-2'), dict(pool_revision=5),
                dict(engine=SimpleNamespace(qt_s1_coordinate_recognition=object()))):
            with self.subTest(changes=changes):
                self.assertFalse(self.valid(work, **changes))
        work.ledger_consumed = False
        self.assertFalse(self.valid(work))
        work.ledger_consumed = True
        self.current = False
        self.assertFalse(self.valid(work))

    def test_cancelled_current_lane_preserves_completed_source_report_without_pose(self):
        prepare = self.prepared.prepare
        def finish_source(*args, **kwargs):
            value = prepare(*args, **kwargs)
            self.cancelled = True
            return value
        self.prepared.prepare = finish_source
        work = self.work(source_cancelled=lambda:False)
        self.assertEqual(self.drive(work), 'cancelled')
        self.assertTrue(work.source_started)
        self.assertTrue(work.coordinate_complete)
        self.assertFalse(work.ledger_consumed)
        self.assertFalse(work._complete)
        self.assertIsNone(work.pose)
        self.assertEqual(work.finish(), self.report)
        self.assertFalse(self.valid(work))

    def test_external_choice_cannot_supply_pose_or_extend_source_scope(self):
        work = self.work()
        self.assertFalse(work.choose_owner_map(dict(mapId='a',poolRevision=4)))
        job = work.next_job()
        job.run()
        self.assertFalse(work.choose_owner_map(dict(mapId='foreign',poolRevision=4)))
        self.assertTrue(work.choose_owner_map(dict(mapId='a',poolRevision=4,
            pose=dict(matrixSourceToClient=np.full((3,3),999.)))))
        self.assertEqual(ObservationWorkScheduler().drive(work), 'complete')
        work.ledger_consumed = True
        self.assertTrue(self.valid(work))
        np.testing.assert_array_equal(work.pose['matrixSourceToClient'], np.eye(3))

    def test_retirement_between_scheduler_check_and_source_kernel_prevents_duplicate_history(self):
        work = self.work()
        job = work.next_job()  # Scheduler has admitted a job, but it has not claimed its source yet.
        self.assertTrue(work.retire_unstarted_source())
        job.run()
        self.assertFalse(work.source_started)
        self.assertFalse(work.coordinate_complete)
        self.assertEqual(self.calls, [])
        self.assertIsNone(work.finish())


if __name__ == '__main__':
    unittest.main()
