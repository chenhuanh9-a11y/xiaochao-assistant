"""Synthetic display-byte contracts, not live-game/renderer asset acceptance."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'app'), str(ROOT/'experiments/qt_quick')]
import cv2
import numpy as np
from s1_native_display import S1NativeDisplay
from client_route_preferences import prepare_display_layer
from client_route_reprojection import prepare_route_reprojection


class NativeDisplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.mid, self.floor = '106015', '-1'
        self.shape = (18, 24)
        self.pixels = np.zeros((*self.shape, 4), np.uint8)
        self.pixels[2:16, 3:21] = (25, 75, 145, 180)
        self.pixels[4:9, 6:12] = (220, 40, 15, 255)
        self.version = dict(sourceVersion='version', baseSnapshot='version', structureVersionKey='structure/-1')
        self.binding = dict(profile='shared-structure', mapId=self.mid, floor=self.floor, variant='variant-01')
        self.canvas = dict(coordinateSpace=f'game-native-pixels:{self.mid}:{self.floor}', layout=self.mid,
            floor=self.floor, width=24, height=18, shapeHW=[18,24], axes='x-right,y-down', worldFloor=4)
        self.image_rel = f'assets/game-native/{self.mid}/{self.floor}.png'
        self.path = self.root/self.image_rel
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(cv2.imencode('.png',self.pixels)[1].tobytes())
        self.digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.ref_rel = f'references/{self.mid}--1.npz'
        self.ref_path = self.root/self.ref_rel
        self.ref_path.parent.mkdir(parents=True)
        self.ref_path.write_bytes(b'fixture-typed-product')
        self.meta = dict(sourceKind='game-rendered-shared-structure', binding=self.binding, canvas=self.canvas,
            sourceGeometry=dict(projectionApplied=False, nativeShapeHW=[18,24], gameFloor=-1,
                sourceHeight=4, rendererImageSha256=self.digest), **self.version)
        self.ref = SimpleNamespace(path=self.ref_path, metadata=self.meta, shape=self.shape,
            clean_mask=np.full(self.shape,255,np.uint8), validation_mask=np.full(self.shape,255,np.uint8))
        self.entry = dict(resourcePath=self.ref_rel, **self.version)
        self.declaration = dict(coordinateSpace=self.canvas['coordinateSpace'], coordinateWidth=24,
            coordinateHeight=18, sourceLayout=self.mid, worldFloor=4, referenceResource=self.ref_rel,
            matrixCanonicalToLegacyRecognition=np.eye(3).tolist(), **self.version)
        self.adapters = SimpleNamespace(maps={self.mid:dict(floors={self.floor:self.declaration})},
            matrix_profile_to_canonical=lambda *a:np.eye(3), profiles={})
        self.ctx = SimpleNamespace(root=self.root, mode='difficult',
            manifest=dict(sourceVersion='version',files=[dict(path=self.image_rel,
                bytes=self.path.stat().st_size,sha256=self.digest)]),
            resources=SimpleNamespace(entries={('shared-structure',self.mid,'variant-01',self.floor):self.entry}),
            _declared_files=frozenset([self.path,self.ref_path]), adapters=self.adapters)
        def local(s):
            p=(self.root/s).resolve()
            if not p.is_relative_to(self.root):raise ValueError('escape')
            return p
        self.ctx.local=local
        self.loader=S1NativeDisplay(self.ctx)
        self.ctx.rendered_display_layer=self.loader.resolve
        self.engine=SimpleNamespace(qt_season='s1',qt_native_context=self.ctx,
            qt_shared_provider=lambda *a:self.ref,qt_adapters=self.adapters,
            qt_catalog=None,qt_optional_displays=None,pack=SimpleNamespace(mode='difficult'))

    def layer(self): return self.loader.resolve(self.mid,self.floor,self.ref)
    def register_overlay(self):
        from s1_native_display import REGISTRATION, PRODUCER_CONTRACT
        original_digest=self.digest
        changed=self.pixels.copy();changed[4:9,6:12,:3]=(20,240,80)
        self.path.write_bytes(cv2.imencode('.png',changed)[1].tobytes())
        final_digest=hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.loader._inventory[self.image_rel].update(bytes=self.path.stat().st_size,sha256=final_digest)
        self.ref.clean_mask=np.where(self.pixels[:,:,3]>0,255,0).astype(np.uint8)
        self.meta['sourceGeometry']['alphaRule']='alpha>0'
        self.loader._inventory[self.ref_rel]=dict(path=self.ref_rel,bytes=self.ref_path.stat().st_size,sha256=hashlib.sha256(self.ref_path.read_bytes()).hexdigest())
        row=dict(binding=copy.deepcopy(self.binding),canvas=copy.deepcopy(self.canvas),referenceResource=self.ref_rel,
            referenceSha256=self.loader._inventory[self.ref_rel]['sha256'],rendererImageSha256=original_digest,
            displayResource=self.image_rel,displaySha256=final_digest,matrixDisplayToReference=np.eye(3).tolist(),**self.version)
        self.registration=dict(schema='xingtu-s1-native-display-bindings-v1',season='s1',mode='difficult',
            sourceVersion='version',producerContract=PRODUCER_CONTRACT,coordinateChanges=[],entries=[row])
        self.registration_path=self.root/REGISTRATION
        self.ctx._declared_files=self.ctx._declared_files|{self.registration_path}
        self.write_registration()
        return changed

    def write_registration(self):
        from s1_native_display import REGISTRATION
        data=json.dumps(self.registration).encode();self.registration_path.write_bytes(data)
        self.loader._inventory[REGISTRATION]=dict(path=REGISTRATION,bytes=len(data),sha256=hashlib.sha256(data).hexdigest())

    def test_registered_overlay_preserves_final_color_and_original_hash(self):
        expected=self.register_overlay();layer=self.layer().layer
        self.assertIsNotNone(layer);np.testing.assert_array_equal(layer['pixels'],expected)
        self.assertEqual(layer['resourceIdentity']['rendererImageSha256'],self.digest)
        self.assertNotEqual(layer['resourceIdentity']['sha256'],self.digest)
        self.assertTrue(layer['resourceIdentity']['displayRegistrationSha256'])

    def test_overlay_accepts_production_binary_one_mask(self):
        expected=self.register_overlay()
        self.ref.clean_mask=(self.ref.clean_mask != 0).astype(np.uint8)
        layer=self.layer().layer
        self.assertIsNotNone(layer)
        np.testing.assert_array_equal(layer['pixels'],expected)
        self.assertEqual(int(self.ref.clean_mask.max()),1)

    def test_changed_alpha_rule_rejects_cached_overlay(self):
        self.register_overlay()
        self.assertIsNotNone(self.layer().layer)
        self.meta['sourceGeometry']['alphaRule']='invalid'
        self.assertIsNone(self.layer().layer)

    def test_same_alpha_wrong_rgb_still_needs_exact_registration(self):
        from s1_native_display import REGISTRATION
        self.register_overlay();self.loader._inventory.pop(REGISTRATION)
        self.assertIsNone(self.layer().layer)

    def test_overlay_binding_mutations_rejected(self):
        self.register_overlay();original=copy.deepcopy(self.registration)
        mutations=[lambda d:d.update(mode='nightmare'),lambda d:d.update(sourceVersion='old'),
            lambda d:d.update(coordinateChanges=['resize']),
            lambda d:d['entries'].append(copy.deepcopy(d['entries'][0])),
            lambda d:d['entries'][0]['binding'].update(floor='-2'),
            lambda d:d['entries'][0].update(rendererImageSha256='0'*64),
            lambda d:d['entries'][0].update(referenceSha256='0'*64),
            lambda d:d['entries'][0].update(structureVersionKey='old'),
            lambda d:d['entries'][0].update(displaySha256='0'*64),
            lambda d:d['entries'][0].update(matrixDisplayToReference=[[1,0,2],[0,1,0],[0,0,1]])]
        for change in mutations:
            with self.subTest(change=change):
                self.registration=copy.deepcopy(original);self.write_registration();self.assertIsNotNone(self.layer().layer)
                change(self.registration);self.write_registration();self.assertIsNone(self.layer().layer)

    def test_registration_file_tamper_rejects_cached_bitmap(self):
        self.register_overlay();self.assertIsNotNone(self.layer().layer)
        self.registration_path.write_bytes(self.registration_path.read_bytes()+b' ')
        self.assertIsNone(self.layer().layer)

    def test_registration_cannot_hide_changed_reference_footprint(self):
        self.register_overlay();self.assertIsNotNone(self.layer().layer)
        self.ref.clean_mask[:]=0
        self.assertIsNone(self.layer().layer)

    def test_registered_final_image_cannot_be_changed_with_same_alpha(self):
        changed=self.register_overlay();self.assertIsNotNone(self.layer().layer)
        changed[4:9,6:12,:3]=(80,40,10)
        self.path.write_bytes(cv2.imencode('.png',changed)[1].tobytes())
        self.loader._inventory[self.image_rel].update(bytes=self.path.stat().st_size,sha256=hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertIsNone(self.layer().layer)

    def test_exact_bytes_colors_alpha_and_identity_readonly(self):
        r=self.layer();self.assertEqual(r.reason,'');self.assertEqual(r.layer['displayKind'],'game-native')
        np.testing.assert_array_equal(r.layer['pixels'],self.pixels)
        np.testing.assert_array_equal(r.layer['matrixDisplayToReference'],np.eye(3))
        self.assertFalse(r.layer['pixels'].flags.writeable)
        self.assertFalse(r.layer['matrixDisplayToReference'].flags.writeable)
        self.assertIs(self.layer().layer['pixels'],r.layer['pixels'])

    def test_calibration_never_borrows_native_identity_after_cache_hit(self):
        self.assertIsNotNone(self.layer().layer)
        self.meta['calibration'] = dict(matrixCurrentReferenceToNative=[[1,0,5],[0,1,0],[0,0,1]])
        self.assertIsNone(self.layer().layer)

    def test_cached_resource_metadata_is_detached(self):
        first = self.layer().layer
        first['resourceIdentity']['binding']['floor'] = '-4'
        first['resourceIdentity']['sha256'] = 'incorrect'
        second = self.layer().layer
        self.assertEqual(second['resourceIdentity']['binding']['floor'], '-1')
        self.assertEqual(second['resourceIdentity']['sha256'], self.digest)

    def test_all_registered_modes_floors_and_bounded_eviction(self):
        original=self.path.read_bytes()
        for mode,mid in [('difficulty2','105001'),('difficult','106015'),('nightmare','107001')]:
            for floor in ['-1','-2','-3','-4']:
                world=5+int(floor);relative=f'assets/game-native/{mid}/{floor}.png'
                path=self.root/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(original)
                self.loader._inventory[relative]=dict(path=relative,bytes=len(original),sha256=self.digest)
                self.ctx._declared_files=self.ctx._declared_files|{path}
                self.ctx.mode=mode
                self.meta['binding'].update(mapId=mid,floor=floor)
                self.meta['canvas'].update(layout=mid,floor=floor,worldFloor=world,coordinateSpace=f'game-native-pixels:{mid}:{floor}')
                self.meta['sourceGeometry'].update(gameFloor=int(floor),sourceHeight=world)
                self.declaration.update(sourceLayout=mid,worldFloor=world,coordinateSpace=f'game-native-pixels:{mid}:{floor}')
                self.ctx.adapters.maps={mid:dict(floors={floor:self.declaration})}
                self.ctx.resources.entries={('shared-structure',mid,'variant-01',floor):self.entry}
                layer=self.loader.resolve(mid,floor,self.ref).layer
                self.assertIsNotNone(layer)
                self.assertEqual(layer['resourceIdentity']['binding']['floor'],floor)
                self.assertEqual(layer['resourceIdentity']['binding']['mapId'],mid)
                self.assertLessEqual(len(self.loader._cache),6)
        self.assertEqual(len(self.loader._cache),6)
        self.loader.max_bytes=self.pixels.nbytes*2
        for i in range(3):
            for row in (self.meta,self.entry,self.declaration):row['structureVersionKey']=f'updated-{i}'
            self.assertIsNotNone(self.loader.resolve(mid,floor,self.ref).layer)
            self.assertLessEqual(sum(v['pixels'].nbytes for v in self.loader._cache.values()),self.loader.max_bytes)

    def test_reference_arrays_unmodified_and_no_recognition_work(self):
        a=self.ref.clean_mask.copy();b=self.ref.validation_mask.copy()
        with patch('client_route_preferences._DisplaySelection.effective',return_value='shared-structure'), patch('client_route_preferences._DisplaySelection._base_author_status',return_value='missing'):
            layer=prepare_display_layer(self.engine,self.mid,self.floor,'shared-structure')
        self.assertEqual(layer['displayKind'],'game-native')
        np.testing.assert_array_equal(a,self.ref.clean_mask);np.testing.assert_array_equal(b,self.ref.validation_mask)

    def test_same_size_wrong_bitmap_cannot_borrow_binding(self):
        p=self.pixels.copy();p[:,:,:3]=120
        self.path.write_bytes(cv2.imencode('.png',p)[1].tobytes())
        self.assertIsNone(self.layer().layer)

    def test_changed_bytes_reject_after_cache_hit(self):
        self.assertIsNotNone(self.layer().layer)
        data=bytearray(self.path.read_bytes());data[-1]^=1;self.path.write_bytes(data)
        self.assertIsNone(self.layer().layer)

    def test_binding_fields_checked_even_after_cached_success(self):
        mutations=[lambda:self.meta['binding'].update(mapId='106014'),
            lambda:self.meta['binding'].update(floor='-2'),
            lambda:self.meta.update(sourceVersion='old'),
            lambda:self.meta.update(structureVersionKey='other'),
            lambda:self.meta['sourceGeometry'].pop('rendererImageSha256'),
            lambda:self.meta['sourceGeometry'].update(projectionApplied=True),
            lambda:self.meta['sourceGeometry'].update(gameFloor=-2),
            lambda:self.meta['canvas'].update(axes='x-left,y-down'),
            lambda:self.meta['canvas'].update(width=25),
            lambda:self.declaration.update(sourceVersion='old'),
            lambda:self.declaration.update(matrixCanonicalToLegacyRecognition=[[1,0,2],[0,1,0],[0,0,1]])]
        for change in mutations:
            with self.subTest(change=change):
                old_meta=copy.deepcopy(self.meta);old_decl=copy.deepcopy(self.declaration)
                self.assertIsNotNone(self.layer().layer);change();self.assertIsNone(self.layer().layer)
                self.meta.clear();self.meta.update(old_meta);self.declaration.clear();self.declaration.update(old_decl)

    def test_wrong_reference_path_rejected(self):
        self.ref.path=self.root/'another.npz';self.assertIsNone(self.layer().layer)

    def test_undeclared_image_does_not_display(self):
        self.ctx._declared_files=frozenset([self.ref_path]);self.assertIsNone(self.layer().layer)

    def test_missing_image_has_explicit_reason(self):
        self.loader._inventory.clear();r=self.layer();self.assertIsNone(r.layer)
        self.assertEqual(r.reason,'s1-native-display-not-installed')

    def test_wrong_mode_floor_and_map_cannot_reuse(self):
        self.assertIsNotNone(self.layer().layer)
        self.assertIsNone(self.loader.resolve('105001',self.floor,self.ref).layer)
        self.assertIsNone(self.loader.resolve(self.mid,'-2',self.ref).layer)
        self.ctx.mode='nightmare';self.assertIsNone(self.layer().layer)

    def test_decode_dimensions_must_match_typed_canvas(self):
        p=self.pixels[:8];self.path.write_bytes(cv2.imencode('.png',p)[1].tobytes());digest=hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.loader._inventory[self.image_rel].update(bytes=self.path.stat().st_size,sha256=digest)
        self.meta['sourceGeometry']['rendererImageSha256']=digest
        self.assertIsNone(self.layer().layer)

    def test_bgr_is_normalized_without_recoloring(self):
        p=self.pixels[:,:,:3];self.path.write_bytes(cv2.imencode('.png',p)[1].tobytes());digest=hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.loader._inventory[self.image_rel].update(bytes=self.path.stat().st_size,sha256=digest)
        self.meta['sourceGeometry']['rendererImageSha256']=digest
        actual=self.layer().layer['pixels'];np.testing.assert_array_equal(actual[:,:,:3],p)
        self.assertTrue(np.all(actual[:,:,3]==255))

    def test_transparent_empty_image_rejected(self):
        p=np.zeros_like(self.pixels);self.path.write_bytes(cv2.imencode('.png',p)[1].tobytes());digest=hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.loader._inventory[self.image_rel].update(bytes=self.path.stat().st_size,sha256=digest)
        self.meta['sourceGeometry']['rendererImageSha256']=digest
        self.assertIsNone(self.layer().layer)

    def test_byte_limit_does_not_decode(self):
        self.loader.max_bytes=8
        with patch('s1_native_display.cv2.imdecode',side_effect=AssertionError('must not decode')):
            self.assertEqual(self.layer().reason,'s1-native-display-byte-limit')

    def test_context_caches_are_separate(self):
        first=self.layer().layer['pixels'];other=S1NativeDisplay(self.ctx)
        second=other.resolve(self.mid,self.floor,self.ref).layer['pixels']
        self.assertIsNot(first,second);np.testing.assert_array_equal(first,second)

    def test_native_fallback_diagnostic_and_s0_not_routed(self):
        with patch('client_route_preferences._DisplaySelection.effective',return_value='shared-structure'), patch('client_route_preferences._DisplaySelection._base_author_status',return_value='missing'):
            self.path.unlink();r=prepare_display_layer(self.engine,self.mid,self.floor,'shared-structure')
            self.assertEqual(r['displayKind'],'shared-structure')
            self.assertEqual(r['fallbackReason'],'s1-native-display-invalid-binding-or-image')
            self.engine.qt_season='s0';self.ctx.rendered_display_layer=lambda *a: (_ for _ in ()).throw(AssertionError('S0 cannot consume S1'))
            self.assertEqual(prepare_display_layer(self.engine,self.mid,self.floor,'shared-structure')['displayKind'],'shared-structure')

    def test_custom_priority_and_missing_custom_failure_unchanged(self):
        marker=dict(pixels=self.pixels,matrixDisplayToReference=np.eye(3),routeProfileId='custom:x')
        self.engine.qt_custom_display=lambda *a:marker
        self.assertIs(prepare_display_layer(self.engine,self.mid,self.floor,'custom:x')['pixels'],self.pixels)
        self.engine.qt_custom_display=None
        with self.assertRaisesRegex(ValueError,'custom-display-unavailable'):
            prepare_display_layer(self.engine,self.mid,self.floor,'custom:x')

    def test_reprojection_uses_same_canonical_pose(self):
        H=np.array([[.5,0,15],[0,.5,33],[0,0,1.]])
        with patch('client_route_preferences._DisplaySelection.effective',return_value='shared-structure'), patch('client_route_preferences._DisplaySelection._base_author_status',return_value='missing'):
            out=prepare_route_reprojection(self.engine,self.mid,self.floor,'shared-structure',H)
        np.testing.assert_array_equal(out['matrixSourceToClient'],H)
        self.assertEqual(out['layer']['displayKind'],'game-native')

if __name__=='__main__':unittest.main()
