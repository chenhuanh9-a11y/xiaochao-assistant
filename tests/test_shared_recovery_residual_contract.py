"""Real recovery producer and canonical consumer; controlled geometry boundaries.

Matrices/pixels are synthetic, not missing cycle115 successful-worker evidence.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from test_native_similarity_runtime import runtime
import single_map_structure as structure


class SharedRecoveryContractTests(unittest.TestCase):
    def setUp(self):
        self.h=runtime.DailyAssistant.__new__(runtime.DailyAssistant)
        h=self.h
        self.frame=np.zeros((40,40,3),np.uint8)
        self.route=np.full((40,40),255,np.uint8)
        self.matrix=np.eye(3)
        self.group=SimpleNamespace(verified_floors={'2'},canonical_geometry_verified_floors={'2'},
            relations={'1':np.eye(3),'2':np.eye(3)},route_profile_id='shared-structure')
        h.game_mode='nightmare';h.gate=SimpleNamespace(confirmed_id='nightmare-16')
        h.current_floor='1';h.current_frame=self.frame;h.current_frame_generation=412
        h.active_map_cycle_id=115;h.surface_visibility_generation=167
        h.route_profile_id='nightingale'
        h._active_round_layer_group=lambda *a:self.group
        h._round_group_floor_view=lambda *a,**kw:dict(recognitionMatrixSourceToClient=self.matrix)
        h._shared_structure_mode_enabled=lambda **kw:True
        h._alignment_v2_structure_enabled=lambda:True
        h._open_cycle_transient_motion_active=lambda *a:False
        h._author_route_clean_product_for_route=lambda *a:SimpleNamespace(clean_mask=self.route)
        h._author_route_structure_reference_cached=lambda *a,**kw:(self.route,True,0.)
        h._shared_structure_reference_context=lambda *a:('shared-structure','nightmare-16','1')
        h._shared_structure_reference=lambda *a:SimpleNamespace(metadata={})
        self.spec=dict(route=self.route,matrixSourceToClient=self.matrix,
            coordinatePackId='synthetic-reference-pack',profileBridgeVerified=True,
            basis='game-rendered-shared-structure')
        h._route_profile_validation_spec=lambda *a,**kw:self.spec
        h._aligned_render_spec=lambda *a,**kw:dict(canonicalMatrixSourceToClient=self.matrix,
            matrixSourceToClient=self.matrix,routeSource=self.route,routeProfileId='nightingale',
            displayKind='author-route',displayLabel='fixture',sourceCoordinateSpace='fixture')
        self.geometry=dict(structureModel='h0',h0=dict(success=True,matrixSourceToClient=self.matrix),
            graph=dict(regionalEdgeVerified=True),acceptedPointCount=4)
        self.context=dict(query=np.ones((40,40),np.uint8),nodes=[])

    def solve(self, *, raw_verified=True, partial_success=True, cancelled=None):
        with patch.object(structure,'graph_point_geometry',return_value=self.geometry), \
             patch.object(structure,'partial_wall_geometry',return_value=dict(success=partial_success,matrix=self.matrix,diagnostics={'planes':[1,2,3,4]})), \
             patch.object(structure,'selected_scale_observation',return_value=None), \
             patch.object(structure,'selected_route_current_frame_residual',return_value={'verified':raw_verified}), \
             patch.object(runtime,'selected_route_current_frame_residual',return_value={'verified':raw_verified}), \
             patch.object(structure,'retrieve',return_value=([],{})), \
             patch.object(structure,'retrieve_native_turns',return_value=([],{})), \
             patch.object(structure,'current_part',return_value=[]):
            return structure.solve(self.h,self.frame,'nightmare-16','1','shared-structure',self.route,_context=self.context,cancelled=cancelled)

    def test_cross_floor_recovery_producer_meets_unchanged_consumer_contract(self):
        for mode in ('nightmare','difficult'):
            with self.subTest(mode=mode):
                self.h.game_mode=mode
                fit=self.solve()
                self.assertTrue(fit['success'])
                residual=fit['selectedRouteCurrentFrameResidual']
                self.assertTrue(runtime.rendered_profile_residual_is_verified(residual))
                event=dict(alignmentV2WorkerFit=fit,sourceFrameGeneration=408)
                candidate=self.h._alignment_v2_current_frame_candidate('nightmare-16','1','2',event)
                self.assertIsNotNone(candidate)
                self.assertEqual(candidate['candidate'].frame_generation,408)
                self.assertEqual(self.h.current_frame_generation,412)

    def test_missing_pack_or_bridge_still_rejects(self):
        for field,value in (('coordinatePackId',None),('profileBridgeVerified',False)):
            with self.subTest(field=field):
                original=self.spec[field];self.spec[field]=value
                self.assertFalse(self.solve()['success'])
                self.spec[field]=original

    def test_bad_geometry_or_residual_still_rejects(self):
        self.assertFalse(self.solve(raw_verified=False)['success'])
        self.geometry['h0']['success']=False
        self.assertFalse(self.solve(partial_success=False)['success'])

    def test_cancelled_work_never_returns_pose(self):
        self.assertEqual(self.solve(cancelled=lambda:True)['reason'],'structure-work-obsolete')

    def test_scale_relocalization_uses_same_contract(self):
        self.geometry['h0']['success']=False
        self.h._alignment_entry_locator=lambda:SimpleNamespace(rank=lambda *a,**kw:(
            [dict(poseVerified=True,verification=dict(matrixSourceToClient=self.matrix,residual={'verified':True}))],{}))
        fit=self.solve(partial_success=False)
        self.assertTrue(fit['success'])
        self.assertEqual(fit['reason'],'difficult-entry-scale-relocalization')
        self.assertTrue(runtime.rendered_profile_residual_is_verified(fit['selectedRouteCurrentFrameResidual']))

if __name__=='__main__':unittest.main()
