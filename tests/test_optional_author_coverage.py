"""Optional displays use only declared current floors; missing layers fall back safely."""
from pathlib import Path
from types import SimpleNamespace
import copy,json,sys,tempfile,unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from optional_author_displays import OptionalAuthorDisplays
from author_route_catalog import AuthorRouteCatalog

class OptionalAuthorCoverageTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
  self.profile='lianghapi-single';self.folder=self.root/'references/author-packs'/self.profile;self.folder.mkdir(parents=True)
  self.pack=SimpleNamespace(maps=[SimpleNamespace(id=m,layers={'1':None}) for m in ('m1','m2')])
  self.adapters=SimpleNamespace(canonical_pack_id='native',coordinate_reference_profile_id='shared-structure',profiles={})
  self.versions={};self.entries=[];maps={}
  for i,m in enumerate(('m1','m2')):
   path=f'assets/authors/{self.profile}/{m}.png';p=self.root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'display')
   version=f'v{i}';key=f'{m}@hash/1';resource=dict(layoutId=m,structureRawRecordSha256='hash',floor='1')
   self.versions[('zhan10-speed',m,'variant-01','1')]=dict(expected=dict(sourceVersion=version,structureVersionKey=key))
   self.entries.append(dict(profileId=self.profile,mapId=m,floor='1',assetPath=path,sourceVersion=version,nativeResourceKey=resource,structureVersionKey=key,matrixAssetToBase=np.eye(3).tolist(),matrixAssetToCanonical=np.eye(3).tolist(),registration=dict(status='confirmed'),identityAuthority=False,structureValidationAuthority=False,currentFrameGeometryFitRequired=True,crossFloorProjectionAllowed=False,coordinateBridge=dict(baseToNativeIsIdentity=True,authorGeometryErrorUnchanged=True)))
   maps[m]=[dict(id='variant-01',displayName='route',resourceBase='asset-root',identityStatus='display-only',displayPolicy='requires-current-shared-structure-fit',layers={'1':path})]
  self.raw=dict(schemaVersion=1,profiles=[dict(id=self.profile,displayName='凉哈皮',authorName='凉哈皮',routeFamilyName='单人路线',runtimeEnabled=True,settingsSelectable=True,alignedOverlayEligible=True,maps=maps)])
  self.payload=dict(schema='canonical-author-display-bindings-v1',profileId=self.profile,canonicalPackId='native',coordinateReferenceProfileId='shared-structure',entries=self.entries)
 def load(self):
  (self.folder/'catalog.json').write_text(json.dumps(self.raw),encoding='utf-8');(self.folder/'display-bindings.json').write_text(json.dumps(self.payload),encoding='utf-8')
  return OptionalAuthorDisplays(self.root,self.pack,None,self.adapters,self.versions)
 def test_registered_author_mixed_versions_and_exact_per_floor_matrix(self):
  loaded=self.load();self.assertEqual(loaded.profiles,{self.profile});self.assertFalse(loaded.errors)
  for m in ('m1','m2'):
   self.assertTrue(loaded.binding(self.profile,m,'1')['usable']);np.testing.assert_array_equal(loaded.adapters.matrix_profile_to_canonical(self.profile,m,'1'),np.eye(3))
 def test_partial_coverage_has_no_fabricated_binding(self):
  self.raw['profiles'][0]['maps'].pop('m2');self.payload['entries']=self.entries[:1]
  loaded=self.load();self.assertFalse(loaded.errors);self.assertIsNone(loaded.binding(self.profile,'m2','1'));self.assertIsNone(loaded.adapters.matrix_profile_to_canonical(self.profile,'m2','1'))
 def test_catalog_and_bindings_must_cover_identical_layers(self):
  self.payload['entries']=self.entries[:1];self.assertIn(self.profile,self.load().errors)
 def test_wrong_structure_floor_source_version_and_matrix_are_rejected(self):
  for key,value in [('structureVersionKey','wrong'),('floor','B1'),('sourceVersion','wrong'),('matrixAssetToBase',np.zeros((3,3)).tolist())]:
   with self.subTest(key=key):
    before=copy.deepcopy(self.entries[0]);self.entries[0][key]=value;self.assertIn(self.profile,self.load().errors);self.entries[0].clear();self.entries[0].update(before)
 def test_installed_author_is_selectable_without_loading_another_mode(self):
  import ast
  path=Path(__file__).resolve().parents[1]/'app/identityv_daily_assistant.py';tree=ast.parse(path.read_text('utf-8-sig'));method=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='_author_route_options')
  namespace=dict(SUJU_ROUTE_PROFILE_ID='suju',DEFAULT_ROUTE_PROFILE_ID='wang-qingxin',ROUTE_PROFILE_LABELS={'lianghapi-single':'凉哈皮','shared-structure':'结构参考'},SHARED_STRUCTURE_PROFILE_ID='shared-structure',Path=Path)
  exec(compile(ast.fix_missing_locations(ast.Module(body=[method],type_ignores=[])),str(path),'exec'),namespace)
  loaded=self.load();subject=SimpleNamespace(_shared_structure_mode_enabled=lambda:True,optional_author_displays=loaded,author_route_catalog=loaded.catalog)
  self.assertEqual(namespace['_author_route_options'](subject),(('lianghapi-single','凉哈皮'),))
 def test_legacy_payload_source_version_still_validated(self):
  self.raw['profiles'][0]['maps'].pop('m2');self.payload['entries']=self.entries[:1];self.payload['sourceVersion']=self.entries[0].pop('sourceVersion')
  self.assertFalse(self.load().errors);self.payload['sourceVersion']='wrong';self.assertIn(self.profile,self.load().errors)

if __name__=='__main__':unittest.main()
