"""Explicit-domain arbitration contracts; fake solver results are not pixel QA."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from client_live_runtime import _AlignmentHost
from nightmare_candidate import visible_map_component, visible_map_components


class ExplicitComponentAlignmentTests(unittest.TestCase):
    def setUp(self):
        self.frame = np.zeros((720, 1280, 3), np.uint8)
        reference = SimpleNamespace(clean_mask=np.ones((100, 200), np.uint8))
        engine = SimpleNamespace(pack=object(), qt_adapters=object(),
            qt_shared_provider=lambda *args: reference)
        self.host = _AlignmentHost(engine)
        self.bounds = (0, 0, 1280, 720)
        self.islands = [(self.frame[10:40, 10:40], (10, 10, 40, 40), dict(area=3000)),
                        (self.frame[70:110, 80:120], (80, 70, 120, 110), dict(area=2000))]

    def fit(self, outcomes, *, islands=None, cancelled=lambda: False):
        def geometry(frame, **kwargs):
            visible = kwargs.get('visible_override')
            return dict(query='independent', observationBounds=visible[1]) if visible is not None else None
        with patch('nightmare_candidate.visible_map_components', return_value=self.islands if islands is None else islands), \
             patch('single_map_structure.current_geometry', side_effect=geometry), \
             patch('single_map_structure.observed_corner_chains', return_value=[]), \
             patch('single_map_structure.marker_occluded_corner_proposals', return_value=[]), \
             patch.object(self.host, '_fit_observation', side_effect=outcomes) as solver:
            result = self.host.fit(self.frame, 'map', '-1', cancelled, observation_bounds=self.bounds)
            return result, solver.call_count

    @staticmethod
    def pose(tx=0, scale=1):
        return dict(success=True, reason='test-verified', matrixSourceToClient=np.array(
            [[scale, 0, tx], [0, scale, 0], [0, 0, 1]], dtype=float))

    def test_legacy_largest_wrapper_and_default_domain_stay_the_same(self):
        self.frame[200:270, 300:400] = 90
        self.frame[300:350, 500:550] = 90
        # Larger exterior scenery is not inside the legacy canvas.
        self.frame[520:710, :190] = 90
        rows = visible_map_components(self.frame)
        chosen = visible_map_component(self.frame)
        self.assertEqual(chosen[1], rows[0][1])
        self.assertEqual(chosen[2]['area'], 7000)
        self.assertEqual(visible_map_components(self.frame, observation_bounds=self.bounds)[0][2]['area'], 36100)

    def test_implicit_domain_never_enumerates(self):
        with patch('nightmare_candidate.visible_map_components', side_effect=AssertionError('changed legacy path')), \
             patch.object(self.host, '_fit_observation', return_value=dict(success=False, reason='legacy')) as solver:
            self.assertEqual(self.host.fit(self.frame, 'map', '1', lambda: False)['reason'], 'legacy')
            self.assertEqual(solver.call_count, 1)

    def test_failed_large_scenery_does_not_hide_smaller_map(self):
        result, count = self.fit([dict(success=False, reason='no-geometry'), self.pose()])
        self.assertTrue(result['success'])
        self.assertEqual(count, 2)
        self.assertEqual(result['acceptedPoseCount'], 1)

    def test_original_structure_bearing_failure_is_not_bypassed(self):
        with patch('single_map_structure.current_geometry', return_value=dict(query='real-observation')), \
             patch('single_map_structure.observed_corner_chains', return_value=[object()]), \
             patch('nightmare_candidate.visible_map_components', side_effect=AssertionError('bypassed structural rejection')), \
             patch.object(self.host, '_fit_observation', return_value=dict(success=False, reason='geometry-rejected')):
            result = self.host.fit(self.frame, 'map', '-1', lambda: False, observation_bounds=self.bounds)
            self.assertEqual(result['reason'], 'geometry-rejected')

    def test_original_wall_only_success_and_unresolved_result_are_preserved(self):
        for initial in (self.pose(), dict(success=False, reason='initial-structure-ambiguous'),
                        dict(success=False, reason='initial-structure-validation-budget-unresolved')):
            with self.subTest(initial=initial['reason']), \
                 patch('single_map_structure.current_geometry', return_value=dict(query='wall-only')), \
                 patch('single_map_structure.observed_corner_chains', return_value=[]), \
                 patch('nightmare_candidate.visible_map_components', side_effect=AssertionError('changed initial result')), \
                 patch.object(self.host, '_fit_observation', return_value=initial):
                result = self.host.fit(self.frame, 'map', '-1', lambda: False, observation_bounds=self.bounds)
                self.assertIs(result, initial)

    def test_small_matching_island_cannot_hide_an_independent_structural_conflict(self):
        def geometry(frame, **kwargs):
            visible = kwargs.get('visible_override')
            return dict(query='independent', observationBounds=visible[1]) if visible is not None else None
        with patch('nightmare_candidate.visible_map_components', return_value=self.islands), \
             patch('single_map_structure.current_geometry', side_effect=geometry), \
             patch('single_map_structure.observed_corner_chains', return_value=[object()]), \
             patch.object(self.host, '_fit_observation', side_effect=[dict(success=False, reason='geometry-rejected'), self.pose()]), \
             patch.object(self.host, '_component_pose_consistent', return_value=False) as check:
            result = self.host.fit(self.frame, 'map', '-1', lambda: False, observation_bounds=self.bounds)
            self.assertFalse(result['success'])
            self.assertEqual(result['reason'], 'manual-components-inconsistent')
            check.assert_called_once()

    def test_cancellation_during_final_peer_check_cannot_publish_success(self):
        cancelled = [False]
        def geometry(frame, **kwargs):
            visible = kwargs.get('visible_override')
            return dict(query='independent', observationBounds=visible[1]) if visible is not None else None
        def check(*args):
            cancelled[0] = True
            return True
        with patch('nightmare_candidate.visible_map_components', return_value=self.islands), \
             patch('single_map_structure.current_geometry', side_effect=geometry), \
             patch('single_map_structure.observed_corner_chains', return_value=[object()]), \
             patch.object(self.host, '_fit_observation', side_effect=[dict(success=False, reason='geometry-rejected'), self.pose()]), \
             patch.object(self.host, '_component_pose_consistent', side_effect=check):
            result = self.host.fit(self.frame, 'map', '-1', lambda: cancelled[0], observation_bounds=self.bounds)
            self.assertFalse(result['success'])
            self.assertEqual(result['reason'], 'structure-work-obsolete')

    def test_equivalent_pose_on_two_islands_is_one_hypothesis(self):
        result, count = self.fit([self.pose(), self.pose(1)])
        self.assertTrue(result['success'])
        self.assertEqual(count, 2)

    def test_distinct_pose_is_not_hidden_by_first_success(self):
        result, count = self.fit([self.pose(), self.pose(20)])
        self.assertFalse(result['success'])
        self.assertEqual(result['reason'], 'manual-component-pose-ambiguous')
        self.assertEqual(result['acceptedPoseCount'], 2)
        self.assertEqual(count, 2)

    def test_scale_difference_is_compared_over_source_extent(self):
        result, _ = self.fit([self.pose(), self.pose(scale=1.05)])
        self.assertFalse(result['success'])

    def test_unresolved_component_cannot_be_hidden_by_a_success(self):
        for reason in ('initial-structure-ambiguous', 'initial-structure-validation-budget-unresolved'):
            with self.subTest(reason=reason):
                result, count = self.fit([self.pose(), dict(success=False, reason=reason)])
                self.assertFalse(result['success'])
                self.assertTrue(result['unresolved'])
                self.assertEqual(count, 2)

    def test_budget_overflow_does_not_truncate_to_unique_success(self):
        result, count = self.fit([], islands=self.islands * 17)
        self.assertFalse(result['success'])
        self.assertEqual(result['reason'], 'manual-component-budget-unresolved')
        self.assertEqual(count, 0)

    def test_cancellation_after_original_success_cannot_publish_success(self):
        cancelled = [False]
        def original_fit(*args, **kwargs):
            cancelled[0] = True
            return self.pose()
        with patch('single_map_structure.current_geometry', return_value=dict(query='original')), \
             patch.object(self.host, '_fit_observation', side_effect=original_fit), \
             patch('nightmare_candidate.visible_map_components', side_effect=AssertionError('cancelled fallback')):
            result = self.host.fit(self.frame, 'map', '-1', lambda: cancelled[0], observation_bounds=self.bounds)
        self.assertFalse(result['success'])
        self.assertEqual(result['reason'], 'structure-work-obsolete')

    def test_peer_regional_check_preserves_original_unknown_mask_semantics(self):
        import cv2
        from alignment_observation import canonical_residual_evidence
        source = np.zeros((180, 300), np.uint8)
        source[30:120, 30:110] = 1
        query = source.copy()
        query[40:110, 180:250] = 1
        unknown = np.zeros_like(source)
        unknown[:, 150:] = 1
        frame = np.zeros((*source.shape, 3), np.uint8)
        context = dict(query=query, observed=cv2.morphologyEx(query, cv2.MORPH_GRADIENT,
            np.ones((3, 3), np.uint8)) > 0)
        reference = SimpleNamespace(clean_mask=source, unknown_mask=unknown)
        residual = canonical_residual_evidence(dict(verified=True),
            basis='game-rendered-shared-structure', coordinate_pack_id='synthetic')
        with patch.object(self.host, '_rendered_profile_current_frame_residual', return_value=residual):
            self.assertTrue(self.host._component_pose_consistent(frame, reference, np.eye(3), context))
            reference.unknown_mask = None
            self.assertFalse(self.host._component_pose_consistent(frame, reference, np.eye(3), context))

    def test_cancellation_never_returns_previous_component_success(self):
        calls = iter([False, True])
        result, count = self.fit([self.pose()], cancelled=lambda: next(calls, True))
        self.assertFalse(result['success'])
        self.assertEqual(result['reason'], 'structure-work-obsolete')
        self.assertEqual(count, 1)


if __name__ == '__main__':
    unittest.main()
