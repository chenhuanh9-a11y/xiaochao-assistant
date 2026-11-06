"""Real runtime ownership and prior-consumer seam; synthetic capture/fit only."""
import unittest
from unittest.mock import patch
import numpy as np
import test_s1_native_candidate_preview as native
from s1_pose_prior import PosePrior, AcknowledgedPose

class RuntimePriorContracts(unittest.TestCase):
    start_patch=native.NativeCandidateContracts.start_patch
    show=native.NativeCandidateContracts.show
    current_payload=native.NativeCandidateContracts.current_payload
    def setUp(self):
        native.NativeCandidateContracts.setUp(self)
        self.registry.capability['observedFloors']=(-1,-2,-3,-4)
        self.prior_calls=[];self.scans=[]
        original=self.registry.engine.qt_s1_structure_scan
        def scan(frame,**kw):
            self.scans.append(dict(kw))
            # This fixture bypasses the production _AlignmentHost adapter.
            # Preserve the invocation evidence, then strip its host-only argument.
            kw.pop("pose_priors", None)
            return original(frame,**kw)
        self.registry.engine.qt_s1_structure_scan=scan
        def hint(source,floor,scope):
            self.prior_calls.append((source,floor,scope))
            return PosePrior(source.mode,source.resource_key,'fixture-index',source.map_id,
                source.floor,str(floor),source.frame_id,scope,source.matrix)
        self.registry.engine.qt_s1_pose_prior=hint
    def accepted(self):
        row=self.show();r=self.runtime.acknowledge_render_material(row['token'])
        self.assertTrue(r['success'],r);return row
    def reopen(self,floor='-1',payload=None):
        self.runtime.dispatch('close_map');self.floor=floor
        r=self.runtime.dispatch('capture_now',payload or {});self.assertTrue(r['success'],r)
        return self.current_payload()
    def test_no_ack_and_ui_payload_never_mint_prior(self):
        first=self.show();self.assertIsNone(self.runtime._s1_candidate_pose_hint)
        self.reopen(payload={'pose_priors':{('a','-1'):np.eye(3)},'AcknowledgedPose':True})
        self.assertEqual(self.prior_calls,[]);self.assertNotIn('pose_priors',self.scans[-1])
        self.assertFalse(self.runtime.acknowledge_render_material(first['token'])['success'])
        self.assertIsNone(self.runtime._s1_candidate_pose_hint)
    def test_same_round_cross_floor_consumes_only_acked_map_hint(self):
        events=[];self.runtime.set_event_callback(events.append)
        first=self.accepted();source=self.runtime._s1_candidate_pose_hint
        self.assertIs(type(source),AcknowledgedPose);self.assertEqual(source.floor,'-1')
        second=self.reopen('-2');self.assertNotEqual(second['frameId'],first['frameId'])
        self.assertEqual(second['floor'],'-2');self.assertFalse(self.runtime.snapshot()['identityLocked'])
        self.assertEqual(self.prior_calls[-1][0],source)
        self.assertEqual(set(self.scans[-1]['pose_priors']),{(first['mapId'],'-2')})
        plan=next(e['recognitionDiagnostics'] for e in reversed(events)
            if e['event']=='recognition-diagnostic' and e['recognitionDiagnostics'].get('stage')=='s1-scan-plan')
        self.assertEqual(plan['reason'],'pose-prior-bound')
        self.assertTrue(plan['posePriorAvailable'])
        self.assertEqual((plan['sourceFloor'],plan['targetFloor']),('-1','-2'))
        from diagnostic_privacy import safe_record
        self.assertEqual(safe_record(plan), plan)
        self.assertIsNone(self.runtime.render_payload(first['token']))
        self.assertTrue(self.runtime.acknowledge_render_material(second['token'])['success'])
        self.assertEqual(self.runtime._s1_candidate_pose_hint.floor,'-2')
    def test_end_round_discards_hint(self):
        self.accepted();self.runtime.dispatch('end_round');self.assertIsNone(self.runtime._s1_candidate_pose_hint)
        self.show();self.assertEqual(self.prior_calls,[])
    def test_settings_scope_discards_hint(self):
        self.accepted();self.runtime.dispatch('close_map')
        self.runtime.configure_settings(dict(mapSelectionMode='auto',idvb0059Enabled=False))
        r=self.runtime.dispatch('capture_now');self.assertTrue(r['success'],r)
        self.assertEqual(self.prior_calls,[]);self.assertNotIn('pose_priors',self.scans[-1])
    def test_capture_generation_discards_hint(self):
        self.accepted();self.runtime.dispatch('close_map');self.capture.generation+=1
        r=self.runtime.dispatch('capture_now');self.assertTrue(r['success'],r)
        self.assertEqual(self.prior_calls,[])
    def test_changed_capture_geometry_discards_hint(self):
        self.accepted();self.runtime.dispatch('close_map');self.capture.frame=np.zeros((800,1280,3),np.uint8)
        r=self.runtime.dispatch('capture_now');self.assertTrue(r['success'],r)
        self.assertEqual(self.prior_calls,[])
    def test_force_catalogue_skips_prior(self):
        self.accepted();self.reopen(payload={'forceCatalogue':True})
        self.assertEqual(self.prior_calls,[]);self.assertNotIn('pose_priors',self.scans[-1])
    def test_missing_bound_index_falls_back_to_original_fit(self):
        self.accepted()
        def missing(*a):raise OSError('fixture-index-unavailable')
        self.registry.engine.qt_s1_pose_prior=missing
        self.reopen('-2');self.assertNotIn('pose_priors',self.scans[-1])
    def test_wrong_mode_hint_cannot_reach_solver(self):
        self.accepted()
        def wrong(source,floor,scope):
            return PosePrior('difficult',source.resource_key,'fixture',source.map_id,
                source.floor,str(floor),source.frame_id,scope,source.matrix)
        self.registry.engine.qt_s1_pose_prior=wrong
        self.reopen('-2');self.assertNotIn('pose_priors',self.scans[-1])
    def test_unknown_current_floor_never_displays_old_hint(self):
        row=self.accepted();self.runtime.dispatch('close_map');self.floor=None
        result=self.runtime.dispatch('capture_now')
        self.assertFalse(result['success']);self.assertIsNone(self.runtime.render_payload(row['token']))
        self.assertIsNone(self.current_payload());self.assertEqual(self.prior_calls,[])

if __name__=='__main__':unittest.main()
