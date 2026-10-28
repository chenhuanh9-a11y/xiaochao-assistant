"""Exercise actual integration methods without importing Windows/Tk runtime."""
import ast
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import json

import cv2
import numpy as np

APP = Path(__file__).resolve().parents[1] / 'app'
sys.path.insert(0, str(APP))
from custom_maps import RouteError, gallery_of, profile_id as custom_profile_id


def methods(filename, names, namespace):
    tree = ast.parse((APP / filename).read_text(encoding='utf-8-sig'))
    selected = [node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name in names]
    if len(selected) != len(names):
        raise AssertionError('integration method missing')
    wrapper = ast.ClassDef(name='Subject', bases=[], keywords=[], body=selected, decorator_list=[])
    module = ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[]))
    exec(compile(module, filename, 'exec'), namespace)
    return namespace['Subject']


NAMES = ['_shared_display_layer', '_custom_route_image', '_display_route_context',
         '_route_author_fallback_notice', '_chest_auto_tap_reserved', '_publish_hotkey_proposal']


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.render = Mock(side_effect=lambda library, map_id, floor, points: np.full((4, 4, 4), 9, np.uint8))
        self.ns = dict(np=np, cv2=cv2, Path=Path, json=json, SHARED_STRUCTURE_PROFILE_ID='shared',
                       ROUTE_PROFILE_LABELS={}, render_custom_map_layer=self.render,
                       CustomRouteError=RouteError, ACTION_ORDER=('action',))
        self.subject = methods('identityv_daily_assistant.py', NAMES, self.ns)()
        s = self.subject
        s.shared_structure_resources = object()
        s.aligned_author_route_lock = threading.RLock()
        s._shared_structure_display_cache = {}
        s.aligned_author_route_cache = {}
        self.refs = {}
        self.routes = {}
        self.enabled = True
        s._shared_structure_reference = lambda profile, map_id, floor: self.refs[str(map_id)]
        s._shared_structure_lookup_binding = lambda *args: dict(profile='shared', variant='v')
        s._custom_route_active = lambda map_id, floor: self.enabled
        s._custom_route_key = lambda map_id: ('gallery', json.dumps(self.routes[str(map_id)]), 'v1')
        self.validate_reference = Mock()
        s.custom_map_library = lambda: SimpleNamespace(version='v1', validate_reference=self.validate_reference)
        s.preferred_route_profile_id = 'custom:gallery'
        s._route_profile_label = lambda profile: '自定义 · 测试'
        s._shared_display_profile_id = lambda: 'shared'
        for i in range(9):
            self.refs[str(i)] = SimpleNamespace(shape=(4, 4), clean_mask=np.ones((4, 4), np.uint8), path=Path('mask'))
            self.routes[str(i)] = [dict(floor='1', x=1.0, y=1.0, kind='via', room=None, color='#63CAFF')]

    def layer(self, key='0'):
        return self.subject._shared_display_layer('shared', key, '1')

    def test_eviction_then_disable_never_reuses_custom_as_plain(self):
        s = self.subject
        custom = self.layer()['routeSource']
        self.enabled = False
        plain = self.layer()['routeSource']
        self.enabled = True
        for i in range(1, 9):
            self.layer(str(i))
        self.enabled = False
        self.assertIs(self.layer()['routeSource'], plain)
        self.assertTrue(np.all(plain == [218, 228, 238, 255]))
        self.assertIsNone(s._display_route_context(custom))

    def test_route_and_reference_changes_invalidate_custom_provenance(self):
        s = self.subject
        first = self.layer()['routeSource']
        self.assertEqual(s._display_route_context(first), ('shared', '0', '1'))
        self.routes['0'] = [dict(floor='1', x=2.0, y=2.0, kind='via', room=None, color='#63CAFF')]
        self.assertIsNone(s._display_route_context(first))
        second = self.layer()['routeSource']
        self.assertIsNot(first, second)
        self.refs['0'] = SimpleNamespace(shape=(4, 4), clean_mask=np.ones((4, 4), np.uint8), path=Path('new'))
        self.assertIsNone(s._display_route_context(second))
        third = self.layer()['routeSource']
        self.assertIsNot(second, third)
        s.shared_structure_resources = object()
        self.assertIsNone(s._display_route_context(third))

    def test_failed_render_hides_old_layer_and_reports_reason(self):
        s = self.subject
        old = self.layer()['routeSource']
        self.routes['0'] = self.routes['0'] + [dict(floor='1', x=3.0, y=3.0, kind='via', room=None, color='#63CAFF')]
        self.render.side_effect = RouteError('第1段：这两个位置之间没有已确认的通路')
        self.assertIsNone(self.layer())
        self.assertIsNone(s._display_route_context(old))
        s.gate = SimpleNamespace(confirmed_id='0')
        s.current_floor = '1'
        self.assertIn('自定义路线无法显示', s._route_author_fallback_notice())
        self.render.side_effect = None
        self.render.return_value = np.full((5, 5, 4), 9, np.uint8)
        self.routes['0'] = [dict(floor='1', x=4.0, y=4.0, kind='via', room=None, color='#63CAFF')]
        self.assertIsNone(self.layer(), 'art on a different canvas size is rejected')
        self.render.return_value = None
        self.render.side_effect = RouteError('第1段：这两个位置之间没有已确认的通路')
        self.routes['0'] = self.routes['0'][:0] + [dict(floor='1', x=1.0, y=1.0, kind='via', room=None, color='#63CAFF'),
                                                   dict(floor='1', x=3.0, y=3.0, kind='via', room=None, color='#63CAFF')]
        calls = self.render.call_count
        self.assertIsNone(self.layer())
        self.assertEqual(self.render.call_count, calls + 1)
        self.assertIsNone(self.layer())
        self.assertEqual(self.render.call_count, calls + 1, 'same failed route is cached without repeated rendering')
        self.enabled = False
        self.assertEqual(self.layer()['displayKind'], 'shared-structure')

    def test_same_shape_changed_mask_rejects_cached_layer(self):
        old = self.layer()['routeSource']
        self.validate_reference.side_effect = ValueError('mask hash mismatch')
        self.assertIsNone(self.layer())
        self.assertEqual(self.render.call_count, 1)
        self.assertIsNone(self.subject._display_route_context(old))

    def test_combination_shortcuts_reserve_primary_keys(self):
        s = self.subject
        s.hotkey_name, s.hotkey_vk = 'Ctrl+F', 70
        s.action_hotkeys = {'action': ('Alt+F12', 123)}
        reserved = s._chest_auto_tap_reserved()
        self.assertIn(70, reserved)
        self.assertIn(123, reserved)

    def test_hotkey_publish_reconciles_after_successful_save(self):
        s = self.subject
        s._validate_hotkey_proposal = Mock()
        s._save_settings = Mock(return_value=False)
        s._sync_hotkey_current = Mock()
        s._sync_sheet_visibility_ui = Mock()
        s._log = Mock()
        s.hotkey_capture_generation = 1
        s.chest_auto_tap = SimpleNamespace(reconcile_reserved=Mock(return_value=dict(success=False, message='conflict')))
        bindings = dict(map=SimpleNamespace(name='Ctrl+F', vk=70), action=SimpleNamespace(name='Alt+G', vk=71))
        self.assertFalse(s._publish_hotkey_proposal(bindings))
        s.chest_auto_tap.reconcile_reserved.assert_not_called()
        s._save_settings.return_value = True
        self.assertTrue(s._publish_hotkey_proposal(bindings))
        s.chest_auto_tap.reconcile_reserved.assert_called_once_with()
        self.assertEqual(s._chest_auto_tap_reserved()[70], '星图快捷键 Ctrl+F')
        self.assertEqual(s.hotkey_settings_warning, 'conflict')


class GalleryPreferenceTests(unittest.TestCase):
    def subject(self):
        return methods('identityv_daily_assistant.py',
                       ['_restore_route_profile_preference', '_route_profile_label', 'use_custom_gallery'],
                       dict(normalize_route_profile_id=lambda v:v, DEFAULT_ROUTE_PROFILE_ID='suju',
                            gallery_of=gallery_of, custom_profile_id=custom_profile_id,
                            ROUTE_PROFILE_LABELS={}))()

    def test_startup_without_author_catalog_preserves_custom_preference(self):
        s=self.subject()
        s.game_mode='nightmare'
        s.settings={'routeProfileId':'custom:two'}
        s.author_route_catalog=None
        s._restore_route_profile_preference()
        self.assertEqual(s.route_profile_id,'custom:two')
        s.game_mode='difficult'
        s._restore_route_profile_preference()
        self.assertEqual(s.route_profile_id,'zhan10-speed')

    def test_duplicate_names_have_unique_labels_and_use_passes_id(self):
        s=self.subject()
        s.settings_path=Path('fixture/settings.json')
        s.custom_galleries=lambda:SimpleNamespace(name=lambda _id:'Same',
            galleries=lambda:[('one','Same',True),('two','Same',False)])
        first=s._route_profile_label('custom:one')
        second=s._route_profile_label('custom:two')
        self.assertNotEqual(first,second)
        s._custom_maps_ready=lambda:True
        s.route_profile_var=Mock()
        s._apply_route_profile_setting=Mock()
        self.assertTrue(s.use_custom_gallery('one')['success'])
        s._apply_route_profile_setting.assert_called_once_with(profile_id='custom:one')


class StatusTests(unittest.TestCase):
    def test_failed_disable_save_uses_current_runtime_state_for_controls(self):
        subject = methods('desktop_main_ui.py', ['_save_chest_auto_tap'],
                          dict(GREEN='green', ERROR='error'))()
        before = dict(enabled=True, holdKey='F12', tapKey='F', intervalMs=80)
        after = dict(before, enabled=False)
        subject.assistant = SimpleNamespace(
            chest_auto_tap_snapshot=Mock(side_effect=[before, after]),
            update_chest_auto_tap=Mock(return_value=dict(success=False, message='save failed')))
        subject._chest_enabled = Mock(get=Mock(return_value='关闭'))
        subject._chest_hold = Mock(get=Mock(return_value='F12'))
        subject._chest_tap = Mock(get=Mock(return_value='F'))
        subject._chest_interval = Mock(get=Mock(return_value='80'))
        subject.chest_feedback = Mock()
        subject._sync_chest_auto_tap = Mock()
        subject._save_chest_auto_tap()
        subject._chest_enabled.set.assert_called_with('关闭')
        self.assertEqual(subject.assistant.chest_auto_tap_snapshot.call_count, 2)

    def test_disabled_runtime_release_error_is_visible(self):
        subject = methods('desktop_main_ui.py', ['_sync_chest_auto_tap'],
                          dict(MUTED='muted', ERROR='error', GREEN='green', PRIMARY='primary'))()
        subject.assistant = SimpleNamespace(chest_auto_tap_snapshot=lambda: dict(
            available=True, enabled=False, status='release failed', error='release failed'))
        subject._chest_signature = None
        subject._chest_enabled = Mock()
        subject.chest_status = Mock()
        subject._sync_chest_auto_tap()
        subject._chest_enabled.set.assert_called_with('关闭')
        subject.chest_status.configure.assert_called_with(text='●  release failed', fg='error')


if __name__ == '__main__':
    unittest.main()
