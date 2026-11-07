"""Scan-only cache contracts; synthetic observations are not live pixel QA."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from client_live_runtime import _AlignmentHost
from shared_structure_reference import _binary
from single_map_structure import observed_corner_chains


class ScanObservationReuseTests(unittest.TestCase):
    def setUp(self):
        self.frame = np.zeros((60, 80, 3), np.uint8)
        self.bounds = (0, 0, 80, 60)
        self.host = _AlignmentHost(SimpleNamespace(pack=object(), qt_adapters=object()))

    def test_binary_membership_matches_old_kernel_for_every_uint8_value(self):
        for v in range(256):
            actual = _binary(np.full((3, 5), v, np.uint8))
            self.assertEqual(actual is not None, v in (0, 1, 255))
            if actual is not None:
                np.testing.assert_array_equal(actual, np.full((3, 5), int(v != 0), np.uint8))
        for a in (np.zeros((2,), np.uint8), np.zeros((2, 2), np.float32)):
            self.assertIsNone(_binary(a))
        self.assertIsNone(_binary(np.zeros((2, 2), np.uint8), (3, 3)))
        self.assertEqual(_binary(np.empty((0, 0), np.uint8)).shape, (0, 0))

    def test_observation_is_lazy_and_candidate_contexts_do_not_share_projections(self):
        query = np.zeros((60, 80), np.uint8)
        contexts = []
        def fit(*args, **kwargs):
            context = kwargs['_context']
            self.assertNotIn('_projection', context)
            self.assertNotIn('candidate_verdict', context)
            contexts.append(context)
            context['_projection'] = object()
            context['candidate_verdict'] = args[1]
            return dict(success=True, reason='synthetic')
        with patch('single_map_structure.current_geometry', return_value=dict(query=query, observationBounds=self.bounds)) as geometry, \
             patch.object(self.host, '_fit_observation', side_effect=fit):
            prepared = self.host.prepare_structure_scan(self.frame, self.bounds)
            geometry.assert_not_called()
            for mid in ('a', 'b'):
                self.assertTrue(prepared(self.frame, mid, '-1', lambda: False, observation_bounds=self.bounds)['success'])
            self.assertEqual(geometry.call_count, 1)
            self.assertIsNot(contexts[0], contexts[1])
            self.assertIs(contexts[0]['query'], contexts[1]['query'])
            fresh = self.host.prepare_structure_scan(self.frame, self.bounds)
            fresh(self.frame, 'a', '-1', lambda: False, observation_bounds=self.bounds)
            self.assertEqual(geometry.call_count, 2)
        for frame, bounds in ((self.frame.copy(), self.bounds), (self.frame, (0, 0, 40, 30))):
            with self.assertRaises(ValueError):
                prepared(frame, 'a', '-1', lambda: False, observation_bounds=bounds)

    def test_chain_cache_is_rejected_after_candidate_specific_filtering(self):
        nodes, observed, result = [], np.zeros((60, 80), bool), [object()]
        context = dict(nodes=nodes, observed=observed, _observed_corner_chains=(nodes, observed, result))
        self.assertIs(observed_corner_chains(self.frame, context), result)
        self.assertEqual(observed_corner_chains(self.frame, dict(context, observed=observed.copy())), [])
        self.assertEqual(observed_corner_chains(self.frame, dict(context, nodes=[])), [])

    def test_component_arbitration_and_cancel_remain_fail_closed(self):
        query = np.zeros((60, 80), np.uint8)
        islands = [(None, (5, 5, 20, 20), dict(area=200)),
                   (None, (30, 30, 50, 50), dict(area=100))]
        reference = SimpleNamespace(clean_mask=np.ones((100, 200), np.uint8))
        pose = dict(success=True, matrixSourceToClient=np.eye(3), observationBounds=islands[0][1])
        def geometry(frame, **kwargs):
            visible = kwargs.get('visible_override')
            return dict(query=query, observationBounds=visible[1]) if visible is not None else None
        for second, consistent, expected in (
                (dict(success=False, reason='structure-rejected'), False, 'manual-components-inconsistent'),
                (dict(success=False, reason='initial-structure-ambiguous'), True, 'manual-component-pose-ambiguous'),
                (dict(success=False, reason='initial-structure-validation-budget-unresolved'), True, 'manual-component-pose-ambiguous')):
            with self.subTest(expected=expected), \
                 patch('single_map_structure.current_geometry', side_effect=geometry), \
                 patch('single_map_structure.observed_corner_chains', return_value=[object()]), \
                 patch('single_map_structure.marker_occluded_corner_proposals', return_value=[]), \
                 patch('nightmare_candidate.visible_map_components', return_value=islands) as components, \
                 patch.object(self.host, '_shared_structure_reference', return_value=reference), \
                 patch.object(self.host, '_component_pose_consistent', return_value=consistent), \
                 patch.object(self.host, '_fit_observation', side_effect=[pose, second, pose, second]):
                prepared = self.host.prepare_structure_scan(self.frame, self.bounds)
                for mid in ('a', 'b'):
                    result = prepared(self.frame, mid, '-1', lambda: False, observation_bounds=self.bounds)
                    self.assertFalse(result['success'])
                    self.assertEqual(result['reason'], expected)
                self.assertEqual(components.call_count, 1)
        cancelled = [False]
        def fit(*args, **kwargs):
            cancelled[0] = True
            return pose
        with patch('single_map_structure.current_geometry', return_value=dict(query=query, observationBounds=self.bounds)), \
             patch.object(self.host, '_fit_observation', side_effect=fit):
            prepared = self.host.prepare_structure_scan(self.frame, self.bounds)
            result = prepared(self.frame, 'a', '-1', lambda: cancelled[0], observation_bounds=self.bounds)
            self.assertEqual(result['reason'], 'structure-work-obsolete')

    def test_pre_cancelled_scan_does_not_extract_pixels(self):
        from s1_structure_selection import StructureCatalog, StructureCandidateScanner
        catalog = StructureCatalog('s1', 'difficult', 'fixture', ('a', 'b'), ('-1', '-2', '-3', '-4'))
        scanner = StructureCandidateScanner(catalog, self.host.fit, prepare_fit=self.host.prepare_structure_scan)
        with patch('single_map_structure.current_geometry', side_effect=AssertionError('cancelled extraction')):
            report = scanner.scan(self.frame, floor='-1', floor_source='current-frame-verified', cancelled=lambda: True)
        self.assertEqual(report['status'], 'cancelled')
        self.assertEqual(report['unknownMapIds'], ['a', 'b'])
        self.assertFalse(report['supportedMapIds'])
        self.assertEqual(report['observedFloor'], '-1')


if __name__ == '__main__':
    unittest.main()
