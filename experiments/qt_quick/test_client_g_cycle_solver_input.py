"""Synthetic same-frame evidence contracts; no game or display accuracy claim."""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import numpy as np
from g_cycle_observation import emit, observe_inputs, observing
from client_g_cycle_evidence import _Trace
import test_client_g_cycle_evidence as common


class SolverInputEvidence(unittest.TestCase):
    setUp = common.GCycleIntegration.setUp

    def fit(self, frame, *args, **kwargs):
        emit('solver-input', frame)
        emit('structure-input', frame, dict(observationBounds=(10, 20, 40, 45)))
        emit('structure-input', frame, dict(observationBounds=(100, 200, 150, 250)))
        emit('fit-evidence', frame, dict(schemaVersion=1, mode='nightmare',
            resourceKey='a'*64, floor='1', observations=[],
            priorBinding=dict(mapId='107014', sourceFloor='-2', targetFloor='-3',
                              indexSha256='b'*64),
            password='must-not-escape', arbitraryArray=np.zeros((20, 20))))
        return dict(self.f.fit)

    def run_fit(self):
        with patch.object(common.fixtures._AlignmentHost, 'fit', side_effect=self.fit):
            result = self.f.dispatch('map', openIntent=True, inputSequence=1)
        self.assertTrue(result['success'], result)
        return self.recorder.rows[-1]

    def test_default_stays_crop_and_records_both_observations(self):
        roi, meta = self.run_fit()
        self.assertEqual(roi.shape, (50, 50, 3))
        self.assertEqual(meta['input_scope'], 'algorithm-roi')
        self.assertEqual(meta['observation_bounds'], [[10, 20, 40, 45], [100, 200, 150, 250]])
        self.assertEqual(meta['fit_evidence']['resourceKey'], 'a'*64)
        self.assertNotIn('must-not-escape', json.dumps(meta))
        self.assertNotIn('arbitraryArray', json.dumps(meta))

    def test_full_client_is_explicit_exact_existing_solver_input(self):
        self.recorder.full_input = True
        self.f.capture.frame[300:310, 400:410] = (17, 83, 201)
        roi, meta = self.run_fit()
        np.testing.assert_array_equal(roi, self.f.capture.frame)
        self.assertEqual(meta['sample_role'], 'solver-client-input')
        self.assertEqual(meta['roi_provenance'], 'existing-solver-client-frame')
        self.assertEqual(meta['crop_bounds'], [0, 0, 1280, 720])
        self.assertEqual(meta['full_size'], [1280, 720])
        self.assertEqual(self.f.capture.count, 1, 'evidence must not capture a second frame')

    def test_full_mode_does_not_accept_foreign_same_size_frame_or_evidence(self):
        self.recorder.full_input = True
        def foreign(frame, *args, **kwargs):
            other = np.full_like(frame, 233)
            emit('solver-input', other)
            emit('structure-input', other, dict(observationBounds=(1, 2, 30, 40)))
            emit('fit-evidence', other, dict(resourceKey='f'*64))
            return dict(self.f.fit)
        with patch.object(common.fixtures._AlignmentHost, 'fit', side_effect=foreign):
            result = self.f.dispatch('map', openIntent=True, inputSequence=1)
        self.assertTrue(result['success'])
        roi, meta = self.recorder.rows[-1]
        self.assertNotEqual(meta['sample_role'], 'solver-client-input')
        self.assertEqual(meta['observation_bounds'], [])
        self.assertEqual(meta['fit_evidence'], {})
        if roi is not None:
            self.assertFalse(np.any(roi == 233))

    def test_full_input_over_limit_is_explicit_missing_not_resized_or_crop(self):
        self.recorder.full_input = True
        with patch('client_g_cycle_evidence.MAX_CROP_BYTES', 20):
            roi, meta = self.run_fit()
        self.assertIsNone(roi)
        self.assertEqual(meta['missing_reason'], 'roi-byte-limit')
        self.assertEqual(meta['sample_role'], 'solver-client-input')
        self.assertNotIn('crop_bounds', meta)

    def test_next_capture_clears_old_fit_and_observation_metadata(self):
        trace = _Trace(self.f.runtime, self.recorder, 'map', {})
        self.f.capture.enable()
        captured = self.f.capture.capture()
        trace('capture', captured, None, self.f.runtime._failure_capture_scope(), 'a'*32)
        trace('structure-input', captured.frame_bgr, dict(observationBounds=(10, 20, 40, 45)))
        trace('fit-evidence', captured.frame_bgr, dict(resourceKey='a'*64))
        trace('capture-start')
        self.assertEqual(trace.observation_bounds, [])
        self.assertEqual(trace.fit_evidence, {})
        self.assertIsNone(trace.roi)

    def test_observation_list_is_bounded(self):
        trace = _Trace(self.f.runtime, self.recorder, 'map', {})
        self.f.capture.enable()
        captured = self.f.capture.capture()
        trace('capture', captured, None, self.f.runtime._failure_capture_scope(), 'a'*32)
        for index in range(25):
            trace('structure-input', captured.frame_bgr,
                  dict(observationBounds=(index, 20, index+30, 45)))
        self.assertEqual(len(trace.observation_bounds), 16)
        self.assertEqual(trace.omitted_observation_count, 9)

    def test_actual_host_emits_solver_frame_before_failed_segmentation(self):
        from client_live_runtime import _AlignmentHost
        host = _AlignmentHost(self.f.engine)
        frame = self.f.capture.frame
        rows = []
        with observe_inputs(lambda *args: rows.append(args)):
            host.prepare_structure_scan(frame, (0, 0, 1280, 720))
        self.assertEqual(rows[0][0], 'solver-input')
        self.assertIs(rows[0][1], frame)

    def test_explicit_full_flag_requires_test_mode_without_starting_gui(self):
        root = Path(__file__).resolve().parents[2]
        result = subprocess.run([sys.executable, str(root/'experiments/qt_quick/client.py'),
                                 '--g-cycle-full-input'], cwd=root,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn('requires --g-cycle-evidence', result.stderr)

    def test_observing_is_dispatch_scoped(self):
        self.assertFalse(observing())
        with observe_inputs(lambda *args: None):
            self.assertTrue(observing())
        self.assertFalse(observing())


class FormalLockMetadata(unittest.TestCase):
    def test_formal_s1_lock_uses_public_identity_not_legacy_field(self):
        from test_s1_formal_round_lock import FormalRoundLockContracts
        fx = FormalRoundLockContracts()
        fx.setUp()
        self.addCleanup(fx.doCleanups)
        pool, _ = fx.locked()
        self.assertEqual(fx.runtime._durable_map_id, '')
        trace = _Trace(fx.runtime, common._Recorder(), 'map', {})
        state = trace._state()
        self.assertTrue(state['locked'])
        self.assertEqual(state['map'], pool.locked_id)
        self.assertEqual(state['locked'], fx.runtime.snapshot()['identityLocked'])

    def test_opt_in_collects_current_scan_evidence_without_unlocking_or_auto_samples(self):
        from test_s1_formal_round_lock import FormalRoundLockContracts
        fx = FormalRoundLockContracts()
        fx.setUp()
        self.addCleanup(fx.doCleanups)
        pool, _ = fx.locked()
        fx.runtime.set_automatic_failure_sampling_enabled(False)
        recorder = common._Recorder()
        recorder.full_input = True
        fx.runtime.g_cycle_evidence = recorder
        original = fx.registry.engine.qt_s1_structure_scan
        options = []
        def scan(frame, **kwargs):
            options.append(dict(kwargs))
            emit('solver-input', frame)
            # The fixture bypasses client_providers, which normally consumes
            # this option before invoking StructureCandidateScanner.scan.
            kwargs.pop('collect_fit_diagnostics', None)
            report = original(frame, **kwargs)
            report['fitEvidence'] = dict(schemaVersion=1, resourceKey='c'*64,
                observations=[dict(mapId='107014', floor='-1', success=True)])
            return report
        fx.registry.engine.qt_s1_structure_scan = scan
        fx.runtime.dispatch('close_map')
        result = fx.runtime.dispatch('map', dict(openIntent=True, inputSequence=1))
        fx.idle()
        self.assertTrue(result['success'], result)
        self.assertTrue(options[-1]['collect_fit_diagnostics'])
        self.assertEqual(tuple(options[-1]['planned_map_ids']), (pool.locked_id,))
        roi, meta = recorder.rows[-1]
        self.assertIsNotNone(roi)
        self.assertTrue(meta['locked'])
        self.assertEqual(meta['fit_evidence']['resourceKey'], 'c'*64)
        self.assertFalse(fx.runtime._failure_samples.automatic_enabled)
        # Turning evidence off must restore the previous non-collecting path.
        recorder.accepting = False
        fx.runtime.dispatch('close_map')
        self.assertTrue(fx.runtime.dispatch('map', dict(openIntent=True, inputSequence=2))['success'])
        fx.idle()
        self.assertNotIn('collect_fit_diagnostics', options[-1])
        self.assertTrue(fx.runtime.snapshot()['identityLocked'])


if __name__ == '__main__':
    unittest.main()
