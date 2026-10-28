"""Synthetic state replay of changed-floor unconfirmed recovery; no game timing."""
from types import SimpleNamespace
from unittest.mock import Mock
import sys
from pathlib import Path
import unittest
import queue
import threading
import time
import numpy as np
from test_reopen_lifecycle import method

sys.path.insert(0, str(Path(__file__).parents[1] / 'app'))
from native_similarity_groups import NativeSimilarityState


class Recovery(unittest.TestCase):
    def setUp(self):
        self.now = 10.
        self.retry = method('_retry_unconfirmed_changed_frame')
        self.retry.__globals__.update(np=np, time=SimpleNamespace(monotonic=lambda: self.now),
            map_frame_signature=lambda frame: frame.astype(float),
            map_frame_change_score=lambda a,b: float(np.mean(abs(a-b))))
        self.accept = method('_accept_unconfirmed_recovery_result')
        self.accept.__globals__['np'] = np

    def state(self, mode='nightmare'):
        return SimpleNamespace(map_is_open=True, game_foreground=True, floor_required=False,
            current_frame=np.zeros((3,3,3), dtype=np.uint8), current_bounds=(0,0,3,3),
            current_floor='B1', active_map_cycle_id=58, current_frame_generation=1,
            current_frame_floor_observation=(58,1,-1), pack=object(), game_mode=mode,
            gate=SimpleNamespace(confirmed_id=None), surface_visibility_generation=2,
            _rerank_current=Mock(return_value=SimpleNamespace(is_alive=lambda:False)),
            _log_core_chain_event=Mock())

    def test_provisional_floor_change_discards_only_unverified_identity(self):
        call = method('_native_similarity_floor_changed')
        for mode in ('nightmare', 'difficult'):
            group = NativeSimilarityState(state='GROUP_LOCKED', floor='1',
                condition_mode=mode, provisional_member_id='map', group_id='conditions')
            gate = SimpleNamespace(confirmed_id='map')
            gate.hide_for_new_frame = lambda: setattr(gate, 'confirmed_id', None)
            state = SimpleNamespace(native_similarity_state=group, gate=gate,
                                    _hide_spatial_route=Mock())
            call(state, 'B1')
            self.assertIsNone(gate.confirmed_id)
            self.assertEqual(group.state, 'UNLOCKED')
            self.assertIsNone(group.provisional_member_id)
            state._hide_spatial_route.assert_called_once_with('front-group-floor-changed')

    def test_same_open_changed_pixels_retry_once_and_identical_pixels_do_not_loop(self):
        for mode in ('nightmare', 'difficult'):
            state=self.state(mode)
            self.now=10.; self.assertFalse(self.retry(state))
            for _ in range(20):
                self.now += 1.; self.assertFalse(self.retry(state))
            state.current_frame[:] = 1
            self.assertTrue(self.retry(state))
            for _ in range(20):
                self.now += 1.; self.assertFalse(self.retry(state))
            state._rerank_current.assert_called_once()
            self.assertIsNone(state.gate.confirmed_id)

    def test_busy_worker_and_rate_limit_bound_changed_pixel_requests(self):
        state=self.state(); self.retry(state)
        state.current_frame[:] = 1; self.now += .5
        self.assertFalse(self.retry(state))
        state._unconfirmed_identity_worker=SimpleNamespace(is_alive=lambda:True)
        self.now += 2.; self.assertFalse(self.retry(state))
        state._unconfirmed_identity_worker=None
        self.assertTrue(self.retry(state))

    def test_no_retry_without_current_floor_or_after_lock_close_suppression(self):
        for field,value in (('floor_required',True), ('map_is_open',False),
                            ('map_visual_suppressed',True), ('game_foreground',False),
                            ('capture_in_progress',True), ('current_frame_floor_observation',(58,0,-1))):
            state=self.state(); self.retry(state)
            state.current_frame[:]=1; self.now+=2
            setattr(state,field,value)
            self.assertFalse(self.retry(state)); state._rerank_current.assert_not_called()
        state=self.state();state.gate.confirmed_id='map'
        self.assertFalse(self.retry(state))

    def event(self, state):
        return dict(unconfirmedRecoveryContext=(58,'B1',id(state.pack),state.game_mode,2),
            frame=state.current_frame.copy(), bounds=state.current_bounds, rankSourceFrameGeneration=1)

    def test_late_result_never_rewinds_pixels_and_retries_current_frame(self):
        state=self.state(); self.retry(state)
        event=self.event(state); state.current_frame[:]=1; state.current_frame_generation=2
        state.current_frame_floor_observation=(58,2,-1)
        self.assertFalse(self.accept(state,event))
        self.assertTrue(state._unconfirmed_identity_retry_pending)
        self.assertTrue(np.all(state.current_frame==1))
        self.now+=2; self.assertTrue(self.retry(state))
        fresh=self.event(state)
        self.assertTrue(self.accept(state,fresh))
        self.assertIs(fresh['frame'],state.current_frame)
        self.assertEqual(fresh['rankSourceFrameGeneration'],2)
        self.assertIsNone(state.gate.confirmed_id)

    def test_late_close_floor_pack_surface_or_new_lock_cannot_apply(self):
        for field,value in (('map_is_open',False),('current_floor','2'),
                            ('pack',object()),('surface_visibility_generation',3),
                            ('active_map_cycle_id',60),('floor_required',True)):
            state=self.state(); event=self.event(state); setattr(state,field,value)
            self.assertFalse(self.accept(state,event))
        state=self.state();event=self.event(state);state.gate.confirmed_id='new'
        self.assertFalse(self.accept(state,event))

    def test_rank_exception_has_terminal_event_and_releases_worker(self):
        call = method('_rerank_current')
        call.__globals__.update(threading=threading, time=time, AUTO_IDENTITY_BUDGET_SECONDS=.1)
        state=self.state(); state.capture_generation=58; state.events=queue.Queue()
        scope = method('_rerank_scope')
        state._rerank_scope = lambda floor: scope(state, floor)
        state._front_group_provisional_lock_context=lambda: None
        state.engine=SimpleNamespace(preload_status=lambda _: {}, rank=Mock(side_effect=ValueError('fixture')))
        context=self.event(state)['unconfirmedRecoveryContext']
        worker=call(state,'B1','automatic-stable',recovery_context=context)
        event=state.events.get(timeout=2); worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(event['kind'],'unconfirmed-identity-rank-error')
        self.assertEqual(event['errorType'],'ValueError')

    def test_ambiguous_rank_seeds_baseline_before_first_changed_probe(self):
        call=method('_open_candidate_shell_automatically')
        call.__globals__.update(np=np, time=SimpleNamespace(monotonic=lambda:self.now),
                                map_frame_signature=lambda f:f.astype(float))
        state=self.state();state._round_identity_lock_context=lambda:None
        state._log=Mock();state._sync_recognition_progress=Mock()
        self.assertFalse(call(state,'unlocked-ranked-candidates-ready'))
        state.current_frame[:]=1;self.now+=2
        self.assertTrue(self.retry(state))


if __name__=='__main__': unittest.main()
