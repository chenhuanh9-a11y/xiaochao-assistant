"""Synthetic group/review lifecycle fixtures; no game or end-to-end timing."""
import copy
import queue
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import test_native_group_display as display_fixture
from test_native_similarity_runtime import IDS, runtime
from map_identity_engine import MapIdentityEngine


class GroupReviewTests(display_fixture.NativeGroupDisplayTests):
    # Reuse only the fixture; inherited baseline tests remain useful here.
    def prepare(self):
        h=self.host
        self.consume(self.event())
        h.events=queue.Queue();h.game_foreground=True;h.closed=False
        h.map_visual_suppressed=False;h.alignment_in_progress=False
        h.route_profile_id='fixture';h._native_similarity_member_committed=lambda *a,**kw:False
        return h

    def terminal(self,h):
        request=h._native_group_review_request
        request['worker'].join(2)
        self.assertFalse(request['worker'].is_alive())
        return h.events.get_nowait()

    def winner(self,mid):
        row=dict(mapId=mid,identityHardGatePassed=True,
                 difficultEntry=self.f.current_side((mid,))[0])
        return [row]

    def test_single_slot_survives_worker_exit_until_terminal_consumption(self):
        h=self.prepare();calls=[]
        h.engine.rank_established_group=lambda *a,**kw:(calls.append(kw) or [])
        self.assertTrue(h._start_native_group_review())
        event=self.terminal(h)
        h.current_frame_generation+=1
        h.current_frame=np.ones_like(h.current_frame)*200
        h._native_group_review_started-=2
        self.assertFalse(h._start_native_group_review())
        self.assertFalse(h._consume_native_group_review(event))
        self.assertTrue(h._start_native_group_review())
        second=self.terminal(h)
        self.assertFalse(h._consume_native_group_review(event))
        self.assertIs(h._native_group_review_request,second['request'])
        self.assertFalse(h._consume_native_group_review(second))
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[0]['budget_seconds'],.2)

    def test_slow_job_cannot_touch_foreground_and_changed_frame_is_rejected(self):
        h=self.prepare();release=threading.Event();entered=threading.Event()
        def slow(*a,**kw):
            entered.set();release.wait(2);return self.winner(IDS[1])
        h.engine.rank_established_group=slow
        self.assertTrue(h._start_native_group_review());self.assertTrue(entered.wait(1))
        before=h.gate.confirmed_id
        for i in range(10):
            h.current_frame=np.full_like(h.current_frame,i+1)
            h.current_frame_generation+=1
            self.assertFalse(h._start_native_group_review())
        release.set();event=self.terminal(h)
        self.assertFalse(h._consume_native_group_review(event))
        self.assertEqual(h.gate.confirmed_id,before)
        self.assertIsNone(getattr(h,'front_group_sibling_pending',None))

    def test_stable_identical_pixels_after_changed_frames_accept_without_generation_starvation(self):
        h=self.prepare();source=h.gate.confirmed_id
        h.engine.rank_established_group=lambda *a,**kw:self.winner(source)
        h._start_native_group_review();event=self.terminal(h)
        h.current_frame_generation+=7
        h.current_frame=h.current_frame.copy()
        self.assertTrue(h._consume_native_group_review(event))
        self.assertEqual(h.native_similarity_state.pending_identity,
                         (source,'1',h._native_similarity_context()))

    def test_errors_and_insufficient_evidence_leave_live_state_untouched(self):
        for error in (False,True):
            with self.subTest(error=error):
                h=self.prepare();before=h.native_similarity_state.snapshot()
                def run(*a,**kw):
                    if error:raise RuntimeError('fixture')
                    return []
                h.engine.rank_established_group=run
                self.assertTrue(h._start_native_group_review())
                self.assertFalse(h._consume_native_group_review(self.terminal(h)))
                self.assertEqual(h.native_similarity_state.snapshot(),before)
                h._native_group_review_last_scope=None;h._native_group_review_started-=2

    def test_same_member_waits_for_real_pose_confirmation(self):
        h=self.prepare();source=h.gate.confirmed_id;commits=[]
        h._native_similarity_member_committed=lambda *a,**kw:commits.append((a,kw))
        h.engine.rank_established_group=lambda *a,**kw:self.winner(source)
        self.assertTrue(h._start_native_group_review())
        self.assertTrue(h._consume_native_group_review(self.terminal(h)))
        self.assertEqual(h.native_similarity_state.state,'GROUP_LOCKED')
        self.assertEqual(h.native_similarity_state.pending_identity[0],source)
        self.assertFalse(commits[0][1]['global_verified'])
        self.assertEqual(h.gate.confirmed_id,source)

    def test_target_staged_without_clearing_source_and_commits_after_validation(self):
        h=self.prepare();source=h.gate.confirmed_id;calls=[]
        h.engine.rank_established_group=lambda *a,**kw:self.winner(IDS[1])
        h._start_alignment_v2_first_pose_worker=lambda *a,**kw:calls.append((a,kw))
        self.assertTrue(h._start_native_group_review())
        self.assertTrue(h._consume_native_group_review(self.terminal(h)))
        self.assertEqual(h.gate.confirmed_id,source)
        self.assertEqual(h.native_similarity_state.provisional_member_id,source)
        pending=h.front_group_sibling_pending
        event=dict(mapId=IDS[1],cycleId=h.active_map_cycle_id,
                   sourceFrameGeneration=h.current_frame_generation,
                   alignmentRequestId=pending['requestId'])
        self.assertTrue(h._front_group_sibling_event_is_current(event))
        self.assertFalse(h._prepare_front_group_sibling_commit(dict(event,fit={})))
        self.assertEqual(h.gate.confirmed_id,source)
        # The preparation/paint itself remains covered by the production sibling
        # transaction tests. Here test the group publication only after its gate.
        h.gate.select(IDS[1]);h.gate.confirm()
        self.assertTrue(h._commit_front_group_sibling_state())
        self.assertEqual(h.native_similarity_state.provisional_member_id,IDS[1])
        self.assertEqual(h.native_similarity_state.state,'GROUP_LOCKED')
        self.assertIsNone(h.front_group_sibling_pending)

    def test_floor_surface_mode_and_resource_epochs_reject_background_result(self):
        for key,value in [('current_floor','2'),('surface_visibility_generation',3),
                          ('game_mode','difficult'),('engine',SimpleNamespace())]:
            with self.subTest(key=key):
                self.setUp();h=self.prepare()
                h.engine.rank_established_group=lambda *a,**kw:self.winner(IDS[1])
                h._start_native_group_review();event=self.terminal(h)
                setattr(h,key,value)
                self.assertFalse(h._consume_native_group_review(event))
                self.assertEqual(h.gate.confirmed_id,IDS[0])

    def test_final_member_and_busy_foreground_do_not_start_identity_job(self):
        h=self.prepare()
        for state,busy in [('MEMBER_VERIFIED',False),('GROUP_LOCKED',True)]:
            h.native_similarity_state.state=state;h.alignment_in_progress=busy
            h.engine.rank_established_group=lambda *a,**kw:self.fail('must not rank')
            self.assertFalse(h._start_native_group_review())

    def test_prior_controls_full_group_scope_and_bad_binding_is_rejected(self):
        h=self.prepare();engine=MapIdentityEngine.__new__(MapIdentityEngine)
        engine.pack=self.f.pack;engine.native_similarity_index=self.f.index
        engine.entry_shared_reference_provider=self.f.provider;calls=[]
        engine.rank=lambda *a,**kw:(calls.append(kw) or [])
        prior=h.native_similarity_state.context_snapshot()
        engine.rank_established_group(h.current_frame,'1',prior=prior,budget_seconds=.2)
        self.assertEqual(set(calls[0]['_established_members']),set(IDS))
        calls.clear()
        for bad in (None,('difficult',*prior[1:]),(prior[0],prior[1],'wrong',prior[3]),
                    (*prior[:3],(('unknown',(0,)),))):
            self.assertEqual(engine.rank_established_group(h.current_frame,'1',prior=bad,budget_seconds=.2),[])
        self.assertEqual(calls,[])

    def test_old_target_attempt_does_not_block_later_stable_review(self):
        h=self.prepare();h.engine.rank_established_group=lambda *a,**kw:self.winner(IDS[1])
        h._start_alignment_v2_first_pose_worker=lambda *a,**kw:None
        h._start_native_group_review();h._consume_native_group_review(self.terminal(h))
        old=h.front_group_sibling_pending
        h.current_frame_generation+=1;h.current_frame[:]=200
        h._native_group_review_started-=2
        self.assertTrue(h._start_native_group_review())
        self.assertIsNone(h.front_group_sibling_pending)
        self.assertEqual(h.gate.confirmed_id,IDS[0])
        self.assertFalse(h._front_group_sibling_event_is_current(dict(mapId=IDS[1],
            cycleId=old['cycleId'],sourceFrameGeneration=old['frameGeneration'],
            alignmentRequestId=old['requestId'])))
        h._consume_native_group_review(self.terminal(h))

    def test_provisional_badge_does_not_mutate_shared_cached_pixels(self):
        pixels=np.zeros((60,240,4),np.uint8);pixels[:,:,3]=255;before=pixels.copy()
        result=runtime.DailyAssistant._mark_provisional_reference(pixels)
        np.testing.assert_array_equal(pixels,before)
        self.assertFalse(np.array_equal(result,before))
        np.testing.assert_array_equal(result[40:],before[40:])

    def test_two_established_cohorts_keep_every_rival_in_original_rank_path(self):
        h=self.prepare()
        for expanded,expected in [(False,IDS),(True,IDS[:2])]:
            state=type(h.native_similarity_state)()
            proposal=self.f.propose_condition(np.zeros((128,128),np.uint8),
                front=self.f.front_rows(IDS[:2] if expanded else IDS,expanded=expanded))
            state.accept(proposal,('fixture',),1)
            prior=state.context_snapshot()
            self.assertIsNotNone(prior)
            engine=MapIdentityEngine.__new__(MapIdentityEngine)
            engine.pack=self.f.pack;engine.identity_floors=('1','2');engine.identity_profile_id='zhan10-speed'
            engine.native_similarity_index=self.f.index;engine.entry_shared_reference_provider=self.f.provider
            calls=[]
            def produce(frame,floor,**kw):
                calls.append(set(kw['candidate_map_ids']))
                return [],dict(complete=True,verificationComplete=True,reason='fixture-empty',elapsedMs=0)
            engine.entry_identity=SimpleNamespace(rank=produce,geometry=lambda _:None)
            engine.side_entry_identity=SimpleNamespace(rank=produce)
            engine.side_entry_error=None;engine._shared_entry_inventory=lambda _:None
            engine.native_similarity_priority_provider=None
            engine._rank_source_structure_candidates=lambda *a,**kw:self.fail('global source discovery')
            engine.entry_advisory=lambda *a:self.fail('global icon advice')
            engine.front_gate_prefilter=SimpleNamespace(select=lambda *a:self.fail('must not narrow group again'))
            rows=engine.rank_established_group(h.current_frame,'1',prior=prior,budget_seconds=1)
            self.assertEqual({r['mapId'] for r in rows},set(expected))
            self.assertTrue(all(scope==set(expected) for scope in calls))
            self.assertTrue(all(not row['identityHardGatePassed'] for row in rows))

    def test_difficult_uses_same_scoped_rank_and_keeps_missing_inventory_suppression(self):
        from pathlib import Path
        from test_native_similarity_runtime import ConditionFixture
        from difficult_native_identity import NativeDifficultCandidateEngine
        f=ConditionFixture(Path(self.temp.name)/'difficult')
        f.pack.mode='difficult'
        for row in f.payload['references']:
            row['mode']='difficult';row['referenceId']=row['referenceId'].replace('nightmare/','difficult/')
        for row in f.payload['poses']:
            row['target']=row['target'].replace('nightmare/','difficult/')
        for row in f.payload['domains']:
            row['source']=row['source'].replace('nightmare/','difficult/')
        for row in f.payload['conditions']:row['mode']='difficult'
        f.write()
        state=type(self.host.native_similarity_state)()
        state.accept(f.propose_condition(np.zeros((128,128),np.uint8)),('fixture',),1)
        prior=state.context_snapshot();self.assertEqual(prior[0],'difficult')
        engine=NativeDifficultCandidateEngine.__new__(NativeDifficultCandidateEngine)
        engine.pack=f.pack;engine.native_similarity_index=f.index;engine.entry_shared_reference_provider=f.provider
        captured=[]
        def original(self,frame,floor,limit,**kw):
            captured.append(kw['_established_members'])
            return [dict(mapId=IDS[0],identityHardGatePassed=True)]
        with patch.object(MapIdentityEngine,'rank',original):
            rows=engine.rank_established_group(np.zeros((2,2,3),np.uint8),'1',prior=prior,budget_seconds=1)
            self.assertTrue(rows[0]['identityHardGatePassed'])
            f.pack.by_id[IDS[2]].layers['1']=None
            rows=engine.rank_established_group(np.zeros((2,2,3),np.uint8),'1',prior=prior,budget_seconds=1)
            self.assertFalse(rows[0]['identityHardGatePassed'])
            self.assertEqual(rows[0]['nativeInventory']['missingMapIds'],[IDS[2]])
        self.assertTrue(all(set(scope)==set(IDS) for scope in captured))

    def test_floor_transition_retains_family_but_not_old_floor_pose_or_identity(self):
        h=self.prepare();prior=h._established_group_prior();hidden=[]
        h._hide_spatial_route=lambda reason:hidden.append(reason)
        h._invalidate_floor_switch_work(h.gate.confirmed_id,'1','2',reason='fixture-floor-change')
        self.assertEqual(h.native_similarity_state.state,'UNLOCKED')
        self.assertIsNone(h.gate.confirmed_id)
        self.assertEqual(h._established_group_prior(),prior)
        self.assertEqual(hidden,['front-group-floor-changed','fixture-floor-change'])
        h.engine=SimpleNamespace()
        self.assertIsNone(h._established_group_prior())

    def test_final_identity_survives_floor_change_and_can_reopen_on_new_floor(self):
        h=self.prepare();state=h.native_similarity_state;source=h.gate.confirmed_id
        state.state='MEMBER_VERIFIED';state.verified_member_id=source
        h.active_confirmation=dict(mapId=source,floor='1')
        h._hide_spatial_route=lambda reason:None
        h._invalidate_floor_switch_work(source,'1','2',reason='fixture-floor-change')
        h.current_floor='2'
        self.assertEqual(state.state,'MEMBER_VERIFIED')
        self.assertEqual(h.gate.confirmed_id,source)
        self.assertIsNotNone(h._established_group_prior())
        for _ in range(2):
            h.current_frame_generation+=1
            event=dict(mapId=source,floor='2',cycleId=h.active_map_cycle_id,
                sourceFrameGeneration=h.current_frame_generation,
                frontGroupDifferenceEvidence=dict(confident=True,candidateMapId=IDS[1]))
            reopened=h._native_similarity_strong_conflict(event)
        self.assertTrue(reopened)
        h._check_native_similarity_resources()
        self.assertEqual(state.state,'GROUP_LOCKED')
        self.assertEqual(h._front_group_provisional_lock_context()['floor'],'2')
        self.assertIsNone(h.active_confirmation)
        # The existing source is not authorized on the new floor by this lock.
        h.alignment_core_v2=SimpleNamespace(display_authorization=None,can_display=lambda **kw:False)
        self.assertFalse(h._provisional_reference_is_verified())
        h._invalidate_floor_switch_work(source,'2','2',reason='fixture-recheck')
        self.assertEqual(state.state,'GROUP_LOCKED')
        # A subsequent unrelated floor again drops only the provisional pose.
        h._native_similarity_floor_changed('B1')
        self.assertEqual(state.state,'UNLOCKED')
        self.assertIsNotNone(h._established_group_prior())

    def test_two_strong_current_contradictions_reopen_final_member_without_attempt_cap(self):
        h=self.prepare();state=h.native_similarity_state;source=h.gate.confirmed_id
        for cycle in range(3):
            state.state='MEMBER_VERIFIED';state.verified_member_id=source
            h.active_confirmation={'mapId':source}
            event=dict(mapId=source,floor='1',cycleId=h.active_map_cycle_id,
                sourceFrameGeneration=h.current_frame_generation,
                frontGroupDifferenceEvidence=dict(confident=True,candidateMapId=IDS[1]))
            self.assertFalse(h._native_similarity_strong_conflict(event))
            self.assertFalse(h._native_similarity_strong_conflict(event))
            h.current_frame_generation+=1;event['sourceFrameGeneration']=h.current_frame_generation
            self.assertTrue(h._native_similarity_strong_conflict(event))
            self.assertEqual(state.state,'GROUP_LOCKED');self.assertIsNone(h.active_confirmation)
            self.assertEqual(h.gate.confirmed_id,source)
            h.current_frame_generation+=1
        state.state='MEMBER_VERIFIED';state.verified_member_id=source
        event['fit']={'selectedRouteCurrentFrameResidual':{'verified':False}}
        event.pop('frontGroupDifferenceEvidence')
        for _ in range(4):
            h.current_frame_generation+=1;event['sourceFrameGeneration']=h.current_frame_generation
            self.assertFalse(h._native_similarity_strong_conflict(event))
        self.assertEqual(state.state,'MEMBER_VERIFIED')
        h.gate.confirmed_id=IDS[1]
        event['fit']['entryCrossCheck']={'contradicted':True}
        for _ in range(2):
            h.current_frame_generation+=1;event['sourceFrameGeneration']=h.current_frame_generation
            self.assertFalse(h._native_similarity_strong_conflict(event))
        self.assertEqual(state.state,'MEMBER_VERIFIED')

    def test_real_failure_normalizer_routes_strong_evidence_to_reopening(self):
        h=self.prepare();source=h.gate.confirmed_id;state=h.native_similarity_state
        state.state='MEMBER_VERIFIED';state.verified_member_id=source
        h.active_confirmation=dict(mapId=source,floor='1')
        for _ in range(2):
            h.current_frame_generation+=1
            event=h._front_group_v2_failure_event(source,'1',dict(
                cycleId=h.active_map_cycle_id,sourceFrameGeneration=h.current_frame_generation,
                alignmentV2WorkerFit=dict(selectedRouteCurrentFrameResidual=dict(verified=False),
                    entryCrossCheck=dict(contradicted=True))))
            self.assertIsNotNone(event)
            h._handle_front_group_alignment_failure(event)
        self.assertEqual(state.state,'GROUP_LOCKED')
        self.assertIsNone(h.active_confirmation)

    def test_same_member_finalizes_only_with_current_global_pose_then_stops_review(self):
        h=self.prepare();source=h.gate.confirmed_id
        del h._native_similarity_member_committed
        h.current_alignment_candidate=dict(globalResidualVerified=True)
        h.alignment_core_v2=SimpleNamespace(display_authorization=object(),can_display=lambda **kw:True)
        h._remember_confirmation=lambda mid,floor:setattr(h,'active_confirmation',dict(mapId=mid,floor=floor))
        h.engine.rank_established_group=lambda *a,**kw:self.winner(source)
        h._start_native_group_review();event=self.terminal(h)
        self.assertTrue(h._consume_native_group_review(event))
        self.assertEqual(h.native_similarity_state.state,'MEMBER_VERIFIED')
        self.assertEqual(h.active_confirmation['mapId'],source)
        h.current_frame_generation+=1;h._native_group_review_started-=2
        self.assertFalse(h._start_native_group_review())

    def test_successful_pose_commit_breaks_strong_conflict_sequence(self):
        h=self.prepare();state=h.native_similarity_state;source=h.gate.confirmed_id
        state.state='MEMBER_VERIFIED';state.verified_member_id=source
        event=dict(mapId=source,floor='1',cycleId=h.active_map_cycle_id,
            sourceFrameGeneration=h.current_frame_generation,
            frontGroupDifferenceEvidence=dict(confident=True,candidateMapId=IDS[1]))
        self.assertFalse(h._native_similarity_strong_conflict(event))
        h.alignment_core_v2=SimpleNamespace(display_authorization=object(),can_display=lambda **kw:True)
        runtime.DailyAssistant._native_similarity_member_committed(h,source,'1',global_verified=True)
        self.assertIsNone(h._native_group_conflicts)
        h.current_frame_generation+=1;event['sourceFrameGeneration']=h.current_frame_generation
        self.assertFalse(h._native_similarity_strong_conflict(event))
        self.assertEqual(state.state,'MEMBER_VERIFIED')

    def test_cancelled_group_job_releases_shared_rank_lane_only_at_delivery(self):
        h=self.prepare();h.engine.rank_established_group=lambda *a,**kw:self.winner(IDS[1])
        h._start_native_group_review();event=self.terminal(h)
        h._cancel_rerank_work()
        self.assertIs(h._identity_rank_request,event['request'])
        self.assertFalse(h._consume_native_group_review(event))
        self.assertIsNone(h._identity_rank_request)
        self.assertEqual(h.gate.confirmed_id,IDS[0])

    def test_provisional_reference_requires_current_core_authorization(self):
        h=self.prepare();h.pack=self.f.pack
        h._current_frame_floor_conflicts=lambda _:False
        h._current_frame_matches_display_environment=lambda:True
        h._round_identity_lock_context=lambda *a:dict(provisional=True)
        h.alignment_core_v2=SimpleNamespace(display_authorization=object(),can_display=lambda **kw:False)
        self.assertFalse(h._reference_sheet_may_show())
        h.alignment_core_v2.can_display=lambda **kw:True
        self.assertTrue(h._reference_sheet_may_show())
        h._remember_reference_sheet_context();h.map_is_open=False
        self.assertTrue(h._reference_sheet_may_show())
        h.current_floor='2'
        self.assertFalse(h._reference_sheet_may_show())

if __name__=='__main__':unittest.main()
