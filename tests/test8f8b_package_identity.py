"""Synthetic upgrade/export regressions; no packaged EXE is executed."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(os.environ.get('QT_IDENTITY_SOURCE_ROOT', Path(__file__).resolve().parents[1]))
sys.path[:0] = [str(ROOT / 'app'), str(ROOT / 'tools')]
import diagnostic_export as export
import diagnostic_evidence as evidence
import build_qt_client as build
import rebuild_qt_installer as rebuild

COMMIT = '8f8b14bda334ccf7bba26d1eadd87f723e83a34a'


class PackageIdentityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.live = self.root / 'work/nightmare-live'
        self.live.mkdir(parents=True)
        self.exe = self.write('fixture.exe', b'synthetic EXE bytes; never executed')
        self.write('_internal/client_coordinator.py', b'# synthetic module copy')
        self.write('_internal/app/map_identity_engine.py', b'# synthetic app module copy')
        self.write('_internal/references/nightmare-map-pack.json', b'{"fixture":1}')
        self.write('_internal/installation-profile.json', json.dumps(dict(sourceCommit=COMMIT)).encode())
        self.write('release-report.json', b'{"sourceCommit":"old","buildFingerprint":"OLD"}')

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def report(self):
        return build.stage_release_report(self.root, product='fixture',
            source=dict(commit=COMMIT, clean=True),
            version=dict(version='v-test', displayVersion='1.0.5', numericVersion='1.0.0.9'),
            resource_version='synthetic-baseline', resource_inventory_sha256='a' * 64)

    def bundle(self):
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', str(self.exe)):
            result = export.export_safe_diagnostic_bundle(self.root, self.live,
                app_version='v-test', build_fingerprint=COMMIT[:8].upper(),
                package_metadata=dict(status='startup-snapshot', qtSourceCommit=COMMIT))
        with zipfile.ZipFile(result['path']) as archive:
            return json.loads(archive.read('diagnostic-manifest.json'))

    def test_build_report_replaces_stale_identity_and_is_program_owned(self):
        self.report()
        report = json.loads((self.root / 'release-report.json').read_text('utf-8'))
        self.assertEqual(report['sourceCommit'], COMMIT)
        self.assertEqual(report['executableSha256'], hashlib.sha256(self.exe.read_bytes()).hexdigest())
        self.assertEqual(report['buildFingerprint'], '8F8B14BD')
        self.assertEqual(report['resourceVersion'], 'synthetic-baseline')
        self.assertEqual(build.resource_file_family('release-report.json'), 'program')
        self.assertNotIn(str(self.root), json.dumps(report))
        (self.root / 'projection').mkdir()
        _, projection = build.write_production_files_include(self.root,
            {'references/nightmare-map-pack.json': {}}, self.root / 'projection')
        self.assertIn('release-report.json', projection['families']['program'])

    def test_matching_generated_report_verifies_actual_exported_identity(self):
        self.report()
        identity = self.bundle()['packageIdentity']
        self.assertEqual(identity['executableVerification'], 'match')
        self.assertTrue(identity['files']['_internal/client_coordinator.py']['matchesReport'])
        self.assertTrue(identity['files']['_internal/references/nightmare-map-pack.json']['matchesReport'])

    def test_installer_only_rebind_updates_report_and_ownership_without_claiming_new_freeze(self):
        self.write('_installer-owned-files.txt', b'fixture.exe\n_installer-owned-files.txt\n')
        self.write('installation-profile.json', b'{}')
        self.write('_internal/references/build-fingerprint.txt', b'OLD\n')
        record = dict(product='fixture', source=dict(commit=COMMIT, clean=True),
            version=dict(version='v-test', displayVersion='1.0.5', numericVersion='1.0.0.9'),
            resourceVersion='synthetic-baseline', resourceInventorySha256='a' * 64,
            programOwnership=dict(files=2), installerInstance=dict(id='production'),
            reusedCompiledPayload={}, resources={})
        original_exe = self.exe.read_bytes()
        rebuild.refresh_release_identity(self.root, record, 'b' * 40)
        report = json.loads((self.root / 'release-report.json').read_text('utf-8'))
        self.assertEqual(report['sourceCommit'], 'b' * 40)
        self.assertEqual(report['compiledPayloadSourceCommit'], 'b' * 40)
        self.assertEqual(report['installerSourceCommit'], COMMIT)
        for name in ('installation-profile.json', '_internal/installation-profile.json'):
            profile = json.loads((self.root / name).read_text('utf-8'))
            self.assertEqual(profile['sourceCommit'], 'b' * 40)
            self.assertEqual(profile['buildFingerprint'], 'BBBBBBBB')
            self.assertEqual(profile['installerSourceCommit'], COMMIT)
        self.assertEqual(record['buildFingerprint'], 'BBBBBBBB')
        self.assertEqual(record['installerBuildFingerprint'], COMMIT[:8].upper())
        self.assertEqual(self.exe.read_bytes(), original_exe)
        owned = (self.root / '_installer-owned-files.txt').read_text('utf-8-sig').splitlines()
        self.assertIn('release-report.json', owned)
        self.assertEqual(record['programOwnership']['files'], 3)
        rebuild.refresh_release_identity(self.root, record, 'b' * 40)
        self.assertEqual(record['programOwnership']['files'], 3)

    def test_export_collects_actual_exe_even_when_report_is_stale(self):
        manifest = self.bundle()
        identity = manifest['packageIdentity']
        self.assertEqual(identity['executable']['sha256'], hashlib.sha256(self.exe.read_bytes()).hexdigest())
        self.assertEqual(identity['executableVerification'], 'expected-hash-unavailable')
        self.assertEqual(manifest['packageMetadataSource'], 'startup-snapshot')
        self.assertEqual(identity['collectionTime'], 'export-worker')

    def test_export_detects_changed_exe_module_and_resource_declaration(self):
        self.report()
        self.exe.write_bytes(b'changed synthetic EXE')
        self.write('_internal/client_coordinator.py', b'# changed module copy')
        self.write('_internal/references/nightmare-map-pack.json', b'{"fixture":2}')
        self.write('references/nightmare-map-pack.json', b'{"preservedExternal":true}')
        identity = self.bundle()['packageIdentity']
        self.assertEqual(identity['executableVerification'], 'mismatch')
        self.assertFalse(identity['files']['_internal/client_coordinator.py']['matchesReport'])
        self.assertFalse(identity['files']['_internal/references/nightmare-map-pack.json']['matchesReport'])
        self.assertEqual(identity['files']['references/nightmare-map-pack.json']['status'], 'hashed')
        self.assertIsNone(identity['files']['references/nightmare-map-pack.json']['matchesReport'])
        self.assertNotIn(str(self.root), json.dumps(identity))

    def test_untrusted_report_cannot_request_private_file_hash_or_scan(self):
        private = self.write('work/private.txt', b'private fixture')
        report = dict(identityFiles={'work/private.txt': {}, '../outside': {}})
        self.write('release-report.json', json.dumps(report).encode())
        identity = self.bundle()['packageIdentity']
        self.assertNotIn('work/private.txt', identity['files'])
        self.assertNotIn('../outside', identity['files'])
        self.assertNotIn(hashlib.sha256(private.read_bytes()).hexdigest(), json.dumps(identity))

    def test_oversized_identity_file_is_explicitly_omitted(self):
        path = self.root / '_internal/client_coordinator.py'
        with path.open('wb') as stream:
            stream.truncate(8 * 1024 * 1024 + 1)
        identity = self.bundle()['packageIdentity']
        self.assertEqual(identity['files']['_internal/client_coordinator.py']['status'], 'byte-limit')

    def test_qt_timestamp_log_includes_exact_session_failure_and_excludes_other_session(self):
        log = 'events-qt.jsonl'
        self.write('work/nightmare-live/' + log, b'\n'.join(json.dumps(dict(timestampUTC=t)).encode() for t in
            ('2026-10-31T12:00:00+00:00', '2026-10-31T12:02:00+00:00')))
        frame = b'\x89PNG\r\n\x1a\nsynthetic-header-only'
        self.write('memory/failures/sample.png', frame)
        row = dict(event='alignment-failure-sample-written', recordedAt='2026-10-31T12:01:00+00:00',
            capturedAt='2026-10-31T12:01:00+00:00', samplePath='failures/sample.png',
            diagnostic=dict(eventPath=log, buildFingerprint='8F8B14BD'))
        other = dict(row, diagnostic=dict(eventPath='events-other.jsonl', buildFingerprint='8F8B14BD'))
        self.write('memory/alignment-failures.jsonl', '\n'.join(json.dumps(r) for r in [row, other]).encode())
        result = export.export_safe_diagnostic_bundle(self.root, self.live,
            app_version='v-test', build_fingerprint='8F8B14BD', failure_roots=(self.root / 'memory',))
        with zipfile.ZipFile(result['path']) as archive:
            manifest = json.loads(archive.read('diagnostic-manifest.json'))
            self.assertEqual(manifest['failureEvidence']['imageCount'], 1)
            self.assertEqual(manifest['failureEvidence']['matchingCases'], 1)

    def test_timestamp_parser_preserves_legacy_and_ignores_malformed_rows(self):
        payload = b'[]\n{"time":"bad","timestampUTC":"2026-10-31T12:00:00+00:00"}\n{"time":"2026-10-31T12:01:00+00:00"}\n'
        window = evidence.log_window(payload)
        self.assertEqual(window[1] - window[0], 60)

    def rotation_bundle(self, source_log, backup_log, *, include_current=True):
        if include_current:
            self.write('work/nightmare-live/' + source_log,
                b'{"timestampUTC":"2026-10-31T12:04:00+00:00"}')
        else:
            (self.live / source_log).unlink(missing_ok=True)
        self.write('work/nightmare-live/' + backup_log, b'\n'.join(json.dumps(dict(timestampUTC=t)).encode() for t in
            ('2026-10-31T12:00:00+00:00', '2026-10-31T12:02:00+00:00')))
        self.write('memory/failures/sample.png', b'\x89PNG\r\n\x1a\nsynthetic-header-only')
        row = dict(event='alignment-failure-sample-written', recordedAt='2026-10-31T12:01:00+00:00',
            capturedAt='2026-10-31T12:01:00+00:00', samplePath='failures/sample.png',
            diagnostic=dict(eventPath=source_log, buildFingerprint='8F8B14BD'))
        self.write('memory/alignment-failures.jsonl', json.dumps(row).encode())
        result = export.export_safe_diagnostic_bundle(self.root, self.live,
            app_version='v-test', build_fingerprint='8F8B14BD', failure_roots=(self.root / 'memory',))
        with zipfile.ZipFile(result['path']) as archive:
            return json.loads(archive.read('diagnostic-manifest.json'))['failureEvidence']

    def test_qt_same_session_rotated_log_retains_failure_with_or_without_current_log(self):
        current = 'events-qt-20261031T120000123456Z-1234.jsonl'
        previous = current.removesuffix('.jsonl') + '-previous.jsonl'
        for present in (True, False):
            with self.subTest(currentPresent=present):
                evidence = self.rotation_bundle(current, previous, include_current=present)
                self.assertEqual(evidence['imageCount'], 1)
                self.assertEqual(evidence['cases'][0]['logFile'], previous)

    def test_rotated_log_never_reassigns_other_session_or_non_qt_explicit_path(self):
        pairs = [('events-qt-20261031T120000123456Z-1234.jsonl',
                  'events-qt-20261031T120000123456Z-5678-previous.jsonl'),
                 ('events-legacy.jsonl', 'events-legacy-previous.jsonl')]
        for current, previous in pairs:
            with self.subTest(current=current):
                self.assertEqual(self.rotation_bundle(current, previous)['imageCount'], 0)


if __name__ == '__main__':
    unittest.main()
