"""Session association through real ZIP output using only synthetic evidence."""
import base64
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).parents[1] / 'app'))
from diagnostic_evidence import export_failure_evidence


class DiagnosticSessionAssociationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'failures').mkdir()
        # Fixed one-pixel PNG, unrelated to any user's captured game frame.
        self.image = base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aZ1cAAAAASUVORK5CYII=')
        (self.root / 'failures/frame.png').write_bytes(self.image)
        self.log = 'events-session-a.jsonl'
        self.stamp = '2026-10-28T16:21:00+00:00'
        moment = datetime.fromisoformat(self.stamp).timestamp()
        self.windows = {self.log: (moment - 60, moment + 60)}

    def export(self, diagnostic, windows=None, captured_at=None):
        row = dict(event='alignment-failure-sample-written', recordedAt=self.stamp,
                   capturedAt=captured_at or self.stamp, diagnostic=diagnostic,
                   samplePath='failures/frame.png', sampleSha256=hashlib.sha256(self.image).hexdigest())
        (self.root / 'alignment-failures.jsonl').write_text(json.dumps(row) + '\n', encoding='utf-8')
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            report = export_failure_evidence(archive, [self.root],
                self.windows if windows is None else windows, lambda data: data,
                build_fingerprint='SYNTHETIC')
        return report, zipfile.ZipFile(output)

    def assert_excluded(self, diagnostic, **kwargs):
        report, archive = self.export(diagnostic, **kwargs)
        with archive:
            self.assertEqual(report['matchingCases'], 0)
            self.assertEqual(report['imageCount'], 0)
            self.assertEqual(report['cases'], [])
            self.assertEqual(archive.namelist(), [])

    def test_explicit_unexported_session_cannot_use_overlapping_same_build(self):
        for path in ('events-session-b.jsonl', r'C:\synthetic\events-session-b.jsonl'):
            with self.subTest(path=path):
                self.assert_excluded(dict(eventPath=path, buildFingerprint='SYNTHETIC'))

    def test_explicit_session_without_window_cannot_fall_back_to_another(self):
        self.assert_excluded(dict(eventPath='events-session-b.jsonl', buildFingerprint='SYNTHETIC'),
            windows={**self.windows, 'events-session-b.jsonl': None})

    def test_explicit_path_without_filename_is_not_missing_session(self):
        self.assert_excluded(dict(eventPath='C:/synthetic/', buildFingerprint='SYNTHETIC'))

    def test_matching_session_keeps_exact_image_and_log_identity(self):
        for path in (self.log, 'C:/synthetic/' + self.log, 'C:\\synthetic\\' + self.log):
            with self.subTest(path=path):
                report, archive = self.export(dict(eventPath=path, buildFingerprint='OLDER'))
                with archive:
                    self.assertEqual(report['matchingCases'], 1)
                    case = report['cases'][0]
                    self.assertEqual(case['logFile'], self.log)
                    self.assertEqual(archive.read(case['files'][0]['path']), self.image)
                    self.assertEqual(json.loads(archive.read(case['metadataPath']))['exportedLogFile'], self.log)

    def test_legacy_missing_session_keeps_same_build_time_fallback(self):
        for session in ({}, {'eventPath': ''}, {'eventPath': None}):
            with self.subTest(session=session):
                report, archive = self.export(dict(session, buildFingerprint='SYNTHETIC'))
                with archive:
                    self.assertEqual(report['matchingCases'], 1)
                    self.assertEqual(report['cases'][0]['logFile'], self.log)
                    self.assertEqual(report['imageCount'], 1)

    def test_legacy_fallback_still_requires_build_and_capture_window(self):
        self.assert_excluded(dict(buildFingerprint='OTHER'))
        self.assert_excluded({})
        self.assert_excluded(dict(buildFingerprint='SYNTHETIC'), captured_at='2026-10-28T16:00:00+00:00')

    def test_matching_explicit_session_still_requires_its_own_capture_window(self):
        self.assert_excluded(dict(eventPath=self.log, buildFingerprint='SYNTHETIC'),
                             captured_at='2026-10-28T16:00:00+00:00')


if __name__ == '__main__':
    unittest.main()
