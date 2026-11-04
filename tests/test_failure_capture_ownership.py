"""Synthetic frames/provider/recorder only; no native capture or image disk IO."""
import sys
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
import numpy as np
ROOT = Path(__file__).parents[1]
sys.path[:0] = [str(ROOT/'app'), str(ROOT/'experiments/qt_quick')]
from client_live_runtime import LiveRuntime

class FailureCaptureOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.captured = SimpleNamespace(frame_bgr=np.zeros((8, 10, 3), np.uint8),
            roi_client_bounds=(0, 0, 10, 8), context=SimpleNamespace(
                full_client_size=(10, 8), client_screen_bounds=(0, 0, 10, 8), dpi=96))
        self.provider = SimpleNamespace(generation=2, capture=Mock(return_value=self.captured),
            is_current=Mock(return_value=True))
        self.runtime = LiveRuntime(engine_factories={}, capture_provider=self.provider,
            evidence_kind='live-client-capture')
        self.runtime._active = True
        self.runtime._engine = SimpleNamespace(memory_store=SimpleNamespace(root=Path('/fixture')))
        self.runtime._notify = Mock()
        self.runtime._failure_samples.automatic_enabled = True
        self.calls = []
        def record(**kw):
            self.calls.append(kw)
            return dict(saved=False, missingReason=kw['diagnostic'].get('frameUnavailableReason', 'mock-no-io'))
        self.runtime._failure_samples.record_automatic = record

    def capture(self, attempt='a'*32):
        return self.runtime._capture_frame(lambda: False, attempt_id=attempt)

    def sample(self, captured=None, attempt='a'*32, **kw):
        return self.runtime._record_automatic_failure_sample('identity-not-uniquely-verified',
            captured=captured, attempt_id=attempt, **kw)

    def test_failed_new_capture_clears_old_pixels_and_controls(self):
        self.runtime._capture_frame(lambda: False)
        self.runtime._failure_control_observation = (self.captured, (0, 0, 3, 3))
        self.provider.capture.side_effect = OSError('synthetic failure')
        with self.assertRaises(OSError):
            self.runtime._capture_frame(lambda: False)
        self.assertIsNone(self.runtime._failure_capture_stamp)
        self.assertIsNone(self.runtime._failure_control_observation)

    def test_no_implicit_last_frame_fallback(self):
        self.capture()
        self.sample()
        self.assertIsNone(self.calls[-1]['frame'])
        self.assertEqual(self.calls[-1]['diagnostic']['frameUnavailableReason'], 'legal-captured-frame-unavailable')

    def test_explicit_same_attempt_is_the_only_frame_admitted(self):
        captured = self.capture()
        self.sample(captured)
        self.assertIs(self.calls[-1]['frame'], captured.frame_bgr)
        self.sample(captured, attempt='b'*32)
        self.assertIsNone(self.calls[-1]['frame'])
        self.assertEqual(self.calls[-1]['diagnostic']['frameUnavailableReason'], 'capture-attempt-mismatch')

    def test_context_or_provider_changes_cannot_relabel_old_pixels(self):
        for field, value in (('_epoch', 2), ('_round', 2), ('_season', 's1'),
                ('_mode', 'difficult'), ('_engine', object()), ('_display_generation', 42)):
            with self.subTest(field=field):
                captured = self.capture()
                old = getattr(self.runtime, field, None)
                setattr(self.runtime, field, value)
                self.sample(captured)
                self.assertIsNone(self.calls[-1]['frame'])
                self.assertEqual(self.calls[-1]['diagnostic']['frameUnavailableReason'], 'capture-scope-changed')
                setattr(self.runtime, field, old)
        captured = self.capture()
        self.provider.is_current.return_value = False
        self.sample(captured)
        self.assertIsNone(self.calls[-1]['frame'])
        self.assertEqual(self.calls[-1]['diagnostic']['frameUnavailableReason'], 'capture-not-current')

    def test_cancelled_or_new_capture_never_uses_previous_frame(self):
        captured = self.capture()
        self.sample(captured, current=lambda: False)
        self.assertIsNone(self.calls[-1]['frame'])
        newer = SimpleNamespace(**vars(self.captured))
        self.provider.capture.return_value = newer
        self.capture('b'*32)
        self.sample(captured)
        self.assertIsNone(self.calls[-1]['frame'])

    def test_invalid_full_frame_does_not_become_evidence(self):
        self.captured.roi_client_bounds = (1, 0, 10, 8)
        captured = self.capture()
        self.sample(captured)
        self.assertIsNone(self.calls[-1]['frame'])
        self.assertIsNone(self.runtime._failure_capture_stamp)

    def test_disabling_automatic_keeps_it_disabled_during_write_recheck(self):
        captured = self.capture()
        self.sample(captured)
        guard = self.calls[-1]['current']
        self.runtime.set_automatic_failure_sampling_enabled(False)
        self.assertFalse(guard())
        self.assertEqual(self.provider.capture.call_count, 1)

    def test_reenable_cannot_resurrect_pre_disable_frame(self):
        captured = self.capture()
        self.runtime.set_automatic_failure_sampling_enabled(False)
        self.runtime.set_automatic_failure_sampling_enabled(True)
        self.sample(captured)
        self.assertIsNone(self.calls[-1]['frame'])

    def test_preparation_last_probe_failure_has_no_prior_probe_pixels(self):
        import time
        from client_live_runtime import _PreparationHost, _Cancellation
        self.runtime._engine.pack = SimpleNamespace(mode='nightmare')
        self.runtime._observe = Mock(return_value=(dict(detected=False), None, dict(selectedFloor=None)))
        payload = {'_diagnosticAttemptId': 'a'*32}
        host = _PreparationHost(self.runtime, self.runtime._engine, lambda: False, payload)
        deadline = time.perf_counter()+5
        with unittest.mock.patch('map_interface_observation.map_preparation_diagnostic', return_value={}):
            host._capture_map_open_full_probe(None, 1, self.runtime._epoch, None, deadline, _Cancellation(lambda:False))
            self.assertIsNotNone(host.last_capture)
            self.provider.capture.side_effect = OSError('synthetic newest probe failed')
            with self.assertRaises(OSError):
                host._capture_map_open_full_probe(None, 1, self.runtime._epoch, None, deadline, _Cancellation(lambda:False))
        self.assertIsNone(host.last_capture)
        self.assertIsNone(self.runtime._failure_capture_stamp)
        self.assertEqual(host.observation_diagnostic(), {})
        self.sample(captured=host.last_capture)
        self.assertIsNone(self.calls[-1]['frame'])

    def test_diagnostic_formatter_failure_never_replaces_capture_result(self):
        with unittest.mock.patch('client_preparation_diagnostics.capture_metadata', side_effect=RuntimeError('formatter')):
            self.runtime._capture_diagnostic({'_diagnosticAttemptId':'a'*32}, 'capture-result',
                'frame-captured', captured=self.captured)
        self.assertFalse(self.runtime._notify.called)

    def test_refresh_metadata_does_not_create_continuous_success_log(self):
        for _ in range(50):
            self.runtime._capture_diagnostic({'_diagnosticAttemptId':'a'*32}, 'capture-result',
                'frame-captured', action='refresh_map', captured=self.captured)
            self.runtime._capture_diagnostic({'_diagnosticAttemptId':'a'*32}, 'observation-result',
                'current-frame-observed', action='refresh_map', observed=({}, None, {}, False))
        self.runtime._notify.assert_not_called()

    def test_repeated_no_image_notices_are_bounded_and_counted(self):
        with unittest.mock.patch('client_live_runtime.time.monotonic', return_value=100.):
            for _ in range(50):
                self.sample()
        self.assertEqual(self.runtime._notify.call_count, 1)
        with unittest.mock.patch('client_live_runtime.time.monotonic', return_value=161.):
            self.sample()
        self.assertEqual(self.runtime._notify.call_count, 2)
        self.assertEqual(self.runtime._notify.call_args.kwargs['failureSample']['suppressed'], 49)
        self.assertEqual(self.runtime._notify.call_args.kwargs['failureSample']['missingReason'],
            'legal-captured-frame-unavailable')

    def test_new_failure_families_do_not_collect_unrelated_regions(self):
        captured = self.capture()
        self.runtime._failure_control_observation = (captured, (2,2,4,4))
        self.sample(captured)
        self.assertEqual(len(self.calls), 1)  # identity: map only, no controls
        self.calls.clear()
        self.runtime._record_automatic_failure_sample('current-floor-unavailable',
            captured=captured, attempt_id='a'*32)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]['crop_provenance'], 'detector-control-band')
        self.assertEqual(self.calls[0]['crop_bounds'], (2,2,4,4))
        self.calls.clear()
        self.runtime._failure_control_observation = None
        self.runtime._record_automatic_failure_sample('current-floor-unavailable',
            captured=captured, attempt_id='a'*32)
        self.assertIsNone(self.calls[0]['frame'])
        self.assertEqual(self.calls[0]['diagnostic']['frameUnavailableReason'],
            'detector-control-band-unavailable')

if __name__ == '__main__': unittest.main()
