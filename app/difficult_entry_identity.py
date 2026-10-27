"""Read-only entry hypotheses. The existing identity/pose consumers own state."""
import json,time
from pathlib import Path
import numpy as np
import cv2


def entry_geometry(frame):
    """Difficult entry at bottom of map was outside legacy 22–80% crop.

    Keep this observation local to entry retrieval; nightmare extraction and
    all final display validators retain their existing behavior.
    """
    from identityv_daily_assistant import map_reuse_evidence
    h,w=frame.shape[:2];gray=cv2.cvtColor(frame[:,:,:3],cv2.COLOR_BGR2GRAY)
    search=np.zeros((h,w),np.uint8)
    y0,y1=int(h*.14),int(h*.90);x0,x1=int(w*.16),int(w*.86)
    search[y0:y1,x0:x1]=(gray[y0:y1,x0:x1]>=75).astype(np.uint8)
    count,labels,stats,centers=cv2.connectedComponentsWithStats(search,8)
    valid=[i for i in range(1,count) if stats[i,4]>=1000]
    if not valid:return None
    i=max(valid,key=lambda i:stats[i,4]);x,y,ww,hh,_=map(int,stats[i])
    bounds=(max(x0,x-8),max(y0,y-8),min(x1,x+ww+8),min(y1,y+hh+8))
    x,y,r,b=bounds
    visible=(frame[y:b,x:r],bounds,dict(area=int(stats[i,4]),center=centers[i],width=ww,height=hh))
    current=map_reuse_evidence(frame,maximum_width=w,visible_override=visible)['translationMask']
    return dict(query=(current[0]>.22).astype(np.uint8),observationBounds=bounds) if current is not None else None


def entry_corners(frame,context):
    from single_map_structure import current_part
    observed=current_part(frame,context,'observed');rows=[]
    # contour_graph simplifies with a 2px Douglas-Peucker tolerance. After
    # resampling, its segment can be two pixels from the measured raster wall.
    # This tube only retrieves candidates; final validation uses the original
    # unsimplified observation, not this expanded support mask.
    supported=cv2.dilate(observed.astype(np.uint8),np.ones((5,5),np.uint8))
    for node in current_part(frame,context,'nodes'):
        if not node['valid']:continue
        p=node['point'];ok=True
        for ray,length in zip(node['rays'],node['lengths']):
            samples=np.rint(p+np.arange(2,min(12,int(length)))[:,None]*ray).astype(int)
            if not len(samples) or not supported[samples[:,1],samples[:,0]].all():ok=False
        if ok:rows.append(node)
    return rows


class MapEntryGeometryIdentity:
    def __init__(self,root,*,structure_store=None,route_loader=None,shared_reference_provider=None,profile_id='zhan10-speed',profile_for_map=None):
        self.profile_id=profile_id
        self.profile_for_map=profile_for_map or (lambda map_id: self.profile_id)
        self.root=Path(root);self.entries=[];self.error=None
        self._observation=None
        self._wall_lines={}
        self.route_loader=route_loader
        self.shared_reference_provider=shared_reference_provider
        from author_structure_resources import AuthorStructureResourceStore
        self.structures=(structure_store if structure_store is not None else
            AuthorStructureResourceStore(self.root/'index.json',self.root))
        try:
            payload=json.loads((self.root/'entries.json').read_text(encoding='utf8'))
            if payload['schemaVersion']!=1:raise ValueError('unsupported entry index')
            for item in payload['entries']:
                points=np.array([c['points'] for c in item['chains']],float)
                rays=np.array([c['rays'] for c in item['chains']],float)
                if points.shape!=(len(points),3,2) or not np.isfinite(points).all():raise ValueError('invalid entry chains')
                if rays.shape!=(len(points),2,2) or not np.isfinite(rays).all():raise ValueError('invalid entry rays')
                outline=np.array(item['outline'],float)
                main=np.linalg.norm(points[:,1,None]-outline[None],axis=2).min(axis=1)<=3
                self.entries.append((item,points,rays,main))
        except (OSError,KeyError,ValueError,TypeError) as error:
            self.error=str(error);self.entries=[]

    def geometry(self,frame):
        """Reuse only preprocessing for one byte-identical captured image.

        A private pixel copy prevents in-place reuse of a capture buffer from
        reusing stale geometry. No identity, pose or validation verdict lives
        here; every consumer still performs its current author validation.
        """
        cached=self._observation
        if cached is not None and np.array_equal(cached[0],frame):
            return cached[1]
        context=entry_geometry(frame)
        self._observation=(frame.copy(),context)
        return context

    def native_wall_lines(self,product,map_id,floor):
        """Static known walls shared by entry and cross-floor refinement."""
        if product.known_boundary is None or product.unknown_mask is None:
            # Legacy products have no measured-wall channel. Let the existing
            # refiner derive contour proposals; do not invent known evidence.
            return None
        from single_map_structure import explicit_wall_segments
        key=(map_id,str(floor));cached=self._wall_lines.get(key)
        if cached is None or cached[0] is not product:
            lines=product.native_wall_segments
            if lines is None:
                known=(cv2.dilate(product.known_boundary.astype(np.uint8),np.ones((3,3),np.uint8))>0)&(product.unknown_mask==0)
                lines=explicit_wall_segments(product.clean_mask,product.known_boundary,known)
            cached=(product,lines)
            self._wall_lines[key]=cached
        return cached[1]

    def author_visible(self,frame,*,context=None):
        """Reuse the original author-validator crop, never its verdict."""
        from nightmare_candidate import visible_map_component
        if context is None:
            cached=self._observation
            if cached is not None and np.array_equal(cached[0],frame):context=cached[1]
        if context is None:return visible_map_component(frame)
        if 'authorVisible' not in context:
            visible=visible_map_component(frame)
            # Capture buffers may be reused in place. Retain crop pixels,
            # not a view that can silently change under the stored context.
            context['authorVisible']=(visible[0].copy(),visible[1],visible[2]) if visible is not None else None
        return context['authorVisible']

    def rank(self,frame,floor,*,budget_seconds=None,map_id=None,route=None):
        started=time.perf_counter()
        if not any(item['floor']==str(floor) for item,*_ in self.entries):
            return [],dict(reason='entry-floor-not-indexed',complete=True,elapsedMs=0.)
        if budget_seconds is not None and budget_seconds<=0:
            return [],dict(reason='entry-budget-incomplete',complete=False,elapsedMs=0.)
        context=self.geometry(frame)
        query=entry_corners(frame,context) if context else []
        rows=[];budget=1.0 if budget_seconds is None else max(0.,float(budget_seconds))
        complete=True
        if len(query)<3:return [],dict(reason='entry-visible-structure-insufficient',elapsedMs=(time.perf_counter()-started)*1000)
        qpoints=np.array([q['point'] for q in query]);qrays=np.array([q['rays'] for q in query])
        for item,source,rays,main in self.entries:
            if item['floor']!=str(floor):continue
            if map_id is not None and item['mapId']!=map_id:continue
            if time.perf_counter()-started>budget:complete=False;break
            source=source[:,1];hypotheses=[]
            oriented=np.min(np.sum(qrays[:,None]*rays[None],axis=3),axis=2)>=.95
            oriented_pairs=np.nonzero(oriented)
            main_ids=np.flatnonzero(main)
            # Two entry corners seed scale; different measured corners and
            # neighboring rooms must support it. No complete wall endpoint
            # is inferred from a fog-clipped contour.
            for ai,si in enumerate(main_ids):
                for sj in main_ids[ai+1:]:
                    delta=source[sj]-source[si];denom=float(delta@delta)
                    if denom<(.7*item['unit'])**2:continue
                    qa,qb=np.meshgrid(np.flatnonzero(oriented[:,si]),np.flatnonzero(oriented[:,sj]))
                    qa=qa.ravel();qb=qb.ravel()
                    qdelta=qpoints[qb]-qpoints[qa]
                    scales=qdelta@delta/denom
                    errors=np.linalg.norm(qdelta-scales[:,None]*delta,axis=1)
                    compatible=(errors<=2)&(scales*item['unit']>=8)&(scales*item['unit']<=min(frame.shape[:2])/4)
                    for pair_index in np.flatnonzero(compatible):
                        qi=qa[pair_index];s=float(scales[pair_index]);t=qpoints[qi]-s*source[si]
                        self._score_hypothesis(item,source,main,qpoints,oriented,s,t,hypotheses,oriented_pairs)
            if hypotheses:
                hypotheses.sort(key=lambda h:-h['chainCount'])
                # Updating an existing hypothesis can move it beside another
                # previously distinct pose. Apply the same metric once more
                # to the final parameters before deriving pose ambiguity.
                distinct=[]
                for hypothesis in hypotheses:
                    if not any(np.linalg.norm(hypothesis['anchor']-old['anchor'])
                        + abs(hypothesis['scale']-old['scale'])*item['unit']*12<=3 for old in distinct):
                        distinct.append(hypothesis)
                hypotheses[:]=distinct
                best=hypotheses[0]
                rows.append(dict(mapId=item['mapId'],profileId=self.profile_for_map(item['mapId']),entryKind='main',material=item['material'],
                    score=float(best['chainCount']),hypothesis={k:v for k,v in best.items() if k!='anchor'},
                    poseAmbiguous=len(hypotheses)>1 and hypotheses[1]['chainCount']>=best['chainCount']-1,
                    sourceProduct=item['sourceProduct']))
        rows.sort(key=lambda r:-r['score'])
        # Verify only the small, competitive set. Budget/incomplete competition
        # never grants identity. Entry-family repetition remains ambiguous.
        competitive=[r for r in rows if r['score']>=max(10,rows[0]['score']*.65)] if rows else []
        if complete and 0<len(competitive)<=3:
            for row in competitive:
                if time.perf_counter()-started>budget:complete=False;break
                try:
                    row['verification']=self.verify(frame,row,context=context,route=route)
                except (ValueError,TypeError,KeyError,OSError,cv2.error,StopIteration) as error:
                    row['verification']=dict(success=False,reason='entry-resource-unavailable',error=str(error))
        accepted=[r for r in competitive if r.get('verification',{}).get('success')]
        winner=accepted[0] if complete and len(accepted)==1 else None
        for row in rows:
            row['poseVerified']=bool(row is winner and not row['poseAmbiguous']
                and row['hypothesis']['neighborChains']>=6 and row['score']>=10
                and (row['hypothesis']['mainChains']>=3 or (
                    row['hypothesis']['neighborChains']>=8
                    and min(row['hypothesis']['neighborExtentGrid'])>=4)))
            row['identityVerified']=row['poseVerified'] and map_id is None
        if winner is not None:rows.sort(key=lambda r:(not r['identityVerified'],-r['score']))
        return rows,dict(reason=('entry-known-identity-pose-verified' if map_id is not None and any(r['poseVerified'] for r in rows)
                         else 'entry-identity-verified' if any(r['identityVerified'] for r in rows)
                         else 'entry-neighborhood-candidates' if complete else 'entry-budget-incomplete'),
                         complete=complete,queryChains=len(query),elapsedMs=(time.perf_counter()-started)*1000)

    def verify(self,frame,row,*,context=None,route=None):
        from single_map_structure import regional_validation,_static_validation_context,partial_wall_geometry
        from nightmare_candidate import imread_unicode,selected_route_current_frame_residual
        item=next(e for e,*_ in self.entries if e['mapId']==row['mapId'])
        profile=self.profile_for_map(item['mapId'])
        shared_reference = None
        if self.shared_reference_provider is not None:
            try:
                shared_reference = self.shared_reference_provider(profile, item['mapId'], item['floor'])
            except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                shared_reference = None
            product = shared_reference
            route = None
        else:
            product=self.structures.load(profile,item['mapId'],item['floor'])
        if product is None:return dict(success=False,reason='entry-resource-unavailable')
        h=row['hypothesis'];s=h['scale'];x,y=h['translation']
        matrix=np.array([[s,0,x],[0,s,y],[0,0,1.]])
        initial_matrix=matrix.copy()
        context=self.geometry(frame) if context is None else context
        if context is None:return dict(success=False,reason='entry-observation-unavailable')
        projected=cv2.warpAffine(product.clean_mask,matrix[:2],frame.shape[1::-1],flags=cv2.INTER_NEAREST)
        regional=regional_validation(frame,context['query'],projected,
            _context=_static_validation_context(frame,context,matrix,product.unknown_mask))
        wall_refinement=None
        if regional['regionalEdgeVerified']:
            refined=partial_wall_geometry(frame,product.clean_mask,matrix,_context=context,
                source_known_boundary=product.known_boundary,source_unknown=product.unknown_mask,
                source_wall_mask=product.known_boundary,
                _initial_projection=projected,_initial_regions=regional,
                _source_lines=self.native_wall_lines(product,item['mapId'],item['floor']))
            wall_refinement=dict(success=refined['success'],reason=refined['reason'],
                refinement=refined['diagnostics'].get('refinement'),
                scaleRefinement=refined['diagnostics'].get('scaleRefinement'))
            if refined['success']:matrix=refined['matrix']
        # Wrong candidates often fail regional geometry. Decode an author image
        # only after that test, and reuse the worker's actual selected image.
        if regional['regionalEdgeVerified'] and route is None and shared_reference is None and self.shared_reference_provider is None:
            route=(self.route_loader(profile,item['mapId'],item['floor'])
                if self.route_loader is not None else imread_unicode(self.root/item['routeResource'],-1))
        if regional['regionalEdgeVerified'] and route is None and shared_reference is None:
            return dict(success=False,reason='entry-shared-structure-unavailable')
        if regional['regionalEdgeVerified'] and route is not None and route.shape[:2]!=product.clean_mask.shape:
            return dict(success=False,reason='entry-resource-unavailable')
        visible=self.author_visible(frame,context=context) if regional['regionalEdgeVerified'] else None
        residual=selected_route_current_frame_residual(frame,route,matrix,maximum_dimension=960,visible_override=visible,
            route_profile_id=profile,
            reference_override=(None if shared_reference is not None else product.validation_mask),
            shared_reference=shared_reference) if regional['regionalEdgeVerified'] else None
        if (residual and not residual.get('verified') and wall_refinement
                and (wall_refinement.get('scaleRefinement') or {}).get('accepted')):
            # A scale proposal cannot replace an otherwise valid old result.
            # Recover the original translation estimator, then validate that
            # actual author pose as usual; never reuse the rejected verdict.
            fallback=partial_wall_geometry(frame,product.clean_mask,initial_matrix,_context=context,
                source_known_boundary=product.known_boundary,source_unknown=product.unknown_mask,
                source_wall_mask=product.known_boundary,
                _initial_projection=projected,_initial_regions=regional)
            matrix=fallback['matrix'] if fallback['success'] else initial_matrix
            wall_refinement['scaleRefinement']['authorRejected']=True
            wall_refinement['scaleRefinement']['accepted']=False
            wall_refinement['fallbackReason']=fallback['reason']
            wall_refinement['success']=bool(fallback['success'])
            residual=selected_route_current_frame_residual(frame,route,matrix,maximum_dimension=960,visible_override=visible,
                route_profile_id=profile,
                reference_override=(None if shared_reference is not None else product.validation_mask),
                shared_reference=shared_reference)
        from single_map_structure import current_part,initial_wall_raster_validation
        wall_check=initial_wall_raster_validation(current_part(frame,context,'lines'),
            product.clean_mask,np.r_[matrix[0,0],matrix[:2,2]],
            source_unknown=product.unknown_mask,source_wall_mask=product.known_boundary)
        return dict(success=bool(regional['regionalEdgeVerified'] and wall_refinement
            and wall_refinement['success'] and residual and residual.get('verified') and wall_check['verified']),
            reason='entry-current-frame-validation',regions=regional,residual=residual,
            wallRefinement=wall_refinement,entryWalls=wall_check,matrixSourceToClient=matrix.tolist())

    @staticmethod
    def _score_hypothesis(item,source,main,qpoints,oriented,s,t,hypotheses,oriented_pairs=None):
        anchor=np.array(item['center'])*s+t
        if any(np.linalg.norm(anchor-h['anchor'])<3 and abs(s-h['scale'])*item['unit']<.25 for h in hypotheses):return
        # Only measured corners contribute. Fog is never a missing-room vote.
        qi,si=np.nonzero(oriented) if oriented_pairs is None else oriented_pairs
        for iteration in range(3):
            # Direction-incompatible pairs were always discarded. Compute the
            # same distances only for compatible pairs, in np.argwhere order.
            errors=np.linalg.norm(qpoints[qi]-(source*s+t)[si],axis=1)
            hit=errors<=3
            qhit,shit=qi[hit],si[hit]
            unique=(np.bincount(qhit,minlength=len(qpoints))[qhit]==1)&(np.bincount(shit,minlength=len(source))[shit]==1)
            pairs=np.column_stack((qhit[unique],shit[unique]))
            if len(pairs)<4:return
            main_count=int(main[pairs[:,1]].sum())
            # Two measured entry corners solve the axis-aligned similarity;
            # remaining visible neighborhood, never inferred fog corners,
            # supplies independent support. Partial entries require a wider
            # two-dimensional neighborhood at the identity decision above.
            if main_count<2:return
            a=source[pairs[:,1]];b=qpoints[pairs[:,0]];centered=a-a.mean(axis=0)
            proposed_s=float(np.sum(centered*(b-b.mean(axis=0)))/np.sum(centered*centered))
            proposed_t=b.mean(axis=0)-proposed_s*a.mean(axis=0)
            # The final pairs were measured at the current supported pose.
            # LS can reduce RMS while moving one counted pair past 3 px;
            # reject that refinement rather than retain unsupported score.
            if iteration<2 or np.all(np.linalg.norm(b-(a*proposed_s+proposed_t),axis=1)<=3):
                s,t=proposed_s,proposed_t
        refined=dict(scale=s,translation=t.tolist(),anchor=np.array(item['center'])*s+t,chainCount=len(pairs),
            mainChains=main_count,neighborChains=len(pairs)-main_count,pairs=pairs.tolist(),
            neighborExtentGrid=(np.ptp(source[pairs[~main[pairs[:,1]],1]],axis=0)/item['unit']).tolist()
                if len(pairs)>main_count else [0.,0.])
        for old in hypotheses:
            if np.linalg.norm(refined['anchor']-old['anchor'])+abs(s-old['scale'])*item['unit']*12<=3:
                if len(pairs)>old['chainCount']:old.update(refined)
                return
        hypotheses.append(refined)


DifficultEntryIdentity = MapEntryGeometryIdentity
