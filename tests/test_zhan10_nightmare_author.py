"""Independent nightmare Zhan10 package, mode isolation and partial display coverage."""
import ast
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import star_map_import as importer
from optional_author_displays import OptionalAuthorDisplays
import test_live_author_import as live_fixture
import test_optional_author_coverage as coverage_fixture

PROFILE = 'zhan10-nightmare'
PACKAGE = 'nightmare-zhan10'


class Zhan10NightmareTests(unittest.TestCase):
    def display_fixture(self):
        f = coverage_fixture.OptionalAuthorCoverageTests(); f.setUp()
        self.addCleanup(f.temp.cleanup)
        f.profile = PROFILE
        f.folder = f.root / 'references/author-packs' / PROFILE
        f.folder.mkdir(parents=True)
        f.raw['profiles'][0]['id'] = PROFILE
        f.payload['profileId'] = PROFILE
        for entry in f.entries:
            entry['profileId'] = PROFILE
        return f

    def load(self, f, mode='nightmare'):
        (f.folder / 'catalog.json').write_text(json.dumps(f.raw), encoding='utf8')
        (f.folder / 'display-bindings.json').write_text(json.dumps(f.payload), encoding='utf8')
        return OptionalAuthorDisplays(f.root, f.pack, None, f.adapters, f.versions, mode=mode)

    def test_first_repeat_and_receipt_owned_removal_leave_base_unchanged(self):
        from author_package_management import inspect_author_package_removal, remove_author_package_live
        f = live_fixture.LiveAuthorImportTests(); f.setUp(); self.addCleanup(f.temp.cleanup)
        asset = '_internal/assets/authors/zhan10-nightmare/m1.png'
        archive = f.package(PACKAGE, {asset: b'display'})
        self.assertEqual(f.live(archive)['added'], 1)
        self.assertEqual(f.live(archive)['added'], 0)
        plan = inspect_author_package_removal(f.root, PACKAGE)
        remove_author_package_live(f.root, PACKAGE, application_idle=True,
            expected_receipt_sha256=plan['receiptSha256'], expected_plan_sha256=plan['planSha256'])
        self.assertFalse((f.root / asset).exists())
        f.assert_base_preserved()

    def test_rejects_other_author_and_core_paths(self):
        for path in ['_internal/assets/authors/nightingale/m1.png', live_fixture.CORE]:
            f = live_fixture.LiveAuthorImportTests(); f.setUp(); self.addCleanup(f.temp.cleanup)
            with self.assertRaises(importer.ImportRejected):
                importer.inspect_package(f.package(PACKAGE, {path: b'forbidden'}), f.root)

    def test_nightmare_only_partial_coverage_and_exact_matrix(self):
        f = self.display_fixture()
        f.raw['profiles'][0]['maps'].pop('m2'); f.payload['entries'] = f.entries[:1]
        loaded = self.load(f)
        self.assertEqual(loaded.profiles, {PROFILE}); self.assertFalse(loaded.errors)
        self.assertTrue(loaded.binding(PROFILE, 'm1', '1')['usable'])
        self.assertIsNone(loaded.binding(PROFILE, 'm2', '1'))
        self.assertEqual(self.load(f, 'difficult').profiles, set())
        self.assertEqual(loaded.adapters.matrix_profile_to_canonical(PROFILE, 'm1', '1').tolist(),
                         f.entries[0]['matrixAssetToBase'])
        f.entries[0]['sourceVersion'] = 'wrong'
        self.assertIn(PROFILE, self.load(f).errors)

    def test_registered_selection_survives_restore_and_is_hidden_from_legacy(self):
        f = self.display_fixture(); loaded = self.load(f)
        path = Path(__file__).resolve().parents[1] / 'app/identityv_daily_assistant.py'
        tree = ast.parse(path.read_text('utf-8-sig'))
        names = {'_author_route_options', '_restore_route_profile_preference', 'normalize_route_profile_id'}
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        nodes += [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                  and n.name in names and n not in nodes]
        ns = dict(Path=Path, SUJU_ROUTE_PROFILE_ID='suju', DEFAULT_ROUTE_PROFILE_ID='wang-qingxin',
            SHARED_STRUCTURE_PROFILE_ID='shared-structure', gallery_of=lambda _: None)
        options = next(n for n in tree.body if isinstance(n, ast.Assign)
                       and any(isinstance(t, ast.Name) and t.id == 'ROUTE_PROFILE_OPTIONS' for t in n.targets))
        exec(compile(ast.Module(body=[options], type_ignores=[]), str(path), 'exec'), ns)
        ns['ROUTE_PROFILE_LABELS'] = dict(ns['ROUTE_PROFILE_OPTIONS'])
        self.assertEqual(ns['ROUTE_PROFILE_LABELS'][PROFILE], '展十 · 噩梦')
        exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(path), 'exec'), ns)
        subject = SimpleNamespace(_shared_structure_mode_enabled=lambda: True, optional_author_displays=loaded,
            author_route_catalog=loaded.catalog, game_mode='nightmare', settings={'routeProfileId':PROFILE})
        self.assertEqual(ns['_author_route_options'](subject), ((PROFILE,'展十 · 噩梦'),))
        ns['_restore_route_profile_preference'](subject)
        self.assertEqual(subject.route_profile_id, PROFILE)
        subject._shared_structure_mode_enabled = lambda: False
        self.assertNotIn(PROFILE, dict(ns['_author_route_options'](subject)))


if __name__ == '__main__':
    unittest.main()
