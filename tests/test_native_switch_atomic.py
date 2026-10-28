"""Actual native staging/core/owner transaction with controlled pixels and sinks."""
import unittest
import threading
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import test_group_review_lifecycle as fixture
from alignment_core_v2 import AlignmentCoreV2, AlignmentCandidate


def prepare(test):
    t=fixture.GroupReviewTests(); t.setUp(); test.addCleanup(t.doCleanups)
    h=t.prepare(); source=fixture.IDS[0]; target=fixture.IDS[1]
    h.pack=t.f.pack; h.current_bounds=(0,0,40,40)
    h.route_profile_id='zhan10-speed'
    h.alignment_core_v2=core=AlignmentCoreV2()
    h._alignment_v2_runtime_is_enabled=lambda:True
    h._alignment_v2_structure_enabled=lambda:True
    h._shared_structure_mode_enabled=lambda **kw:True
    h.author_coordinate_adapters=SimpleNamespace(matrix_profile_to_canonical=lambda *a:np.eye(3))
    h._current_frame_floor_conflicts=lambda floor:False
    h._alignment_v2_seed_group_pose=lambda mid:None
    h._alignment_v2_note_worker_failure=lambda event:None
    h._alignment_v2_worker_pose_stale=lambda *a:False
    h._consume_b1_floor_transition_evidence=lambda mid,floor,prev,event:event
    h._alignment_v2_front_pose_seed=lambda *a:None
    h._open_cycle_transient_motion_active=lambda mid:False
    h._alignment_v2_verified_scale_observation=lambda *a:None
    h._active_open_cycle_drag_offset=lambda mid:np.zeros(2)
    h._active_open_cycle_zoom_transform=lambda mid:np.eye(3)
    h._active_mouse_drag_preview_offset=lambda:np.zeros(2)
    h._prime_round_layer_group_relations=lambda *a:None
    h._select_candidate_programmatically=lambda mid,**kw:setattr(h,'selected_ui',mid)
    h._log_core_chain_event=lambda *a,**kw:None
    h._start_alignment_v2_first_pose_worker=lambda *a,**kw:None
    h.selected_ui=source; h.surface_visible=True; h.hide_reasons=[]
    def hide(reason):
        h.surface_visible=False; h.hide_reasons.append(reason)
    h._hide_spatial_route=hide
    h._spatial_route_surface_is_visible=lambda:h.surface_visible
    h.aligned_overlay=SimpleNamespace(hide=lambda:hide('overlay.hide'))
    route=np.full((40,40,4),255,np.uint8)
    residual=dict(verified=True,poseVerifiedAgainstCanonicalGeometry=True,
        profileRenderDerivedFromCanonical=True,validationBasis='shared-canonical-geometry-current-frame',coordinatePackId='fixture')
    def candidate(mid):
        m=np.eye(3); m[0,2]=0 if mid==source else 3
        return AlignmentCandidate(mid,'1',m,'initial-structure-v2',h.current_frame_generation,
            current_frame_verified=True,residual_verified=True,global_residual_verified=True,
            metadata={'routeProfileId':h.route_profile_id})
    core.begin_observation(cycle_id=h.active_map_cycle_id,map_id=source,floor='1',frame_generation=h.current_frame_generation)
    grant=core.authorize(core.adjudicate(candidate(source)))
    assert core.commit(grant)
    assert h._commit_alignment_v2_round_pose(source,'1',route,h.current_frame,h.current_bounds,
        render_matrix=np.eye(3),canonical_matrix=np.eye(3),route_profile_id=h.route_profile_id,
        source='alignment-v2:initial-structure-v2',residual=residual)
    h.current_alignment_candidate=dict(accepted=True,mapId=source,floor='1',cycleId=h.active_map_cycle_id,
        globalResidualVerified=True)
    def snapshot():
        return dict(gate=h.gate.confirmed_id,selectedUi=h.selected_ui,
            nativeMember=h.native_similarity_state.provisional_member_id,
            groupMap=h.round_layer_group.map_id,corePoseMaps=sorted(core.pose_by_map),
            coreObservation=core.active_observation.map_id,
            sourceGrantActive=grant.active,sourceCanDisplay=core.can_display(authorization=grant,
                cycle_id=h.active_map_cycle_id,map_id=source,floor='1',frame_generation=h.current_frame_generation),
            visible=h.surface_visible,hideReasons=list(h.hide_reasons))
    before=snapshot()
    h.engine.rank_established_group=lambda *a,**kw:t.winner(target)
    assert h._start_native_group_review()
    assert h._consume_native_group_review(t.terminal(h))
    pending=h.front_group_sibling_pending
    assert pending['nativeSimilarity']
    c=candidate(target)
    h._alignment_v2_current_frame_candidate=lambda *a:dict(candidate=c,route=route,renderMatrix=c.matrix_source_to_client,residual=residual)
    h._build_display_overlay=lambda *a,**kw:(route.copy(),{'fixture':True})

    event=dict(alignmentV2WorkerFit=dict(success=True,selectedRouteCurrentFrameResidual=residual),
        sourceFrameGeneration=h.current_frame_generation,alignmentRequestId=pending['requestId'],
        sensorMode='front-group-sibling')
    return h, core, grant, snapshot, event


class NativeSwitchAtomicTests(unittest.TestCase):
    def run_case(self, effect):
        h,core,grant,snapshot,event=prepare(self)
        before=snapshot()
        def paint(*args,**kwargs):
            self.assertTrue(grant.active)
            self.assertEqual(core.active_observation.map_id,fixture.IDS[0])
            return effect(h)
        h._alignment_v2_show_overlay=paint
        return h,core,grant,snapshot,event,before

    def test_rejected_paint_restores_full_source(self):
        h,c,g,s,e,b=self.run_case(lambda h:False)
        result=h._run_alignment_v2_for_current_frame_unlocked(fixture.IDS[1],'1','1',e)
        self.assertEqual(result['reason'],'compositor-rejected')
        self.assertEqual(s(),b)
        self.assertIs(h.alignment_core_v2,c)
        self.assertIn('routeDisplayBridge',h.round_layer_group.tracking)

    def test_paint_exception_preserves_source_and_propagates(self):
        def fail(h): raise RuntimeError('sink failure')
        h,c,g,s,e,b=self.run_case(fail)
        with self.assertRaisesRegex(RuntimeError,'sink failure'):
            h._run_alignment_v2_for_current_frame_unlocked(fixture.IDS[1],'1','1',e)
        self.assertEqual(s(),b)

    def test_candidate_unavailable_preserves_source_opacity_and_drag_consumers(self):
        h,c,g,s,e=prepare(self)
        h.alignment_succeeded=True;h.alignment_in_progress=True
        h.closed=False;h.drag_current_overlay=np.ones((40,40,4),np.uint8)
        h.drag_transform=np.eye(3)
        h.root=SimpleNamespace(after_cancel=lambda *a:None)
        h._refresh_visible_sheet_after_route_setting=lambda:None
        calls=[];h._show_aligned_overlay=lambda *a,**kw:calls.append(True)
        h._alignment_v2_current_frame_candidate=lambda *a:None
        h._run_alignment_v2_for_current_frame_unlocked(fixture.IDS[1],'1','1',e)
        h._sheet_opacity_after_id=1;h._flush_sheet_opacity_update()
        self.assertEqual(len(calls),1)
        self.assertTrue(h.alignment_succeeded)
        self.assertFalse(h.alignment_in_progress)
        h._cancel_post_drag_probe=lambda:None
        h._cancel_mouse_drag_source_refresh=lambda:None
        self.assertTrue(h._begin_mouse_drag_prediction(h.active_map_cycle_id,probe_generation=0))

    def test_failed_target_does_not_restore_success_after_context_expires(self):
        for field,value in (('map_is_open',False),('current_floor','2'),
                            ('surface_visibility_generation',999)):
            with self.subTest(field=field):
                def expire(h): setattr(h,field,value);return False
                h,c,g,s,e,b=self.run_case(expire)
                h.alignment_succeeded=True
                h._run_alignment_v2_for_current_frame_unlocked(fixture.IDS[1],'1','1',e)
                self.assertFalse(h.alignment_succeeded)
                self.assertFalse(g.active)

    def test_failed_target_tokens_are_not_recycled(self):
        h,c,g,s,e,b=self.run_case(lambda h:False)
        old_id=c._next_authorization_id
        h._run_alignment_v2_for_current_frame_unlocked(fixture.IDS[1],'1','1',e)
        self.assertGreater(c._next_authorization_id,old_id)
        self.assertTrue(g.active)

    def test_close_during_paint_never_restores_expired_source(self):
        def close(h): h.map_is_open=False; return False
        h,c,g,s,e,b=self.run_case(close)
        h._run_alignment_v2_for_current_frame_unlocked(fixture.IDS[1],'1','1',e)
        self.assertFalse(g.active)
        self.assertFalse(h.surface_visible)
        self.assertNotIn(fixture.IDS[1],c.pose_by_map)

    def test_new_frame_during_paint_cannot_commit_or_restore_grant(self):
        def advance(h): h.current_frame_generation+=1; return True
        h,c,g,s,e,b=self.run_case(advance)
        result=h._run_alignment_v2_for_current_frame_unlocked(fixture.IDS[1],'1','1',e)
        self.assertFalse(result['success'])
        self.assertFalse(g.active)
        self.assertNotIn(fixture.IDS[1],c.pose_by_map)

    def test_cancel_after_paint_preserves_source_and_new_pending(self):
        replacement={'requestId':999,'fromMemberId':fixture.IDS[0]}
        def cancel(h):
            h.alignment_request_generation+=1
            h.front_group_sibling_pending=replacement
            return True
        h,c,g,s,e,b=self.run_case(cancel)
        result=h._run_alignment_v2_for_current_frame_unlocked(fixture.IDS[1],'1','1',e)
        self.assertFalse(result['success'])
        self.assertTrue(g.active);self.assertTrue(h.surface_visible)
        self.assertIs(h.front_group_sibling_pending,replacement)
        self.assertEqual(h.selected_ui,fixture.IDS[0])
        self.assertEqual(c.active_observation.map_id,fixture.IDS[0])

    def test_floor_reset_during_paint_keeps_new_identity_state(self):
        from native_similarity_groups import NativeSimilarityState
        replacement=NativeSimilarityState()
        def reset(h):
            h.current_floor='2';h.gate.confirmed_id=None
            h.native_similarity_state=replacement
            h.auto_lock_pending=None;h.front_group_sibling_pending=None
            return False
        h,c,g,s,e,b=self.run_case(reset)
        h._run_alignment_v2_for_current_frame_unlocked(fixture.IDS[1],'1','1',e)
        self.assertEqual(h.current_floor,'2')
        self.assertIsNone(h.gate.confirmed_id)
        self.assertIs(h.native_similarity_state,replacement)
        self.assertIsNone(h.auto_lock_pending)
        self.assertIsNone(h.front_group_sibling_pending)
        self.assertFalse(g.active);self.assertFalse(h.surface_visible)

    def test_stale_request_never_paints_or_replaces_source(self):
        def unexpected(h): self.fail('stale result painted')
        h,c,g,s,e,b=self.run_case(unexpected)
        e['alignmentRequestId']-=1
        result=h._run_alignment_v2_for_current_frame_unlocked(fixture.IDS[1],'1','1',e)
        self.assertFalse(result['success'])
        self.assertEqual(s(),b)

    def test_modes_share_same_failed_transaction(self):
        for mode in ('nightmare','difficult'):
            with self.subTest(mode=mode):
                h,c,g,s,e,b=self.run_case(lambda h:False)
                h.game_mode=mode
                bridge=np.array(h.round_layer_group.tracking['routeDisplayBridge'])
                pose=h.round_layer_group.root_pose.copy()
                result=h._run_alignment_v2_for_current_frame_unlocked(fixture.IDS[1],'1','1',e)
                self.assertEqual(result['reason'],'compositor-rejected')
                self.assertEqual(s(),b)
                np.testing.assert_array_equal(bridge,h.round_layer_group.tracking['routeDisplayBridge'])
                np.testing.assert_array_equal(pose,h.round_layer_group.root_pose)


class RecordingSurface:
    def __init__(self, accept=True, error=None):
        self.hwnd=123;self.visible=False;self.accept=accept;self.error=error
        self.hide_count=0;self.closed=False;self._retained_packet=None
    def hide(self): self.hide_count+=1;self.visible=False
    def close(self): self.closed=True
    def show_bgra(self,*a,**kw):
        if self.error: raise self.error
        self.visible=self.accept
        return self.accept
    def show_retained(self,packet,*a,**kw):
        self._retained_packet=packet
        return self.show_bgra(*a,**kw)


class NativeSwitchCompositorTests(unittest.TestCase):
    def setup_chain(self, accept, retained=False, error=None):
        h,c,g,s,e=prepare(self)
        source=RecordingSurface();source.visible=True
        target=RecordingSurface(accept,error)
        h.aligned_overlay=source
        h._display_capture_lock=threading.RLock();h._display_capture_active=False
        h._close_display_surface_now=lambda *a,surface,**kw:surface.close()
        h._protect_display_window=lambda *a:None
        h._current_frame_matches_display_environment=lambda:True
        h._surface_visibility_is_current=lambda generation:generation==h.surface_visibility_generation
        h._ui_action_first_content=lambda *a:None
        h._queue_ui_action_settle=lambda *a,**kw:None
        h._mark_overlay_display_verified=lambda *a,**kw:None
        h.show_sheet=lambda:None;h._note_route_pose_verified=lambda:None
        h._refresh_route_presentation=lambda:None
        h._shared_structure_reference=lambda *a:None
        from collections import OrderedDict
        h._idvb0059_surfaces=OrderedDict(source=source)
        if retained:
            from idvb0059_display import RetainedDisplayCache
            cache=RetainedDisplayCache()
            pixels=np.full((40,40,4),255,np.uint8)
            canvas,packet,_=cache.build(pixels,np.eye(3),(40,40),'target')
            h._build_display_overlay=lambda *a,**kw:(canvas,{'fixture':True})
            self.addCleanup(patch.stopall)
            patcher=patch('idvb0059_runtime.packet_for',return_value=packet);patcher.start()
        self.addCleanup(patch.stopall)
        patch('native_sibling_transaction.new_surface',return_value=target).start()
        return h,c,g,e,source,target

    def test_real_compositor_rejection_keeps_source_surface_and_grant(self):
        for retained in (False,True):
            with self.subTest(retained=retained):
                h,c,g,e,source,target=self.setup_chain(False,retained)
                result=h._run_alignment_v2_for_current_frame_unlocked(fixture.IDS[1],'1','1',e)
                self.assertEqual(result['reason'],'compositor-rejected')
                self.assertIs(h.aligned_overlay,source)
                self.assertTrue(source.visible);self.assertEqual(source.hide_count,0)
                self.assertTrue(g.active);self.assertTrue(target.closed)
                self.assertEqual(c.active_observation.map_id,fixture.IDS[0])
                self.assertNotIn(fixture.IDS[1],c.pose_by_map)
                patch.stopall()

    def test_promotion_failure_or_exception_still_preserves_source(self):
        for raises in (False,True):
            with self.subTest(raises=raises):
                h,c,g,e,source,target=self.setup_chain(True,True)
                h.engine.front_group_state=SimpleNamespace(state='GROUP_LOCKED',
                    provisional_member_id=fixture.IDS[1])
                def promote(*a,**kw):
                    if raises: raise RuntimeError('promotion failed')
                    return {'state':'GROUP_LOCKED'}
                h.engine.mark_front_member_verified=promote
                with patch.object(fixture.runtime,'FrontDoorLiveTestEngine',SimpleNamespace):
                    if raises:
                        with self.assertRaisesRegex(RuntimeError,'promotion failed'):
                            h._run_alignment_v2_for_current_frame_unlocked(fixture.IDS[1],'1','1',e)
                    else:
                        result=h._run_alignment_v2_for_current_frame_unlocked(fixture.IDS[1],'1','1',e)
                        self.assertEqual(result['reason'],'front-group-member-promotion-failed')
                self.assertTrue(source.visible);self.assertEqual(source.hide_count,0)
                self.assertTrue(g.active);self.assertTrue(target.closed)
                self.assertFalse(target.visible)
                self.assertEqual(h.gate.confirmed_id,fixture.IDS[0])
                self.assertEqual(h.native_similarity_state.provisional_member_id,fixture.IDS[0])
                patch.stopall()

    def test_identity_rejection_after_target_paint_keeps_original_pixels(self):
        h,c,g,e,source,target=self.setup_chain(True,True)
        h._commit_front_group_sibling_state=lambda:False
        result=h._run_alignment_v2_for_current_frame_unlocked(fixture.IDS[1],'1','1',e)
        self.assertEqual(result['reason'],'front-group-sibling-state-commit-failed')
        self.assertTrue(source.visible);self.assertEqual(source.hide_count,0)
        self.assertTrue(g.active);self.assertTrue(target.closed)
        self.assertFalse(target.visible)
        self.assertEqual(c.active_observation.map_id,fixture.IDS[0])
        self.assertEqual(h.selected_ui,fixture.IDS[0])

    def test_hidden_preference_commits_to_empty_real_surface(self):
        h,c,g,e,source,target=self.setup_chain(True,True)
        h.sheet_visibility_enabled=False
        result=h._run_alignment_v2_for_current_frame_unlocked(fixture.IDS[1],'1','1',e)
        self.assertTrue(result['success'])
        self.assertIs(h.aligned_overlay,target)
        self.assertFalse(target.visible)
        self.assertFalse(source.visible)

    def test_real_compositor_exception_closes_only_target(self):
        h,c,g,e,source,target=self.setup_chain(False,True,RuntimeError('upload failed'))
        with self.assertRaisesRegex(RuntimeError,'upload failed'):
            h._run_alignment_v2_for_current_frame_unlocked(fixture.IDS[1],'1','1',e)
        self.assertTrue(source.visible);self.assertEqual(source.hide_count,0)
        self.assertTrue(g.active);self.assertTrue(target.closed)

    def test_success_publishes_target_core_gate_bridge_ui_and_surface(self):
        h,c,g,e,source,target=self.setup_chain(True,True)
        result=h._run_alignment_v2_for_current_frame_unlocked(fixture.IDS[1],'1','1',e)
        self.assertTrue(result['success'])
        self.assertIs(h.alignment_core_v2,c)
        self.assertFalse(g.active)
        self.assertEqual(c.active_observation.map_id,fixture.IDS[1])
        self.assertEqual(h.round_layer_group.map_id,fixture.IDS[1])
        self.assertEqual(h.selected_ui,fixture.IDS[1])
        self.assertEqual(h.native_similarity_state.provisional_member_id,fixture.IDS[1])
        self.assertEqual(h.gate.confirmed_id,fixture.IDS[1])
        self.assertIn('routeDisplayBridge',h.round_layer_group.tracking)
        self.assertIs(h.aligned_overlay,target);self.assertTrue(target.visible)
        self.assertFalse(source.visible)
        self.assertIs(h._idvb0059_surfaces['target'],target)

if __name__=='__main__': unittest.main()
