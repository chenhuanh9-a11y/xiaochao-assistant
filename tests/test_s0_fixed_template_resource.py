"""Installed references are read-only data, independent of CPU rendering paths."""
from io import BytesIO
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

import s0_floor_templates as templates
from export_native_base_package import _controls_payloads


class FixedTemplateResourceTests(unittest.TestCase):
    def tearDown(self):
        templates._bank.cache_clear()

    def test_export_and_client_read_same_bank_without_rendering_or_normalizing(self):
        optimized = cv2.useOptimized()
        banks = []
        try:
            for setting in (False, True):
                cv2.setUseOptimized(setting)
                templates._bank.cache_clear()
                with patch.object(templates, 'make_template', side_effect=AssertionError('runtime generation')), \
                        patch.object(cv2, 'putText', side_effect=AssertionError('runtime rendering')), \
                        patch.object(np.linalg, 'norm', side_effect=AssertionError('runtime normalization')):
                    bank, labels = templates._bank()
                    for mode in ('nightmare', 'difficult'):
                        payload = _controls_payloads(mode)
                        exported = np.load(BytesIO(payload['templates.npy']), allow_pickle=False)
                        np.testing.assert_array_equal(exported, bank)
                    self.assertEqual(bank.dtype, np.dtype('float32'))
                    self.assertEqual(bank.shape, (720, 6144))
                    self.assertFalse(bank.flags.writeable)
                    self.assertFalse(labels.flags.writeable)
                    banks.append(bank)
            np.testing.assert_array_equal(*banks)
        finally:
            cv2.setUseOptimized(optimized)

    def test_separate_cpu_dispatch_processes_export_identical_numerical_bytes(self):
        code = """
import sys
from pathlib import Path
import cv2
cv2.setUseOptimized(sys.argv[1] == 'optimized')
from export_native_base_package import _controls_payloads
Path(sys.argv[2]).write_bytes(_controls_payloads('difficult')['templates.npy'])
"""
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            paths = []
            for setting in ('scalar', 'optimized'):
                env = dict(os.environ, PYTHONPATH=os.pathsep.join(
                    str(root / name) for name in ('app', 'tools')))
                if setting == 'scalar':
                    env['OPENCV_CPU_DISABLE'] = 'AVX2,FMA3'
                    env['NPY_DISABLE_CPU_FEATURES'] = 'AVX2,FMA3'
                path = Path(temporary) / (setting + '.npy')
                subprocess.run([sys.executable, '-B', '-c', code, setting, str(path)],
                               cwd=root, env=env, check=True, capture_output=True, timeout=30)
                paths.append(path)
            self.assertEqual(paths[0].read_bytes(), paths[1].read_bytes())

    def test_frozen_and_source_load_same_fixed_asset(self):
        source = Path(templates.__file__).parent / 'control-data' / 's0-floor-templates-v1.npz'
        expected, expected_labels = templates._bank()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / 'control-data'
            directory.mkdir()
            (directory / source.name).write_bytes(source.read_bytes())
            with patch.object(sys, 'frozen', True, create=True), \
                    patch.object(sys, '_MEIPASS', temporary, create=True):
                templates._bank.cache_clear()
                actual, labels = templates._bank()
                np.testing.assert_array_equal(actual, expected)
                np.testing.assert_array_equal(labels, expected_labels)

    def test_missing_or_malformed_program_asset_fails_without_fallback(self):
        bank, labels = templates._bank()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / 'control-data'
            directory.mkdir()
            path = directory / 's0-floor-templates-v1.npz'
            with patch.object(sys, 'frozen', True, create=True), \
                    patch.object(sys, '_MEIPASS', temporary, create=True), \
                    patch.object(templates, 'make_template', side_effect=AssertionError('fallback')):
                templates._bank.cache_clear()
                with self.assertRaises(FileNotFoundError):
                    templates._bank()
                for invalid in (bank.astype(np.float64), bank[:, :-1]):
                    np.savez_compressed(path, vectors=invalid, labels=labels, allow_pickle=False)
                    with self.assertRaisesRegex(ValueError, 's0-template-resource-invalid'):
                        templates._bank()


if __name__ == '__main__':
    unittest.main()
