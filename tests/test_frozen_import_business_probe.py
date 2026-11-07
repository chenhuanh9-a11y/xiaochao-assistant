"""Probe orchestration/real fixture validity only, no frozen or native execution."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('frozen_business_probe', ROOT / 'tools/verify_frozen_import_business.py')
probe = importlib.util.module_from_spec(spec); spec.loader.exec_module(probe)


class FrozenBusinessProbeContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = (Path(self.temp.name) / 'synthetic-install').resolve(); self.root.mkdir()
        self.fixture = probe.prepare_fixture(self.root)
        self.fixture.update(requestId='unit-probe', probeId='a' * 32)
        self.receipt = dict(requestId='unit-probe', archiveSha256=self.fixture['archiveSha256'],
            parentExit='exited-matching-process-handle', completedAtUTC='unit-test-only',
            success=False, errorCode='ModuleNotFoundError', reason="No module named 'PIL'")

    def test_synthetic_zip_is_actually_valid_under_real_source_inspector(self):
        import star_map_import
        result = star_map_import.inspect_package(self.root / 'fixture-author.zip', self.root)
        self.assertEqual(result['packageId'], 'author-ci-synthetic')
        self.assertEqual(len(result['files']), 2)
        self.assertTrue(star_map_import.supports_live_author_import(result))
        self.assertFalse((self.root / 'work/map-packages/author-ci-synthetic.json').exists())

    def test_old_red_requires_exact_missing_pil_and_proved_parent_gate(self):
        result = probe.validate_business(self.root, self.fixture, self.receipt, 2, 'missing-image-dependencies')
        self.assertEqual(result['outcome'], 'old-red-confirmed'); self.assertFalse(result['businessValidated'])
        for change in ({'parentExit':'unknown'}, {'requestId':'old'}, {'archiveSha256':'b' * 64},
                       {'errorCode':'ImportRejected'}, {'reason':"No module named 'numpy'"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                probe.validate_business(self.root, self.fixture, dict(self.receipt, **change), 2, 'missing-image-dependencies')

    def test_old_red_cannot_hide_base_drift_or_partial_publication(self):
        row = self.fixture['dependencies'][0]; path = self.root / row['path']; original = path.read_bytes()
        path.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'dependency changed'):
            probe.validate_business(self.root, self.fixture, self.receipt, 2, 'missing-image-dependencies')
        path.write_bytes(original)
        payload = self.root / self.fixture['payload'][0]['path']; payload.parent.mkdir(parents=True, exist_ok=True); payload.write_bytes(b'partial')
        with self.assertRaisesRegex(ValueError, 'published author'):
            probe.validate_business(self.root, self.fixture, self.receipt, 2, 'missing-image-dependencies')

    def test_unknown_and_user_sentinels_must_survive_both_result_modes(self):
        self.assertEqual(len(self.fixture['preservedFiles']), 2)
        for row in self.fixture['preservedFiles']:
            path = self.root / row['path']; original = path.read_bytes()
            path.write_bytes(b'changed unknown synthetic object')
            for mode in ('missing-image-dependencies', 'success', 'invalid-image-rejected'):
                with self.subTest(path=row['path'], mode=mode), self.assertRaisesRegex(ValueError, 'unknown/user object changed'):
                    probe.validate_business(self.root, self.fixture, self.receipt, 2, mode)
            path.write_bytes(original)

    def test_success_requires_receipt_and_exact_installed_payload(self):
        good = dict(self.receipt, success=True)
        with self.assertRaises((ValueError, FileNotFoundError)):
            probe.validate_business(self.root, self.fixture, good, 0, 'success')
        with zipfile.ZipFile(self.root / 'fixture-author.zip') as archive:
            for row in self.fixture['payload']:
                p = self.root / row['path']; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(archive.read('data/' + row['path']))
        author = self.root / 'work/map-packages/author-ci-synthetic.json'
        probe.compile_probe.write_json(author, dict(packageId=self.fixture['packageId'], archiveSha256=self.fixture['archiveSha256'], requiresFiles=self.fixture['dependencies']))
        result = probe.validate_business(self.root, self.fixture, good, 0, 'success')
        self.assertTrue(result['businessValidated'])
        (self.root / self.fixture['payload'][0]['path']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'author bytes differ'):
            probe.validate_business(self.root, self.fixture, good, 0, 'success')

    def test_invalid_image_passes_hashes_but_real_source_rejects_without_publication(self):
        import star_map_import
        root = self.root.parent / 'invalid-install'; root.mkdir()
        fixture = probe.prepare_fixture(root, invalid_image=True)
        fixture.update(requestId='bad-image', probeId='b' * 32)
        with self.assertRaises(star_map_import.ImportRejected) as caught:
            star_map_import.install_package(root / 'fixture-author.zip', root,
                application_stopped=True, expected_archive_sha256=fixture['archiveSha256'])
        self.assertEqual(caught.exception.code, 'author-v2-invalid-image')
        receipt = dict(requestId=fixture['requestId'], archiveSha256=fixture['archiveSha256'],
            parentExit='exited-matching-process-handle', completedAtUTC='unit-test-only',
            success=False, errorCode=caught.exception.code, reason=str(caught.exception))
        result = probe.validate_business(root, fixture, receipt, 2, 'invalid-image-rejected')
        self.assertTrue(result['rejectionValidated']); self.assertFalse(result['businessValidated'])
        self.assertTrue((root / 'fixture-valid-author.zip').is_file())
        for change in ({'errorCode':'ModuleNotFoundError'}, {'parentExit':'unknown'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                probe.validate_business(root, fixture, dict(receipt, **change), 2, 'invalid-image-rejected')
        extra = root / 'work/map-packages/unexpected-receipt.json'
        extra.parent.mkdir(parents=True, exist_ok=True); extra.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'transaction/publication'):
            probe.validate_business(root, fixture, receipt, 2, 'invalid-image-rejected')

    def test_same_valid_fixture_actually_installs_through_source_transaction(self):
        import star_map_import
        result = star_map_import.install_package(self.root / 'fixture-author.zip', self.root,
            application_stopped=True, expected_archive_sha256=self.fixture['archiveSha256'])
        self.assertEqual(result['packageId'], self.fixture['packageId'])
        receipt = dict(self.receipt, success=True)
        checked = probe.validate_business(self.root, self.fixture, receipt, 0, 'success')
        self.assertTrue(checked['businessValidated'])

    def test_evidence_preserves_data_but_not_partial_software(self):
        (self.root / 'fake-helper.exe').write_bytes(b'not a PE')
        evidence = self.root.parent / 'evidence'; evidence.mkdir()
        probe.preserve_data_evidence(self.root, evidence)
        self.assertTrue((evidence / 'fixture-author.zip').is_file())
        self.assertTrue((evidence / '_internal').is_dir())
        self.assertFalse((evidence / 'fake-helper.exe').exists())
        self.assertTrue((self.root / 'fake-helper.exe').exists())

    def test_observer_ack_is_complete_before_atomic_publication(self):
        target = self.root / 'observer-ready.json'
        value = dict(probeId='unit', child=dict(pid=123))
        replace = probe.os.replace
        calls = []
        def inspect_before_publish(source, destination):
            self.assertFalse(target.exists())
            self.assertEqual(json.loads(Path(source).read_text('utf-8')), value)
            calls.append((source, destination))
            return replace(source, destination)
        with patch.object(probe.os, 'replace', side_effect=inspect_before_publish):
            probe.publish_ack(target, value)
        self.assertEqual(len(calls), 1)
        self.assertEqual(json.loads(target.read_text('utf-8')), value)
        with self.assertRaisesRegex(ValueError, 'already exists'):
            probe.publish_ack(target, value)

    def test_only_import_helper_keeps_required_image_dependencies_without_gui(self):
        def exclusions(kind):
            tree = ast.parse((ROOT / ('tools/qt-' + kind + '-helper.spec')).read_text())
            analysis = next(n for n in ast.walk(tree) if isinstance(n, ast.Call)
                            and isinstance(n.func, ast.Name) and n.func.id == 'Analysis')
            return set(ast.literal_eval(next(k.value for k in analysis.keywords if k.arg == 'excludes')))
        excluded = exclusions('import')
        self.assertFalse({'numpy', 'PIL'} & excluded)
        self.assertTrue({'tkinter', '_tkinter', 'PySide6', 'PyQt5', 'PyQt6', 'PySide2', 'cv2'} <= excluded)
        for kind in ('recovery', 'reset'):
            self.assertTrue({'numpy', 'PIL'} <= exclusions(kind))
        spec = (ROOT / 'tools/qt-import-helper.spec').read_text()
        for name in ('PIL.Image', 'PIL.PngImagePlugin', 'PIL.JpegImagePlugin', 'numpy.linalg'):
            self.assertIn(name, spec)
        self.assertEqual(len(probe.PACKAGES), len(set(probe.PACKAGES)))
        self.assertTrue({'numpy', 'Pillow'} <= set(probe.PACKAGES))
        parent = (ROOT / 'tests/fixtures/synthetic_import_parent.py').read_text()
        self.assertIn('launch_import_helper(', parent)
        self.assertIn('with resource_runtime_lease(root)', parent)
        self.assertNotIn('run_helper(', parent)
        self.assertNotIn('subprocess.run(', parent)


if __name__ == '__main__':
    unittest.main()
