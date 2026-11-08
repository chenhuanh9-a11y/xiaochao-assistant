"""Display-only affine contracts; no claim of visual/game registration accuracy."""
import copy
from types import SimpleNamespace
import unittest

import numpy as np
import test_generic_author_package as fixtures
import generic_author_package as generic
import s1_author_package as legacy
import star_map_import as importer
from s1_author_displays import S1AuthorDisplays


class GenericAffineTests(unittest.TestCase):
    def test_accepts_bounded_anisotropy_shear_rotation_without_rewriting(self):
        matrices = [
            [[1.0999, 0, 3], [0, 1, -2], [0, 0, 1]],
            [[.9, .12, 0], [.03, 1.05, 0], [0, 0, 1]],
            [[0, -1.0999, 0], [1, 0, 0], [0, 0, 1]],
            [[.01, 0, 0], [0, .01, 0], [0, 0, 1]],
            [[100, 0, 0], [0, 100, 0], [0, 0, 1]],
        ]
        for matrix in matrices:
            before = copy.deepcopy(matrix)
            generic.validate_display_affine(matrix)
            self.assertEqual(matrix, before)

    def test_rejects_projective_reflected_unbounded_and_ill_conditioned(self):
        matrices = [
            [[1, 0, 0], [0, 1, 0], [.001, 0, 1]],
            [[-1, 0, 0], [0, 1, 0], [0, 0, 1]],
            [[1, 0, 0], [0, 0, 0], [0, 0, 1]],
            [[1, 100000, 0], [0, 1, 0], [0, 0, 1]], # det=1, unstable
            [[.00001, 0, 0], [0, 100000, 0], [0, 0, 1]], # det=1
            [[101, 0, 0], [0, .01, 0], [0, 0, 1]],
            [[1, 0, 100001], [0, 1, 0], [0, 0, 1]],
            [[float('nan'), 0, 0], [0, 1, 0], [0, 0, 1]],
            [[1, 0, 0], [0, float('inf'), 0], [0, 0, 1]],
            [[True, 0, 0], [0, 1, 0], [0, 0, 1]],
            [[10 ** 1000, 0, 0], [0, 1, 0], [0, 0, 1]],
        ]
        for matrix in matrices:
            with self.subTest(matrix=matrix), self.assertRaises(importer.ImportRejected):
                generic.validate_display_affine(matrix)

    def test_legacy_s1_approval_model_not_widened(self):
        matrix = [[1.0999, 0, 3], [0, 1, -2], [0, 0, 1]]
        with self.assertRaises(importer.ImportRejected): legacy._matrix(matrix)
        generic.validate_display_affine(matrix)

    def test_install_and_real_display_adapter_preserve_affine_and_geometry(self):
        fixture = fixtures.GenericPackageTests('runTest')
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        matrix = [[1.0999, .07, 3], [.02, .98, -2], [0, 0, 1]]
        archive = fixture.package(mutate=lambda doc: doc['layers'][0].update(matrixDisplayToReference=matrix))
        fixture.install(archive)
        native = SimpleNamespace(root=fixture.root/'_internal', mode='nightmare',
            catalog=SimpleNamespace(profiles={}, _raw={'profiles': []}, default_profile_id='shared-structure'),
            adapters=SimpleNamespace(profiles={}))
        display = S1AuthorDisplays(fixture.root/'_internal', native, season='s0')
        actual = display.adapters.matrix_profile_to_canonical(fixture.profile, 'map', 'B1')
        np.testing.assert_array_equal(actual, matrix)
        self.assertFalse(actual.flags.writeable)
        canonical = np.array([[.6, -.1, 30], [.1, .6, 40], [0, 0, 1]])
        original = canonical.copy()
        composed = display.adapters.compose_profile_to_client_from_canonical(fixture.profile, 'map', 'B1', canonical)
        np.testing.assert_array_equal(composed, canonical @ np.asarray(matrix))
        np.testing.assert_array_equal(canonical, original)
        self.assertEqual(display.adapters.profiles[fixture.profile]['role'], 'display-only')
        self.assertEqual(display.pixels(display.binding(fixture.profile, 'map', 'B1')['path'], 'aligned-overlay').shape, (8,9,4))
        for dependency in fixture.deps:
            self.assertEqual(importer._sha(fixture.root/dependency['path']), dependency['sha256'])


if __name__ == '__main__': unittest.main()
