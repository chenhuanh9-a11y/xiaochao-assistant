import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'app'))
from route_topology import Topology,for_mask,mask_hash,simplify,display_name
from custom_routes import Walkable,FloorRoute,CustomRouteStore,route_polyline,RoutePathError
from route_topology_pilot_data import PILOT

def fixture(links):
    return {'schema':'custom-route-topology-v1','points':[[10,10],[30,10],[50,10]],'pointRooms':['a','a','b'],'registration':{'pixelsPerTile':20},'links':links}

class TopologyTests(unittest.TestCase):
 def test_adjacent_footprint_rooms_do_not_connect_without_a_source_door(self):
    mask=np.ones((30,70),np.uint8);t=Topology(mask,fixture([[0,1,'room-grid']]))
    self.assertIsNone(t.path((10,10),(50,10)))
    t=Topology(mask,fixture([[0,1,'room-grid'],[1,2,'source-door']]))
    self.assertEqual(t.path((10,10),(50,10)),[(10,10),(50,10)])
 def test_unknown_transfer_and_straight_shortcut_across_void_are_rejected(self):
    mask=np.ones((30,70),np.uint8);mask[:,35:37]=0
    self.assertIsNone(Topology(mask,fixture([[0,1,'room-grid'],[1,2,'source-door']])).path((10,10),(50,10)))
    with self.assertRaises(ValueError):Topology(mask,fixture([[0,2,'unknown-transfer']]))
 def test_collinear_only_reduction_preserves_bends_diagonals_and_returns(self):
    self.assertEqual(simplify([(0,0),(10,0),(20,0),(20,10),(30,20),(20,10)]),[(0,0),(20,0),(20,10),(30,20),(20,10)])
 def test_free_waypoint_midway_along_a_real_diagonal_arm_is_retained(self):
    data={'points':[[10,10],[190,130]],'pointRooms':['y','y'],'registration':{'pixelsPerTile':60},'links':[[0,1,'source-diagonal']]}
    t=Topology(np.ones((160,220),np.uint8),data)
    self.assertTrue(t.contains((100,70)));self.assertEqual(t.snap((100,70)),(100,70));self.assertEqual(t.path((100,70),(190,130)),[(100,70),(190,130)])
 def test_exact_resource_binding_and_ambiguous_binding_fail_closed(self):
    mask=np.ones((30,70),np.uint8);data=fixture([[0,1,'room-grid']]);data.update(maskSha256=mask_hash(mask),shape=list(mask.shape))
    with patch('route_topology._catalog',return_value={'floors':[data]}):self.assertIsNotNone(for_mask(mask));mask[0,0]=0;self.assertIsNone(for_mask(mask))
    mask[0,0]=1
    with patch('route_topology._catalog',return_value={'floors':[data,data]}):self.assertIsNone(for_mask(mask))
 def test_manual_points_and_failed_route_semantics_unchanged(self):
    mask=np.ones((30,70),np.uint8);w=Walkable(mask);w.topology=Topology(mask,fixture([[0,1,'room-grid']]))
    route=FloorRoute(((10,10),(50,10)),snap=False);self.assertEqual(route_polyline(route,w),[[(10,10),(50,10)]])
    with self.assertRaises(RoutePathError) as raised:route_polyline(FloorRoute(route.points),w)
    self.assertEqual(raised.exception.reason,'no_path')
    with tempfile.TemporaryDirectory() as d:
        store=CustomRouteStore(Path(d)/'routes.json');store.set_route('nightmare-09','1',FloorRoute((),snap=False));self.assertFalse(CustomRouteStore(store.path).route('nightmare-09','1').snap)
 def test_portable_pilot_has_explicit_registration_and_no_crossfloor_edges(self):
    self.assertEqual(len(PILOT['floors']),2)
    for floor in PILOT['floors']:
        self.assertEqual(floor['sourceLayout'],'104001');self.assertEqual(floor['registration']['pixelsPerTile'],60)
        self.assertFalse(floor['transfers']);self.assertTrue(floor['doors']);self.assertEqual(set(d['liveState'] for d in floor['doors']),{'unknown'})
        self.assertFalse(any(k in json.dumps(floor) for k in ['E:/','C:/','E:\\','C:\\']))
    self.assertEqual(display_name('nightmare-09','old'),'一楼正门 · 二楼侧门 01');self.assertEqual(display_name('nightmare-01','old'),'地图 01');self.assertEqual(display_name('other','old'),'old')

if __name__=='__main__':unittest.main()
