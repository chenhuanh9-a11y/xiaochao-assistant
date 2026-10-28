"""Actual worker, extracted poll branch, owner and core; controlled geometry/IO."""
import ast
from pathlib import Path
import queue
import time
import unittest
import numpy as np
import test_shared_recovery_residual_contract as contract
import test_native_switch_atomic as atomic
runtime=contract.runtime


def poll_sensor_branch():
    tree=ast.parse((Path(__file__).parents[1]/'app/identityv_daily_assistant.py').read_text(encoding='utf8'))
    node=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and isinstance(n.test,ast.Compare)
        and isinstance(n.test.left,ast.Name) and n.test.left.id=='kind'
        and any(isinstance(v,ast.Constant) and v.value=='alignment-v2-sensor-result' for v in n.test.comparators))
    shell=ast.parse("""def deliver(self,event):
    worker_deliveries=[]
    event_dequeued_at=time.perf_counter()
    for event in [event]:
        pass
""")
    shell.body[0].body[-1].body=node.body
    ns=dict(vars(runtime));exec(compile(ast.fix_missing_locations(shell),'actual-poll-sensor-branch','exec'),ns)
    return ns['deliver']


class SharedRecoveryDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.c=contract.SharedRecoveryContractTests();self.c.setUp()
        owner_fixture=atomic.NativeSwitchCompositorTests();self.addCleanup(owner_fixture.doCleanups)
        h,core,grant,event,source,target=owner_fixture.setup_chain(True)
        self.h=h;self.core=core
        h.pack.by_id['nightmare-16']=h.pack.by_id[atomic.fixture.IDS[0]]
        h.front_group_sibling_pending=None
        h.native_similarity_state=runtime.NativeSimilarityState()
        h.gate.confirmed_id=h.gate.selected_id='nightmare-16'
        h.current_frame=self.c.frame;h.current_frame_generation=412
        h.active_map_cycle_id=115;h.current_floor='1';h.surface_visibility_generation=167
        h.route_profile_id='nightingale';h.map_is_open=True
        h.runtime_timeline_enabled=False;h._alignment_v2_reset_generation=4
        h._alignment_reference_image=lambda *a:self.c.route
        h._alignment_v2_structure_fit=lambda *a,**kw:self.c.solve()
        h._shared_structure_reference=lambda *a:None
        h._aligned_render_spec=self.c.h._aligned_render_spec
        del h._alignment_v2_current_frame_candidate
        h._alignment_v2_front_pose_seed=lambda *a:None
        h._record_b1_v2_worker_result=lambda *a,**kw:False
        h._alignment_v2_requeue_latest_pose_recovery=lambda *a,**kw:None
        h._record_route_issue=lambda *a,**kw:None
        h._save_settings=lambda:None
        h._alignment_v2_note_worker_failure=lambda *a:None
        h._start_front_group_difference_probe_after_commit=lambda *a:None
        self.logs=[];h._log_core_chain_event=lambda event,**kw:self.logs.append((event,kw))
        h.events=queue.Queue()
        self.poll=poll_sensor_branch()

    def produce(self):
        h=self.h
        token=(115,'nightmare-16','1',408,(),4)
        h._alignment_v2_worker_token=(115,'nightmare-16','1',412,(),4)
        args=(token,'nightmare-16','1','2',h.current_frame.copy(),h.current_bounds,
              'group-recovery',runtime.map_frame_signature(h.current_frame),167,{})
        h._alignment_v2_first_pose_worker(args)
        return h.events.get_nowait()

    def test_same_pixels_new_capture_reaches_actual_core_commit(self):
        event=self.produce()
        self.assertTrue(event['fit']['success'])
        self.poll(self.h,event)
        self.assertTrue(self.h.alignment_succeeded)
        self.assertEqual(self.core.active_observation.map_id,'nightmare-16')
        self.assertEqual(self.core.active_observation.frame_generation,412)
        self.assertTrue(self.core.can_display(cycle_id=115,map_id='nightmare-16',floor='1',frame_generation=412))
        self.assertTrue(any(name=='alignment-v2-runtime-committed' for name,_ in self.logs))

    def test_changed_pixels_do_not_authorize_old_worker_pose(self):
        event=self.produce()
        self.h.current_frame=np.full_like(self.h.current_frame,255)
        self.poll(self.h,event)
        self.assertFalse(self.core.can_display(cycle_id=115,map_id='nightmare-16',floor='1',frame_generation=412))
        self.assertFalse(any(name=='alignment-v2-runtime-committed' for name,_ in self.logs))

    def test_changed_floor_does_not_commit_old_floor(self):
        event=self.produce()
        self.h.current_floor='2'
        self.poll(self.h,event)
        self.assertFalse(any(name=='alignment-v2-runtime-committed' for name,_ in self.logs))
        self.assertNotIn('nightmare-16',self.core.pose_by_map)

    def test_closed_map_only_caches_successful_pose(self):
        event=self.produce()
        self.h.map_is_open=False
        self.poll(self.h,event)
        self.assertFalse(any(name=='alignment-v2-runtime-committed' for name,_ in self.logs))
        self.assertNotIn('nightmare-16',self.core.pose_by_map)

    def test_repeated_open_delivery_reaches_core_in_both_modes(self):
        for mode in ('nightmare','difficult'):
            for _ in range(3):
                with self.subTest(mode=mode):
                    self.h.game_mode=mode
                    self.h.current_frame_generation+=4
                    self.h.current_alignment_candidate={}
                    event=self.produce()
                    self.poll(self.h,event)
                    self.assertTrue(self.core.can_display(cycle_id=115,map_id='nightmare-16',floor='1',
                        frame_generation=self.h.current_frame_generation))

if __name__=='__main__':unittest.main()
