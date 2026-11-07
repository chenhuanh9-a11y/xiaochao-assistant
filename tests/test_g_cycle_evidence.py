"""Synthetic ROI tests; no GUI/game/capture and no native Windows claim."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import g_cycle_evidence as evidence


def meta(**updates):
    result = dict(identity='test', session='session-1', round=1, cycle=2,
                  opening=3, attempt=1, frame=7, outcome='success', reason='accepted',
                  crop_bounds=[2, 3, 8, 6], full_size=[100, 100],
                  timings={'solve_ms': 1.5}, solver={'confidence': 0.9})
    result.update(updates)
    return result


class RecorderTests(unittest.TestCase):
    def test_extended_fit_metadata_is_bounded_and_strips_arbitrary_strings(self):
        data = meta(input_scope='solver-client-input', observation_bounds=[[2, 3, 10, 9]],
                    omitted_observation_count=0, fit_evidence=dict(resourceKey='a'*64,
                    secret='must-not-persist', priorAttempts=[dict(matrix=np.eye(3).tolist(),
                    accepted=False, rejection=dict(reason='fast-existing-validation-rejected'))]))
        clean = evidence._metadata(data, self.roi.shape)
        self.assertEqual(clean['fit_evidence']['resourceKey'], 'a'*64)
        self.assertNotIn('must-not-persist', json.dumps(clean))
        self.assertLessEqual(len(json.dumps(clean['fit_evidence']).encode()), 6144)
        data['observation_bounds'] *= 17
        with self.assertRaises(ValueError):
            evidence._metadata(data, self.roi.shape)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'evidence'
        self.roi = np.arange(6*8*3, dtype=np.uint8).reshape(6, 8, 3)
        self.recorders = []

    def tearDown(self):
        for recorder in self.recorders:
            recorder.shutdown(wait=True)
        self.temp.cleanup()

    def recorder(self, **kwargs):
        recorder = evidence.GCycleEvidenceRecorder(self.root, enabled=True, **kwargs)
        self.recorders.append(recorder)
        self.assertTrue(recorder.start())
        return recorder

    def test_disabled_and_explicit_start_zero_io(self):
        for enabled in (False, True):
            recorder = evidence.GCycleEvidenceRecorder(self.root, enabled=enabled)
            self.assertIn(recorder.submit(self.roi, meta())['status'], ('disabled', 'not-started'))
            self.assertIsNone(recorder._thread)
            self.assertFalse(self.root.exists())
        self.assertFalse(evidence.GCycleEvidenceRecorder(self.root).start())

    def test_success_failure_same_contract_and_exact_pixels(self):
        recorder = self.recorder()
        ids = []
        for outcome in ('success', 'failure'):
            reply = recorder.submit(self.roi, meta(outcome=outcome))
            self.assertEqual(reply['status'], 'queued')
            ids.append(reply['case_id'])
            self.assertTrue(recorder.idle())
        self.assertNotEqual(*ids)
        for case_id, outcome in zip(ids, ('success', 'failure')):
            doc = json.loads((self.root / f'case-{case_id}.json').read_text())
            png = self.root / f'case-{case_id}.png'
            self.assertEqual(doc['metadata']['outcome'], outcome)
            self.assertEqual(doc['raw_sha256'], hashlib.sha256(self.roi.tobytes()).hexdigest())
            self.assertEqual(doc['png_sha256'], hashlib.sha256(png.read_bytes()).hexdigest())
            np.testing.assert_array_equal(np.asarray(Image.open(png))[:, :, ::-1], self.roi)
        self.assertEqual(recorder.snapshot()['counters']['saved'], 2)

    def test_slow_writer_queue_bounds_immutable_and_drain(self):
        started, release = threading.Event(), threading.Event()
        original = evidence._encode
        def slow(*args):
            started.set()
            self.assertTrue(release.wait(3))
            return original(*args)
        recorder = self.recorder()
        before = self.roi.copy()
        metadata = meta()
        with patch.object(evidence, '_encode', side_effect=slow):
            reply = recorder.submit(self.roi, metadata)
            self.assertTrue(started.wait(3))
            self.roi[:] = 0
            metadata['timings']['solve_ms'] = 999
            self.assertEqual(recorder.submit(self.roi, meta())['status'], 'queued')
            self.assertEqual(recorder.submit(self.roi, meta())['status'], 'queue-full')
            self.assertEqual(recorder.pendingWrites, 2)
            recorder.shutdown(wait=False)
            self.assertEqual(recorder.submit(self.roi, meta())['status'], 'disabled')
            release.set()
            recorder.shutdown(wait=True)
        doc = json.loads((self.root / f"case-{reply['case_id']}.json").read_text())
        self.assertEqual(doc['raw_sha256'], hashlib.sha256(before.tobytes()).hexdigest())
        self.assertEqual(doc['metadata']['timings']['solve_ms'], 1.5)
        self.assertEqual(recorder.pendingWrites, 0)
        self.assertFalse(recorder.snapshot()['running'])
        self.assertEqual(len(list(self.root.glob('*.png'))), 2)

    def test_disable_cancels_encoding_and_pending_without_disk(self):
        entered, release = threading.Event(), threading.Event()
        def slow(*args):
            entered.set()
            release.wait(3)
            return b'not-committed'
        recorder = self.recorder()
        with patch.object(evidence, '_encode', side_effect=slow):
            recorder.submit(self.roi, meta())
            self.assertTrue(entered.wait(3))
            recorder.submit(self.roi, meta())
            recorder.disable()
            self.assertEqual(recorder.pendingWrites, 1)
            release.set()
            recorder.shutdown(wait=True)
        self.assertFalse(self.root.exists())
        self.assertEqual(recorder.snapshot()['counters']['cancelled'], 2)

    def test_invalid_input_and_budgets(self):
        recorder = self.recorder(limits=evidence.EvidenceLimits(roi_bytes=500, pending_bytes=1800))
        self.assertEqual(recorder.submit(np.zeros((20, 20, 3), np.uint8), meta())['status'], 'roi-byte-limit')
        for roi in (self.roi.astype(float), self.roi[:, :, 0], np.zeros((0, 8, 3), np.uint8)):
            self.assertEqual(recorder.submit(roi, meta())['status'], 'invalid-roi')
        for metadata in (meta(secret='private'), meta(reason='free text!'), meta(timings={'solve_ms': float('nan')}),
                         meta(solver={'unknown': 1}), meta(crop_bounds=[2, 3, 9, 6]), meta(full_size=[1, 1])):
            self.assertEqual(recorder.submit(self.roi, metadata)['status'], 'invalid-metadata')
        small = self.recorder(limits=evidence.EvidenceLimits(pending_bytes=100))
        self.assertEqual(small.submit(self.roi, meta())['status'], 'queue-byte-limit')
        self.assertFalse(self.root.exists())

    def test_missing_input_has_explicit_case_and_counter(self):
        recorder = self.recorder()
        reply = recorder.submit(None, meta(outcome='failure', missing_reason='no-valid-roi'))
        self.assertTrue(recorder.idle())
        doc = json.loads((self.root / f"case-{reply['case_id']}.json").read_text())
        self.assertEqual(doc['image_status'], 'missing-input')
        self.assertIsNone(doc['png_sha256'])
        self.assertEqual(recorder.snapshot()['counters']['missing-input'], 1)
        self.assertEqual(recorder.snapshot()['counters']['saved-missing-input'], 1)

    def test_foreign_root_untouched(self):
        self.root.mkdir()
        foreign = self.root / 'important.txt'
        foreign.write_text('keep')
        recorder = self.recorder()
        recorder.submit(self.roi, meta())
        self.assertTrue(recorder.idle())
        self.assertEqual(recorder.snapshot()['status'], 'storage-unowned')
        self.assertEqual(list(self.root.iterdir()), [foreign])

    def test_changed_owned_file_and_unknown_file_fail_closed(self):
        for corruption in ('changed', 'unknown', 'registry'):
            with self.subTest(corruption=corruption):
                self.root = Path(self.temp.name) / corruption
                recorder = self.recorder()
                recorder.submit(self.roi, meta())
                self.assertTrue(recorder.idle())
                if corruption == 'changed':
                    next(self.root.glob('*.png')).write_bytes(b'foreign replacement')
                elif corruption == 'unknown':
                    (self.root / 'foreign').write_bytes(b'keep')
                else:
                    (self.root / evidence.REGISTRY).write_text('corrupt')
                before = {p.name: p.read_bytes() for p in self.root.iterdir()}
                recorder.submit(self.roi, meta())
                self.assertTrue(recorder.idle())
                self.assertNotEqual(recorder.snapshot()['status'], 'saved')
                self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})

    @unittest.skipIf(os.name == 'nt', 'POSIX link fixture; native Windows not established')
    def test_linked_root_or_owned_file_fail_closed(self):
        target = Path(self.temp.name) / 'target'
        target.mkdir()
        self.root.symlink_to(target, target_is_directory=True)
        recorder = self.recorder()
        recorder.submit(self.roi, meta())
        self.assertTrue(recorder.idle())
        self.assertEqual(list(target.iterdir()), [])
        self.root.unlink()
        recorder.submit(self.roi, meta())
        self.assertTrue(recorder.idle())
        png = next(self.root.glob('*.png'))
        os.link(png, target / 'foreign.png')
        recorder.submit(self.roi, meta())
        self.assertTrue(recorder.idle())
        self.assertEqual(recorder.snapshot()['status'], 'storage-error')
        self.assertTrue((target / 'foreign.png').exists())

    @unittest.skipIf(os.name == 'nt', 'POSIX intentionally has no destructive retirement')
    def test_capacity_and_expiry_stop_without_deletion(self):
        recorder = self.recorder(limits=evidence.EvidenceLimits(max_cases=1))
        recorder.submit(self.roi, meta())
        self.assertTrue(recorder.idle())
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        recorder.submit(self.roi, meta())
        self.assertTrue(recorder.idle())
        self.assertEqual(recorder.snapshot()['status'], 'retirement-unsupported')
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})
        self.root = Path(self.temp.name) / 'age'
        recorder = self.recorder()
        recorder.submit(self.roi, meta())
        self.assertTrue(recorder.idle())
        with patch.object(evidence.time, 'time', return_value=10**11):
            recorder.submit(self.roi, meta())
            self.assertTrue(recorder.idle())
        self.assertEqual(recorder.snapshot()['status'], 'retirement-unsupported')
        self.assertEqual(len(list(self.root.glob('*.png'))), 1)

    def test_case_encoded_byte_cap(self):
        recorder = self.recorder(limits=evidence.EvidenceLimits(case_bytes=100))
        recorder.submit(self.roi, meta())
        self.assertTrue(recorder.idle())
        self.assertEqual(recorder.snapshot()['status'], 'case-byte-limit')
        self.assertEqual(list(self.root.glob('case-*')), [])

    def test_retention_selection_with_explicit_retire_test_double(self):
        # This substitute tests selection/accounting only, NOT safe native unlink.
        retired = []
        def retire(root, row, limit):
            for name, record in row['files'].items():
                data = (root / name).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), record['sha256'])
            retired.append(row['id'])
            for name in row['files']:
                (root / name).unlink()
        recorder = self.recorder(limits=evidence.EvidenceLimits(max_cases=1))
        first = recorder.submit(self.roi, meta())['case_id']
        self.assertTrue(recorder.idle())
        with patch.object(evidence, '_retire', side_effect=retire):
            second = recorder.submit(self.roi, meta(outcome='failure'))['case_id']
            self.assertTrue(recorder.idle())
        self.assertEqual(retired, [first])
        self.assertEqual(recorder.snapshot()['status'], 'saved')
        self.assertEqual([p.name for p in self.root.glob('*.png')], [f'case-{second}.png'])

    def test_solver_matrix_is_bounded_and_deep_copied(self):
        matrix = [[1, 0, 2], [0, 1, 3]]
        clean = evidence._metadata(meta(solver={'success': True, 'matrixSourceToClient': matrix},
                                        timings={'dispatch_ms': 3}), self.roi.shape)
        matrix[0][0] = 999
        self.assertEqual(clean['solver']['matrixSourceToClient'][0][0], 1)
        for matrix in ([[1]*4]*4, [[float('inf'), 0, 1]]*2, 'matrix'):
            with self.assertRaises(ValueError):
                evidence._metadata(meta(solver={'matrixSourceToClient': matrix}), self.roi.shape)

    def test_accepting_and_coordinator_cancel_drains(self):
        recorder = self.recorder()
        self.assertTrue(recorder.accepting)
        recorder.submit(self.roi, meta())
        recorder.cancel()
        self.assertFalse(recorder.accepting)
        recorder.shutdown(wait=True)
        self.assertEqual(recorder.pendingWrites, 0)
        self.assertEqual(recorder.snapshot()['counters']['saved'], 1)

    @unittest.skipUnless(os.name == 'nt', 'Windows exclusive-handle retirement only')
    def test_native_windows_retirement(self):
        recorder = self.recorder(limits=evidence.EvidenceLimits(max_cases=1))
        first = recorder.submit(self.roi, meta())['case_id']
        self.assertTrue(recorder.idle())
        second = recorder.submit(self.roi, meta())['case_id']
        self.assertTrue(recorder.idle())
        self.assertEqual(recorder.snapshot()['status'], 'saved')
        self.assertFalse((self.root / f'case-{first}.png').exists())
        self.assertTrue((self.root / f'case-{second}.png').exists())

    def test_existing_token_identifiers(self):
        recorder = self.recorder()
        self.assertEqual(recorder.submit(self.roi, meta(opening='abc123', attempt='none', frame='frame-2'))['status'], 'queued')
        self.assertTrue(recorder.idle())
        self.assertEqual(recorder.snapshot()['status'], 'saved')

    def test_disk_byte_budget_stops_before_new_case(self):
        recorder = self.recorder(limits=evidence.EvidenceLimits(max_bytes=2048))
        recorder.submit(self.roi, meta())
        self.assertTrue(recorder.idle())
        self.assertEqual(recorder.snapshot()['status'], 'case-byte-limit')
        self.assertLessEqual(sum(p.stat().st_size for p in self.root.iterdir()), 2048)
        self.assertEqual(len(list(self.root.glob('case-*'))), 0)

    def test_completion_callback_safe_correlation_and_unlocked(self):
        received = []
        lock_checks = []
        def callback(payload):
            def check_locks():
                commit_free = recorder._commit.acquire(blocking=False)
                if commit_free:
                    recorder._commit.release()
                condition_free = recorder._condition.acquire(blocking=False)
                if condition_free:
                    recorder._condition.release()
                lock_checks.append((commit_free, condition_free))
            checker = threading.Thread(target=check_locks)
            checker.start()
            checker.join(1)
            received.append(payload)
        recorder = self.recorder(result_callback=callback)
        metadata = meta(diagnostic_id='diagnostic-1', input_sequence=7,
                        capture_generation=3, sample_role='map-roi')
        reply = recorder.submit(self.roi, metadata)
        self.assertTrue(recorder.idle())
        self.assertEqual(lock_checks, [(True, True)])
        self.assertEqual(len(received), 1)
        payload = received[0]
        self.assertEqual(payload['status'], 'saved')
        self.assertEqual(payload['case_id'], reply['case_id'])
        self.assertEqual(payload['metadata']['opening'], 3)
        self.assertEqual(payload['metadata']['diagnostic_id'], 'diagnostic-1')
        self.assertNotIn('solver', payload['metadata'])
        self.assertNotIn('crop_bounds', payload['metadata'])
        self.assertLess(len(json.dumps(payload).encode()), 4096)
        self.assertNotIn(str(self.root), json.dumps(payload))
        payload['metadata']['outcome'] = 'modified-by-observer'
        doc = json.loads((self.root / f"case-{reply['case_id']}.json").read_text())
        self.assertEqual(doc['metadata']['outcome'], 'success')

    def test_callback_failure_does_not_kill_writer_or_change_outcome(self):
        received = []
        def broken(payload):
            received.append(payload)
            raise SystemExit('private callback error must not escape')
        recorder = self.recorder(result_callback=broken)
        for _ in range(2):
            recorder.submit(self.roi, meta())
            self.assertTrue(recorder.idle())
        self.assertEqual(len(received), 2)
        state = recorder.snapshot()
        self.assertEqual(state['status'], 'saved')
        self.assertEqual(state['counters']['saved'], 2)
        self.assertEqual(state['counters']['callback-error'], 2)
        self.assertTrue(state['running'])

    def test_callback_failure_status_has_no_raw_error(self):
        received = []
        recorder = self.recorder(result_callback=received.append)
        with patch.object(recorder._store, 'write', side_effect=OSError('/private/path SECRET')):
            reply = recorder.submit(self.roi, meta())
            self.assertTrue(recorder.idle())
        self.assertEqual(received[0]['status'], 'storage-error')
        self.assertEqual(received[0]['case_id'], reply['case_id'])
        self.assertNotIn('SECRET', json.dumps(received))
        self.assertNotIn('/private', json.dumps(received))

    def test_callback_for_active_and_queued_disable_cancellation(self):
        entered, release = threading.Event(), threading.Event()
        received = []
        def slow(*args):
            entered.set()
            release.wait(3)
            return b'not-committed'
        recorder = self.recorder(result_callback=received.append)
        with patch.object(evidence, '_encode', side_effect=slow):
            active = recorder.submit(self.roi, meta())['case_id']
            self.assertTrue(entered.wait(3))
            queued = recorder.submit(self.roi, meta())['case_id']
            self.assertEqual(recorder.submit(self.roi, meta())['status'], 'queue-full')
            recorder.disable()
            self.assertEqual([(r['case_id'], r['status']) for r in received], [(queued, 'cancelled')])
            release.set()
            recorder.shutdown(wait=True)
        self.assertEqual([(r['case_id'], r['status']) for r in received],
                         [(queued, 'cancelled'), (active, 'cancelled')])
        self.assertEqual(recorder.pendingWrites, 0)
        self.assertFalse(self.root.exists())

    def test_callback_is_included_in_drain_and_can_reenter_snapshot(self):
        entered, release = threading.Event(), threading.Event()
        def callback(payload):
            recorder.snapshot()
            entered.set()
            release.wait(3)
        recorder = self.recorder(result_callback=callback)
        recorder.submit(None, meta())
        self.assertTrue(entered.wait(3))
        self.assertEqual(recorder.pendingWrites, 1)
        self.assertFalse(recorder.idle(timeout=0.01))
        release.set()
        recorder.shutdown(wait=True)
        self.assertEqual(recorder.pendingWrites, 0)
        self.assertEqual(recorder.snapshot()['status'], 'saved-missing-input')

    def test_limit_configuration_cannot_expand_policy(self):
        for values in ({'max_cases': 101}, {'pending_count': 3}, {'max_age': -1}, {'max_bytes': True}):
            with self.assertRaises(ValueError):
                evidence.EvidenceLimits(**values)


if __name__ == '__main__':
    unittest.main()
