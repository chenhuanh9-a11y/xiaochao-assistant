"""Opt-in read-only 87-image consumer checks, not Windows/game acceptance.

Set XINGTU_S1_AUTHOR_TEST_ROOT to an isolated installation with the reviewed
package. No source data is synthesized or mutated by these tests.
"""
import hashlib
import os
from pathlib import Path
import sys
import unittest
import cv2
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'app'),str(ROOT/'experiments/qt_quick')]
from client_providers import build_engine_factories
from client_route_preferences import prepare_display_layer
from map_resource_context import load_browse_context
from s1_author_package import PROFILE_ID

@unittest.skipUnless(os.environ.get('XINGTU_S1_AUTHOR_TEST_ROOT'),'requires real private native/87-image installation')
class RealS1AuthorConsumerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root=Path(os.environ['XINGTU_S1_AUTHOR_TEST_ROOT']).resolve()/'_internal'
        cls.factory=build_engine_factories(cls.root)
        cls.engines={m:cls.factory._get(m,season='s1') for m in ('difficult','nightmare')}

    def test_all87_original_canvases_and_exact_display_bridges(self):
        count=0
        for mode,engine in self.engines.items():
            native=engine.qt_native_context
            self.assertIs(engine.qt_shared_provider.resources,native.resources)
            self.assertNotIn(PROFILE_ID,native.catalog.profiles)
            self.assertNotIn(PROFILE_ID,native.adapters.profiles)
            for item in engine.pack.maps:
                for floor in ('-1','-2','-3'):
                    with self.subTest(mode=mode,map=item.id,floor=floor):
                        layer=prepare_display_layer(engine,item.id,floor,PROFILE_ID)
                        binding=engine.qt_optional_displays.binding(PROFILE_ID,item.id,floor)
                        row=binding['declaration'];data=binding['path'].read_bytes()
                        source=cv2.imdecode(np.frombuffer(data,np.uint8),cv2.IMREAD_COLOR)
                        self.assertEqual(hashlib.sha256(data).hexdigest(),row['displaySha256'])
                        self.assertEqual(layer['routeProfileId'],PROFILE_ID)
                        self.assertEqual(layer['displayKind'],'author-route')
                        np.testing.assert_array_equal(layer['pixels'][:,:,:3],source)
                        np.testing.assert_array_equal(layer['matrixDisplayToReference'],row['matrixDisplayToReference'])
                        self.assertTrue(np.all(layer['pixels'][:,:,3]==255))
                        self.assertFalse(layer['pixels'].flags.writeable);count+=1
        self.assertEqual(count,87)

    def test_all29_b4_layers_use_actual_native_pixels(self):
        count=0
        for engine in self.engines.values():
            for item in engine.pack.maps:
                self.assertIsNone(engine.qt_optional_displays.binding(PROFILE_ID,item.id,'-4'))
                layer=prepare_display_layer(engine,item.id,'-4',PROFILE_ID)
                self.assertEqual(layer['routeProfileId'],'shared-structure')
                self.assertEqual(layer['displayKind'],'game-native');self.assertEqual(layer['authorStatus'],'missing')
                self.assertTrue(layer['fallback']);np.testing.assert_array_equal(layer['matrixDisplayToReference'],np.eye(3))
                image=self.root/f'seasons/s1/{engine.pack.mode}/assets/game-native/{item.id}/-4.png'
                native=cv2.imdecode(np.frombuffer(image.read_bytes(),np.uint8),cv2.IMREAD_UNCHANGED)
                np.testing.assert_array_equal(layer['pixels'],native);count+=1
        self.assertEqual(count,29)

    def test_native_author_native_switch_preserves_typed_identity_and_pixels(self):
        for engine in self.engines.values():
            mid=engine.pack.maps[0].id;floor='-2';before=engine.qt_shared_provider('shared-structure',mid,floor)
            digest=hashlib.sha256(before.clean_mask.tobytes()).hexdigest()
            first=prepare_display_layer(engine,mid,floor,'shared-structure')
            author=prepare_display_layer(engine,mid,floor,PROFILE_ID)
            last=prepare_display_layer(engine,mid,floor,'shared-structure')
            after=engine.qt_shared_provider('shared-structure',mid,floor)
            self.assertIs(before,after);self.assertEqual(digest,hashlib.sha256(after.clean_mask.tobytes()).hexdigest())
            self.assertEqual(first['displayKind'],'game-native');self.assertEqual(last['displayKind'],'game-native')
            np.testing.assert_array_equal(first['pixels'],last['pixels'])
            np.testing.assert_array_equal(first['matrixDisplayToReference'],last['matrixDisplayToReference'])
            self.assertEqual(author['routeProfileId'],PROFILE_ID)

    def test_browse_exact_coverage_no_b4_and_catalog_hash_validation(self):
        for mode,count in [('difficult',14),('nightmare',15)]:
            context=load_browse_context(self.root,mode,season='s1');self.assertFalse(context.error)
            profile=next(p for p in context.profiles if p.profile_id==PROFILE_ID)
            self.assertEqual((profile.available_layers,profile.missing_floors),(count*3,count))
            for item in profile.maps:self.assertEqual(set(item.variants[0].layers),{'source-height-4','source-height-3','source-height-2'})
            self.assertTrue(self.engines[mode].qt_catalog.validate(verify_hashes=True)['success'])

    def test_normal_mode_never_adopts_other_modes_author(self):
        engine=self.factory._get('difficulty2',season='s1')
        self.assertFalse(engine.qt_optional_displays.profiles);self.assertFalse(engine.qt_optional_displays.bindings)

if __name__=='__main__':unittest.main()
