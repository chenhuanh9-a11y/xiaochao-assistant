import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'app'))
from route_map_display import load_background,_cache
from route_topology import mask_hash
from custom_routes import render_layer,FloorRoute,Walkable
from custom_route_editor import _map_picture

class NormalRenderTests(unittest.TestCase):
 def fixture(self,root):
    mask=np.ones((40,50),np.uint8);p=Path(root)/'assets/route-render/pilot.png';p.parent.mkdir(parents=True,exist_ok=True)
    image=Image.new('RGBA',(50,40),(83,47,29,255));image.putpixel((2,2),(17,126,61,255));image.save(p)
    entry={'maskSha256':mask_hash(mask),'shape':list(mask.shape),'imagePath':'assets/route-render/pilot.png','imageSha256':hashlib.sha256(p.read_bytes()).hexdigest(),'sourceKind':'source-rendered-no-author-overlays'}
    return mask,p,entry
 def test_normal_preview_uses_true_source_pixels_not_mask_recolouring(self):
    with tempfile.TemporaryDirectory() as root:
        mask,p,e=self.fixture(root);before=mask.copy()
        with patch('route_map_display._entries',return_value=[e]):
            bg=load_background(mask,asset_root=root);self.assertIsNotNone(bg.image)
            self.assertEqual(_map_picture(mask,(50,40),bg).getpixel((2,2)),(17,126,61))
            image=render_layer(mask,FloorRoute(),Walkable(mask),background=bg);self.assertEqual(tuple(image[2,2,:3]),(61,126,17));self.assertTrue(np.array_equal(mask,before))
 def test_missing_changed_or_unpaired_render_is_explicit_fallback_and_invalidates_cache(self):
    with tempfile.TemporaryDirectory() as root:
        mask,p,e=self.fixture(root)
        with patch('route_map_display._entries',return_value=[e]):
            bg=load_background(mask,asset_root=root);self.assertIsNotNone(bg.image)
            Image.new('RGBA',(50,40),(2,3,4,255)).save(p)
            changed=load_background(mask,asset_root=root);self.assertIsNone(changed.image);self.assertNotEqual(changed.signature,bg.signature)
            p.unlink();missing=load_background(mask,asset_root=root);self.assertIsNone(missing.image);self.assertNotEqual(missing.signature,changed.signature)
            different=mask.copy();different[0,0]=0;self.assertIsNone(load_background(different,asset_root=root).image)
 def test_path_escape_and_author_image_classification_are_not_admitted(self):
    mask=np.ones((40,50),np.uint8)
    for path,kind in [('../other.png','source-rendered-no-author-overlays'),('author.png','author-route')]:
        e={'maskSha256':mask_hash(mask),'shape':list(mask.shape),'imagePath':path,'sourceKind':kind}
        with patch('route_map_display._entries',return_value=[e]):self.assertIsNone(load_background(mask).image)
if __name__=='__main__':unittest.main()
