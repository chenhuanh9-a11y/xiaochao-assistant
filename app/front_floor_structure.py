"""Current corner-layout witnesses against original, unregistered floor drawings."""
import json
from functools import lru_cache
from pathlib import Path
import numpy as np


def directions(chains):
    delta=chains[:,[0,2]]-chains[:,1,None]
    return delta/np.maximum(np.linalg.norm(delta,axis=2,keepdims=True),1e-9)


def fits(query, sources):
    """Least-squares uniform scale/translation for complete six-point layouts."""
    centered=query-query.mean(axis=0)
    means=sources.mean(axis=1)
    relative=sources-means[:,None]
    denominator=(relative*relative).sum(axis=(1,2))
    scales=(relative*centered).sum(axis=(1,2))/denominator
    shifts=query.mean(axis=0)-scales[:,None]*means
    errors=np.linalg.norm(sources*scales[:,None,None]+shifts[:,None]-query,axis=2)
    return scales,shifts,errors


def static_member(chains, rival):
    """Enumerate rival arrangements without using any live frame or pose."""
    if len(chains)<2 or len(rival)<2:
        raise ValueError('Both original drawings need complete corner layouts')
    own_directions=directions(chains);other_directions=directions(rival)
    # If both drawings can match the same measured ray at cosine .95,
    # their mutual cosine can be as low as cos(2*acos(.95)). Include it.
    compatible=[np.flatnonzero(np.min((other_directions*d).sum(axis=2),axis=1)>=2*.95**2-1) for d in own_directions]
    rows=[]
    for left in range(len(chains)):
        for right in range(left+1,len(chains)):
            if np.min(np.linalg.norm(chains[left,:,None]-chains[right,None,:],axis=2))<=3:continue
            i,j=np.meshgrid(compatible[left],compatible[right],indexing='ij')
            i=i.ravel();j=j.ravel()
            if not len(i):continue
            alternatives=np.concatenate((rival[i],rival[j]),axis=1)
            query=np.concatenate((chains[left],chains[right]))
            scales,shifts,errors=fits(query,alternatives)
            rms=np.sqrt((errors*errors).mean(axis=1));rms[scales<=0]=np.inf
            best=int(np.argmin(rms))
            if not np.isfinite(rms[best]):continue
            rows.append(dict(pair=[left,right],rivalRms=float(rms[best]),rivalPair=[int(i[best]),int(j[best])]))
    return dict(chains=chains.tolist(),layouts=rows)


@lru_cache(maxsize=4)
def load_library(path):
    try:return json.loads(Path(path).read_text(encoding='utf8'))
    except (OSError,ValueError):return None


def observe(frame, library, group_id, member_ids, floor, *, cancelled=None, source_context=None, lifecycle_cancelled=None):
    from single_map_structure import current_geometry, observed_corner_chains
    if not library or library.get('groupId')!=group_id or library.get('floor')!=floor:
        return None
    if set(library.get('members',{}))!=set(member_ids):return None
    if cancelled and cancelled():return None
    if source_context is None:source_context={}
    if 'geometry' not in source_context:source_context['geometry']=current_geometry(frame)
    context=source_context['geometry']
    if context is None:return None
    query=np.array([c['points'] for c in observed_corner_chains(frame,context)])
    evidence=match_layouts(query,library,cancelled=cancelled) if len(query)>=2 else None
    if evidence is not None:return evidence
    from sparse_floor_structure import observe as observe_sparse
    return observe_sparse(frame,library,cancelled=lifecycle_cancelled or cancelled,context=context)


def match_layouts(query, library, *, cancelled=None):
    """Match measured full chains; source fitting failure is not an input."""
    if cancelled and cancelled():return None
    if len(library.get('members',{}))!=2:return None
    for data in library['members'].values():
        source=np.asarray(data.get('chains',[]),dtype=float)
        if source.ndim!=3 or source.shape[1:]!=(3,2) or len(source)<2 or not np.isfinite(source).all() or not data.get('layouts'):
            return None
    if len(query)<2:return None
    query_directions=directions(query);witnesses=[]
    for member,data in library['members'].items():
        source=np.asarray(data['chains'],dtype=float);source_directions=directions(source)
        margins={tuple(row['pair']):row['rivalRms'] for row in data['layouts']}
        for left in range(len(query)):
            for right in range(left+1,len(query)):
                if cancelled and cancelled():return None
                if np.min(np.linalg.norm(query[left,:,None]-query[right,None,:],axis=2))<=3:continue
                indexes=[np.flatnonzero(np.min((source_directions*query_directions[q]).sum(axis=2),axis=1)>=.95) for q in (left,right)]
                i,j=np.meshgrid(*indexes,indexing='ij');keep=i!=j;i=i[keep];j=j[keep]
                if not len(i):continue
                observed=np.concatenate((query[left],query[right]))
                candidates=np.concatenate((source[i],source[j]),axis=1)
                scales,shifts,errors=fits(observed,candidates)
                if cancelled and cancelled():return None
                for k in np.flatnonzero((scales>0)&(errors.max(axis=1)<=3)):
                    pair=tuple(sorted((int(i[k]),int(j[k]))))
                    rival_rms=margins.get(pair)
                    if rival_rms is None or not np.isfinite(rival_rms) or rival_rms<0:continue
                    measured_rms=float(np.sqrt((errors[k]**2).mean()))
                    # Triangle inequality: even the rival's optimal six-point
                    # arrangement must exceed the existing six-pixel wall limit.
                    separation=float(scales[k]*rival_rms-measured_rms)
                    if separation<=6:continue
                    witnesses.append(dict(mapId=member,queryPair=[left,right],sourcePair=list(pair),
                        queryPoints=observed.tolist(),sourcePoints=candidates[k].tolist(),
                        scale=float(scales[k]),translation=shifts[k].tolist(),
                        maxPixels=float(errors[k].max()),rivalMinimumRmsPixels=separation))
    winners={w['mapId'] for w in witnesses}
    if len(winners)!=1:return None
    return dict(attempted=True,confident=True,candidateMapId=next(iter(winners)),
        currentFloorStructureCompetition=True,witnesses=witnesses,
        reason='front-group-original-floor-layout-witness')
