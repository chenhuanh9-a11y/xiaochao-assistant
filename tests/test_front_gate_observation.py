"""Raster counterexamples for automatic entrance gating, independent of map identity."""
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
import cv2
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'app'))
from front_gate_observation import FrontGatePrefilter, observe
from difficult_side_entry_identity import MapLocalEntryIdentity


def entrance(ports, *, brown_above=False):
    mask = np.zeros((400, 480), np.uint8)
    mask[140:261, 140:261] = 1
    mask[260:301, 170:231] = 1
    if 'left' in ports:mask[220:261, 80:141] = 1
    if 'right' in ports:mask[220:261, 260:321] = 1
    if 'top' in ports:mask[100:141, 180:221] = 1
    frame = np.full((400, 480, 3), 28, np.uint8)
    frame[mask>0] = 90
    edge = cv2.morphologyEx(mask, cv2.MORPH_GRADIENT, np.ones((3,3),np.uint8))
    frame[edge>0] = 140
    if brown_above:
        # A second room shares floor-colour pixels across an internal wall.
        # It has no connecting neck and is not a top entrance port.
        frame[100:140,180:221] = [80,100,120]
        mask[100:141,180:221] = 1
    return frame, dict(query=mask, observationBounds=(0,0,480,400))


def filter_for(mode='nightmare', extra_unknown=False):
    entries=[(dict(mapId=str(room),floor='1',entryRoomSource=dict(roomConfigId=room,rotation=1)),)
             for room in range(10201,10205)]
    ids=[str(room) for room in range(10201,10205)]
    if extra_unknown:ids.append('new-unknown-map')
    return FrontGatePrefilter(SimpleNamespace(mode=mode,maps=[SimpleNamespace(id=m) for m in ids]),entries)


class AutomaticGateTests(unittest.TestCase):
    def test_three_measured_ports_retain_only_three_gate_family(self):
        frame,context=entrance(('left','right','top'))
        allowed,report=filter_for().select(frame,'1',lambda _:context)
        self.assertEqual(report['states'],dict(left='open',right='open',top='open'))
        self.assertEqual(allowed,{'10201'})

    def test_real_wall_with_top_and_right_routes_selects_family(self):
        frame,context=entrance(('right','top'))
        allowed,report=filter_for().select(frame,'1',lambda _:context)
        self.assertEqual(report['states']['left'],'closed')
        self.assertEqual(allowed,{'10204'})

    def test_colour_behind_internal_top_wall_cannot_open_top_port(self):
        frame,context=entrance(('left','right'),brown_above=True)
        allowed,report=filter_for().select(frame,'1',lambda _:context)
        self.assertNotEqual(report['states']['top'],'open')
        self.assertTrue(allowed is None or '10202' in allowed)

    def test_missing_sharp_wall_is_unknown_not_closed(self):
        frame,context=entrance(('right','top'))
        context['observed']=np.zeros(context['query'].shape,bool)
        report=observe(frame,context)
        self.assertFalse(report['applied'])
        self.assertNotIn('closed',report['states'].values())

    def test_unknown_registered_resource_stays_in_competition(self):
        frame,context=entrance(('right','top'))
        allowed,_=filter_for(extra_unknown=True).select(frame,'1',lambda _:context)
        self.assertIn('new-unknown-map',allowed)

    def test_floor_and_mode_bypass_does_not_request_geometry(self):
        for mode,floor in (('difficult','1'),('nightmare','2'),('nightmare','B1')):
            def unexpected(_):self.fail('bypassed floor requested geometry')
            allowed,report=filter_for(mode).select(None,floor,unexpected)
            self.assertIsNone(allowed);self.assertFalse(report['applied'])

    def test_no_anchor_keeps_whole_catalog(self):
        frame=np.zeros((400,480,3),np.uint8)
        allowed,report=filter_for().select(frame,'1',lambda _:dict(query=np.zeros((400,480),np.uint8)))
        self.assertIsNone(allowed);self.assertFalse(report['applied'])

    def local_ranker(self, entries):
        identity=MapLocalEntryIdentity.__new__(MapLocalEntryIdentity)
        identity.floors={'1','2'};identity.entries=entries;identity.side_search={}
        identity.side_door_error=None;identity.side_search_error=None
        nodes=[dict(point=[i*40,40],rays=[[1,0],[0,1]]) for i in range(4)]
        # Only extraction is controlled. Exercise the actual rank wrapper and
        # inner per-map loop introduced by the side-entry implementation.
        identity._observe=lambda _:({},nodes,np.array([[0,1],[1,2],[2,3]]))
        return identity

    def test_gate_candidates_reach_inner_local_search_before_source_access(self):
        class ExcludedPairs:
            def __len__(self):raise AssertionError('Excluded map entered local search')
        entries=[(dict(mapId='excluded',floor='1'),None,None,ExcludedPairs()),
                 (dict(mapId='included',floor='1'),None,None,[])]
        rows,diagnostic=self.local_ranker(entries).rank(np.zeros((100,100,3),np.uint8),
            '1',candidate_map_ids={'included'})
        self.assertEqual(rows,[]);self.assertTrue(diagnostic['complete'])
        self.assertEqual(diagnostic['seedCorrespondences'],0)

    def test_no_gate_candidates_keeps_default_local_search_callable(self):
        for floor in ('1','2'):
            rows,diagnostic=self.local_ranker([(dict(mapId='m',floor=floor),None,None,[])]).rank(
                np.zeros((100,100,3),np.uint8),floor)
            self.assertEqual(rows,[]);self.assertTrue(diagnostic['complete'])

if __name__=='__main__':unittest.main()
