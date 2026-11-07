"""Exact within-fit reuse; generated pixels, not live-game timing evidence."""
import copy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np
from client_live_runtime import _AlignmentHost
from shared_structure_reference import SharedStructureReference
from test_marker_occluded_structure import asymmetric_structure, render
from fast_structure_alignment import prepare
import nightmare_candidate


class ObservationResidualReuse(unittest.TestCase):
    def setUp(self):
        self.source = asymmetric_structure()
        self.frame, self.truth = render(self.source)
        self.reference = SharedStructureReference(self.source, self.source.copy(), {}, Path('synthetic/reference.npz'))
        self.host = _AlignmentHost(SimpleNamespace(pack=SimpleNamespace(mode='nightmare'),
            qt_adapters=SimpleNamespace(canonical_pack_id='synthetic'),
            qt_shared_provider=lambda *args: self.reference),
            pose_priors={('synthetic', '-3'): self.truth})
        self.host.map_id, self.host.floor = 'synthetic', '-3'

    def residual(self, **kwargs):
        return self.host._rendered_profile_current_frame_residual(
            self.frame, self.source, self.truth, **kwargs)

    def test_full_solver_reuses_one_kernel_and_next_frame_recomputes(self):
        original = nightmare_candidate.selected_route_current_frame_residual
        prepared = prepare(self.source)
        with patch('fast_structure_alignment.load_prepared', return_value=prepared), patch(
                'nightmare_candidate.selected_route_current_frame_residual', wraps=original) as kernel:
            for count in (1, 2):
                fit = self.host.fit(self.frame, 'synthetic', '-3', lambda: False,
                    observation_bounds=(0, 0, 1200, 1000))
                self.assertTrue(fit['success'], fit['reason'])
                self.assertEqual(kernel.call_count, count)
                self.assertIsNone(self.host._observation_residual_cache)

    def test_local_room_additions_preserve_pose_without_relaxation(self):
        from test_marker_occluded_structure import run_fit
        for width in (8, 16, 32, 64):
            observed = self.source.copy()
            observed[180:220, 510:510+width] = 1
            frame, truth = render(observed)
            for mode in ('difficult', 'nightmare'):
                for indexed in (False, True):
                    with self.subTest(width=width, mode=mode, indexed=indexed):
                        result = run_fit(frame, self.source, mode=mode, indexed=indexed)
                        self.assertTrue(result['success'], result['reason'])
                        corners = np.array([[0, 0, 1], [650, 0, 1], [0, 650, 1], [650, 650, 1]])
                        error = np.linalg.norm((corners @ (result['matrixSourceToClient']-truth).T)[:, :2], axis=1).max()
                        self.assertLess(error, 2.)


    def test_direct_calls_are_not_cached(self):
        with patch('nightmare_candidate.selected_route_current_frame_residual', return_value={'verified': False}) as kernel:
            self.residual(); self.residual()
            self.assertEqual(kernel.call_count, 2)

    def test_exact_key_and_consumer_isolation(self):
        self.host._observation_residual_cache = {}
        with patch('nightmare_candidate.selected_route_current_frame_residual', return_value={'verified': False, 'detail': [1]}) as kernel:
            one = self.residual(); one['detail'].append(2)
            self.assertEqual(self.residual()['detail'], [1])
            self.assertEqual(kernel.call_count, 1)
            self.truth[0, 2] += .001; self.residual()
            self.assertEqual(kernel.call_count, 2)
            self.residual(maximum_dimension=800)
            self.assertEqual(kernel.call_count, 3)
            self.assertLessEqual(len(self.host._observation_residual_cache), 2)
            self.frame = self.frame.copy(); self.residual(maximum_dimension=800)
            self.assertEqual(kernel.call_count, 4)
            self.reference = copy.copy(self.reference); self.residual(maximum_dimension=800)
            self.assertEqual(kernel.call_count, 5)
            self.residual(maximum_dimension=800, visible_override=(None, (0, 0, 1, 1), {}))
            self.assertEqual(kernel.call_count, 6)
            self.host.floor = '-2'; self.residual(maximum_dimension=800)
            self.assertEqual(kernel.call_count, 7)

    def test_exception_and_cancel_do_not_retain_cache(self):
        with patch.object(self.host, '_fit_observation_uncached', side_effect=ValueError('fixture')):
            with self.assertRaises(ValueError):
                self.host._fit_observation(self.frame, 'synthetic', '-3', lambda: False)
        self.assertIsNone(self.host._observation_residual_cache)
        with patch('fast_structure_alignment.load_prepared', return_value=prepare(self.source)):
            result = self.host.fit(self.frame, 'synthetic', '-3', lambda: True,
                observation_bounds=(0, 0, 1200, 1000))
        self.assertFalse(result['success'])
        self.assertIsNone(self.host._observation_residual_cache)


if __name__ == '__main__':
    unittest.main()
