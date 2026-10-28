"""Real floor -> rerank -> passive retry seam; controlled engine/clock, no GUI."""
import queue
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest
import numpy as np
from test_reopen_lifecycle import method


class Host:
    pass


for name in ('_rerank_scope', '_rerank_current', '_cancel_rerank_work',
             '_finish_rerank_request', '_drain_pending_rerank',
             '_retry_unconfirmed_changed_frame', '_accept_unconfirmed_recovery_result',
             '_consider_floor_observation'):
    call = method(name)
    call.__globals__.update(np=np, time=time, threading=threading,
        AUTO_IDENTITY_BUDGET_SECONDS=1.85, display_event_is_current=lambda a,b:a==b,
        stable_floor_from_observations=lambda *args,**kwargs:-1,
        map_frame_signature=lambda f:f.astype(float),
        map_frame_change_score=lambda a,b:float(np.mean(abs(a-b))))
    setattr(Host, name, call)


class SharedLane(unittest.TestCase):
    def setUp(self):
        self.now=10.
        self.clock=patch.dict(Host._retry_unconfirmed_changed_frame.__globals__,
            time=SimpleNamespace(monotonic=lambda:self.now))
        self.clock.start();self.addCleanup(self.clock.stop)
        self.release=threading.Event();self.entered=threading.Event()
        self.calls=[];self.active=0;self.max_active=0;self.lock=threading.Lock()
        def rank(frame,**kwargs):
            with self.lock:
                self.active+=1;self.max_active=max(self.max_active,self.active)
                self.calls.append(float(frame.mean()));self.entered.set()
            try:
                if not self.release.wait(2):raise TimeoutError('test-release-not-set')
                return [{'mapId':'fixture', 'identityHardGatePassed':True}]
            finally:
                with self.lock:self.active-=1
        self.h=h=Host()
        h.__dict__.update(map_is_open=True, game_foreground=True, floor_required=True,
            current_frame=np.zeros((3,3,3),np.uint8),current_bounds=(0,0,3,3),
            current_floor='B1',active_map_cycle_id=58,current_frame_generation=1,
            current_frame_floor_observation=None,capture_generation=58,
            pack=SimpleNamespace(by_id={}),game_mode='nightmare',
            gate=SimpleNamespace(confirmed_id=None),surface_visibility_generation=2,
            events=queue.Queue(),engine=SimpleNamespace(preload_status=lambda _: {},rank=rank),
            _front_group_provisional_lock_context=lambda:None,_log_core_chain_event=Mock(),
            _mode_selected_floor=lambda f:{'selectedFloor':-1},floor_observations=[],
            floor_manual_override=False,floor_hard_locked=False,manual_floor_hold_until=0,
            runtime_timeline_enabled=False,_set_status=Mock())
        self.addCleanup(self.stop)

    def stop(self):
        self.release.set()
        request=getattr(self.h,'_identity_rank_request',None)
        worker=(request or {}).get('worker')
        if worker is not None and worker.ident is not None:worker.join(2)

    def begin_floor(self):
        h=self.h
        h._consider_floor_observation(h.current_frame,h.current_bounds,58)
        self.assertTrue(self.entered.wait(1))
        self.assertFalse(h.floor_required)
        return h._identity_rank_request

    def frame(self,value):
        h=self.h;h.current_frame=np.full((3,3,3),value,np.uint8)
        h.current_frame_generation+=1
        h.current_frame_floor_observation=(h.active_map_cycle_id,h.current_frame_generation,-1)
        self.now+=1.1

    def terminal(self):
        event=self.h.events.get(timeout=2)
        event['rankRequest']['worker'].join(2)
        return event

    def test_actual_floor_producer_and_retry_share_slot_in_both_modes(self):
        for mode in ('nightmare','difficult'):
            with self.subTest(mode=mode):
                h=self.h;h.game_mode=mode;h.floor_required=True
                self.release.clear();self.entered.clear()
                request=self.begin_floor()
                self.assertFalse(h._retry_unconfirmed_changed_frame())
                self.frame(int(h.current_frame.mean())+1)
                self.assertFalse(h._retry_unconfirmed_changed_frame())
                self.assertIs(h._identity_rank_request,request)
                self.release.set();event=self.terminal()
                self.assertIsNotNone(event['unconfirmedRecoveryContext'])
                self.assertTrue(h._finish_rerank_request(event))
                self.assertFalse(h._accept_unconfirmed_recovery_result(event))
        self.assertEqual(self.max_active,1)
        self.assertEqual(len(self.calls),2)

    def test_finished_worker_keeps_slot_until_terminal_consumed(self):
        h=self.h;request=self.begin_floor();self.release.set();event=self.terminal()
        self.assertFalse(request['worker'].is_alive())
        self.frame(1);h._retry_unconfirmed_changed_frame();self.frame(2)
        self.assertFalse(h._retry_unconfirmed_changed_frame())
        returned=h._rerank_current('B1','automatic-stable')
        self.assertIs(returned,request['worker']);self.assertEqual(len(self.calls),1)
        self.assertTrue(h._finish_rerank_request(event))
        self.assertFalse(h._accept_unconfirmed_recovery_result(event))
        h._drain_pending_rerank();new=self.terminal()
        self.assertTrue(h._finish_rerank_request(new))
        self.assertTrue(h._accept_unconfirmed_recovery_result(new))
        self.assertEqual(self.calls,[0.,2.]);self.assertEqual(self.max_active,1)

    def test_continuous_changes_coalesce_then_stable_valid_result_is_accepted(self):
        h=self.h;self.begin_floor()
        for value in range(1,21):
            self.frame(value)
            h._rerank_current('B1','automatic-stable')
            self.assertFalse(h._retry_unconfirmed_changed_frame())
        self.assertEqual(len(self.calls),1)
        self.release.set();old=self.terminal()
        self.assertTrue(h._finish_rerank_request(old))
        self.assertFalse(h._accept_unconfirmed_recovery_result(old))
        h._drain_pending_rerank();fresh=self.terminal()
        self.assertTrue(h._finish_rerank_request(fresh))
        self.assertTrue(h._accept_unconfirmed_recovery_result(fresh))
        self.assertEqual(fresh['ranked'][0]['identityHardGatePassed'],True)
        self.assertIs(fresh['frame'],h.current_frame)
        self.assertEqual(self.calls,[0.,20.]);self.assertEqual(self.max_active,1)
        # Accepting a producer event alone never commits identity/display.
        self.assertIsNone(h.gate.confirmed_id)

    def test_cancel_retains_running_slot_and_old_terminal_cannot_release_new(self):
        h=self.h;old_request=self.begin_floor();h._cancel_rerank_work()
        self.assertIs(h._identity_rank_request,old_request)
        h.active_map_cycle_id=h.capture_generation=60;self.frame(4)
        h._rerank_current('B1','automatic-stable')
        self.release.set();old=self.terminal()
        self.assertFalse(h._finish_rerank_request(old))
        h._drain_pending_rerank();new_request=h._identity_rank_request
        self.assertFalse(h._finish_rerank_request(old))
        self.assertIs(h._identity_rank_request,new_request)
        new=self.terminal();self.assertTrue(h._finish_rerank_request(new))
        self.assertTrue(h._accept_unconfirmed_recovery_result(new))

    def test_error_terminal_releases_slot_and_new_request_succeeds(self):
        h=self.h;h.floor_required=False
        original=h.engine.rank;h.engine.rank=Mock(side_effect=ValueError('fixture'))
        h._rerank_current('B1','automatic-stable');error=self.terminal()
        self.assertEqual(error['kind'],'unconfirmed-identity-rank-error')
        self.assertIsNotNone(h._identity_rank_request)
        self.assertTrue(h._finish_rerank_request(error));self.assertIsNone(h._identity_rank_request)
        h.engine.rank=original;self.release.set()
        h._rerank_current('B1','automatic-stable');event=self.terminal()
        self.assertTrue(h._finish_rerank_request(event))
        self.assertTrue(h._accept_unconfirmed_recovery_result(event))

    def test_manual_floor_has_provenance_and_new_lock_discards_pending(self):
        h=self.h;h.floor_required=False
        h._rerank_current('B1','manual');self.assertTrue(self.entered.wait(1))
        h._rerank_current('B1','manual');self.release.set();event=self.terminal()
        self.assertEqual(event['floorSource'],'manual')
        self.assertIsNotNone(event['unconfirmedRecoveryContext'])
        self.assertTrue(h._finish_rerank_request(event))
        self.assertTrue(h._accept_unconfirmed_recovery_result(event))
        h.gate.confirmed_id='new-verified-map';h._drain_pending_rerank()
        self.assertIsNone(h._pending_rerank_request)
        self.assertEqual(len(self.calls),1)

    def test_capture_invalidation_cancels_but_does_not_release_running_slot(self):
        h=self.h;request=self.begin_floor()
        h._set_sheet_move_mode=Mock();h._invalidate_alignment_work=Mock()
        h._invalidate_round_group_edge_correction=Mock()
        method('_invalidate_capture_work')(h)
        self.assertTrue(request['cancelled'].is_set())
        self.assertIs(h._identity_rank_request,request)
        self.assertEqual(h.capture_generation,59)
        self.release.set();self.assertFalse(h._finish_rerank_request(self.terminal()))
        self.assertIsNone(h._identity_rank_request)

    def test_thread_start_failure_is_a_consumable_terminal(self):
        h=self.h;h.floor_required=False
        fake=Mock();fake.start.side_effect=RuntimeError('fixture-start-failed')
        fake.ident=None
        with patch.object(threading,'Thread',return_value=fake):
            self.assertIs(h._rerank_current('B1','automatic-stable'),fake)
        event=h.events.get(timeout=1)
        self.assertIsNotNone(h._identity_rank_request)
        self.assertEqual(event['errorType'],'RuntimeError')
        self.assertTrue(h._finish_rerank_request(event))
        self.assertIsNone(h._identity_rank_request)

    def test_pending_automatic_request_waits_for_current_floor_evidence(self):
        h=self.h;self.begin_floor()
        h._rerank_current('B1','automatic-stable');self.release.set();event=self.terminal()
        self.assertTrue(h._finish_rerank_request(event))
        self.frame(3);h.current_frame_floor_observation=None
        h._drain_pending_rerank();self.assertEqual(len(self.calls),1)
        self.assertIsNotNone(h._pending_rerank_request)
        h.current_frame_floor_observation=(58,h.current_frame_generation,-1)
        h._drain_pending_rerank();fresh=self.terminal()
        self.assertTrue(h._finish_rerank_request(fresh))
        self.assertTrue(h._accept_unconfirmed_recovery_result(fresh))


if __name__=='__main__':unittest.main()
