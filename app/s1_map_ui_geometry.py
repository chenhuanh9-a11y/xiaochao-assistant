"""Current S1 map-UI shape evidence, independent of floor/canvas content.

Templates below are programmatic geometric primitives, not saved game assets.
A close X, distinct circular +/- buttons and their connecting straight rail
must occur as one correctly spaced group outside the entire floor search ROI.
No single edge, color region, numeral or prior observation can satisfy this.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Mapping

import cv2
import numpy as np

from client_space_geometry import centered_safe_viewport

# Positions, sizes and group distances share the centered safe HUD viewport.
SEARCH = {'close': (.92, .02, .99, .18), 'plus': (.92, .25, .99, .46),
          'minus': (.92, .66, .99, .86)}
SIZES = (.024, .026, .028, .030, .032, .034, .036, .038)
MIN_CORRELATION = {'close': .62, 'plus': .65, 'minus': .65}


@dataclass(frozen=True)
class MapUiGeometryParameters:
    """Validated search bounds, scale fractions and score thresholds."""
    search: Mapping[str, tuple[float, float, float, float]]
    sizes: tuple[float, ...]
    minimum_correlation: Mapping[str, float]


@lru_cache(maxsize=96)
def _template(kind, size):
    out = np.zeros((size, size), np.uint8)
    if kind == 'close':
        pad, thick = round(size*.12), max(2, round(size*.20))
        cv2.line(out, (pad,pad), (size-1-pad,size-1-pad), 255, thick, cv2.LINE_AA)
        cv2.line(out, (size-1-pad,pad), (pad,size-1-pad), 255, thick, cv2.LINE_AA)
    else:
        center = (size//2,size//2)
        cv2.circle(out, center, round(size*.45), 180, -1, cv2.LINE_AA)
        low, high, thick = round(size*.19), size-1-round(size*.19), max(2,round(size*.12))
        cv2.line(out, (low,center[1]), (high,center[1]), 30, thick, cv2.LINE_AA)
        if kind == 'plus':
            cv2.line(out, (center[0],low), (center[0],high), 30, thick, cv2.LINE_AA)
    out = cv2.GaussianBlur(out, (0,0), max(.7,size*(.025 if kind=='close' else .02)))
    out.setflags(write=False)
    return out


def _glyph_contrasts(patch, kind):
    """Verify both X diagonals, and distinguish a plus from a minus disk."""
    p = cv2.resize(patch, (64,64), interpolation=cv2.INTER_AREA).astype(np.float32)
    yy, xx = np.mgrid[:64,:64];x=(xx-31.5)/64;y=(yy-31.5)/64
    if kind == 'close':
        contrasts=[]
        for sx,sy in ((1,1),(1,-1),(-1,1),(-1,-1)):
            quadrant=(x*sx>.10)&(y*sy>.10)&(np.abs(x)<.39)&(np.abs(y)<.39)
            ray=quadrant&(np.abs(np.abs(x)-np.abs(y))<.065)
            flank=quadrant&(np.abs(np.abs(x)-np.abs(y))>.17)
            contrasts.append(float(p[ray].mean()-p[flank].mean()))
        return contrasts
    ax,ay=np.abs(x),np.abs(y)
    surround=(ax>.12)&(ax<.24)&(ay>.12)&(ay<.24)
    horizontal=(ay<.055)&(ax>.12)&(ax<.25)
    vertical=(ax<.055)&(ay>.12)&(ay<.25)
    background=float(p[surround].mean())
    return [background-float(p[horizontal].mean()), background-float(p[vertical].mean())]


def _match(gray, width, viewport, kind, floor_right, parameters=None):
    roi=(SEARCH if parameters is None else parameters.search)[kind]
    sizes=SIZES if parameters is None else parameters.sizes
    minimum=(MIN_CORRELATION if parameters is None else parameters.minimum_correlation)[kind]
    x0=max(floor_right,round(viewport.left+roi[0]*viewport.width))
    y0=round(viewport.top+roi[1]*viewport.height)
    x1=round(viewport.left+roi[2]*viewport.width)
    y1=round(viewport.top+roi[3]*viewport.height)
    image=gray[y0:y1,x0:x1]
    if image.size==0:
        return None
    candidates=[]
    for fraction in sizes:
        size=max(12,round(width*fraction))
        if size>=min(image.shape):
            continue
        scores=cv2.matchTemplate(image,_template(kind,size),cv2.TM_CCOEFF_NORMED)
        # Inspect bounded separated peaks, including peaks rejected by the
        # stroke checks. An unresolved surplus fails closed. Nearby scale and
        # translation variants of one icon are merged below.
        for _ in range(8):
            _,score,_,where=cv2.minMaxLoc(scores)
            if not np.isfinite(score) or score<minimum:
                break
            x,y=where;patch=image[y:y+size,x:x+size]
            contrasts=_glyph_contrasts(patch,kind)
            strokes=(min(contrasts)>=12 if kind=='close' else
                     contrasts[0]>=12 and (contrasts[1]>=12 if kind=='plus' else contrasts[1]<8))
            if strokes:
                candidates.append(dict(score=float(score),bounds=[x0+x,y0+y,size,size],
                          center=[x0+x+size/2,y0+y+size/2],contrasts=contrasts))
            radius=max(1,round(size*.65))
            scores[max(0,y-radius):y+radius+1,max(0,x-radius):x+radius+1]=-1
        else:
            if float(scores.max())>=minimum:
                return None
    distinct=[]
    for candidate in sorted(candidates,key=lambda item:item['score'],reverse=True):
        if any(np.linalg.norm(np.subtract(candidate['center'],old['center']))<=width*.010
               for old in distinct):
            continue
        distinct.append(candidate)
        if len(distinct)>1:
            return None
    return distinct[0] if distinct else None



def _rail(gray, width, plus, minus, floor_right):
    xp,yp=plus['center'];xm,ym=minus['center']
    start,end=round(yp+.025*width),round(ym-.025*width)
    if end<=start:
        return None
    middle=round((xp+xm)/2);half=max(1,round(.002*width));side=max(half+1,round(.010*width))
    band=gray[start:end].astype(np.float32)
    best=None
    for x in range(middle-round(.005*width),middle+round(.005*width)+1):
        if x-side-half<floor_right or x+side+half>=gray.shape[1]:
            continue
        center=band[:,x-half:x+half+1].mean(axis=1)
        left=band[:,x-side-half:x-side+half+1].mean(axis=1)
        right=band[:,x+side-half:x+side+half+1].mean(axis=1)
        support=float((center-np.maximum(left,right)>=12).mean())
        if best is None or support>best['support']:
            best=dict(support=support,x=x,startY=start,endY=end)
    return best if best is not None and best['support']>=.85 else None


def observe_s1_map_ui_geometry(frame, *, floor_search_right=.92, minimum_height=600, parameters=None):
    """Finite per-frame evidence only; no map/floor/runtime authority here.

    S1 retains its 600px default. S0 may pass its independently replayed
    minimum; all size-relative shape, alignment and rail checks still apply.
    """
    if (not isinstance(frame,np.ndarray) or frame.dtype!=np.uint8
            or frame.ndim!=3 or frame.shape[2]!=3):
        return dict(verified=False,reason='s1-map-ui-frame-invalid')
    height,width=frame.shape[:2]
    if width<1000 or height<minimum_height:
        return dict(verified=False,reason='s1-map-ui-size-unsupported')
    viewport=centered_safe_viewport((width,height))
    scale_width=viewport.width
    if (type(floor_search_right) not in (int,float) or not 0<=floor_search_right<=1):
        return dict(verified=False,reason='s1-map-ui-search-boundary-invalid')
    right=round(viewport.left+scale_width*floor_search_right)
    if not 0<=right<width:
        return dict(verified=False,reason='s1-map-ui-empty-search-band')
    # Isolate bytes before *all* preprocessing. Blurring the full frame would
    # let floor pixels across the boundary change external shape evidence.
    # Keep global coordinates, but never sample or convolve the floor ROI.
    band=np.ascontiguousarray(frame[:,right:])
    if not band.size:
        return dict(verified=False,reason='s1-map-ui-empty-search-band')
    gray=np.zeros((height,width),np.uint8)
    gray[:,right:]=cv2.GaussianBlur(cv2.cvtColor(band,cv2.COLOR_BGR2GRAY),
                                  (0,0),max(1,scale_width/1920))
    shapes={}
    for kind in ('close','plus','minus'):
        shape=_match(gray,scale_width,viewport,kind,right,parameters)
        if shape is None:
            return dict(verified=False,reason='s1-map-ui-'+kind+'-unverified',shapes=shapes)
        shapes[kind]=shape
    close,plus,minus=(shapes[k] for k in ('close','plus','minus'))
    xs=[s['center'][0] for s in (close,plus,minus)]
    close_gap=(plus['center'][1]-close['center'][1])/scale_width
    zoom_gap=(minus['center'][1]-plus['center'][1])/scale_width
    size_ratio=plus['bounds'][2]/minus['bounds'][2]
    if (max(xs)-min(xs)>.010*scale_width or not .13<=close_gap<=.18
            or not .22<=zoom_gap<=.28 or not .85<=size_ratio<=1.15):
        return dict(verified=False,reason='s1-map-ui-group-misaligned',shapes=shapes)
    rail=_rail(gray,scale_width,plus,minus,right)
    if rail is None:
        return dict(verified=False,reason='s1-map-ui-rail-unverified',shapes=shapes)
    return dict(verified=True,reason='s1-map-ui-shape-group',shapes=shapes,rail=rail)
