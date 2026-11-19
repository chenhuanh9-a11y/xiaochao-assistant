"""Real Info export and saved PNG/JSON/ZIP across season roots in owned Temp.

No capture, recognition, network or installed data. POSIX replaces only the
Windows transaction/require gates for fresh fixtures; inventory, identity,
hash checks, session matching, cleanup traversal and ZIP writing stay real.
Windows uses the native storage transactions without these substitutes.
"""
from contextlib import contextmanager, ExitStack
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

import cv2
import numpy as np
from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication

import failure_sampling
import diagnostic_evidence
import test_client_info as fixtures
from nightmare_candidate import NightmareMemoryStore


CONTEXTS = (('s0', 'nightmare'), ('s0', 'difficult'),
            ('s1', 'nightmare'), ('s1', 'difficult'))


class SeasonFailureExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        fixtures.InfoTests.app = self.app
        self.fixture = fixtures.InfoTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.info = self.fixture.service()
        self.now = datetime.now(timezone.utc)
        self.sessions = (('a' * 32, 123), ('b' * 32, 456))
        self.live = self.info.paths.work_root / 'nightmare-live'
        self.live.mkdir(parents=True)
        self.log_name = 'events-qt-ring.jsonl'
        rows = [dict(timestampUTC=(self.now + timedelta(seconds=offset)).isoformat(),
                     sessionId=sid, pid=pid, logStream=self.log_name, seq=seq,
                     event='automatic-failure-sample')
                for sid, pid in self.sessions for seq, offset in enumerate((-30, 30), 1)]
        (self.live / self.log_name).write_text('\n'.join(map(json.dumps, rows)), encoding='utf-8')
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        if os.name != 'nt':
            @contextmanager
            def transaction(root, *, create=False, namespace='automatic'):
                directory = Path(root) / 'failures' / namespace
                yield 'ready' if directory.exists() else 'storage-absent'
            self.stack.enter_context(patch.object(failure_sampling, 'automatic_storage_transaction', transaction))
            self.stack.enter_context(patch.object(failure_sampling, '_require_transaction',
                side_effect=lambda root, namespace: Path(root) / 'failures' / namespace))

    def sample(self, context, index=0, *, diagnostic=None):
        season, mode = context
        root = self.info.paths.memory_root(mode, season)
        directory = root / 'failures' / 'automatic-v2'
        directory.mkdir(parents=True, exist_ok=True)
        # Deliberately identical mode-directory basenames AND image names
        # across seasons. Content must still come from the correct root.
        image = directory / ('auto-%032x.png' % index)
        value = CONTEXTS.index(context) * 40 + index
        pixels = np.full((6, 8, 3), value, np.uint8)
        encoded = cv2.imencode('.png', pixels)[1].tobytes()
        image.write_bytes(encoded)
        stat = image.stat()
        sid, pid = self.sessions[CONTEXTS.index(context) % 2]
        details = dict(eventPath=str(self.live / self.log_name), sessionId=sid, pid=pid,
            season=season, mode=mode, contextKey=f'{season}:{mode}',
            buildFingerprint=self.info._build_fingerprint, attemptId=f'{value + 1:032x}',
            fitEvidence=dict(schemaVersion=1, frameShapeHW=[6, 8], floor='-1',
                fastAttempts=[dict(accepted=False, reason='fast-existing-validation-rejected',
                    residualMatrix=np.eye(3).tolist())]))
        details.update(diagnostic or {})
        row = dict(event='automatic-failure-sample', recordedAt=self.now.isoformat(),
            capturedAt=self.now.isoformat(), diagnostic=details,
            samplePath=image.relative_to(root).as_posix(), sampleIdentity=[stat.st_dev, stat.st_ino],
            sampleBytes=len(encoded), sampleSha256=hashlib.sha256(encoded).hexdigest())
        with (directory / 'automatic-failures.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row) + '\n')
        return pixels, row

    def export(self, name, **evidence_limits):
        target = self.fixture.root / (name + '.zip')
        real_export = diagnostic_evidence.export_failure_evidence
        def export_evidence(*args, **kwargs):
            return real_export(*args, **kwargs, **evidence_limits)
        with patch.object(diagnostic_evidence, 'export_failure_evidence', side_effect=export_evidence) as export:
            self.info.exportDiagnosticsTo(QUrl.fromLocalFile(str(target)).toString())
            self.fixture.wait(lambda: not self.info.checking)
        self.assertTrue(self.info.diagnosticsReceipt['success'], self.info.diagnosticsReceipt)
        self.assertEqual(export.call_count, 1, 'all contexts must share one evidence budget')
        self.assertEqual(self.fixture.calls, [])
        self.assertEqual(self.fixture.opened, [])
        return zipfile.ZipFile(target)

    def test_all_contexts_same_names_and_mixed_sessions_reach_zip(self):
        expected = {context: self.sample(context)[0] for context in CONTEXTS}
        with patch.object(failure_sampling, 'cleanup_automatic_failure_samples',
                wraps=failure_sampling.cleanup_automatic_failure_samples) as cleanup, self.export('mixed') as archive:
            manifest = json.loads(archive.read('diagnostic-manifest.json'))
            summary = json.loads(archive.read('qt-client-summary.json'))
            evidence = manifest['failureEvidence']
            self.assertEqual(evidence['imageCount'], 4)
            self.assertEqual(len(evidence['journals']), 4)
            self.assertEqual({row['rootIndex'] for row in evidence['journals']}, {1, 2, 3, 4})
            self.assertEqual(len(archive.namelist()), len(set(archive.namelist())))
            seen = set()
            for case in evidence['cases']:
                metadata = json.loads(archive.read(case['metadataPath']))
                details = metadata['diagnostic']
                context = details['season'], details['mode']
                seen.add(context)
                data = archive.read(case['files'][0]['path'])
                np.testing.assert_array_equal(cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR), expected[context])
                self.assertEqual(details['fitEvidence']['fastAttempts'][0]['residualMatrix'], np.eye(3).tolist())
                self.assertEqual(case['logFile'], self.log_name)
                self.assertNotIn(str(self.fixture.root), archive.read(case['metadataPath']).decode())
            self.assertEqual(seen, set(CONTEXTS))
            self.assertEqual(set(summary['automaticFailureCleanup']),
                {'nightmare', 'difficult', 's1:nightmare', 's1:difficult'})
            self.assertTrue(all(row['complete'] for row in summary['automaticFailureCleanup'].values()))
        expected_roots = {self.info.paths.memory_root(mode, season) for season, mode in CONTEXTS}
        self.assertEqual({call.args[0] for call in cleanup.call_args_list}, expected_roots)

    def test_unmatched_session_and_unbound_wrong_build_do_not_export_images(self):
        good = []
        for context in CONTEXTS:
            good.append(self.sample(context)[1]['sampleSha256'])
            self.sample(context, 1, diagnostic=dict(sessionId='c' * 32))
            self.sample(context, 2, diagnostic=dict(eventPath='', buildFingerprint='OTHERBUILD'))
        with self.export('exclude-unrelated') as archive:
            evidence = json.loads(archive.read('diagnostic-manifest.json'))['failureEvidence']
            self.assertEqual(evidence['matchingCases'], 4)
            exported = [json.loads(archive.read(case['metadataPath']))['sampleSha256'] for case in evidence['cases']]
            self.assertCountEqual(exported, good)

    def test_changed_s1_image_fails_closed_without_hiding_other_contexts(self):
        for context in CONTEXTS:
            self.sample(context)
        root = self.info.paths.memory_root('difficult', 's1')
        image = root / 'failures/automatic-v2' / ('auto-' + '0' * 32 + '.png')
        image.write_bytes(image.read_bytes() + b'changed')
        with self.export('changed-s1-image') as archive:
            evidence = json.loads(archive.read('diagnostic-manifest.json'))['failureEvidence']
            summary = json.loads(archive.read('qt-client-summary.json'))
            self.assertEqual(evidence['imageCount'], 3)
            self.assertEqual(evidence['journals'][3]['automaticV2Status'], 'ownership-changed')
            self.assertFalse(summary['automaticFailureCleanup']['s1:difficult']['complete'])
            self.assertEqual(summary['automaticFailureCleanup']['s1:difficult']['status'], 'ownership-changed')
            self.assertTrue(all(json.loads(archive.read(case['metadataPath']))['diagnostic']['contextKey']
                                != 's1:difficult' for case in evidence['cases']))

    def test_automatic_default_manual_opt_in_and_text_only_cover_all_contexts(self):
        for season, mode in CONTEXTS:
            self.sample((season, mode))
            sid, pid = self.sessions[CONTEXTS.index((season, mode)) % 2]
            store = NightmareMemoryStore(SimpleNamespace(mode=mode, by_id={}), self.info.paths.memory_root(mode, season))
            store.record_alignment_failure(None, '1', 'identity-unresolved', {},
                np.zeros((6, 8, 3), np.uint8), cycle_id=9, lossless=True,
                diagnostic=dict(stage='identity-rejection', cycleId=9,
                    eventPath=str(self.live / self.log_name), sessionId=sid, pid=pid,
                    season=season, mode=mode, frameCapturedAtUtc=self.now.isoformat()))
        for name, count in (('automatic', 4), ('manual-enabled', 8), ('text-only', 0)):
            if name == 'manual-enabled':
                self.info.setIncludeFailureEvidence(True)
            elif name == 'text-only':
                self.info.setTextOnlyDiagnostics(True)
            with self.export(name) as archive:
                evidence = json.loads(archive.read('diagnostic-manifest.json'))['failureEvidence']
                self.assertEqual(evidence['imageCount'], count)
                self.assertEqual(sum(name.endswith('.png') for name in archive.namelist()), count)
                if not count:
                    self.assertEqual(evidence['status'], 'not-requested')
                    self.assertFalse(any(name.startswith('evidence/') for name in archive.namelist()))

    def test_global_sample_and_byte_budgets_are_not_multiplied_by_contexts(self):
        for context in CONTEXTS:
            for index in range(4):
                self.sample(context, index)
        with self.export('sample-budget') as archive:
            evidence = json.loads(archive.read('diagnostic-manifest.json'))['failureEvidence']
            self.assertEqual(evidence['matchingCases'], 16)
            self.assertEqual(evidence['imageCount'], 12)
            self.assertEqual(evidence['omittedBySampleLimit'], 4)
            self.assertEqual(evidence['limits']['imageBytes'], 64 * 1024 * 1024)
        with self.export('byte-budget', max_bytes=1) as archive:
            evidence = json.loads(archive.read('diagnostic-manifest.json'))['failureEvidence']
            self.assertEqual(evidence['imageCount'], 0)
            self.assertEqual(evidence['imageBytes'], 0)
            self.assertEqual(evidence['limits']['imageBytes'], 1)
            self.assertEqual(evidence['omittedBySampleLimit'], 4)
            self.assertTrue(all(case['files'][0]['status'] == 'automatic-image-byte-limit'
                                for case in evidence['cases']))


if __name__ == '__main__':
    unittest.main()
