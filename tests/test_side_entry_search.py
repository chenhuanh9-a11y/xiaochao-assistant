"""Local priorities must not conceal a valid rival outside the neighbourhood."""
import copy
from pathlib import Path
import sys
import tempfile
import time
import unittest

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from side_entry_search import load_index, local_hypotheses, possible_turns, prepare_arrays, write_index


def graph(offset=(0,0)):
    points=3*np.asarray([[0,0],[60,0],[60,20],[100,20],[100,80],[40,80],[40,40],[0,40]],float)+[30,30]+offset
    rays=np.stack((np.roll(points,1,axis=0)-points,np.roll(points,-1,axis=0)-points),axis=1)
    rays/=np.linalg.norm(rays,axis=2)[:,:,None]
    pairs=np.column_stack((np.arange(len(points)),np.roll(np.arange(len(points)),-1)))
    return points,rays,pairs


class PreparedSearchTests(unittest.TestCase):
    def setUp(self):
        self.points,self.rays,self.pairs=graph()
        self.item=dict(mapId='map-a',floor='2',unit=60.,canvas=dict(width=1100,height=1000))
        self.door=dict(boundaryCenter=[30,30],structureVersionKey='a/current/2',sourceVersion='current')
        self.prepared=prepare_arrays(self.item,self.points,self.rays,self.pairs,self.door)

    def test_lower_bound_never_removes_exact_matches(self):
        rng=np.random.default_rng(47)
        q=.45*self.points+[400,270]
        qr=self.rays.copy()
        compatible=np.min(np.sum(qr[:,None]*self.rays[None],axis=3),axis=2)>=.95
        scales=np.r_[rng.uniform(.14,2.,250),.45]
        shifts=rng.uniform(-400,700,(len(scales),2));shifts[-1]=[400,270]
        upper=possible_turns(q,scales,shifts,compatible,self.prepared)
        exact=np.asarray([np.sum(np.any(compatible & (np.linalg.norm(q[:,None]-(s*self.points[None]+t),axis=2)<=3),axis=1))
                          for s,t in zip(scales,shifts)])
        self.assertTrue(np.all(upper>=exact))
        self.assertEqual(exact[-1],8)

    def test_rival_far_from_its_door_is_still_expanded(self):
        q=.5*self.points+[200,200]
        results=[]
        for offset in ((0,0),(600,300)):
            points,rays,pairs=graph(offset)
            prepared=prepare_arrays(self.item,points,rays,pairs,self.door)
            compatible=np.min(np.sum(self.rays[:,None]*rays[None],axis=3),axis=2)>=.95
            hypotheses,complete,counts=local_hypotheses(q,self.pairs,compatible,points,pairs,
                prepared,(1200,1600),60,time.perf_counter()+2)
            self.assertTrue(complete)
            self.assertTrue(any(h['chainCount']==8 for h in hypotheses))
            results.append(counts)
        self.assertGreater(sum(results[1]['ringEvaluations'][1:]),0)
        self.assertEqual(results[1]['localSeedTotal'],0)

    def test_deadline_cannot_report_complete_local_competition(self):
        compatible=np.min(np.sum(self.rays[:,None]*self.rays[None],axis=3),axis=2)>=.95
        rows,complete,_=local_hypotheses(self.points,self.pairs,compatible,self.points,self.pairs,
            self.prepared,(1200,1600),60,time.perf_counter()-1)
        self.assertFalse(complete)
        self.assertEqual(rows,[])

    def test_index_roundtrip_and_changed_contour_falls_back(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);entries=[(self.item,self.points,self.rays,self.pairs)]
            doors={'map-a':self.door}
            write_index(root/'side-entry-search.npz',entries,doors)
            index,error=load_index(root,entries,doors)
            self.assertIsNone(error)
            self.assertEqual(len(index),1)
            self.assertFalse(index['map-a','2']['lower'].flags.writeable)
            changed=self.points.copy();changed[0,0]+=1
            index,error=load_index(root,[(self.item,changed,self.rays,self.pairs)],doors)
            self.assertEqual(index,{})
            self.assertIn('contour mismatch',error)

    def test_missing_or_corrupt_optional_index_keeps_ordinary_search(self):
        entries=[(self.item,self.points,self.rays,self.pairs)]
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            self.assertEqual(load_index(root,entries,{'map-a':self.door})[0],{})
            (root/'side-entry-search.npz').write_bytes(b'not a numpy archive')
            self.assertEqual(load_index(root,entries,{'map-a':self.door})[0],{})

    def test_float_ray_jitter_shares_orientation_fields(self):
        points=np.tile(self.points,(4,1))+np.repeat(np.arange(4)*40,len(self.points))[:,None]
        rays=np.tile(self.rays,(4,1,1))
        rays[:,0,0]+=np.linspace(0,.001,len(rays))
        rays/=np.linalg.norm(rays,axis=2)[:,:,None]
        pairs=np.tile(self.pairs,(4,1))+np.repeat(np.arange(4)*len(self.points),len(self.pairs))[:,None]
        prepared=prepare_arrays(self.item,points,rays,pairs,self.door)
        self.assertLessEqual(len(prepared['lower']),len(self.prepared['lower']))


if __name__=='__main__':unittest.main()
