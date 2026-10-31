"""Preserve old identity loaders and registered legacy display fallback."""
import ast
from functools import lru_cache
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1] / 'app'
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


@lru_cache(maxsize=1)
def source_tree():
    return ast.parse((Path(__file__).resolve().parents[1] / 'app/identityv_daily_assistant.py').read_text('utf-8-sig'))


def subject():
    names = {'_effective_route_profile', '_shared_display_profile_id', '_legacy_difficult_display_profile', '_shared_display_bridge', '_display_route_context'}
    methods = [n for n in ast.walk(source_tree()) if isinstance(n, ast.FunctionDef) and n.name in names]
    cls = ast.ClassDef(name='Subject', bases=[], keywords=[], body=methods, decorator_list=[])
    import numpy as np
    ns = dict(Path=Path, np=np, DEFAULT_ROUTE_PROFILE_ID='wang-qingxin', SUJU_ROUTE_PROFILE_ID='suju', SHARED_STRUCTURE_PROFILE_ID='shared-structure')
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), '<production-compat>', 'exec'), ns)
    return ns['Subject']()


class DifficultCompatibilityTests(unittest.TestCase):
    def test_optional_loading_keeps_legacy_identity_structure_provider(self):
        # Execute the real installation block without constructing an engine/UI.
        block = next(n for n in ast.walk(source_tree()) if isinstance(n, ast.If)
                     and isinstance(n.test, ast.BoolOp)
                     and any(isinstance(x, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'optional_author_displays' for t in x.targets) for x in n.body))
        module = ModuleType('optional_author_displays')
        module.OptionalAuthorDisplays = lambda *a, **k: SimpleNamespace(catalog=a[2], adapters=a[3])
        for mode, native_identity, expected in [('difficult', False, 'zhan10-speed'), ('difficult', True, 'shared-structure'), ('nightmare', False, 'shared-structure')]:
            with self.subTest(mode=mode, native_identity=native_identity):
                base = SimpleNamespace(_structure_profile_provider=lambda: 'zhan10-speed')
                direct, product = object(), object()
                base._structure_direct_resource_loader = direct
                base._structure_resource_loader = product
                ns = dict(mode=mode, native_identity=native_identity, base=base,
                          shared_resources=object(), native_context=SimpleNamespace(root=Path('.')) if mode == 'difficult' else None,
                          nightmare_root=Path('.'), pack=object(), catalog=object(), adapters=object(), shared_version_context={},
                          SHARED_STRUCTURE_PROFILE_ID='shared-structure')
                with patch.dict(sys.modules, {'optional_author_displays': module}):
                    exec(compile(ast.Module(body=[block], type_ignores=[]), '<production-install-block>', 'exec'), ns)
                self.assertEqual(base._structure_profile_provider(), expected)
                self.assertIs(base._structure_direct_resource_loader, direct)
                self.assertIs(base._structure_resource_loader, product)

    def make_subject(self):
        import numpy as np
        s = subject()
        s.preferred_route_profile_id = 'aichicu-difficult'
        s.optional_author_displays = SimpleNamespace(profiles={'aichicu-difficult'}, binding=lambda *a: None)
        s.difficult_native_context = SimpleNamespace(legacy_bindings={('zhan10-speed', 'difficult-map-29', 'variant-01', f): {} for f in ('1', '2')})
        s.author_route_catalog = SimpleNamespace(profiles={}, route_path=lambda p,m,f,**k: Path('old-' + f + '.png'))
        matrices = {'1': np.array([[1.,0.,-72.], [0.,1.,-10.], [0.,0.,1.]]), '2': np.array([[1.,0.,-41.], [0.,1.,-40.], [0.,0.,1.]])}
        s.author_coordinate_adapters = SimpleNamespace(matrix_profile_to_canonical=lambda p,m,f: matrices.get(f))
        s._shared_structure_mode_enabled = lambda: True
        s._custom_route_active = lambda *a: False
        s._custom_gallery_selected = lambda: False
        return s

    def test_both_registered_legacy_floors_keep_old_display_and_exact_bridge(self):
        import numpy as np
        s = self.make_subject()
        for floor in ('1', '2'):
            effective = s._effective_route_profile('difficult-map-29', floor)
            self.assertEqual(effective, 'zhan10-speed')
            np.testing.assert_array_equal(s._shared_display_bridge(effective, 'difficult-map-29', floor), np.eye(3))
        import threading
        image = object()
        s.aligned_author_route_lock = threading.RLock()
        s.aligned_author_route_cache = {('zhan10-speed', 'difficult-map-29', '1', None): image}
        self.assertEqual(s._display_route_context(image), ('zhan10-speed', 'difficult-map-29', '1'))
        self.assertEqual(s.preferred_route_profile_id, 'aichicu-difficult')
        self.assertEqual(s.optional_author_displays.profiles, {'aichicu-difficult'})

    def test_unregistered_floor_missing_old_path_or_bridge_cannot_fallback(self):
        s = self.make_subject()
        self.assertEqual(s._effective_route_profile('difficult-map-03', '1'), 'shared-structure')
        self.assertEqual(s._effective_route_profile('difficult-map-29', 'B1'), 'shared-structure')
        s.author_route_catalog.route_path = lambda *a, **k: None
        self.assertEqual(s._effective_route_profile('difficult-map-29', '1'), 'shared-structure')
        s = self.make_subject()
        s.author_coordinate_adapters.matrix_profile_to_canonical = lambda *a: None
        self.assertEqual(s._effective_route_profile('difficult-map-29', '1'), 'shared-structure')

    def test_usable_selected_author_and_custom_routes_keep_priority(self):
        s = self.make_subject()
        s.optional_author_displays.binding = lambda *a: dict(usable=True)
        self.assertEqual(s._effective_route_profile('difficult-map-29', '1'), 'aichicu-difficult')
        s._custom_route_active = lambda *a: True
        self.assertEqual(s._effective_route_profile('difficult-map-29', '1'), 'shared-structure')

    def test_nightmare_missing_selected_author_does_not_switch_to_an_imported_author(self):
        s = self.make_subject()
        s.difficult_native_context = None
        s.preferred_route_profile_id = 'wang-qingxin'
        s.optional_author_displays.profiles = {'suju', 'nightingale'}
        s.author_route_catalog.profiles = {}
        self.assertEqual(s._shared_display_profile_id(), 'wang-qingxin')
        self.assertEqual(s._effective_route_profile('nightmare-03', '1'), 'shared-structure')
        self.assertEqual(s.preferred_route_profile_id, 'wang-qingxin')

    def test_nightmare_leftover_catalog_image_cannot_replace_validated_sidecar(self):
        s = self.make_subject()
        s.difficult_native_context = None
        s.preferred_route_profile_id = 'wang-qingxin'
        s.optional_author_displays.profiles = {'nightingale'}
        s.author_route_catalog.profiles = {'wang-qingxin': SimpleNamespace(runtime_enabled=True)}
        s.author_route_catalog.route_path = lambda *a, **k: self.fail('unregistered image must not be probed')
        self.assertEqual(s._effective_route_profile('nightmare-03', '1'), 'shared-structure')

    def test_absent_optional_loader_retains_the_legacy_default_in_each_mode(self):
        s = self.make_subject()
        s.optional_author_displays = None
        self.assertEqual(s._shared_display_profile_id(), 'zhan10-speed')
        s.difficult_native_context = None
        self.assertEqual(s._shared_display_profile_id(), 'suju')

    def test_legacy_catalog_without_pr64_cache_helper_keeps_suju_available(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix='legacy-author-path-') as folder:
            path = Path(folder) / 'display.png'
            path.write_bytes(b'path-availability-fixture-not-decoded')
            s = self.make_subject()
            s.difficult_native_context = None
            s.preferred_route_profile_id = 'suju'
            s.optional_author_displays.profiles = set()
            profile = SimpleNamespace(runtime_enabled=True,
                maps={'map': [SimpleNamespace(layers={'1': path})]})
            s.author_route_catalog = SimpleNamespace(profiles={'suju': profile})
            self.assertEqual(s._effective_route_profile(None, '1'), 'suju')


if __name__ == '__main__':
    unittest.main()
