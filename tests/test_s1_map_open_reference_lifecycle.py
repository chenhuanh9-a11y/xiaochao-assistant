"""Physical map-edge seam with real coordinator/session/runtime, synthetic IO.

No game input, Windows window, real capture, or visual latency is measured.
Unlike direct runtime reopen tests, this includes coordinator pre-invalidation.
"""
import unittest
from unittest.mock import Mock, patch

import s1_candidate_purpose as purpose
import test_client_recognition as qml
from test_s1_drag_probe_lifecycle import DragProbeContracts


class MapOpenReferenceContracts:
    candidate_purpose = purpose._NATIVE_S1_PRODUCTION_CANDIDATE
    retained_lock = True
    mode = 'nightmare'
    cleanup_session = DragProbeContracts.cleanup_session
    settle = DragProbeContracts.settle

    def setUp(self):
        DragProbeContracts.setUp(self)
        self.coordinator._bound = '123'
        # Restore the actual static-sheet consumer omitted by drag-only tests.
        del self.coordinator._sync_reference_payload
        self.runtime.dispatch('close_map')
        self.session.refreshRuntime()
        qml.APP.processEvents()
        self.assertTrue(self.coordinator.referenceData)
        self.fixture.windows.hide.reset_mock()

    def open_queued(self, sequence=1):
        pending = []
        with patch.object(self.session._pool, 'start', side_effect=pending.append):
            self.coordinator._map_input_requested(dict(sequence=sequence,
                openIntent=True, createdAt=1., hwnd=123, source='hotkey'))
        self.assertEqual(len(pending), 1)
        qml.APP.processEvents()
        return pending[0]

    def test_physical_reopen_preserves_sheet_before_worker_and_during_capture(self):
        for sequence in (1, 3, 5):
            reference = dict(self.coordinator.referenceData)
            retained = self.runtime.reference_payload()
            self.fixture.windows.hide.reset_mock()
            work = self.open_queued(sequence)
            try:
                self.assertIsNotNone(self.runtime.reference_payload())
                self.assertEqual(self.coordinator.referenceData['imageUrl'], reference['imageUrl'])
                self.assertNotIn(unittest.mock.call('reference'), self.fixture.windows.hide.call_args_list)
                self.assertFalse(self.coordinator.alignedData)
                self.assertFalse(self.runtime.snapshot()['displayAuthorized'])
                seen = []
                original = self.capture.capture
                def capture(*args, **kwargs):
                    row = self.runtime.reference_payload()
                    seen.append(None if row is None else row['token'])
                    return original(*args, **kwargs)
                with patch.object(self.capture, 'capture', side_effect=capture):
                    self.session._pool.start(work)
                    self.settle()
                work = None
                self.assertTrue(seen)
                self.assertTrue(all(token == retained['token'] for token in seen), seen)
                self.assertEqual(self.coordinator.referenceData['imageUrl'], reference['imageUrl'])
                self.assertNotIn(unittest.mock.call('reference'), self.fixture.windows.hide.call_args_list)
            finally:
                if work is not None:
                    self.session._pool.start(work)
                    self.settle()
            self.coordinator._map_input_requested(dict(sequence=sequence+1,
                openIntent=False, createdAt=1., hwnd=123, source='hotkey'))
            self.settle()

    def test_end_while_open_queued_clears_sheet_and_rejects_late_result(self):
        work = self.open_queued()
        self.session.dispatch('end_round')
        self.settle()
        self.session.refreshRuntime()
        qml.APP.processEvents()
        self.assertIsNone(self.runtime.reference_payload())
        self.assertFalse(self.coordinator.referenceData)
        self.session._pool.start(work)
        self.settle()
        self.assertIsNone(self.runtime.reference_payload())
        self.assertFalse(self.coordinator.referenceData)
        self.assertFalse(self.coordinator.alignedData)

    def test_physical_close_before_open_worker_keeps_only_historical_sheet(self):
        reference = dict(self.coordinator.referenceData)
        work = self.open_queued()
        self.coordinator._map_input_requested(dict(sequence=2, openIntent=False,
            createdAt=1., hwnd=123, source='hotkey'))
        self.settle()
        self.session._pool.start(work)
        self.settle()
        self.assertTrue(self.coordinator.referenceData)
        self.assertEqual(self.coordinator.referenceData['imageUrl'], reference['imageUrl'])
        self.assertIsNotNone(self.runtime.reference_payload())
        self.assertFalse(self.coordinator.alignedData)
        self.assertFalse(self.runtime.snapshot()['displayAuthorized'])
        self.assertFalse(self.runtime.snapshot()['mapOpen'])


class FormalLockedOpen(MapOpenReferenceContracts, unittest.TestCase):
    def test_locked_conflict_and_failed_reopen_keep_only_static_reference(self):
        from test_s1_locked_identity import evidence
        pool = self.runtime._s1_private_pool
        before = pool.snapshot()
        self.assertTrue(pool.retain_locked_identity)
        self.assertEqual(pool.locked_id, 'a')
        reference = dict(self.coordinator.referenceData)
        pool.observe(evidence(pool.remaining, bad=('a',)))
        self.assertEqual(pool.snapshot(), before)
        self.producer.unknown.add('a')
        work = self.open_queued()
        self.session._pool.start(work)
        self.settle()
        self.producer.idle()
        self.session.refreshRuntime()
        qml.APP.processEvents()
        self.assertTrue(self.runtime.snapshot()['identityLocked'])
        self.assertEqual(pool.snapshot(), before)
        self.assertEqual(self.coordinator.referenceData['imageUrl'], reference['imageUrl'])
        self.assertFalse(self.coordinator.alignedData)
        self.assertFalse(self.runtime.snapshot()['displayAuthorized'])
        self.runtime.dispatch('end_round')
        self.session.refreshRuntime()
        qml.APP.processEvents()
        self.assertIsNone(self.runtime.reference_payload())
        self.assertFalse(self.coordinator.referenceData)
        self.assertFalse(self.runtime.snapshot()['identityLocked'])


class FormalCandidateOpen(MapOpenReferenceContracts, unittest.TestCase):
    retained_lock = False


class DifficultLockedOpen(MapOpenReferenceContracts, unittest.TestCase):
    mode = 'difficult'


class DifficultEvidenceLockedOpen(MapOpenReferenceContracts, unittest.TestCase):
    # Preserve all three queued-open/close/end assertions on an active resource
    # contract, now also exercising the optional evidence dispatch wrapper.
    mode = 'difficult'

    def setUp(self):
        super().setUp()
        self.recorder = Mock(accepting=True, full_input=False)
        self.recorder.submit.return_value = dict(status='queued')
        self.runtime.g_cycle_evidence = self.recorder
        original = self.producer.registry.engine.qt_s1_structure_scan
        def scan(frame, *, collect_fit_diagnostics=False, **kwargs):
            # The fixture's direct scanner lacks the production engine adapter
            # that consumes this telemetry-only option. Keep its fit unchanged.
            self.assertTrue(collect_fit_diagnostics)
            return original(frame, **kwargs)
        self.producer.registry.engine.qt_s1_structure_scan = scan

    def test_physical_reopen_preserves_sheet_before_worker_and_during_capture(self):
        super().test_physical_reopen_preserves_sheet_before_worker_and_during_capture()
        self.assertTrue(self.recorder.submit.called)


if __name__ == '__main__':
    unittest.main()
