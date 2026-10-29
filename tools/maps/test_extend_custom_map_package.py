"""Incremental editor source bindings and static-road boundaries, synthetic only."""
import copy
import unittest

import numpy as np

from extend_custom_map_package import check_native_binding, topology_from_geometry


class IncrementalBindingTests(unittest.TestCase):
    def setUp(self):
        self.row = dict(runtimeMapId="new", floor="1", sourceLayout="104026", sourceWorldFloor=2)
        self.binding = dict(structureVersionKey="104026@hash/1", sourceVersion="new-version")
        self.metadata = dict(levelId="104026", floor=2, targetPixelsPerVoxel=60,
                             bounds=dict(minX=0, minY=-128), placements=[dict(roomIndex=0)])
        self.native = dict(binding=dict(mapId="new", floor="1", profile="zhan10-speed"),
            canvas=dict(layout="104026", worldFloor=2),
            sourceGeometry=dict(bounds=self.metadata["bounds"], matrixRendererPixelToProduct=np.eye(3).tolist()),
            **self.binding)

    def test_exact_binding_accepts_but_other_map_floor_or_source_rejects(self):
        check_native_binding(self.metadata, self.row, self.binding, self.native)
        for path, value in [(('binding','mapId'),'other'), (('binding','floor'),'2'),
                            (('canvas','worldFloor'),3), (('sourceVersion',),'old'),
                            (('structureVersionKey',),'old-hash')]:
            changed = copy.deepcopy(self.native)
            if len(path) == 2:
                changed[path[0]][path[1]] = value
            else:
                changed[path[0]] = value
            with self.subTest(path=path), self.assertRaises(ValueError):
                check_native_binding(self.metadata, self.row, self.binding, changed)

    def test_renderer_translation_cannot_be_hidden_by_matching_shape(self):
        changed = copy.deepcopy(self.native)
        changed['sourceGeometry']['matrixRendererPixelToProduct'][0][2] = 60
        with self.assertRaises(ValueError):
            check_native_binding(self.metadata, self.row, self.binding, changed)

    def test_static_floor_roads_do_not_infer_cross_floor_links(self):
        geometry = dict(mapId="104026", rooms=[dict(roomIndex=0, roomConfigId=12345,
            position=[0,2,0], rotation=0, worldVoxels=[[0,2,0],[1,2,0],[0,3,0]])], pairedDoors=[])
        topo, missing = topology_from_geometry(geometry, self.metadata, np.ones((60,120),bool),"new","1")
        self.assertEqual(topo['points'], [[30,30],[90,30]])
        self.assertEqual(topo['links'], [[0,1,'room-grid']])
        self.assertEqual(missing, [])
        self.assertNotIn('stairs', topo)

    def test_unrendered_rooms_are_not_added(self):
        geometry = dict(mapId="104026", rooms=[dict(roomIndex=9, roomConfigId=12345,
            position=[0,2,0], rotation=0, worldVoxels=[[0,2,0],[1,2,0]])], pairedDoors=[])
        with self.assertRaisesRegex(ValueError,'empty'):
            topology_from_geometry(geometry,self.metadata,np.ones((60,120),bool),'new','1')


if __name__ == '__main__':
    unittest.main()
