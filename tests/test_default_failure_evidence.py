"""Recorder/export contracts with synthetic pixels and explicit storage doubles.

These tests do not claim Windows handle or real-game validation.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace
import zipfile

import cv2
import numpy as np
ROOT = Path(__file__).parents[1]
sys.path[:0] = [str(ROOT / 'app'), str(ROOT / 'experiments/qt_quick')]
import failure_sampling
from client_failure_samples import FailureSampleRecorder
from diagnostic_evidence import export_failure_evidence


class DefaultFailureEvidenceContracts(unittest.TestCase):
    def test_recorder_uses_identity_store_and_does_not_stop_after_six_process_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = root / 'failures' / 'automatic-v2'
            directory.mkdir(parents=True)
            journal = directory / 'automatic-failures.jsonl'
            recorder = FailureSampleRecorder()
            recorder.automatic_enabled = True
            recorder.policy.interval_seconds = 0
            calls = []
            @contextmanager
            def transaction(base, *, create=False, namespace='automatic'):
                self.assertEqual(namespace, 'automatic-v2')
                yield 'ready'
            def inventory(base, *, namespace='automatic'):
                self.assertEqual(namespace, 'automatic-v2')
                rows = [(line, json.loads(line)) for line in journal.read_bytes().splitlines()] if journal.exists() else []
                return dict(status='ready', rows=rows, images=[], imageRows={}, directory=directory)
            def save_journal(path, data, **kwargs):
                self.assertEqual(path, directory)
                journal.write_bytes(data)
            def create_image(base, relative, pixels, *, namespace='automatic-v2'):
                self.assertEqual(namespace, 'automatic-v2')
                calls.append(relative)
                return dict(sampleIdentity=[1,len(calls)], sampleBytes=len(pixels), sampleSha256=hashlib.sha256(pixels).hexdigest())
            with patch.object(failure_sampling, 'automatic_storage_transaction', transaction), \
                 patch.object(failure_sampling, '_cleanup_automatic_failure_samples_locked', return_value=dict(complete=True,status='ready')), \
                 patch.object(failure_sampling, 'automatic_storage_inventory', inventory), \
                 patch.object(failure_sampling, 'replace_automatic_journal', save_journal), \
                 patch.object(failure_sampling, 'create_automatic_image', create_image, create=True), \
                 patch.object(failure_sampling, 'ensure_automatic_capacity', return_value=dict(status='ready',complete=True,removed=0), create=True) as capacity:
                for index in range(8):
                    result = recorder.record_automatic(root=root, reason='map-open-preparation-deadline',
                        frame=np.full((20,20,3),index,np.uint8), diagnostic={'evidenceKind':'live-client-capture'},
                        current=lambda:True,crop_bounds=(0,0,10,10),crop_provenance='detector-control-band')
                    self.assertTrue(result['saved'], result)
                self.assertEqual(capacity.call_count, 8)
            rows = [json.loads(line) for line in journal.read_text().splitlines()]
            self.assertEqual(len(calls), 8)
            self.assertTrue(all(row.get('sampleIdentity') for row in rows))
            self.assertTrue(all('/automatic-v2/' in row['samplePath'] for row in rows))

    def test_export_reads_v2_only_under_matching_session_and_supports_text_only(self):
        now = datetime.now(timezone.utc).isoformat()
        stamp = datetime.fromisoformat(now).timestamp()
        pixels = cv2.imencode('.png', np.zeros((5,5,3),np.uint8))[1].tobytes()
        relative = 'failures/automatic-v2/auto-'+'a'*32+'.png'
        row = dict(event='automatic-failure-sample',samplePath=relative,
            sampleIdentity=[1,2],sampleBytes=len(pixels),sampleSha256=hashlib.sha256(pixels).hexdigest(),
            recordedAt=now,capturedAt=now,diagnostic=dict(eventPath='events-qt-ring.jsonl',sessionId='fixture-session',pid=123))
        seen=[]
        @contextmanager
        def transaction(root, *, create=False, namespace='automatic'):
            seen.append(namespace)
            yield 'ready'
        def inventory(root, *, namespace='automatic'):
            rows=[(json.dumps(row).encode(),row)] if namespace=='automatic-v2' else []
            return dict(status='ready', rows=rows, images=[])
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(failure_sampling,'automatic_storage_transaction',transaction), \
             patch.object(failure_sampling,'automatic_storage_inventory',inventory), \
             patch.object(failure_sampling,'cleanup_automatic_failure_samples',return_value=dict(status='ready',complete=True)), \
             patch.object(failure_sampling,'read_automatic_image',return_value=pixels,create=True):
            for matched, include in [(True,True),(False,True),(True,False)]:
                with self.subTest(matched=matched,include=include), zipfile.ZipFile(io.BytesIO(),'w') as archive:
                    report=export_failure_evidence(archive,[Path(tmp)],{'events-qt-ring.jsonl':(stamp-1,stamp+1)},lambda b:b,
                        build_fingerprint='TEST',automatic_only=True,include_automatic=include,
                        session_windows={('fixture-session' if matched else 'different',123,'events-qt-ring.jsonl'):(stamp-1,stamp+1)})
                    self.assertEqual(report['imageCount'], int(matched and include), report)
        self.assertIn('automatic-v2',seen)


    def test_partial_crop_success_is_reported_as_saved_without_hiding_missing_reason(self):
        from client_live_runtime import LiveRuntime
        captured = SimpleNamespace(context=object(), frame_bgr=np.zeros((10, 10, 3), np.uint8))
        runtime = LiveRuntime(engine_factories={})
        runtime._active = True
        runtime._capture_is_current = lambda *args: True
        runtime._full_frame = lambda frame: captured.frame_bgr
        runtime._failure_samples.automatic_enabled = True
        runtime._failure_samples.record_automatic = Mock(side_effect=[
            dict(saved=False, missingReason='map-crop-unproven'),
            dict(saved=True, missingReason='')])
        runtime._engine = SimpleNamespace(memory_store=SimpleNamespace(root=Path('/fixture')))
        runtime._failure_capture_stamp = (captured, '2026-11-03T00:00:00+00:00', 1., 2,
            'a'*32, runtime._failure_capture_scope())
        runtime._failure_control_observation = (captured, (0, 0, 5, 5))
        runtime._notify = Mock()
        result = runtime._record_automatic_failure_sample('map-open-preparation-deadline',
            captured=captured, attempt_id='a'*32)
        self.assertFalse(result['saved'])
        self.assertTrue(result['controlBand']['saved'])
        self.assertEqual(result['missingReason'], 'map-crop-unproven')
        self.assertEqual(runtime._notify.call_args.kwargs['reason'], 'saved')

    def test_retired_reason_survives_privacy_schema_and_export_metadata(self):
        from diagnostic_privacy import safe_record
        event = safe_record(dict(event='automatic-failure-sample',
            failureSample=dict(saved=False, missingReason='automatic-image-retired')))
        self.assertEqual(event['failureSample']['missingReason'], 'automatic-image-retired')
        now = datetime.now(timezone.utc).isoformat()
        stamp = datetime.fromisoformat(now).timestamp()
        row = dict(event='automatic-failure-sample', missingReason='automatic-image-retired',
            recordedAt=now, capturedAt=now,
            diagnostic=dict(eventPath='events-qt-ring.jsonl', sessionId='fixture-session', pid=123))
        @contextmanager
        def transaction(root, *, create=False, namespace='automatic'):
            self.assertEqual(namespace, 'automatic-v2', 'legacy export must not open a writable transaction')
            yield 'ready'
        def inventory(root, *, namespace='automatic'):
            rows=[(json.dumps(row).encode(),row)] if namespace=='automatic-v2' else []
            return dict(status='ready', rows=rows, images=[])
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(failure_sampling,'automatic_storage_transaction',transaction), \
             patch.object(failure_sampling,'automatic_storage_inventory',inventory), \
             patch.object(failure_sampling,'cleanup_automatic_failure_samples',return_value=dict(status='ready',complete=True)), \
             zipfile.ZipFile(io.BytesIO(),'w') as archive:
            report=export_failure_evidence(archive,[Path(tmp)],{'events-qt-ring.jsonl':(stamp-1,stamp+1)},lambda b:b,
                build_fingerprint='TEST',automatic_only=True,
                session_windows={('fixture-session',123,'events-qt-ring.jsonl'):(stamp-1,stamp+1)})
            self.assertEqual(report['cases'][0]['files'][0]['status'], 'automatic-image-retired')
            self.assertEqual(report['imageCount'], 0)
            self.assertIn(b'automatic-image-retired', archive.read('evidence/001/failure.json'))


if __name__ == '__main__':
    unittest.main()
