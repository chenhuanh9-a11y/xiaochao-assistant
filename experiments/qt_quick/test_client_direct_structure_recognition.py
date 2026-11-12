"""Imported scanner wiring and actual A/B computation, without game claims."""
from contextlib import ExitStack
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np

from entry_evidence_work import FrameSnapshot
from structure_pose_solver import PreparedStructurePoseSolver
from structure_identity_observation import observe_structure_identity
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
