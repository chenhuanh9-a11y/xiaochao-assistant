"""Synthetic observations exercise directed compatibility, never game labels."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import contextlib
import io

import numpy as np

SPEC=importlib.util.spec_from_file_location('similarity_catalog_scan',
    Path(__file__).resolve().parents[1]/'tools/maps/scan_similarity_catalog.py')
scanner=importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name]=scanner
SPEC.loader.exec_module(scanner)


def fixture(name, *, extra=False, repeated=False, hole=False):
    mask=np.zeros((480,480),np.uint8);mask[60:181,60:181]=1
    points=[[60,60],[180,60],[180,180],[60,180]]
    if extra:
        mask[300:361,300:361]=1
        points += [[300,300],[360,300],[360,360],[300,360]]
    if repeated:
        mask[60:181,300:421]=1
        points += [[300,60],[420,60],[420,180],[300,180]]
    if hole:mask[110:131,110:131]=0
    points=np.asarray(points,float)
    rays=np.tile(np.asarray([[1.,0.],[0.,1.]]),(len(points),1,1))
    main=np.zeros(len(points),bool);main[:4]=True
    edges=np.asarray([(i,i//4*4+(i+1)%4) for i in range(len(points))],int)
    f=scanner.Features(points,rays,edges,main,np.asarray([120.,120.]),60.,
        dict(path='synthetic-features.json',sha256='synthetic',rowIndex=0),[])
    metadata=dict(sourceGeometry=dict(bounds=dict(minX=0,minY=0,maxX=1024,maxY=1024)))
    return scanner.Structure('synthetic',name,'1',mask,metadata,{},dict(front=f,side=f))


class SimilarityCatalogScanTests(unittest.TestCase):
    def test_whole_different_entry_local_same_is_discovered(self):
        a,b=fixture('a'),fixture('b',extra=True)
        self.assertGreater(scanner.whole_pair(a,b)['bOnly'],0)
        forward,fd=scanner.scan_direction(a,b,'front')
        backward,bd=scanner.scan_direction(b,a,'front')
        self.assertTrue(any(1 in row['radiiUnits'] for row in forward))
        self.assertTrue(any(1 in row['radiiUnits'] for row in backward))
        self.assertTrue(any(4 in row['radiiUnits'] for row in forward))
        self.assertFalse(any(4 in row['radiiUnits'] for row in backward))
        self.assertTrue(all(row['proposalOnly'] for row in forward+backward))

    def test_matching_corners_do_not_hide_missing_positive_footprint(self):
        a,b=fixture('a'),fixture('b',hole=True)
        records,status=scanner.scan_direction(a,b,'front')
        self.assertFalse(records)
        self.assertGreater(status['cornerProposalWindows'],0)
        self.assertEqual(status['status'],'NO_COMPATIBLE_UNDER_SCANNED_CONDITIONS')
        reverse,_=scanner.scan_direction(b,a,'front')
        self.assertTrue(reverse)

    def test_multiple_local_poses_are_retained_without_priority(self):
        a,b=fixture('a'),fixture('b',repeated=True)
        records,_=scanner.scan_direction(a,b,'side')
        anchor=[row for row in records if row['sourceAnchorIndex']==0 and 2 in row['radiiUnits']]
        translations=[row['translationCanonical'] for row in anchor]
        self.assertIn([0.,0.],translations)
        self.assertIn([-240.,0.],translations)
        self.assertTrue(all('priority' not in row for row in records))

    def test_no_valid_window_is_not_a_no_similarity_verdict(self):
        a,b=fixture('a'),fixture('b')
        a.features['side'].points=a.features['side'].points[:3]
        a.features['side'].rays=a.features['side'].rays[:3]
        a.features['side'].edges=np.asarray([[0,1],[1,2]])
        records,status=scanner.scan_direction(a,b,'side')
        self.assertFalse(records)
        self.assertEqual(status['status'],'NOT_ELIGIBLE')

    def test_legacy_world_bridge_is_unknown_not_zero_overlap(self):
        a,b=fixture('a'),fixture('b')
        b.metadata={}
        self.assertEqual(scanner.whole_pair(a,b)['status'],'UNKNOWN_NO_COMMON_WORLD_BRIDGE')

    def test_two_main_corners_remain_eligible_as_proposals(self):
        a,b=fixture('a'),fixture('b')
        a.features['front'].main[2:]=False
        b.features['front'].main[2:]=False
        records,_=scanner.scan_direction(a,b,'front')
        self.assertTrue(records)
        self.assertEqual(records[0]['targetMainCorners'],2)
        self.assertEqual(records[0]['targetNeighborCorners'],2)
        self.assertEqual(records[0]['runtimeFrontNeighborGate'],'NOT_EVALUATED_BY_STATIC_SURVEY')

    def test_nearby_distinct_poses_are_not_replaced_by_one_representative(self):
        a,b=fixture('a'),fixture('b')
        f=b.features['side']
        f.points=np.concatenate([f.points,f.points+[1,0]])
        f.rays=np.concatenate([f.rays,f.rays])
        f.main=np.concatenate([f.main,f.main])
        f.edges=np.concatenate([f.edges,f.edges+4])
        records,_=scanner.scan_direction(a,b,'side')
        translations=[r['translationCanonical'] for r in records if r['sourceAnchorIndex']==0]
        self.assertIn([0.,0.],translations)
        self.assertIn([-1.,0.],translations)

    def test_reused_target_corner_cannot_count_as_independent_support(self):
        a,b=fixture('a'),fixture('b')
        f=a.features['front']
        f.points=np.concatenate([f.points,f.points[:1]+[1,0]])
        f.rays=np.concatenate([f.rays,f.rays[:1]])
        f.main=np.r_[f.main,True]
        records,_=scanner.scan_direction(a,b,'front')
        self.assertFalse(records)

    def test_stable_samples_keep_existing_runtime_order(self):
        spec=importlib.util.spec_from_file_location('native_similarity_groups_for_sampling',
            Path(__file__).resolve().parents[1]/'app/native_similarity_groups.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        mask=np.zeros((121,121),np.uint8);mask[::2,3::3]=1
        samples=scanner.stable_samples(mask,1)
        existing=module.NativeSimilarityGroups._samples(mask)
        np.testing.assert_array_equal(np.asarray(samples['pointsXY']),existing[:,:2]-60)

    def test_subpixel_distance_upper_bound_is_not_a_negative_verdict(self):
        a,b=fixture('a'),fixture('b')
        a.mask[:]=0;b.mask[:]=0;a.mask[5,5]=1;b.mask[5,5]=1
        distance=scanner.cv2.distanceTransform(1-b.mask,scanner.cv2.DIST_L2,scanner.cv2.DIST_MASK_PRECISE)
        _,residual,_=scanner.positive_support(a,b,1.,1.,np.asarray([5.,5.]),
            np.asarray([2.6,0.]),1,distance)
        self.assertAlmostEqual(residual[0],2.6)

    def test_injective_matching_can_use_a_non_nearest_legal_target(self):
        a,b=fixture('a'),fixture('b')
        a.features['front'].points=np.asarray([[60,60],[62,60],[180,180],[60,180]],float)
        b.features['front'].points=np.asarray([[60,60],[65,60],[180,180],[60,180]],float)
        records,_=scanner.scan_direction(a,b,'front')
        exact=[r for r in records if r['translationCanonical']==[0.,0.]]
        self.assertTrue(exact)
        self.assertEqual(exact[0]['targetFeatureIds'],[0,1,2,3])

    def test_non_monotonic_proposal_coverage_is_not_a_distinguishing_claim(self):
        a,b=fixture('a'),fixture('b')
        conditions={(a.key,'front',-1,r):[dict(target=b.key,poseId=0)] for r in (4,8,12)}
        with tempfile.TemporaryDirectory() as temp:
            scanner.exploration_sequences(Path(temp),conditions,{a.key:a,b.key:b},{(a.key,'front'):[]})
            row=json.loads((Path(temp)/'exploration-sequences.jsonl').read_text())
        self.assertIsNone(row['firstStaticDistinguishingRadiusUnits'])
        self.assertEqual(row['status'],'UNKNOWN_NON_MONOTONIC_PROPOSAL_COVERAGE')

    def test_front_scan_enumerates_all_711_pairs_without_side_or_other_floors(self):
        structures=[];inventory=[]
        for mode,count,floors in [('difficult',30,['1','2']),('nightmare',24,['1','2','B1'])]:
            for i in range(count):
                for floor in floors:
                    # Empty synthetic feature corpus makes every direction
                    # explicitly unknown; this test targets enumeration only.
                    structures.append(scanner.Structure(mode,str(i),floor,np.zeros((1,1),np.uint8),{},{},{}))
                    inventory.append(dict(mode=mode,mapId=str(i),floor=floor,status='AVAILABLE'))
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(scanner,'load_catalog',return_value=(structures,inventory)), contextlib.redirect_stdout(io.StringIO()):
            summary=scanner.scan(Path(temp)/'source',Path(temp)/'output',selected_kind='front')
            records=[json.loads(line) for line in (Path(temp)/'output/pair-status.jsonl').read_text().splitlines()]
        self.assertEqual(summary['pairBuckets'],{'front':711})
        self.assertEqual(len(records),711)
        self.assertTrue(all(r['kind']=='front' and r['floor']=='1' for r in records))
        self.assertEqual(summary['structureCount'],132)
        self.assertEqual(summary['directedStatuses'],{'front:UNKNOWN_MISSING_FEATURES':1422})

    def test_post_entry_differences_keep_distant_structure_outside_entry_window(self):
        a,b=fixture('a',extra=True),fixture('b')
        matrix=np.array([[1,0,-120],[0,1,-120],[0,0,1]],float)
        entry_a=scanner.condition_raster(a,matrix,2)
        entry_b=scanner.condition_raster(b,matrix,2)
        self.assertFalse(np.any(entry_a&~entry_b))
        # This one-pixel fringe is within the original three-pixel tolerance.
        a.mask[100,181]=1
        result=scanner.post_entry_pair_differences(a,matrix,b,matrix)
        points=np.asarray(result['aOnly']['pointsXY'])
        self.assertEqual(result['differenceScope'],'post-entry-positive-structure')
        self.assertEqual(len(points),512)
        self.assertTrue(np.all(points[:,0]>120))
        self.assertTrue(np.all(points[:,1]>120))
        self.assertGreater(result['aOnly']['rawExclusivePositivePixels'],result['aOnly']['positivePixels'])
        self.assertEqual(result['bOnly']['positivePixels'],0)
        # Bounds cover the actual complete reference extent, not radius two.
        self.assertLessEqual(result['observationBounds'][0],-120)
        self.assertGreaterEqual(result['observationBounds'][2],359)

    def test_post_entry_distance_resolves_subpixel_bound_straddling(self):
        target=fixture('target');target.mask[:]=0;target.mask[5,5]=1
        points=np.asarray([[7.6,5.],[8.4,5.]])
        np.testing.assert_array_equal(scanner.positive_points_outside_tolerance(points,target,np.eye(3)),[False,True])

    def test_post_entry_classifies_original_source_pixels_before_warp_rounding(self):
        a,b=fixture('a'),fixture('b')
        a.mask=np.zeros((16,16),np.uint8);b.mask=np.zeros((16,16),np.uint8)
        a.mask[5,8]=1;b.mask[5,5]=1
        matrix=np.array([[1,0,-.5],[0,1,-.7173913],[0,0,1]])
        result=scanner.post_entry_pair_differences(a,matrix,b,matrix)
        self.assertEqual(result['aOnly']['positivePixels'],0)
        self.assertEqual(result['bOnly']['positivePixels'],0)
        # A true four-pixel difference must survive the same fractional origin.
        a.mask[:]=0;b.mask[:]=0;a.mask[5,10]=1;b.mask[5,6]=1
        result=scanner.post_entry_pair_differences(a,matrix,b,matrix)
        self.assertGreater(result['aOnly']['positivePixels'],0)
        self.assertGreater(result['bOnly']['positivePixels'],0)


if __name__=='__main__':unittest.main()
