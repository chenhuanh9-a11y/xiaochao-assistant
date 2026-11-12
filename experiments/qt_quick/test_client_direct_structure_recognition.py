"""Imported scanner wiring and actual A/B computation, without game claims."""
from contextlib import ExitStack
import copy
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np

from entry_evidence_work import FrameSnapshot
from fast_structure_alignment import prepare
from structure_pose_solver import PreparedStructurePoseSolver
from structure_identity_index import DirectoryEntry, StructureIdentityIndex
from structure_identity_observation import observe_structure_identity
from structure_identity_recognition import StructureIdentityRecognition
from structure_identity_resources import footprint_anchors
from test_structure_identity_observation import pixel_fixture
import test_client_native_base_provider as native_fixture


class DirectStructureRecognitionProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        native_fixture.NativeBaseProviderTests.setUpClass()
        cls.addClassCleanup(native_fixture.NativeBaseProviderTests.doClassCleanups)

    def setUp(self):
        self.source = native_fixture.NativeBaseProviderTests('runTest')
        self.source.setUp()
        self.addCleanup(self.source.doCleanups)
        self.source.activate()
        self.engine = self.source.factories.get_engine('nightmare')
        self.direct = self.engine.qt_direct_structure_recognition
        self.addCleanup(self.direct.close)
        _, frame = pixel_fixture()
        self.snapshot = FrameSnapshot(frame)

    def finish(self, run):
        if run.thread is not None:
            run.thread.join(5)
            self.assertFalse(run.thread.is_alive(), 'bounded recognition worker did not finish')
        return run.collect()

    def test_factory_prepares_complete_declared_domain_and_actual_numerics_do_no_io(self):
        self.assertEqual(self.direct.preparation['status'], 'prepared', self.direct.preparation)
        self.assertEqual(self.direct.preparation['domainCount'], 3)
        self.assertEqual(set(self.direct.indexes), {'B1', '1', '2'})
        def forbidden(*args, **kwargs):
            raise AssertionError('direct-numerics-touched-resource-io')
        with ExitStack() as stack:
            for owner, name in ((Path, 'stat'), (Path, 'resolve'), (Path, 'read_bytes'),
                    (self.engine.qt_fast_structure_source, 'read'),
                    (self.engine.qt_shared_provider, '_assert_current')):
                stack.enter_context(patch.object(owner, name, side_effect=forbidden))
            report = self.finish(self.direct.start(self.snapshot, '1'))
        self.assertNotIn(report['status'], ('failed', 'unavailable', 'busy', 'pending'), report)
        self.assertEqual(report['frameKey'], self.snapshot.frame_key)
        self.assertEqual(report['shapeSnapshotId'], self.snapshot.sha256)
        self.assertEqual(report['floor'], '1')
        self.assertGreaterEqual(report['measuredChainCount'], 9)
        self.assertGreater(report['extractionMs'], 0)
        self.assertFalse(report['authorizesIdentity'])
        self.assertFalse(report['lock'])
        self.assertIn('screen-colour-boundary-versus-static-alpha-footprint-unverified',
                      report['qualificationGaps'])
        self.assertIn('runtime-identity-qualification-required', report['qualificationGaps'])

    def test_synchronous_typed_computation_uses_no_thread_or_resource_io_and_drives_report(self):
        def forbidden(*args, **kwargs):
            raise AssertionError('synchronous-computation-created-thread-or-touched-resource-io')
        with ExitStack() as stack:
            for owner, name in ((threading, 'Thread'), (Path, 'stat'), (Path, 'read_bytes'),
                    (Path, 'resolve'), (self.engine.qt_fast_structure_source, 'read'),
                    (self.engine.qt_shared_provider, '_assert_current')):
                stack.enter_context(patch.object(owner, name, side_effect=forbidden))
            computation = self.direct.compute(self.snapshot, '1')
        self.assertIsInstance(computation.recognition, StructureIdentityRecognition)
        self.assertEqual(computation.recognition.frame_key, self.snapshot.frame_key)
        self.assertEqual(computation.observation.frame_key, self.snapshot.frame_key)
        self.assertEqual(computation.shape_snapshot_id, self.snapshot.sha256)
        self.assertEqual(computation.binding.component_ref, self.engine.qt_native_base.component_ref)
        self.assertEqual(computation.binding.resource_revision, self.engine.qt_resource_revision)
        self.assertEqual(computation.binding.engine_identity, id(self.engine))
        self.assertEqual(computation.binding.provider_identity, id(self.engine.qt_shared_provider))
        self.assertEqual(computation.observation.constraint_kind, 'full-chain')
        self.assertGreater(computation.observation.measured_chain_count, 0)
        self.assertFalse(computation.recognition.authorizesIdentity)
        expected = self.direct.report_computation(self.snapshot, '1', computation)
        # The actual diagnostic worker now only projects this actual typed
        # computation; no second recognition algorithm is kept in the thread.
        with patch.object(self.direct, 'compute', return_value=computation) as compute:
            report = self.finish(self.direct.start(self.snapshot, '1'))
        self.assertEqual(report, expected)
        self.assertEqual(compute.call_count, 1)

    def test_synchronous_cancel_deadline_and_stale_owner_cannot_return_live_proposals(self):
        for options, reason in ((dict(cancelled=lambda: True), 'cancelled-or-stale'),
                (dict(deadline=time.perf_counter()-1), 'time-budget-exhausted')):
            computation = self.direct.compute(self.snapshot, '1', **options)
            self.assertEqual(computation.recognition.status, 'cancelled')
            self.assertEqual(computation.recognition.pose_proposals, ())
            self.assertEqual(computation.cancellation_reason, reason)
            self.assertFalse(computation.recognition.lock)
        self.source.factories.invalidate()
        with patch('client_structure_recognition.observe_structure_identity',
                   side_effect=AssertionError('stale owner entered synchronous numerics')):
            with self.assertRaisesRegex(ValueError, 'resource-context-superseded'):
                self.direct.compute(self.snapshot, '1')

    def test_resource_switch_before_worker_input_capture_is_cancelled(self):
        original = self.direct.compute
        def switched(*args, **options):
            self.source.factories.invalidate()
            return original(*args, **options)
        with patch.object(self.direct, 'compute', side_effect=switched), \
             patch('client_structure_recognition.observe_structure_identity',
                   side_effect=AssertionError('superseded worker entered numerics')):
            report = self.finish(self.direct.start(self.snapshot, '1'))
        self.assertEqual(report['status'], 'cancelled', report)
        self.assertEqual(report['poseProposals'], [])
        self.assertFalse(report['lock'])

    def test_close_revokes_synchronous_computation_without_owning_its_thread(self):
        def observe(snapshot, **options):
            result = observe_structure_identity(snapshot, **options)
            self.direct.close()
            return result
        with patch('client_structure_recognition.observe_structure_identity', side_effect=observe):
            computation = self.direct.compute(self.snapshot, '1')
        self.assertEqual(computation.recognition.status, 'cancelled')
        self.assertEqual(computation.recognition.pose_proposals, ())
        self.assertEqual(computation.cancellation_reason, 'cancelled-or-stale')

    def _install_pixel_index(self):
        # Explicitly replace this small fixture's static index with synthetic
        # pixel geometry. The actual start -> observe -> A/B -> report path runs.
        mask, _ = pixel_fixture()
        features = footprint_anchors(prepare(mask))
        key = self.direct.indexes['1'].domain[0]
        entry = DirectoryEntry(key, 'synthetic-report-race-fixture', features,
            frozenset(row.feature_id for row in features), True, True)
        self.direct.indexes['1'] = StructureIdentityIndex((key,), (entry,), directory_complete=True)

    def _assert_revoked_during_report(self, action, *, deadline=False):
        self._install_pixel_index()
        entered, release = threading.Event(), threading.Event()
        built = []
        original = self.direct.report_computation
        def blocked(*args, **options):
            report = original(*args, **options)
            built.append(report)
            entered.set()
            if not release.wait(5):
                raise AssertionError('report revocation test did not release worker')
            return report
        clock = [time.perf_counter()]
        with ExitStack() as stack:
            stack.enter_context(patch.object(self.direct, 'report_computation', side_effect=blocked))
            if deadline:
                stack.enter_context(patch('client_structure_recognition.time.perf_counter',
                                          side_effect=lambda: clock[0]))
            run = self.direct.start(self.snapshot, '1',
                **(dict(deadline=clock[0]+1.) if deadline else {}))
            try:
                self.assertTrue(entered.wait(4), 'actual numerical worker did not build report')
                self.assertTrue(built[0]['poseProposals'], built[0])
                if deadline:
                    clock[0] += 2.
                else:
                    action(run)
            finally:
                release.set()
            report = self.finish(run)
        self.assertEqual(report['status'], 'cancelled')
        self.assertEqual(report['poseProposals'], [])
        self.assertFalse(report['lock'])

    def test_cancel_during_report_construction_revokes_actual_start_result(self):
        self._assert_revoked_during_report(lambda run: run.cancel())

    def test_close_during_report_construction_revokes_actual_start_result(self):
        self._assert_revoked_during_report(lambda run: self.direct.close())

    def test_resource_switch_during_report_construction_revokes_actual_start_result(self):
        self._assert_revoked_during_report(lambda run: self.source.factories.invalidate())

    def test_deadline_during_report_construction_revokes_actual_start_result(self):
        self._assert_revoked_during_report(None, deadline=True)

    def test_cancel_during_collection_deepcopy_revokes_already_published_result(self):
        self._install_pixel_index()
        cancelled = threading.Event()
        run = self.direct.start(self.snapshot, '1', cancelled=cancelled.is_set)
        run.thread.join(5)
        self.assertFalse(run.thread.is_alive())
        self.assertTrue(run.report['poseProposals'], run.report['status'])
        original = copy.deepcopy
        def copying(value, *args, **options):
            result = original(value, *args, **options)
            if value is run.report:
                cancelled.set()
            return result
        with patch('client_structure_recognition.copy.deepcopy', side_effect=copying):
            report = run.collect()
        self.assertEqual(report['status'], 'cancelled')
        self.assertEqual(report['poseProposals'], [])

    def test_close_during_collection_deepcopy_stays_nonblocking_and_revokes_result(self):
        self._install_pixel_index()
        run = self.direct.start(self.snapshot, '1')
        run.thread.join(5)
        self.assertFalse(run.thread.is_alive())
        self.assertTrue(run.report['poseProposals'], run.report['status'])
        entered, release = threading.Event(), threading.Event()
        results, errors = [], []
        original = copy.deepcopy
        def copying(value, *args, **options):
            if value is run.report:
                entered.set()
                if not release.wait(3):
                    raise AssertionError('close blocked on the collection deepcopy lock')
            return original(value, *args, **options)
        def collect():
            try:
                results.append(run.collect())
            except BaseException as error:
                errors.append(error)
        with patch('client_structure_recognition.copy.deepcopy', side_effect=copying):
            collector = threading.Thread(target=collect)
            collector.start()
            try:
                self.assertTrue(entered.wait(2))
                self.direct.close()
                self.assertTrue(collector.is_alive(), 'close waited for report copying')
            finally:
                release.set()
                collector.join(5)
        self.assertFalse(collector.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['status'], 'cancelled')
        self.assertEqual(results[0]['poseProposals'], [])

    def test_actual_scanner_runs_direct_before_fit_and_collects_on_calling_thread(self):
        extracted = threading.Event()
        owner = threading.get_ident()
        callback_threads = []
        observed_snapshots = []
        original_fit = PreparedStructurePoseSolver.fit

        def observe(snapshot, **options):
            observed_snapshots.append(snapshot)
            try:
                return observe_structure_identity(snapshot, **options)
            finally:
                extracted.set()

        def fit(host, *args, **options):
            self.assertTrue(extracted.wait(3), 'direct observation waited for candidate fit')
            # Finish numerical retrieval before the original scanner completes,
            # without replacing the scanner or the direct recognition algorithm.
            active = self.direct._active
            self.finish(active)
            return original_fit(host, *args, **options)

        with patch('client_structure_recognition.observe_structure_identity', side_effect=observe), \
             patch.object(PreparedStructurePoseSolver, 'fit', fit):
            report = self.engine.qt_structure_scan(self.snapshot.pixels, floor='1',
                floor_source='current-frame-verified', frame_snapshot=self.snapshot,
                on_direct_recognition=lambda value: callback_threads.append(threading.get_ident()))
        self.assertEqual(observed_snapshots, [self.snapshot])
        self.assertEqual(callback_threads, [owner])
        self.assertEqual(report['expectedMapIds'], list(self.engine.pack.by_id))
        self.assertIn('entryEvidence', report)
        self.assertTrue(report['posePreparation'])
        self.assertGreaterEqual(report['posePreparation'][0]['fitMs'], 0)
        self.assertNotEqual(report['directStructureRecognition']['status'], 'pending')
        self.assertFalse(report['automaticSelectionAuthorized'])
        self.assertFalse(report['directStructureRecognition']['lock'])

    def test_cancelled_old_frame_has_no_callback_and_cannot_queue_more_work(self):
        entered, release = threading.Event(), threading.Event()

        def observe(snapshot, **options):
            entered.set()
            release.wait(3)
            return observe_structure_identity(snapshot, **options)

        with patch('client_structure_recognition.observe_structure_identity', side_effect=observe):
            old = self.direct.start(self.snapshot, '1')
            self.assertTrue(entered.wait(2))
            old.cancel()
            successor = self.direct.start(self.snapshot, '2')
            self.assertEqual(successor.collect()['status'], 'busy')
            self.assertIsNone(successor.thread)
            release.set()
            report = self.finish(old)
        self.assertEqual(report['status'], 'cancelled')
        self.assertEqual(report['poseProposals'], [])
        self.assertFalse(report['lock'])

    def test_factory_resource_invalidation_rejects_cached_index_before_numerics(self):
        # Factory context validity is installed after static engine assembly.
        first = self.finish(self.direct.start(self.snapshot, '1'))
        self.source.factories.invalidate()
        with patch('client_structure_recognition.observe_structure_identity',
                   side_effect=AssertionError('stale frame entered numerics')):
            rejected = self.direct.start(self.snapshot, '1').collect()
        self.assertEqual(rejected['status'], 'cancelled')
        self.assertEqual(rejected['reason'], 'resource-context-superseded')
        self.assertFalse(first['lock'])


if __name__ == '__main__':
    unittest.main()
