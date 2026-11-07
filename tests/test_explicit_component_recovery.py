"""Generated pixels exercise real extraction/solvers; not a game accuracy test.

Only the resource provider and prepared-index loader are synthetic. Geometry,
candidate generation, regional checks and final canonical residuals are real.
No captured frames, player information or production assets are distributed.
"""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from client_live_runtime import _AlignmentHost
from fast_structure_alignment import prepare
from nightmare_candidate import map_color_mask, visible_map_components
from shared_structure_reference import SharedStructureReference
from single_map_structure import (contour_graph, current_geometry, current_part,
                                  observed_corner_chains)


def synthetic_scene(*, wall_only=False, conflicting=False, scenery=False):
    mask = np.zeros((1500, 1400) if scenery else (1000, 1200), np.uint8)
    mask[30:560, 30:470] = 1
    mask[90:480, 570:950] = 1
    for x, y, w, h in ((610, 130, 45, 65), (720, 140, 75, 41),
                       (850, 135, 48, 83), (630, 275, 67, 54),
                       (780, 250, 51, 97), (860, 370, 37, 56)):
        mask[y:y+h, x:x+w] = 0
    gray = mask * 90
    # Soft outer boundaries carry no sharp wall evidence. One sharp notch
    # leaves two corner chains/three walls, insufficient to solve on its own.
    gray[:, :500] = cv2.GaussianBlur(gray[:, :500], (41, 41), 7)
    gray[20:160, 170:290] = 0
    frame = np.repeat(gray[:, :, None], 3, axis=2)
    if wall_only:
        frame[420:570, 330:420] = 0
        # Saturated markers obscure every primary corner, but preserve six
        # sharp wall fragments with separated normals on both axes.
        for node in contour_graph(map_color_mask(frame, live=True)):
            x, y = map(int, node['point'])
            if x < 500:
                frame[max(0, y-6):y+7, max(0, x-6):x+7] = (0, 0, 255)
    reference_mask = map_color_mask(frame, live=True)
    if conflicting:
        reference_mask[29:165, 160:300] = 1  # Same smaller island, wrong primary walls.
    if scenery:
        # Eligible bright component, but outside the map color band. It must
        # not veto the map or prevent the other observations being checked.
        frame[800:1250, 50:750] = 240
    reference = SharedStructureReference(reference_mask, reference_mask.copy(),
        {}, Path('synthetic-component/reference.npz'))
    return frame, reference


def make_host(reference, mode='difficult'):
    return _AlignmentHost(SimpleNamespace(pack=SimpleNamespace(mode=mode),
        qt_adapters=SimpleNamespace(canonical_pack_id='synthetic-component'),
        qt_shared_provider=lambda *args: reference))


class ExplicitComponentPixelRecoveryTests(unittest.TestCase):
    @staticmethod
    def run_fit(host, frame, *, cached=False):
        bounds = (0, 0, frame.shape[1], frame.shape[0])
        fit = host.prepare_structure_scan(frame, bounds) if cached else host.fit
        return fit(frame, 'synthetic', '-3', lambda: False, observation_bounds=bounds)

    def test_failed_primary_chains_do_not_hide_reliable_compatible_secondary(self):
        frame, reference = synthetic_scene()
        bounds = (0, 0, frame.shape[1], frame.shape[0])
        islands = visible_map_components(frame, observation_bounds=bounds)
        self.assertEqual(len(islands), 2)
        primary = current_geometry(frame, observation_bounds=bounds)
        self.assertEqual(len(observed_corner_chains(frame, primary)), 2)
        self.assertEqual(len(current_part(frame, primary, 'lines')), 3)
        extent = np.array([[0, 0, 1], [1200, 0, 1], [0, 1000, 1], [1200, 1000, 1]])
        for mode in ('nightmare', 'difficult'):
            for indexed in (False, True):
                for cached in (False, True):
                    with self.subTest(mode=mode, indexed=indexed, cached=cached), \
                         patch('fast_structure_alignment.load_prepared',
                               return_value=prepare(reference.clean_mask) if indexed else None):
                        host = make_host(reference, mode)
                        initial = host._fit_observation(frame, 'synthetic', '-3', lambda: False,
                            observation_bounds=bounds, _context=primary)
                        self.assertFalse(initial['success'])
                        self.assertEqual(initial['reason'], 'initial-structure-current-geometry-failed')
                        secondary = current_geometry(frame, observation_bounds=bounds, visible_override=islands[1])
                        independent = host._fit_observation(frame, 'synthetic', '-3', lambda: False,
                            observation_bounds=bounds, _context=secondary)
                        self.assertTrue(independent['success'])
                        result = self.run_fit(host, frame, cached=cached)
                        self.assertTrue(result['success'], result['reason'])
                        self.assertTrue(result['manualComponentFallback'])
                        self.assertEqual(result['acceptedPoseCount'], 1)
                        self.assertEqual(tuple(result['observationBounds']), islands[1][1])
                        matrix = np.asarray(result['matrixSourceToClient'])
                        np.testing.assert_allclose(matrix, independent['matrixSourceToClient'])
                        truth_error = np.linalg.norm((extent @ (matrix-np.eye(3)).T)[:, :2], axis=1).max()
                        self.assertLess(truth_error, 5 if indexed else .5)
                        self.assertTrue(host._component_pose_consistent(frame, reference, matrix, primary))

    def test_primary_corner_conflict_cannot_be_hidden_by_secondary_success(self):
        frame, reference = synthetic_scene(conflicting=True)
        for indexed in (False, True):
            for cached in (False, True):
                with self.subTest(indexed=indexed, cached=cached), \
                     patch('fast_structure_alignment.load_prepared',
                           return_value=prepare(reference.clean_mask) if indexed else None):
                    result = self.run_fit(make_host(reference), frame, cached=cached)
                    self.assertFalse(result['success'])
                    self.assertEqual(result['reason'], 'manual-components-inconsistent')
                    self.assertEqual(result['conflictingBounds'], [29, 29, 471, 561])

    def test_current_pose_prior_still_requires_the_same_component_checks(self):
        frame, reference = synthetic_scene()
        for cached in (False, True):
            with self.subTest(cached=cached), \
                 patch('fast_structure_alignment.load_prepared', return_value=prepare(reference.clean_mask)):
                host = make_host(reference)
                host.pose_priors[('synthetic', '-3')] = np.eye(3)
                result = self.run_fit(host, frame, cached=cached)
                self.assertTrue(result['success'], result['reason'])
                self.assertTrue(result['manualComponentFallback'])
                prior_events = [event for event in host.events if event['event'] == 'experimental-pose-prior']
                self.assertEqual([event['accepted'] for event in prior_events], [False, True])
                original = current_geometry(frame, observation_bounds=(0, 0, frame.shape[1], frame.shape[0]))
                self.assertTrue(host._component_pose_consistent(
                    frame, reference, result['matrixSourceToClient'], original))

    def test_wall_only_primary_and_peer_conflicts_are_not_silently_dropped(self):
        for scenery in (False, True):
            for conflicting in (False, True):
                frame, reference = synthetic_scene(wall_only=True, conflicting=conflicting, scenery=scenery)
                bounds = (0, 0, frame.shape[1], frame.shape[0])
                islands = visible_map_components(frame, observation_bounds=bounds)
                wall_island = islands[1 if scenery else 0]
                context = current_geometry(frame, observation_bounds=bounds, visible_override=wall_island)
                self.assertEqual(len(observed_corner_chains(frame, context)), 0)
                self.assertEqual(len(current_part(frame, context, 'lines')), 6)
                for cached in (False, True):
                    with self.subTest(scenery=scenery, conflicting=conflicting, cached=cached), \
                         patch('fast_structure_alignment.load_prepared', return_value=None):
                        result = self.run_fit(make_host(reference), frame, cached=cached)
                        self.assertEqual(result['success'], not conflicting, result['reason'])
                        if conflicting:
                            self.assertEqual(result['reason'], 'manual-components-inconsistent')
                            self.assertEqual(result['conflictingBounds'], list(wall_island[1]))


if __name__ == '__main__':
    unittest.main()
