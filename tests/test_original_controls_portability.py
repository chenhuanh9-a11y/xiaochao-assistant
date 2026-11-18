"""Portable control identity/content checks over real synthetic font banks."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from floor_contract import floor_contract
from floor_control_region import VERTICAL_SEARCH_AREA, AUTO_VERTICAL_SEARCH_AREA
from imported_original_controls import OriginalControlsError, require_original_controls
from s0_floor_templates import _bank, BANK_ID, GENERATOR_VERSION, TEMPLATE_FAMILY


class OriginalControlsPortabilityTests(unittest.TestCase):
    def controls(self, *, mode='nightmare'):
        vectors, labels = _bank()
        return SimpleNamespace(bank_id=BANK_ID, generator_version=GENERATOR_VERSION,
            assert_current=lambda: None,
            for_context=lambda season, requested: SimpleNamespace(contract=floor_contract(requested, season=season)),
            fixed_search_region=VERTICAL_SEARCH_AREA, expanded_search_region=AUTO_VERTICAL_SEARCH_AREA,
            templates=SimpleNamespace(vectors=vectors.copy(), labels=labels.copy(), family=TEMPLATE_FAMILY))

    def qualify(self, controls, mode='nightmare'):
        with patch('s0_floor_templates.make_template', side_effect=AssertionError('runtime generation')):
            return require_original_controls(controls, season='s0', mode=mode)

    def assert_code(self, controls, code):
        with self.assertRaises(OriginalControlsError) as caught:
            self.qualify(controls)
        self.assertEqual(caught.exception.code, 'original-controls-' + code)
        return caught.exception

    def test_real_cross_build_golden_banks_both_qualify(self):
        fixture = Path(__file__).parent / 'fixtures/s0-controls-golden/opencv-4.12.0-numpy-2.2.5.npz'
        controls = self.controls()
        self.assertGreaterEqual(self.qualify(controls)['worstCos'], 1 - 1e-6)
        with np.load(fixture, allow_pickle=False) as alternate:
            controls.templates.vectors = alternate['vectors']
            controls.templates.labels = alternate['labels']
        self.assertEqual(controls.templates.vectors.dtype, np.dtype('float32'))
        for mode in ('nightmare', 'difficult'):
            self.assertGreaterEqual(self.qualify(controls, mode)['worstCos'], 1 - 1e-6)

    def test_cross_build_changed_renderer_is_rejected_as_different_content(self):
        fixture = Path(__file__).parent / 'fixtures/s0-controls-golden/opencv-5.0.0-numpy-2.2.5.npz'
        controls = self.controls()
        with np.load(fixture, allow_pickle=False) as alternate:
            controls.templates.vectors = alternate['vectors']
            controls.templates.labels = alternate['labels']
        self.assert_code(controls, 'template-vector-mismatch')

    def test_immutable_resource_reuses_cosine_but_rechecks_parameters(self):
        controls = self.controls()
        controls.templates.vectors = np.frombuffer(controls.templates.vectors.tobytes(), np.float32).reshape(720, 6144)
        expected = self.qualify(controls)
        with patch('imported_original_controls.np.einsum', side_effect=AssertionError('repeated cosine')):
            self.assertEqual(self.qualify(controls), expected)
            controls.fixed_search_region = (.751, .15, .18, .45)
            self.assert_code(controls, 'fixed-region-mismatch')

    def test_readonly_owned_memory_cannot_hide_later_content_changes(self):
        controls = self.controls()
        controls.templates.vectors.flags.writeable = False
        self.qualify(controls)
        controls.templates.vectors.flags.writeable = True
        controls.templates.vectors[0] *= -1
        self.assert_code(controls, 'template-vector-mismatch')

    def test_random_signed_one_ulp_noise_qualifies_in_both_modes(self):
        controls = self.controls()
        rng = np.random.default_rng(20261114)
        toward = np.where(rng.integers(0, 2, controls.templates.vectors.shape), np.inf, -np.inf).astype(np.float32)
        controls.templates.vectors = np.nextafter(controls.templates.vectors, toward)
        for mode in ('nightmare', 'difficult'):
            self.assertGreaterEqual(self.qualify(controls, mode)['worstCos'], 1 - 1e-6)

    def test_swapped_labels_have_a_specific_failure(self):
        controls = self.controls()
        other = int(np.flatnonzero(controls.templates.labels != controls.templates.labels[0])[0])
        controls.templates.labels[[0, other]] = controls.templates.labels[[other, 0]]
        self.assert_code(controls, 'template-labels-mismatch')

    def test_wrong_family_has_a_specific_failure(self):
        controls = self.controls()
        controls.templates.family = 'different-font'
        self.assert_code(controls, 'template-family-mismatch')

    def test_changed_regions_have_distinct_failures(self):
        for field, code in (('fixed_search_region', 'fixed-region-mismatch'),
                            ('expanded_search_region', 'expanded-region-mismatch')):
            controls = self.controls()
            region = list(getattr(controls, field))
            region[0] += 1e-3
            setattr(controls, field, region)
            self.assert_code(controls, code)

    def test_missing_vector_row_has_shape_failure(self):
        controls = self.controls()
        controls.templates.vectors = controls.templates.vectors[:-1]
        error = self.assert_code(controls, 'template-vector-shape-mismatch')
        self.assertEqual(error.details['shape'], [719, 6144])

    def test_zero_and_nonfinite_rows_are_degenerate(self):
        for value in (0.0, np.nan, np.inf):
            controls = self.controls()
            controls.templates.vectors[0] = value
            self.assert_code(controls, 'template-vector-degenerate')

    def test_changed_glyph_keeps_numeric_diagnostic_evidence(self):
        controls = self.controls()
        row = 42
        glyph = controls.templates.vectors[row]
        changed = glyph + np.float32(.02) * np.roll(glyph, 97)
        changed -= changed.mean()
        changed /= np.linalg.norm(changed)
        controls.templates.vectors[row] = changed
        error = self.assert_code(controls, 'template-vector-mismatch')
        self.assertEqual(error.details['worstRowIndex'], row)
        self.assertLess(error.details['worstCos'], 1 - 1e-6)
        self.assertGreater(error.details['maxAbsDiff'], 0)
        self.assertEqual(error.details['bankId'], BANK_ID)

    def test_wrong_identity_is_version_incompatibility(self):
        controls = self.controls()
        controls.bank_id = '0' * 32
        self.assert_code(controls, 'bank-version-mismatch')
        controls = self.controls()
        controls.generator_version = 'future-generator-v2'
        self.assert_code(controls, 'generator-version-mismatch')

    def test_legacy_identity_still_requires_content_equivalence(self):
        controls = self.controls()
        controls.bank_id = controls.generator_version = None
        self.qualify(controls)
        controls.templates.vectors[0] = -controls.templates.vectors[0]
        self.assert_code(controls, 'template-vector-mismatch')


if __name__ == '__main__':
    unittest.main()
