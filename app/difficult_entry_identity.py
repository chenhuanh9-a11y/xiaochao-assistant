"""Read-only entry hypotheses. The existing identity/pose consumers own state."""
import json,time
from pathlib import Path
import numpy as np
import cv2

from entry_evidence_work import EntryEvidenceJob as MainEntryJob, EntryEvidenceWork


def _pose_source_eligible(source):
    return source.get('proposalOnly', False) is False and source.get('authorizesPose', True) is True


def entry_geometry(frame):
    """Difficult entry at bottom of map was outside legacy 22–80% crop.

    Keep this observation local to entry retrieval; nightmare extraction and
    all final display validators retain their existing behavior.
    """
    from map_observation import map_reuse_evidence
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
    components=[(max(x0,int(stats[j,0])-8),max(y0,int(stats[j,1])-8),
        min(x1,int(stats[j,0]+stats[j,2])+8),min(y1,int(stats[j,1]+stats[j,3])+8))
        for j in sorted(valid,key=lambda j:-stats[j,4])
        if j!=i and stats[j,2]>=48 and stats[j,3]>=64]
    return dict(query=(current[0]>.22).astype(np.uint8),observationBounds=bounds,
                otherObservationBounds=components) if current is not None else None


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


class MainEntryEvidence(EntryEvidenceWork):
    """One observation's source work; its caller owns deadline and cancellation.

    The scanning worker runs jobs sequentially. Probe every eligible source
    before deriving the original competitive band.
    Stopping before every required job finishes leaves identity unresolved.
    No job writes runtime state or grants display/live authority.
    """
    def __init__(self, engine, frame, floor, *, map_id=None, route=None,
                 candidate_map_ids=None):
        self.engine, self.floor, self.map_id, self.route = engine, str(floor), map_id, route
        self.floor_indexed = any(item['floor'] == self.floor for item, *_ in engine.entries)
        self.entries = [entry for entry in engine.entries
                        if entry[0]['floor'] == self.floor
                        and (map_id is None or entry[0]['mapId'] == map_id)
                        and (candidate_map_ids is None or entry[0]['mapId'] in candidate_map_ids)]
        self.context, self.query = None, []
        self.rows, self.competitive, self.evaluated_map_ids = [], [], []
        self._verification_unavailable = False
        super().__init__(frame if self.floor_indexed else None)

    def _observe(self):
        shared = getattr(self.engine, 'observation_dependencies', None)
        if shared is not None and shared.owns(self.frame):
            self.context, self.query, self.qpoints, self.qrays = shared.get(
                ('main-observation',), self._observation,
                cancelled=getattr(self.engine, 'observation_cancelled', None))
            # The geometry and its lazy derived parts belong to this consumer.
            self.engine._observation = (self.frame, self.context)
            return
        self.context, self.query, self.qpoints, self.qrays = self._observation()

    def _observation(self):
        context = self.engine.geometry(self.frame)
        query = entry_corners(self.frame, context) if context else []
        return context, query, np.array([row['point'] for row in query]), np.array([row['rays'] for row in query])

    def _probe(self, entry):
        self.evaluated_map_ids.append(entry[0]['mapId'])
        probe = lambda: self.engine._probe_entry(self.frame, entry, self.qpoints, self.qrays)
        shared = getattr(self.engine, 'observation_dependencies', None)
        row = (shared.get(('main-probe', tuple(id(part) for part in entry)), probe,
                         cancelled=getattr(self.engine, 'observation_cancelled', None))
               if shared is not None and shared.owns(self.frame) else probe())
        if row is not None:
            self.rows.append(row)

    def _verify(self, row):
        try:
            row['verification'] = self.engine.verify(self.frame, row, context=self.context, route=self.route)
        except (ValueError, TypeError, KeyError, OSError, cv2.error, StopIteration) as error:
            row['verification'] = dict(success=False, reason='entry-resource-unavailable', error=str(error))
        if row['verification'].get('reason') in (
                'entry-resource-unavailable', 'entry-shared-structure-unavailable', 'entry-observation-unavailable'):
            self._verification_unavailable = True

    def _jobs(self):
        if not self.floor_indexed:
            return
        yield self._job(None, 'observe', self._observe)
        if len(self.query) < 3:
            return
        for entry in self.entries:
            yield self._job(entry[0]['mapId'], 'probe', lambda entry=entry: self._probe(entry))
        self.rows.sort(key=lambda row: -row['score'])
        self.competitive = [row for row in self.rows
                            if row['score'] >= max(10, self.rows[0]['score'] * .65)] if self.rows else []
        if 0 < len(self.competitive) <= 3:
            for row in self.competitive:
                yield self._job(row['mapId'], 'verify', lambda row=row: self._verify(row))

    def finish(self, *, elapsed_ms=0.):
        if self._result is not None:
            return self._result
        self._close()
        if not self.floor_indexed:
            self._result = ([], dict(reason='entry-floor-not-indexed', complete=True, elapsedMs=0.))
        elif self._complete and len(self.query) < 3:
            self._result = ([], dict(reason='entry-visible-structure-insufficient', queryChains=len(self.query),
                                     minimumQueryChains=3, elapsedMs=elapsed_ms))
        else:
            self.rows.sort(key=lambda row: -row['score'])
            complete = self._complete and not self._verification_unavailable and not self._failed
            self.engine._finalize_rank(self.rows, self.competitive, complete, self.map_id)
            self._result = (self.rows, dict(
                reason=('entry-work-failed' if self._failed
                        else 'entry-verification-unavailable' if self._verification_unavailable
                        else 'entry-known-identity-pose-verified' if self.map_id is not None and any(row['poseVerified'] for row in self.rows)
                        else 'entry-identity-verified' if any(row['identityVerified'] for row in self.rows)
                        else 'entry-neighborhood-candidates' if complete else 'entry-budget-incomplete'),
                complete=complete, queryChains=len(self.query), evaluatedMapIds=self.evaluated_map_ids,
                elapsedMs=elapsed_ms))
            if self._verification_unavailable or self._failed:
                self._result[1]['verificationComplete'] = False
        return self._result


class MapEntryGeometryIdentity:
    def __init__(self,root,*,structure_store=None,route_loader=None,shared_reference_provider=None,profile_id='zhan10-speed',profile_for_map=None,read_resource=None):
        self.profile_id=profile_id
        self.profile_for_map=profile_for_map or (lambda map_id: self.profile_id)
        self.root=Path(root);self.entries=[];self.error=None
        self._observation=None
        self._wall_lines={}
        self.route_loader=route_loader
        self.read_resource=read_resource
        self.shared_reference_provider=shared_reference_provider
        if structure_store is None:
            if read_resource is not None:
                raise ValueError('entry-reader-requires-structure-store')
            from author_structure_resources import AuthorStructureResourceStore
            structure_store=AuthorStructureResourceStore(self.root/'index.json',self.root)
        self.structures=structure_store
        try:
            path=self.root/'entries.json'
            payload=json.loads(path.read_text(encoding='utf8') if read_resource is None else read_resource(path))
            if payload['schemaVersion']!=1:raise ValueError('unsupported entry index')
            if read_resource is not None:
                from side_entry_geometry import _bounded_list,_bounded_points
                _bounded_list(payload['entries'],256,'main entries')
                # Current source maxima are 124 chains and 21 outline points.
                # Bound both axes before the chains x outline distance array.
                total_chains=0
                for item in payload['entries']:
                    chains=_bounded_list(item['chains'],512,'main chains')
                    outline=_bounded_points(item['outline'],256,'main outline')
                    total_chains+=len(chains)
                    if total_chains>65536 or not outline or len(chains)*len(outline)>131072:
                        raise ValueError('entry-resource-complexity: main distances')
                    for chain in chains:
                        if len(_bounded_points(chain['points'],3,'main chain points'))!=3 or len(_bounded_points(chain['rays'],2,'main chain rays'))!=2:
                            raise ValueError('invalid entry chain shape')
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
        if cached is not None and (cached[0] is frame or np.array_equal(cached[0],frame)):
            return cached[1]
        shared = getattr(self, 'observation_dependencies', None)
        if shared is not None and shared.owns(frame):
            # Geometry is a shared dependency of an already admitted kernel.
            # A retiring producer must not poison the other lane's kernel.
            context = shared.get(('entry-geometry',), lambda: entry_geometry(frame))
            self._observation = (frame, context)
            return context
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

    def has_pose_eligible_source(self, map_id, floor):
        """Resource purpose only; this does not verify a current-frame pose.

        A known-map pose consumer can omit a producer with no eligible source.
        General candidate ranking still retains every proposal and competitor.
        """
        return any(item['mapId'] == map_id and str(item['floor']) == str(floor)
                   and _pose_source_eligible(item) for item, *_ in self.entries)

    def rank(self,frame,floor,*,budget_seconds=None,map_id=None,route=None,candidate_map_ids=None):
        started=time.perf_counter()
        if not any(item['floor']==str(floor) for item,*_ in self.entries):
            return [],dict(reason='entry-floor-not-indexed',complete=True,elapsedMs=0.)
        if budget_seconds is not None and budget_seconds<=0:
            return [],dict(reason='entry-budget-incomplete',complete=False,elapsedMs=0.)
        budget=1.0 if budget_seconds is None else max(0.,float(budget_seconds))
        evidence=self.prepare_rank(frame,floor,map_id=map_id,route=route,candidate_map_ids=candidate_map_ids)
        while (job := evidence.next_job()) is not None:
            if time.perf_counter()-started>budget:
                break
            job.run()
        return evidence.finish(elapsed_ms=(time.perf_counter()-started)*1000)

    def prepare_rank(self,frame,floor,*,map_id=None,route=None,candidate_map_ids=None):
        return MainEntryEvidence(self,frame,floor,map_id=map_id,route=route,candidate_map_ids=candidate_map_ids)

    def _probe_entry(self,frame,entry,qpoints,qrays):
        item,source,rays,main=entry
        source=source[:,1];hypotheses=[]
        oriented=np.min(np.sum(qrays[:,None]*rays[None],axis=3),axis=2)>=.95
        oriented_pairs=np.nonzero(oriented)
        oriented_query_points=qpoints[oriented_pairs[0]]
        oriented_queries=[np.flatnonzero(oriented[:, index]) for index in range(len(source))]
        main_ids=np.flatnonzero(main)
        # Two entry corners seed scale; different measured corners and
        # neighboring rooms must support it. No complete wall endpoint
        # is inferred from a fog-clipped contour.
        for ai,si in enumerate(main_ids):
            for sj in main_ids[ai+1:]:
                qa_ids, qb_ids = oriented_queries[si], oriented_queries[sj]
                if not len(qa_ids) or not len(qb_ids):
                    continue
                delta=source[sj]-source[si];denom=float(delta@delta)
                if denom<(.7*item['unit'])**2:continue
                qa,qb=np.meshgrid(qa_ids,qb_ids)
                qa=qa.ravel();qb=qb.ravel()
                qdelta=qpoints[qb]-qpoints[qa]
                scales=qdelta@delta/denom
                errors=np.linalg.norm(qdelta-scales[:,None]*delta,axis=1)
                compatible=(errors<=2)&(scales*item['unit']>=8)&(scales*item['unit']<=min(frame.shape[:2])/4)
                for pair_index in np.flatnonzero(compatible):
                    qi=qa[pair_index];s=float(scales[pair_index]);t=qpoints[qi]-s*source[si]
                    self._score_hypothesis(item,source,main,qpoints,oriented,s,t,hypotheses,
                                           oriented_pairs,oriented_query_points)
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
            return dict(mapId=item['mapId'],profileId=self.profile_for_map(item['mapId']),entryKind='main',material=item['material'],
                score=float(best['chainCount']),hypothesis={k:v for k,v in best.items() if k!='anchor'},
                poseAmbiguous=len(hypotheses)>1 and hypotheses[1]['chainCount']>=best['chainCount']-1,
                sourceProduct=item['sourceProduct'],
                **{key:item[key] for key in ('proposalOnly','authorizesPose','authorizesIdentity') if key in item})

    @staticmethod
    def _finalize_rank(rows,competitive,complete,map_id):
        accepted=[r for r in competitive if r.get('verification',{}).get('success')]
        winner=accepted[0] if complete and len(accepted)==1 else None
        for row in rows:
            # Proposal geometry still competes and can seed a later independent
            # fit. Passing this entry check cannot upgrade its resource purpose.
            row['poseVerified']=bool(row is winner and not row['poseAmbiguous']
                and _pose_source_eligible(row)
                and row['hypothesis']['neighborChains']>=6 and row['score']>=10
                and (row['hypothesis']['mainChains']>=3 or (
                    row['hypothesis']['neighborChains']>=8
                    and min(row['hypothesis']['neighborExtentGrid'])>=4)))
            row['identityVerified']=row['poseVerified'] and map_id is None and row.get('authorizesIdentity',True) is True
        if winner is not None:rows.sort(key=lambda r:(not r['identityVerified'],-r['score']))

    def verify(self,frame,row,*,context=None,route=None):
        shared = getattr(self, 'observation_dependencies', None)
        if shared is not None and shared.owns(frame):
            actual_context = self.geometry(frame) if context is None else context
            key = ('main-verify', id(self.shared_reference_provider), id(route),
                   shared.value_key(row), shared.value_key(None if actual_context is None else
                       {name: actual_context.get(name) for name in ('query', 'observationBounds')}))
            return shared.get(key, lambda: self._verify_uncached(frame, row, context=actual_context, route=route))
        return self._verify_uncached(frame, row, context=context, route=route)

    def _verify_uncached(self,frame,row,*,context=None,route=None):
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
            if self.route_loader is not None:
                route=self.route_loader(profile,item['mapId'],item['floor'])
            elif self.read_resource is None:
                route=imread_unicode(self.root/item['routeResource'],-1)
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
    def _score_hypothesis(item,source,main,qpoints,oriented,s,t,hypotheses,oriented_pairs=None,
                          oriented_query_points=None):
        anchor=np.array(item['center'])*s+t
        if any(np.linalg.norm(anchor-h['anchor'])<3 and abs(s-h['scale'])*item['unit']<.25 for h in hypotheses):return
        # Only measured corners contribute. Fog is never a missing-room vote.
        qi,si=np.nonzero(oriented) if oriented_pairs is None else oriented_pairs
        if oriented_query_points is None:
            oriented_query_points=qpoints[qi]
        for iteration in range(3):
            # Direction-incompatible pairs were always discarded. Compute the
            # same distances only for compatible pairs, in np.argwhere order.
            errors=np.linalg.norm(oriented_query_points-(source*s+t)[si],axis=1)
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
            a=source[pairs[:,1]];b=qpoints[pairs[:,0]]
            a_mean,b_mean=a.mean(axis=0),b.mean(axis=0)
            centered=a-a_mean
            proposed_s=float(np.sum(centered*(b-b_mean))/np.sum(centered*centered))
            proposed_t=b_mean-proposed_s*a_mean
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
