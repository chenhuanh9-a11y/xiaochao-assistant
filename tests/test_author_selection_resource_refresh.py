"""Selection intent and resolved display source differ during asset refresh."""
import ast
from functools import lru_cache
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest

APP = Path(__file__).resolve().parents[1] / 'app'
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


@lru_cache(maxsize=1)
def subject():
    tree = ast.parse((APP / 'identityv_daily_assistant.py').read_text(encoding='utf-8'))
    owner = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'DailyAssistant')
    names = {'_shared_display_profile_id', '_effective_route_profile', '_legacy_difficult_display_profile'}
    methods = [n for n in owner.body if isinstance(n, ast.FunctionDef) and n.name in names]
    cls = ast.ClassDef(name='Subject', bases=[], keywords=[], body=methods, decorator_list=[])
    ns = dict(Path=Path, DEFAULT_ROUTE_PROFILE_ID='wang-qingxin',
              SUJU_ROUTE_PROFILE_ID='suju', SHARED_STRUCTURE_PROFILE_ID='shared-structure')
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), '<production-selection>', 'exec'), ns)
    return ns['Subject']


class AuthorSelectionTests(unittest.TestCase):
    def setUp(self):
        self.available = {'suju'}
        self.s = subject()()
        self.s.preferred_route_profile_id = 'wang-qingxin'
        self.s._shared_structure_mode_enabled = lambda: True
        self.s._custom_route_active = lambda *a: False
        self.s._custom_gallery_selected = lambda: False
        self.s.optional_author_displays = SimpleNamespace(profiles=self.available)
        self.s.author_route_catalog = SimpleNamespace(
            profiles={p: SimpleNamespace(runtime_enabled=True) for p in ('suju', 'wang-qingxin')},
            profile_has_assets=lambda p: p in self.available)

    def test_importing_unselected_author_keeps_base_fallback(self):
        self.assertEqual(self.s._effective_route_profile(None, '1'), 'shared-structure')
        self.assertEqual(self.s.preferred_route_profile_id, 'wang-qingxin')

    def test_selected_author_restores_when_its_assets_arrive(self):
        self.available.add('wang-qingxin')
        self.assertEqual(self.s._effective_route_profile(None, '1'), 'wang-qingxin')
        self.available.remove('wang-qingxin')
        self.assertEqual(self.s._effective_route_profile(None, '1'), 'shared-structure')
        self.assertEqual(self.s.preferred_route_profile_id, 'wang-qingxin')

    def test_explicit_other_author_selection_is_honored(self):
        self.s.preferred_route_profile_id = 'suju'
        self.assertEqual(self.s._effective_route_profile(None, '1'), 'suju')

    def test_legacy_context_without_optional_registry_keeps_contract(self):
        self.s.optional_author_displays = None
        self.assertEqual(self.s._shared_display_profile_id(), 'suju')

    def test_difficult_native_display_selection_is_unchanged(self):
        self.s.difficult_native_context = object()
        self.assertEqual(self.s._shared_display_profile_id(), 'zhan10-speed')


    def test_unregistered_optional_author_cannot_use_leftover_catalog_files(self):
        self.s.author_route_catalog.profile_has_assets = lambda p: True
        self.s.author_route_catalog.route_path = lambda *a, **k: __file__
        self.assertEqual(self.s._effective_route_profile(None, '1'), 'shared-structure')
        self.assertEqual(self.s._effective_route_profile('nightmare-01', '1'), 'shared-structure')

    def test_selected_optional_author_requires_usable_current_floor_binding(self):
        self.available.add('wang-qingxin')
        self.s.optional_author_displays.binding = lambda p, m, f: dict(usable=f == '1')
        self.assertEqual(self.s._effective_route_profile('nightmare-01', '1'), 'wang-qingxin')
        self.assertEqual(self.s._effective_route_profile('nightmare-01', '2'), 'shared-structure')

    def test_custom_route_keeps_structure_identity_source(self):
        self.s._custom_route_active = lambda *a: True
        self.available.add('wang-qingxin')
        self.assertEqual(self.s._effective_route_profile('nightmare-01', '1'), 'shared-structure')


class AuditResolutionTests(unittest.TestCase):
    def test_only_selected_author_resolution_can_restore(self):
        sys.path.insert(0, str(APP))
        from ui_import_audit import selection_preserved
        before = dict(mode='nightmare', preferred='wang-qingxin', route='shared-structure', modeRoutes={})
        after = dict(before, route='wang-qingxin')
        self.assertTrue(selection_preserved(before, after, restored_profiles=('wang-qingxin',)))
        self.assertFalse(selection_preserved(before, after, restored_profiles=('suju',)))
        self.assertFalse(selection_preserved(before, dict(before, route='suju'), restored_profiles=('suju',)))
        self.assertFalse(selection_preserved(before, dict(after, preferred='suju'), restored_profiles=('suju',)))
        self.assertFalse(selection_preserved(before, dict(before, mode='difficult')))
        self.assertTrue(selection_preserved(after, before, removed_profile='wang-qingxin'))
        self.assertFalse(selection_preserved(after, before, removed_profile='suju'))
        self.assertFalse(selection_preserved(after, before))


if __name__ == '__main__':
    unittest.main()
