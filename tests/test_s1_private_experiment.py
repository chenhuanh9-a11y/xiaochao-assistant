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


class PrivateRuntimeContracts(unittest.TestCase):
    setUp=native.NativeCandidateContracts.setUp
    start_patch=native.NativeCandidateContracts.start_patch
    show=native.NativeCandidateContracts.show
    current_payload=native.NativeCandidateContracts.current_payload

    def wire(self, prepare=None, fail=False):
        self.events=[];self.fit_ids=[];self.prepared=[]
        def default(frame, ids, floor, *, cancelled):
            self.prepared.append((tuple(ids),floor,int(frame[0,0,0])))
            return dict(mode='nightmare',resourceKey='fixture',floor=floor,coordinateVerified=True,
                evaluatedMapIds=list(ids),contradictedMapIds=[i for i in ids if i=='b'],
                unknownMapIds=[i for i in ids if i!='b'],reason='same-coordinate-positive-contradictions-only')
        self.registry.engine.qt_s1_private_coordinate_prepare=prepare or default
        def scan(frame, **kwargs):
            evidence=kwargs.pop('private_coordinate_report',None)
            def fit(f,mid,fl,cancelled,**kw):
                self.fit_ids.append(mid)
                return dict(success=False) if fail and evidence is not None else copy.deepcopy(self.fit)
            return StructureCandidateScanner(self.registry.catalog,fit).scan(frame,**kwargs)
        self.registry.engine.qt_s1_structure_scan=scan
        self.runtime.set_event_callback(lambda row:self.events.append(row))

    def test_private_lock_and_single_id_reopen_without_formal_identity(self):
        self.wire();self.show()
        state=self.runtime.snapshot()
        self.assertTrue(state['privateExperiment']['privateExperimentalLocked'])
        self.assertFalse(state['identityLocked']);self.assertEqual(self.runtime._durable_map_id,'')
        self.runtime.dispatch('close_map');self.capture.frame[0,0,0]+=1;self.show()
        self.assertEqual(self.fit_ids,['a','a'])
        self.assertEqual(sum(e['event']=='private-experimental-locked' for e in self.events),1)
        self.assertEqual(len(self.prepared),2)

    def test_singleton_fit_unknown_restores_full_competition(self):
        self.wire(fail=True);self.show()
        self.assertEqual(self.fit_ids,['a','a','b'])
        self.assertFalse(self.runtime.snapshot()['privateExperiment']['privateExperimentalLocked'])
        self.assertEqual(self.runtime._s1_private_pool.remaining,('a','b'))

    def test_force_rediscovery_replaces_round_pool_before_work(self):
        self.wire();self.show();old=self.runtime._s1_private_pool
        self.assertTrue(self.runtime.dispatch('capture_now',{'forceCatalogue':True})['success'])
        self.assertIsNot(old,self.runtime._s1_private_pool)
        self.assertEqual(self.prepared[-1][0],('a','b'))

    def test_late_observer_after_end_cannot_lock_or_restore_state(self):
        entered,release=threading.Event(),threading.Event()
        def blocked(frame,ids,floor,*,cancelled):
            entered.set();self.assertTrue(release.wait(3))
            return dict(mode='nightmare',resourceKey='fixture',floor=floor,coordinateVerified=True,
                evaluatedMapIds=list(ids),contradictedMapIds=['b'],unknownMapIds=['a'])
        self.wire(blocked);result=[]
        worker=threading.Thread(target=lambda:result.append(self.runtime.dispatch('capture_now')))
        worker.start();self.assertTrue(entered.wait(3));self.runtime.dispatch('end_round');release.set();worker.join(3)
        self.assertFalse(worker.is_alive());self.assertFalse(result[0]['success'])
        self.assertIsNone(self.runtime._s1_private_pool);self.assertIsNone(self.runtime._render)
        self.assertFalse(any(e['event']=='private-experimental-locked' for e in self.events))

    def test_late_observer_cannot_replace_successor_pool(self):
        self.wire();seen=[False]
        def event(row):
            self.events.append(row)
            if row['event']=='private-pool-observed' and not seen[0]:
                seen[0]=True
                self.runtime.dispatch('close_map')
                self.capture.frame[0,0,0]+=1
                self.assertTrue(self.runtime.dispatch('capture_now')['success'])
                self.successor=self.runtime._s1_private_pool
                self.successor_token=self.runtime._render['token']
        self.runtime.set_event_callback(event)
        result=self.runtime.dispatch('capture_now')
        self.assertFalse(result['success'])
        self.assertIs(self.runtime._s1_private_pool,self.successor)
        self.assertEqual(self.runtime._render['token'],self.successor_token)
        self.assertTrue(self.runtime.snapshot()['privateExperiment']['privateExperimentalLocked'])

    def test_resource_and_manual_boundaries_clear_experiment(self):
        self.wire();self.show();self.registry.invalidate=lambda:None
        self.runtime.invalidate('resource-changed')
        self.assertIsNone(self.runtime._s1_private_pool)
        self.runtime.configure_settings(dict(mapSelectionMode='manual'))
        self.assertIsNone(self.runtime._s1_private_pool)

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
