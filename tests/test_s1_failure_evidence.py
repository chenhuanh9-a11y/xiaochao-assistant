"""Current S1 failure evidence with synthetic capture/solver, no game or file IO."""
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'app'), str(ROOT / 'experiments/qt_quick')]
import test_s1_native_candidate_preview as native
from diagnostic_privacy import safe_record
from recognition_diagnostics import bounded_recognition, s1_scan_failure


class S1FailureEvidenceTests(unittest.TestCase):
    setUp = native.NativeCandidateContracts.setUp
    start_patch = native.NativeCandidateContracts.start_patch

    def prepare_failure(self):
        self.events, self.samples = [], []
        self.runtime.set_event_callback(self.events.append)
        self.runtime.set_automatic_failure_sampling_enabled(True)
        self.registry.engine.memory_store = SimpleNamespace(root=Path('/synthetic-no-io'))
        self.fit = dict(success=False, reason='initial-structure-current-geometry-failed')
        def record(**values):
            self.assertFalse(self.runtime._lock._is_owned())
            self.samples.append(dict(values, currentAtWrite=values['current']()))
            return dict(saved=False, missingReason='mock-no-file-io')
        self.runtime._failure_samples.record_automatic = record
        self.runtime.dispatch('start_round')

    def test_no_material_reuses_origin_capture_and_reports_unknown_fit_reasons(self):
        self.prepare_failure()
        before = self.capture.count
        result = self.runtime.dispatch('capture_now')
        self.assertEqual(result['reason'], 'no-current-supported-candidates')
        self.assertEqual(self.capture.count, before + 1)
        self.assertEqual(len(self.samples), 1)
        sample = self.samples[0]
        self.assertIsNotNone(sample['frame'])
        self.assertTrue(sample['currentAtWrite'])
        self.assertEqual(sample['diagnostic']['attemptId'], result['mapAttempt']['attemptId'])
        self.assertEqual(sample['diagnostic']['season'], 's1')
        diagnostic = next(row['recognitionDiagnostics'] for row in self.events
            if row['event'] == 'recognition-diagnostic' and row['reason'] == result['reason'])
        summary = diagnostic['structureScanFailure']
        self.assertEqual(diagnostic['stage'], 's1-scan-no-material')
        self.assertEqual(summary['checkedCount'], 2)
        self.assertEqual(summary['supportedCount'], 0)
        self.assertEqual(summary['reasonCounts'], [dict(reason=self.fit['reason'], count=2)])
        self.assertTrue(all(row['outcome'] == 'unknown' for row in summary['outcomes']))
        self.assertEqual(diagnostic['inputs']['attemptId'], result['mapAttempt']['attemptId'])
        self.assertFalse(self.runtime.snapshot()['identityLocked'])

    def test_disabled_sampling_never_writes_or_recaptures(self):
        self.prepare_failure()
        self.runtime.set_automatic_failure_sampling_enabled(False)
        # Use the real recorder's disabled branch, not a replacement writer.
        from client_failure_samples import FailureSampleRecorder
        recorder = FailureSampleRecorder()
        self.runtime._failure_samples = recorder
        result = self.runtime.dispatch('capture_now')
        self.assertFalse(result['success'])
        self.assertEqual(self.capture.count, 1)
        self.assertEqual(recorder.automatic_saved_count, 0)
        self.assertTrue(any(row['event'] == 'automatic-failure-sample' and
            row['failureSample']['missingReason'] == 'automatic-sampling-disabled' for row in self.events))

    def test_cancelled_observations_do_not_sample_as_no_material(self):
        self.prepare_failure()
        terminal = Mock()
        self.runtime._terminal_failure_sample = terminal
        self.registry.engine.qt_s1_structure_scan = lambda frame, **kw: dict(status='cancelled')
        self.assertTrue(self.runtime.dispatch('capture_now')['stale'])
        terminal.assert_not_called()
        self.assertFalse(any(row['event'] == 'recognition-diagnostic' for row in self.events))

    def test_success_does_not_sample_as_no_material(self):
        self.events = []
        self.runtime.set_event_callback(self.events.append)
        terminal = Mock()
        self.runtime._terminal_failure_sample = terminal
        self.runtime.dispatch('start_round')
        self.assertTrue(self.runtime.dispatch('capture_now')['success'])
        terminal.assert_not_called()
        self.assertFalse(any(row['event'] == 'recognition-diagnostic' and
            row['recognitionDiagnostics']['stage'] == 's1-scan-no-material' for row in self.events))

    def test_missing_pose_on_same_floor_is_not_reported_as_floor_change(self):
        self.prepare_failure()
        self.runtime.dispatch('capture_now')
        self.events.clear()
        result = self.runtime.dispatch('observe_candidate_floor')
        self.assertFalse(result['success'])
        reasons = [row['reason'] for row in self.events if row['event'] == 'render-revoked']
        self.assertIn('candidate-pose-unavailable', reasons)
        self.assertNotIn('candidate-floor-changed', reasons)

    def test_sampling_io_does_not_hold_state_lock_and_close_invalidates_image(self):
        self.prepare_failure()
        entered, release, inspected = threading.Event(), threading.Event(), threading.Event()
        current = []
        def record(**values):
            self.assertFalse(self.runtime._lock._is_owned())
            entered.set()
            self.assertTrue(release.wait(3))
            current.append(values['current']())
            return dict(saved=False, missingReason='capture-not-current')
        self.runtime._failure_samples.record_automatic = record
        result = []
        worker = threading.Thread(target=lambda: result.append(self.runtime.dispatch('capture_now')))
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            def close():
                self.runtime.snapshot()
                self.runtime.invalidate('map-closed')
                inspected.set()
            inspector = threading.Thread(target=close)
            inspector.start()
            self.assertTrue(inspected.wait(1))
        finally:
            release.set()
            worker.join(3)
            if 'inspector' in locals(): inspector.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(current, [False])
        self.assertIsNone(self.runtime.render_payload(''))


class S1FailurePrivacyTests(unittest.TestCase):
    def test_counts_preferred_reason_and_omissions_survive_both_bounded_privacy_passes(self):
        outcomes = [dict(mapId='107%03d' % index, outcome='unknown',
            reason='initial-structure-query-unavailable') for index in range(1, 16)]
        outcomes[0]['reason'] = 'OSError: PRIVATE_NAME C:/private/file'
        report = dict(status='budget-unresolved', scanComplete=False, checkedMapIds=list(range(15)),
            supportedMapIds=[], unscannedMapIds=[16], elapsedSeconds=.8, outcomes=outcomes,
            imageSource='PRIVATE_NAME', frame=object())
        value = dict(stage='s1-scan-no-material', reason='no-current-supported-candidates',
            structureScanFailure=s1_scan_failure(report, '107015'))
        result = safe_record(bounded_recognition(bounded_recognition(value)))
        encoded = json.dumps(result)
        self.assertLessEqual(len(encoded.encode()), 2048)
        self.assertNotIn('PRIVATE_NAME', encoded)
        summary = result['structureScanFailure']
        self.assertEqual(summary['status'], 'budget-unresolved')
        self.assertEqual(summary['checkedCount'], 15)
        self.assertEqual(summary['omittedOutcomeCount'], 9)
        self.assertEqual(summary['preferred']['mapId'], '107015')
        self.assertEqual(summary['outcomes'][0]['mapId'], '107015')
        self.assertEqual(summary['reasonCounts'], [dict(reason='initial-structure-query-unavailable', count=14),
            dict(reason='unknown-operation-failure', count=1)])


if __name__ == '__main__':
    unittest.main()
