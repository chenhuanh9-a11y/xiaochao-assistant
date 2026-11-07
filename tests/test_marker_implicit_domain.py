"""Real pixel regressions for the S0/default-ROI marker recovery consumer.

Only provider/index loading and explicit cancellation timing are substitutes.
The separate review scripts stay unchanged; these fixtures retain their maps,
translations, areas and conflict, and add domain-boundary controls.
"""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from client_live_runtime import _AlignmentHost
from fast_structure_alignment import prepare
from nightmare_candidate import visible_map_components
from shared_structure_reference import SharedStructureReference
from single_map_structure import current_geometry, current_part
from test_marker_occluded_structure import asymmetric_structure


FULL = (0, 0, 1800, 1200)
DEFAULT = (288, 264, 1368, 960)
TRUTH = np.array([[1., 0, 300], [0, 1., 250], [0, 0, 1.]])


def render(mask, matrix, size=(1800, 1200), *, marker=True):
    gray = cv2.warpAffine(mask*90, np.asarray(matrix)[:2], size)
    frame = np.repeat(gray[:, :, None], 3, axis=2)
    bounds = (0, 0, size[0], size[1])
    if marker:
        for island in visible_map_components(frame, observation_bounds=bounds):
            context = current_geometry(frame, observation_bounds=bounds, visible_override=island)
            for node in current_part(frame, context, 'nodes'):
                x, y = np.rint(node['point']).astype(int)
                frame[y, x] = (0, 0, 255)
    return frame


def scene(*, conflict=False):
    source = np.zeros((650, 950), np.uint8)
    source[:, :650] = asymmetric_structure()
    source[140:410, 710:870] = 1
    source[185:230, 745:810] = 0
    source[300:365, 785:835] = 0
    frame = render(source, TRUTH)
    if conflict:
        frame[:, 980:] = cv2.warpAffine(frame[:, 980:],
            np.array([[1., 0, 25], [0, 1., 0]]), (820, 1200))
    return frame, source


def host_for(source, mode='nightmare', unknown=None):
    reference = SharedStructureReference(source, source.copy(), {}, Path('synthetic-implicit/reference.npz'),
        unknown_mask=unknown)
    return _AlignmentHost(SimpleNamespace(pack=SimpleNamespace(mode=mode),
        qt_adapters=SimpleNamespace(canonical_pack_id='synthetic-implicit'),
        qt_shared_provider=lambda *args: reference))


def fit(frame, source, *, mode='nightmare', indexed=False, bounds=None, cached=False,
        unknown=None, cancelled=lambda: False, host=None):
    host = host or host_for(source, mode, unknown)
    with patch('fast_structure_alignment.load_prepared', return_value=prepare(source) if indexed else None):
        if cached:
            assert bounds is not None  # The production implicit entry has no scan cache.
            return host.prepare_structure_scan(frame, bounds)(frame, 'synthetic', '1', cancelled,
                observation_bounds=bounds)
        return host.fit(frame, 'synthetic', '1', cancelled, observation_bounds=bounds)


class MarkerImplicitDomainTests(unittest.TestCase):
    def test_reviewer_conflict_and_positive_across_both_modes_and_domains(self):
        expected = {
            False: np.array([[1.000865659796853, 0, 299.7515759230137],
                             [0, 1.000865659796853, 249.7033421398938], [0, 0, 1]]),
            True: np.array([[1.0032920338222429, 0, 298.1046447753906],
                            [0, 1.0032920338222429, 248.56365966796875], [0, 0, 1]]),
        }
        for conflict in (False, True):
            frame, source = scene(conflict=conflict)
            islands = visible_map_components(frame)
            self.assertEqual([i[2]['area'] for i in islands], [152426, 37037])
            self.assertEqual(islands[0][1], (366, 306, 819, 779))
            self.assertEqual(islands[1][1], (1026, 381, 1204, 669) if conflict else (1001, 381, 1179, 669))
            for mode in ('nightmare', 'difficult'):
                for indexed in (False, True):
                    for bounds, cached in ((None, False), (DEFAULT, False), (DEFAULT, True),
                                           (FULL, False), (FULL, True)):
                        with self.subTest(conflict=conflict, mode=mode, indexed=indexed, bounds=bounds, cached=cached):
                            result = fit(frame, source, mode=mode, indexed=indexed, bounds=bounds, cached=cached)
                            if conflict:
                                self.assertFalse(result['success'])
                                self.assertEqual(result['reason'], 'manual-components-inconsistent')
                            else:
                                self.assertTrue(result['success'], result['reason'])
                                np.testing.assert_allclose(result['matrixSourceToClient'], expected[indexed], atol=1e-6)

    def test_outside_default_roi_is_not_a_peer_but_full_frame_can_reject_it(self):
        for edge_halo in (False, True):
            frame, source = scene()
            if edge_halo:
                # This right wall lies 4px outside the ROI but inside the
                # component's8px padded image. It must remain unknown in S0.
                frame[700:830, 1300:1372] = 90
                self.assertEqual(len(visible_map_components(frame)), 3)
            else:
                frame[400:600, 1450:1650] = 90
                self.assertEqual(len(visible_map_components(frame)), 2)
            for mode in ('nightmare', 'difficult'):
                for indexed in (False, True):
                    for bounds, cached in ((None, False), (DEFAULT, True), (FULL, True)):
                        with self.subTest(edge_halo=edge_halo, mode=mode, indexed=indexed, bounds=bounds):
                            result = fit(frame, source, mode=mode, indexed=indexed, bounds=bounds, cached=cached)
                            self.assertEqual(result['success'], bounds != FULL, result['reason'])
                            if bounds == FULL:
                                self.assertEqual(result['reason'], 'manual-components-inconsistent')

    def test_clean_secondary_cannot_hide_marker_primary_conflict(self):
        source = asymmetric_structure()
        wrong = source.copy()
        wrong[228:339, 276:327] = 1
        wrong[248:359, 302:353] = 0
        wrong[390:445, 190:279] = 1
        wrong[375:430, 170:259] = 0
        first = render(wrong, [[1.,0,210],[0,1.,130]], size=(2000,1000))
        second = render(source, [[.75,0,1010],[0,.75,130]], size=(2000,1000), marker=False)
        frame = np.maximum(first, second)
        bounds = (0,0,2000,1000)
        self.assertEqual([i[2]['area'] for i in visible_map_components(frame, observation_bounds=bounds)], [152426, 84753])
        for mode in ('nightmare', 'difficult'):
            for indexed in (False, True):
                for cached in (False, True):
                    with self.subTest(mode=mode, indexed=indexed, cached=cached):
                        result = fit(frame, source, mode=mode, indexed=indexed, bounds=bounds, cached=cached)
                        self.assertFalse(result['success'])
                        self.assertEqual(result['reason'], 'manual-components-inconsistent')

    def test_weak_marker_primary_does_not_veto_clean_secondary(self):
        source = asymmetric_structure()
        frame = render(source, [[.75,0,1010],[0,.75,130]], size=(2000,1000), marker=False)
        frame[30:560, 30:470] = 90
        frame[550:560, 30:470] = (0,0,255)
        bounds = (0,0,2000,1000)
        islands = visible_map_components(frame, observation_bounds=bounds)
        context = current_geometry(frame, observation_bounds=bounds, visible_override=islands[0])
        for node in current_part(frame, context, 'nodes'):
            x,y = np.rint(node['point']).astype(int)
            frame[y,x] = (0,0,255)
        context = current_geometry(frame, observation_bounds=bounds)
        self.assertEqual(len(current_part(frame, context, 'lines')), 3)
        for mode in ('nightmare', 'difficult'):
            for indexed in (False, True):
                for cached in (False, True):
                    with self.subTest(mode=mode, indexed=indexed, cached=cached):
                        result = fit(frame, source, mode=mode, indexed=indexed, bounds=bounds, cached=cached)
                        self.assertTrue(result['success'], result['reason'])
                        self.assertTrue(result['manualComponentFallback'])
                        self.assertFalse(result.get('markerOccludedWallProposal', False))

    def test_source_unknown_and_three_wall_peer_stay_unknown_in_implicit_domain(self):
        for kind in ('source-unknown', 'three-wall'):
            frame, source = scene(conflict=kind=='source-unknown')
            unknown = np.zeros_like(source) if kind == 'source-unknown' else None
            if unknown is not None:
                unknown[:,650:] = 1
            else:
                frame[700:850, 1000:1200] = 90
                frame[843:850, 1000:1200] = (0,0,255)
            for mode in ('nightmare', 'difficult'):
                for indexed in (False, True):
                    with self.subTest(kind=kind, mode=mode, indexed=indexed):
                        result = fit(frame, source, mode=mode, indexed=indexed, unknown=unknown)
                        self.assertTrue(result['success'], result['reason'])

    def test_implicit_repeated_and_wrong_reference_remain_rejected(self):
        frame, source = scene()
        wrong = source.copy()
        wrong[228:339,276:327] = 1
        wrong[248:359,302:353] = 0
        wrong[390:445,190:279] = 1
        wrong[375:430,170:259] = 0
        for reference in (np.concatenate((source, source), axis=1), wrong):
            for mode in ('nightmare', 'difficult'):
                for indexed in (False, True):
                    with self.subTest(repeated=reference.shape[1]>950, mode=mode, indexed=indexed):
                        result = fit(frame, reference, mode=mode, indexed=indexed)
                        self.assertFalse(result['success'])
                        if reference.shape[1]>950:
                            self.assertIn('ambiguous', result['reason'])

    def test_implicit_guard_cancellation_after_peer_validation_does_not_publish(self):
        frame, source = scene()
        host = host_for(source)
        cancelled = [False]
        check = host._component_pose_consistent
        def checked(*args):
            result = check(*args)
            cancelled[0] = True
            return result
        with patch.object(host, '_component_pose_consistent', side_effect=checked):
            result = fit(frame, source, host=host, cancelled=lambda:cancelled[0])
        self.assertFalse(result['success'])
        self.assertEqual(result['reason'], 'structure-work-obsolete')

    def test_implicit_component_budget_is_fail_closed(self):
        frame, source = scene()
        for i in range(33):
            x, y = 900+(i%6)*65, 700+(i//6)*40
            frame[y:y+27,x:x+45] = 90
        self.assertGreater(len(visible_map_components(frame)), 32)
        for indexed in (False, True):
            host = host_for(source)
            with patch.object(host, '_component_pose_consistent', side_effect=AssertionError('budget must stop first')):
                result = fit(frame, source, indexed=indexed, host=host)
            self.assertFalse(result['success'])
            self.assertEqual(result['reason'], 'manual-component-budget-unresolved')

    def test_nonmarker_implicit_paths_keep_original_order_and_do_not_scan_peers(self):
        frame, source = scene()
        host = host_for(source)
        for result in (dict(success=False, reason='unchanged'),
                       dict(success=True, reason='unchanged', matrixSourceToClient=np.eye(3))):
            with patch.object(host, '_fit_observation', return_value=result) as solver, \
                 patch.object(host, '_marker_recovery_peer_rejection', side_effect=AssertionError('broadened legacy path')):
                self.assertIs(host.fit(frame, 'synthetic', '1', lambda:False, {'entry':'unchanged'}), result)
                self.assertEqual(solver.call_args.args[4], {'entry':'unchanged'})
                self.assertEqual(solver.call_args.kwargs, {})


if __name__ == '__main__':
    unittest.main()
