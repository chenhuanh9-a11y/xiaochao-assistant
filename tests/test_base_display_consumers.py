"""Production consumer methods and real registered fixture pixels, no game acceptance."""
import threading
import queue
from types import SimpleNamespace
from pathlib import Path
import unittest
from unittest.mock import patch
import numpy as np
import test_base_map_display as base_fixture
from test_native_similarity_runtime import runtime


class BaseDisplayConsumerTests(unittest.TestCase):
    def setUp(self):
        self.f = base_fixture.BaseMapDisplayTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.mid = 'nightmare-layout-104026'
        self.a = a = runtime.DailyAssistant.__new__(runtime.DailyAssistant)
        a.game_mode = 'nightmare'
        a.preferred_route_profile_id = 'missing-author'
        a.route_profile_id = runtime.SHARED_STRUCTURE_PROFILE_ID
        a.current_floor = '1'
        a.active_map_cycle_id = 3
        a.current_frame_generation = 4
        a.surface_visibility_generation = 5
        a._custom_map_library = self.f.library
        a._custom_gallery_selected = lambda: None
        a._custom_route_image = lambda *args: None
        a._custom_route_active = lambda *args: False
        a.author_route_catalog = SimpleNamespace(profiles={}, variants=lambda *args, **kw: (), route_path=lambda *args, **kw: None)
        a.aligned_author_route_lock = threading.RLock()
        a._shared_structure_display_cache = {}
        a._shared_structure_reference = lambda *args: self.f.ref
        a._shared_structure_lookup_binding = lambda *args: dict(profile='shared', variant='fixture')
        a._shared_structure_mode_enabled = lambda: True
        a.pack = SimpleNamespace(pack_id='fixture')
        self.f.ref.metadata = {}
        a._invalidate_base_display()
        a._begin_display_round()

    def layer(self):
        return self.a._shared_display_layer(runtime.SHARED_STRUCTURE_PROFILE_ID, self.mid, '1')

    def prepare(self):
        self.a._prepare_base_map_display(self.mid, '1', self.f.ref)

    def test_real_layer_before_and_after_worker_preparation_no_ui_io(self):
        before = self.layer()
        self.assertEqual(before['displayKind'], 'shared-structure')
        threads = []
        original = Path.stat
        def stat(path, *args, **kw):
            threads.append(threading.get_ident())
            return original(path, *args, **kw)
        with patch.object(Path, 'stat', stat):
            worker = threading.Thread(target=self.prepare)
            worker.start(); worker.join(3)
        self.assertTrue(threads)
        self.assertNotIn(threading.get_ident(), threads)
        with patch.object(Path, 'stat', side_effect=AssertionError('UI stat')):
            after = self.layer()
        self.assertEqual(after['displayKind'], 'base-map')
        self.assertEqual(after['sourcePath'], before['sourcePath'])
        self.assertNotEqual(after['displayResourceKey'], before['displayResourceKey'])
        self.assertFalse(np.array_equal(after['routeSource'], before['routeSource']))

    def test_authoritative_bridge_applied_once_and_recognition_unchanged(self):
        self.prepare()
        recognition = np.array([[2, 0, 11], [0, 2, 13], [0, 0, 1]], float)
        before = self.f.ref.clean_mask.copy()
        spec = self.a._aligned_render_spec(self.mid, '1', recognition)
        np.testing.assert_array_equal(spec['matrixSourceToClient'], recognition)
        np.testing.assert_array_equal(spec['recognitionMatrixSourceToClient'], recognition)
        np.testing.assert_array_equal(self.f.ref.clean_mask, before)
        self.assertEqual(spec['displayKind'], 'base-map')
        self.assertEqual(spec['routeProfileId'], runtime.SHARED_STRUCTURE_PROFILE_ID)

    def test_explicit_empty_or_failed_custom_never_falls_back(self):
        self.prepare()
        self.a._custom_route_active = lambda *args: True
        self.assertIsNone(self.layer())
        pixels = np.zeros((12,16,4), np.uint8)
        self.a._custom_route_image = lambda *args: pixels
        self.assertEqual(self.layer()['displayKind'], 'custom-route')

    def test_declared_unusable_is_not_missing(self):
        self.prepare()
        self.a.author_route_catalog.variants = lambda *args, **kw: (SimpleNamespace(layers={'1':Path('absent.png')}),)
        self.assertEqual(self.layer()['authorStatus'], 'load-error')
        self.assertEqual(self.layer()['displayKind'], 'shared-structure')
        self.a.optional_author_displays = SimpleNamespace(binding=lambda *args: dict(usable=False))
        self.assertEqual(self.layer()['authorStatus'], 'registration-failed')

    def test_difficult_does_not_reuse_nightmare_base(self):
        self.prepare()
        self.a.game_mode = 'difficult'
        self.assertEqual(self.layer()['displayKind'], 'shared-structure')

    def test_resource_replacement_revokes_old_provider_and_late_publication(self):
        old = self.a._base_map_display
        self.a._invalidate_base_display()
        old.resolve(self.f.library, self.mid, '1', self.f.ref)
        self.assertEqual(self.layer()['displayKind'], 'shared-structure')
        self.prepare()
        self.assertEqual(self.layer()['displayKind'], 'base-map')

    def test_notice_only_from_exact_committed_context_once_per_round(self):
        self.prepare()
        descriptor = self.a._display_descriptor(self.layer(), self.mid, '1')
        self.assertEqual(self.a._committed_display_notice(self.mid, '1'), '')
        self.assertTrue(self.a._record_display_commit(descriptor, self.mid, '1'))
        self.assertIn('基础地图', self.a._committed_display_notice(self.mid, '1'))
        self.assertEqual(self.a._committed_display_notice(self.mid, '1'), '')
        # G reopen is a capture epoch, never a new round.
        self.a.active_map_cycle_id += 1
        self.assertFalse(self.a._record_display_commit(descriptor, self.mid, '1'))
        fresh = self.a._display_descriptor(self.layer(), self.mid, '1')
        self.a._record_display_commit(fresh, self.mid, '1')
        self.assertEqual(self.a._committed_display_notice(self.mid, '1'), '')
        self.a._begin_display_round()
        self.a._record_display_commit(self.a._display_descriptor(self.layer(), self.mid, '1'), self.mid, '1')
        self.assertIn('基础地图', self.a._committed_display_notice(self.mid, '1'))

    def test_author_resource_surface_frame_changes_reject_captured_descriptor(self):
        self.prepare()
        for attr in ('preferred_route_profile_id','_base_display_epoch',
                     'current_frame_generation','surface_visibility_generation','game_mode'):
            with self.subTest(attr=attr):
                old = getattr(self.a, attr)
                descriptor = self.a._display_descriptor(self.layer(), self.mid, '1')
                setattr(self.a, attr, 'changed' if isinstance(old,str) else old+1)
                self.assertFalse(self.a._record_display_commit(descriptor,self.mid,'1'))
                setattr(self.a,attr,old)

    def test_copy_keeps_source_guard_and_failed_compositor_cannot_commit(self):
        from base_display_integration import display_source_guard
        self.prepare()
        descriptor = self.a._display_descriptor(self.layer(), self.mid, '1')
        pixels = self.a._remember_display_output(np.zeros((2,2,4),np.uint8), descriptor)
        copied = pixels.copy()
        calls = []
        @display_source_guard
        def show(host, image, accepted=True):
            calls.append(image)
            return accepted
        self.assertFalse(show(self.a, copied, accepted=False))
        self.assertIsNone(self.a._presented_display)
        self.a.preferred_route_profile_id = 'new-author'
        self.assertFalse(show(self.a, copied))
        self.assertEqual(len(calls), 1)
        self.assertIsNone(self.a._presented_display)

    def test_source_captured_before_render_cannot_be_relabeled_after_import(self):
        self.prepare()
        layer = self.layer()
        self.a._invalidate_base_display()
        captured = self.a._display_descriptor(layer, self.mid, '1')
        self.assertFalse(self.a._display_descriptor_is_current(captured))

    def test_existing_author_worker_prepares_future_missing_author_switch(self):
        self.a.author_route_catalog.variants = lambda *args, **kw: (SimpleNamespace(layers={'1':Path('author.png')}),)
        self.prepare()
        self.a.preferred_route_profile_id = 'another-missing-author'
        self.a.author_route_catalog.variants = lambda *args, **kw: ()
        with patch.object(Path, 'stat', side_effect=AssertionError('UI stat')):
            self.assertEqual(self.layer()['displayKind'], 'base-map')

    def test_production_overlay_pixels_cache_and_provenance_change_together(self):
        a = self.a
        a.gate = SimpleNamespace(confirmed_id=self.mid)
        a.events = queue.Queue()
        a._log = lambda *args, **kw: None
        a._route_profile_validation_spec = lambda *args: dict(
            profileBridgeVerified=True, sharedReference=self.f.ref, matrixSourceToClient=np.eye(3))
        def render():
            return a._build_display_overlay(np.zeros((12,16,4),np.uint8),np.eye(3),(12,16,3),
                map_id=self.mid,floor='1',minimum_coverage=0,minimum_visible_pixels=1)
        # Exercise the production CPU pixel/render cache, with retained GPU
        # acceleration disabled; pose authorization is outside this pixel test.
        with patch('idvb0059_runtime.build_display', return_value=None):
            structure, first = render()
            self.prepare()
            base, second = render()
            cached, third = render()
        self.assertEqual(first['displayKind'],'shared-structure')
        self.assertEqual(second['displayKind'],'base-map')
        self.assertFalse(np.array_equal(structure,base))
        np.testing.assert_array_equal(base,cached)
        self.assertEqual(a._display_output_metadata(base)['displayResourceKey'],second['displayResourceKey'])
        self.assertEqual(third['displayResourceKey'],second['displayResourceKey'])

    def test_fixed_actual_commit_notice_requires_pose_and_shares_round_dedup(self):
        self.prepare()
        a=self.a
        descriptor=a._display_descriptor(self.layer(),self.mid,'1')
        a.round_work_active=True
        a.current_floor_authorization={'poseVerified':False}
        a._current_floor_authorization_is_current=lambda *args:True
        a._ensure_route_notification_context=lambda:None
        a._route_notified_floors=set();a._route_notified_identities=set()
        messages=[];a._publish_route_notification=messages.append
        a._note_reference_fallback_committed(descriptor)
        self.assertEqual(messages,[])
        a.current_floor_authorization['poseVerified']=True
        a._note_reference_fallback_committed(descriptor,provisional=True)
        self.assertEqual(messages,[])
        a._note_reference_fallback_committed(descriptor)
        self.assertEqual(len(messages),1)
        a._record_display_commit(descriptor,self.mid,'1')
        self.assertEqual(a._committed_display_notice(self.mid,'1'),'')

    def test_provider_byte_limit_counts_retained_reference_and_pixels(self):
        from base_map_display import BaseMapDisplay
        size=self.f.mask.nbytes*5
        provider=BaseMapDisplay(max_bytes=size)
        first=provider.resolve(self.f.library,self.mid,'1',self.f.ref)
        self.assertIsNotNone(first.layer)
        other=SimpleNamespace(shape=self.f.ref.shape,clean_mask=self.f.mask.copy(),path=self.f.ref.path)
        self.assertIsNotNone(provider.resolve(self.f.library,self.mid,'1',other).layer)
        self.assertEqual(len(provider._cache),1)
        self.assertIsNone(provider.peek(self.f.library,self.mid,'1',self.f.ref).layer)
        small=BaseMapDisplay(max_bytes=size-1)
        self.assertEqual(small.resolve(self.f.library,self.mid,'1',self.f.ref).reason,'base-map-cache-byte-limit')
        self.assertEqual(len(small._cache),0)


if __name__ == '__main__': unittest.main()
