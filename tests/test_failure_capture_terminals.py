"""Real runtime terminal branches with synthetic providers and a no-IO recorder."""
import sys
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
ROOT = Path(__file__).parents[1]
sys.path[:0] = [str(ROOT/'app'), str(ROOT/'experiments/qt_quick')]
import test_client_live_runtime as fixtures

class FailureCaptureTerminalTests(unittest.TestCase):
    setUp = fixtures.LiveRuntimeContracts.setUp
    dispatch = fixtures.LiveRuntimeContracts.dispatch

    def enable_evidence(self, mode):
        self.dispatch('end_round')
        self.assertTrue(self.dispatch('set_context', mode=mode)['success'])
        self.assertTrue(self.dispatch('start_round')['success'])
        self.runtime._evidence_kind = 'live-client-capture'  # Synthetic fixture only, exercises legal-guard branch.
        self.capture.generation = 2
        self.capture.is_current = lambda *a, **k: True
        self.runtime._engine.memory_store = SimpleNamespace(root=Path('/synthetic-no-io'))
        self.runtime._failure_samples.automatic_enabled = True
        self.calls = []
        def record(**kw):
            self.assertFalse(self.runtime._lock._is_owned(), 'image IO must not own runtime state lock')
            self.calls.append(kw)
            return dict(saved=False, missingReason='mock-no-file-io')
        self.runtime._failure_samples.record_automatic = record

    def test_identity_terminal_reuses_this_capture_without_new_acquisition(self):
        for mode in ('nightmare', 'difficult'):
            with self.subTest(mode=mode):
                self.enable_evidence(mode)
                self.runtime._engine.rank = lambda *a, **k: []
                before = self.capture.count
                result = self.dispatch('capture_now')
                self.assertEqual(result['reason'], 'identity-not-uniquely-verified', result)
                self.assertFalse(result['success'])
                self.assertEqual(self.capture.count, before+1)
                self.assertTrue(self.calls, 'terminal failure has no evidence decision')
                row = self.calls[0]
                self.assertIsNotNone(row['frame'])
                self.assertEqual(row['reason'], result['reason'])
                self.assertEqual(row['diagnostic']['attemptId'], result['mapAttempt']['attemptId'])
                self.assertEqual(row['diagnostic']['mode'], mode)
                self.dispatch('end_round')

    def test_floor_unreadable_keeps_result_and_records_only_current_frame(self):
        self.enable_evidence('nightmare')
        with patch('floor_control_region.locate_vertical_floor_controls', return_value=dict(selectedFloor=None)), \
                patch('recognition_diagnostics.control_band', return_value=(2, 2, 4, 4)):
            result = self.dispatch('capture_now')
        self.assertEqual(result['reason'], 'current-floor-unavailable')
        self.assertFalse(result['success'])
        self.assertTrue(self.calls)
        self.assertIsNotNone(self.calls[0]['frame'])
        self.assertEqual(self.calls[0]['reason'], 'current-floor-unavailable')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]['diagnostic']['sampleRole'], 'full-client-frame')
        self.assertNotIn('crop_bounds', self.calls[0])
        self.assertNotIn('crop_provenance', self.calls[0])

    def test_post_capture_selection_pending_is_not_the_pre_capture_exit(self):
        self.enable_evidence('nightmare')
        self.runtime._selection_pending = True
        result = self.dispatch('capture_now')
        self.assertEqual(result['reason'], 'candidate-awaiting-confirmation')
        self.assertTrue(self.calls)
        self.assertIsNotNone(self.calls[0]['frame'])
        self.calls.clear()
        before = self.capture.count
        result = self.runtime._solve_retained('confirm_route', {}, None)
        self.assertEqual(result['reason'], 'candidate-awaiting-confirmation')
        self.assertEqual(self.capture.count, before)
        self.assertFalse(self.calls, 'pre-capture exit must not silently sample cached image')

    def test_blocked_sample_does_not_block_snapshot_or_invalidation(self):
        import threading
        self.enable_evidence('nightmare')
        entered, release, observed = threading.Event(), threading.Event(), threading.Event()
        rechecks = []
        def blocked(**kw):
            self.assertFalse(self.runtime._lock._is_owned())
            entered.set()
            if not release.wait(3):
                raise AssertionError('test release timeout')
            rechecks.append(kw['current']())
            return dict(saved=False, missingReason='capture-not-current')
        self.runtime._failure_samples.record_automatic = blocked
        result = []
        with patch('floor_control_region.locate_vertical_floor_controls', return_value=dict(selectedFloor=None)), \
                patch('recognition_diagnostics.control_band', return_value=(2, 2, 4, 4)):
            worker = threading.Thread(target=lambda:result.append(self.dispatch('capture_now')))
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                def inspect_and_invalidate():
                    self.runtime.snapshot()
                    self.runtime.invalidate('map-closed')
                    observed.set()
                inspector = threading.Thread(target=inspect_and_invalidate)
                inspector.start()
                self.assertTrue(observed.wait(1), 'diagnostic IO blocked cancellation/snapshot')
            finally:
                release.set()
                worker.join(3)
                if 'inspector' in locals(): inspector.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(rechecks, [False])
        self.assertFalse(result[0]['success'])

    def test_formerly_locked_terminal_writers_allow_cancellation(self):
        import threading
        for family in ('unsupported-floor', 'manual-group', 'recovery-exhausted'):
            with self.subTest(family=family):
                self.enable_evidence('nightmare')
                self.assertTrue(self.runtime._ensure_round_active({}, None)['success'])
                entered, release, inspected = threading.Event(), threading.Event(), threading.Event()
                rechecks = []
                def record(**kw):
                    self.assertFalse(self.runtime._lock._is_owned(), family)
                    entered.set()
                    if not release.wait(3): raise AssertionError('release timeout')
                    rechecks.append(kw['current']())
                    return dict(saved=False, missingReason='capture-not-current')
                self.runtime._failure_samples.record_automatic = record
                payload = {}
                if family == 'unsupported-floor':
                    self.runtime._engine.identity_floors = ('2',)
                    invoke = lambda:self.dispatch('capture_now')
                elif family == 'manual-group':
                    engine = self.runtime._engine
                    scope, intent = self.runtime._sibling_scope(), self.runtime._group_intent_generation
                    invoke = lambda:self.runtime._capture_group_choice_observation(
                        'select_group_member', payload, None, engine, scope, intent, '1')
                else:
                    self.runtime._monotonic = lambda: 100.
                    self.assertTrue(self.runtime._arm_map_open_recovery(None, self.runtime._epoch,
                        dict(_diagnosticOpeningId='a'*32, inputSequence=1)))
                    ticket = self.runtime._map_recovery
                    ticket['remaining'], ticket['nextAt'] = 1, 99.
                    invoke = lambda:self.runtime._recover_map_open(dict(recoveryId=ticket['id']), None)
                selected = 1 if family == 'unsupported-floor' else None
                with patch('floor_control_region.locate_vertical_floor_controls', return_value=(
                        fixtures.synthetic_read(selected=selected) if selected is not None else dict(selectedFloor=None))), \
                        patch('map_interface_observation.detect_map_interface', return_value=dict(detected=family!='recovery-exhausted')), \
                        patch('recognition_diagnostics.control_band', return_value=(2,2,4,4)):
                    worker = threading.Thread(target=invoke)
                    worker.start()
                    try:
                        self.assertTrue(entered.wait(2), family)
                        def cancel():
                            self.runtime.snapshot()
                            self.runtime.invalidate('map-closed')
                            inspected.set()
                        reader = threading.Thread(target=cancel)
                        reader.start()
                        self.assertTrue(inspected.wait(1), family)
                    finally:
                        release.set()
                        worker.join(3)
                        if 'reader' in locals(): reader.join(3)
                self.assertFalse(worker.is_alive())
                self.assertTrue(rechecks)
                self.assertTrue(all(value is False for value in rechecks), rechecks)
                self.runtime._engine.identity_floors = ('1','2','B1')

if __name__ == '__main__': unittest.main()
