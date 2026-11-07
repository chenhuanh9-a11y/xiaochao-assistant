"""S1 author switching: real runtime, synthetic native fits/capture/display art.

No image matching/game acceptance. Private identity never grants pose freshness.
"""
import copy
import threading
import unittest
from unittest.mock import patch
import numpy as np
import test_s1_locked_identity as locked
from test_s1_locked_identity import evidence

PROFILE='s1-jiaye-handnotes'

class AuthorIdentityScopeTests(unittest.TestCase):
    setUp=locked.LockedIdentityContracts.setUp
    start_patch=locked.LockedIdentityContracts.start_patch
    show=locked.LockedIdentityContracts.show
    current_payload=locked.LockedIdentityContracts.current_payload
    idle=locked.LockedIdentityContracts.idle
    locked=locked.LockedIdentityContracts.locked

    def layer(self, engine, mid, floor, profile, **kwargs):
        bridge=np.array([[.8,0,9],[0,.8,4],[0,0,1.]]) if profile==PROFILE else np.eye(3)
        return dict(pixels=np.full((20,24,4),70 if profile==PROFILE else 255,np.uint8),
            resourceIdentity='fixture:'+profile,displayKind='author-route' if profile==PROFILE else 'game-native',
            displayLabel=profile,routeProfileId=profile,matrixDisplayToReference=bridge)

    def switch(self,profile=PROFILE):
        with patch('client_route_reprojection.prepare_display_layer',side_effect=self.layer):
            return self.runtime.dispatch('set_context',dict(season='s1',mode='nightmare',profileId=profile))

    def test_direct_open_switch_preserves_identity_pose_floor_and_new_ack(self):
        pool,old=self.locked();self.assertTrue(self.runtime.acknowledge_render_material(old['token'])['success'])
        hint=self.runtime._s1_candidate_pose_hint;canonical=copy.deepcopy(old['canonicalMatrix']);scope=pool.scope
        for profile in (PROFILE,'shared-structure',PROFILE):
            previous=self.current_payload();self.assertTrue(self.switch(profile)['success'])
            now=self.current_payload();self.assertIsNotNone(now)
            self.assertEqual(now['mapId'],'a');self.assertEqual(now['floor'],'-1')
            self.assertEqual(now['profileId'],profile);self.assertEqual(now['canonicalMatrix'],canonical)
            self.assertIs(self.runtime._s1_private_pool,pool);self.assertEqual(pool.scope,scope)
            self.assertEqual(pool.locked_id,'a');self.assertEqual(self.runtime._durable_map_id,'')
            self.assertEqual(self.runtime._map_id,'');self.assertIsNone(self.runtime._gate.confirmed_id)
            self.assertFalse(self.runtime.acknowledge_render_material(previous['token'])['success'])
            self.assertIsNone(self.runtime.render_payload(previous['token']))
            self.assertTrue(self.runtime.acknowledge_render_material(now['token'])['success'])
            self.assertEqual(self.runtime._s1_candidate_pose_hint,hint)
        self.assertEqual(self.calls,[])  # Neither identification nor pose sensor ran.

    def test_closed_author_change_retains_lock_but_unknown_fit_never_uses_old_pixels(self):
        pool,old=self.locked();self.runtime.dispatch('close_map');self.assertTrue(self.switch()['success'])
        self.assertIs(self.runtime._s1_private_pool,pool);self.assertIsNone(self.current_payload())
        self.unknown.add('a');receipt=self.runtime.dispatch('capture_now');self.idle()
        self.assertFalse(receipt['success']);self.assertEqual(self.calls,[('a','-1')])
        self.assertEqual(pool.locked_id,'a');self.assertIsNone(self.current_payload())
        self.assertFalse(self.runtime.acknowledge_render_material(old['token'])['success'])

    def test_display_and_interaction_settings_do_not_change_native_scope(self):
        pool,old=self.locked();generation=self.runtime._s1_solver_policy_generation
        changes=dict(contextRouteProfiles={'s1:nightmare':PROFILE},routeProfileId=PROFILE,
            modeRouteProfiles={'nightmare':PROFILE},sheetOpacity=.4,referenceSheetVisible=False,
            sheetVisibilityEnabled=False,hotkey='M',actionHotkeys=['F8'],routePointNumbers=True,
            zhan10CoffinVariantId='variant-02',reduceMotion=True,gameControlLayout={'fixture':True},
            dragBoundaryNormalized=[.1,.1,.8,.8])
        for key,value in changes.items():
            with self.subTest(key=key):
                self.runtime.configure_settings(dict(self.runtime._settings,**{key:value}))
                self.assertIs(self.runtime._s1_private_pool,pool)
                self.assertEqual(pool.locked_id,'a')
                self.assertEqual(self.runtime._s1_solver_policy_generation,generation)
                self.assertTrue(self.runtime._private_pool_scope_current(pool))

    def test_real_solver_policy_changes_revoke_pool_hint_and_old_material(self):
        for key,value in [('idvb0059Enabled',False),
                          ('poseIndexCandidatesEnabled',True),('difficultGridEnabled',True)]:
            with self.subTest(key=key):
                fx=AuthorIdentityScopeTests();fx.setUp()
                try:
                    pool,old=fx.locked();fx.runtime.acknowledge_render_material(old['token'])
                    generation=fx.runtime._s1_solver_policy_generation
                    fx.runtime.configure_settings(dict(fx.runtime._settings,**{key:value}))
                    self.assertIsNone(fx.runtime._s1_private_pool)
                    self.assertIsNone(fx.runtime._s1_candidate_pose_hint)
                    self.assertEqual(fx.runtime._s1_solver_policy_generation,generation+1)
                    self.assertIsNone(fx.current_payload())
                    self.assertFalse(fx.runtime.acknowledge_render_material(old['token'])['success'])
                finally:fx.doCleanups()

    def test_legacy_or_omitted_selection_policy_preserves_s1_native_scope(self):
        pool,old=self.locked()
        generation=self.runtime._s1_solver_policy_generation
        for settings in ({'mapSelectionMode':'manual'}, {'sheetOpacity':.5}, {'mapSelectionMode':'auto'}):
            self.runtime.configure_settings(settings)
            self.assertTrue(self.runtime._s1_provisional_enabled())
            self.assertIs(self.runtime._s1_private_pool,pool)
            self.assertEqual(pool.locked_id,'a')
            self.assertEqual(self.runtime._s1_solver_policy_generation,generation)
            self.assertTrue(self.runtime._private_pool_scope_current(pool))
            self.assertEqual(self.current_payload()['token'],old['token'])
            self.assertEqual(self.runtime.snapshot()['mapSelectionMode'],'auto')

    def test_effective_pose_policy_alias_avoids_false_invalidation(self):
        self.runtime.configure_settings(dict(self.runtime._settings,poseIndexCandidatesEnabled=False))
        pool,_=self.locked();generation=self.runtime._s1_solver_policy_generation
        self.runtime.configure_settings(dict(self.runtime._settings,difficultGridEnabled=True))
        self.assertIs(self.runtime._s1_private_pool,pool)
        self.assertEqual(self.runtime._s1_solver_policy_generation,generation)

    def test_author_switch_allows_owned_async_native_evidence_to_finish(self):
        entered,release=threading.Event(),threading.Event();owned=[]
        def prepare(frame,ids,floor,*,cancelled,**kwargs):
            entered.set();self.assertTrue(release.wait(4));owned.append(not cancelled())
            return evidence(ids,floor,('b',))
        self.registry.engine.qt_s1_private_coordinate_prepare=prepare
        self.show();self.assertTrue(entered.wait(4));pool=self.runtime._s1_private_pool
        try:self.assertTrue(self.switch()['success']);self.assertIs(self.runtime._s1_private_pool,pool)
        finally:release.set();self.idle()
        self.assertEqual(owned,[True]);self.assertEqual(pool.remaining,('a',))
        self.assertEqual(pool.locked_id,'')  # Background evidence alone never promotes lock.
        self.assertEqual(self.current_payload()['profileId'],PROFILE)

    def test_solver_change_rejects_owned_async_evidence(self):
        entered,release=threading.Event(),threading.Event();owned=[]
        def prepare(frame,ids,floor,*,cancelled,**kwargs):
            entered.set();self.assertTrue(release.wait(4));owned.append(not cancelled())
            return evidence(ids,floor,('b',))
        self.registry.engine.qt_s1_private_coordinate_prepare=prepare
        self.show();self.assertTrue(entered.wait(4));pool=self.runtime._s1_private_pool
        try:self.runtime.configure_settings(dict(self.runtime._settings,idvb0059Enabled=False))
        finally:release.set();self.idle()
        self.assertEqual(owned,[False]);self.assertIsNone(self.runtime._s1_private_pool)
        self.assertEqual(pool.remaining,('a','b'))

    def test_close_end_or_capture_change_cannot_commit_late_reprojection(self):
        for boundary in ('close_map','end_round','capture_generation'):
            with self.subTest(boundary=boundary):
                fx=AuthorIdentityScopeTests();fx.setUp()
                try:
                    pool,old=fx.locked();entered,release=threading.Event(),threading.Event();results=[]
                    def delayed(*a,**kw):entered.set();self.assertTrue(release.wait(4));return fx.layer(*a,**kw)
                    with patch('client_route_reprojection.prepare_display_layer',side_effect=delayed):
                        thread=threading.Thread(target=lambda:results.append(fx.runtime.dispatch('set_context',dict(profileId=PROFILE))))
                        thread.start();self.assertTrue(entered.wait(4))
                        if boundary=='capture_generation':fx.capture.generation+=1
                        else:fx.runtime.dispatch(boundary)
                        release.set();thread.join(4)
                    self.assertFalse(thread.is_alive());self.assertIsNone(fx.current_payload())
                    self.assertFalse(fx.runtime.acknowledge_render_material(old['token'])['success'])
                    if boundary!='end_round':self.assertEqual(pool.locked_id,'a')
                    else:self.assertIsNone(fx.runtime._s1_private_pool)
                finally:fx.doCleanups()

    def test_retired_generation_cannot_ack_old_material_or_mint_pose_hint(self):
        pool,old=self.locked();self.assertIsNone(self.runtime._s1_candidate_pose_hint)
        self.runtime.configure_settings(dict(self.runtime._settings,zhan10CoffinVariantId='variant-02'))
        self.assertIs(self.runtime._s1_private_pool,pool)
        self.assertIsNone(self.runtime.render_payload(old['token']))
        self.assertFalse(self.runtime.acknowledge_render_material(old['token'])['success'])
        self.assertIsNone(self.runtime._s1_candidate_pose_hint)

    def test_newer_author_selection_wins_over_blocked_old_reprojection(self):
        pool,old=self.locked();entered,release=threading.Event(),threading.Event();results=[]
        def delayed(engine,mid,floor,profile,**kw):
            if profile==PROFILE:entered.set();self.assertTrue(release.wait(4))
            return self.layer(engine,mid,floor,profile,**kw)
        with patch('client_route_reprojection.prepare_display_layer',side_effect=delayed):
            first=threading.Thread(target=lambda:results.append(self.runtime.dispatch('set_context',dict(profileId=PROFILE))))
            first.start();self.assertTrue(entered.wait(4))
            second=threading.Thread(target=lambda:results.append(self.runtime.dispatch('set_context',dict(profileId='shared-structure'))))
            second.start()
            # The later command retires the first's display generation before
            # either can finish preparation under the work lock.
            import time
            end=time.monotonic()+4
            while self.runtime._profile!='shared-structure' and time.monotonic()<end:time.sleep(.002)
            self.assertEqual(self.runtime._profile,'shared-structure');release.set()
            first.join(4);second.join(4)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(sum(r['success'] for r in results),1)
        now=self.current_payload();self.assertEqual(now['profileId'],'shared-structure')
        self.assertEqual(now['canonicalMatrix'],old['canonicalMatrix'])
        self.assertIs(self.runtime._s1_private_pool,pool);self.assertEqual(pool.locked_id,'a')
        self.assertFalse(self.runtime.acknowledge_render_material(old['token'])['success'])

    def test_session_revocation_during_reprojection_cannot_mint_pixels(self):
        pool,old=self.locked()
        def revoked(*a,**kw):
            self.runtime._s1_structure_session.revoke_current();self.runtime._s1_structure_ticket=None
            return self.layer(*a,**kw)
        with patch('client_route_reprojection.prepare_display_layer',side_effect=revoked):
            result=self.runtime.dispatch('set_context',dict(profileId=PROFILE))
        self.assertFalse(result['success']);self.assertEqual(result['reason'],'stale-candidate-display')
        self.assertIsNone(self.current_payload());self.assertIs(self.runtime._s1_private_pool,pool)

if __name__=='__main__':unittest.main()
