"""Consumer seam fixtures; protocol/real base verification is tested separately."""
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import s1_author_displays as display
from map_resource_context import BrowseContext


class GenericAuthorConsumerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / '_internal'
        self.root.mkdir()
        self.profile = 'author-new-person'
        receipt = self.root.parent / 'work/map-packages' / (self.profile + '.json')
        receipt.parent.mkdir(parents=True)
        receipt.write_text('{}')
        self.path = self.root / 'display.png'
        ok, data = cv2.imencode('.png', np.full((3, 4, 4), (20, 30, 40, 80), dtype=np.uint8))
        self.assertTrue(ok)
        self.data = data.tobytes()
        self.path.write_bytes(self.data)
        self.row = dict(mode='nightmare', mapId='fixture-map', floor='-4',
            displayPath='_internal/display.png', displaySha256=hashlib.sha256(self.data).hexdigest(),
            displayShapeHW=[3, 4], matrixDisplayToReference=np.eye(3).tolist(),
            referenceResource='_internal/reference.npz')
        self.doc = dict(season='s1', layers=[self.row])
        self.package = SimpleNamespace(profile_id=self.profile, document=self.doc,
            display_name='New display', author_name='New author', bindings_path='bindings.json',
            layer_dependencies={('nightmare', 'fixture-map', '-4'): ('_internal/reference.npz',)},
            registration_sha='registration', _path=lambda _: self.path, _read=lambda _: self.data)
        self.catalog = SimpleNamespace(profiles={}, _raw={'profiles': []}, default_profile_id='shared-structure')
        self.adapters = SimpleNamespace(profiles={})
        self.addCleanup(patch.stopall)
        patch.object(display, 'profile_ids', return_value=(self.profile,)).start()
        def installed(root, profile):
            if profile != self.profile:
                raise FileNotFoundError()
            return self.package
        patch.object(display, 'InstalledAuthorPackage', side_effect=installed).start()

    def runtime(self, season='s1', fallback=None):
        root = self.root if season == 's0' else self.root / 'seasons' / season / 'nightmare'
        return display.S1AuthorDisplays(self.root, SimpleNamespace(root=root, mode='nightmare',
            catalog=self.catalog, adapters=self.adapters), season=season, fallback=fallback)

    def test_generic_difficulty2_registered_mode(self):
        self.row['mode'] = 'difficulty2'
        runtime = display.S1AuthorDisplays(self.root, SimpleNamespace(
            root=self.root/'seasons/s1/difficulty2', mode='difficulty2', catalog=self.catalog,
            adapters=self.adapters))
        self.assertIn(self.profile, runtime.profiles)
        context = BrowseContext('difficulty2', self.root, None, None, None, (), {}, season='s1')
        self.assertEqual(display.append_browse_profile(context).profiles[0].profile_id, self.profile)

    def test_dynamic_metadata_b4_alpha_and_missing_layer(self):
        runtime = self.runtime()
        self.assertEqual(runtime.profiles, {self.profile})
        self.assertEqual(runtime.catalog.profiles[self.profile].author_name, 'New author')
        self.assertEqual(runtime.catalog.profiles[self.profile].display_name, 'New display')
        self.assertEqual(runtime.catalog.route_path(self.profile, 'fixture-map', '-4'), self.path)
        self.assertIsNone(runtime.binding(self.profile, 'fixture-map', '-1'))
        self.assertIsNone(runtime.adapters.matrix_profile_to_canonical(self.profile, 'fixture-map', '-1'))
        pixels = runtime.pixels(self.path, 'aligned-overlay')
        self.assertEqual(pixels[0, 0].tolist(), [20, 30, 40, 80])
        self.assertFalse(pixels.flags.writeable)
        self.assertEqual(runtime.binding(self.profile, 'fixture-map', '-4')['status'], 'user-provided')

    def test_mode_season_scope_and_no_empty_browser_profile(self):
        self.assertFalse(self.runtime(season='s0').profiles)
        context = BrowseContext('difficult', self.root, None, None, None, (), {}, season='s1')
        self.assertFalse(display.append_browse_profile(context).profiles)
        context = BrowseContext('nightmare', self.root, None, None, None, (), {}, season='s0')
        self.assertFalse(display.append_browse_profile(context).profiles)

    def test_s0_floor_preserved_s1_metadata_floor_key(self):
        self.doc['season'] = 's0'
        self.row['floor'] = '1'
        context = BrowseContext('nightmare', self.root, None, None, None, (), {}, season='s0')
        result = display.append_browse_profile(context)
        self.assertEqual(result.profiles[0].first_preview[1], '1')
        self.assertIn(self.profile, self.runtime('s0').profiles)
        self.doc['season'] = 's1'
        self.row['floor'] = '-4'
        context = BrowseContext('nightmare', self.root, None, None, None, (), {}, season='s1',
            floor_metadata={'source-height-1': {'gameFloor': -4}})
        result = display.append_browse_profile(context)
        self.assertEqual(result.profiles[0].first_preview[1], 'source-height-1')
        self.assertEqual(result.profiles[0].available_layers, 1)

    def test_generic_missing_layer_never_uses_legacy_author_fallback(self):
        from author_display_policy import SharedAuthorDisplayPolicy
        class Owner(SharedAuthorDisplayPolicy):
            preferred_route_profile_id = 'author-new-person'
            optional_author_displays = SimpleNamespace(profiles={'author-new-person'}, binding=lambda *a: None)
            author_route_catalog = SimpleNamespace(profiles={})
            def _shared_structure_mode_enabled(self): return True
            def _custom_route_active(self, *a): return False
            def _legacy_difficult_display_profile(self, *a): return 'zhan10-speed'
        self.assertEqual(Owner()._effective_route_profile('fixture-map', '-4'), 'shared-structure')

    def test_management_discovers_dynamic_name_season_and_mode(self):
        import author_package_management as manager
        with patch.object(manager, 'PACKAGE_NAMES', {}), \
                patch.object(manager, 'profile_ids', return_value=(self.profile,)), \
                patch.object(manager, 'adopt_legacy_author_package', return_value={}), \
                patch.object(manager, 'verified_installed_package', return_value={'status': 'installed'}), \
                patch.object(manager, 'inspect_author_package_removal', return_value=dict(
                    fileCount=2, bytes=100, modifiedFiles=[], receiptSha256='receipt')):
            rows = manager.managed_author_packages_snapshot(self.root.parent)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['title'], rows[0]['season'], rows[0]['mode']),
                         ('New display', 's1', 'nightmare'))
        self.assertTrue(rows[0]['canRemove'])

    def test_s0_fallback_keeps_old_optional_registration(self):
        old_path = self.root / 'old.png'
        old_binding = dict(path=old_path, matrix=np.eye(3), usable=True)
        old_pixels = np.zeros((1, 1, 4), np.uint8)
        fallback = SimpleNamespace(profiles={'old'}, bindings={('old','map','1'): old_binding},
            errors={'bad': 'invalid'}, binding=lambda *args: old_binding, pixels=lambda *args: old_pixels)
        runtime = self.runtime('s0', fallback)
        self.assertIn('old', runtime.profiles)
        self.assertIs(runtime.binding('old','map','1'), old_binding)
        self.assertIs(runtime.pixels(old_path, 'aligned-overlay'), old_pixels)
        self.assertEqual(runtime.errors['bad'], 'invalid')


if __name__ == '__main__':
    unittest.main()
