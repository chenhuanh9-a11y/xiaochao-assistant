"""Experimental current-pixel structure retrieval.

Static clean occupancy and current pixels validate every retrieved pose.
Difficult mode can also propose a bounded translation from already verified
pixels; the shared committed pose remains its sole coordinate prior.
"""
import time
import cv2
import numpy as np
from nightmare_candidate import (selected_route_current_frame_residual,
    fit_axis_locked_point_geometry)


def contour_graph(mask):
    """Oriented turns joined by measured boundary segments, including holes."""
    contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    nodes = []
    for contour in contours:
        polygon = cv2.approxPolyDP(contour, 2., True).reshape(-1, 2)
        if len(polygon) < 4 or abs(cv2.contourArea(polygon)) < 100:
            continue
        # Keep OpenCV's opposite outer/hole winding: it encodes which side
        # contains terrain. Normalizing both winds destroys turn direction
        # when a partially explored outer contour matches a full-map hole.
        start = len(nodes)
        for i, point in enumerate(polygon):
            rays = np.float64([polygon[(i-1)%len(polygon)]-point,
                               polygon[(i+1)%len(polygon)]-point])
            lengths = np.linalg.norm(rays,axis=1)
            directions = rays / np.maximum(lengths[:,None],1e-9)
            nodes.append(dict(point=point.astype(float), rays=directions, lengths=lengths,
                previous=start+(i-1)%len(polygon), next=start+(i+1)%len(polygon),
                valid=bool(lengths.min()>=8 and abs(np.linalg.det(directions))>.45)))
    return nodes


def _regional_observed_edges_patch(frame, query):
    # Experimental consumer of the contrast-normalized observation principle
    # validated in 19f987f. Public residual code is deliberately unchanged.
    # Sharpness is relative to local range, so a 4-level wall is not weaker
    # evidence than a bright wall. No source or residual enters selection.
    edge=cv2.morphologyEx(query,cv2.MORPH_GRADIENT,np.ones((3,3),np.uint8))>0
    gray=cv2.cvtColor(frame[:,:,:3],cv2.COLOR_BGR2GRAY).astype(np.float32)
    smooth=cv2.GaussianBlur(gray,(3,3),0)
    gx=cv2.Sobel(smooth,cv2.CV_32F,1,0)/8.
    gy=cv2.Sobel(smooth,cv2.CV_32F,0,1)/8.
    neighborhood=np.ones((9,9),np.uint8)
    span=cv2.dilate(gray,neighborhood)-cv2.erode(gray,neighborhood)
    sharp=((np.hypot(gx,gy)>=span*.25)&(span>0)).astype(np.uint8)
    # Reuse the live anchor mask's independent HSV marker definition. Its
    # 50px tracking guard is inappropriate for wall verification: only the
    # marker pixels and two anti-alias pixels are excluded here.
    hsv=cv2.cvtColor(frame[:,:,:3],cv2.COLOR_BGR2HSV)
    marker=((hsv[:,:,1]>=88)&(hsv[:,:,2]>=80)).astype(np.uint8)
    marker=cv2.dilate(marker,np.ones((5,5),np.uint8))
    return edge & (cv2.dilate(sharp,np.ones((3,3),np.uint8))>0) & (marker==0)


def regional_observed_edges(frame,query):
    """Compute the identical local operator only where query edges exist."""
    edge=cv2.morphologyEx(query,cv2.MORPH_GRADIENT,np.ones((3,3),np.uint8))>0
    yy,xx=np.nonzero(edge)
    if not len(xx): return edge
    # 9x9 local range (radius4) plus sharp-edge dilation (radius1).
    # This halo also exceeds Gaussian+Sobel and marker-guard dependencies.
    x0,x1=max(0,int(xx.min())-5),min(query.shape[1],int(xx.max())+6)
    y0,y1=max(0,int(yy.min())-5),min(query.shape[0],int(yy.max())+6)
    result=np.zeros_like(edge)
    result[y0:y1,x0:x1]=_regional_observed_edges_patch(frame[y0:y1,x0:x1],query[y0:y1,x0:x1]) & edge[y0:y1,x0:x1]
    return result


def current_geometry(frame):
    """One solver-owned observation; never retained across frames/cycles."""
    from map_observation import structure_current_map_evidence
    current=structure_current_map_evidence(frame)
    if current is None: return None
    return dict(query=current['query'],observationBounds=current['observationBounds'],visible=current.get('visible'))


def current_part(frame,context,key):
    if key not in context:
        query=context['query']
        if key=='nodes': context[key]=contour_graph(query)
        elif key=='edge': context[key]=cv2.morphologyEx(query,cv2.MORPH_GRADIENT,np.ones((3,3),np.uint8))>0
        elif key=='observed':
            observed=regional_observed_edges(frame,query)
            bounds=context.get('observationBounds')
            if bounds is not None:
                # A crop edge is an unknown boundary, never an observed wall.
                # Three pixels cover mask dilation plus edge/sharpness support.
                # Bounds come from extraction, without a source pose/residual.
                x0,y0,x1,y1=bounds
                interior=np.zeros_like(observed)
                interior[y0+3:y1-3,x0+3:x1-3]=True
                observed &= interior
            context[key]=observed
        elif key=='lines': context[key]=visible_wall_segments(query,current_part(frame,context,'observed'),nodes=current_part(frame,context,'nodes'))
    return context[key]


def projected_geometry(frame, reference, target, context):
    """One exact projection, owned only by the current solver observation."""
    cached=context.get('_projection')
    if (cached is None or cached['reference'] is not reference
            or not np.array_equal(cached['matrix'],target)):
        cached=dict(reference=reference,matrix=target.copy(),
            mask=cv2.warpAffine(reference,target[:2],(frame.shape[1],frame.shape[0]),flags=cv2.INTER_NEAREST))
        context['_projection']=cached
    return cached


def regional_validation(frame, query, projected, *, _context=None):
    context=_context if _context is not None else dict(query=query)
    cached=context.get('_projection')
    reusable=cached is not None and cached['mask'] is projected
    if reusable and 'regional' in cached:return cached['regional']
    edge=current_part(frame,context,'edge')
    source_edge=cv2.morphologyEx(projected,cv2.MORPH_GRADIENT,np.ones((3,3),np.uint8))>0
    edge_pixels=edge.view(np.uint8)
    edge_count=cv2.countNonZero(edge_pixels)
    edge_x,edge_y,edge_width,edge_height=cv2.boundingRect(edge_pixels)
    observed_edge=current_part(frame,context,'observed')
    diagnostics=dict(regionalObservation=dict(maskEdges=edge_count,contrastSupportedEdges=int(observed_edge.sum())),regions=[])
    if not edge_count:
        return dict(**diagnostics,regionalEdgeVerified=False)
    source_pixels=source_edge.view(np.uint8)
    source_x,source_y,source_width,source_height=cv2.boundingRect(source_pixels)
    if source_width and source_height:
        # Only distances at query-edge pixels are consumed below. Keep every
        # zero (source edge) and every query-edge pixel in the ROI so the
        # DIST_L2/3 result at each consumed pixel is unchanged by cropping.
        offset_y=min(edge_y,source_y)
        offset_x=min(edge_x,source_x)
        end_y=max(edge_y+edge_height,source_y+source_height)
        end_x=max(edge_x+edge_width,source_x+source_width)
        source_roi=source_edge[offset_y:end_y,offset_x:end_x]
        distances=cv2.distanceTransform((~source_roi).astype(np.uint8),cv2.DIST_L2,3)
    else:
        # Preserve OpenCV's full-image all-nonzero behavior when the projected
        # source has no edge pixels.
        offset_y=offset_x=0
        distances=cv2.distanceTransform((~source_edge).astype(np.uint8),cv2.DIST_L2,3)
    for gy in range(3):
        for gx in range(3):
            y0,y1=[round(edge_y+edge_height*(gy+i)/3) for i in (0,1)]
            x0,x1=[round(edge_x+edge_width*(gx+i)/3) for i in (0,1)]
            values=distances[y0-offset_y:y1-offset_y,x0-offset_x:x1-offset_x][observed_edge[y0:y1,x0:x1]]
            if len(values)>=max(30,int(edge_count*.015)):
                diagnostics['regions'].append(dict(cell=[gx,gy],count=len(values),median=float(np.median(values)),
                    within3=float(np.mean(values<=3)),within6=float(np.mean(values<=6))))
    diagnostics['regionalEdgeVerified']=bool(diagnostics['regions']) and all(
        r['median']<=4.5 and r['within3']>=.45 and r['within6']>=.62 for r in diagnostics['regions'])
    if reusable:cached['regional']=diagnostics
    return diagnostics


def _static_validation_context(frame,context,matrix,source_unknown):
    if source_unknown is None:return context
    unknown=cv2.warpAffine(source_unknown,matrix[:2],(frame.shape[1],frame.shape[0]),flags=cv2.INTER_NEAREST)>0
    filtered=dict(context,observed=current_part(frame,context,'observed') & ~unknown)
    # A regional result also depends on observed pixels, not only projection.
    # Keep the parent's mask/nodes cache, but never reuse its unfiltered verdict.
    filtered.pop('_projection',None)
    return filtered


def _projected_known(frame,matrix,source_known_boundary,source_unknown):
    if source_known_boundary is None:return None
    known=cv2.warpAffine(source_known_boundary.astype(np.uint8),matrix[:2],(frame.shape[1],frame.shape[0]),flags=cv2.INTER_NEAREST)
    known=cv2.dilate(known,np.ones((3,3),np.uint8))>0
    if source_unknown is not None:
        known &= cv2.warpAffine(source_unknown,matrix[:2],(frame.shape[1],frame.shape[0]),flags=cv2.INTER_NEAREST)==0
    return known


def graph_point_geometry(frame, reference, target, *, _context=None, allow_scale_recovery=False, source_known_boundary=None, source_unknown=None):
    """Match directed neighboring segments before solving a pose.

    Correspondences must explain both a turn orientation and an adjacent
    measured segment. A point is not removed because of its fitted residual.
    """
    context=_context if _context is not None else current_geometry(frame)
    if context is None:
        return {'success':False,'reason':'graph-query-empty'}
    query=context['query']
    projection=projected_geometry(frame,reference,target,context)
    projected=projection['mask']
    if 'nodes' not in projection:projection['nodes']=contour_graph(projected)
    qnodes, snodes = current_part(frame,context,'nodes'), projection['nodes']
    known=_projected_known(frame,target,source_known_boundary,source_unknown)
    if known is not None:
        # Filtering is candidate-specific; do not mutate cached raw nodes.
        snodes=[dict(node) for node in snodes]
        measured={c['index'] for c in observed_corner_chains(frame,dict(nodes=snodes,observed=known))}
        for index,node in enumerate(snodes):node['valid']=node['valid'] and index in measured
    pairs={}
    valid_source=np.array([i for i,n in enumerate(snodes) if n['valid']],dtype=int)
    source_points=np.array([snodes[i]['point'] for i in valid_source]).reshape(-1,2)
    source_rays=np.array([snodes[i]['rays'] for i in valid_source]).reshape(-1,2,2)
    for qi,q in enumerate(qnodes):
        if not q['valid']:
            continue
        near=(np.sum((source_points-q['point'])**2,axis=1)<=48**2)
        oriented=np.min(np.sum(source_rays*q['rays'],axis=2),axis=1)>=.95
        for si in valid_source[near & oriented]: pairs[qi,int(si)]=[]
    for qi,si in pairs:
        q,s=qnodes[qi],snodes[si]
        for side,index in [('previous',0),('next',1)]:
            other=(q[side],s[side])
            # Segment length/direction are measured independently of pose.
            if other in pairs and abs(q['lengths'][index]-s['lengths'][index])<=3:
                pairs[qi,si].append(other)
    supported=[pair for pair,neighbors in pairs.items() if len(neighbors)==2]
    # Retain ambiguity until connectivity resolves it; don't pick the first
    # repeated corner based on proximity to the coarse proposal.
    unique=[]
    for qi,si in supported:
        if sum(qj==qi for qj,sj in supported)==1 and sum(sj==si for qj,sj in supported)==1:
            unique.append((qi,si))
    diagnostics=dict(queryTurns=sum(n['valid'] for n in qnodes),sourceTurns=sum(n['valid'] for n in snodes),
                     orientedPairs=len(pairs),adjacentPairs=len(supported),uniquePairs=len(unique))
    diagnostics['segments']=[dict(queryPoint=qnodes[q]['point'].tolist(),
        projectedSourcePoint=snodes[s]['point'].tolist(),
        queryLengths=qnodes[q]['lengths'].tolist(),projectedSourceLengths=snodes[s]['lengths'].tolist())
        for q,s in unique]
    if len(unique)<6:
        return dict(success=False,reason='graph-adjacent-pairs-insufficient',graph=diagnostics)
    observed=np.asarray([qnodes[q]['point'] for q,s in unique])
    expected=np.asarray([snodes[s]['point'] for q,s in unique])
    source_h=np.column_stack((expected,np.ones(len(expected))))@np.linalg.inv(target).T
    source=source_h[:,:2]/source_h[:,2,None]
    scale_policy={} if allow_scale_recovery else {'maximum_relative_scale_correction':0.}
    result=fit_axis_locked_point_geometry(source,observed,target,local_points=observed,
        query_shape=query.shape,forward_backward=np.zeros(len(source)),flow_error_values=np.zeros(len(source)),
        initial_count=len(qnodes),component=None,**scale_policy)
    result['graph']=diagnostics
    result['measuredPairs']={'source':source.tolist(),'observed':observed.tolist()}
    # Preserve successful translation-only recovery. An obsolete scale prior
    # can recover only through the existing observable, disjoint-held-out H1.
    model='h0' if (result.get('h0') or {}).get('success') else 'h1'
    selected=result.get(model) or {}
    result['structureModel']=model if selected.get('success') else None
    if selected.get('success'):
        projected=projected_geometry(frame,reference,np.asarray(selected['matrixSourceToClient']),context)['mask']
    validation_matrix=np.asarray(selected['matrixSourceToClient']) if selected.get('success') else target
    diagnostics.update(regional_validation(frame,query,projected,_context=_static_validation_context(frame,context,validation_matrix,source_unknown)))
    return result


def observed_corner_chains(frame,context):
    """Sharp connected chains are hypotheses, not proof of complete walls."""
    nodes=current_part(frame,context,'nodes')
    observed=current_part(frame,context,'observed')
    chains=[]
    h,w=observed.shape
    for i,node in enumerate(nodes):
        ids=(node['previous'],i,node['next'])
        if not all(nodes[j]['valid'] for j in ids): continue
        points=np.array([nodes[j]['point'] for j in ids])
        if np.any(points<5) or np.any(points>np.array([w-6,h-6])): continue
        supported=True
        for endpoint in (points[0],points[2]):
            samples=np.rint(points[1]+np.linspace(0,1,int(np.ceil(np.linalg.norm(endpoint-points[1])))+1)[:,None]*(endpoint-points[1])).astype(int)
            if not observed[samples[:,1],samples[:,0]].all():
                supported=False; break
        if supported: chains.append(dict(index=i,points=points,rays=node['rays']))
    return chains


def _wall_raster_samples(qlines):
    """Prepare immutable observation samples for one solver invocation."""
    samples=[]
    for q in qlines:
        tangent=np.arange(int(np.ceil(q['lo'])),int(np.floor(q['hi']))+1)
        normal=np.full(len(tangent),round(q['normal']),dtype=int)
        xy=np.column_stack((normal,tangent) if q['axis']==0 else (tangent,normal))
        direction=(0 if q['sign']>0 else 1) if q['axis']==0 else (3 if q['sign']>0 else 2)
        samples.append((xy,direction))
    if not samples:return None
    points=np.concatenate([xy for xy,direction in samples])
    lower=points.min(axis=0)-8;upper=points.max(axis=0)+9
    lengths=np.array([len(xy) for xy,direction in samples],dtype=int)
    nonempty=np.flatnonzero(lengths)
    starts=np.r_[0,np.cumsum(lengths)][nonempty]
    directions=np.array([direction for xy,direction in samples],dtype=int)[nonempty]
    return dict(points=points,lower=lower,upper=upper,local=points-lower,
        fragmentCount=len(samples),starts=starts,directions=directions,
        pointFragments=np.repeat(np.arange(len(nonempty)),lengths[nonempty]),
        pointDirections=np.repeat(directions,lengths[nonempty]),pointIndexes=np.arange(len(points)))


def _wall_distance_six_stencil():
    # DIST_L2/3 has positive local costs: seven minimum-cost steps exceed6.
    # Measure the exact <=6 neighborhood on this OpenCV backend, rather than
    # substituting a Euclidean disk or hard-coded chamfer weights.
    pulse=np.ones((17,17),np.uint8);pulse[8,8]=0
    y,x=np.nonzero(cv2.distanceTransform(pulse,cv2.DIST_L2,3)<=6)
    offsets=np.column_stack((x-8,y-8))
    offsets.setflags(write=False)
    return offsets


_WALL_DISTANCE_SIX_STENCIL=_wall_distance_six_stencil()


def _wall_raster_negative_witness(supports, unknown, samples):
    points=samples['local']
    measured=np.flatnonzero(~unknown[points[:,1],points[:,0]])
    if len(measured):
        measured=measured[np.linspace(0,len(measured)-1,min(128,len(measured)),dtype=int)]
    height,width=unknown.shape
    for direction,edge in enumerate(supports):
        selected=measured[samples['pointDirections'][measured]==direction]
        if not len(selected):continue
        xy=points[selected]
        x=xy[:,0,None]+_WALL_DISTANCE_SIX_STENCIL[None,:,0]
        y=xy[:,1,None]+_WALL_DISTANCE_SIX_STENCIL[None,:,1]
        inside=(x>=0)&(x<width)&(y>=0)&(y<height)
        x=np.clip(x,0,width-1);y=np.clip(y,0,height-1)
        if not np.all(np.any(inside & edge[y,x] & ~unknown[y,x],axis=1)):
            return True
    return False


def initial_wall_raster_validation(qlines, reference, model, *, source_unknown=None, source_wall_mask=None, _samples=None):
    """Public callers always retain complete success and failure diagnostics."""
    return _wall_raster_validation(qlines,reference,model,source_unknown=source_unknown,
        source_wall_mask=source_wall_mask,_samples=_samples)


def _wall_raster_validation(qlines, reference, model, *, source_unknown=None, source_wall_mask=None, _samples=None, _discard_rejected_details=False):
    """Check every observed fragment against the unsimplified directed raster.

    Polygon segment filtering is useful for fitting but is not an exhaustive
    wall inventory: a slight tilt or a short scaled edge can disappear there.
    Keep the same six-client-pixel limit and test every fragment pixel.
    """
    from nightmare_candidate import _wall_normal_support
    samples=_samples if _samples is not None else _wall_raster_samples(qlines)
    if samples is None:return dict(verified=False,reason='no-observed-fragments')
    points,lower,upper=(samples[key] for key in ('points','lower','upper'))
    matrix=np.array([[model[0],0,model[1]-lower[0]],[0,model[0],model[2]-lower[1]]])
    projected=cv2.warpAffine(reference,matrix,tuple((upper-lower).tolist()),flags=cv2.INTER_NEAREST)
    unknown=(cv2.warpAffine(source_unknown,matrix,tuple((upper-lower).tolist()),flags=cv2.INTER_NEAREST)>0
             if source_unknown is not None else np.zeros(projected.shape,dtype=bool))
    supports=_wall_normal_support(projected)
    if source_wall_mask is not None:
        # Occupancy contains rooms on both sides of an internal wall. Its
        # silhouette cannot represent that wall; consume the explicit positive
        # wall channel without treating unmarked pixels as negative evidence.
        divided=reference & (source_wall_mask==0).astype(np.uint8)
        wall_projection=cv2.warpAffine(divided,matrix,tuple((upper-lower).tolist()),flags=cv2.INTER_NEAREST)
        supports=[a|b for a,b in zip(supports,_wall_normal_support(wall_projection))]
    # Only initial_wall_geometry discards failed raster details immediately.
    # Preserve the original full path (including its exceptions) for malformed
    # non-2D inputs. No witness grants acceptance; the full DT still decides.
    if (_discard_rejected_details and projected.ndim==2 and unknown.ndim==2
        and all(edge.ndim==2 for edge in supports)
        and _wall_raster_negative_witness(supports,unknown,samples)):
        return dict(verified=False)
    fields=[cv2.distanceTransform((~(edge & ~unknown)).astype(np.uint8),cv2.DIST_L2,3)
            for edge in supports]
    local=samples['local']
    measured=~unknown[local[:,1],local[:,0]]
    values=np.empty(len(points),dtype=fields[0].dtype)
    for direction,field in enumerate(fields):
        selected=samples['pointDirections']==direction
        values[selected]=field[local[selected,1],local[selected,0]]
    values[~measured]=-np.inf
    maxima=np.maximum.reduceat(values,samples['starts'])
    present=np.logical_or.reduceat(measured,samples['starts'])
    # argmax selects the first measured sample on ties. Preserve that exact
    # choice, fragment order and diagnostics while reducing all fragments.
    first=np.minimum.reduceat(np.where(
        values==maxima[samples['pointFragments']],samples['pointIndexes'],len(points)),samples['starts'])
    errors=maxima[present].astype(float).tolist()
    worst=[dict(client=points[at].tolist(),direction=int(direction),errorPixels=error)
        for at,direction,error in zip(first[present],samples['directions'][present],errors)]
    measured_count=int(measured.sum());unknown_count=len(points)-measured_count
    return dict(verified=bool(errors and max(errors)<=6),maxPixels=max(errors) if errors else None,fragmentMaxPixels=errors,
                fragmentCount=samples['fragmentCount'],observedPixels=len(points),measuredPixels=measured_count,sourceUnknownPixels=unknown_count,worstPoints=worst)


def initial_wall_geometry(qlines, slines, scale, translation, arrays, cache=None, *, reference=None, source_unknown=None, raster_samples=None):
    """Validate unknown uniform scale with independent directed wall planes.

    A chain only proposes a pose. Partial walls supply normal constraints;
    their fog-truncated endpoints never become point correspondences. Each
    physical plane is held out from a full-rank scale/translation fit.
    """
    qa,sa,_,sparse=arrays
    qi,si,starts,rows,pair_query,pair_source=sparse
    if not len(qi):return None
    def reduce_rows(values,operation,fill):
        result=np.full(len(qlines),fill,dtype=values.dtype)
        result[rows]=operation.reduceat(values,starts)
        return result
    def distances_at(factor,shift):
        normal=np.abs(factor*pair_source[:,2]+shift[pair_query[:,0].astype(int)]-pair_query[:,2])
        tangent_shift=shift[1-pair_query[:,0].astype(int)]
        overlap=np.minimum(pair_query[:,4],factor*pair_source[:,4]+tangent_shift)-np.maximum(pair_query[:,3],factor*pair_source[:,3]+tangent_shift)
        return np.where(overlap>=np.minimum(12,pair_query[:,4]-pair_query[:,3]-1),normal,np.inf)
    distances=distances_at(scale,np.asarray(translation))
    # The selected physical correspondences completely determine the fit and
    # its all-fragment check. Different chain seeds often yield this exact
    # same assignment. Reuse only identical assignments within this call.
    matches=distances<=3
    source_normals=pair_source[:,2]
    nearest=reduce_rows(distances,np.minimum,np.inf)
    present=nearest<=3
    if np.any((reduce_rows(np.where(matches,source_normals,-np.inf),np.maximum,-np.inf)-
               reduce_rows(np.where(matches,source_normals,np.inf),np.minimum,np.inf))[present]>3):return None
    choices=reduce_rows(np.where(distances==nearest[qi],si,len(slines)),np.minimum,len(slines))
    choices=np.where(present,choices,-1)
    identity=tuple(choices.tolist())
    if cache is not None and identity in cache:
        return cache[identity]
    # Cache failed assignments as well. No future seed can repair the same
    # constraints merely by repeating their identical fit.
    if cache is not None:cache[identity]=None
    groups=[]
    for q,index in zip(qlines,choices):
        axis=q['axis']
        if index<0:continue
        s=slines[index]
        # Opposite normals are different faces even when their coordinates
        # are within three pixels. Collapsing them makes seed order choose
        # different constraints and invents duplicate pose identities.
        if any(g['axis']==axis and g['sign']==q['sign'] and abs(g['queryNormal']-q['normal'])<=3 for g in groups):continue
        if any(g['axis']==axis and g['sign']==q['sign'] and abs(g['sourceNormal']-s['normal'])<=3 for g in groups):return None
        groups.append(dict(axis=axis,sign=q['sign'],queryNormal=q['normal'],sourceNormal=s['normal'],
                           queryEdge=q.get('edgeId'),sourceEdge=s.get('edgeId')))
    for axis in (0,1):
        planes=[g for g in groups if g['axis']==axis]
        if len(planes)<2 or np.ptp([g['queryNormal'] for g in planes])<24:return None
    design=np.array([[g['sourceNormal'],int(g['axis']==0),int(g['axis']==1)] for g in groups])
    observed=np.array([g['queryNormal'] for g in groups])
    full_model,_,rank,_=np.linalg.lstsq(design,observed,rcond=None)
    if rank!=3 or full_model[0]<=0:return None
    # This check does not depend on the leave-one-plane-out fits. Rejecting
    # the same unsupported candidate first avoids solving those fits in vain.
    raster_validation=None
    if reference is not None:
        raster_validation=_wall_raster_validation(qlines,reference,full_model,source_unknown=source_unknown,_samples=raster_samples,_discard_rejected_details=True)
        if not raster_validation['verified']:return None
    elif np.any(reduce_rows(distances_at(full_model[0],full_model[1:]),np.minimum,np.inf)>6):return None
    errors=[]
    for i in range(len(groups)):
        keep=np.arange(len(groups))!=i
        model,_,rank,_=np.linalg.lstsq(design[keep],observed[keep],rcond=None)
        if rank!=3 or model[0]<=0:return None
        errors.append(abs(float(design[i]@model-observed[i])))
    heldout=dict(count=len(errors),medianPixels=float(np.median(errors)),p90Pixels=float(np.percentile(errors,90)),maxPixels=float(np.max(errors)))
    if heldout['medianPixels']>3 or heldout['p90Pixels']>8 or heldout['maxPixels']>20:return None
    # Account for every independently observed straight fragment before the
    # bounded raster validation. A repeated room cannot pass using only the
    # subset of planes it happens to explain.
    model=full_model
    matrix=np.array([[model[0],0,model[1]],[0,model[0],model[2]],[0,0,1.]])
    result=dict(matrix=matrix,planes=groups,heldOutNormals=heldout,rasterValidation=raster_validation)
    if cache is not None:cache[identity]=result
    return result


def selected_scale_observation(geometry, measured_matrix, final_matrix, residual, profile, *, wall_geometry=None):
    """Carry only the selected, validated scale through its final wall refinement."""
    measured=np.asarray(measured_matrix,dtype=np.float64)
    final=np.asarray(final_matrix,dtype=np.float64)
    if (measured.shape!=(3,3) or final.shape!=(3,3)
        or not np.isfinite(measured).all() or not np.isfinite(final).all()
        or not isinstance(residual,dict) or residual.get('verified') is not True):
        return None
    proof=None
    h1=geometry.get('h1') or {}
    h1_matrix=np.asarray(h1.get('matrixSourceToClient',[]),dtype=np.float64)
    if (geometry.get('scaleObservable') is True and h1.get('success') is True
        and (geometry.get('structureModel')=='h1' or geometry.get('mode')=='scale-plus-translation')
        and h1_matrix.shape==(3,3) and np.allclose(measured,h1_matrix,rtol=0,atol=1e-10)):
        proof=dict(success=True,scaleObservable=True,basis='disjoint-point-h1',
            validation=geometry.get('h1Validation'),supportPointCount=geometry.get('supportPointCount'))
    initial_wall=geometry.get('wallScale') or {}
    if (proof is None and initial_wall.get('heldOutNormals')
        and (initial_wall.get('rasterValidation') or {}).get('verified') is True):
        proof=dict(success=True,scaleObservable=True,basis='directed-wall-held-out',
            validation=initial_wall['heldOutNormals'],supportPointCount=len(initial_wall.get('planes') or []))
    wall=wall_geometry or {}
    wall_matrix=np.asarray(wall.get('matrix',[]),dtype=np.float64)
    same_linear=np.allclose(measured[:2,:2],final[:2,:2],rtol=0,atol=1e-10)
    if not same_linear:
        # A successful H1 does not prove a later wall similarity's new scale.
        # Only that wall fit's own independent holdout can replace its proof.
        similarity=(wall.get('diagnostics') or {}).get('scaleRefinement') or {}
        similarity_matrix=np.asarray(similarity.get('matrix',[]),dtype=np.float64)
        if (wall.get('success') is not True or similarity.get('success') is not True
            or not similarity.get('heldout') or similarity_matrix.shape!=(3,3)
            or not np.allclose(final[:2,:2],similarity_matrix[:2,:2],rtol=0,atol=1e-10)):
            return None
        measured=similarity_matrix
        proof=dict(success=True,scaleObservable=True,basis='wall-similarity-held-out',
            validation=similarity['heldout'],supportPointCount=len(similarity.get('support') or []))
    if proof is None or not (proof.get('validation') or {}).get('count'):
        return None
    if not np.allclose(measured,final,rtol=0,atol=1e-10):
        if (wall.get('success') is not True or wall_matrix.shape!=(3,3)
            or not np.allclose(wall_matrix,final,rtol=0,atol=1e-10)):
            return None
    return dict(routeProfileId=profile,geometry=proof,
        scaleMatrixSourceToClient=measured.copy(),matrixSourceToClient=final.copy())


def solve_initial_structure(frame,reference,validation,route,profile, *, cancelled=None, source_known_boundary=None, source_unknown=None, _context=None, wall_correspondence_identity=False, pose_index_candidates=(), pose_index_diagnostics=None):
    """Pure unknown-scale author->client candidate. Never initializes a group."""
    started=time.perf_counter()
    if cancelled and cancelled(): return dict(success=False,reason='structure-work-obsolete')
    context=_context if _context is not None else current_geometry(frame)
    if context is None: return dict(success=False,reason='initial-structure-query-unavailable')
    # A bounded optional index already paid for its shortlist on this same
    # frame. Do not launch the quadratic descriptor search in parallel; the
    # shared graph/refinement/author gates below will decide those seeds.
    query_chains=[] if pose_index_candidates else observed_corner_chains(frame,context)
    source_nodes=contour_graph(reference)
    source_chains=[]
    for i,n in enumerate(source_nodes):
        ids=(n['previous'],i,n['next'])
        if all(source_nodes[j]['valid'] for j in ids):
            points=np.array([source_nodes[j]['point'] for j in ids])
            supported=True
            if source_known_boundary is not None:
                for endpoint in (points[0],points[2]):
                    samples=np.rint(points[1]+np.linspace(0,1,int(np.ceil(np.linalg.norm(endpoint-points[1])))+1)[:,None]*(endpoint-points[1])).astype(int)
                    if not source_known_boundary[samples[:,1],samples[:,0]].all():
                        supported=False;break
            source_chains.append(dict(index=i,points=points,rays=n['rays'],measured=supported))
    diagnostic=dict(queryChains=len(query_chains),sourceChains=len(source_chains),candidates=[],pointRejections=[],stages=[],seedBudget=1024,validationBudget=8)
    def finish(success,reason,**kw):
        diagnostic['elapsedMs']=(time.perf_counter()-started)*1000
        return dict(success=success,reason=reason,diagnostics=diagnostic,**kw)
    if not query_chains and not pose_index_candidates:
        return finish(False,'initial-structure-supported-chains-insufficient')
    seeds=[]
    source_rays=np.array([s['rays'] for s in source_chains]).reshape(-1,2,2)
    source_delta=np.array([s['points'][[0,2]]-s['points'][1] for s in source_chains]).reshape(-1,2,2)
    denominators=np.sum(source_delta*source_delta,axis=(1,2))
    for qi,q in enumerate(query_chains):
        if cancelled and cancelled(): return finish(False,'structure-work-obsolete')
        qdelta=q['points'][[0,2]]-q['points'][1]
        orientations=np.min(np.sum(q['rays']*source_rays,axis=2),axis=1)>=.95
        scales=np.sum(qdelta*source_delta,axis=(1,2))/denominators
        # Pixel discrepancy, not a guessed scale range. Truncated chains
        # can still propose false seeds; other chains must corroborate them.
        errors=np.linalg.norm(qdelta-scales[:,None,None]*source_delta,axis=2).max(axis=1)
        for si in np.flatnonzero(orientations & (scales>0) & (errors<=3)):
            s=source_chains[si]; scale=float(scales[si])
            seeds.append((qi,si,scale,q['points'][1]-scale*s['points'][1]))
            # Bound the quadratic descriptor-consensus work. Overflow is an
            # unresolved search, never uniqueness after truncating hypotheses.
            if len(seeds)>diagnostic['seedBudget']:
                diagnostic['seedCount']=len(seeds)
                return finish(False,'initial-structure-descriptor-budget-unresolved')
    diagnostic['seedCount']=len(seeds)
    diagnostic['stages'].append(dict(stage='chain-descriptors',elapsedMs=(time.perf_counter()-started)*1000))
    if not seeds and not pose_index_candidates:return finish(False,'initial-structure-no-chain-ratios')
    qtriples=np.array([query_chains[q]['points'] for q,s,scale,t in seeds])
    striples=np.array([source_chains[s]['points'] for q,s,scale,t in seeds])
    seen=set(); maximal=[]; candidates=[]
    h,w=reference.shape
    extent=np.array([[0,0,1],[w,0,1],[0,h,1],[w,h,1]])
    consensus_started=time.perf_counter()
    for qi,si,scale,translation in seeds:
        if cancelled and cancelled(): return finish(False,'structure-work-obsolete')
        compatible=np.flatnonzero(np.linalg.norm(striples*scale+translation-qtriples,axis=2).max(axis=1)<=3)
        pairs=set((seeds[j][0],seeds[j][1]) for j in compatible)
        unique=tuple(sorted((q,s) for q,s in pairs if sum(qj==q for qj,sj in pairs)==1 and sum(sj==s for qj,sj in pairs)==1))
        if len(unique)<6 or unique in seen: continue
        seen.add(unique)
        pair_set=frozenset(unique)
        if any(pair_set<=old for old in maximal): continue
        maximal=[old for old in maximal if not old<pair_set]
        maximal.append(pair_set)
    diagnostic['consensusCount']=len(seen)
    # Merge only compatible *identities*, sharing an already distributed
    # six-point correspondence. Similar numeric poses alone cannot join rooms.
    changed=True
    while changed:
        changed=False
        for i,left in enumerate(maximal):
            for j in range(i+1,len(maximal)):
                right=maximal[j]; common=left & right
                if len(common)<6: continue
                positions=np.array([query_chains[q]['points'][1] for q,s in common])
                union=left | right
                if np.min(np.ptp(positions,axis=0))<24: continue
                if len({q for q,s in union})!=len(union) or len({s for q,s in union})!=len(union): continue
                maximal[i]=union; maximal.pop(j); changed=True; break
            if changed: break
    diagnostic['maximalCorrespondenceSets']=len(maximal)
    # A strict subset of identical correspondences is not another location.
    # Fit all the corroborating pairs; never discard them to rescue a bad fit.
    for pair_set in sorted(maximal,key=lambda p:(-len(p),tuple(sorted(p)))):
        if cancelled and cancelled(): return finish(False,'structure-work-obsolete')
        unique=tuple(sorted(pair_set))
        # A completed static contour can propose a wall search, but obscured
        # corners never enter a point fit as measured correspondences.
        if any(not source_chains[s]['measured'] for q,s in unique):continue
        source=np.array([source_chains[s]['points'][1] for q,s in unique])
        observed=np.array([query_chains[q]['points'][1] for q,s in unique])
        if np.min(np.ptp(observed,axis=0))<24: continue
        # Identity is the native author coordinate basis, not a supplied pose.
        # With no scale prior, the H1 relative-scale bound does not apply;
        # its existing observability and disjoint holdout gates still do.
        geometry=fit_axis_locked_point_geometry(source,observed,np.eye(3),local_points=observed,
            query_shape=frame.shape,forward_backward=np.zeros(len(source)),flow_error_values=np.zeros(len(source)),
            initial_count=len(query_chains),component=None,maximum_relative_scale_correction=float('inf'),
            distribute_temporal_support=True)
        if not geometry.get('success') or not geometry.get('scaleObservable'):
            diagnostic['pointRejections'].append(dict(pairs=len(unique),reason=geometry.get('reason'),scaleObservable=geometry.get('scaleObservable'),h1Validation=geometry.get('h1Validation')))
            continue
        matrix=np.asarray(geometry['selected']['matrixSourceToClient'])
        if any(np.linalg.norm((extent@(matrix-c['matrix']).T)[:,:2],axis=1).max()<=3 for c in candidates): continue
        candidates.append(dict(matrix=matrix,geometry=geometry,pairs=[list(p) for p in unique],sourcePoints=source.tolist(),queryPoints=observed.tolist()))
        if len(candidates)>diagnostic['validationBudget']:
            diagnostic['candidateCount']=len(candidates)
            diagnostic['pointCandidates']=[dict(matrix=c['matrix'].tolist(),pairs=c['pairs']) for c in candidates]
            return finish(False,'initial-structure-validation-budget-unresolved')
    # An optional pose index proposes the same author-to-client seed consumed
    # by the ordinary structure solver. Its score never grants a pose: each
    # seed is continuously refined from current structure and then reaches the
    # same current-frame and selected-author validators below.
    diagnostic['poseIndexCandidates']=dict(count=len(pose_index_candidates),validated=[])
    index_seed_rows=[]
    for index,seed in enumerate(pose_index_candidates):
        if cancelled and cancelled():return finish(False,'structure-work-obsolete')
        seed=np.asarray(seed,dtype=np.float64)
        item=dict(index=index,accepted=False)
        if (seed.shape!=(3,3) or not np.isfinite(seed).all()
            or abs(seed[0,1])>1e-8 or abs(seed[1,0])>1e-8
            or seed[0,0]<=0 or abs(seed[0,0]-seed[1,1])>1e-8):
            item['reason']='invalid-isotropic-seed'
            diagnostic['poseIndexCandidates']['validated'].append(item)
            continue
        index_seed_rows.append((index,seed.copy()))
        correspondences=(pose_index_diagnostics or {}).get('correspondences') or []
        pair=correspondences[index] if index<len(correspondences) else {}
        source=np.asarray(pair.get('source',[]),dtype=np.float64).reshape(-1,2)
        observed=np.asarray(pair.get('observed',[]),dtype=np.float64).reshape(-1,2)
        if len(source)>=6 and len(source)==len(observed):
            geometry=fit_axis_locked_point_geometry(source,observed,np.eye(3),local_points=observed,
                query_shape=frame.shape,forward_backward=np.zeros(len(source)),flow_error_values=np.zeros(len(source)),
                initial_count=len(source),component=None,maximum_relative_scale_correction=float('inf'),
                distribute_temporal_support=True)
            selected=geometry.get('selected') or {} if geometry.get('success') and geometry.get('scaleObservable') else {}
            item['geometry']={k:geometry.get(k) for k in ('reason','scaleObservable','h1Validation','heldOut')}
        else:
            geometry=graph_point_geometry(frame,reference,seed,_context=context,allow_scale_recovery=True,
                source_known_boundary=source_known_boundary,source_unknown=source_unknown)
            selected=geometry.get(geometry.get('structureModel')) or {}
            item['geometry']={k:geometry.get(k) for k in ('reason','structureModel','scaleObservable','acceptedPointCount','supportPointCount','validationPointCount')}
        if not selected.get('success'):
            diagnostic['poseIndexCandidates']['validated'].append(item)
            continue
        matrix=np.asarray(selected['matrixSourceToClient'],dtype=np.float64)
        if any(np.linalg.norm((extent@(matrix-c['matrix']).T)[:,:2],axis=1).max()<=3 for c in candidates):
            item['reason']='same-pose-as-structural-candidate'
            diagnostic['poseIndexCandidates']['validated'].append(item)
            continue
        candidates.append(dict(matrix=matrix,geometry=geometry,pairs=[],sourcePoints=[],queryPoints=[],
            poseIndexCandidate=index))
        item.update(accepted=True,matrix=matrix.tolist())
        diagnostic['poseIndexCandidates']['validated'].append(item)
    # Small visible islands can have too few complete chains for disjoint
    # point fitting while still exposing independently separated wall planes.
    # Keep the point gate intact; use a normal-only scale fit with leave-one-
    # plane-out validation for these hypotheses instead of inventing corners.
    qlines=current_part(frame,context,'lines')
    slines=visible_wall_segments(reference,source_known_boundary,nodes=source_nodes)
    qa=np.array([[g[k] for k in ('axis','sign','normal','lo','hi')] for g in qlines]).reshape(-1,5)
    sa=np.array([[g[k] for k in ('axis','sign','normal','lo','hi')] for g in slines]).reshape(-1,5)
    oriented=np.all(qa[:,None,:2]==sa[None,:,:2],axis=2)
    qi,si=np.nonzero(oriented)
    starts=np.r_[0,np.flatnonzero(np.diff(qi))+1] if len(qi) else np.array([],dtype=int)
    arrays=(qa,sa,oriented,(qi,si,starts,qi[starts],qa[qi],sa[si]))
    wall_identities=set()
    wall_fit_cache={}
    raster_samples=_wall_raster_samples(qlines)
    for index,seed in index_seed_rows:
        if cancelled and cancelled():return finish(False,'structure-work-obsolete')
        wall=initial_wall_geometry(qlines,slines,float(seed[0,0]),np.asarray(seed[:2,2]),arrays,
            wall_fit_cache,reference=reference,source_unknown=source_unknown,raster_samples=raster_samples)
        if wall is None:continue
        matrix=np.asarray(wall['matrix'],dtype=np.float64)
        existing=next((c for c in candidates if c.get('poseIndexCandidate')==index),None)
        wall_geometry={'wallScale':{k:v for k,v in wall.items() if k!='matrix'}}
        if existing is not None:
            existing.update(matrix=matrix,geometry=wall_geometry,
                indexedWallRefinement=True)
            for item in diagnostic['poseIndexCandidates']['validated']:
                if item.get('index')==index:
                    item['wallRefinement']={k:v for k,v in wall.items() if k!='matrix'}
                    item['matrix']=matrix.tolist()
                    break
            continue
        if any(np.linalg.norm((extent@(matrix-c['matrix']).T)[:,:2],axis=1).max()<=3 for c in candidates):
            continue
        candidates.append(dict(matrix=matrix,geometry=wall_geometry,pairs=[],sourcePoints=[],queryPoints=[],
            poseIndexCandidate=index,indexedWallRefinement=True))
        for item in diagnostic['poseIndexCandidates']['validated']:
            if item.get('index')==index:
                item.update(accepted=True,indexedWallRefinement=True,
                    wallRefinement={k:v for k,v in wall.items() if k!='matrix'},matrix=matrix.tolist())
                break
    for qi,si,scale,translation in seeds:
        # This exact source chain already belongs to a distributed point
        # solution; rerunning its weaker normal hypothesis adds no identity.
        if cancelled and cancelled():return finish(False,'structure-work-obsolete')
        source_chain=source_chains[si]['points']
        query_chain=query_chains[qi]['points']
        if any(c['pairs'] and np.linalg.norm(source_chain@c['matrix'][:2,:2].T+c['matrix'][:2,2]-query_chain,axis=1).max()<=3 for c in candidates):continue
        wall=initial_wall_geometry(qlines,slines,scale,translation,arrays,wall_fit_cache,reference=reference,source_unknown=source_unknown,raster_samples=raster_samples)
        if wall is None:continue
        # Masked fragments of one directed contour edge can have different
        # median normals (slightly sloped raster walls). Those medians are
        # measurements, not distinct room identities. Keep the stronger
        # complete correspondence set; never merge merely nearby poses.
        identity=tuple(sorted(
            (g['axis'],g['sign'],g['queryEdge'],g['sourceEdge'])
            if wall_correspondence_identity and g.get('queryEdge') is not None and g.get('sourceEdge') is not None
            else (g['axis'],g['sign'],g['queryNormal'],g['sourceNormal'])
            for g in wall['planes']))
        identity=frozenset(identity)
        # A strict subset of the same measured plane correspondences is the
        # same hypothesis with less evidence. Keep the maximal verified fit;
        # different source planes (repeated rooms) remain separate identities.
        if any(identity<=old for old in wall_identities):continue
        subsets={old for old in wall_identities if old<identity}
        if subsets:
            candidates=[c for c in candidates if c.get('wallIdentity') not in subsets]
            wall_identities.difference_update(subsets)
        wall_identities.add(identity)
        matrix=wall['matrix']
        # A dense point solution that explains these same source/query planes
        # already represents this correspondence, with stronger point support.
        # Never average it with a less constrained wall-only estimate.
        if any(c['pairs'] and all(abs(c['matrix'][g['axis'],g['axis']]*g['sourceNormal']+c['matrix'][g['axis'],2]-g['queryNormal'])<=6 for g in wall['planes']) for c in candidates):continue
        if any(np.linalg.norm((extent@(matrix-c['matrix']).T)[:,:2],axis=1).max()<=3 for c in candidates):continue
        candidates.append(dict(matrix=matrix,geometry={'wallScale':{k:v for k,v in wall.items() if k!='matrix'}},pairs=[],sourcePoints=[],queryPoints=[],wallIdentity=identity))
        if len(candidates)>diagnostic['validationBudget']:
            diagnostic['candidateCount']=len(candidates)
            diagnostic['wallCandidates']=[dict(matrix=c['matrix'].tolist(),geometry=c['geometry']) for c in candidates]
            return finish(False,'initial-structure-validation-budget-unresolved')
    diagnostic['candidateCount']=len(candidates)
    diagnostic['stages'].append(dict(stage='scale-consensus',elapsedMs=(time.perf_counter()-consensus_started)*1000))
    accepted=[]; validation_started=time.perf_counter()
    for c in candidates:
        if cancelled and cancelled():return finish(False,'structure-work-obsolete')
        matrix=c['matrix']
        projected=cv2.warpAffine(reference,matrix[:2],(frame.shape[1],frame.shape[0]),flags=cv2.INTER_NEAREST)
        validation_context=_static_validation_context(frame,context,matrix,source_unknown)
        regions=regional_validation(frame,context['query'],projected,_context=validation_context)
        residual=(selected_route_current_frame_residual(frame,route,matrix,maximum_dimension=960,
            route_profile_id=profile,reference_override=validation) if regions['regionalEdgeVerified'] else None)
        passed=bool(regions['regionalEdgeVerified'] and residual and residual.get('verified'))
        diagnostic['candidates'].append(dict(matrix=matrix.tolist(),pointGeometry=c['geometry'],pairs=c['pairs'],sourcePoints=c['sourcePoints'],queryPoints=c['queryPoints'],regions=regions,residual=residual,accepted=passed))
        if passed and not any(np.linalg.norm((extent@(matrix-old[0]).T)[:,:2],axis=1).max()<=3
                for old in accepted):
            accepted.append((matrix,residual,c))
    diagnostic['stages'].append(dict(stage='current-validation',elapsedMs=(time.perf_counter()-validation_started)*1000))
    if cancelled and cancelled():return finish(False,'structure-work-obsolete')
    if len(accepted)!=1: return finish(False,'initial-structure-ambiguous' if accepted else 'initial-structure-current-geometry-failed')
    matrix,residual,selected_candidate=accepted[0]
    # No coordinate conversion occurs after this exact author/frame check.
    # Return its evidence with the selected pose instead of recalculating it.
    return finish(True,'initial-structure-candidate',matrixSourceToClient=matrix,coordinateSpace='author-render-to-client',routeProfileId=profile,
        currentFrameScaleObservation=selected_scale_observation(selected_candidate['geometry'],matrix,matrix,residual,profile),
        selectedRouteCurrentFrameResidual=dict(residual,validationBasis='actual-rendered-profile-structural-mask',poseVerifiedAgainstRenderedProfile=True))


def current_point_geometry(frame, reference, target):
    """Static-to-current patch pairs; no previous pixels or optical flow."""
    from map_observation import map_translation_mask
    current = map_translation_mask(frame, maximum_width=frame.shape[1])
    if current is None:
        return {'success': False, 'reason': 'static-point-query-empty'}
    query = (current[0] > .22).astype(np.uint8)
    projected = cv2.warpAffine(reference, target[:2], (frame.shape[1], frame.shape[0]),
                               flags=cv2.INTER_NEAREST)
    points = cv2.goodFeaturesToTrack(query, 128, .015, 16, blockSize=7)
    if points is None:
        return {'success': False, 'reason': 'static-point-query-empty'}
    expected, observed, scores, roundtrips = [], [], [], []
    for x, y in np.rint(points.reshape(-1, 2)).astype(int):
        radius, search = 12, 48
        if x < radius+search or y < radius+search or x+radius+search >= query.shape[1] or y+radius+search >= query.shape[0]:
            continue
        patch = query[y-radius:y+radius+1, x-radius:x+radius+1]
        roi = projected[y-radius-search:y+radius+search+1, x-radius-search:x+radius+search+1]
        corr = cv2.matchTemplate(roi, patch, cv2.TM_CCOEFF_NORMED)
        _, score, _, (sx, sy) = cv2.minMaxLoc(corr)
        cv2.circle(corr, (sx, sy), 4, -1, -1)
        if score < .55 or score-float(corr.max()) < .025:
            continue
        px, py = x-search+sx, y-search+sy
        source_patch = projected[py-radius:py+radius+1, px-radius:px+radius+1]
        reverse_roi = query[y-radius-search:y+radius+search+1, x-radius-search:x+radius+search+1]
        reverse = cv2.matchTemplate(reverse_roi, source_patch, cv2.TM_CCOEFF_NORMED)
        _, reverse_score, _, reverse_at = cv2.minMaxLoc(reverse)
        roundtrip = float(np.linalg.norm(np.asarray(reverse_at)-search))
        if roundtrip > 2.5 or reverse_score < .55:
            continue
        expected.append([x-search+sx, y-search+sy])
        observed.append([x, y])
        scores.append(score)
        roundtrips.append(roundtrip)
    if len(expected) < 6:
        return {'success': False, 'reason': 'static-point-pairs-insufficient', 'pairs': len(expected)}
    expected, observed = np.asarray(expected), np.asarray(observed)
    source_h = np.column_stack((expected, np.ones(len(expected)))) @ np.linalg.inv(target).T
    source = source_h[:, :2] / source_h[:, 2, None]
    # Static template round trips and correlation errors feed the existing
    # geometric ambiguity calculation; neither value comes from temporal LK.
    result = fit_axis_locked_point_geometry(source, observed, target,
        local_points=observed, query_shape=query.shape,
        forward_backward=np.asarray(roundtrips), flow_error_values=(1-np.asarray(scores))*100,
        initial_count=len(points), component=None,
        started=time.perf_counter(), maximum_relative_scale_correction=0.)
    result['measuredPairs'] = {'source': source.tolist(), 'observed': observed.tolist()}
    return result


def retrieve(reference, query, linear, query_scale):
    """Retain repeated local peaks, then vote on their spatial arrangement."""
    projected = np.asarray(linear, dtype=np.float64) * query_scale
    h, w = reference.shape
    corners = np.array([[0, 0], [w, 0], [0, h], [w, h]]) @ projected.T
    origin = np.floor(corners.min(axis=0))
    size = np.ceil(corners.max(axis=0) - origin).astype(int) + 1
    if max(size) > 4096 or min(size) < 16:
        return [], {'reason': 'structure-index-size-unsupported'}
    affine = np.column_stack((projected, -origin))
    sheet = cv2.warpAffine(reference, affine, tuple(size), flags=cv2.INTER_NEAREST)
    radius = 24
    sheet = cv2.copyMakeBorder(sheet, radius, radius, radius, radius, cv2.BORDER_CONSTANT)
    points = cv2.goodFeaturesToTrack(query, 16, .015, 24, blockSize=7)
    if points is None:
        return [], {'reason': 'structure-query-no-corners'}
    peaks = []
    for index, (x, y) in enumerate(np.rint(points.reshape(-1, 2)).astype(int)):
        x0, x1 = max(0, x-radius), min(query.shape[1], x+radius)
        y0, y1 = max(0, y-radius), min(query.shape[0], y+radius)
        patch = query[y0:y1, x0:x1]
        if patch.sum() < 24 or min(patch.shape) < 16:
            continue
        # Centered correlation also compares the empty space around turns;
        # occupancy-only correlation rewards a corridor inside a solid room.
        scores = cv2.matchTemplate(sheet, patch, cv2.TM_CCOEFF_NORMED)
        for _ in range(8):
            _, score, _, (sx, sy) = cv2.minMaxLoc(scores)
            if score < .55:
                break
            translation = (np.array([x0-sx+radius, y0-sy+radius])-origin) / query_scale
            peaks.append((index, translation, score, (x, y)))
            cv2.circle(scores, (sx, sy), max(4, round(32*query_scale)), -1, -1)
    candidates = []
    for _, translation, _, _ in peaks:
        nearby = [p for p in peaks if np.linalg.norm(p[1]-translation) <= 16]
        unique = {}
        for p in nearby:
            if p[0] not in unique or p[2] > unique[p[0]][2]:
                unique[p[0]] = p
        support = list(unique.values())
        if len(support) < 3:
            continue
        if max(np.linalg.norm(np.array(a[3])-b[3]) for a in support for b in support) < 48:
            continue
        center = np.median([p[1] for p in support], axis=0)
        if any(np.linalg.norm(center-c['translation']) < 24 for c in candidates):
            continue
        candidates.append(dict(translation=center, support=len(support),
                               score=float(np.mean([p[2] for p in support]))))
    candidates.sort(key=lambda c: (c['support'], c['score']), reverse=True)
    return candidates[:8], dict(peaks=len(peaks), candidates=len(candidates),
                                sheetShape=list(sheet.shape), corners=len(points))


def retrieve_native_turns(reference,frame,linear, *, _context=None):
    """Retain native-scale corner constellations in a small visible island."""
    context=_context if _context is not None else current_geometry(frame)
    query=context['query']
    h,w=reference.shape
    bounds=np.array([[0,0],[w,0],[0,h],[w,h]])@linear.T
    origin=np.floor(bounds.min(0))
    size=np.ceil(bounds.max(0)-origin).astype(int)+1
    sheet=cv2.warpAffine(reference,np.column_stack((linear,-origin)),tuple(size),flags=cv2.INTER_NEAREST)
    qnodes=[n for n in current_part(frame,context,'nodes') if n['valid']]
    snodes=[n for n in contour_graph(sheet) if n['valid']]
    votes=[]
    source_rays=np.array([s['rays'] for s in snodes]).reshape(-1,2,2)
    for qi,q in enumerate(qnodes):
        compatible=np.flatnonzero(np.min(np.sum(source_rays*q['rays'],axis=2),axis=1)>=.95)
        for si in compatible:
            votes.append((qi,int(si),q['point']-snodes[si]['point']-origin))
    candidates=[]
    vote_positions=np.array([v[2] for v in votes])
    buckets={}
    for i,position in enumerate(vote_positions):
        key=tuple(np.floor(position/8).astype(int))
        buckets.setdefault(key,[]).append(i)
    for qi,si,translation in votes:
        bx,by=np.floor(translation/8).astype(int)
        indexes=np.array(sorted(i for dx in (-1,0,1) for dy in (-1,0,1) for i in buckets.get((bx+dx,by+dy),[])),dtype=int)
        distances=np.sum((vote_positions[indexes]-translation)**2,axis=1)
        unique={}
        nearest={}
        for i,distance in zip(indexes[distances<=64],distances[distances<=64]):
            qj,sj,t=votes[i]
            if qj not in unique or distance<nearest[qj]:
                unique[qj]=t
                nearest[qj]=distance
        if len(unique)<3: continue
        points=np.array([qnodes[qj]['point'] for qj in unique])
        if np.linalg.norm(np.ptp(points,axis=0))<48: continue
        center=np.median(list(unique.values()),axis=0)
        if any((float(center[0]-c['translation'][0])**2+float(center[1]-c['translation'][1])**2)<24**2 for c in candidates): continue
        candidates.append(dict(translation=center,support=len(unique),score=1.,origin='native-turn-constellation'))
    candidates.sort(key=lambda c:c['support'],reverse=True)
    return candidates[:8],dict(queryTurns=len(qnodes),sourceTurns=len(snodes),votes=len(votes),candidates=len(candidates))


def visible_wall_segments(mask, observed=None, *, nodes=None):
    """Directed straight fragments; fog may truncate a wall's endpoints."""
    nodes=contour_graph(mask) if nodes is None else nodes
    segments=[]
    for edge_id,node in enumerate(nodes):
        p=node['point']; end=nodes[node['next']]['point']; delta=end-p
        axis=int(abs(delta[0])<abs(delta[1])) # tangent axis
        length=abs(delta[axis])
        if length<12 or abs(delta[1-axis])>3: continue
        count=int(np.ceil(length))+1
        points=p+np.linspace(0,1,count)[:,None]*delta
        valid=np.ones(count,dtype=bool)
        if observed is not None:
            xy=np.rint(points).astype(int)
            valid=observed[xy[:,1],xy[:,0]]
        indexes=np.flatnonzero(valid)
        if not len(indexes): continue
        for group in np.split(indexes,np.flatnonzero(np.diff(indexes)>1)+1):
            if len(group)<12: continue
            sample=points[group]
            segments.append(dict(axis=1-axis,sign=int(np.sign(delta[axis])),edgeId=edge_id,
                normal=float(np.median(sample[:,1-axis])),lo=float(sample[:,axis].min()),hi=float(sample[:,axis].max())))
    return segments


def explicit_wall_segments(reference, wall_mask, observed=None):
    """Add measured internal wall faces lost by occupancy contour simplification.

    Thin wall slots can collapse under polygon approximation. Connected directed
    raster faces retain their door gaps and orientation without filling them.
    """
    from nightmare_candidate import _wall_normal_support
    lines=visible_wall_segments(reference,observed)
    divided=reference & (wall_mask==0).astype(np.uint8)
    for direction,field in enumerate(_wall_normal_support(divided)):
        if observed is not None:field=field & observed
        count,labels,stats,_=cv2.connectedComponentsWithStats(field.astype(np.uint8),8)
        axis=0 if direction<2 else 1
        sign=(1 if direction==0 else -1) if axis==0 else (1 if direction==3 else -1)
        for index in range(1,count):
            x,y,w,h,area=stats[index]
            if area<12 or (w if axis==0 else h)>4:continue
            yy,xx=np.nonzero(labels[y:y+h,x:x+w]==index)
            normal=(xx+x) if axis==0 else (yy+y)
            tangent=(yy+y) if axis==0 else (xx+x)
            if np.ptp(tangent)<11:continue
            row=dict(axis=axis,sign=sign,normal=float(np.median(normal)),
                lo=float(tangent.min()),hi=float(tangent.max()))
            if any(old['axis']==axis and old['sign']==sign
                and abs(old['normal']-row['normal'])<=3
                and min(old['hi'],row['hi'])>=max(old['lo'],row['lo']) for old in lines):continue
            lines.append(row)
    return lines


def fit_partial_wall_translation(groups):
    """Current spatial order fixes support/holdout before fitting residuals."""
    correction=[]; heldout=[]; partitions=[]
    for axis in (0,1):
        planes=sorted((g for g in groups if g['axis']==axis),key=lambda g:g['queryNormal'])
        train,validate=planes[::2],planes[1::2]
        if min(len(train),len(validate))<2:
            return dict(success=False,reason='wall-refinement-disjoint-support-insufficient')
        if min(np.ptp([p['queryNormal'] for p in subset]) for subset in (train,validate))<24:
            return dict(success=False,reason='wall-refinement-disjoint-spread-insufficient')
        shift=float(np.median([p['delta'] for p in train]))
        correction.append(shift)
        heldout.extend(abs(p['delta']-shift) for p in validate)
        partitions.append(dict(axis=axis,support=[p['queryNormal'] for p in train],
            holdout=[p['queryNormal'] for p in validate]))
    validation=dict(count=len(heldout),medianPixels=float(np.median(heldout)),
        p90Pixels=float(np.percentile(heldout,90)),maxPixels=float(np.max(heldout)))
    return dict(success=validation['medianPixels']<=3 and validation['p90Pixels']<=8 and validation['maxPixels']<=20,
        correction=correction,partitions=partitions,heldout=validation)


def fit_partial_wall_similarity(groups,native_lines,target,*,require_holdout=True):
    """Fit one continuous scale and translation from unambiguous native walls.

    Source coordinates stay floating point instead of inheriting warp rounding.
    Spatially alternating wall planes fix support/holdout before residual fitting.
    Speed-first recovery may fit all planes and report fitting residuals instead;
    its caller still checks the actual author route before committing the pose.
    """
    if not np.allclose(target[:2,:2],np.eye(2)*target[0,0]) or target[0,0]<=0:
        return dict(success=False,reason='wall-scale-axis-model-unavailable')
    planes=[]
    for group in groups:
        axis=group['axis'];tangent=1-axis;matches=[]
        for wall in native_lines:
            if wall['axis']!=axis or wall['sign']!=group['sign']:continue
            normal=wall['normal']*target[axis,axis]+target[axis,2]
            lo=wall['lo']*target[tangent,tangent]+target[tangent,2]
            hi=wall['hi']*target[tangent,tangent]+target[tangent,2]
            if abs(normal-group['sourceNormal'])<=3 and min(hi,group['sourceSpan'][1])-max(lo,group['sourceSpan'][0])>=12:
                matches.append(normal)
        if len(matches)==1:
            planes.append(dict(group,sourceNormal=matches[0],delta=group['queryNormal']-matches[0]))
    train=[];hold=[]
    for axis in (0,1):
        ordered=sorted((p for p in planes if p['axis']==axis),key=lambda p:p['queryNormal'])
        support,validation=(ordered[::2],ordered[1::2]) if require_holdout else (ordered,[])
        subsets=(support,validation) if require_holdout else (support,)
        if min(map(len,subsets))<2:
            return dict(success=False,reason='wall-scale-disjoint-support-insufficient')
        if min(np.ptp([p['queryNormal'] for p in subset]) for subset in subsets)<24:
            return dict(success=False,reason='wall-scale-disjoint-spread-insufficient')
        train.extend(support);hold.extend(validation)
    center=np.array([np.median([p['sourceNormal'] for p in train if p['axis']==a]) for a in (0,1)])
    def design(items):
        return np.array([[p['sourceNormal']-center[p['axis']],int(p['axis']==0),int(p['axis']==1)] for p in items])
    beta,_,rank,_=np.linalg.lstsq(design(train),np.array([p['delta'] for p in train]),rcond=None)
    evaluation=hold if require_holdout else train
    errors=np.abs(np.array([p['delta'] for p in evaluation])-design(evaluation)@beta)
    validation=dict(count=len(errors),medianPixels=float(np.median(errors)),
        p90Pixels=float(np.percentile(errors,90)),maxPixels=float(np.max(errors)))
    # Same small uniform-scale range as the existing axis-locked point solver.
    accepted=bool(rank==3 and np.isfinite(beta).all() and abs(beta[0])<=.08
        and validation['medianPixels']<=3 and validation['p90Pixels']<=8 and validation['maxPixels']<=20)
    refined=target.copy();refined[:2,:2]*=1+beta[0]
    refined[:2,2]=(1+beta[0])*target[:2,2]+beta[1:]-beta[0]*center
    return dict(success=accepted,reason='wall-native-uniform-scale',matrix=refined.tolist(),
        correction=beta.tolist(),heldout=validation if require_holdout else None,
        support=train,holdout=hold,**({'fittingResiduals':validation} if not require_holdout else {}))


def partial_wall_geometry(frame,reference,target, *, _context=None, source_known_boundary=None, source_unknown=None,
                          _initial_projection=None, _initial_regions=None, _source_lines=None, source_wall_mask=None,
                          _matching_source_lines=None, _fit_only=False, _allow_scale_recovery=True):
    """Independently verify a turn-constellation pose against wall normals.

    At least two separated wall planes constrain each translation component.
    Every matched plane is checked against a translation estimated without
    that plane; clipped endpoints never become fictitious point matches.
    """
    context=_context if _context is not None else current_geometry(frame)
    query=context['query']
    qlines=current_part(frame,context,'lines')
    if _initial_projection is None:
        projection=projected_geometry(frame,reference,target,context)
    else:
        projection=context.get('_projection')
        if (projection is None or projection['reference'] is not reference
            or not np.array_equal(projection['matrix'],target)
            or projection['mask'] is not _initial_projection):
            projection=dict(reference=reference,matrix=target.copy(),mask=_initial_projection)
            context['_projection']=projection
    projected=projection['mask']
    if source_wall_mask is not None:
        native=_matching_source_lines if _matching_source_lines is not None else _source_lines
        if native is None:
            native=explicit_wall_segments(reference,source_wall_mask,source_known_boundary)
        slines=[dict(line,normal=line['normal']*target[line['axis'],line['axis']]+target[line['axis'],2],
            lo=line['lo']*target[1-line['axis'],1-line['axis']]+target[1-line['axis'],2],
            hi=line['hi']*target[1-line['axis'],1-line['axis']]+target[1-line['axis'],2]) for line in native]
    else:
        # Native walls above replace the projected contour entirely. Build
        # legacy fragments only when they are the actual matching source.
        if 'nodes' not in projection:projection['nodes']=contour_graph(projected)
        known=_projected_known(frame,target,source_known_boundary,source_unknown)
        slines=visible_wall_segments(projected,known,nodes=projection['nodes'])
    groups=[]
    for q in qlines:
        compatible=[s for s in slines if s['axis']==q['axis'] and s['sign']==q['sign']
            and (min(q['hi'],s['hi'])-max(q['lo'],s['lo'])+1>=min(12,q['hi']-q['lo']-1,s['hi']-s['lo']-1)
                 if source_wall_mask is not None else
                 min(q['hi'],s['hi'])-max(q['lo'],s['lo'])>=min(12,q['hi']-q['lo']-1))
            and abs(q['normal']-s['normal'])<=48]
        if not compatible: continue
        # Inclusive pixel spans cannot require more overlap than either short
        # measured fragment contains. Native walls keep fractional endpoints.
        s=min(compatible,key=lambda s:abs(q['normal']-s['normal']))
        item=dict(axis=q['axis'],sign=q['sign'],queryNormal=q['normal'],sourceNormal=s['normal'],delta=q['normal']-s['normal'],
            observedSpan=[q['lo'],q['hi']],sourceSpan=[s['lo'],s['hi']])
        # Multiple fragments of one wall are one piece of evidence.
        if any(g['axis']==item['axis'] and g['sign']==item['sign'] and abs(g['queryNormal']-item['queryNormal'])<=3 for g in groups): continue
        groups.append(item)
    diagnostics=dict(querySegments=len(qlines),sourceSegments=len(slines),planes=groups)
    errors=[]
    for axis in (0,1):
        planes=[g for g in groups if g['axis']==axis]
        if len(planes)<2 or np.ptp([p['queryNormal'] for p in planes])<24:
            return dict(success=False,reason='partial-wall-independent-planes-insufficient',diagnostics=diagnostics)
        deltas=np.array([p['delta'] for p in planes])
        errors.extend(abs(deltas[i]-np.median(np.delete(deltas,i))) for i in range(len(deltas)))
    errors=np.array(errors)
    validation=dict(count=len(errors),medianPixels=float(np.median(errors)),p90Pixels=float(np.percentile(errors,90)),maxPixels=float(errors.max()))
    diagnostics['heldOutNormals']=validation
    relative_failed=validation['medianPixels']>3 or validation['p90Pixels']>8 or validation['maxPixels']>20
    # The native turn constellation remains the pose estimator. Wall planes
    # verify its absolute error as well as their independent relative spacing;
    # they do not move a pose based on arbitrary clipped endpoints.
    absolute=np.abs([g['delta'] for g in groups])
    diagnostics['candidateNormals']=dict(medianPixels=float(np.median(absolute)),
        p90Pixels=float(np.percentile(absolute,90)),maxPixels=float(np.max(absolute)))
    absolute_failed=np.median(absolute)>3 or np.percentile(absolute,90)>8 or np.max(absolute)>20
    if _fit_only and _allow_scale_recovery and source_wall_mask is not None and (relative_failed or absolute_failed):
        # Keep successful fixed-scale following unchanged. When its residuals
        # fail, solve scale from these same correspondences before rejecting.
        similarity=fit_partial_wall_similarity(groups,native,target,require_holdout=False)
        diagnostics['scaleRefinement']=similarity
        if similarity['success']:
            diagnostics['validationPolicy']='wall-similarity-then-author'
            return dict(success=True,reason='partial-wall-similarity-speed-first',
                matrix=np.asarray(similarity['matrix'],dtype=float),diagnostics=diagnostics)
    if relative_failed:
        return dict(success=False,reason='partial-wall-held-out-failed',diagnostics=diagnostics)
    if absolute_failed:
        return dict(success=False,reason='partial-wall-candidate-failed',diagnostics=diagnostics)
    if _fit_only:
        refinement=fit_partial_wall_translation(groups)
        diagnostics.update(refinement=refinement,validationPolicy='wall-fit-then-author')
        if refinement['success']:
            target=target.copy();target[:2,2]+=refinement['correction']
        return dict(success=True,reason='partial-wall-fit-speed-first',matrix=target,diagnostics=diagnostics)
    diagnostics.update(_initial_regions if _initial_regions is not None else
        regional_validation(frame,query,projected,_context=_static_validation_context(frame,context,target,source_unknown)))
    scale_accepted=False;scale_raster=None
    if groups and _source_lines is not None:
        similarity=fit_partial_wall_similarity(groups,_source_lines,target)
        diagnostics['scaleRefinement']=similarity
        if similarity['success']:
            refined=np.asarray(similarity['matrix'],dtype=float)
            scale_projection=cv2.warpAffine(reference,refined[:2],frame.shape[1::-1],flags=cv2.INTER_NEAREST)
            regions=regional_validation(frame,query,scale_projection,
                _context=_static_validation_context(frame,context,refined,source_unknown))
            raster=(initial_wall_raster_validation(qlines,reference,[refined[0,0],refined[0,2],refined[1,2]],source_unknown=source_unknown,source_wall_mask=source_wall_mask)
                if source_unknown is not None else None)
            scale_accepted=bool(regions['regionalEdgeVerified'] and (raster is None or raster['verified']))
            similarity.update(accepted=scale_accepted,regions=regions,knownWallRaster=raster)
            if scale_accepted:
                diagnostics['beforeRefinementRegions']=diagnostics['regions']
                diagnostics.update(regions);target=refined;scale_raster=raster
    if groups and not scale_accepted:
        refinement=fit_partial_wall_translation(groups)
        diagnostics['refinement']=refinement
        if refinement['success']:
            refined=target.copy(); refined[:2,2]+=refinement['correction']
            projected=projected_geometry(frame,reference,refined,context)['mask']
            diagnostics['beforeRefinementRegions']=diagnostics['regions']
            diagnostics.update(regional_validation(frame,query,projected,_context=_static_validation_context(frame,context,refined,source_unknown)))
            refinement['translation']=refined[:2,2].tolist()
            if diagnostics['regionalEdgeVerified']:
                target=refined
    if source_unknown is not None:
        raster=scale_raster if scale_raster is not None else initial_wall_raster_validation(qlines,reference,[target[0,0],target[0,2],target[1,2]],source_unknown=source_unknown,source_wall_mask=source_wall_mask)
        diagnostics['knownWallRaster']=raster
        if not raster['verified']:return dict(success=False,reason='partial-known-wall-conflict',diagnostics=diagnostics)
    return dict(success=diagnostics['regionalEdgeVerified'],
        reason='partial-wall-native-similarity' if scale_accepted else 'partial-wall-fixed-linear',
        matrix=target,diagnostics=diagnostics)


def solve(assistant, frame, map_id, floor, profile, route, *, cancelled=None, _context=None):
    from alignment_observation import rendered_profile_residual_is_verified
    started = time.perf_counter()
    assistant._single_map_structure_diagnostics = None
    group = assistant._active_round_layer_group(map_id)
    view = assistant._round_group_floor_view(map_id, floor, render_overlay=False) if group else None
    shared = bool(getattr(assistant, '_shared_structure_mode_enabled', lambda: False)())
    failure = dict(success=False, reason='structure-no-reliable-scale')
    obsolete = dict(success=False, reason='structure-work-obsolete')
    if cancelled and cancelled(): return obsolete
    if not view or not group.verified_floors or (not shared and group.route_profile_id != profile):
        return failure
    # Shared products and route pixels are in the native reference domain.
    # Author display matrices are static adapters, never solver coordinates.
    base = np.asarray(view['recognitionMatrixSourceToClient'] if shared else view['matrixSourceToClient'], dtype=np.float64)
    product = assistant._author_route_clean_product_for_route(route, profile, map_id, floor)
    context=_context if _context is not None else current_geometry(frame)
    if product is None or product.clean_mask is None:
        return dict(failure, reason='structure-clean-resource-unavailable')
    static_args={}
    if getattr(product,'known_boundary',None) is not None:
        static_args=dict(source_known_boundary=product.known_boundary,source_unknown=product.unknown_mask)
    wall_args=dict(static_args)
    translation_only=str(floor) in group.verified_floors
    if static_args:
        wall_args['source_wall_mask']=product.known_boundary
        wall_args['_fit_only']=True
        wall_args['_allow_scale_recovery']=not translation_only
        locator=assistant._alignment_entry_locator() if hasattr(assistant,'_alignment_entry_locator') else getattr(getattr(assistant,'engine',None),'entry_identity',None)
        if locator is not None:
            wall_args['_matching_source_lines']=locator.native_wall_lines(product,map_id,floor)
    if floor not in group.verified_floors and static_args:
        locator=assistant._alignment_entry_locator() if hasattr(assistant,'_alignment_entry_locator') else getattr(getattr(assistant,'engine',None),'entry_identity',None)
        if locator is not None:
            wall_args['_source_lines']=locator.native_wall_lines(product,map_id,floor)
    validation, _, _ = assistant._author_route_structure_reference_cached(
        route, profile, map_id=map_id, floor=floor)
    if validation is None:
        return dict(failure, reason='structure-validation-resource-unavailable')
    if context is None:
        # The capture consumer owns map/floor presence. Missing extracted
        # structure on an open map is unknown geometry, not a pose rejection.
        return dict(failure, reason='structure-query-unavailable',
            sharedProjectionGeometryInsufficient=True)
    query_scale=min(1.,640./frame.shape[1])
    query=context['query']
    if query_scale<1:
        query=cv2.resize(query,(max(1,round(frame.shape[1]*query_scale)),max(1,round(frame.shape[0]*query_scale))),interpolation=cv2.INTER_AREA)
    query=(query>.22).astype(np.uint8)
    candidates=[]
    diagnostics=dict(searchStages=[])
    accepted = []
    height,width=route.shape[:2]
    extent=np.asarray([[0,0,1],[width,0,1],[0,height,1],[width,height,1]])
    def current_residual(matrix):
        if shared:
            # Geometry and its display-coordinate provenance must travel
            # together. Raw mask residuals have no reviewed bridge/pack and
            # cannot satisfy the unchanged V2 consumer contract.
            residual, _ = assistant._canonical_geometry_current_frame_residual(
                frame, map_id, floor, 'shared-structure', matrix, route,
                maximum_dimension=960)
            return residual
        return selected_route_current_frame_residual(frame, route, matrix,
            maximum_dimension=960, route_profile_id=profile, reference_override=validation)

    def residual_verified(residual):
        return (rendered_profile_residual_is_verified(residual) if shared
                else residual.get('verified') is True)

    def validate(candidate):
        target = np.asarray(candidate.get('seedMatrix',base),dtype=np.float64).copy()
        if (target.shape!=(3,3) or not np.isfinite(target).all()
            or abs(target[0,1])>1e-8 or abs(target[1,0])>1e-8
            or target[0,0]<=0 or abs(target[0,0]-target[1,1])>1e-8):
            candidate['validationFailure']='invalid-isotropic-seed'
            return
        target[:2, 2] = candidate['translation']
        candidate['refinedTranslation'] = target[:2, 2].tolist()
        geometry = graph_point_geometry(frame, product.clean_mask, target,_context=context,
            allow_scale_recovery=not translation_only,**static_args)
        selected = geometry.get(geometry.get('structureModel')) or {}
        candidate['pointGeometry'] = {k: geometry.get(k) for k in
            ('reason', 'graph', 'pairs', 'acceptedPointCount', 'supportPointCount', 'validationPointCount', 'h0Validation', 'h1Validation', 'structureModel', 'scaleObservable', 'scaleFactor')}
        if selected.get('success'):
            target = np.asarray(selected['matrixSourceToClient'])
            candidate['refinedTranslation'] = target[:2, 2].tolist()
        point_target=target.copy()
        geometry_verified=selected.get('success') and geometry['graph']['regionalEdgeVerified']
        support=int(geometry.get('acceptedPointCount') or 0)
        # A point proposal can pass a coarse regional check while retaining
        # a biased translation. Independent wall-normal holdouts refine its
        # position as well; regional visibility alone is not subpixel tracking.
        partial=partial_wall_geometry(frame,product.clean_mask,target,_context=context,**wall_args)
        candidate['partialWallGeometry']={k:v for k,v in partial.items() if k!='matrix'}
        if wall_args.get('source_wall_mask') is not None:
            # A coarse graph proposal cannot overrule explicit current-wall
            # contradictions that also govern entry and reopened candidates.
            geometry_verified=bool(partial['success'])
        wall_holdout = (partial.get('diagnostics') or {}).get('heldOutNormals') or {}
        wall_absolute = (partial.get('diagnostics') or {}).get('candidateNormals') or {}
        wall_author_recovery = bool(
            translation_only and wall_args.get('source_wall_mask') is not None
            and ((partial.get('reason')=='partial-wall-held-out-failed'
                  and wall_holdout.get('medianPixels',float('inf'))<=3
                  and wall_holdout.get('p90Pixels',float('inf'))<=8)
                 or (partial.get('reason')=='partial-wall-candidate-failed'
                     and wall_holdout.get('maxPixels',float('inf'))<=8
                     and wall_absolute.get('maxPixels',float('inf'))<=8)))
        if partial['success']:
            target=partial['matrix']
            candidate['refinedTranslation']=target[:2,2].tolist()
            geometry_verified=True
            support=len(partial['diagnostics']['planes'])
        residual = (current_residual(target)
            if geometry_verified or wall_author_recovery else dict(verified=False,reason='not-evaluated-geometry-failed'))
        candidate['residual'] = residual
        if wall_author_recovery and residual_verified(residual):
            # One wrong nearest wall or a small consistent candidate offset
            # need not hide a route whose complete author structure explains
            # this current frame. This never authorizes by wall score alone.
            geometry_verified=True
            candidate['wallAuthorRecovery']={'reason':partial['reason'],
                'heldOut':wall_holdout,'candidateNormals':wall_absolute}
        if geometry_verified and residual_verified(residual):
            candidate['support']=max(candidate['support'],support)
            candidate['currentFrameScaleObservation']=selected_scale_observation(
                geometry,point_target,target,residual,profile,wall_geometry=partial)
            # H1 proposals may differ in scale; translation proximity alone
            # must not collapse distinct poses into one accepted hypothesis.
            if not any(np.linalg.norm((extent@(target-other[0]).T)[:,:2],axis=1).max()<24 for other in accepted):
                accepted.append((target, candidate))
    # The shared committed pose is the only retained prior. The existing graph
    # searches current/source corners within 48px; no previous pixels are used.
    # Structural commits keep the verified pose but may clear render-profile
    # display metadata. A search prior must not depend on that display flag.
    if floor in group.verified_floors or (
        floor in {'1', '2'}
        and bool(group.canonical_geometry_verified_floors & {'1', '2'})
        and floor in group.relations
    ):
        if cancelled and cancelled(): return obsolete
        phase_started=time.perf_counter()
        displayed=getattr(assistant,'current_alignment_candidate',None) or {}
        prior_frame=displayed.get('verifiedFrame')
        prior_matrix=np.asarray(displayed.get('matrixSourceToClient',[]))
        if (displayed.get('accepted') and displayed.get('mapId')==map_id
            and displayed.get('floor')==str(floor) and displayed.get('routeProfileId')==profile
            and displayed.get('cycleId')==getattr(assistant,'active_map_cycle_id',None)
            and prior_matrix.shape==(3,3) and np.allclose(prior_matrix,base,atol=1e-7,rtol=0)
            and isinstance(prior_frame,np.ndarray) and prior_frame.shape==frame.shape
            and not np.array_equal(prior_frame,frame)
            and not assistant._open_cycle_transient_motion_active(map_id)):
            # The existing phase/containment solver proposes a bounded
            # translation from verified pixels. It does not grant display;
            # the same geometry/author validators below still decide.
            from alignment_observation import phase_relocalize_translation_masks
            source_context=displayed.setdefault('verifiedFrameContext',{})
            if 'localTranslationQuery' not in source_context:
                previous=current_geometry(prior_frame)
                old_query=previous['query'] if previous is not None else None
                if old_query is not None and query_scale<1:
                    old_query=cv2.resize(old_query,(query.shape[1],query.shape[0]),interpolation=cv2.INTER_AREA)
                source_context['localTranslationQuery']=(old_query>.22).astype(np.float32) if old_query is not None else None
            old_query=source_context['localTranslationQuery']
            motion=phase_relocalize_translation_masks(
                (old_query,query_scale) if old_query is not None else None,
                (query.astype(np.float32),query_scale),allow_containment=False)
            diagnostics['localTranslationProposal']=motion
            if motion.get('tracked') and max(abs(motion['dx']),abs(motion['dy']))<=48:
                proposal=dict(translation=base[:2,2]+[motion['dx'],motion['dy']],support=0,
                    score=motion.get('confidence',0.),origin='verified-frame-local-translation')
                candidates.append(proposal);validate(proposal)
            if cancelled and cancelled():return obsolete
        local=dict(translation=base[:2,2].copy(),support=0,score=1.,origin=(
            'committed-pose-local' if floor in group.verified_floors else 'shared-root-local'))
        if not accepted:
            candidates.append(local); validate(local)
        diagnostics['searchStages'].append(dict(stage='local',elapsedMs=(time.perf_counter()-phase_started)*1000,accepted=len(accepted)))
    if not accepted and not translation_only and str(floor)=='1':
        # Keep a successful local/follow solve free of entry work. If its old
        # scale fails, the confirmed member's entry supplies a fresh continuous
        # similarity before repeating the broad old-scale translation search.
        locator=assistant._alignment_entry_locator() if hasattr(assistant,'_alignment_entry_locator') else getattr(getattr(assistant,'engine',None),'entry_identity',None)
        surface_generation=getattr(assistant,'surface_visibility_generation',0)
        if locator is not None:
            if cancelled and cancelled():return obsolete
            try:
                rows,entry_diagnostic=locator.rank(frame,floor,map_id=map_id,route=route)
            except (ValueError,TypeError,KeyError,OSError,cv2.error):
                rows=[];entry_diagnostic={'reason':'entry-resource-unavailable'}
            if ((cancelled and cancelled()) or getattr(assistant,'surface_visibility_generation',0)!=surface_generation
                or assistant._open_cycle_transient_motion_active(map_id)):
                return obsolete
            diagnostics['entryRecovery']=entry_diagnostic
            selected=[row for row in rows if row.get('poseVerified')]
            if len(selected)==1:
                checked=selected[0]['verification']
                if shared:
                    residual = current_residual(np.asarray(checked['matrixSourceToClient']))
                    if cancelled and cancelled(): return obsolete
                    if not residual_verified(residual):
                        return dict(failure, reason='structure-current-geometry-failed')
                    checked = dict(checked, residual=residual)
                assistant._single_map_structure_diagnostics=diagnostics
                return dict(success=True,reason='difficult-entry-scale-relocalization',
                    matrixSourceToClient=np.asarray(checked['matrixSourceToClient']),
                    coordinateSpace='shared-structure-to-client' if shared else 'author-render-to-client',
                    routeProfileId='shared-structure' if shared else profile,
                    selectedRouteCurrentFrameResidual=dict(checked['residual'],
                        validationBasis='shared-canonical-geometry-current-frame' if shared else 'actual-rendered-profile-structural-mask',
                        poseVerifiedAgainstRenderedProfile=not shared,
                        **(dict(poseVerifiedAgainstCanonicalGeometry=True,poseVerifiedAgainstSharedStructure=True) if shared else {})),
                    difficultEntryValidation=checked)
    if not accepted and bool(getattr(assistant,'settings',{}).get(
            'poseIndexCandidatesEnabled',getattr(assistant,'settings',{}).get('difficultGridEnabled',False))):
        engine=getattr(assistant,'engine',None)
        propose=getattr(engine,'pose_index_candidates',None)
        if callable(propose):
            if cancelled and cancelled():return obsolete
            try:
                matrices,index_diagnostics=propose(frame,map_id,floor,cancelled=cancelled,context=context)
            except (ValueError,TypeError,KeyError,OSError,cv2.error) as error:
                matrices=[];index_diagnostics=dict(reason='pose-index-error',error=str(error))
            diagnostics['poseIndexCandidates']=index_diagnostics
            for index,matrix in enumerate(matrices):
                if cancelled and cancelled():return obsolete
                seed=np.asarray(matrix,dtype=np.float64)
                candidate=dict(seedMatrix=seed,translation=seed[:2,2].copy(),support=0,
                    score=float((index_diagnostics.get('candidateCount') or len(matrices))-index),
                    origin='indexed-structure-pose')
                candidates.append(candidate);validate(candidate)
                if len(accepted)>1:break
    if not accepted:
        if cancelled and cancelled(): return obsolete
        phase_started=time.perf_counter()
        global_candidates,global_diagnostics=retrieve(product.clean_mask,query,base[:2,:2],query_scale)
        diagnostics['globalRecall']=global_diagnostics
        candidates.extend(global_candidates)
        for candidate in global_candidates:
            if cancelled and cancelled(): return obsolete
            validate(candidate)
            if accepted and len(accepted)>1:break
        if not accepted:
            if cancelled and cancelled(): return obsolete
            native,native_diagnostics=retrieve_native_turns(product.clean_mask,frame,base[:2,:2],_context=context)
            diagnostics['nativeRecall']=native_diagnostics
            extra=[c for c in native if not any(np.linalg.norm(c['translation']-old['translation'])<8 for old in global_candidates)]
            candidates.extend(extra)
            for candidate in extra:
                if cancelled and cancelled(): return obsolete
                validate(candidate)
                if accepted and len(accepted)>1:break
        diagnostics['searchStages'].append(dict(stage='expanded',elapsedMs=(time.perf_counter()-phase_started)*1000,accepted=len(accepted)))
    diagnostics.update(elapsedMs=round((time.perf_counter()-started)*1000, 2),
                       proposals=[dict(c, translation=c['translation'].tolist()) for c in candidates])
    assistant._single_map_structure_diagnostics = diagnostics
    if len(accepted) != 1:
        local = next((c for c in candidates if c.get('origin') in {
            'committed-pose-local', 'shared-root-local'}), {})
        partial = local.get('partialWallGeometry') or {}
        # Use the existing two separated planes per axis requirement on the
        # observation itself: many fragments of one wall are still insufficient.
        lines = current_part(frame, context, 'lines')
        observed_axes = [[line['normal'] for line in lines if line['axis']==axis]
                         for axis in (0, 1)]
        planes_observable = all(len(values)>=2 and np.ptp(values)>=24 for values in observed_axes)
        insufficient = bool(not accepted
            and partial.get('reason') == 'partial-wall-independent-planes-insufficient'
            and not planes_observable
            and (local.get('pointGeometry') or {}).get('reason') == 'graph-adjacent-pairs-insufficient')
        return dict(failure, reason='structure-ambiguous' if len(accepted)>1 else 'structure-current-geometry-failed',
            sharedProjectionGeometryInsufficient=insufficient)
    target, candidate = accepted[0]
    residual=candidate['residual']
    if shared:
        return dict(success=True, matrixSourceToClient=target,
            coordinateSpace='shared-structure-to-client', routeProfileId='shared-structure',
            inliers=candidate['support'], structureProposalOrigin=candidate.get('origin'),
            structureModel=(candidate.get('pointGeometry') or {}).get('structureModel'),
            currentFrameScaleObservation=candidate.get('currentFrameScaleObservation'),
            selectedRouteCurrentFrameResidual=dict(residual,
                validationBasis='shared-canonical-geometry-current-frame',
                poseVerifiedAgainstCanonicalGeometry=True, poseVerifiedAgainstSharedStructure=True,
                poseVerifiedAgainstRenderedProfile=False))
    # validate() measured the actual selected author route at this exact
    # matrix. Preserve that evidence through the shared consumer for every mode.
    residual=dict(residual,validationBasis='actual-rendered-profile-structural-mask',
        poseVerifiedAgainstRenderedProfile=True)
    # This result is already in author/render coordinates; do not apply the
    # neutral resource adapter a second time in the common consumer.
    return dict(success=True, matrixSourceToClient=target, inliers=candidate['support'],
        structureProposalOrigin=candidate.get('origin'),
        structureModel=(candidate.get('pointGeometry') or {}).get('structureModel'),
        currentFrameScaleObservation=candidate.get('currentFrameScaleObservation'),
        selectedRouteCurrentFrameResidual=residual,
        _v2WorkerBaseRender=base,
        _v2WorkerBaseCanonical=np.asarray(view['canonicalMatrixSourceToClient']),
        _v2WorkerValidationMatrix=target, _v2WorkerRenderMatrix=target,
        _v2WorkerRoute=route, _v2WorkerCrossFloor=False,
        _v2WorkerStructural=True, _v2WorkerStructuralLandmarks=[{'score': candidate['score']}],
        _v2WorkerStructuralAmbiguity=0.)
