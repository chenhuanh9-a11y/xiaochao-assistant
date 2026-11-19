"""Full-client automatic evidence through the Windows writer and ZIP exporter.

Only capture pixels and log/session metadata are fixtures. PNG encoding,
identity-bound file creation/readback, cleanup, InfoService export and ZIP
creation use the production implementations.
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

import cv2
import numpy as np
from PySide6.QtCore import QUrl

ROOT = Path(__file__).parents[1]
sys.path[:0] = [str(ROOT / 'app'), str(ROOT / 'experiments/qt_quick')]
from client_failure_samples import FailureSampleRecorder
import test_client_info as info_fixtures
import failure_sampling


CONTEXTS = (('s0', 'nightmare'), ('s0', 'difficult'),
            ('s1', 'nightmare'), ('s1', 'difficult'))
RESOLUTIONS = ((1920, 1080), (2560, 1440), (3840, 2160))


def _frame(width, height, seed):
    """Deterministic high-entropy BGR input with distinct full-frame edges."""
    pixels = np.random.default_rng(seed).integers(
        0, 256, (height, width, 3), dtype=np.uint8)
    pixels[0, :, :] = (17, 61, 193)
    pixels[-1, :, :] = (203, 31, 47)
    pixels[:, 0, :] = (29, 211, 73)
    pixels[:, -1, :] = (239, 197, 11)
    pixels[0, 0, :] = (5, 10, 15)
    pixels[0, -1, :] = (25, 50, 75)
    pixels[-1, 0, :] = (100, 125, 150)
    pixels[-1, -1, :] = (175, 200, 225)
    return pixels


@unittest.skipUnless(os.name == 'nt', 'requires the production Windows exclusive-handle contract')
class FullClientWriterTests(unittest.TestCase):
    def test_1080p_1440p_and_4k_encode_read_back_full_png(self):
        with tempfile.TemporaryDirectory(prefix='full-client-writer-') as temporary:
            root = Path(temporary)
            recorder = FailureSampleRecorder()
            recorder.automatic_enabled = True
            recorder.policy.interval_seconds = 0
            rows = []

            for seed, (width, height) in enumerate(RESOLUTIONS, 11):
                with self.subTest(width=width, height=height):
                    source = _frame(width, height, seed)
                    compatibility_crop = ({} if seed == 11 else dict(
                        crop_bounds=(-5, 12, 37, 41), crop_provenance='old-roi-request'))
                    reasons = ('map-open-preparation-deadline', 'identity-not-uniquely-verified',
                               'current-floor-unavailable')
                    result = recorder.record_automatic(
                        root=root,
                        reason=reasons[seed - 11],
                        frame=source,
                        diagnostic=dict(evidenceKind='live-client-capture', mode='nightmare',
                            eventPath='events-qt-ring.jsonl', sessionId='a' * 32, pid=123,
                            frameCapturedAtUtc=datetime.now(timezone.utc).isoformat()),
                        current=lambda: True,
                        # The first legal frame has no ROI. Later compatibility
                        # arguments are deliberately invalid and must not trim it.
                        **compatibility_crop)
                    self.assertTrue(result['saved'], result)
                    self.assertEqual(result['missingReason'], '')

                    journal = root / 'failures/automatic-v2/automatic-failures.jsonl'
                    row = json.loads(journal.read_text(encoding='utf-8').splitlines()[-1])
                    self.assertEqual(row['cropBounds'], [0, 0, width, height])
                    self.assertEqual(row['cropProvenance'], 'full-client-capture')
                    self.assertEqual(row['diagnostic']['sampleRole'], 'full-client-frame')
                    self.assertLessEqual(row['sampleBytes'], 32 * 1024 * 1024)
                    rows.append((source, row))

                    with failure_sampling.automatic_storage_transaction(
                            root, namespace='automatic-v2') as status:
                        self.assertEqual(status, 'ready')
                        payload = failure_sampling.read_automatic_image(root, row)
                    self.assertEqual(len(payload), row['sampleBytes'])
                    self.assertEqual(len(payload), (root / row['samplePath']).stat().st_size)
                    decoded = cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_UNCHANGED)
                    self.assertEqual(decoded.shape, source.shape)
                    np.testing.assert_array_equal(decoded, source)
                    np.testing.assert_array_equal(decoded[0, :], source[0, :])
                    np.testing.assert_array_equal(decoded[-1, :], source[-1, :])
                    np.testing.assert_array_equal(decoded[:, 0], source[:, 0])
                    np.testing.assert_array_equal(decoded[:, -1], source[:, -1])

            inventory = failure_sampling.automatic_storage_inventory(root, namespace='automatic-v2')
            self.assertEqual(inventory['status'], 'ready')
            self.assertEqual(len(inventory['images']), 3)
            self.assertLessEqual(inventory['imageBytes'], 64 * 1024 * 1024)

            from diagnostic_evidence import export_failure_evidence
            captured_at = datetime.now(timezone.utc).timestamp()
            with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as archive_buffer:
                with zipfile.ZipFile(archive_buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                    report = export_failure_evidence(archive, [root],
                        {'events-qt-ring.jsonl': (captured_at - 86400, captured_at + 86400)},
                        lambda value: value, build_fingerprint='fixture', automatic_only=True,
                        include_automatic=True,
                        session_windows={('a' * 32, 123, 'events-qt-ring.jsonl'):
                            (captured_at - 86400, captured_at + 86400)})
                self.assertEqual(report['imageCount'], 3, report)
                self.assertGreater(report['imageBytes'], 12 * 1024 * 1024)
                with zipfile.ZipFile(archive_buffer) as archive:
                    exported_4k = next(case for case in report['cases']
                        if json.loads(archive.read(case['metadataPath']))['diagnostic']['frameShapeHW']
                        == [2160, 3840])
                    exported_size = exported_4k['files'][0]['bytes']
                    self.assertGreater(exported_size, 12 * 1024 * 1024)
                    exported_png = archive.read(exported_4k['files'][0]['path'])
                    decoded_4k = cv2.imdecode(np.frombuffer(exported_png, np.uint8), cv2.IMREAD_UNCHANGED)
                    np.testing.assert_array_equal(decoded_4k, rows[-1][0])

    def test_three_4k_frames_rotate_by_real_64_mib_context_capacity(self):
        width, height = 3840, 2160
        with tempfile.TemporaryDirectory(prefix='full-client-capacity-') as temporary:
            root = Path(temporary)
            recorder = FailureSampleRecorder()
            recorder.automatic_enabled = True
            recorder.policy.interval_seconds = 0
            written = []
            for index in range(3):
                pixels = _frame(width, height, 51 + index)
                result = recorder.record_automatic(root=root,
                    reason='identity-not-uniquely-verified', frame=pixels, current=lambda: True,
                    diagnostic=dict(evidenceKind='live-client-capture', mode='nightmare',
                        attemptId=f'{51 + index:032x}',
                        frameCapturedAtUtc=datetime.now(timezone.utc).isoformat()),
                    crop_bounds=(2, 3, 10, 11), crop_provenance='legacy-crop-request')
                self.assertTrue(result['saved'], result)
                journal = root / 'failures/automatic-v2/automatic-failures.jsonl'
                journal_rows = [json.loads(line) for line in journal.read_text(encoding='utf-8').splitlines()]
                latest = next(row for row in journal_rows
                    if row['diagnostic']['attemptId'] == f'{51 + index:032x}')
                written.append((latest['samplePath'], 51 + index))
                self.assertGreater(latest['sampleBytes'], 12 * 1024 * 1024)
                self.assertLessEqual(latest['sampleBytes'], 32 * 1024 * 1024)
                del pixels

            inventory = failure_sampling.automatic_storage_inventory(root, namespace='automatic-v2')
            self.assertEqual(inventory['status'], 'ready')
            self.assertEqual(len(inventory['images']), 2)
            self.assertGreater(inventory['imageBytes'], 48_000_000)
            self.assertLessEqual(inventory['imageBytes'], 64 * 1024 * 1024)
            current_paths = {path.relative_to(root).as_posix() for path in inventory['images']}
            self.assertNotIn(written[0][0], current_paths)
            self.assertFalse((root / written[0][0]).exists())
            self.assertEqual(current_paths, {written[1][0], written[2][0]})

            rows = [json.loads(line) for line in (root / 'failures/automatic-v2/automatic-failures.jsonl')
                    .read_text(encoding='utf-8').splitlines()]
            sample_rows = [row for row in rows if row.get('samplePath')]
            retired_rows = [row for row in rows if row.get('missingReason') == 'automatic-image-retired']
            self.assertEqual({row['samplePath'] for row in sample_rows}, current_paths)
            self.assertEqual(len(retired_rows), 1)
            self.assertEqual(retired_rows[0]['diagnostic']['attemptId'], f'{written[0][1]:032x}')
            for path, seed in written[1:]:
                row = next(row for row in rows if row['samplePath'] == path)
                with failure_sampling.automatic_storage_transaction(root, namespace='automatic-v2') as status:
                    self.assertEqual(status, 'ready')
                    payload = failure_sampling.read_automatic_image(root, row)
                decoded = cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_UNCHANGED)
                np.testing.assert_array_equal(decoded, _frame(width, height, seed))

    def test_oversized_high_entropy_frame_is_rejected_without_cropping_or_cooldown(self):
        width, height = 3500, 3400
        pixels = np.random.default_rng(20261112).integers(
            0, 256, (height, width, 3), dtype=np.uint8)
        self.assertLess(pixels.nbytes, 64 * 1024 * 1024)
        with tempfile.TemporaryDirectory(prefix='full-client-oversize-') as temporary:
            root = Path(temporary)
            recorder = FailureSampleRecorder()
            recorder.automatic_enabled = True
            result = recorder.record_automatic(root=root,
                reason='identity-not-uniquely-verified', frame=pixels, current=lambda: True,
                diagnostic=dict(evidenceKind='live-client-capture', mode='nightmare',
                    sampleRole='full-client-frame', frameShapeHW=[height, width],
                    frameCapturedAtUtc=datetime.now(timezone.utc).isoformat()))
            self.assertFalse(result['saved'], result)
            self.assertEqual(result['missingReason'], 'image-byte-limit')
            self.assertFalse(recorder.policy.saved)
            self.assertEqual(recorder.automatic_saved_count, 0)
            directory = root / 'failures/automatic-v2'
            self.assertFalse(list(directory.glob('*.png')))
            row = json.loads((directory / 'automatic-failures.jsonl')
                .read_text(encoding='utf-8').splitlines()[-1])
            self.assertIsNone(row['samplePath'])
            self.assertEqual(row['missingReason'], 'image-byte-limit')
            self.assertEqual(row['diagnostic']['frameShapeHW'], [height, width])

            repeated = recorder.record_automatic(root=root,
                reason='identity-not-uniquely-verified', frame=pixels, current=lambda: True,
                diagnostic=dict(evidenceKind='live-client-capture', mode='nightmare',
                    sampleRole='full-client-frame', frameShapeHW=[height, width],
                    frameCapturedAtUtc=datetime.now(timezone.utc).isoformat()))
            self.assertEqual(repeated['missingReason'], 'image-byte-limit')
            self.assertFalse(recorder.policy.saved)
            self.assertEqual(recorder.automatic_saved_count, 0)
            self.assertFalse(list(directory.glob('*.png')))


@unittest.skipUnless(os.name == 'nt', 'requires the production Windows exclusive-handle contract')
class FullClientInfoExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtGui import QGuiApplication
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        info_fixtures.InfoTests.app = self.app
        self.fixture = info_fixtures.InfoTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.info = self.fixture.service()
        self.now = datetime.now(timezone.utc)
        self.sessions = (('a' * 32, 123), ('b' * 32, 456))
        self.live = self.info.paths.work_root / 'nightmare-live'
        self.live.mkdir(parents=True)
        self.log_name = 'events-qt-ring.jsonl'
        log_rows = [dict(timestampUTC=(self.now + timedelta(seconds=offset)).isoformat(),
                         sessionId=session, pid=pid, logStream=self.log_name, seq=seq,
                         event='automatic-failure-sample')
                    for session, pid in self.sessions for seq, offset in enumerate((-30, 30), 1)]
        (self.live / self.log_name).write_text(
            '\n'.join(json.dumps(row) for row in log_rows), encoding='utf-8')

    def _save(self, context, index):
        season, mode = context
        root = self.info.paths.memory_root(mode, season)
        seed = index + 31
        pixels = np.zeros((73, 91, 3), np.uint8)
        pixels[:] = (seed, 60 + seed, 130 + seed)
        pixels[0, 0] = (1, 2, 3)
        pixels[0, -1] = (4, 5, 6)
        pixels[-1, 0] = (7, 8, 9)
        pixels[-1, -1] = (10, 11, 12)
        session, pid = self.sessions[index % len(self.sessions)]
        recorder = FailureSampleRecorder()
        recorder.automatic_enabled = True
        result = recorder.record_automatic(root=root, reason='identity-not-uniquely-verified',
            frame=pixels, current=lambda: True,
            diagnostic=dict(evidenceKind='live-client-capture', mode=mode, season=season,
                contextKey=f'{season}:{mode}', eventPath=str(self.live / self.log_name),
                sessionId=session, pid=pid, buildFingerprint=self.info._build_fingerprint,
                frameCapturedAtUtc=self.now.isoformat(), attemptId=f'{seed:032x}'),
            crop_bounds=(4, 4, 20, 20), crop_provenance='detector-control-band')
        self.assertTrue(result['saved'], result)
        return pixels

    def _export(self, filename):
        target = self.fixture.root / (filename + '.zip')
        self.info.exportDiagnosticsTo(QUrl.fromLocalFile(str(target)).toString())
        self.fixture.wait(lambda: not self.info.checking)
        self.assertTrue(self.info.diagnosticsReceipt['success'], self.info.diagnosticsReceipt)
        return zipfile.ZipFile(target)

    def test_default_export_four_contexts_and_text_only_setting(self):
        expected = {context: self._save(context, index) for index, context in enumerate(CONTEXTS)}
        self.assertTrue(self.info.includeAutomaticFailureEvidence)
        self.assertFalse(self.info.includeFailureEvidence)

        with self._export('full-client-default') as archive:
            manifest = json.loads(archive.read('diagnostic-manifest.json'))
            evidence = manifest['failureEvidence']
            self.assertEqual(evidence['imageCount'], 4)
            self.assertEqual(len(evidence['journals']), 4)
            self.assertEqual({row['rootIndex'] for row in evidence['journals']}, {1, 2, 3, 4})
            seen = set()
            for case in evidence['cases']:
                metadata = json.loads(archive.read(case['metadataPath']))
                diagnostic = metadata['diagnostic']
                context = diagnostic['season'], diagnostic['mode']
                seen.add(context)
                self.assertEqual(diagnostic['contextKey'], f'{context[0]}:{context[1]}')
                self.assertNotIn(str(self.fixture.root), archive.read(case['metadataPath']).decode('utf-8'))
                image_path = case['files'][0]['path']
                decoded = cv2.imdecode(np.frombuffer(archive.read(image_path), np.uint8),
                                       cv2.IMREAD_UNCHANGED)
                np.testing.assert_array_equal(decoded, expected[context])
            self.assertEqual(seen, set(CONTEXTS))
            self.assertEqual(sum(name.endswith('.png') for name in archive.namelist()), 4)

        self.info.setIncludeAutomaticFailureEvidence(False)
        with self._export('full-client-disabled') as archive:
            evidence = json.loads(archive.read('diagnostic-manifest.json'))['failureEvidence']
            self.assertEqual(evidence['imageCount'], 0)
            self.assertEqual(evidence['status'], 'not-requested')
            self.assertFalse(any(name.startswith('evidence/') for name in archive.namelist()))

        self.info.setTextOnlyDiagnostics(True)
        self.assertTrue(self.info.textOnlyDiagnostics)
        with self._export('full-client-text-only') as archive:
            manifest = json.loads(archive.read('diagnostic-manifest.json'))
            self.assertTrue(manifest['textOnly'])
            self.assertEqual(manifest['failureEvidence']['status'], 'not-requested')
            self.assertEqual(manifest['failureEvidence']['imageCount'], 0)
            self.assertFalse(any(name.startswith('evidence/') for name in archive.namelist()))
        self.assertEqual(self.fixture.calls, [])
        self.assertEqual(self.fixture.opened, [])


if __name__ == '__main__':
    unittest.main()
