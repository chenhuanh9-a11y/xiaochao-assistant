"""Merged author registration: exact namespaces, mode/prefs and legacy isolation."""
import ast
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import star_map_import as importer
from optional_author_displays import OptionalAuthorDisplays
import test_optional_author_coverage as coverage_fixture
import test_live_author_import as live_fixture

CASES = (
    ('nightmare-zhan10', 'zhan10-nightmare', 'nightmare', '展十 · 噩梦'),
    ('nightmare-suju-speed', 'suju-speed-nightmare', 'nightmare', '酥居速刷 · 噩梦'),
    ('difficult-suju-speed', 'suju-speed-difficult', 'difficult', '酥居速刷 · 困难'),
)


class RegistrationMergeTests(unittest.TestCase):
    def test_exact_namespaces_and_difficult_dependency_gate(self):
        from author_package_management import PACKAGE_NAMES
        for package, profile, mode, title in CASES:
            with self.subTest(package=package):
                self.assertEqual(importer.AUTHOR_PACKAGES[package], profile)
                self.assertEqual(PACKAGE_NAMES[package], title)
                f = live_fixture.LiveAuthorImportTests(); f.setUp(); self.addCleanup(f.temp.cleanup)
                asset = f'_internal/assets/authors/{profile}/m1.png'
                if mode == 'difficult':
                    with self.assertRaisesRegex(importer.ImportRejected, 'base-dependencies-not-declared'):
                        importer.inspect_package(f.package(package, {asset: b'display'}), f.root)
                    f.dependencies = []
                    for index, name in enumerate(sorted(importer.DIFFICULT_AUTHOR_DEPENDENCIES)):
                        data = f'difficult-{index}'.encode(); f.write(name, data)
                        f.dependencies.append(live_fixture.row(name, data))
                self.assertEqual(importer.inspect_package(f.package(package, {asset: b'display'}), f.root)['packageId'], package)
                for forbidden in ('_internal/assets/suju/foreign.png', live_fixture.CORE):
                    with self.assertRaises(importer.ImportRejected):
                        importer.inspect_package(f.package(package, {forbidden: b'forbidden'}), f.root)

    def test_production_options_persist_independent_ids_and_hide_in_legacy(self):
        source = Path(__file__).resolve().parents[1] / 'app/identityv_daily_assistant.py'
        tree = ast.parse(source.read_text('utf-8-sig'))
        names = {'_author_route_options', '_restore_route_profile_preference', 'normalize_route_profile_id'}
        nodes = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name in names]
        options = next(node for node in tree.body if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == 'ROUTE_PROFILE_OPTIONS' for target in node.targets))
        ns = dict(Path=Path, SUJU_ROUTE_PROFILE_ID='suju', DEFAULT_ROUTE_PROFILE_ID='wang-qingxin',
                  SHARED_STRUCTURE_PROFILE_ID='shared-structure', gallery_of=lambda _: None)
        exec(compile(ast.Module(body=[options], type_ignores=[]), str(source), 'exec'), ns)
        ns['ROUTE_PROFILE_LABELS'] = dict(ns['ROUTE_PROFILE_OPTIONS'])
        exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(source), 'exec'), ns)
        self.assertEqual(ns['ROUTE_PROFILE_LABELS']['suju'], '滋酥居')
        self.assertEqual(ns['normalize_route_profile_id']('suju'), 'suju')
        for _, profile, mode, title in CASES:
            with self.subTest(profile=profile):
                f = coverage_fixture.OptionalAuthorCoverageTests(); f.setUp(); self.addCleanup(f.temp.cleanup)
                f.folder = f.root / 'references/author-packs' / profile; f.folder.mkdir(parents=True)
                f.raw['profiles'][0]['id'] = profile; f.payload['profileId'] = profile
                for entry in f.entries: entry['profileId'] = profile
                f.raw['profiles'][0]['maps'].pop('m2'); f.payload['entries'] = f.entries[:1]
                (f.folder / 'catalog.json').write_text(json.dumps(f.raw), encoding='utf-8')
                (f.folder / 'display-bindings.json').write_text(json.dumps(f.payload), encoding='utf-8')
                loaded = OptionalAuthorDisplays(f.root, f.pack, None, f.adapters, f.versions, mode=mode)
                self.assertEqual(loaded.profiles, {profile}); self.assertFalse(loaded.errors)
                self.assertIsNone(loaded.binding(profile, 'm2', '1'))
                other = 'difficult' if mode == 'nightmare' else 'nightmare'
                self.assertEqual(OptionalAuthorDisplays(f.root, f.pack, None, f.adapters, f.versions, mode=other).profiles, set())
                subject = SimpleNamespace(_shared_structure_mode_enabled=lambda: True, optional_author_displays=loaded,
                    author_route_catalog=loaded.catalog, game_mode=mode, settings={'routeProfileId': profile})
                self.assertEqual(ns['_author_route_options'](subject), ((profile, title),))
                ns['_restore_route_profile_preference'](subject); self.assertEqual(subject.route_profile_id, profile)
                subject._shared_structure_mode_enabled = lambda: False
                self.assertNotIn(profile, dict(ns['_author_route_options'](subject)))


if __name__ == '__main__':
    unittest.main()
