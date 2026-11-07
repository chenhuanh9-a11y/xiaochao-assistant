"""Shared recovery producer through the current Qt alignment host.

Synthetic geometry boundaries; real solve, provenance and final consumer.
No Tk application, copied methods, HWND or game acceptance claims.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from client_live_runtime import _AlignmentHost
from alignment_observation import rendered_profile_residual_is_verified
import single_map_structure as structure


class SharedRecoveryContractTests(unittest.TestCase):
    def setUp(self):
        self.frame = np.zeros((40, 40, 3), np.uint8)
        self.route = np.full((40, 40), 255, np.uint8)
        self.matrix = np.eye(3)
        self.reference = SimpleNamespace(clean_mask=self.route, validation_mask=self.route, metadata={})
        self.adapters = SimpleNamespace(canonical_pack_id='synthetic-reference-pack',
            matrix_profile_to_canonical=lambda *args: self.matrix)
        self.group = SimpleNamespace(map_id='nightmare-16', verified_floors={'2'},
            canonical_geometry_verified_floors={'2'},
            relations={'1': np.eye(3), '2': np.eye(3)}, route_profile_id='shared-structure',
            canonical_pose_for_floor=lambda floor: self.matrix)
        self.engine = SimpleNamespace(pack=SimpleNamespace(mode='nightmare'),
            qt_adapters=self.adapters, qt_shared_provider=lambda *args: self.reference,
            entry_identity=None)
        self.h = _AlignmentHost(self.engine, self.group)
        self.h.map_id, self.h.floor = 'nightmare-16', '1'
        self.geometry = dict(structureModel='h0', h0=dict(success=True, matrixSourceToClient=self.matrix),
            graph=dict(regionalEdgeVerified=True), acceptedPointCount=4)
        self.context = dict(query=np.ones((40, 40), np.uint8), nodes=[])

    def solve(self, *, raw_verified=True, partial_success=True, cancelled=None, consumer=False):
        with patch.object(structure, 'graph_point_geometry', return_value=self.geometry), \
             patch.object(structure, 'partial_wall_geometry', return_value=dict(success=partial_success,
                 matrix=self.matrix, diagnostics={'planes': [1, 2, 3, 4]})), \
             patch.object(structure, 'selected_scale_observation', return_value=None), \
             patch('nightmare_candidate.selected_route_current_frame_residual', return_value={'verified': raw_verified}), \
             patch.object(structure, 'retrieve', return_value=([], {})), \
             patch.object(structure, 'retrieve_native_turns', return_value=([], {})), \
             patch.object(structure, 'current_part', return_value=[]), \
             patch.object(structure, 'current_geometry', return_value=self.context), \
             patch('fast_structure_alignment.try_alignment', return_value=None):
            if consumer:
                return self.h._fit_observation(self.frame, 'nightmare-16', '1', cancelled)
            return structure.solve(self.h, self.frame, 'nightmare-16', '1', 'shared-structure',
                                   self.route, _context=self.context, cancelled=cancelled)

    def test_cross_floor_recovery_producer_meets_current_qt_consumer_contract(self):
        for mode in ('nightmare', 'difficult'):
            with self.subTest(mode=mode):
                self.engine.pack.mode = mode
                fit = self.solve(consumer=True)
                self.assertTrue(fit['success'], fit)
                self.assertTrue(rendered_profile_residual_is_verified(fit['selectedRouteCurrentFrameResidual']))
                self.assertEqual(fit['strategy'], 'shared-established-group-recovery')
                self.assertEqual(fit['coordinateSpace'], 'shared-structure-to-client')
                np.testing.assert_array_equal(fit['matrixSourceToClient'], self.matrix)

    def test_qt_consumer_rechecks_provenance_after_successful_solver(self):
        # An accepted producer cannot substitute an untyped raw verdict at the
        # final current-frame consumer boundary.
        with patch.object(self.h, '_rendered_profile_current_frame_residual',
                          return_value={'verified': True}):
            fit = self.solve(consumer=True)
        self.assertFalse(fit['success'])
        self.assertEqual(fit['reason'], 'canonical-current-frame-residual-unverified')

    def test_missing_pack_or_bridge_still_rejects(self):
        self.adapters.canonical_pack_id = None
        self.assertFalse(self.solve()['success'])
        self.adapters.canonical_pack_id = 'synthetic-reference-pack'
        self.adapters.matrix_profile_to_canonical = lambda *args: None
        self.assertFalse(self.solve()['success'])

    def test_bad_geometry_or_residual_still_rejects(self):
        self.assertFalse(self.solve(raw_verified=False)['success'])
        self.geometry['h0']['success'] = False
        self.assertFalse(self.solve(partial_success=False)['success'])

    def test_cancelled_work_never_returns_pose(self):
        self.assertEqual(self.solve(cancelled=lambda: True)['reason'], 'structure-work-obsolete')

    def test_scale_relocalization_uses_same_contract(self):
        self.geometry['h0']['success'] = False
        self.engine.entry_identity = SimpleNamespace(rank=lambda *args, **kwargs: (
            [dict(poseVerified=True, verification=dict(matrixSourceToClient=self.matrix,
                residual={'verified': True}))], {}))
        fit = self.solve(partial_success=False)
        self.assertTrue(fit['success'], fit)
        self.assertEqual(fit['reason'], 'difficult-entry-scale-relocalization')
        self.assertTrue(rendered_profile_residual_is_verified(fit['selectedRouteCurrentFrameResidual']))


if __name__ == '__main__':
    unittest.main()
