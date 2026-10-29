"""Temporary declared resources; no UI, runtime factory or network operations."""
from concurrent.futures import CancelledError
from dataclasses import FrozenInstanceError
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from map_resource_context import BrowsePack, load_browse_context
from map_resource_model import choose_resource


class BrowseContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, data):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data), encoding='utf-8')
        return target

    def png(self, name):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (8, 6), 'red').save(target)
        return target

    def maps(self, count=2, mode='nightmare'):
        return [dict(id=f'map-{i}', name=f'Map {i}', resourceBase='resource-root',
                     routeProfileId='suju' if mode == 'nightmare' else 'zhan10-speed',
                     layers={'1': f'base/{i}-1.png', '2': f'base/{i}-2.png'}) for i in range(count)]

    def nightmare(self, count=2):
        self.write('references/nightmare-map-pack.json',
                   dict(packId='fixture', displayName='Fixture', defaultEntryFloor=1,
                        floors=['1', '2'], maps=self.maps(count)))

    def catalog(self, profile, maps, path='references/nightmare-author-route-catalog.json'):
        return self.write(path, dict(schemaVersion=1, defaultProfileId=profile,
            profiles=[dict(id=profile, displayName=profile, authorName=profile, routeFamilyName='fixture',
                runtimeEnabled=False, settingsSelectable=False,
                maps={mid: [dict(id='v1', displayName='Variant', identityStatus='display-only',
                                displayPolicy='preview-only', layers=layers)] for mid, layers in maps.items()})]))

    def native(self, catalog=True):
        self.write('difficult-native-manifest.json', dict(runtimeInterfaces={
            'identityPack': 'references/native-pack.json', 'authorCatalog': 'references/native-authors.json',
            'typedIndex': 'references/native-index.json'}))
        self.write('references/native-pack.json', dict(schemaVersion=1, mode='difficult', packId='native',
            floors=['1', '2'], defaultEntryFloor=1, maps=self.maps(mode='difficult')))
        self.write('references/native-index.json', dict(schemaVersion=1, resourceKind='shared-structure',
            entries=[dict(profile='shared-structure', mapId='map-0', floor='1', variant='variant-01',
                          resourcePath='structure/0.npz', sourceVersion='fixture')]))
        if catalog:
            self.catalog('zhan10-speed', {'map-0': {'1': 'authors/0.png'}}, 'references/native-authors.json')

    def test_nightmare_partial_counts_maps_and_floors_separately(self):
        self.nightmare(3)
        self.catalog('nightingale', {'map-0': {'1': 'authors/0.png', '2': 'authors/missing.png'},
                                    'map-1': {'1': 'authors/1.png'}})
        first = self.png('authors/0.png')
        self.png('authors/1.png')
        context = load_browse_context(self.root, 'nightmare')
        self.assertEqual(context.error, '')
        author = next(p for p in context.profiles if p.profile_id == 'nightingale')
        self.assertEqual((author.map_count, author.available_maps, author.missing_maps), (3, 2, 1))
        self.assertEqual((author.layer_count, author.available_layers, author.missing_floors), (3, 2, 4))
        self.assertEqual(author.first_preview, ('map-0', '1', first))
        self.assertEqual(context.path_stamps[first], (first.stat().st_mtime_ns, first.stat().st_size))
        self.assertIsNone(context.path_stamps[self.root / 'authors/missing.png'])
        self.assertEqual(choose_resource(author.maps[0], '1', '1')[2], first)
        self.assertEqual(author.name, '爱吃醋')
        self.assertFalse(context.catalog.profiles['nightingale'].runtime_enabled)
        with self.assertRaises(FrozenInstanceError):
            context.mode = 'difficult'
        with self.assertRaises(TypeError):
            context.path_stamps[first] = None

    def test_native_mode_uses_catalog_and_never_loads_npz_pixels(self):
        self.native()
        image = self.png('authors/0.png')
        with patch('numpy.load', side_effect=AssertionError('pixel/solver loading forbidden')):
            context = load_browse_context(self.root, 'difficult')
        self.assertEqual(context.error, '')
        self.assertIsInstance(context.pack, BrowsePack)
        self.assertEqual(context.pack.layer_role, 'structure-reference')
        self.assertEqual(context.profiles[0].first_preview, ('map-0', '1', image))
        self.assertEqual(context.profiles[0].missing_floors, 3)
        self.assertIsNone(context.path_stamps[self.root / 'structure/0.npz'])

    def test_legacy_difficult_uses_declared_tilemap_or_bundled_source(self):
        self.write('references/difficult-runtime.json', dict(mode='difficult', packId='legacy',
            maps=self.maps(mode='difficult')))
        self.write('references/difficult-tilemaps/index.json', dict(entries=[dict(mapId='map-0', floor='1',
            routeResource='route.png', resourcePath='structure.npz')]))
        path = self.png('references/difficult-tilemaps/route.png')
        with patch.dict(os.environ, {'IDENTITYV_DIFFICULT_MAPS': 'Z:/outside'}):
            context = load_browse_context(self.root, 'difficult')
        self.assertEqual(context.error, '')
        self.assertEqual(context.pack.source_root, self.root / 'assets/difficult-maps')
        self.assertEqual(context.profiles[0].first_preview, ('map-0', '1', path))

    def test_missing_mode_is_distinct_from_missing_author(self):
        absent = load_browse_context(self.root, 'nightmare')
        self.assertIsNone(absent.model)
        self.assertIn('FileNotFoundError', absent.error)
        self.native(catalog=False)
        available = load_browse_context(self.root, 'difficult')
        self.assertEqual(available.error, '')
        self.assertIsNotNone(available.model)
        self.assertIn('author-catalog-missing', available.warnings)
        self.assertFalse(any(p.available_maps for p in available.profiles))

    def test_optional_old_partial_catalog_is_browsable_without_runtime_authorization(self):
        self.nightmare(3)
        self.catalog('wang-qingxin', {'map-0': {'1': 'authors/old.png'}},
                     'references/author-packs/wang-qingxin/catalog.json')
        self.png('authors/old.png')
        context = load_browse_context(self.root, 'nightmare')
        author = next(p for p in context.profiles if p.profile_id == 'wang-qingxin')
        self.assertEqual((author.available_maps, author.missing_maps, author.missing_floors), (1, 2, 5))
        self.assertEqual(author.package_id, 'nightmare-wang-qingxin')
        self.assertFalse(context.catalog.profiles['wang-qingxin'].settings_selectable)

    def test_bad_catalog_and_structure_json_are_not_healthy_empty_states(self):
        self.nightmare()
        path = self.root / 'references/nightmare-author-route-catalog.json'
        path.write_text('{bad', encoding='utf-8')
        self.assertIn('JSONDecodeError', load_browse_context(self.root, 'nightmare').error)
        path.unlink()
        (self.root / 'references/nightmare-structure-display.json').write_text('{bad', encoding='utf-8')
        self.assertIn('JSONDecodeError', load_browse_context(self.root, 'nightmare').error)

    def test_invalid_optional_metadata_is_explicit_and_does_not_hide_other_authors(self):
        self.nightmare()
        self.catalog('suju', {})
        path = self.root / 'references/author-packs/nightingale/catalog.json'
        path.parent.mkdir(parents=True)
        path.write_text('{bad', encoding='utf-8')
        context = load_browse_context(self.root, 'nightmare')
        self.assertEqual(context.error, '')
        broken = next(p for p in context.profiles if p.profile_id == 'nightingale')
        self.assertIn('invalid-author-metadata', broken.error)
        self.assertTrue(any(p.profile_id == 'suju' for p in context.profiles))

    def test_escape_paths_are_rejected_before_file_stat(self):
        self.nightmare()
        self.catalog('nightingale', {'map-0': {'1': '../outside.png'}})
        original_stat = Path.stat
        def confined_stat(path, *args, **kwargs):
            if path.name == 'outside.png':
                raise AssertionError('must not stat escape')
            return original_stat(path, *args, **kwargs)
        with patch.object(Path, 'stat', confined_stat):
            context = load_browse_context(self.root, 'nightmare')
        self.assertIn('escapes browse root', context.error)

    def test_bad_optional_catalog_shapes_are_isolated_from_valid_suju(self):
        self.nightmare()
        self.catalog('suju', {'map-0': {'1': 'authors/suju.png'}})
        self.png('authors/suju.png')
        self.png('base/0-1.png')
        bad_profiles = [
            dict(id='nightingale', displayName='N', authorName='N', routeFamilyName='R', maps=[]),
            dict(id='nightingale', maps={'map-0': {}}),
            dict(id='nightingale', maps={'map-0': [dict(layers=[])]}),
        ]
        for profile in bad_profiles:
            with self.subTest(profile=profile):
                self.write('references/author-packs/nightingale/catalog.json',
                           dict(schemaVersion=1, profiles=[profile]))
                context = load_browse_context(self.root, 'nightmare')
                self.assertEqual(context.error, '')
                self.assertIsNotNone(context.model)
                broken = next(p for p in context.profiles if p.profile_id == 'nightingale')
                self.assertEqual(broken.error, 'invalid-author-metadata: ValueError')
                suju = next(p for p in context.profiles if p.profile_id == 'suju')
                self.assertEqual(suju.available_maps, 1)
                self.assertEqual(suju.error, '')
                self.assertIn('suju', context.catalog.profiles)

    def test_source_root_and_environment_never_select_external_development_assets(self):
        maps = self.maps(1)
        maps[0].pop('resourceBase')
        self.write('references/nightmare-map-pack.json', dict(packId='legacy', sourceRoot='Z:/dev', maps=maps))
        with patch.dict(os.environ, {'IDENTITYV_NIGHTMARE_MAPS': 'Z:/environment',
                                    'IDENTITYV_AUTHOR_ROUTE_ASSET_ROOT': 'Z:/environment'}):
            context = load_browse_context(self.root, 'nightmare')
        self.assertEqual(context.error, '')
        self.assertEqual(context.pack.source_root, self.root / 'assets/nightmare-maps')
        self.assertEqual(context.pack.maps[0].layers['1'], self.root / 'assets/nightmare-maps/base/0-1.png')

    def test_cancellation_checks_during_profiles_and_paths_and_has_no_runtime_writes(self):
        self.nightmare(20)
        calls = []
        def cancel():
            calls.append(True)
            return len(calls) > 25
        with self.assertRaises(CancelledError):
            load_browse_context(self.root, 'nightmare', cancelled=cancel)
        source = (Path(__file__).resolve().parents[1] / 'app/map_resource_context.py').read_text(encoding='utf-8')
        for forbidden in ('identityv_daily_assistant', 'set_game_mode(', '_install_map_mode(',
                          'tkinter', 'NightmareMemoryStore', 'MapIdentityEngine', 'Thread('):
            self.assertNotIn(forbidden, source)
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        load_browse_context(self.root, 'nightmare')
        after = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
