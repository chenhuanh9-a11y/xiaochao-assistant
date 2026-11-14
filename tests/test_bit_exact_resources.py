"""Actual producer/consumer admission using synthetic original-layout inputs.

The complete fixture map pools have tiny synthetic canvases and S1 private
ownership placeholders. They prove packaging and numeric admission, not game
recognition, coordinate calibration, GUI interaction or cross-CPU invariance.
"""
import hashlib
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import numpy as np

import export_native_base_package as exporter
from export_resource_preflight import assert_source_equal
from imported_resource_archive import inspect_archive
from original_qt_resource_fixtures import (OriginalS0DifficultSourceFixture,
    OriginalS0NightmareSourceFixture, OriginalS1SourceFixture)


class SourceEqualityDiagnosticsTests(unittest.TestCase):
    def test_mixed_contract_dtypes_are_retained(self):
        for dtype in (np.uint16, np.float32, np.float64, np.int64):
            with self.subTest(dtype=dtype):
                expected = np.array([[0, 1], [2, 3]], dtype=dtype)
                assert_source_equal('resource', expected, expected.copy())

    def test_one_ulp_reports_position_rows_and_distance(self):
        for dtype in (np.float32, np.float64):
            with self.subTest(dtype=dtype):
                expected = np.array([[1., -1.], [0., 2.]], dtype=dtype)
                actual = expected.copy()
                actual[1, 1] = np.nextafter(actual[1, 1], dtype(np.inf))
                with self.assertRaisesRegex(ValueError, r'affectedRows=1/2; maxULP=1;.*\[1,1\].*ULP=1'):
                    assert_source_equal('resource', expected, actual)

    def test_scalar_one_ulp_and_contract_mismatches(self):
        with self.assertRaisesRegex(ValueError, r'maxULP=1;.*\[\]'):
            assert_source_equal('pitch', 0., np.nextafter(0., np.inf))
        with self.assertRaisesRegex(ValueError, 'shape'):
            assert_source_equal('resource', np.zeros(2), np.zeros(3))
        with self.assertRaisesRegex(ValueError, 'dtype'):
            assert_source_equal('resource', np.zeros(2, np.uint16), np.zeros(2, np.float32))


class OriginalExportNumericAdmissionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='export-numeric-admission-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    @staticmethod
    def snapshot(root):
        return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}

    def test_four_contexts_use_the_real_producer_and_semantic_readers(self):
        for season, mode in (('s0', 'difficult'), ('s0', 'nightmare'),
                             ('s1', 'difficult'), ('s1', 'nightmare')):
            with self.subTest(season=season, mode=mode):
                root = self.root / f'{season}-{mode}'
                root.mkdir()
                if season == 's1':
                    fixture = OriginalS1SourceFixture(root, mode=mode)
                    source_root = fixture.sourceRoot
                elif mode == 'difficult':
                    fixture = OriginalS0DifficultSourceFixture(root)
                    source_root = fixture.sourceRoot
                else:
                    fixture = OriginalS0NightmareSourceFixture(root)
                    source_root = fixture.source.sourceRoot
                # Original fixture wrappers prepare their explicit ownership
                # inputs during export. Compare source bytes at the actual
                # producer boundary, after those fixture-only preparations.
                original = exporter.export_package
                snapshots = []

                def run(*args, **kwargs):
                    before = self.snapshot(source_root)
                    result = original(*args, **kwargs)
                    self.assertEqual(self.snapshot(source_root), before)
                    snapshots.append(before)
                    return result

                with patch.object(exporter, 'export_package', side_effect=run):
                    result = fixture.export()
                self.assertTrue(snapshots)
                self.assertTrue(result['semanticValidated'])
                self.assertFalse(result['catalogActivated'])
                summary = inspect_archive(fixture.output)
                self.assertEqual(summary.archive_sha256, result['sha256'])
                self.assertFalse(any(root.rglob('catalog.json')))
                with zipfile.ZipFile(fixture.output) as archive:
                    manifest = json.loads(archive.read('manifest.json'))
                    self.assertIn('numpy', manifest['provenance']['numericRuntime'])
                    if season == 's1':
                        path = next(p for p in archive.namelist() if '/fast-structure/' in p)
                        with np.load(BytesIO(archive.read(path)), allow_pickle=False) as arrays:
                            self.assertEqual(arrays['distance'].dtype, np.uint16)
                            self.assertEqual(arrays['chains'].dtype, np.float32)
                            self.assertEqual(arrays['rays'].dtype, np.float32)
                            self.assertEqual(arrays['pitch'].dtype, np.float64)
                            self.assertEqual(arrays['shape'].dtype, np.int64)

    def test_valid_npy_one_ulp_is_rejected_before_output_publication(self):
        fixture = OriginalS0NightmareSourceFixture(self.root)
        original = exporter._controls_payloads

        def shifted(mode):
            payloads = original(mode)
            vectors = np.load(BytesIO(payloads['templates.npy']), allow_pickle=False).copy()
            vectors[0, 0] = np.nextafter(vectors[0, 0], np.float32(np.inf))
            output = BytesIO()
            np.save(output, vectors, allow_pickle=False)
            return dict(payloads, **{'templates.npy': output.getvalue()})

        before = self.snapshot(fixture.source.sourceRoot)
        # Only the produced resource is fault-injected. The archive, hashes,
        # profile decoder and downstream semantic validator are all real.
        with patch.object(exporter, '_controls_payloads', side_effect=shifted):
            with self.assertRaisesRegex(ValueError,
                    r'original-controls-parameters-not-source-equivalent.*templates.npy:.*maxULP=1;.*\[0,0\]'):
                fixture.export()
        self.assertFalse(fixture.output.exists())
        self.assertEqual(self.snapshot(fixture.source.sourceRoot), before)
        self.assertFalse(any(self.root.rglob('catalog.json')))

    def test_valid_npz_one_ulp_with_correct_inventory_is_rejected_before_publication(self):
        fixture = OriginalS1SourceFixture(self.root, mode='nightmare')
        path = fixture.prefixRoot / next(iter(fixture.prepared_resources.values()))
        with np.load(path, allow_pickle=False) as archive:
            arrays = {name: archive[name].copy() for name in archive.files}
        arrays['pitch'] = np.nextafter(arrays['pitch'], np.float64(np.inf))
        np.savez_compressed(path, allow_pickle=False, **arrays)
        fixture.refresh_after_source_edit()
        inventory = json.loads(fixture.inventory.read_bytes())['files']
        relative = path.relative_to(fixture.sourceRoot).as_posix()
        self.assertEqual(inventory[relative], dict(bytes=path.stat().st_size,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        with self.assertRaisesRegex(ValueError,
                r'original-qt-base-fast-derived-mismatch:.*pitch.*data/base/.*:pitch:.*maxULP=1'):
            fixture.export()
        self.assertFalse(fixture.output.exists())
        self.assertFalse(any(self.root.rglob('catalog.json')))


if __name__ == '__main__':
    unittest.main()
