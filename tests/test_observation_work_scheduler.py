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

    def test_candidate_purpose_keeps_legacy_entry_geometry_without_granting_identity(self):
        engine = self.engine(unique=True)
        engine.qt_rank_ready = False
        updates = []
        def accept(evidence):
            updates.append(evidence)
            return any(row['identityHardGatePassed'] for row in evidence['rows'])
        report = self.scan(engine, on_entry_evidence=accept)
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['checkedMapIds'], ['map-a', 'map-b'])
        self.assertEqual(len(updates), 1)
        row = next(row for row in updates[0]['rows'] if row['mapId'] == 'map-a')
        self.assertTrue(row['difficultEntry']['verification']['success'])
        self.assertFalse(row['difficultEntry']['poseVerified'])
        self.assertFalse(row['difficultEntry']['identityVerified'])
        self.assertFalse(row['identityHardGatePassed'])
        self.assertFalse(row['strongSuggestion'])
        self.assertFalse(engine.last_difficult_evidence['tilemapEntry']['identityAuthorizationAllowed'])

    def test_current_proposal_reaches_prepared_fit_and_keeps_the_complete_pool(self):
        engine = self.engine(unique=True)
        engine.qt_rank_ready = False
        matrix = [[.5, 0., 200.], [0., .5, 150.], [0., 0., 1.]]
        engine.entry_identity.verify.return_value = dict(success=True, matrixSourceToClient=matrix)
        calls = []
        def prepare(frame, bounds):
            def fit(actual, mid, floor, cancelled, **kwargs):
                self.assertIs(actual, frame)
                self.assertEqual(kwargs['observation_bounds'], bounds)
                calls.append((mid, kwargs.get('entry_candidate')))
                return dict(success=False, reason='fixture-canonical-unknown')
            return fit
        c = StructureCatalog('s0', 'nightmare', 'real-fixture-base', ('map-b', 'map-a'), ('2',))
        scanner = StructureCandidateScanner(c, lambda *a, **kw: self.fail('unprepared fit'),
            prepare_fit=prepare)
        report = scanner.scan(self.fixture.frame, floor='2', floor_source='operator-declared',
            prepare_entry=engine.prepare_entry_observation)
        self.assertEqual([mid for mid, _ in calls], ['map-a', 'map-b'])
        self.assertTrue(calls[0][1]['verification']['success'])
        self.assertFalse(calls[0][1]['identityVerified'])
        self.assertFalse(calls[0][1]['poseVerified'])
        self.assertIsNone(calls[1][1])
        self.assertEqual(report['checkedMapIds'], ['map-a', 'map-b'])
        self.assertEqual(report['unscannedMapIds'], [])
        self.assertEqual(report['unknownMapIds'], ['map-b', 'map-a'])
        self.assertEqual(report['supportedMapIds'], [])
        self.assertFalse(report['identityHardGatePassed'])

    def test_entry_from_another_snapshot_cannot_seed_the_current_fit(self):
        engine = self.engine(unique=True)
        c = StructureCatalog('s0', 'nightmare', 'real-fixture-base', ('map-a', 'map-b'), ('2',))
        fit = Mock(side_effect=AssertionError('foreign frame fit'))
        def prepare(snapshot, floor, **kwargs):
            return engine.prepare_entry_observation(FrameSnapshot(snapshot.pixels), floor, **kwargs)
        with self.assertRaisesRegex(ValueError, 'entry-frame-snapshot-mismatch'):
            StructureCandidateScanner(c, fit).scan(self.fixture.frame, floor='2',
                floor_source='operator-declared', prepare_entry=prepare)
        fit.assert_not_called()

    def test_early_preview_keeps_candidate_identity_work_running(self):
        engine = self.engine(unique=True)
        engine.entry_identity.verify.return_value = dict(success=True, matrixSourceToClient=np.eye(3))
        events, calls = [], []
        original = engine.prepare_source_structure_candidates
        def source(*args, **kwargs):
            events.append('source')
            return original(*args, **kwargs)
        def fit(frame, mid, floor, cancelled, **kwargs):
            events.append('fit')
            calls.append((mid, kwargs))
            return pose()
        def publish(evidence):
            events.append('identity')
            self.assertTrue(any(row['identityHardGatePassed'] for row in evidence['rows']))
            return True
        c = StructureCatalog('s0', 'nightmare', 'real-fixture-base', ('map-a', 'map-b'), ('2',))
        with patch.object(engine, 'prepare_source_structure_candidates', side_effect=source):
            report = StructureCandidateScanner(c, fit).scan(self.fixture.frame, floor='2',
                floor_source='operator-declared', prepare_entry=engine.prepare_entry_observation,
                on_progress=lambda report: events.append('preview'),
                first_material_committed=lambda mid: True, on_entry_evidence=publish)
        self.assertEqual(events, ['fit', 'preview', 'source', 'identity'])
        self.assertEqual([mid for mid, _ in calls], ['map-a'])
        self.assertIn('entry_observation', calls[0][1])
        self.assertIsNone(calls[0][1]['entry_observation'])
        self.assertTrue(calls[0][1]['entry_candidate']['verification']['success'])
        self.assertEqual(report['checkedMapIds'], ['map-a'])
        self.assertEqual(report['supportedMapIds'], ['map-a'])
        self.assertEqual(report['unscannedMapIds'], ['map-b'])
        self.assertFalse(report['identityHardGatePassed'])

    def test_early_proposal_is_not_a_fit_or_identity_authority(self):
        engine = self.engine(unique=True)
        engine.qt_rank_ready = False
        engine.entry_identity.verify.return_value = dict(success=True, matrixSourceToClient=np.eye(3))
        events, calls = [], []
        original = engine.prepare_source_structure_candidates
        def source(*args, **kwargs):
            events.append('source')
            return original(*args, **kwargs)
        def fit(frame, mid, floor, cancelled, **kwargs):
            events.append('fit:' + mid)
            calls.append(mid)
            return dict(success=False, reason='actual-canonical-rejected')
        c = StructureCatalog('s0', 'nightmare', 'real-fixture-base', ('map-a', 'map-b'), ('2',))
        with patch.object(engine, 'prepare_source_structure_candidates', side_effect=source):
            report = StructureCandidateScanner(c, fit).scan(self.fixture.frame, floor='2',
                floor_source='operator-declared', prepare_entry=engine.prepare_entry_observation,
                on_progress=lambda report: self.fail('rejected geometry cannot preview'))
        self.assertEqual(events, ['fit:map-a', 'source', 'fit:map-a', 'fit:map-b'])
        self.assertEqual(calls, ['map-a', 'map-a', 'map-b'])
        self.assertEqual(report['checkedMapIds'], ['map-a', 'map-b'])
        self.assertEqual(report['supportedMapIds'], [])
        self.assertFalse(any(row['identityHardGatePassed'] for row in report['entryEvidence']['rows']))

    def test_cancelling_early_preview_cannot_publish_later_identity(self):
        engine = self.engine(unique=True)
        engine.entry_identity.verify.return_value = dict(success=True, matrixSourceToClient=np.eye(3))
        cancelled = [False]
        c = StructureCatalog('s0', 'nightmare', 'real-fixture-base', ('map-a', 'map-b'), ('2',))
        def preview(report):
            cancelled[0] = True
        publish = Mock(return_value=True)
        with patch.object(engine, 'prepare_source_structure_candidates',
                side_effect=AssertionError('cancelled remainder')):
            report = StructureCandidateScanner(c, lambda *a, **kw: pose()).scan(
                self.fixture.frame, floor='2', floor_source='operator-declared',
                prepare_entry=engine.prepare_entry_observation, on_progress=preview,
                on_entry_evidence=publish, cancelled=lambda: cancelled[0])
        publish.assert_not_called()
        self.assertEqual(report['status'], 'cancelled')
        self.assertEqual(report['poses'], {})

    def test_preview_error_does_not_abort_candidate_identity(self):
        engine = self.engine(unique=True)
        engine.entry_identity.verify.return_value = dict(success=True, matrixSourceToClient=np.eye(3))
        evidence = []
        def publish(report):
            evidence.append(report)
            return True
        c = StructureCatalog('s0', 'nightmare', 'real-fixture-base', ('map-a', 'map-b'), ('2',))
        fit = Mock(side_effect=OSError('temporary preview failure'))
        report = StructureCandidateScanner(c, fit).scan(self.fixture.frame, floor='2',
            floor_source='operator-declared', prepare_entry=engine.prepare_entry_observation,
            on_progress=lambda report: self.fail('error cannot produce material'), on_entry_evidence=publish)
        fit.assert_called_once()
        self.assertEqual(len(evidence), 1)
        self.assertTrue(any(row['identityHardGatePassed'] for row in evidence[0]['rows']))
        self.assertEqual(report['outcomes'][0]['reason'], 'entry-preview-fit-unresolved')
        self.assertEqual(report['supportedMapIds'], [])

    def test_preview_memory_error_is_not_hidden(self):
        engine = self.engine(unique=True)
        engine.entry_identity.verify.return_value = dict(success=True, matrixSourceToClient=np.eye(3))
        c = StructureCatalog('s0', 'nightmare', 'real-fixture-base', ('map-a', 'map-b'), ('2',))
        with self.assertRaises(MemoryError), patch.object(engine, 'prepare_source_structure_candidates',
                side_effect=AssertionError('failed preview cannot advance')):
            StructureCandidateScanner(c, Mock(side_effect=MemoryError())).scan(self.fixture.frame,
                floor='2', floor_source='operator-declared', prepare_entry=engine.prepare_entry_observation,
                on_progress=lambda report: self.fail('failed preview'))

    def test_prefix_unknown_can_use_completed_work_without_duplicate_checked_ids(self):
        engine = self.engine(unique=True)
        engine.qt_rank_ready = False
        engine.entry_identity.verify.return_value = dict(success=True, matrixSourceToClient=np.eye(3))
        c = StructureCatalog('s0', 'nightmare', 'real-fixture-base', ('map-a', 'map-b'), ('2',))
        session = StructureEvidenceSession(c, 'round')
        ticket = session.begin_observation('2')
        calls = []
        def fit(frame, mid, floor, cancelled, **kwargs):
            calls.append((mid, 'entry_observation' in kwargs))
            if mid == 'map-a' and 'entry_observation' not in kwargs:
                return pose()
            return dict(success=False, reason='prefix-unknown')
        def preview(report):
            self.assertTrue(session.commit(report, ticket))
        report = StructureCandidateScanner(c, fit).scan(self.fixture.frame, floor='2',
            floor_source='operator-declared', prepare_entry=engine.prepare_entry_observation,
            on_progress=preview)
        self.assertEqual(calls, [('map-a', True), ('map-a', False), ('map-b', False)])
        self.assertEqual(report['checkedMapIds'], ['map-a', 'map-b'])
        self.assertEqual(report['supportedMapIds'], ['map-a'])
        self.assertEqual([row['mapId'] for row in report['outcomes']], ['map-a', 'map-b'])
        self.assertEqual(report['outcomes'][0]['outcome'], 'supported')
        self.assertTrue(session.commit(report, ticket))

    def test_changed_prefix_seed_retries_but_identical_seed_does_not(self):
        class Seeds(EntryEvidenceWork):
            pose_proposal_rows = []
            def _jobs(self):
                for x in (0., 0., 40.):
                    def snapshot(x=x):
                        matrix = np.eye(3)
                        matrix[0, 2] = x
                        self.pose_proposal_rows = [dict(mapId='map-a', difficultEntry=dict(
                            mapId='map-a', verification=dict(success=True, matrixSourceToClient=matrix)))]
                    yield self._job(None, 'entry-pose-proposals', snapshot)
            def finish(self):
                self.close()
                return self.pose_proposal_rows
        c = StructureCatalog('s0', 'nightmare', 'real-fixture-base', ('map-a',), ('2',))
        translations = []
        def fit(frame, mid, floor, cancelled, **kwargs):
            x = kwargs['entry_candidate']['verification']['matrixSourceToClient'][0, 2]
            translations.append(x)
            return pose() if x == 40. else dict(success=False, reason='initial-seed-unresolved')
        report = StructureCandidateScanner(c, fit).scan(self.fixture.frame, floor='2',
            floor_source='operator-declared', prepare_entry=lambda snapshot, *a, **kw: Seeds(snapshot),
            on_progress=lambda report: None)
        self.assertEqual(translations, [0., 40.])
        self.assertEqual(report['checkedMapIds'], ['map-a'])
        self.assertEqual(report['supportedMapIds'], ['map-a'])

    def test_entry_hint_keeps_preferred_and_planned_scope_and_cannot_revive_cancel(self):
        engine = self.engine(unique=True)
        engine.qt_rank_ready = False
        engine.entry_identity.verify.return_value = dict(success=True, matrixSourceToClient=np.eye(3))
        c = StructureCatalog('s0', 'nightmare', 'real-fixture-base', ('map-b', 'map-a'), ('2',))
        for options, expected in ((dict(preferred_map_id='map-b'), ['map-b', 'map-a']),
                                  (dict(planned_map_ids=('map-b',)), ['map-b'])):
            with self.subTest(options=options):
                fit = Mock(return_value=dict(success=False, reason='fixture-unknown'))
                report = StructureCandidateScanner(c, fit).scan(self.fixture.frame, floor='2',
                    floor_source='operator-declared', prepare_entry=engine.prepare_entry_observation, **options)
                self.assertEqual([call.args[1] for call in fit.call_args_list], expected)
                self.assertEqual(set(report['checkedMapIds']) | set(report['unscannedMapIds']), set(c.map_ids))
                self.assertFalse(report['identityHardGatePassed'])
        cancelled = [False]
        def stop(report):
            cancelled[0] = True
            return False
        fit = Mock(side_effect=AssertionError('cancelled hint fit'))
        report = StructureCandidateScanner(c, fit).scan(self.fixture.frame, floor='2',
            floor_source='operator-declared', prepare_entry=engine.prepare_entry_observation,
            on_entry_evidence=stop, cancelled=lambda: cancelled[0])
        fit.assert_not_called()
        self.assertEqual(report['status'], 'cancelled')

    def test_ui_evidence_and_fit_mutations_do_not_change_the_entry_source_rows(self):
        engine = self.engine(unique=True)
        engine.qt_rank_ready = False
        matrix = np.eye(3)
        engine.entry_identity.verify.return_value = dict(success=True, matrixSourceToClient=matrix)
        c = StructureCatalog('s0', 'nightmare', 'real-fixture-base', ('map-b', 'map-a'), ('2',))
        def publish(report):
            entry = next(r['difficultEntry'] for r in report['rows'] if r.get('difficultEntry'))
            entry['verification']['matrixSourceToClient'][0, 2] = 3000
            return False
        def fit(frame, mid, floor, cancelled, **kwargs):
            proposal = kwargs.get('entry_candidate')
            if proposal is not None:
                self.assertEqual(proposal['verification']['matrixSourceToClient'][0, 2], 0)
                proposal['verification']['matrixSourceToClient'][0, 2] = 6000
            return dict(success=False, reason='fixture-unknown')
        report = StructureCandidateScanner(c, fit).scan(self.fixture.frame, floor='2',
            floor_source='operator-declared', prepare_entry=engine.prepare_entry_observation, on_entry_evidence=publish)
        entry = next(r['difficultEntry'] for r in report['entryEvidence']['rows'] if r.get('difficultEntry'))
        self.assertEqual(entry['verification']['matrixSourceToClient'][0, 2], 0)
        self.assertEqual(matrix[0, 2], 0)

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
