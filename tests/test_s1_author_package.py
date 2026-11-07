"""Synthetic archive/transaction contracts, plus an opt-in real 87-image probe.

Synthetic native bytes test the importer boundary, not geometry or gameplay.
"""
import copy
import hashlib
import io
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import star_map_import as imp
import s1_author_package as s1


def encoded(x):return json.dumps(x,sort_keys=True,separators=(',',':')).encode()
def record(path,data):return dict(path=path,bytes=len(data),sha256=hashlib.sha256(data).hexdigest())


class S1AuthorPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.root=self.base/'install';self.root.mkdir()
        self.write('_internal/version.json',encoded(dict(displayVersion='1.0.15',numericVersion='1.0.0.19')))
        self.write('custom-routes/user.json',b'keep-custom-route')
        raw=io.BytesIO();Image.new('RGB',(16,16),(60,70,100)).save(raw,format='JPEG');jpeg=raw.getvalue()
        raw=io.BytesIO();Image.new('RGBA',(16,16),(60,70,100,255)).save(raw,format='PNG');png=raw.getvalue()
        self.payload={};layers=[]
        for mode in s1.MODES:
            prefix=f'_internal/seasons/s1/{mode}/';entries=[];maps={};native={}
            for mid in sorted(s1.layouts(mode)):
                maps[mid]={'floors':{}}
                for floor in s1.FLOORS:
                    ref=f'references/entries/{mid}-{floor}-shared.npz';img=f'assets/game-native/{mid}/{floor}.png'
                    native[ref]=('fixture-native-'+mid+floor).encode();native[img]=png
                    ver=f'{mid}@'+('b'*64)+'/'+floor
                    entries.append(dict(profile='shared-structure',mapId=mid,floor=floor,resourcePath=ref,
                        sourceVersion=s1.SOURCE_VERSION,baseSnapshot=s1.SOURCE_VERSION,structureVersionKey=ver))
                    maps[mid]['floors'][floor]=dict(coordinateHeight=16,coordinateWidth=16,
                        coordinateSpace=f'game-native-pixels:{mid}:{floor}',referenceResource=ref)
                    path=s1.image_path(mode,mid,floor);self.payload[path]=jpeg
                    layers.append(dict(season='s1',mode=mode,mapId=mid,floor=floor,displayPath=path,
                        displaySha256=hashlib.sha256(jpeg).hexdigest(),displayBytes=len(jpeg),displayShapeHW=[16,16],
                        referenceResource=prefix+ref,referenceSha256=hashlib.sha256(native[ref]).hexdigest(),
                        nativeImageResource=prefix+img,nativeImageSha256=hashlib.sha256(png).hexdigest(),
                        referenceShapeHW=[16,16],coordinateSpace=f'game-native-pixels:{mid}:{floor}',
                        sourceVersion=s1.SOURCE_VERSION,baseSnapshot=s1.SOURCE_VERSION,structureVersionKey=ver,
                        matrixDisplayToReference=[[1,0,0],[0,1,0],[0,0,1]],
                        registration=dict(status='offline-reviewed',method='affine-features-full-outline-review-v1',
                            outlineIoU=.999,inliers=20,evidenceSha256='a'*64)))
            native['references/shared-structure-index.json']=encoded(dict(entries=entries))
            native['references/coordinate-pack.json']=encoded(dict(maps=maps))
            native['references/structure-version-bindings.json']=encoded(dict(schema='fixture'))
            for path,data in native.items():self.write(prefix+path,data)
            self.write(prefix+'native-map-manifest.json',encoded(dict(season='s1',mode=mode,sourceVersion=s1.SOURCE_VERSION,
                files=[record(p,b) for p,b in native.items()])))
        self.document=dict(schema=s1.BINDINGS_SCHEMA,season='s1',sourceVersion=s1.SOURCE_VERSION,profileId=s1.PROFILE_ID,
            identityAuthority=False,structureValidationAuthority=False,crossFloorProjectionAllowed=False,
            currentFrameGeometryFitRequired=True,layers=layers,missingAuthorLayers=[dict(mode=mode,mapId=mid,floor='-4',
                fallback='canonical-native-if-available') for mode in s1.MODES for mid in sorted(s1.layouts(mode))])
        self.dependencies=[record(p,(self.root/p).read_bytes()) for p in sorted(s1.required_dependencies())]
        self.approved=set();self.addCleanup(patch.stopall)
        patch.object(s1,'APPROVED_BINDINGS',self.approved).start()
        self.archive=self.package()

    def write(self,path,data):
        p=self.root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)

    def package(self,*,document=None,metadata_edit=None,extra=None,approve=True,name='package.zip'):
        payload=dict(self.payload);payload[s1.BINDINGS_PATH]=encoded(document or self.document)
        if approve:self.approved.add(hashlib.sha256(payload[s1.BINDINGS_PATH]).hexdigest())
        metadata=dict(schema=s1.SCHEMA,schemaVersion=1,revision=1,season='s1',sourceVersion=s1.SOURCE_VERSION,
            packageId=s1.PACKAGE_ID,clientCompatibility=imp.CLIENT_COMPATIBILITY,
            files=[record(p,b) for p,b in payload.items()],requiresFiles=self.dependencies)
        if metadata_edit:metadata_edit(metadata)
        path=self.base/name
        with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr('map-package.json',encoded(metadata))
            for p,b in payload.items():z.writestr('data/'+p,b)
            if extra:extra(z)
        return path

    def inspect(self,path=None,season='s1'):
        return imp.inspect_package(path or self.archive,self.root,season=season)

    def install(self,path=None,**kwargs):
        return imp.install_author_package_live(path or self.archive,self.root,application_idle=True,season='s1',**kwargs)

    def assert_untouched(self):
        self.assertEqual((self.root/'custom-routes/user.json').read_bytes(),b'keep-custom-route')
        for row in self.dependencies:self.assertEqual(imp._sha(self.root/row['path']),row['sha256'])

    def test_full87_inspect_install_repeat_and_exact_base_preserved(self):
        result=self.inspect();self.assertEqual(result['fileCount'],88);self.assertTrue(imp.supports_live_author_import(result))
        first=self.install();self.assertEqual(first['added'],88);self.assertEqual(first['season'],'s1')
        receipt=self.root/f'work/map-packages/{s1.PACKAGE_ID}.json';before=receipt.read_bytes();inode=receipt.stat().st_ino
        second=self.install();self.assertEqual(second['added'],0);self.assertTrue(second['receiptPreserved'])
        self.assertEqual(receipt.read_bytes(),before);self.assertEqual(receipt.stat().st_ino,inode);self.assert_untouched()

    def test_unknown_or_cross_season_never_imports(self):
        for season in ['s0','s99']:
            with self.subTest(season=season),self.assertRaises(imp.ImportRejected):self.inspect(season=season)
        bad=self.package(metadata_edit=lambda d:d.update(season='s0'))
        with self.assertRaisesRegex(imp.ImportRejected,'season-mismatch'):self.inspect(bad)
        self.assert_untouched()

    def test_draft_candidate_cannot_self_approve(self):
        doc=copy.deepcopy(self.document);doc['layers'][0]['registration']['status']='candidate'
        path=self.package(document=doc,approve=False)
        with self.assertRaisesRegex(imp.ImportRejected,'registration-not-reviewed'):self.inspect(path)

    def test_empty_or_truncated_dependencies_fail_closed(self):
        for deps in [[],self.dependencies[:-1]]:
            p=self.package(metadata_edit=lambda d:d.update(requiresFiles=deps))
            with self.assertRaises(imp.ImportRejected):self.inspect(p)

    def test_native_mutation_rejects_even_matching_source_version(self):
        self.write(self.dependencies[0]['path'],b'changed-native')
        with self.assertRaisesRegex(imp.ImportRejected,'missing-or-incompatible-dependency'):self.inspect()

    def test_rejects_duplicate_map_floor_and_b4_forgery(self):
        for mutate in [lambda d:d['layers'].__setitem__(1,copy.deepcopy(d['layers'][0])),
                       lambda d:d['layers'][0].update(floor='-4'),
                       lambda d:d['missingAuthorLayers'].pop()]:
            doc=copy.deepcopy(self.document);mutate(doc)
            with self.subTest(doc=doc['layers'][0]['floor']),self.assertRaises(imp.ImportRejected):self.inspect(self.package(document=doc))

    def test_rejects_bad_matrices_and_canvas(self):
        for matrix in [[[0,0,1],[0,0,1],[0,0,1]],[[float('nan'),0,0],[0,1,0],[0,0,1]],
                       [[-1,0,0],[0,1,0],[0,0,1]],[[1,0,0],[0,1,0],[0,1,1]]]:
            doc=copy.deepcopy(self.document);doc['layers'][0]['matrixDisplayToReference']=matrix
            with self.subTest(matrix=matrix),self.assertRaises(imp.ImportRejected):self.inspect(self.package(document=doc))
        doc=copy.deepcopy(self.document);doc['layers'][0]['displayShapeHW']=[16000,16000]
        with self.assertRaises(imp.ImportRejected):self.inspect(self.package(document=doc))

    def test_rejects_identity_authority_and_reference_substitution(self):
        for edit in [lambda d:d.update(identityAuthority=True),lambda d:d.update(crossFloorProjectionAllowed=True),
                     lambda d:d['layers'][0].update(referenceSha256='0'*64),
                     lambda d:d['layers'][0].update(coordinateSpace='game-native-pixels:106002:-2'),
                     lambda d:d['layers'][0].update(referenceResource='_internal/references/s0.npz')]:
            doc=copy.deepcopy(self.document);edit(doc)
            with self.assertRaises(imp.ImportRejected):self.inspect(self.package(document=doc))

    def test_unsafe_zip_paths_links_duplicates_extra_data(self):
        for name in ['../escape','/absolute','C:/escape','data/x\\y','data/CON.png','data/x.','extra.exe',
                     'data/'+next(iter(self.payload)).upper()]:
            with self.subTest(name=name):
                path=self.package(extra=lambda z:z.writestr(name,b'bad'))
                with self.assertRaises(imp.ImportRejected):self.inspect(path)
        info=zipfile.ZipInfo('link');info.create_system=3;info.external_attr=(stat.S_IFLNK|0o777)<<16
        with self.assertRaises(imp.ImportRejected):self.inspect(self.package(extra=lambda z:z.writestr(info,b'/tmp/x')))
        self.assert_untouched()

    def test_rejects_archive_and_expanded_limits(self):
        with patch.object(imp,'MAX_ARCHIVE_BYTES',10),self.assertRaisesRegex(imp.ImportRejected,'archive-size-limit'):self.inspect()
        with patch.object(imp,'MAX_TOTAL_BYTES',10),self.assertRaisesRegex(imp.ImportRejected,'expanded-size-limit'):self.inspect()
        with patch.object(imp,'MAX_FILE_BYTES',10),self.assertRaises(imp.ImportRejected):self.inspect()

    def test_bad_payload_and_duplicate_json_key(self):
        name=next(iter(self.payload));self.payload[name]=b'not-an-image'
        with self.assertRaises(imp.ImportRejected):self.inspect(self.package())
        with zipfile.ZipFile(self.base/'bad.zip','w') as z:
            z.writestr('map-package.json',b'{"season":"s1","season":"s0"}');z.writestr('data/x',b'x')
        with self.assertRaises(imp.ImportRejected):self.inspect(self.base/'bad.zip')

    def test_target_symlink_rejected(self):
        target=self.root/s1.IMAGE_PREFIX.rstrip('/');target.parent.mkdir(parents=True,exist_ok=True)
        target.symlink_to(self.base,target_is_directory=True)
        with self.assertRaisesRegex(imp.ImportRejected,'linked-target'):self.inspect()

    def test_cancelled_addition_rolls_back(self):
        def cancelled():return any((self.root/p).exists() for p in self.payload)
        with self.assertRaisesRegex(imp.ImportRejected,'cancelled'):self.install(cancelled=cancelled)
        self.assertFalse(any((self.root/p).exists() for p in self.payload));self.assert_untouched()

    def test_receipt_missing_dependencies_blocks_repeat(self):
        self.install();path=self.root/f'work/map-packages/{s1.PACKAGE_ID}.json';doc=json.loads(path.read_bytes());doc.pop('requiresFiles');path.write_bytes(encoded(doc))
        with self.assertRaises(imp.ImportRejected):self.inspect()

    def test_changed_archive_selection_rejected(self):
        with self.assertRaisesRegex(imp.ImportRejected,'selected-archive-changed'):
            self.install(expected_archive_sha256='0'*64)

    def stage_managed_base(self, data=b'base-v1', version='fixture-base-v1'):
        import managed_base_resources as managed
        name='_internal/references/managed-fixture.json'
        self.write(managed.CANDIDATE+'/payload/'+name,data)
        self.write(managed.CANDIDATE+'/manifest.json',encoded(
            managed.manifest_for_rows([record(name,data)],version)))
        return self.root/name

    def test_installed_s1_author_allows_next_base_check_and_unrelated_owned_upgrade(self):
        import managed_base_resources as managed
        self.install();receipt=self.root/f'work/map-packages/{s1.PACKAGE_ID}.json'
        before=receipt.read_bytes();base=self.stage_managed_base()
        self.assertEqual(managed.repair_managed_base_resources(self.root)['changed'],1)
        self.assertEqual(managed.repair_managed_base_resources(self.root)['status'],'healthy')
        self.stage_managed_base(b'base-v2','fixture-base-v2')
        self.assertEqual(managed.repair_managed_base_resources(self.root)['changed'],1)
        self.assertEqual(base.read_bytes(),b'base-v2')
        self.assertEqual(receipt.read_bytes(),before);self.assert_untouched()

    def test_s1_dependency_change_blocks_entire_base_repair_without_mutation(self):
        import managed_base_resources as managed
        self.install();base=self.stage_managed_base()
        dependency=self.root/self.dependencies[0]['path'];dependency.write_bytes(b'changed-native')
        plan=managed.plan_managed_base_resources(self.root)
        self.assertEqual(plan['status'],'blocked')
        self.assertIn('installed-package-dependency-conflict',[row['reason'] for row in plan['issues']])
        with self.assertRaisesRegex(managed.ManagedBaseResourceError,'preflight-blocked'):
            managed.repair_managed_base_resources(self.root)
        self.assertFalse(base.exists());self.assertFalse((self.root/managed.STATE).exists())
        self.assertEqual(dependency.read_bytes(),b'changed-native')

    def test_invalid_s1_receipt_scope_still_blocks_before_base_write(self):
        import managed_base_resources as managed
        self.install();base=self.stage_managed_base()
        path=self.root/f'work/map-packages/{s1.PACKAGE_ID}.json';before=json.loads(path.read_bytes())
        for dependencies in ([],self.dependencies[:-1],
                [dict(row,path='_internal/references/foreign.json') if index==0 else row
                 for index,row in enumerate(self.dependencies)]):
            with self.subTest(dependencies=len(dependencies)):
                doc=copy.deepcopy(before);doc['requiresFiles']=dependencies;path.write_bytes(encoded(doc))
                malformed=path.read_bytes()
                with self.assertRaises(managed.ManagedBaseResourceError) as caught:
                    managed.repair_managed_base_resources(self.root)
                self.assertEqual(caught.exception.code,'installed-package-receipt-rejected')
                self.assertIsInstance(caught.exception.__cause__,imp.ImportRejected)
                self.assertFalse(base.exists());self.assertEqual(path.read_bytes(),malformed)


if __name__=='__main__':unittest.main()
