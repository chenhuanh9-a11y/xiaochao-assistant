import importlib.util
from pathlib import Path
import sys
import unittest
import numpy as np
from PIL import Image

TOOLS=Path(__file__).resolve().parents[1]/'tools/maps'
if not TOOLS.exists():TOOLS=Path(__file__).resolve().parent
sys.path.insert(0,str(TOOLS))
from render_editor_doors import source_frames,native_frame,image_point,composite_frames,contained,checked_geometry

class DoorDisplayTests(unittest.TestCase):
    def setUp(self):
        self.defs={'1':{'room_type':2,'room_paths':[[[0,0,0],[1,0,0]]]},
                   '2':{'room_type':0,'room_paths':[[[0,0,0],[-1,0,0]]]}}
    def test_reciprocal_entry_is_one_frame(self):
        frames=source_frames({'room_data':[[1,[0,2,0],0],[2,[1,2,0],0]],'door':{}},self.defs)
        self.assertEqual(len(frames),1);self.assertEqual(frames[0]['kind'],'entry-room-connection')
    def test_unpaired_port_is_not_invented_door(self):
        self.assertEqual(source_frames({'room_data':[[1,[0,2,0],0]],'door':{}},self.defs),[])
    def test_explicit_slot_is_external_not_spawn_marker(self):
        frames=source_frames({'room_data':[[1,[0,3,0],0]],'door':{'0':[0,0]}},self.defs)
        self.assertEqual(frames[0]['kind'],'explicit-external-door')
        self.assertEqual(frames[0]['worldVoxels'],[(0,3,0),(1,3,0)])
    def test_world_to_native_and_inverse_display(self):
        frame=native_frame({'worldVoxels':[(0,2,0),(1,2,0)]},{'minX':0,'minY':-128})
        self.assertEqual(frame['nativeCenter'],[60,30]);self.assertEqual(frame['rotationDegrees'],90)
        self.assertEqual(image_point(frame['nativeCenter'],np.array([[.5,0,5],[0,.5,7],[0,0,1]])),[35,22])
    def test_overlay_only_changes_small_region_and_not_input(self):
        original=Image.new('RGBA',(200,200),(30,40,50,255)); before=np.asarray(original).copy()
        sprite=Image.new('RGBA',(128,128),(255,255,255,255))
        result=composite_frames(original,[{'nativeCenter':[100,100],'rotationDegrees':90}],sprite,np.eye(3))
        after=np.asarray(result);changed=np.any(before!=after,axis=2)
        self.assertTrue(changed.any());self.assertLessEqual(int(changed.sum()),3600)
        self.assertTrue(np.array_equal(np.asarray(original),before));self.assertFalse(changed[:60].any())
    def test_path_escape_rejected(self):
        with self.assertRaises(ValueError):contained(Path.cwd(),'../escape.webp')
    def test_native_world_and_identity_mismatch_rejected(self):
        binding={'mapId':'m','floor':'1','structureVersionKey':'k','sourceVersion':'v','baseSnapshot':'v'}
        meta=dict(binding=binding.copy(),canvas={'worldFloor':2,'floor':'1','layout':'100'},sourceGeometry={'layout':'100','worldFloor':2,'targetPixelsPerVoxel':60,'matrixRendererPixelToProduct':np.eye(3).tolist()},**{k:binding[k] for k in ('structureVersionKey','sourceVersion','baseSnapshot')})
        checked_geometry(meta,binding,'m','1')
        meta['sourceGeometry']['worldFloor']=3
        with self.assertRaises(ValueError):checked_geometry(meta,binding,'m','1')
        meta['sourceGeometry']['worldFloor']=2;meta['binding']['mapId']='wrong'
        with self.assertRaises(ValueError):checked_geometry(meta,binding,'m','1')

if __name__=='__main__':unittest.main()
