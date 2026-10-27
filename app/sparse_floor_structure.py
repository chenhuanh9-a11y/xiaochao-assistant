"""Original-floor wall, tread, fill and door evidence for sparse observations.

The 3/6 client-pixel endpoint/wall limits match the existing structural solver.
Treads need six parallel edges (not one door/text stroke), >=12 pixel support,
and pitch gaps within a quarter period. Color is only corroboration: each of
two eroded fill classes needs 128 pixels, half coverage and 90% agreement.
These conservative visibility requirements deliberately reject lost/uneven
fill, door or tread evidence; they are not a lighting-invariance guarantee.
"""
import base64
from functools import lru_cache
import cv2
import numpy as np


def evidence_crop(frame, mask, padding=16):
    """Bound local operators by measured support plus their largest halo."""
    points=cv2.findNonZero(mask.astype(np.uint8))
    if points is None:return frame,mask,0,0
    x,y,w,h=cv2.boundingRect(points)
    x0,y0=max(0,x-padding),max(0,y-padding)
    x1,y1=min(mask.shape[1],x+w+padding),min(mask.shape[0],y+h+padding)
    return frame[y0:y1,x0:x1],mask[y0:y1,x0:x1],x0,y0


@lru_cache(maxsize=4)
def decoded_labels(png):
    return cv2.imdecode(np.frombuffer(base64.b64decode(png),np.uint8),cv2.IMREAD_GRAYSCALE)


def wang_structure_mask(route):
    """Same Wang reference predicates, fused uint8 operations on static pixels."""
    hsv=cv2.cvtColor(route[:,:,:3],cv2.COLOR_BGR2HSV)
    neutral=cv2.inRange(hsv,(0,0,68),(179,72,205))
    brown=cv2.inRange(hsv,(4,22,68),(32,175,205))
    reference=cv2.morphologyEx(cv2.bitwise_or(neutral,brown),cv2.MORPH_OPEN,np.ones((3,3),np.uint8))
    if route.shape[2]>=4:reference &= cv2.inRange(route[:,:,3],8,255)
    annotation=cv2.inRange(hsv,(0,82,105),(179,255,255))
    outline=cv2.inRange(hsv,(4,82,105),(32,255,255))
    reference |= outline
    annotation &= cv2.bitwise_not(outline)
    annotation=cv2.dilate(annotation,np.ones((5,5),np.uint8))
    reference &= cv2.bitwise_not(annotation)
    return reference//255

def static_features(frame, mask, component_box):
    """Build only from the complete original floor crop, never a live frame."""
    from single_map_structure import visible_wall_segments
    labels = semantic_regions(frame, mask)
    if labels is None:
        raise ValueError('Original floor lacks separable room and corridor fill')
    return dict(componentBox=list(component_box), shape=list(mask.shape),
        lines=visible_wall_segments(mask),
        strips=strips(frame, cv2.dilate(mask, np.ones((3,3), np.uint8))),
        doors=doors(frame, mask),
        labelsPng=base64.b64encode(cv2.imencode('.png', labels)[1]).decode('ascii'))


def observe(frame, library, *, cancelled=None, context=None):
    """Enumerate both complete floors; only one semantically verified pose wins."""
    from single_map_structure import current_geometry, current_part
    members = library.get('members', {})
    if len(members) != 2 or any(not m.get('sparseSemantics') for m in members.values()):
        return None
    if cancelled and cancelled():return None
    if context is None:context=current_geometry(frame)
    if context is None:return None
    query=context['query']
    lines=current_part(frame,context,'lines')
    live_strips=strips(frame,cv2.dilate(query,np.ones((3,3),np.uint8)))
    if not live_strips or not lines:return None
    labels=semantic_regions(frame,query)
    live_doors=doors(frame,query)
    if labels is None or not live_doors:return None
    accepted=[];diagnostics={}
    for member,data in members.items():
        if cancelled and cancelled():return None
        source=data['sparseSemantics']
        source_labels=decoded_labels(source['labelsPng'])
        if source_labels is None or list(source_labels.shape)!=source['shape']:return None
        enumeration={}
        candidates=hypotheses(lines,live_strips,source['lines'],source['strips'],cancelled=cancelled,diagnostics=enumeration)
        checks=[]
        for candidate in candidates:
            if cancelled and cancelled():return None
            validation=semantic_validation(labels,live_doors,source_labels,source['doors'],candidate['scale'],candidate['translation'])
            checks.append(validation)
            if validation and validation['accepted']:
                scale=float(candidate['scale']);tx,ty=map(float,candidate['translation'])
                accepted.append(dict(mapId=member,componentBox=source['componentBox'],
                    matrixRawToClient=[[scale,0,tx],[0,scale,ty],[0,0,1]],validation=validation))
        diagnostics[member]=dict(enumeration=enumeration,geometryCandidates=len(candidates),semanticChecks=checks)
    if cancelled and cancelled():return None
    if len(accepted)!=1:return None
    winner=accepted[0]
    return dict(attempted=True,confident=True,candidateMapId=winner['mapId'],
        currentFloorStructureCompetition=True,sparseRawFit=winner,
        semanticCompetition=diagnostics,reason='front-group-original-floor-semantic-witness')


def semantic_regions(frame,mask):
    full_mask=mask
    frame,mask,x0,y0=evidence_crop(frame,mask)
    local=_semantic_regions_patch(frame,mask)
    if local is None:return None
    labels=np.zeros(full_mask.shape,np.uint8)
    labels[y0:y0+local.shape[0],x0:x0+local.shape[1]]=local
    return labels


def _semantic_regions_patch(frame,mask):
    rgb=frame.astype(np.float32)
    ring=(cv2.dilate(mask.astype(np.uint8),np.ones((31,31),np.uint8))>0)&(cv2.dilate(mask.astype(np.uint8),np.ones((5,5),np.uint8))==0)
    if not ring.any():return None
    background=np.median(rgb[ring],axis=0)
    relative=rgb-background
    chroma=(relative[:,:,2]-relative[:,:,0])/np.maximum(np.abs(relative).sum(axis=2),1)
    interior=cv2.erode(mask.astype(np.uint8),np.ones((5,5),np.uint8))>0
    values=chroma[interior]
    if len(values)<128:return None
    quantized=np.uint8(np.clip((values+1)*127.5,0,255))
    threshold,_=cv2.threshold(quantized,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)
    split=float(threshold)/127.5-1
    low=values[values<=split];high=values[values>split]
    if not len(low) or not len(high):return None
    cold,warm=float(np.median(low)),float(np.median(high))
    if not cold<0<warm:return None
    labels=np.zeros(mask.shape,np.uint8)
    labels[interior&(chroma<=cold/2)]=1
    labels[interior&(chroma>=warm/2)]=2
    return labels

def doors(frame,mask):
    frame,mask,x0,y0=evidence_crop(frame,mask)
    result=_doors_patch(frame,mask)
    for door in result:
        door['center'][0]+=x0;door['center'][1]+=y0
    return result


def _doors_patch(frame,mask):
    hsv=cv2.cvtColor(frame,cv2.COLOR_BGR2HSV)
    yellow=((hsv[:,:,0]>=18)&(hsv[:,:,0]<=40)&(hsv[:,:,1]>=100)&(hsv[:,:,2]>=130)).astype(np.uint8)
    yellow &= cv2.dilate(mask.astype(np.uint8),np.ones((5,5),np.uint8))
    _,_,stats,_=cv2.connectedComponentsWithStats(yellow)
    result=[]
    for x,y,w,h,area in stats[1:]:
        if max(w,h)<6 or min(w,h)>6 or max(w,h)<2*min(w,h):continue
        result.append(dict(center=[float(x+(w-1)/2),float(y+(h-1)/2)],axis=0 if w>h else 1,
                           length=int(max(w,h))))
    # Original clean maps can draw an open doorway as yellow end caps;
    # the live closed panel occupies its midpoint. Only join a same-line
    # pair with a real wall gap between it, never two doors across a wall.
    openings=[]
    for i,first in enumerate(result):
        for second in result[i+1:]:
            axis=first['axis']
            if axis!=second['axis']:continue
            a=np.asarray(first['center']);b=np.asarray(second['center'])
            separation=abs(a[axis]-b[axis])
            if abs(a[1-axis]-b[1-axis])>3 or separation>4*max(first['length'],second['length']):continue
            if separation<max(first['length'],second['length']):continue
            midpoint=(a+b)/2;x,y=np.round(midpoint).astype(int)
            patch=frame[max(0,y-1):y+2,max(0,x-1):x+2].astype(float)
            if not patch.size:continue
            # The gap retains chromatic floor fill; desaturated wall white
            # between caps is not an opening.
            if np.median(np.max(patch,axis=2)-np.min(patch,axis=2))<22:continue
            openings.append(dict(center=midpoint.tolist(),axis=axis,length=separation,
                                 kind='open-doorway-midpoint'))
    result.extend(openings)
    return result

def semantic_validation(current_labels,current_doors,source_labels,source_doors,scale,translation):
    if current_labels is None or source_labels is None:return None
    matrix=np.array([[scale,0,translation[0]],[0,scale,translation[1]],[0,0,1.]])
    warped=cv2.warpPerspective(source_labels,matrix,(current_labels.shape[1],current_labels.shape[0]),flags=cv2.INTER_NEAREST)
    regions=[]
    for value in (1,2):
        selected=current_labels==value
        common=selected&(warped>0)
        regions.append(dict(label=value,count=int(common.sum()),coverage=float(common.sum()/max(1,selected.sum())),
            agreement=float(np.mean(warped[common]==value)) if common.any() else 0.))
    door_errors=[min([float(np.linalg.norm(np.asarray(sd['center'])*scale+translation-np.asarray(cd['center'])))
                      for sd in source_doors if sd['axis']==cd['axis']],default=float('inf')) for cd in current_doors]
    return dict(regions=regions,doorErrors=door_errors,
        accepted=all(r['count']>=128 and r['coverage']>=.5 and r['agreement']>=.9 for r in regions)
                 and bool(door_errors) and max(door_errors)<=6)

def strips(frame, mask=None):
    if mask is None:return _strips_patch(frame)
    frame,mask,x0,y0=evidence_crop(frame,mask)
    result=_strips_patch(frame,mask)
    for strip in result:
        cross_offset,along_offset=(x0,y0) if strip['axis']==0 else (y0,x0)
        strip['lo']+=along_offset;strip['hi']+=along_offset
        strip['crossLo']+=cross_offset;strip['crossHi']+=cross_offset
        for bar in strip['bars']:
            bar['cross']+=cross_offset;bar['lo']+=along_offset;bar['hi']+=along_offset
    return result


def _strips_patch(frame, mask=None):
    gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
    if mask is not None:
        contours,_=cv2.findContours(mask.astype(np.uint8),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        filled=np.zeros_like(mask,dtype=np.uint8)
        cv2.drawContours(filled,contours,-1,1,cv2.FILLED)
        mask=filled
    result=[]
    for axis in (0,1):
        gradient=cv2.Sobel(gray,cv2.CV_32F,1-axis,axis,ksize=3)
        binary=(gradient>36).astype(np.uint8)
        if mask is not None:binary[mask==0]=0
        kernel=np.ones((12,1) if axis==0 else (1,12),np.uint8)
        binary=cv2.morphologyEx(binary,cv2.MORPH_OPEN,kernel)
        _,_,stats,_=cv2.connectedComponentsWithStats(binary)
        bars=[]
        for x,y,w,h,area in stats[1:]:
            thickness,length=(w,h) if axis==0 else (h,w)
            if thickness>5 or length<12:continue
            bars.append(dict(cross=float(x+(w-1)/2) if axis==0 else float(y+(h-1)/2),
                lo=float(y) if axis==0 else float(x),hi=float(y+h-1) if axis==0 else float(x+w-1)))
        # Require both endpoints to agree, so adjacent letters/door edges do
        # not become a staircase merely by sharing an orientation.
        pending=set(range(len(bars)))
        while pending:
            seed=pending.pop();group=[seed];queue=[seed]
            while queue:
                i=queue.pop();a=bars[i]
                neighbors=[j for j in pending if abs(a['cross']-bars[j]['cross'])<=12
                    and abs(a['lo']-bars[j]['lo'])<=3 and abs(a['hi']-bars[j]['hi'])<=3]
                for j in neighbors:pending.remove(j);group.append(j);queue.append(j)
            if len(group)<6:continue
            members=sorted([bars[i] for i in group],key=lambda b:b['cross'])
            crosses=np.array([b['cross'] for b in members])
            gaps=np.diff(crosses);pitch=float(np.median(gaps))
            if pitch<=0:continue
            breaks=np.flatnonzero(np.abs(gaps/pitch-np.round(gaps/pitch))>.25)+1
            for indexes in np.split(np.arange(len(members)),breaks):
                if len(indexes)<6:continue
                regular=[members[i] for i in indexes]
                result.append(dict(axis=axis,count=len(regular),lo=float(np.median([b['lo'] for b in regular])),
                    hi=float(np.median([b['hi'] for b in regular])),crossLo=regular[0]['cross'],
                    crossHi=regular[-1]['cross'],pitch=pitch,bars=regular))
    return result

def corners(lines):
    result=[]
    for h in lines:
        if h['axis']!=1:continue
        for v in lines:
            if v['axis']!=0:continue
            if min(abs(v['normal']-h['lo']),abs(v['normal']-h['hi']))>3:continue
            if min(abs(h['normal']-v['lo']),abs(h['normal']-v['hi']))>3:continue
            result.append(dict(point=np.array([v['normal'],h['normal']]),hSign=h['sign'],vSign=v['sign'],
                hEnd='lo' if abs(v['normal']-h['lo'])<abs(v['normal']-h['hi']) else 'hi',
                vEnd='lo' if abs(h['normal']-v['lo'])<abs(h['normal']-v['hi']) else 'hi'))
    return result

def hypotheses(observed_lines, observed_strips, source_lines, source_strips, *, cancelled=None, diagnostics=None):
    accepted=[]
    observed_corners=corners(observed_lines);source_corners=corners(source_lines)
    counts=dict(observedCorners=len(observed_corners),sourceCorners=len(source_corners),sourceStrips=len(source_strips),
        compatibleCornerPairs=0,parallelStripPairs=0,nonpositiveOrShortSeparation=0,stripExtentRejected=0,wallRejected=0)
    if diagnostics is not None:diagnostics.update(counts);counts=diagnostics
    for oc in observed_corners:
        for sc in source_corners:
            if cancelled and cancelled():return []
            if any(oc[k]!=sc[k] for k in ('hSign','vSign','hEnd','vEnd')):continue
            counts['compatibleCornerPairs']+=1
            for ot in observed_strips:
                for st in source_strips:
                    if ot['axis']!=st['axis']:continue
                    counts['parallelStripPairs']+=1
                    axis=1-ot['axis']
                    source_delta=(st['lo']+st['hi'])/2-sc['point'][axis]
                    observed_delta=(ot['lo']+ot['hi'])/2-oc['point'][axis]
                    if abs(source_delta)<12:
                        counts['nonpositiveOrShortSeparation']+=1;continue
                    scale=observed_delta/source_delta
                    if not np.isfinite(scale) or scale<=0:
                        counts['nonpositiveOrShortSeparation']+=1;continue
                    translation=oc['point']-scale*sc['point']
                    cross_axis=ot['axis']
                    if (scale*st['crossLo']+translation[cross_axis]>ot['crossLo']+6 or
                        scale*st['crossHi']+translation[cross_axis]<ot['crossHi']-6):
                        counts['stripExtentRejected']+=1;continue
                    if scale*(st['hi']-st['lo'])<(ot['hi']-ot['lo'])-6:
                        counts['stripExtentRejected']+=1;continue
                    errors=[]
                    for line in observed_lines:
                        axis=line['axis']
                        choices=[sl for sl in source_lines if sl['axis']==axis and sl['sign']==line['sign']
                            and scale*sl['lo']+translation[1-axis]<=line['lo']+3
                            and scale*sl['hi']+translation[1-axis]>=line['hi']-3]
                        errors.append(min([abs(scale*sl['normal']+translation[axis]-line['normal']) for sl in choices],default=float('inf')))
                    if not errors or max(errors)>6:
                        counts['wallRejected']+=1;continue
                    accepted.append(dict(scale=scale,translation=translation,errors=errors,
                        sourceCorner=sc,sourceStrip=st,observedStrip=ot))
    return accepted
