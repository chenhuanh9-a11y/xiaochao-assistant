"""Offline, directed positive-evidence similarity survey; no runtime decisions.

Every radius denotes a square in which ALL stored corners and positive footprint
pixels are assumed observable. This finite observation family does not exhaust
fog-clipped views. Extra target structure is never negative evidence. Native
world comparisons and legacy reference-domain local comparisons remain distinct.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import itertools
import json
from pathlib import Path
import time

import cv2
import numpy as np
from scipy.spatial import cKDTree


CONDITIONS = dict(pixelsPerUnit=60., radiiUnits=[1, 2, 4, 8, 12],
    residualPixels=3., minimumRayDot=.95, minimumCorners=4, minimumExtentUnits=1.,
    transform='fixed scale 60/sourceUnit per reference plus translation; no arbitrary scale or rotation',
    observation='all stored corners and positive footprint pixels in the square window',
    negativeEvidence=False, runtimeThresholdChanges=False,
    claim='finite static observation compatibility, not real initial identity ambiguity')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


@dataclass
class Features:
    points: np.ndarray
    rays: np.ndarray
    edges: np.ndarray
    main: np.ndarray
    center: np.ndarray | None
    unit: float
    provenance: dict
    gates: list

    @property
    def scale(self):
        return CONDITIONS['pixelsPerUnit'] / self.unit


@dataclass
class Structure:
    mode: str
    map_id: str
    floor: str
    mask: np.ndarray
    metadata: dict
    provenance: dict
    features: dict

    @property
    def key(self):
        return f'{self.mode}/{self.map_id}/{self.floor}'


def feature_row(row, kind, provenance):
    if kind == 'front':
        points = np.asarray([c['points'][1] for c in row['chains']], float).reshape(-1, 2)
        rays = np.asarray([c['rays'] for c in row['chains']], float).reshape(-1, 2, 2)
        outline = np.asarray(row['outline'], float).reshape(-1, 2)
        main = np.linalg.norm(points[:, None] - outline[None], axis=2).min(axis=1) <= 3 if len(points) else np.zeros(0, bool)
        edges = np.empty((0, 2), int)
        center = np.asarray(row['center'], float)
    else:
        points = np.asarray([t['point'] for t in row['turns']], float).reshape(-1, 2)
        rays = np.asarray([t['rays'] for t in row['turns']], float).reshape(-1, 2, 2)
        edges = np.asarray([(i, t['next']) for i, t in enumerate(row['turns'])
                            if 0 <= t.get('next', -1) < len(points)], int).reshape(-1, 2)
        main, center = np.zeros(len(points), bool), None
    unit = float(row['unit'])
    if unit <= 0 or not np.isfinite(points).all() or not np.isfinite(rays).all():
        raise ValueError('invalid feature geometry')
    return Features(points, rays, edges, main, center, unit,
        dict(provenance, region=row.get('region'), sourceProduct=row.get('sourceProduct'),
             sourceKind=row.get('sourceKind'), sourceEntryId=row.get('sourceEntryId')),
        row.get('gates', []))


def load_catalog(root):
    """Read declared IDs (including UUIDs), exact floors, and original R domains."""
    root = Path(root).resolve()
    manifest = read(root / 'difficult-native-manifest.json')
    legacy = manifest['runtimeInterfaces']['legacyClean']['referenceCanvas']
    inventory, structures = [], []
    for mode, pack_name, feature_base in (
            ('nightmare', 'nightmare-map-pack.json', 'references/nightmare-entries'),
            ('difficult', 'difficult-native-identity-pack.json', 'recognition/native')):
        pack = read(root / 'references' / pack_name)
        shared = read(root / 'references' / f'{mode}-shared-structure-index.json')
        entries = {(e['mapId'], str(e['floor'])):e for e in shared['entries']}
        feature_maps = {}
        for kind, filename in (('front', 'entries.json'), ('side', 'side-entries.json')):
            relative = f'{feature_base}/{filename}'
            payload = read(root / relative)
            sha = digest(root / relative)
            table = {}
            for i, row in enumerate(payload['entries']):
                key = (row['mapId'], str(row['floor']))
                if key in table:
                    raise ValueError(f'duplicate feature row {key}')
                table[key] = (row, dict(path=relative, sha256=sha, rowIndex=i))
            feature_maps[kind] = table
        for item in pack['maps']:
            for floor, catalog_path in item['layers'].items():
                key = (item['id'], str(floor))
                status = dict(mode=mode, mapId=key[0], floor=key[1], catalog=f'references/{pack_name}',
                              status='NOT_YET_SCANNED', unknown=[])
                try:
                    entry = entries.get(key)
                    if entry is not None:
                        relative = entry['resourcePath']
                        with np.load(root / relative, allow_pickle=False) as archive:
                            metadata = json.loads(str(archive['metadata'].item()))
                            mask = (archive[metadata['roles']['clean']] > 0).astype(np.uint8)
                        expected_binding = {k:entry[k] for k in ('profile', 'mapId', 'floor', 'variant')}
                        if metadata.get('binding') != expected_binding:
                            raise ValueError('native binding mismatch')
                        for name in ('structureVersionKey', 'sourceVersion', 'baseSnapshot'):
                            if metadata.get(name) != entry.get(name):
                                raise ValueError(f'native index mismatch: {name}')
                        canvas = metadata['canvas']; geometry = metadata['sourceGeometry']
                        if canvas['shapeHW'] != list(mask.shape) or canvas['floor'] != floor:
                            raise ValueError('native shape/floor mismatch')
                        bounds = geometry['bounds']
                        scales = [mask.shape[1]/(bounds['maxX']-bounds['minX']),
                                  mask.shape[0]/(bounds['maxY']-bounds['minY'])]
                        if not np.allclose(scales, 60/128, atol=1e-10, rtol=0) or geometry['matrixRendererPixelToProduct'] != np.eye(3).tolist():
                            raise ValueError('unsupported native world bridge')
                        status.update(coordinateDomain='game-native-world', sourceLayout=canvas['layout'],
                                      worldBounds=bounds, worldPixelsPerUnit=scales[0])
                    else:
                        # The complete catalog deliberately retains this original
                        # legacy competitor. Do not invent sourceGeometry for it.
                        declaration = item['legacyCleanFloors'][floor]
                        canvas = legacy[floor]
                        relative = declaration['resourcePath']
                        with np.load(root / relative, allow_pickle=False) as archive:
                            mask = (archive['clean_mask'] > 0).astype(np.uint8)
                        if digest(root / relative) != canvas['resourceSHA256'] or list(mask.shape) != canvas['shapeHW']:
                            raise ValueError('legacy reference declaration mismatch')
                        metadata = dict(sourceKind='legacy-shared-binary', sourceClass=canvas['sourceClass'],
                            binding=dict(profile=item['identityProfileId'], mapId=key[0], floor=floor, variant='variant-01'),
                            canvas=dict(shapeHW=list(mask.shape), coordinateSpace=canvas['coordinateSpace'], floor=floor),
                            **{name:canvas[name] for name in ('structureVersionKey','sourceVersion','baseSnapshot')})
                        status.update(coordinateDomain='legacy-reference', sourceLayout=None)
                        status['unknown'].append('native-world-bridge-unavailable')
                    features = {}
                    for kind in ('front', 'side'):
                        source_row = feature_maps[kind].get(key)
                        if source_row:
                            row, provenance = source_row
                            if row.get('structureVersionKey') and row['structureVersionKey'] != metadata['structureVersionKey']:
                                raise ValueError('feature structure version mismatch')
                            features[kind] = feature_row(row, kind, provenance)
                        status[kind] = ('AVAILABLE' if kind in features else
                            'NOT_APPLICABLE_NO_FRONT_ON_SOURCE_FLOOR' if kind == 'front' and floor != '1' else 'UNKNOWN_MISSING_FEATURES')
                    if not features.get('side') or not features['side'].gates:
                        status['unknown'].append('semantic-side-entry-location-unavailable; local-structure-only')
                    status['unknown'].append('real-fog-visibility-and-runtime-identity-not-tested')
                    provenance = dict(resourcePath=relative, resourceSha256=digest(root / relative),
                        binding=metadata['binding'], structureVersionKey=metadata['structureVersionKey'],
                        sourceVersion=metadata['sourceVersion'], baseSnapshot=metadata['baseSnapshot'],
                        shapeHW=list(mask.shape), coordinateDomain=status['coordinateDomain'])
                    status.update(provenance, maskPixels=int(mask.sum()), status='AVAILABLE',
                        features={kind:dict(f.provenance,unit=f.unit,points=len(f.points),
                            validEdges=len(f.edges),mainCorners=int(f.main.sum()),
                            gates=f.gates,center=f.center.tolist() if f.center is not None else None)
                            for kind,f in features.items()},
                        sharedChannelSemantics=metadata.get('roleSemantics'),
                        missingChannels=metadata.get('missingChannels',[]))
                    structures.append(Structure(mode, key[0], floor, mask, metadata, provenance, features))
                except (OSError, KeyError, ValueError, TypeError) as error:
                    status.update(status='UNKNOWN_RESOURCE_ERROR', error=str(error))
                inventory.append(status)
    return structures, inventory


def whole_pair(a, b):
    ga, gb = a.metadata.get('sourceGeometry'), b.metadata.get('sourceGeometry')
    if not ga or not gb:
        return dict(status='UNKNOWN_NO_COMMON_WORLD_BRIDGE')
    ba, bb = ga['bounds'], gb['bounds']; scale = 60/128
    x0, x1 = max(ba['minX'],bb['minX']), min(ba['maxX'],bb['maxX'])
    y0, y1 = max(ba['minY'],bb['minY']), min(ba['maxY'],bb['maxY'])
    intersection = 0
    if x1 > x0 and y1 > y0:
        w,h = round((x1-x0)*scale),round((y1-y0)*scale)
        ax,ay = round((x0-ba['minX'])*scale),round((ba['maxY']-y1)*scale)
        bx,by = round((x0-bb['minX'])*scale),round((bb['maxY']-y1)*scale)
        intersection = int(np.count_nonzero(a.mask[ay:ay+h,ax:ax+w] & b.mask[by:by+h,bx:bx+w]))
    na,nb = int(a.mask.sum()),int(b.mask.sum()); union=na+nb-intersection
    return dict(status='SCANNED_DESCRIPTIVE_ONLY', intersection=intersection, union=union,
        iou=intersection/union if union else None, aOnly=na-intersection, bOnly=nb-intersection,
        establishesInitialAmbiguity=False)


def ray_compatible(a, b):
    return np.min(np.sum(a*b, axis=-1), axis=-1) >= CONDITIONS['minimumRayDot']


def seeds(a, b, kind):
    """All directed anchored translations at the declared static unit scale."""
    ap,bp = a.points*a.scale,b.points*b.scale
    if kind == 'front':
        ai,bi = np.flatnonzero(a.main),np.flatnonzero(b.main)
        if not len(ai) or not len(bi): return [],0
        compatible = ray_compatible(a.rays[ai,None],b.rays[None,bi])
        ii,jj=np.nonzero(compatible)
        return [(int(ai[i]),int(bi[j]),ap[ai[i]]-bp[bi[j]]) for i,j in zip(ii,jj)],len(ai)*len(bi)
    result=[]; raw=len(a.edges)*len(b.edges)
    if not raw:return result,raw
    b0,b1=b.edges[:,0],b.edges[:,1]
    for edge_index,(a0,a1) in enumerate(a.edges):
        compatible=ray_compatible(a.rays[a0],b.rays[b0]) & ray_compatible(a.rays[a1],b.rays[b1])
        compatible &= np.linalg.norm((ap[a1]-ap[a0])-(bp[b1]-bp[b0]),axis=1)<=CONDITIONS['residualPixels']
        for j in np.flatnonzero(compatible):
            result.append((edge_index,int(j),ap[a0]-bp[b0[j]]))
    return result,raw


def corner_matches(a, b, translations):
    """Nearest compatible ray pair, including non-nearest geometric candidates."""
    ap,bp=a.points*a.scale,b.points*b.scale
    tree=cKDTree(bp)
    query=ap[None]-np.asarray(translations)[:,None]
    distance, nearest=tree.query(query, distance_upper_bound=CONDITIONS['residualPixels']+1e-9)
    safe=np.minimum(nearest,max(0,len(bp)-1))
    ok=np.isfinite(distance)&ray_compatible(a.rays[None],b.rays[safe])
    for ti,pi in zip(*np.nonzero(np.isfinite(distance)&~ok)):
        ids=tree.query_ball_point(query[ti,pi],CONDITIONS['residualPixels']+1e-9)
        ids=[j for j in ids if ray_compatible(a.rays[pi],b.rays[j])]
        if ids:
            j=min(ids,key=lambda j:(np.linalg.norm(query[ti,pi]-bp[j]),j))
            nearest[ti,pi]=j;distance[ti,pi]=np.linalg.norm(query[ti,pi]-bp[j]);ok[ti,pi]=True
    return ok, nearest, distance


def injective_window_matches(a, b, translation, ids, nearest, residual):
    """Keep the common nearest fast path; solve only a conflicting local graph."""
    matched=nearest[ids]
    if len(set(matched))==len(ids):return matched,residual[ids]
    source=a.points[ids]*a.scale-translation
    target=b.points*b.scale
    neighborhoods=cKDTree(target).query_ball_point(source,CONDITIONS['residualPixels']+1e-9)
    candidates=[]
    for qi,neighbors in enumerate(neighborhoods):
        legal=[j for j in neighbors if ray_compatible(a.rays[ids[qi]],b.rays[j])]
        legal.sort(key=lambda j:(np.linalg.norm(source[qi]-target[j]),j))
        if not legal:return None
        candidates.append(legal)
    owner={}
    def augment(qi,visited):
        for target_id in candidates[qi]:
            if target_id in visited:continue
            visited.add(target_id)
            if target_id not in owner or augment(owner[target_id],visited):
                owner[target_id]=qi
                return True
        return False
    for qi in sorted(range(len(ids)),key=lambda qi:(len(candidates[qi]),qi)):
        if not augment(qi,set()):return None
    result=np.empty(len(ids),int)
    for target_id,qi in owner.items():result[qi]=target_id
    return result,np.linalg.norm(source-target[result],axis=1)


def positive_support(a, b, fa, fb, center, translation, radius, target_distance):
    """Every positive source pixel needs target footprint within scan tolerance.

    Distance to a rounded target pixel plus the rounding distance is an upper
    bound on distance to real target structure. It avoids inventing coverage by
    interpolation, including at image borders and legacy subpixel scales.
    """
    extent=radius*60/fa
    x0=max(0,int(np.ceil(center[0]-extent)));x1=min(a.mask.shape[1],int(np.floor(center[0]+extent))+1)
    y0=max(0,int(np.ceil(center[1]-extent)));y1=min(a.mask.shape[0],int(np.floor(center[1]+extent))+1)
    ys,xs=np.nonzero(a.mask[y0:y1,x0:x1]);xs=xs+x0;ys=ys+y0
    if not len(xs): return np.empty(0),np.empty(0),0
    points=np.column_stack([xs,ys])*fa
    target=(points-translation)/fb
    rounded=np.rint(target).astype(int)
    rounded[:,0]=np.clip(rounded[:,0],0,b.mask.shape[1]-1)
    rounded[:,1]=np.clip(rounded[:,1],0,b.mask.shape[0]-1)
    grid_distance=target_distance[rounded[:,1],rounded[:,0]]
    rounding=np.linalg.norm(target-rounded,axis=1)
    residual=(grid_distance+rounding)*fb
    uncertain=(residual>3+1e-8)&((grid_distance-rounding)*fb<=3+1e-8)&(grid_distance>0)
    if np.any(uncertain):
        boundary=b.mask & (1-cv2.erode(b.mask,np.ones((3,3),np.uint8),borderType=cv2.BORDER_CONSTANT,borderValue=0))
        yy,xx=np.nonzero(boundary)
        if len(xx):
            exact,_=cKDTree(np.column_stack([xx,yy])).query(target[uncertain])
            residual[uncertain]=exact*fb
    radial=np.max(np.abs(points-center*fa),axis=1)/60
    return radial,residual,len(xs)


def observation_windows(features, kind):
    af=features
    anchors=[(-1,af.center)] if kind=='front' else [(i,af.points[edge[0]]) for i,edge in enumerate(af.edges)]
    windows={};eligible=0
    for anchor_id,center in anchors:
        radial=np.max(np.abs((af.points-center)*af.scale),axis=1)/60
        values=[]
        for radius in CONDITIONS['radiiUnits']:
            ids=np.flatnonzero(radial<=radius+1e-10)
            valid=len(ids)>=4 and np.min(np.ptp(af.points[ids]*af.scale,axis=0))>=60-1e-8
            if valid and kind=='front':valid=np.count_nonzero(af.main[ids])>=2
            if valid and kind=='side':
                edge=af.edges[anchor_id]
                valid=bool(np.all(np.isin(edge,ids)) and np.linalg.norm(
                    af.points[edge[1]]-af.points[edge[0]])>=.6*af.unit)
            if valid:values.append((radius,ids));eligible+=1
        windows[anchor_id]=(center,values)
    return windows,eligible


def scan_direction(a, b, kind):
    af,bf=a.features.get(kind),b.features.get(kind)
    if af is None or bf is None:
        return [],dict(status='UNKNOWN_MISSING_FEATURES',directedWindows=0)
    windows,eligible=observation_windows(af,kind)
    if not len(bf.points) or (kind=='side' and not len(bf.edges)):
        return [],dict(status='UNKNOWN_TARGET_FEATURES_EMPTY',directedWindows=eligible)
    candidates,raw=seeds(af,bf,kind)
    if not eligible:return [],dict(status='NOT_ELIGIBLE',directedWindows=0,rawSeeds=raw,seedCount=len(candidates))
    by_anchor=defaultdict(list)
    # Exact repeated seeds are identical poses; preserve all feature origins.
    translations=[];translation_ids={}
    for ai,bi,t in candidates:
        key=tuple(t)
        if key not in translation_ids:
            translation_ids[key]=len(translations);translations.append(t)
        by_anchor[-1 if kind=='front' else ai].append((translation_ids[key],ai,bi))
    if not translations:return [],dict(status='NO_COMPATIBLE_UNDER_SCANNED_CONDITIONS',directedWindows=eligible,rawSeeds=raw,seedCount=0)
    target_distance=cv2.distanceTransform(1-b.mask,cv2.DIST_L2,cv2.DIST_MASK_PRECISE)
    records=[];corner_proposals=0;positive_queries=0
    # Batch memory is bounded; no candidate/pose cap or early top-k truncation.
    for start in range(0,len(translations),128):
        chunk=translations[start:start+128]
        ok,nearest,residual=corner_matches(af,bf,chunk)
        for anchor_id,rows in by_anchor.items():
            center,valid_windows=windows[anchor_id]
            for ti,source_anchor,target_anchor in rows:
                if not start<=ti<start+len(chunk):continue
                ci=ti-start
                passed=[]
                for radius,ids in valid_windows:
                    if not ok[ci,ids].all() or (kind=='front' and source_anchor not in ids):continue
                    match=injective_window_matches(af,bf,translations[ti],ids,nearest[ci],residual[ci])
                    if match is not None:passed.append((radius,ids,*match))
                if not passed:continue
                corner_proposals+=len(passed)
                radial,errors,pixel_count=positive_support(a,b,af.scale,bf.scale,center,
                    translations[ti],passed[-1][0],target_distance)
                positive_queries+=1
                qualified=[(radius,ids,matched,errors_corner) for radius,ids,matched,errors_corner in passed
                           if not np.any(errors[radial<=radius+1e-10]>CONDITIONS['residualPixels']+1e-8)]
                if not qualified:continue
                largest,ids,matched,errors_corner=qualified[-1]
                t=translations[ti]
                # Only identical matrices share a row. Nearby poses retain
                # their own footprint evidence and never inherit a verdict.
                existing=next((r for r in records if r['sourceAnchorIndex']==anchor_id
                    and np.array_equal(r['translationCanonical'],t)
                    and r['radiiUnits']==[v[0] for v in qualified]),None)
                if existing is not None:
                    existing['equivalentSeedCount']+=1
                    existing['exactEquivalentSeeds'].append([source_anchor,target_anchor])
                    continue
                records.append(dict(source=a.key,target=b.key,kind=kind,sourceAnchorIndex=anchor_id,
                    sourceSeedIndex=source_anchor,targetSeedIndex=target_anchor,
                    centerReference=center.tolist(),radiiUnits=[v[0] for v in qualified],
                    translationCanonical=t.tolist(),targetToSourceReference=[
                        [bf.scale/af.scale,0,float(t[0]/af.scale)],
                        [0,bf.scale/af.scale,float(t[1]/af.scale)],[0,0,1]],
                    sourceFeatureIds=ids.tolist(),targetFeatureIds=matched.tolist(),
                    cornerResidualMax=float(errors_corner.max(initial=0)),
                    footprintResidualUpperMax=float(errors[radial<=largest+1e-10].max(initial=0)),
                    positivePixels=int(np.count_nonzero(radial<=largest+1e-10)),
                    sourceReferenceToCondition=[[af.scale,0,float(-center[0]*af.scale)],
                        [0,af.scale,float(-center[1]*af.scale)],[0,0,1]],
                    targetReferenceToCondition=[[bf.scale,0,float(t[0]-center[0]*af.scale)],
                        [0,bf.scale,float(t[1]-center[1]*af.scale)],[0,0,1]],
                    sourceMainCorners=int(af.main[ids].sum()),
                    targetMainCorners=int(bf.main[matched].sum()),
                    targetNeighborCorners=int((~bf.main[matched]).sum()),
                    runtimeFrontNeighborGate='NOT_EVALUATED_BY_STATIC_SURVEY',
                    supportScopeRadiusUnits=largest,
                    windows=[dict(radiusUnits=radius,sourceFeatureIds=window_ids.tolist(),
                        targetFeatureIds=window_matched.tolist(),
                        sourceMainCorners=int(af.main[window_ids].sum()),
                        targetMainCorners=int(bf.main[window_matched].sum()),
                        targetNeighborCorners=int((~bf.main[window_matched]).sum()),
                        cornerResidualMax=float(window_errors.max(initial=0)),
                        positivePixels=int(np.count_nonzero(radial<=radius+1e-10)),
                        footprintResidualUpperMax=float(errors[radial<=radius+1e-10].max(initial=0)))
                        for radius,window_ids,window_matched,window_errors in qualified],
                    equivalentSeedCount=1,exactEquivalentSeeds=[[source_anchor,target_anchor]],
                    compatible='visible-positive-footprint-and-contour-compatible',
                    proposalOnly=True))
    return records,dict(status='COMPATIBLE_UNDER_SCANNED_CONDITIONS' if records else 'NO_COMPATIBLE_UNDER_SCANNED_CONDITIONS',
        directedWindows=eligible,rawSeeds=raw,seedCount=len(candidates),distinctExactTranslations=len(translations),
        cornerProposalWindows=corner_proposals,footprintPoseQueries=positive_queries,poseConditions=len(records))


def scan(root, output, *, pair_limit=None, selected_kind='all'):
    if selected_kind not in {'all','front'}:raise ValueError('unsupported scan kind')
    output=Path(output).resolve()
    if output.exists() and any(output.iterdir()):raise ValueError('preserve previous scan output')
    started=time.perf_counter(); structures,inventory=load_catalog(root)
    output.mkdir(parents=True,exist_ok=True)
    write(output/'inventory.json',dict(sourceRoot=str(Path(root).resolve()),conditions=CONDITIONS,structures=inventory))
    by_group=defaultdict(list)
    by_key={s.key:s for s in structures}
    for item in inventory:by_group[(item['mode'],item['floor'])].append(item['mapId'])
    pair_counts=Counter();status_counts=Counter();direction_counts=Counter();direction_windows=0;pose_count=0;condition_map=defaultdict(list)
    comparison_status={};relation_pairs=set();group_counts=Counter()
    with (output/'pair-status.jsonl').open('w',encoding='utf-8') as status_out, (output/'poses.jsonl').open('w',encoding='utf-8') as pose_out:
        for (mode,floor),items in sorted(by_group.items()):
            if selected_kind=='front' and floor!='1':continue
            for map_a,map_b in itertools.combinations(items,2):
                a,b=by_key.get(f'{mode}/{map_a}/{floor}'),by_key.get(f'{mode}/{map_b}/{floor}')
                for kind in (('front',) if selected_kind=='front' else ('whole','front','side')):
                    if kind=='front' and floor!='1':continue
                    pair_counts[kind]+=1
                    row=dict(mode=mode,floor=floor,kind=kind,mapA=map_a,mapB=map_b)
                    if pair_limit is not None and pair_counts[kind]>pair_limit:
                        row.update(status='NOT_SCANNED_PILOT_LIMIT')
                    elif a is None or b is None:row.update(status='UNKNOWN_RESOURCE_ERROR')
                    elif kind=='whole':row.update(whole_pair(a,b))
                    else:
                        directions=[]
                        for source,target in ((a,b),(b,a)):
                            records,diagnostic=scan_direction(source,target,kind)
                            direction_windows+=diagnostic['directedWindows']
                            direction_counts[f'{kind}:{diagnostic["status"]}']+=1
                            comparison_status[(source.key,kind,target.key)]=diagnostic['status']
                            directions.append(dict(source=source.map_id,target=target.map_id,**diagnostic))
                            for record in records:
                                relation_pairs.add((mode,floor,kind,*sorted([source.map_id,target.map_id])))
                                record['poseId']=pose_count;pose_count+=1
                                pose_out.write(json.dumps(record,separators=(',',':'))+'\n')
                                for radius in record['radiiUnits']:
                                    key=(source.key,kind,record['sourceAnchorIndex'],radius)
                                    condition_map[key].append(dict(target=target.key,poseId=record['poseId']))
                        row.update(status='SCANNED',directions=directions)
                    status_counts[f'{kind}:{row["status"]}']+=1
                    status_out.write(json.dumps(row,separators=(',',':'))+'\n');status_out.flush()
                progress=pair_counts['front' if selected_kind=='front' else 'whole']
                if progress%25==0 and (pair_limit is None or progress<=pair_limit):
                    print(json.dumps(dict(progressPairs=progress,poseRecords=pose_count,
                        elapsedSeconds=round(time.perf_counter()-started,1))),flush=True)
    complete_sources={}
    for source,kind,_,_ in condition_map:
        item=by_key[source]
        targets=[f'{item.mode}/{m}/{item.floor}' for m in by_group[(item.mode,item.floor)] if m!=item.map_id]
        unresolved=[dict(target=t,status=comparison_status.get((source,kind,t),'NOT_SCANNED'))
                    for t in targets if comparison_status.get((source,kind,t)) not in {
                        'COMPATIBLE_UNDER_SCANNED_CONDITIONS','NO_COMPATIBLE_UNDER_SCANNED_CONDITIONS','NOT_ELIGIBLE'}]
        complete_sources[(source,kind)]=unresolved
    member_histogram=Counter();multi_pose_conditions=0
    with (output/'conditions.jsonl').open('w',encoding='utf-8') as stream:
        for condition_id,((source,kind,anchor,radius),poses) in enumerate(sorted(condition_map.items())):
            members=sorted({source,*[p['target'] for p in poses]})
            item=by_key[source];features=item.features[kind]
            center=features.center if kind=='front' else features.points[features.edges[anchor,0]]
            unresolved=complete_sources[(source,kind)]
            member_histogram[len(members)]+=1
            multi_pose_conditions+=int(any(count>1 for count in Counter(p['target'] for p in poses).values()))
            group_counts[f'{item.mode}/{item.floor}/{kind}']+=1
            stream.write(json.dumps(dict(conditionId=condition_id,mode=item.mode,floor=item.floor,
                source=source,kind=kind,sourceAnchorIndex=anchor,radiusUnits=radius,
                centerReference=center.tolist(),unit=features.unit,
                scope=('front-entry-window' if kind=='front' else 'local-structure-window'),
                members=members,compatiblePoses=poses,completeForeignMapCompetition=not unresolved,
                unresolvedTargets=unresolved,semanticSideEntryCoverage='UNKNOWN' if kind=='side' else 'NOT_APPLICABLE',
                sameMapAlternativePoses='NOT_SCANNED',runtimeVerified=False),separators=(',',':'))+'\n')
    sequence_summary=exploration_sequences(output,condition_map,by_key,complete_sources)
    summary=dict(conditions=CONDITIONS,selectedKind=selected_kind,pilot=pair_limit is not None,pairBuckets=dict(pair_counts),
        statusCounts=dict(status_counts),structureCount=len(inventory),availableStructures=len(structures),
        directedStatuses=dict(direction_counts),
        directedQueryWindows=direction_windows,poseRecords=pose_count,conditionRecords=len(condition_map),
        conditionCountsByModeFloorKind=dict(group_counts),conditionMemberCountHistogram=dict(member_histogram),
        multiPoseConditions=multi_pose_conditions,compatibleUnorderedPairRelations=len(relation_pairs),
        relationCountsByModeFloorKind=dict(Counter('/'.join(p[:3]) for p in relation_pairs)),
        exploration=sequence_summary,
        outputBytes={p.name:p.stat().st_size for p in output.iterdir() if p.is_file()},
        elapsedSeconds=time.perf_counter()-started,
        limitations=['finite square fully-visible positive feature/footprint windows; not arbitrary fog visibility',
                     'no real-frame solver or runtime identity verification',
                     'absence of semantic door markers is unknown, not proof of no side entrance',
                     'difference samples pending condition-volume review; no production groups generated'])
    write(output/'summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False),flush=True)
    return summary


def exploration_sequences(output, condition_map, by_key, complete_sources, *, filename='exploration-sequences.jsonl'):
    anchors=sorted({key[:3] for key in condition_map});monotonic_errors=[];resolved=unresolved=non_monotonic=0
    windows_cache={}
    with (output/filename).open('w',encoding='utf-8') as stream:
        for source,kind,anchor in anchors:
            if (source,kind) not in windows_cache:
                windows_cache[(source,kind)]=observation_windows(by_key[source].features[kind],kind)[0]
            windows=windows_cache[(source,kind)]
            rows=[];previous=None;first=None;growth=False
            for radius,ids in windows[anchor][1]:
                poses=condition_map.get((source,kind,anchor,radius),[])
                members={source,*[p['target'] for p in poses]}
                if previous is not None and not members<=previous:
                    monotonic_errors.append(dict(source=source,kind=kind,anchor=anchor,radius=radius))
                    growth=True
                previous=members
                complete=not complete_sources[(source,kind)]
                if len(members)==1 and complete and first is None:first=radius
                rows.append(dict(radiusUnits=radius,members=sorted(members),poseIds=[p['poseId'] for p in poses],
                    completeForeignMapCompetition=complete,sourceFeatureCount=len(ids)))
            still=bool(rows and len(rows[-1]['members'])>1)
            if growth:first=None;non_monotonic+=1
            resolved+=int(first is not None);unresolved+=int(still)
            stream.write(json.dumps(dict(source=source,kind=kind,sourceAnchorIndex=anchor,
                windows=rows,firstStaticDistinguishingRadiusUnits=first,
                status=('UNKNOWN_NON_MONOTONIC_PROPOSAL_COVERAGE' if growth else
                    'UNKNOWN_INCOMPLETE_COMPETITION' if complete_sources[(source,kind)] else 'SCANNED_STATIC_SEQUENCE'),
                largestEligibleWindowStillCompatible=still,
                claim='static fully-positive square observations only; not actual initial visibility'),separators=(',',':'))+'\n')
    return dict(anchorSequences=len(anchors),staticDistinguishingSequences=resolved,
                largestWindowStillCompatible=unresolved,nonMonotonicProposalSequences=non_monotonic,
                membershipMonotonicityErrors=monotonic_errors)


def stable_samples(mask, radius):
    """The existing row-major nonzero + linspace(512) order, in condition pixels."""
    offset=round(radius*60)
    return stable_samples_at_origin(mask,(-offset,-offset))


def stable_samples_at_origin(mask, origin):
    y,x=np.nonzero(mask)
    take=np.linspace(0,len(x)-1,min(512,len(x))).astype(int)
    return dict(positivePixels=len(x),pointsXY=(np.column_stack((x[take],y[take]))+origin).tolist(),
                sampleIndices=list(range(len(take))))


def condition_raster(structure, matrix, radius):
    offset=round(radius*60);size=2*offset+1
    transform=np.asarray(matrix,float).copy();transform[0,2]+=offset;transform[1,2]+=offset
    return cv2.warpAffine(structure.mask,transform[:2],(size,size),flags=cv2.INTER_NEAREST,
                          borderMode=cv2.BORDER_CONSTANT,borderValue=0)>0


def positive_points_outside_tolerance(points, target, matrix):
    """Certify distance > the original residual to actual target positive pixels.

    Rounded distance bounds handle the common case. An exact boundary search
    resolves the straddling cases, including the legacy subpixel unit bridge.
    This never uses empty/unobserved live pixels as negative evidence.
    """
    matrix=np.asarray(matrix,float);scale=float(matrix[0,0])
    if scale<=0 or not np.allclose(matrix[:2,:2],np.eye(2)*scale):
        raise ValueError('post-entry difference requires fixed positive scale and translation')
    if not np.any(target.mask):raise ValueError('post-entry target positive structure unavailable')
    query=(points-matrix[:2,2])/scale
    rounded=np.rint(query).astype(int)
    rounded[:,0]=np.clip(rounded[:,0],0,target.mask.shape[1]-1)
    rounded[:,1]=np.clip(rounded[:,1],0,target.mask.shape[0]-1)
    distance=cv2.distanceTransform(1-target.mask,cv2.DIST_L2,cv2.DIST_MASK_PRECISE)
    grid_distance=distance[rounded[:,1],rounded[:,0]]
    rounding=np.linalg.norm(query-rounded,axis=1)
    threshold=CONDITIONS['residualPixels']+1e-8
    lower=(grid_distance-rounding)*scale;upper=(grid_distance+rounding)*scale
    result=lower>threshold
    uncertain=(lower<=threshold)&(upper>threshold)
    if np.any(uncertain):
        boundary=target.mask & (1-cv2.erode(target.mask,np.ones((3,3),np.uint8),
            borderType=cv2.BORDER_CONSTANT,borderValue=0))
        y,x=np.nonzero(boundary)
        exact=cKDTree(np.column_stack((x,y))).query(query[uncertain])[0]*scale
        result[uncertain]=exact>threshold
    return result


def post_entry_pair_differences(a, matrix_a, b, matrix_b):
    """Compare established entry poses over both complete positive structures.

    Entry qualification windows remain unchanged. This separate observation
    domain includes later distinguishing rooms even after local compatibility
    ends. Stable sampling still uses row-major nonzero / linspace(512).
    """
    extents=[]
    for structure,matrix in ((a,matrix_a),(b,matrix_b)):
        h,w=structure.mask.shape
        corners=np.array([[0,0,1],[w-1,0,1],[0,h-1,1],[w-1,h-1,1]])
        extents.extend((corners@np.asarray(matrix,float).T)[:,:2])
    origin=np.floor(np.min(extents,axis=0)).astype(int)
    maximum=np.ceil(np.max(extents,axis=0)).astype(int)
    size=tuple((maximum-origin+1).tolist())
    rasters=[]
    for structure,matrix in ((a,matrix_a),(b,matrix_b)):
        transform=np.asarray(matrix,float).copy();transform[:2,2]-=origin
        rasters.append(cv2.warpAffine(structure.mask,transform[:2],size,
            flags=cv2.INTER_NEAREST,borderMode=cv2.BORDER_CONSTANT,borderValue=0)>0)
    samples=[]
    for i,source,source_matrix,target,target_matrix in ((0,a,matrix_a,b,matrix_b),(1,b,matrix_b,a,matrix_a)):
        # Classify original source pixel centers before condition-grid rounding.
        # Testing the already-warped integer coordinate can turn a true 3px
        # source/target difference into 3.32px merely due to a fractional origin.
        y,x=np.nonzero(source.mask)
        source_points=np.column_stack((x,y,np.ones(len(x))))
        points=(source_points@np.asarray(source_matrix,float).T)[:,:2]
        robust=positive_points_outside_tolerance(points,target,target_matrix)
        source_difference=np.zeros(source.mask.shape,np.uint8)
        source_difference[y[robust],x[robust]]=1
        transform=np.asarray(source_matrix,float).copy();transform[:2,2]-=origin
        mask=cv2.warpAffine(source_difference,transform[:2],size,flags=cv2.INTER_NEAREST,
            borderMode=cv2.BORDER_CONSTANT,borderValue=0)>0
        # The persisted integer sample must also remain outside the tolerance;
        # otherwise an exact source difference could round onto target support.
        raster_y,raster_x=np.nonzero(mask)
        safe=positive_points_outside_tolerance(np.column_stack((raster_x,raster_y))+origin,target,target_matrix)
        mask[raster_y[~safe],raster_x[~safe]]=False
        sample=stable_samples_at_origin(mask,origin)
        sample['rawExclusivePositivePixels']=int(np.count_nonzero(rasters[i]&~rasters[1-i]))
        samples.append(sample)
    return dict(aOnly=samples[0],bOnly=samples[1],
        differenceScope='post-entry-positive-structure',
        observationBounds=[*origin.tolist(),*maximum.tolist()],
        minimumPositiveDistancePixels=CONDITIONS['residualPixels'],
        distanceEvaluationSpace='original-source-and-target-positive-pixel-centers',
        persistedSampleDistanceChecked=True,
        differenceSemantics='both original source positive pixel centers and persisted integer samples farther than original residual from target positive pixel centers; source classified before raster quantization')


def difference_samples(root, output, *, condition_limit=None, summary_name='summary.json', selected_kind='front'):
    """Read the completed survey and emit all cross-member pose-pair differences."""
    output=Path(output);destination=output/'difference-samples.jsonl'
    if destination.exists():raise ValueError('preserve existing difference samples')
    structures,_=load_catalog(root);by_key={s.key:s for s in structures}
    recorded=read(output/'inventory.json')
    for row in recorded['structures']:
        key=f'{row["mode"]}/{row["mapId"]}/{row["floor"]}'
        if key in by_key and by_key[key].provenance['resourceSha256']!=row['resourceSha256']:
            raise ValueError('source changed after survey')
    poses={p['poseId']:p for p in map(json.loads,(output/'poses.jsonl').read_text(encoding='utf-8').splitlines())}
    records=conditions=nonzero=0;started=time.perf_counter();pair_cache={}
    with destination.open('x',encoding='utf-8') as stream:
        for condition in map(json.loads,(output/'conditions.jsonl').read_text(encoding='utf-8').splitlines()):
            if selected_kind=='front' and condition['kind']!='front':continue
            if condition_limit is not None and conditions>=condition_limit:break
            source=by_key[condition['source']];feature=source.features[condition['kind']]
            center=np.asarray(condition['centerReference']);factor=feature.scale
            own=dict(target=source.key,poseId=None,targetReferenceToCondition=[
                [factor,0,float(-center[0]*factor)],[0,factor,float(-center[1]*factor)],[0,0,1]])
            supported=[own]+[poses[p['poseId']] for p in condition['compatiblePoses']]
            rasters=([condition_raster(by_key[p['target']],p['targetReferenceToCondition'],condition['radiusUnits']) for p in supported]
                     if condition['kind']!='front' else None)
            for i,j in itertools.combinations(range(len(supported)),2):
                a,b=supported[i],supported[j]
                if a['target']==b['target']:continue
                if condition['kind']=='front':
                    key=(condition['source'],condition['sourceAnchorIndex'],
                        a['target'],tuple(np.asarray(a['targetReferenceToCondition']).ravel()),
                        b['target'],tuple(np.asarray(b['targetReferenceToCondition']).ravel()))
                    if key not in pair_cache:
                        pair_cache[key]=post_entry_pair_differences(by_key[a['target']],a['targetReferenceToCondition'],
                            by_key[b['target']],b['targetReferenceToCondition'])
                    difference=pair_cache[key]
                    sa,sb=difference['aOnly'],difference['bOnly']
                else:
                    sa=stable_samples(rasters[i]&~rasters[j],condition['radiusUnits'])
                    sb=stable_samples(rasters[j]&~rasters[i],condition['radiusUnits'])
                    difference=dict(aOnly=sa,bOnly=sb,differenceScope='entry-compatibility-window')
                nonzero+=int(bool(sa['positivePixels'] or sb['positivePixels']))
                stream.write(json.dumps(dict(conditionId=condition['conditionId'],
                    memberA=a['target'],poseA=a['poseId'],memberB=b['target'],poseB=b['poseId'],
                    **difference,positiveEvidenceOnly=True,
                    coordinateSpace='condition-pixels-fixed-60-per-unit',
                    resampling='nearest reference pixel; no semantic wall assertion',
                    absentUnobservedPixelsAreNotVetoes=True),separators=(',',':'))+'\n')
                records+=1
            conditions+=1
            if conditions%500==0:print(json.dumps(dict(differenceConditions=conditions,pairPoseRecords=records)),flush=True)
    result=dict(conditions=conditions,pairPoseRecords=records,nonzeroDifferenceRecords=nonzero,
                bytes=destination.stat().st_size,elapsedSeconds=time.perf_counter()-started,
                pilot=condition_limit is not None or read(output/summary_name)['pilot'],
                sampling='row-major nonzero; linspace 512; source ordering preserved',
                selectedKind=selected_kind,distinctPostEntryPosePairs=len(pair_cache))
    write(output/'difference-summary.json',result)
    print(json.dumps(result),flush=True)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source_root',type=Path)
    parser.add_argument('--output-root',type=Path,required=True)
    parser.add_argument('--pair-limit',type=int)
    parser.add_argument('--differences-only',action='store_true')
    parser.add_argument('--condition-limit',type=int)
    parser.add_argument('--summary-name',default='summary.json',help='survey summary used by --differences-only')
    parser.add_argument('--kind',choices=['all','front'],default='all',help='analysis scope; use front for production resources')
    args=parser.parse_args()
    if args.differences_only:difference_samples(args.source_root,args.output_root,condition_limit=args.condition_limit,summary_name=args.summary_name,selected_kind=args.kind)
    else:scan(args.source_root,args.output_root,pair_limit=args.pair_limit,selected_kind=args.kind)


if __name__=='__main__':main()
