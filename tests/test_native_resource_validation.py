"""Real native resource validation on synthetic, hashed fixture files; no game/UI."""
import sys,time,tempfile,threading,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parent))
from test_native_similarity_runtime import Fixture, NativeSimilarityState, runtime, AlignmentCoreV2, AlignmentCandidate, np

class NativeResourceValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.f=Fixture(Path(self.temp.name))
        a=self.a=runtime.DailyAssistant.__new__(runtime.DailyAssistant)
        a.native_similarity_state=NativeSimilarityState()
        a.native_similarity_state.accept(self.f.propose(),('nightmare',10,'1',1,2),'fixture')
        a.engine=SimpleNamespace(native_similarity_index=self.f.index,entry_shared_reference_provider=self.f.provider)
        a.game_mode='nightmare';a.current_floor='1';a.capture_generation=10;a.active_map_cycle_id=10
        a.map_is_open=True;a.closed=False;a.active_confirmation={'mapId':'nightmare-04'}
        a.gate=SimpleNamespace(confirmed_id='nightmare-04',hide_for_new_frame=lambda:None)
        a.alignment_core_v2=AlignmentCoreV2()
        # UI-only display sinks; authorization lifecycle remains production core.
        a._hide_spatial_route=lambda *args:None
        a._clear_open_cycle_drag=lambda *args:None
        a._clear_route_notification=lambda *args:None
        a._clear_round_layer_group=lambda *args:None
        a._log=lambda *args,**kw:None
    def settle(self,predicate):
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            self.a._check_native_similarity_resources()
            if predicate():return
            time.sleep(.002)
        self.fail('validation did not settle')
    def drain(self):
        lane=getattr(self.a,'_native_resource_lane',None)
        if lane:
            lane.cancel()
            deadline=time.monotonic()+3
            while lane.snapshot()['running'] and time.monotonic()<deadline:time.sleep(.002)
    def tearDown(self):self.drain()
    def test_stat_is_off_calling_thread(self):
        threads=[];original=Path.stat;caller=threading.get_ident()
        def tracked(path,*args,**kw):
            threads.append(threading.get_ident());return original(path,*args,**kw)
        with patch.object(Path,'stat',tracked):
            self.settle(self.a._native_similarity_resources_ready)
            self.drain()
        self.assertTrue(threads);self.assertNotIn(caller,threads)
    def test_changed_hash_revokes_active_and_core(self):
        self.settle(self.a._native_similarity_resources_ready)
        core=self.a.alignment_core_v2
        core.begin_observation(cycle_id=10,map_id='nightmare-04',floor='1',frame_generation=1)
        candidate=AlignmentCandidate('nightmare-04','1',np.eye(3),'initial-structure-v2',1,
            current_frame_verified=True,residual_verified=True,global_residual_verified=True)
        verdict=core.adjudicate(candidate);self.assertTrue(verdict.accepted)
        authorization=core.authorize(verdict);self.assertTrue(core.commit(authorization))
        self.assertTrue(authorization.active)
        self.assertIsNotNone(core.active_pose)
        # Complete pending old read before changing file; a subsequent check is real.
        self.drain()
        path=self.f.root/'references/differences.npz'
        path.write_bytes(path.read_bytes()+b'changed')
        self.settle(lambda:getattr(self.a,'_native_resource_rejected',False))
        self.assertFalse(authorization.active)
        self.assertIsNone(core.active_pose)
        self.assertIsNone(self.a.active_confirmation)
        self.assertFalse(self.a.alignment_core_v2.display_authorization.active)
        self.assertIsNone(self.a.current_floor_authorization)
        self.assertEqual(self.a.native_similarity_state.state,'UNLOCKED')
    def test_cancelled_generation_and_close_cannot_adopt_late_result(self):
        entered=threading.Event();release=threading.Event();original=Path.stat
        def blocked(path,*args,**kw):
            if threading.current_thread().name=='resource-validation':
                entered.set();release.wait(3)
            return original(path,*args,**kw)
        with patch.object(Path,'stat',blocked):
            self.a._check_native_similarity_resources();self.assertTrue(entered.wait(2))
            self.a.capture_generation+=1;self.a._cancel_native_resource_validation()
            self.a.closed=True;release.set();self.drain()
            self.assertFalse(self.a._check_native_similarity_resources())
            self.assertFalse(self.a._native_similarity_resources_ready())
            self.assertIsNone(self.a._native_resource_grant)

    def test_hundred_generation_changes_keep_single_worker(self):
        entered=threading.Event();release=threading.Event();original=Path.stat;worker_ids=set()
        def blocked(path,*args,**kw):
            if threading.current_thread().name=='resource-validation':
                worker_ids.add(threading.get_ident());entered.set();release.wait(3)
            return original(path,*args,**kw)
        with patch.object(Path,'stat',blocked):
            try:
                self.a._check_native_similarity_resources();self.assertTrue(entered.wait(2))
                for n in range(100):
                    self.a.native_similarity_state.generation+=1
                    self.a._check_native_similarity_resources()
                    self.assertEqual(self.a._native_resource_lane.snapshot(),
                        dict(running=True,pending=1,results=0))
                self.assertEqual(len(worker_ids),1)
                self.assertFalse(self.a._native_similarity_resources_ready())
            finally:release.set()
            self.settle(self.a._native_similarity_resources_ready)
            self.drain()
        self.assertEqual(len(worker_ids),1)

    def test_expired_grant_fails_closed_then_real_recheck_recovers(self):
        self.settle(self.a._native_similarity_resources_ready);self.drain()
        self.assertEqual(self.a.NATIVE_RESOURCE_GRANT_SECONDS,.5)
        key,_=self.a._native_resource_grant
        self.a._native_resource_grant=(key,time.monotonic()-.501)
        entered=threading.Event();release=threading.Event();original=Path.stat
        def blocked(path,*args,**kw):
            if threading.current_thread().name=='resource-validation':
                entered.set();release.wait(3)
            return original(path,*args,**kw)
        with patch.object(Path,'stat',blocked):
            try:
                self.assertFalse(self.a._check_native_similarity_resources())
                self.assertTrue(entered.wait(2))
                self.assertFalse(self.a._native_similarity_resources_ready())
                self.assertIsNone(self.a.current_floor_authorization)
            finally:release.set()
            self.settle(self.a._native_similarity_resources_ready)
            self.drain()

    def test_durable_identity_survives_close_and_new_capture_without_display_grant(self):
        self.a.map_is_open=False
        self.a._cancel_native_resource_validation()
        self.assertFalse(self.a._native_similarity_resources_ready())
        self.assertEqual(self.a._round_identity_lock_context()['mapId'],'nightmare-04')
        self.a.map_is_open=True;self.a.capture_generation+=1
        self.assertFalse(self.a._native_similarity_resources_ready())
        self.assertEqual(self.a._round_identity_lock_context()['mapId'],'nightmare-04')
        self.a._native_resource_rejected=True
        self.assertIsNone(self.a._round_identity_lock_context())

    def prepare_recovery(self):
        self.settle(self.a._native_similarity_resources_ready);self.drain()
        a=self.a;a.current_frame=np.zeros((64,64,3),np.uint8)
        a.current_frame_generation=1;a.surface_visibility_generation=2;a.floor_required=False
        a._native_similarity_condition_context=None
        a._alignment_v2_worker_base_pose_context=lambda map_id:{}
        a._log_core_chain_event=lambda *args,**kw:None
        seen=[];done=threading.Event()
        def record(args):seen.append(args);done.set()
        # Only the expensive sensor loop is replaced; request construction,
        # real Thread.start and rollback are the production implementations.
        a._alignment_v2_first_pose_worker_loop=record
        a._queue_native_resource_recovery(a._native_resource_binding()[0])
        return seen,done

    def test_matching_recovery_schedules_one_current_frame_fit(self):
        seen,done=self.prepare_recovery()
        self.a._resume_native_resource_recovery();self.assertTrue(done.wait(2))
        self.a._resume_native_resource_recovery()
        self.assertEqual(len(seen),1)
        args=seen[0]
        self.assertEqual(args[0][3],self.a.current_frame_generation)
        self.assertEqual(args[8],self.a.surface_visibility_generation)
        np.testing.assert_array_equal(args[4],self.a.current_frame)
        self.assertEqual(args[6],'first-pose')

    def test_stale_recovery_does_not_schedule(self):
        for change in ('frame','surface','cancel'):
            with self.subTest(change=change):
                seen,done=self.prepare_recovery()
                if change=='frame':self.a.current_frame=self.a.current_frame.copy()
                elif change=='surface':self.a.surface_visibility_generation+=1
                else:self.a._cancel_native_resource_validation()
                self.a._resume_native_resource_recovery()
                self.assertEqual(seen,[])

    def test_recovery_start_failure_rolls_back_and_can_retry(self):
        seen,done=self.prepare_recovery()
        with patch.object(threading.Thread,'start',side_effect=RuntimeError('injected')):
            self.a._resume_native_resource_recovery()
        self.assertFalse(self.a._alignment_v2_worker_running)
        self.assertFalse(self.a._alignment_v2_worker_pending)
        self.assertFalse(self.a.alignment_in_progress)
        self.assertIsNone(self.a._alignment_v2_worker_token)
        self.assertIsNotNone(self.a._native_resource_recovery)
        self.a._resume_native_resource_recovery();self.assertTrue(done.wait(2))
        self.assertEqual(len(seen),1)

if __name__=='__main__':unittest.main()
