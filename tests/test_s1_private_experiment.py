"""Private policy/ownership synthetic seams; not accuracy or native pixel QA."""
import copy
import threading
import unittest
from pathlib import Path
import numpy as np
from s1_private_experiment import PrivateRoundPool
from s1_structure_selection import StructureCandidateScanner
import test_s1_native_candidate_preview as native


class PrivatePoolContracts(unittest.TestCase):
    def test_only_explicit_common_coordinate_evidence_removes(self):
        pool=PrivateRoundPool('scope',('a','b','c'))
        pool.observe(dict(coordinateVerified=False,evaluatedMapIds=['a','b','c'],contradictedMapIds=['b']))
        self.assertEqual(pool.remaining,('a','b','c'))
        pool.observe(dict(coordinateVerified=True,evaluatedMapIds=['a','b','c'],contradictedMapIds=['b']))
        self.assertEqual(pool.remaining,('a','c'))
        self.assertFalse(pool.accept_material('a',coordinate_current=True))
        pool.observe(dict(coordinateVerified=True,evaluatedMapIds=['a','c'],contradictedMapIds=['c']))
        self.assertTrue(pool.accept_material('a',coordinate_current=True))
        self.assertFalse(pool.accept_material('a',coordinate_current=True))
        self.assertEqual(pool.eliminated,{'b','c'})
        pool.observe(dict(coordinateVerified=False,evaluatedMapIds=['a'],contradictedMapIds=[]))
        self.assertEqual(pool.locked_id,'a')
        pool.observe(dict(reason='coordinate-or-catalogue-conflict',coordinateVerified=False))
        self.assertEqual(pool.remaining,('a','b','c'));self.assertEqual(pool.locked_id,'')

    def test_unknown_cannot_remove_and_does_not_promote(self):
        pool=PrivateRoundPool('scope',('a','b'))
        pool.observe(dict(coordinateVerified=True,evaluatedMapIds=['a','b'],contradictedMapIds=['b']))
        self.assertFalse(pool.accept_material('a',coordinate_current=False))
        pool.observe(dict(coordinateVerified=False,evaluatedMapIds=['a'],contradictedMapIds=[]))
        self.assertEqual(pool.remaining,('a',))

    def test_packaged_locator_constructs_and_black_frame_is_unknown(self):
        from s1_gate_anchor import GateAnchorLocator
        from client_paths import RESOURCE_ROOT
        locator=GateAnchorLocator(asset_dir=RESOURCE_ROOT/'s1-private-assets')
        result=locator.detect(np.zeros((120,160,3),np.uint8))
        self.assertEqual(result.status,'unknown');self.assertIsNone(result.center_px)

    def test_private_logs_survive_privacy_without_raw_pixels(self):
        from diagnostic_privacy import safe_record
        report=dict(eventName='private-experimental-locked',privateLockMapId='106009',
            privatePoolBefore=['106009','106015'],privatePoolAfter=['106009'],
            privateCoordinateBasis='current-floor-common-chain-vertices',privateScaleRange=[.4,.6],
            privateGateMs=80.,privateResourceDigest='a'*64,rawPixels=[1,2],windowTitle='private')
        clean=safe_record(report)
        self.assertEqual(clean['privatePoolBefore'],report['privatePoolBefore'])
        self.assertEqual(clean['privateLockMapId'],'106009')
        self.assertNotIn('rawPixels',clean);self.assertNotIn('windowTitle',clean)


class PrivateFixedPoseContracts(unittest.TestCase):
    setUp=native.NativeCandidateContracts.setUp
    start_patch=native.NativeCandidateContracts.start_patch

    def test_fixed_pose_residual_and_material_share_selected_component(self):
        from s1_private_experiment import PrivateCoordinateObserver
        from types import SimpleNamespace
        from unittest.mock import patch
        observer=PrivateCoordinateObserver.__new__(PrivateCoordinateObserver)
        observer.engine=self.registry.engine
        observer.filter=SimpleNamespace(reference_to_client=lambda *args:np.eye(3))
        visible=(np.ones((720,1280),np.uint8),(10,20,200,300),{'area':1000})
        context=dict(visible=visible,observationBounds=(10,20,200,300))
        reference=SimpleNamespace(clean_mask=np.zeros((30,30),np.uint8))
        with patch('client_live_runtime._AlignmentHost._shared_structure_reference',return_value=reference), patch(
                'client_live_runtime._AlignmentHost._rendered_profile_current_frame_residual',
                return_value=self.fit['selectedRouteCurrentFrameResidual']) as residual:
            result=observer.fixed_fit(self.capture.frame,'a','-1',lambda:False,
                report={'_observationContext':context})
        self.assertTrue(result['success'])
        self.assertIs(residual.call_args.kwargs['visible_override'],visible)
        self.assertEqual(result['observationComponent']['bounds'],visible[1])
        self.assertEqual(result['observationBounds'],context['observationBounds'])
