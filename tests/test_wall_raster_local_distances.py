"""Local chamfer distances preserve the full raster acceptance contract."""
import unittest
from unittest.mock import patch
import cv2
import numpy as np
import single_map_structure as structure


def mixed_references():
    """Ten distinct source/channel fixtures, not ten aliases of one reference."""
    from pathlib import Path
    from shared_structure_reference import SharedStructureReference
    from test_marker_occluded_structure import asymmetric_structure
    base=asymmetric_structure()
    wrong=base.copy();wrong[228:339,276:327]=1;wrong[248:359,302:353]=0
    repeated=np.zeros((650,1400),np.uint8);repeated[:,:650]=base;repeated[:,750:]=base
    shifted=cv2.warpAffine(base,np.array([[1.,0,23],[0,1,17]]),(700,700))
    resized=cv2.resize(base,None,fx=1.2,fy=1.2,interpolation=cv2.INTER_NEAREST)
    partial_unknown=np.zeros_like(base);partial_unknown[:180]=1
    all_unknown=np.ones_like(base)
    references=[]
    for name,mask,validation,unknown in (
        ('correct',base,base,None),('wrong-hole',wrong,wrong,None),
        ('resized',resized,resized,None),('shifted-canvas',shifted,shifted,None),
        ('partial-unknown',base,base,partial_unknown),('wrong-validation',base,np.zeros_like(base),None),
        ('repeated',repeated,repeated,None),('mirror',np.fliplr(base).copy(),np.fliplr(base).copy(),None),
        ('all-unknown',base,base,all_unknown),('rotated',np.rot90(base).copy(),np.rot90(base).copy(),None)):
        boundary=cv2.morphologyEx(mask,cv2.MORPH_GRADIENT,np.ones((3,3),np.uint8)) if unknown is not None else None
        references.append((name,SharedStructureReference(mask,validation,{},Path('synthetic')/(name+'.npz'),
            known_boundary=boundary,unknown_mask=unknown)))
    return references


def normalized(value):
    if isinstance(value,np.ndarray):return value.tolist()
    if isinstance(value,np.generic):return value.item()
    if isinstance(value,dict):return {key:normalized(item) for key,item in value.items()
                                    if not key.endswith('Ms') and key != 'timings'}
    if isinstance(value,(tuple,list)):return [normalized(item) for item in value]
    return value


class LocalWallRasterTests(unittest.TestCase):
    def test_all_measured_pixels_keep_full_field_threshold_verdict(self):
        rng=np.random.default_rng(73218)
        for case in range(150):
            lines=[dict(axis=int(rng.integers(2)),sign=int(rng.choice((-1,1))),
                        normal=int(rng.integers(12,85)),lo=int(rng.integers(10,35)),
                        hi=int(rng.integers(36,90))) for _ in range(12)]
            samples=structure._wall_raster_samples(lines)
            shape=tuple((samples['upper']-samples['lower'])[::-1])
            supports=[rng.random(shape)<rng.choice((.003,.04,.2)) for _ in range(4)]
            unknown=rng.random(shape)<.15
            points=samples['local'];known=~unknown[points[:,1],points[:,0]]
            expected=np.full(len(points),-np.inf,np.float32)
            for direction,support in enumerate(supports):
                field=cv2.distanceTransform((~(support & ~unknown)).astype(np.uint8),cv2.DIST_L2,3)
                at=(samples['pointDirections']==direction)&known
                expected[at]=field[points[at,1],points[at,0]]
            actual=structure._wall_raster_local_verified(supports,unknown,samples)
            with self.subTest(case=case):
                self.assertEqual(actual,bool(known.any() and not np.any(expected>6)))

    def test_threshold_boundary_and_support_just_outside_halo(self):
        samples=structure._wall_raster_samples([dict(axis=0,sign=1,normal=20,lo=10,hi=30)])
        shape=tuple((samples['upper']-samples['lower'])[::-1])
        points=samples['local']
        for dx in range(9):
            supports=[np.zeros(shape,bool) for _ in range(4)]
            x=int(points[0,0])+dx
            if x<shape[1]:supports[0][:,x]=True
            expected=dx*float(cv2.distanceTransform(np.array([[0]+[1]*9],np.uint8),cv2.DIST_L2,3)[0,1])
            actual=structure._wall_raster_local_verified(supports,np.zeros(shape,bool),samples)
            if expected<=6:
                self.assertTrue(actual)
                full=cv2.distanceTransform((~supports[0]).astype(np.uint8),cv2.DIST_L2,3)
                self.assertTrue(np.all(full[points[:,1],points[:,0]]<=6))
            else:self.assertFalse(actual)

    def test_all_unknown_empty_source_and_unknown_source(self):
        samples=structure._wall_raster_samples([dict(axis=1,sign=-1,normal=20,lo=10,hi=30)])
        shape=tuple((samples['upper']-samples['lower'])[::-1])
        supports=[np.zeros(shape,bool) for _ in range(4)]
        self.assertFalse(structure._wall_raster_local_verified(supports,np.zeros(shape,bool),samples))
        values=structure._wall_raster_local_verified(supports,np.ones(shape,bool),samples)
        self.assertFalse(values)
        points=samples['local'];direction=samples['directions'][0]
        supports[direction][points[:,1]+1,points[:,0]]=True
        unknown=supports[direction].copy()
        self.assertFalse(structure._wall_raster_local_verified(supports,unknown,samples))

    def test_complete_public_diagnostics_and_internal_success_identical(self):
        reference=np.zeros((120,150),np.uint8);reference[15:105,17:133]=1
        lines=structure.visible_wall_segments(reference)
        for scale in (.55,.8,1.,1.2):
            for tx,ty in ((0,0),(2.2,-1.3),(18,27)):
                for unknown_kind in (None,'middle','all'):
                    unknown=None
                    if unknown_kind:
                        unknown=np.zeros_like(reference)
                        unknown[30:80,30:100]=1
                        if unknown_kind=='all':unknown[:]=1
                    model=np.array([scale,tx,ty])
                    complete=structure.initial_wall_raster_validation(lines,reference,model,source_unknown=unknown)
                    optimized=structure._wall_raster_validation(lines,reference,model,source_unknown=unknown,
                                                                _discard_rejected_details=True)
                    deferred=structure._wall_raster_validation(lines,reference,model,source_unknown=unknown,
                        _discard_rejected_details=True,_defer_verified_details=True)
                    self.assertEqual(deferred['verified'],complete['verified'])
                    self.assertEqual(optimized['verified'],complete['verified'])
                    if complete['verified']:self.assertEqual(optimized,complete)
                    # Public callers keep the detailed negative evidence.
                    self.assertIn('worstPoints',complete)

    def test_deferred_full_solver_matches_original_all_public_evidence(self):
        from types import SimpleNamespace
        from client_live_runtime import _AlignmentHost
        from fast_structure_alignment import prepare
        from test_marker_occluded_structure import asymmetric_structure,render
        frame,_=render(asymmetric_structure(),variant='corner-marker')
        bounds=(0,0,frame.shape[1],frame.shape[0])
        references=dict(mixed_references())
        original=structure._wall_raster_validation
        def full(*args,**kwargs):
            kwargs['_defer_verified_details']=False
            return original(*args,**kwargs)
        for mode in ('difficult','nightmare'):
            for indexed in (False,True):
                prepared={id(ref):prepare(ref.clean_mask) for ref in references.values()} if indexed else {}
                results=[]
                for deferred in (False,True):
                    host=_AlignmentHost(SimpleNamespace(pack=SimpleNamespace(mode=mode),
                        qt_adapters=SimpleNamespace(canonical_pack_id='synthetic'),
                        qt_shared_provider=lambda profile,map_id,floor:references[map_id]))
                    scan=host.prepare_structure_scan(frame,bounds)
                    with patch('fast_structure_alignment.load_prepared',side_effect=lambda ref, **kwargs:prepared.get(id(ref))), \
                         patch.object(structure,'_wall_raster_validation',side_effect=original if deferred else full):
                        results.append([normalized(scan(frame,name,'-3',lambda:False,observation_bounds=bounds))
                                        for name in references])
                for name,before,after in zip(references,*results):
                    with self.subTest(mode=mode,indexed=indexed,map_id=name):self.assertEqual(after,before)

    def test_diagonal_and_explicit_wall_channel_gate_matches_full(self):
        reference=np.zeros((140,170),np.uint8)
        cv2.fillPoly(reference,[np.array([[10,20],[125,12],[150,105],[22,120]])],1)
        wall=np.zeros_like(reference);wall[20:110,70:73]=1
        query=reference.copy();query[20:110,70:73]=0
        lines=structure.visible_wall_segments(query)
        for shift in (-6.5,-6,-.3,0,5.8,6,6.5):
            for unknown in (None,np.zeros_like(reference),wall):
                model=np.array([1.,shift,0])
                full=structure.initial_wall_raster_validation(lines,reference,model,
                    source_unknown=unknown,source_wall_mask=wall)
                deferred=structure._wall_raster_validation(lines,reference,model,
                    source_unknown=unknown,source_wall_mask=wall,
                    _discard_rejected_details=True,_defer_verified_details=True)
                self.assertEqual(deferred['verified'],full['verified'])

    def test_one_unexplained_pixel_is_not_sampled_away(self):
        lines=[dict(axis=0,sign=1,normal=20,lo=10,hi=2100)]
        samples=structure._wall_raster_samples(lines)
        shape=tuple((samples['upper']-samples['lower'])[::-1])
        points=samples['local'];supports=[np.zeros(shape,bool) for _ in range(4)]
        supports[0][points[:,1],points[:,0]]=True
        # A gap just wide enough to put its central pixel outside six pixels.
        supports[0][1000:1015]=False
        self.assertFalse(structure._wall_raster_local_verified(supports,np.zeros(shape,bool),samples))

    def test_tiny_and_boundary_clipped_windows_match_full_field(self):
        for height,width in ((1,1),(1,35),(35,1),(2,2),(17,29)):
            points=np.column_stack(np.nonzero(np.ones((height,width),bool)))[:,::-1]
            for direction in range(4):
                supports=[np.zeros((height,width),bool) for _ in range(4)]
                supports[direction][height//2,width//2]=True
                field=cv2.distanceTransform((~supports[direction]).astype(np.uint8),cv2.DIST_L2,3)
                samples=dict(fragmentWindows=[(points,direction)])
                self.assertEqual(structure._wall_raster_local_verified(supports,np.zeros((height,width),bool),samples),
                                 bool(np.all(field<=6)))

    def test_pose_index_and_budget_diagnostics_are_completed_and_cancellable(self):
        from test_marker_occluded_structure import asymmetric_structure,render
        source=asymmetric_structure();frame,truth=render(source,variant='corner-marker')
        context=structure.current_geometry(frame,observation_bounds=(0,0,1200,1000))
        original=structure._wall_raster_validation
        def full(*args,**kwargs):
            kwargs['_defer_verified_details']=False
            return original(*args,**kwargs)
        outputs=[]
        for deferred in (False,True):
            with patch.object(structure,'_wall_raster_validation',side_effect=original if deferred else full):
                outputs.append(structure.solve_initial_structure(frame,source,source,source,'shared-structure',
                    _context=dict(context),pose_index_candidates=[truth,truth+np.diag([0.,0.,0.])]))
        self.assertEqual(normalized(outputs[0]),normalized(outputs[1]))
        for row in outputs[1]['diagnostics']['poseIndexCandidates']['validated']:
            if 'wallRefinement' in row:self.assertIn('maxPixels',row['wallRefinement']['rasterValidation'])
        repeated=np.concatenate([source]*12,axis=1)
        cancelled=[False];count=[0]
        complete=structure.initial_wall_raster_validation
        def stop_after_first(*args,**kwargs):
            count[0]+=1;value=complete(*args,**kwargs);cancelled[0]=True;return value
        with patch.object(structure,'initial_wall_raster_validation',side_effect=stop_after_first):
            result=structure.solve_initial_structure(frame,repeated,repeated,repeated,'shared-structure',
                _context=dict(context),cancelled=lambda:cancelled[0])
        self.assertEqual(result['reason'],'structure-work-obsolete')
        self.assertEqual(count[0],1)


if __name__=='__main__':unittest.main()
