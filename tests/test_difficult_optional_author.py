"""Mode-scoped author import, persistence and missing-floor display fallback."""
import ast
import copy
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
import test_map_resource_context as browse_fixture
from map_resource_context import load_browse_context

PROFILE = 'aichicu-difficult'
PACKAGE = 'difficult-aichicu'


def runtime_methods():
    path = Path(__file__).resolve().parents[1] / 'app/identityv_daily_assistant.py'
    names = {'_shared_display_profile_id', '_effective_route_profile',
             '_author_route_options', '_restore_route_profile_preference', '_legacy_difficult_display_profile'}
    tree = ast.parse(path.read_text('utf-8-sig'))
    methods = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in names]
    ns = dict(Path=Path, SHARED_STRUCTURE_PROFILE_ID='shared-structure',
              DEFAULT_ROUTE_PROFILE_ID='wang-qingxin', SUJU_ROUTE_PROFILE_ID='suju',
              ROUTE_PROFILE_LABELS={PROFILE:'爱吃醋 · 困难','zhan10-speed':'展十',
                                    'shared-structure':'结构底图'},
              normalize_route_profile_id=lambda x:x, gallery_of=lambda x:None)
    exec(compile(ast.fix_missing_locations(ast.Module(body=methods,type_ignores=[])), str(path), 'exec'),ns)
    return ns


class DifficultAuthorTests(unittest.TestCase):
    def importer_fixture(self):
        f = live_fixture.LiveAuthorImportTests(); f.setUp(); self.addCleanup(f.temp.cleanup)
        f.dependencies = []
        for i,path in enumerate(sorted(importer.DIFFICULT_AUTHOR_DEPENDENCIES)):
            data = f'difficult-base-{i}'.encode(); f.write(path,data)
            f.dependencies.append(live_fixture.row(path,data))
        f.core_bytes = (f.root/live_fixture.CORE).read_bytes()
        return f

    def display_fixture(self):
        f = coverage_fixture.OptionalAuthorCoverageTests(); f.setUp(); self.addCleanup(f.temp.cleanup)
        f.profile = PROFILE
        f.folder = f.root/'references/author-packs'/PROFILE; f.folder.mkdir(parents=True)
        f.raw['profiles'][0]['id'] = PROFILE; f.payload['profileId'] = PROFILE
        for entry in f.entries:
            entry['profileId'] = PROFILE
        return f

    def load(self,f,mode):
        (f.folder/'catalog.json').write_text(json.dumps(f.raw),encoding='utf8')
        (f.folder/'display-bindings.json').write_text(json.dumps(f.payload),encoding='utf8')
        return OptionalAuthorDisplays(f.root,f.pack,None,f.adapters,f.versions,mode=mode)

    def test_real_zip_first_repeat_and_receipt_owned_removal(self):
        from author_package_management import inspect_author_package_removal, remove_author_package_live
        f=self.importer_fixture(); asset='_internal/assets/authors/aichicu-difficult/m1.png'
        p=f.package(PACKAGE,{asset:b'display'})
        first=f.live(p); self.assertEqual(first['added'],1)
        self.assertEqual(f.live(p)['added'],0)
        plan=inspect_author_package_removal(f.root,PACKAGE)
        self.assertTrue(plan)
        result=remove_author_package_live(f.root,PACKAGE,application_idle=True,
            expected_receipt_sha256=plan['receiptSha256'],expected_plan_sha256=plan['planSha256'])
        self.assertFalse((f.root/asset).exists())
        f.assert_base_preserved()

    def test_requires_difficult_bases_and_cannot_write_nightmare_or_core(self):
        f=self.importer_fixture(); asset='_internal/assets/authors/aichicu-difficult/m1.png'
        f.dependencies=f.dependencies[:1]; p=f.package(PACKAGE,{asset:b'display'})
        with self.assertRaisesRegex(importer.ImportRejected,'base-dependencies-not-declared'):
            importer.inspect_package(p,f.root)
        f=self.importer_fixture()
        for path in ['_internal/assets/authors/nightingale/m1.png',live_fixture.CORE]:
            p=f.package(PACKAGE,{path:b'forbidden'})
            with self.assertRaises(importer.ImportRejected):importer.inspect_package(p,f.root)

    def test_mode_isolation_partial_floor_and_version_rejection(self):
        f=self.display_fixture(); loaded=self.load(f,'difficult')
        self.assertEqual(loaded.profiles,{PROFILE}); self.assertFalse(loaded.errors)
        self.assertEqual(self.load(f,'nightmare').profiles,set())
        self.assertIsNone(loaded.binding(PROFILE,'m1','2'))
        f.entries[0]['sourceVersion']='wrong'
        self.assertIn(PROFILE,self.load(f,'difficult').errors)

    def test_selected_author_survives_restore_and_missing_floor_uses_shared(self):
        f=self.display_fixture(); loaded=self.load(f,'difficult'); ns=runtime_methods()
        s=SimpleNamespace(optional_author_displays=loaded,author_route_catalog=loaded.catalog,
            difficult_native_context=object(),game_mode='difficult',settings={'routeProfileId':PROFILE},
            preferred_route_profile_id=PROFILE,pack=SimpleNamespace(),_shared_structure_mode_enabled=lambda:True,
            _custom_route_active=lambda *a:False,_custom_gallery_selected=lambda:False)
        s._shared_display_profile_id=lambda:ns['_shared_display_profile_id'](s)
        s._legacy_difficult_display_profile=lambda m,f:ns['_legacy_difficult_display_profile'](s,m,f)
        self.assertEqual(s._shared_display_profile_id(),PROFILE)
        self.assertEqual(ns['_effective_route_profile'](s,'m1','1'),PROFILE)
        self.assertEqual(ns['_effective_route_profile'](s,'m1','2'),'shared-structure')
        self.assertEqual(ns['_author_route_options'](s),((PROFILE,'爱吃醋 · 困难'),))
        ns['_restore_route_profile_preference'](s); self.assertEqual(s.route_profile_id,PROFILE)
        s.optional_author_displays=None
        ns['_restore_route_profile_preference'](s); self.assertEqual(s.route_profile_id,'zhan10-speed')

    def test_browse_loads_only_its_mode_sidecar(self):
        # Resolve the existing temporary declaration fixture without network/runtime construction.
        cls=next(v for v in vars(browse_fixture).values() if isinstance(v,type) and issubclass(v,unittest.TestCase) and hasattr(v,'native'))
        f=cls();f.setUp();self.addCleanup(f.temp.cleanup);f.native();f.nightmare()
        f.catalog(PROFILE,{'map-0':{'1':'assets/authors/aichicu-difficult/m1.png'}},f'references/author-packs/{PROFILE}/catalog.json')
        f.png('assets/authors/aichicu-difficult/m1.png')
        self.assertIn(PROFILE,load_browse_context(f.root,'difficult').catalog.profiles)
        self.assertNotIn(PROFILE,load_browse_context(f.root,'nightmare').catalog.profiles)

if __name__=='__main__':unittest.main()
