"""Synthetic recorder admission tests; storage doubles do not validate Win32 handles."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

ROOT = Path(__file__).parents[1]
sys.path[:0] = [str(ROOT / 'app'), str(ROOT / 'experiments/qt_quick')]
import failure_sampling
from client_failure_samples import FailureSampleRecorder


class AutomaticFailureSampleAdmissionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root / 'failures' / 'automatic-v2'
        self.directory.mkdir(parents=True)
        self.journal = self.directory / 'automatic-failures.jsonl'
        self.recorder = FailureSampleRecorder()
        self.recorder.automatic_enabled = True
        self.now = 100.0
        self.journal_writes = []
        self.frame = np.arange(20 * 30 * 3, dtype=np.uint8).reshape(20, 30, 3)

        @contextmanager
        def transaction(root, *, create=False, namespace='automatic'):
            self.assertEqual(Path(root), self.root)
            self.assertEqual(namespace, 'automatic-v2')
            yield 'ready'

        def inventory(root, *, namespace='automatic'):
            self.assertEqual(namespace, 'automatic-v2')
            rows = [(line, json.loads(line)) for line in self.journal.read_bytes().splitlines()] if self.journal.exists() else []
            return dict(status='ready', rows=rows, images=list(self.directory.glob('*.png')),
                        imageRows=[row for _, row in rows if row.get('samplePath')], directory=self.directory)

        def journal(directory, data, *, namespace='automatic'):
            self.assertEqual(Path(directory), self.directory)
            self.assertEqual(namespace, 'automatic-v2')
            self.journal_writes.append(data)
            self.journal.write_bytes(data)

        def image(root, relative, pixels, *, namespace='automatic'):
            self.assertEqual(namespace, 'automatic-v2')
            path = self.root / relative
            path.write_bytes(pixels)
            info = path.stat()
            return dict(sampleIdentity=[info.st_dev, info.st_ino], sampleBytes=len(pixels),
                        sampleSha256=hashlib.sha256(pixels).hexdigest())

        self.transaction = self.enterContext(patch.object(failure_sampling, 'automatic_storage_transaction', transaction))
        self.cleanup = self.enterContext(patch.object(failure_sampling, '_cleanup_automatic_failure_samples_locked',
                                                     return_value=dict(status='ready', complete=True)))
        self.inventory = self.enterContext(patch.object(failure_sampling, 'automatic_storage_inventory', side_effect=inventory))
        self.write_journal = self.enterContext(patch.object(failure_sampling, 'replace_automatic_journal', side_effect=journal))
        self.create_image = self.enterContext(patch.object(failure_sampling, 'create_automatic_image', side_effect=image))
        self.capacity = self.enterContext(patch.object(failure_sampling, 'ensure_automatic_capacity',
                                                      return_value=dict(status='ready', complete=True)))
        self.enterContext(patch('client_failure_samples.time.monotonic', side_effect=lambda: self.now))
        self.enterContext(patch('map_observation.structure_current_map_evidence', return_value=None))

    def record(self, **changes):
        arguments = dict(root=self.root, reason='structure-current-geometry-failed', frame=self.frame,
                         diagnostic=dict(evidenceKind='live-client-capture', mode='nightmare'), current=lambda: True,
                         crop_bounds=(2, 3, 10, 11), crop_provenance='color-component-unvalidated')
        arguments.update(changes)
        return self.recorder.record_automatic(**arguments)

    def rows(self):
        return [json.loads(line) for line in self.journal.read_bytes().splitlines()] if self.journal.exists() else []

    def test_missing_frame_does_not_consume_next_valid_image_in_either_mode(self):
        for mode in ('nightmare', 'difficult'):
            with self.subTest(mode=mode):
                diagnostic = dict(evidenceKind='live-client-capture', mode=mode)
                missing = self.record(frame=None, diagnostic=diagnostic)
                self.assertEqual(missing['missingReason'], 'legal-captured-frame-unavailable')
                self.assertFalse(missing['saved'])
                saved = self.record(diagnostic=diagnostic)
                self.assertTrue(saved['saved'], saved)
        self.assertEqual(self.recorder.automatic_saved_count, 2)

    def test_missing_crop_arguments_still_save_the_complete_frame(self):
        missing = self.record(crop_bounds=None, crop_provenance=None)
        self.assertTrue(missing['saved'], missing)
        row = next(row for row in self.rows() if row.get('samplePath'))
        decoded = cv2.imread(str(self.root / row['samplePath']))
        np.testing.assert_array_equal(decoded, self.frame)
        self.assertEqual(row['cropBounds'], [0, 0, self.frame.shape[1], self.frame.shape[0]])
        self.assertEqual(row['cropProvenance'], 'full-client-capture')
        self.assertEqual(row['diagnostic']['sampleRole'], 'full-client-frame')

    def test_s1_no_material_uses_full_frame_admission_and_cooldown(self):
        for reason in ('no-current-supported-candidates', 's1-no-current-material'):
            with self.subTest(reason=reason):
                first = self.record(reason=reason, diagnostic=dict(
                    evidenceKind='live-client-capture', mode='nightmare', season='s1'))
                self.assertTrue(first['saved'], first)
                self.assertFalse(self.record(reason=reason)['saved'])
        self.assertEqual(len(list(self.directory.glob('*.png'))), 2)

    def test_legacy_crop_arguments_are_ignored_and_full_frame_is_saved(self):
        self.recorder.policy.interval_seconds = 0
        for bounds, provenance in (((-1, 3, 10, 11), 'color-component-unvalidated'),
                                   ((2, 3, 10, 11), 'untrusted-provenance'),
                                   ((2.0, 3, 10, 11), 'color-component-unvalidated')):
            with self.subTest(bounds=bounds, provenance=provenance):
                result = self.record(crop_bounds=bounds, crop_provenance=provenance)
                self.assertTrue(result['saved'], result)
                row = self.rows()[-1]
                decoded = cv2.imread(str(self.root / row['samplePath']))
                np.testing.assert_array_equal(decoded, self.frame)
                self.assertEqual(row['cropBounds'], [0, 0, self.frame.shape[1], self.frame.shape[0]])
                self.assertEqual(row['cropProvenance'], 'full-client-capture')
        self.assertEqual(len(list(self.directory.glob('*.png'))), 3)

    def test_legacy_control_band_flags_do_not_change_saved_pixels(self):
        controls = self.record(crop_provenance='detector-control-band')
        self.assertTrue(controls['saved'], controls)
        row = next(row for row in self.rows() if row.get('samplePath'))
        decoded = cv2.imread(str(self.root / row['samplePath']))
        np.testing.assert_array_equal(decoded, self.frame)
        self.assertEqual(row['cropProvenance'], 'full-client-capture')

    def test_metadata_only_loop_is_throttled_and_keeps_specific_missing_reason(self):
        for _ in range(100):
            result = self.record(frame=None)
            self.assertEqual(result['missingReason'], 'legal-captured-frame-unavailable')
            self.assertFalse(result['saved'])
        self.assertEqual(len(self.journal_writes), 1)
        self.assertEqual(len(self.rows()), 1)
        self.assertFalse(self.recorder.policy.saved)
        self.assertEqual(self.recorder.automatic_saved_count, 0)
        self.now += 60
        self.record(frame=None)
        self.assertEqual(len(self.journal_writes), 2)

    def assert_metadata_cooldown_skips_storage_scans(self):
        first = self.record(frame=None)
        self.assertEqual(first['missingReason'], 'legal-captured-frame-unavailable')
        previous_writes = len(self.journal_writes)
        for operation in (self.cleanup, self.inventory, self.create_image, self.capacity):
            operation.reset_mock()
        for _ in range(100):
            result = self.record(frame=None)
            self.assertEqual(result['missingReason'], 'legal-captured-frame-unavailable')
            self.assertFalse(result['saved'])
        self.assertEqual(self.cleanup.call_count, 0)
        self.assertEqual(self.inventory.call_count, 0)
        self.assertEqual(len(self.journal_writes), previous_writes)
        self.create_image.assert_not_called()
        self.capacity.assert_not_called()
        self.assertFalse(list(self.directory.glob('*.png')))

    def test_missing_frame_metadata_cooldown_skips_retained_image_scans(self):
        self.assert_metadata_cooldown_skips_storage_scans()

    def test_valid_image_after_metadata_cooldown_revalidates_storage_ownership(self):
        self.assertEqual(self.record(frame=None)['missingReason'], 'legal-captured-frame-unavailable')
        self.cleanup.reset_mock()
        self.inventory.reset_mock()
        self.cleanup.return_value = dict(status='ownership-changed', complete=False)
        self.assertEqual(self.record(frame=None)['missingReason'], 'legal-captured-frame-unavailable')
        self.cleanup.assert_not_called()
        self.inventory.assert_not_called()
        result = self.record()
        self.assertFalse(result['saved'])
        self.assertEqual(result['missingReason'], 'ownership-changed')
        self.cleanup.assert_called_once()
        self.create_image.assert_not_called()
        self.assertFalse(list(self.directory.glob('*.png')))

    def test_repeated_metadata_tail_stays_bounded_and_preserves_image_ownership(self):
        self.assertTrue(self.record(crop_provenance='detector-control-band')['saved'])
        owned_row = self.rows()[0]
        for _ in range(40):
            self.now += 60
            self.record(frame=None)
        rows = self.rows()
        self.assertEqual(len(rows), 24)
        self.assertLessEqual(self.journal.stat().st_size, 256 * 1024)
        self.assertIn(owned_row, rows)
        self.assertEqual(len(list(self.directory.glob('*.png'))), 1)

    def test_metadata_admission_memory_is_bounded(self):
        self.recorder._automatic_metadata_policy.max_keys = 3
        for missing in ('capture-attempt-unavailable', 'capture-attempt-mismatch',
                        'capture-scope-changed', 'capture-not-current'):
            self.record(frame=None, diagnostic=dict(evidenceKind='live-client-capture',
                        mode='nightmare', frameUnavailableReason=missing))
        self.assertEqual(len(self.recorder._automatic_metadata_policy.saved), 3)
        self.assertFalse(self.recorder.policy.saved)
        self.assertTrue(self.record()['saved'])

    def test_only_committed_image_starts_success_cooldown(self):
        self.assertTrue(self.record()['saved'])
        self.now += 59.9
        result = self.record()
        self.assertEqual(result['missingReason'], 'sample-cooldown')
        self.assertEqual(self.recorder.automatic_saved_count, 1)
        self.now = 160
        self.assertTrue(self.record()['saved'])
        self.assertEqual(self.recorder.automatic_saved_count, 2)

    def test_encode_failure_has_failure_backoff_instead_of_success_cooldown(self):
        with patch.object(cv2, 'imencode', return_value=(False, None)):
            result = self.record()
        self.assertEqual(result['missingReason'], 'sample-encode-failed')
        self.assertFalse(self.recorder.policy.saved)
        self.now += 1
        self.assertEqual(self.record()['missingReason'], 'write-error-backoff')
        self.now += 1
        self.assertTrue(self.record()['saved'])

    def test_storage_failure_has_failure_backoff_instead_of_success_cooldown(self):
        self.cleanup.return_value = dict(status='ownership-changed', complete=False)
        result = self.record()
        self.assertEqual(result['missingReason'], 'ownership-changed')
        self.assertFalse(self.recorder.policy.saved)
        self.cleanup.return_value = dict(status='ready', complete=True)
        self.now += 1
        self.assertEqual(self.record()['missingReason'], 'write-error-backoff')
        self.now += 1
        self.assertTrue(self.record()['saved'])

    def test_storage_failures_back_off_exponentially_then_reset_on_success(self):
        self.cleanup.return_value = dict(status='ownership-changed', complete=False)
        for delay in (2, 4, 8, 16, 32, 32):
            result = self.record()
            self.assertEqual(result['missingReason'], 'ownership-changed')
            self.assertEqual(self.recorder.policy.retry_at, self.now + delay)
            calls = self.cleanup.call_count
            self.now += delay - 0.1
            self.assertEqual(self.record()['missingReason'], 'write-error-backoff')
            self.assertEqual(self.cleanup.call_count, calls)
            self.now += 0.1
        self.cleanup.return_value = dict(status='ready', complete=True)
        self.assertTrue(self.record()['saved'])
        self.assertEqual(self.recorder.policy.retry_at, 0)
        self.assertEqual(self.recorder.policy.errors, 0)

    def test_metadata_write_error_preserves_failure_backoff(self):
        self.write_journal.side_effect = OSError('synthetic disk failure')
        result = self.record(frame=None)
        self.assertEqual(result['missingReason'], 'sample-write-error')
        self.assertFalse(self.recorder.policy.saved)
        self.now += 1
        self.assertEqual(self.record()['missingReason'], 'write-error-backoff')

    def test_failed_final_journal_does_not_count_uncommitted_image_as_saved(self):
        writer = self.write_journal.side_effect

        def fail_commit(directory, data, **kwargs):
            rows = [json.loads(line) for line in data.splitlines()]
            if any(row['event'] == 'automatic-failure-sample' and row.get('samplePath') for row in rows):
                raise OSError('synthetic commit failure')
            writer(directory, data, **kwargs)

        self.write_journal.side_effect = fail_commit
        result = self.record()
        self.assertFalse(result['saved'])
        self.assertEqual(result['missingReason'], 'sample-write-error')
        self.assertEqual(self.recorder.automatic_saved_count, 0)
        self.assertFalse(self.recorder.policy.saved)
        self.assertEqual(self.rows()[0]['event'], 'automatic-failure-image-intent')
        self.assertEqual(len(list(self.directory.glob('*.png'))), 1)
        self.now += 1
        self.assertEqual(self.record()['missingReason'], 'write-error-backoff')

    def test_failed_image_write_does_not_count_intent_as_saved(self):
        self.create_image.side_effect = OSError('synthetic image write failure')
        result = self.record()
        self.assertFalse(result['saved'])
        self.assertEqual(result['missingReason'], 'sample-write-error')
        self.assertEqual(self.recorder.automatic_saved_count, 0)
        self.assertFalse(self.recorder.policy.saved)
        self.assertEqual(self.rows()[0]['event'], 'automatic-failure-image-intent')
        self.assertFalse(list(self.directory.glob('*.png')))
        self.now += 1
        self.assertEqual(self.record()['missingReason'], 'write-error-backoff')

    def assert_cancel_after_image(self, disabled):
        alive = [True]
        writer = self.create_image.side_effect

        def cancel_after_image(*args, **kwargs):
            result = writer(*args, **kwargs)
            if disabled:
                self.recorder.automatic_enabled = False
            else:
                alive[0] = False
            return result

        with patch.object(failure_sampling, 'create_automatic_image', side_effect=cancel_after_image):
            result = self.record(current=lambda: alive[0])
        self.assertFalse(result['saved'])
        self.assertTrue(result['recoveryRequired'])
        self.assertEqual(result['missingReason'], 'capture-not-current')
        self.assertEqual(self.recorder.automatic_saved_count, 0)
        self.assertFalse(self.recorder.policy.saved)
        self.assertEqual(self.rows()[-1]['event'], 'automatic-failure-image-intent')

    def test_stale_after_image_keeps_intent_without_success_cooldown(self):
        self.assert_cancel_after_image(disabled=False)

    def test_disabled_after_image_keeps_intent_without_success_cooldown(self):
        self.assert_cancel_after_image(disabled=True)

    def test_terminal_reasons_save_full_client_frames(self):
        reasons = ('identity-not-uniquely-verified', 'similar-group-pending',
                   'candidate-awaiting-confirmation', 'current-floor-unavailable',
                   'map-open-recovery-exhausted')
        for mode in ('nightmare', 'difficult'):
            for reason in reasons:
                with self.subTest(mode=mode, reason=reason):
                    diagnostic = dict(mode=mode, evidenceKind='live-client-capture')
                    saved = self.record(reason=reason, diagnostic=diagnostic,
                                        crop_bounds=(-10, -10, 1, 1), crop_provenance='invalid-old-roi')
                    self.assertTrue(saved['saved'], saved)
        self.assertEqual(self.recorder.automatic_saved_count, 10)

    def test_ineligible_reason_and_nonlive_capture_remain_rejected(self):
        for reason in ('unknown-reason', 'floor-unreadable', 'floor-unsupported'):
            with self.subTest(reason=reason):
                self.assertEqual(self.record(reason=reason)['missingReason'], 'failure-reason-not-eligible')
        self.assertFalse(self.journal.exists())
        result = self.record(diagnostic=dict(evidenceKind='fixture', mode='nightmare'))
        self.assertEqual(result['missingReason'], 'not-live-bound-game-capture')
        self.assertFalse(list(self.directory.glob('*.png')))

    def test_attempt_metadata_survives_bounded_allowlist_without_private_fields(self):
        metadata = dict(season='fixture-season', contextKey='fixture-context', attemptId=1,
                        openingId=2, inputSequence=3, requestGeneration=4, epoch=5,
                        roundId=6, captureGeneration=7, frameCapturedMonotonicAt=8.5)
        diagnostic = dict(metadata, evidenceKind='live-client-capture', mode='nightmare',
                          unlistedPrivateText='must-not-persist', frameUnavailableReason='also-not-metadata')
        self.assertTrue(self.record(diagnostic=diagnostic)['saved'])
        actual = self.rows()[-1]['diagnostic']
        for key, value in metadata.items():
            self.assertEqual(actual.get(key), value, key)
        self.assertNotIn('unlistedPrivateText', actual)
        self.assertNotIn('frameUnavailableReason', actual)
        self.assertNotIn(b'must-not-persist', self.journal.read_bytes())

    def test_missing_frame_reason_is_a_strict_enum_not_arbitrary_diagnostic_text(self):
        allowed = ('capture-attempt-unavailable', 'capture-attempt-mismatch', 'capture-scope-changed',
                   'capture-not-current', 'capture-frame-invalid', 'detector-control-band-unavailable',
                   'legal-captured-frame-unavailable')
        for missing in (*allowed, 'private arbitrary text', ['capture-not-current']):
            with self.subTest(missing=missing):
                self.now += 60
                result = self.record(frame=None, diagnostic=dict(evidenceKind='live-client-capture',
                                     mode='nightmare', frameUnavailableReason=missing))
                expected = missing if isinstance(missing, str) and missing in allowed else allowed[-1]
                self.assertEqual(result['missingReason'], expected)
                self.assertEqual(self.rows()[-1]['missingReason'], expected)
                self.assertNotIn('frameUnavailableReason', self.rows()[-1]['diagnostic'])
        self.assertNotIn(b'private arbitrary text', self.journal.read_bytes())


if __name__ == '__main__':
    unittest.main()
