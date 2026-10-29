"""Source contract failures must not silently manufacture editor connections."""
import copy
import unittest
from complete_incremental_editor_connections import checked_definitions, check_connector, find_point, local, world


class ConnectionBindingTests(unittest.TestCase):
    def test_changed_room_definition_rejects_exception_reuse(self):
        old={'roomDefinitions':{'data':{'10609':{'room_type':3,'room_voxels':[[0,0,0]]}}}}
        self.assertEqual(len(checked_definitions(old,copy.deepcopy(old))),64)
        changed=copy.deepcopy(old);changed['roomDefinitions']['data']['10609']['room_voxels'].append([1,0,0])
        with self.assertRaisesRegex(ValueError,'definition changed'): checked_definitions(old,changed)

    def test_cell_transform_is_invertible_for_each_rotation(self):
        for rotation in range(4):
            room={'rotation':rotation,'position':[8,2,-3]}
            for point in [[0,0,0],[1,1,-2],[-1,0,3]]:
                self.assertEqual(local(world(point,room),room),point)

    def test_patch_does_not_snap_to_a_nearby_or_other_room_point(self):
        topo={'points':[[30,30],[90,30]],'pointRooms':['r','other']}
        self.assertEqual(find_point(topo,[30,30],'r'),0)
        for point,room in [([30.1,30],'r'),([90,30],'r')]:
            with self.assertRaises(ValueError):find_point(topo,point,room)

    def test_stair_type_and_port_coordinates_are_bound_to_source(self):
        pair=[[0,0,0],[0,0,-1]]
        room={'roomConfigId':123,'position':[0,2,0],'rotation':0}
        catalog={'maps':{'104026':{'rooms':[room]}}}
        new={'roomDefinitions':{'data':{'123':{'room_type':4,'room_paths':[pair]}}}}
        connector={'layoutId':'104026','roomIndex':0,'roomConfigId':123,'roomType':4,
                   'ports':[{'slot':0,'localPair':pair,'worldInside':[0,2,0],
                             'worldOutside':[0,2,-1],'worldFloor':2,'floor':'1'}]}
        check_connector(connector,new,catalog)
        bad=copy.deepcopy(connector);bad['ports'][0]['worldInside'][0]=1
        with self.assertRaises(ValueError):check_connector(bad,new,catalog)
        bad=copy.deepcopy(new);bad['roomDefinitions']['data']['123']['room_type']=3
        with self.assertRaises(ValueError):check_connector(connector,bad,catalog)


if __name__=='__main__':unittest.main()
