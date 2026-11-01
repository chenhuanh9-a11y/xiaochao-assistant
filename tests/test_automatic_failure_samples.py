import sys
from pathlib import Path
import tempfile
import unittest
import ast
from datetime import datetime, timezone
import io
import json
import zipfile
import os
import hashlib
import time
import subprocess
from unittest.mock import patch
from types import SimpleNamespace
import cv2
import numpy as np

ROOT = Path(__file__).parents[1]
sys.path[:0] = [str(ROOT / 'app'), str(ROOT / 'experiments/qt_quick')]
from client_failure_samples import FailureSampleRecorder
from diagnostic_evidence import export_failure_evidence
from failure_sampling import cleanup_automatic_failure_samples
import failure_sampling


def _enabled_recorder():
    recorder = FailureSampleRecorder()
    recorder.automatic_enabled = True
    return recorder


class AutomaticFailureSamplesTests(unittest.TestCase):
    def test_process_exit_before_journal_replace_preserves_auxiliary_files_without_blocking(self):
        with tempfile.TemporaryDirectory() as directory:
            code = r'''
import sys, os, json
sys.path[:0] = [sys.argv[1], sys.argv[2]]
from client_failure_samples import FailureSampleRecorder
import numpy as np
original = os.replace
def interrupt(source, target):
    if str(target).endswith('automatic-failures.jsonl'):
        with open(source, 'rb') as stream:
            rows = [json.loads(line) for line in stream.read().splitlines()]
        if any(row.get('event') == 'automatic-failure-sample' and row.get('samplePath') for row in rows):
            os._exit(74)
    return original(source, target)
os.replace = interrupt
recorder = FailureSampleRecorder()
recorder.automatic_enabled = True
recorder.record_automatic(root=sys.argv[3], reason='structure-current-geometry-failed',
    frame=np.zeros((20,20,3), np.uint8), diagnostic={'evidenceKind':'live-client-capture'},
    current=lambda:True, crop_bounds=(2,3,10,11), crop_provenance='validated-map-region')
'''
            child = subprocess.run([sys.executable, '-c', code, str(ROOT/'app'),
                str(ROOT/'experiments/qt_quick'), directory], capture_output=True, text=True, timeout=10)
            self.assertEqual(child.returncode, 74, child.stderr)
            owned = Path(directory)/'failures/automatic'
            leftovers = list(owned.glob('.automatic-journal-*.tmp'))
            self.assertEqual(len(leftovers), 1)
            original_bytes = leftovers[0].read_bytes()
            sentinel = owned/'.automatic-journal-sentinel.tmp'
            sentinel.write_bytes(b'unknown auxiliary user sentinel')
            original_open = Path.open
            def reject_auxiliary_read(path, *args, **kwargs):
                if path in leftovers or path == sentinel:
                    raise AssertionError('auxiliary contents must never be read')
                return original_open(path, *args, **kwargs)
            with patch.object(Path, 'open', new=reject_auxiliary_read):
                report = cleanup_automatic_failure_samples(directory)
            self.assertTrue(report['complete'], report)
            self.assertEqual(report['retainedAuxiliaryCount'], 2)
            self.assertEqual(leftovers[0].read_bytes(), original_bytes)
            self.assertEqual(sentinel.read_bytes(), b'unknown auxiliary user sentinel')
            result = _enabled_recorder().record_automatic(root=directory, reason='map-open-preparation-deadline',
                frame=None, diagnostic={}, current=lambda:True)
            self.assertEqual(result['missingReason'], 'legal-captured-frame-unavailable')

    def test_retained_auxiliary_count_and_bytes_have_explicit_limits(self):
        for excessive_bytes in (False, True):
            with self.subTest(excessive_bytes=excessive_bytes), tempfile.TemporaryDirectory() as directory:
                owned = Path(directory)/'failures/automatic'
                owned.mkdir(parents=True)
                count = 1 if excessive_bytes else 5
                paths = [owned/f'.automatic-journal-{index:08d}.tmp' for index in range(count)]
                payload = b'x'*(256*1024+1) if excessive_bytes else b'unknown sentinel'
                for path in paths:
                    path.write_bytes(payload)
                report = cleanup_automatic_failure_samples(directory)
                self.assertFalse(report['complete'])
                self.assertEqual(report['status'], 'automatic-auxiliary-byte-limit' if excessive_bytes
                                 else 'automatic-auxiliary-count-limit')
                for path in paths:
                    self.assertEqual(path.read_bytes(), payload)

    def test_recorder_default_disables_automatic_capture_without_touching_storage(self):
        recorder = FailureSampleRecorder()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'not-created'
            result = recorder.record_automatic(root=root, reason='structure-current-geometry-failed',
                frame=np.zeros((20,20,3), np.uint8), diagnostic={'evidenceKind':'live-client-capture'}, current=lambda:True)
            self.assertEqual(result['missingReason'], 'automatic-sampling-disabled')
            self.assertFalse(root.exists())

    def test_process_exit_after_png_is_recovered_from_durable_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            code = r'''
import sys, os, json
sys.path[:0] = [sys.argv[1], sys.argv[2]]
import failure_sampling
from client_failure_samples import FailureSampleRecorder
import numpy as np
original = failure_sampling.replace_automatic_journal
def interrupt(directory, data):
    rows = [json.loads(line) for line in data.splitlines()]
    if any(row.get('event') == 'automatic-failure-sample' and row.get('samplePath') for row in rows):
        os._exit(73)
    return original(directory, data)
failure_sampling.replace_automatic_journal = interrupt
recorder = FailureSampleRecorder()
recorder.automatic_enabled = True
recorder.record_automatic(root=sys.argv[3], reason='structure-current-geometry-failed',
    frame=np.zeros((20,20,3), np.uint8), diagnostic={'evidenceKind':'live-client-capture'},
    current=lambda:True, crop_bounds=(2,3,10,11), crop_provenance='validated-map-region')
'''
            child = subprocess.run([sys.executable, '-c', code, str(ROOT/'app'),
                str(ROOT/'experiments/qt_quick'), directory], capture_output=True, text=True, timeout=10)
            self.assertEqual(child.returncode, 73, child.stderr)
            owned = Path(directory)/'failures/automatic'
            before = json.loads((owned/'automatic-failures.jsonl').read_text())
            self.assertEqual(before['event'], 'automatic-failure-image-intent')
            image = owned/before['samplePath'].rsplit('/',1)[-1]
            pixels = image.read_bytes()
            self.assertTrue(cleanup_automatic_failure_samples(directory)['complete'])
            after = json.loads((owned/'automatic-failures.jsonl').read_text())
            self.assertEqual(after['event'], 'automatic-failure-sample')
            self.assertEqual(image.read_bytes(), pixels)
            self.assertEqual(hashlib.sha256(pixels).hexdigest(), after['sampleSha256'])
            self.assertEqual(failure_sampling.automatic_storage_inventory(directory)['status'], 'ready')

    def test_recovery_preserves_changed_png_and_cancels_missing_png_intent(self):
        class InterruptedProcess(BaseException):
            pass
        for changed in (False, True):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                original = Path.open
                def interrupt_image(path, *args, **kwargs):
                    if path.name.startswith('auto-') and args and args[0] == 'xb':
                        raise InterruptedProcess()
                    return original(path, *args, **kwargs)
                recorder = _enabled_recorder()
                with patch.object(Path, 'open', new=interrupt_image):
                    with self.assertRaises(InterruptedProcess):
                        recorder.record_automatic(root=root, reason='structure-current-geometry-failed',
                            frame=np.zeros((20,20,3), np.uint8), diagnostic={'evidenceKind':'live-client-capture'},
                            current=lambda:True, crop_bounds=(2,3,10,11), crop_provenance='validated-map-region')
                journal = root/'failures/automatic/automatic-failures.jsonl'
                row = json.loads(journal.read_text())
                path = root/row['samplePath']
                if changed:
                    path.write_bytes(b'unknown user content')
                report = cleanup_automatic_failure_samples(root)
                if changed:
                    self.assertEqual(report['status'], 'automatic-image-recovery-mismatch')
                    self.assertEqual(path.read_bytes(), b'unknown user content')
                    self.assertEqual(json.loads(journal.read_text())['event'], 'automatic-failure-image-intent')
                else:
                    self.assertTrue(report['complete'], report)
                    self.assertEqual(journal.read_bytes(), b'')

    def test_recovery_preserves_hardlinked_png_even_when_expected_hash_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'root'
            owned = root/'failures/automatic'
            owned.mkdir(parents=True)
            external = Path(directory)/'external.png'
            cv2.imwrite(str(external), np.zeros((10,10,3), np.uint8))
            pixels = external.read_bytes()
            target = owned/('auto-'+'c'*32+'.png')
            os.link(external, target)
            journal = owned/'automatic-failures.jsonl'
            journal.write_text(json.dumps(dict(event='automatic-failure-image-intent',
                imageTransactionSchema='automatic-image-intent-v1',
                samplePath=target.relative_to(root).as_posix(), sampleBytes=len(pixels),
                sampleSha256=hashlib.sha256(pixels).hexdigest()))+'\n')
            report = cleanup_automatic_failure_samples(root)
            self.assertEqual(report['status'], 'automatic-image-recovery-link-unavailable')
            self.assertTrue(target.exists())
            self.assertEqual(external.read_bytes(), pixels)
            self.assertEqual(external.stat().st_nlink, 2)
            self.assertEqual(json.loads(journal.read_text())['event'], 'automatic-failure-image-intent')

    def test_production_record_recovers_interruption_after_png_before_commit(self):
        class InterruptedProcess(BaseException):
            pass
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            arguments = dict(root=root, reason='structure-current-geometry-failed',
                frame=np.zeros((20,20,3), np.uint8), diagnostic={'evidenceKind':'live-client-capture'},
                current=lambda:True, crop_bounds=(2,3,10,11), crop_provenance='validated-map-region')
            original = failure_sampling.replace_automatic_journal
            def interrupt_commit(directory, data):
                rows = [json.loads(line) for line in data.splitlines()]
                if any(row.get('event') == 'automatic-failure-sample' and row.get('samplePath') for row in rows):
                    raise InterruptedProcess()
                return original(directory, data)
            recorder = _enabled_recorder()
            recorder.automatic_enabled = True
            with patch.object(failure_sampling, 'replace_automatic_journal', side_effect=interrupt_commit):
                with self.assertRaises(InterruptedProcess):
                    recorder.record_automatic(**arguments)
            self.assertEqual(len(list((root/'failures/automatic').glob('auto-*.png'))), 1)
            restarted = _enabled_recorder()
            restarted.automatic_enabled = True
            result = restarted.record_automatic(**arguments)
            self.assertTrue(result['saved'], result)
            self.assertEqual(failure_sampling.automatic_storage_inventory(root)['status'], 'ready')

    def test_storage_transaction_is_nonblocking_across_processes(self):
        with tempfile.TemporaryDirectory() as directory:
            with failure_sampling.automatic_storage_transaction(directory, create=True) as status:
                self.assertEqual(status, 'ready')
                code = ('import sys;sys.path.insert(0,sys.argv[1]);'
                        'from failure_sampling import automatic_storage_transaction;'
                        'context=automatic_storage_transaction(sys.argv[2]);'
                        'print(context.__enter__());context.__exit__(None,None,None)')
                child = subprocess.run([sys.executable, '-c', code, str(ROOT/'app'), directory],
                    capture_output=True, text=True, timeout=5, check=True)
                self.assertEqual(child.stdout.strip(), 'automatic-storage-busy')
            with failure_sampling.automatic_storage_transaction(directory) as status:
                self.assertEqual(status, 'ready')

    def test_cleanup_and_writer_share_nonblocking_root_transaction(self):
        recorder = _enabled_recorder()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'failures/automatic').mkdir(parents=True)
            original = failure_sampling.automatic_storage_inventory
            injected = []
            def interleave(value):
                snapshot = original(value)
                if not injected:
                    injected.append(None)
                    injected[0] = recorder.record_automatic(root=root,
                        reason='structure-current-geometry-failed', frame=np.zeros((20,20,3), np.uint8),
                        diagnostic={'evidenceKind':'live-client-capture'}, current=lambda:True,
                        crop_bounds=(2,3,10,11), crop_provenance='validated-map-region')
                return snapshot
            with patch.object(failure_sampling, 'automatic_storage_inventory', side_effect=interleave):
                report = cleanup_automatic_failure_samples(root)
            self.assertFalse(injected[0]['saved'])
            self.assertEqual(injected[0]['missingReason'], 'automatic-storage-busy')
            self.assertTrue(report['complete'])

    def test_auto_export_rejects_unowned_hardlink_without_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'root'
            owned = root/'failures/automatic'
            owned.mkdir(parents=True)
            external = Path(directory)/'external.png'
            cv2.imwrite(str(external), np.zeros((20,20,3), np.uint8))
            target = owned/('auto-'+'a'*32+'.png')
            os.link(external, target)
            now = datetime.now(timezone.utc)
            (owned/'automatic-failures.jsonl').write_text(json.dumps(dict(event='automatic-failure-sample',
                recordedAt=now.isoformat(), capturedAt=now.isoformat(),
                samplePath=target.relative_to(root).as_posix(),
                diagnostic=dict(eventPath='events.jsonl',buildFingerprint='test')))+'\n')
            with zipfile.ZipFile(io.BytesIO(), 'w') as archive:
                report = export_failure_evidence(archive, [root], {'events.jsonl':(now.timestamp()-1,now.timestamp()+1)},
                    lambda value:value, build_fingerprint='test', automatic_only=True)
            self.assertEqual(report['imageCount'], 0)
    def test_production_runtime_entry_derives_crop_and_exports_exact_pixels(self):
        source = ast.parse((ROOT/'experiments/qt_quick/client_live_runtime.py').read_text('utf-8'))
        cls = next(n for n in source.body if isinstance(n, ast.ClassDef) and any(
            isinstance(m, ast.FunctionDef) and m.name == '_automatic_failure_sample' for m in n.body))
        methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in
                   ('_automatic_failure_sample', '_record_automatic_failure_sample')]
        module = ast.Module(body=[ast.ClassDef(name='Runtime', bases=[], keywords=[], body=methods, decorator_list=[])], type_ignores=[])
        namespace = {}
        exec(compile(ast.fix_missing_locations(module), '<production-runtime>', 'exec'), namespace)
        runtime = namespace['Runtime']()
        frame = np.zeros((300, 400, 3), np.uint8)
        frame[100:170, 100:200] = 150
        captured = SimpleNamespace(context=object())
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            runtime._failure_samples = _enabled_recorder()
            runtime._failure_samples.configure(event_path='events.jsonl', build_fingerprint='test', source_commit='a'*40)
            runtime._failure_capture_stamp = (captured, now.isoformat(), 1, 7)
            runtime._active = True
            runtime._evidence_kind = 'live-client-capture'
            runtime._mode = 'difficult'
            runtime._engine = SimpleNamespace(memory_store=SimpleNamespace(root=Path(directory)))
            runtime._capture_is_current = lambda context, generation: generation == 7
            runtime._full_frame = lambda value: frame
            runtime._notify = lambda *args, **kwargs: None
            result = runtime._automatic_failure_sample('structure-current-geometry-failed', captured=captured, current=lambda: True)
            self.assertTrue(result['saved'], result)
            journal = Path(directory)/'failures/automatic/automatic-failures.jsonl'
            row = json.loads(journal.read_text())
            self.assertEqual(row['cropBounds'], [100, 100, 100, 70])
            self.assertEqual(row['diagnostic']['cropExtractor'], 'structure_current_map_evidence')
            image = cv2.imread(str(Path(directory)/row['samplePath']))
            np.testing.assert_array_equal(image, frame[100:170, 100:200])
            manual = dict(row, event='alignment-failure-sample-written', samplePath='failures/manual.png')
            (Path(directory)/'alignment-failures.jsonl').write_text(json.dumps(manual)+'\n')
            cv2.imwrite(str(Path(directory)/'failures/manual.png'), frame)
            output = io.BytesIO()
            with zipfile.ZipFile(output, 'w') as archive:
                report = export_failure_evidence(archive, [directory], {'events.jsonl': (now.timestamp()-1, now.timestamp()+1)}, lambda value: value, build_fingerprint='test', automatic_only=True)
            self.assertEqual(report['imageCount'], 1)
            self.assertEqual(report['matchingCases'], 1)
            with zipfile.ZipFile(output) as archive:
                exported = cv2.imdecode(np.frombuffer(archive.read('evidence/001/image-0.png'), np.uint8), cv2.IMREAD_COLOR)
            np.testing.assert_array_equal(exported, image)
            runtime._capture_is_current = lambda *args, **kwargs: False
            self.assertEqual(runtime._automatic_failure_sample('map-open-preparation-deadline')['missingReason'], 'capture-not-current')

    def test_unproven_crop_is_metadata_only_and_disable_is_explicit(self):
        recorder = _enabled_recorder()
        with tempfile.TemporaryDirectory() as directory:
            result = recorder.record_automatic(root=Path(directory), reason='structure-current-geometry-failed',
                frame=np.zeros((20, 20, 3), dtype=np.uint8), current=lambda: True,
                diagnostic={'evidenceKind': 'live-client-capture'})
            self.assertEqual(result['missingReason'], 'map-crop-unproven')
            self.assertFalse(list(Path(directory).rglob('*.png')))
            recorder.automatic_enabled = False
            result = recorder.record_automatic(root=Path(directory), reason='map-open-preparation-deadline',
                frame=None, current=lambda: True, diagnostic={})
            self.assertEqual(result['missingReason'], 'automatic-sampling-disabled')

    def test_crop_bytes_count_retention_and_stale_gate(self):
        recorder = _enabled_recorder()
        recorder.policy.interval_seconds = 0
        with tempfile.TemporaryDirectory() as directory:
            args = dict(root=Path(directory), reason='structure-current-geometry-failed',
                frame=np.zeros((20, 20, 3), dtype=np.uint8), current=lambda: True,
                diagnostic={'evidenceKind': 'live-client-capture'},
                crop_bounds=(2, 3, 10, 11), crop_provenance='validated-map-region')
            for _ in range(8):
                recorder.record_automatic(**args)
            self.assertEqual(len(list(Path(directory).rglob('*.png'))), 6)
            self.assertLessEqual(sum(p.stat().st_size for p in Path(directory).rglob('*.png')), 12*1024*1024)
            args['current'] = lambda: False
            self.assertEqual(recorder.record_automatic(**args)['missingReason'], 'capture-not-current')

    def test_retention_cleanup_is_automatic_namespace_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            owned = root/'failures/automatic'
            owned.mkdir(parents=True)
            old = owned/('auto-'+'a'*32+'.png')
            old.write_bytes(b'old')
            manual = root/'failures/manual.png'
            manual.write_bytes(b'manual')
            foreign = owned/'other.png'
            foreign.write_bytes(b'foreign')
            for path in (old, manual, foreign):
                os.utime(path, (time.time()-8*86400,)*2)
            report = cleanup_automatic_failure_samples(root)
            self.assertFalse(report['complete'])
            self.assertTrue(old.exists())  # UUID-like names alone establish no ownership.
            self.assertTrue(manual.exists())
            self.assertTrue(foreign.exists())
            foreign.unlink()
            journal = owned/'automatic-failures.jsonl'
            journal.write_text(json.dumps(dict(event='automatic-failure-sample',
                recordedAt=datetime.fromtimestamp(time.time()-8*86400, timezone.utc).isoformat(),
                samplePath=old.relative_to(root).as_posix(), sampleSha256=hashlib.sha256(b'old').hexdigest()))+'\n')
            self.assertEqual(cleanup_automatic_failure_samples(root)['removed'], 1)
            self.assertFalse(old.exists())

    def test_hardlinked_journal_is_rejected_without_changing_external_sentinel(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'root'
            owned = root/'failures/automatic'
            owned.mkdir(parents=True)
            sentinel = Path(directory)/'external-sentinel'
            sentinel.write_bytes(b'external sentinel must stay unchanged')
            os.link(sentinel, owned/'automatic-failures.jsonl')
            report = cleanup_automatic_failure_samples(root)
            self.assertEqual(report['status'], 'journal-link-unavailable')
            recorder = _enabled_recorder()
            result = recorder.record_automatic(root=root, reason='map-open-preparation-deadline',
                frame=None, current=lambda: True, diagnostic={})
            self.assertEqual(result['missingReason'], 'journal-link-unavailable')
            self.assertEqual(sentinel.read_bytes(), b'external sentinel must stay unchanged')
            self.assertEqual(sentinel.stat().st_nlink, 2)

    def test_cleanup_enumeration_is_bounded_and_changed_image_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            owned = root/'failures/automatic'
            owned.mkdir(parents=True)
            for index in range(129):
                (owned/f'foreign-{index}').write_bytes(b'keep')
            report = cleanup_automatic_failure_samples(root)
            self.assertEqual(report['status'], 'cleanup-entry-limit')
            self.assertEqual(report['removed'], 0)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            owned = root/'failures/automatic'
            owned.mkdir(parents=True)
            changed = owned/('auto-'+'b'*32+'.png')
            changed.write_bytes(b'changed user content')
            os.utime(changed, (time.time()-8*86400,)*2)
            (owned/'automatic-failures.jsonl').write_text(json.dumps(dict(
                event='automatic-failure-sample', recordedAt=datetime.now(timezone.utc).isoformat(),
                samplePath=changed.relative_to(root).as_posix(),
                sampleSha256=hashlib.sha256(b'original automatic content').hexdigest()))+'\n')
            report = cleanup_automatic_failure_samples(root)
            self.assertEqual(report['status'], 'ownership-changed')
            self.assertEqual(changed.read_bytes(), b'changed user content')

if __name__ == '__main__':
    unittest.main()
