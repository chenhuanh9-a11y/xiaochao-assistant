"""Known-floor steady alignment adapted from IDVB 0059's VPSG3 solver.

Only the caller's trusted same-floor scale may seed a locked solve. Coverage
milestones and weak steady fits compare an independent current-frame scale;
translation always comes from this frame. This module proposes, never commits,
pose and coverage state. The caller owns reset and the legacy fallback.

The numerical search, scale policy, precision refinement and measurements follow
the 0059 TryAlignWithVpsg3 wrapper. Representation adaptations: Xiaochao's filled
renderer footprint is converted to a contour during prepare(), and NumPy byte
maps/strided accumulation replace C# packed bitsets. Neither adaptation imports
author imagery, historical frame geometry, or a previous translation.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import math
import threading
import time

import cv2
import numpy as np


MAX_SPARSE_POINTS = 150
MIN_SCALE, MAX_SCALE = .40, 2.40
MIN_SCORE, MIN_MARGIN, MIN_DISTINCT = .50, .09, 10.
COARSE_STRIDE = 4
_K3 = np.ones((3, 3), np.uint8)
_K5 = np.ones((5, 5), np.uint8)
_K11 = np.ones((11, 11), np.uint8)
_cache = OrderedDict()
_cache_lock = threading.Lock()


@dataclass(frozen=True)
class PreparedReference:
    edge: np.ndarray
    k3: np.ndarray
    k5: np.ndarray
    shape: tuple[int, int]
    binding: tuple
    representation: str
    distance: np.ndarray | None = None
    reference_points: np.ndarray | None = None
    pitch: float = 0.
    pitch_ratio: float = 0.


@dataclass(frozen=True)
class Observation:
    points: np.ndarray
    observed_edges: np.ndarray
    proposal_edges: np.ndarray
    valid_mask: np.ndarray
    origin: tuple[int, int]


def prepare(reference) -> PreparedReference:
    """Prepare and retain immutable static arrays, scoped to one resource object.

    Reference objects are held with the cache entry so an id cannot be recycled
    into a different map/floor. Replacing a resource requires a new reference
    object, as with the existing shared-reference provider.
    """
    if isinstance(reference, PreparedReference):
        return reference
    key = id(reference)
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and hit[0] is reference:
            _cache.move_to_end(key)
            return hit[1]
    raw = np.asarray(reference.clean_mask)
    if raw.ndim != 2 or raw.dtype != np.uint8 or not raw.size:
        raise ValueError('ReferenceCleanMaskInvalid')
    mask = np.ascontiguousarray(raw > 0, dtype=np.uint8)
    # The shared field is a room/corridor alpha footprint, not a prebuilt line.
    # Taking its boundary here is essential: filled rooms would make almost
    # every translation score well and destroy the aperture competition.
    edge = cv2.morphologyEx(mask, cv2.MORPH_GRADIENT, _K3)
    unknown = getattr(reference, 'unknown_mask', None)
    if unknown is not None:
        unknown = np.asarray(unknown)
        if unknown.shape != edge.shape:
            raise ValueError('ReferenceUnknownMaskShapeMismatch')
        edge[cv2.dilate((unknown > 0).astype(np.uint8), _K3) > 0] = 0
    if cv2.countNonZero(edge) < 300:
        raise ValueError('ReferenceInsufficientEdges')
    k3, k5 = cv2.dilate(edge, _K3), cv2.dilate(edge, _K5)
    distance = cv2.distanceTransform(1-edge, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    reference_points = cv2.findNonZero(edge).reshape(-1, 2)
    pitch, pitch_ratio = _dominant_pitch(np.count_nonzero(edge, axis=0), interpolate=False)
    for array in (edge, k3, k5, distance, reference_points):
        array.setflags(write=False)
    metadata = getattr(reference, 'metadata', {})
    binding = metadata.get('binding', {})
    prepared = PreparedReference(edge, k3, k5, edge.shape,
        tuple((str(k), str(v)) for k, v in sorted(binding.items())),
        'shared-renderer-footprint-boundary; numpy-byte-index', distance, reference_points, pitch, pitch_ratio)
    with _cache_lock:
        _cache[key] = (reference, prepared)
        while len(_cache) > 6:
            _cache.popitem(last=False)
    return prepared


def extract(frame) -> Observation:
    """0059 semantic contours, photometric support, HUD/fog validity and sampling."""
    frame = np.asarray(frame)
    if frame.dtype != np.uint8 or frame.ndim not in (2, 3) or not frame.size:
        raise ValueError('FrameInvalid')
    if frame.ndim == 2:
        bgr = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    elif frame.shape[2] == 4:
        bgr = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
    elif frame.shape[2] == 3:
        bgr = frame
    else:
        raise ValueError('FrameChannelsUnsupported')
    client_h, client_w = frame.shape[:2]
    # SessionOrchestrator.AutomaticMapViewport's broad client-anchored canvas.
    vx, vy = math.floor(.10*client_w), math.floor(.12*client_h)
    right, bottom = math.ceil(.88*client_w), math.ceil(.94*client_h)
    bgr = bgr[vy:bottom, vx:right]
    h, w = bgr.shape[:2]
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    exclusion = np.zeros((h, w), np.uint8)
    # The actual 0059 full-map entry supplies these regions through
    # MapFrameUiExclusion.WithAutomaticCanvasContext. Its extractor's dynamic
    # fallback alone admits task legends and right-side controls as walls.
    cw, ch = client_w, client_h
    regions = [(0., 0., .18*cw, ch), (.185*cw, ch-.245*cw, .15*cw, .215*cw),
               (.18*cw, 0., .155*cw, .235*cw), (.75*cw, 0., .14*cw, .07*cw),
               (.91*cw, 0., .09*cw, ch), (.52*cw, ch-.09*cw, .23*cw, .09*cw),
               (.80*cw, ch-.12*cw, .20*cw, .12*cw)]
    for x, y, width, height in regions:
        x0, y0 = max(0, math.floor(x-vx)), max(0, math.floor(y-vy))
        x1, y1 = min(w, math.ceil(x+width-vx)), min(h, math.ceil(y+height-vy))
        if x1 > x0 and y1 > y0:
            exclusion[y0:y1, x0:x1] = 255
    room = cv2.bitwise_or(cv2.inRange(hsv, (0, 18, 82), (25, 165, 200)),
                          cv2.inRange(hsv, (170, 18, 82), (179, 165, 200)))
    corridor = cv2.inRange(hsv, (95, 14, 82), (130, 105, 200))
    candidates = np.zeros((h, w), np.uint8)
    batch = []
    for mask in (room, corridor):
        mask[exclusion > 0] = 0
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, _K5)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, _K3)
        contours, hierarchy = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        if hierarchy is None:
            continue
        for contour, relation in zip(contours, hierarchy[0]):
            if cv2.arcLength(contour, True) < 30:
                continue
            if relation[3] != -1 and abs(cv2.contourArea(contour)) < 900:
                continue
            batch.append(cv2.approxPolyDP(contour, .55, True))
    if batch:
        cv2.drawContours(candidates, batch, -1, 255, 2, cv2.LINE_8)
    excluded = cv2.dilate(exclusion, _K5)
    candidates[excluded > 0] = 0
    # Photometric work only needs the envelope of *all* semantic candidates.
    # A client-width 3% halo exceeds the finite blur/dilation dependencies and
    # preserves disconnected rooms. Never choose a single bright component.
    bx, by, bw, bh = cv2.boundingRect(candidates)
    padding = max(8, round(client_w*.03))
    x0, y0 = max(0, bx-padding), max(0, by-padding)
    x1, y1 = min(w, bx+bw+padding), min(h, by+bh+padding)
    if not bw or not bh:
        empty = np.zeros((h, w), np.uint8)
        return Observation(np.empty((0, 2), np.int32), empty, empty.copy(),
                           cv2.bitwise_not(excluded), (vx, vy))
    bgr = bgr[y0:y1, x0:x1]
    candidates = candidates[y0:y1, x0:x1]
    excluded = excluded[y0:y1, x0:x1]
    vx, vy = vx+x0, vy+y0
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    strong = cv2.dilate(cv2.Canny(gray, 80, 180, apertureSize=3, L2gradient=True), _K5)
    detail = cv2.subtract(gray, cv2.GaussianBlur(gray, (5, 5), 1.5))
    detail = cv2.dilate(cv2.threshold(detail, 4, 255, cv2.THRESH_BINARY)[1], _K3)
    weak = cv2.Canny(gray, 25, 65, apertureSize=3, L2gradient=True)
    weak = cv2.dilate(cv2.bitwise_and(weak, detail), _K3)
    support = cv2.bitwise_or(strong, weak)
    observed = cv2.bitwise_and(candidates, support)
    proposal = cv2.bitwise_and(candidates, strong)
    frontier = cv2.dilate(cv2.bitwise_and(candidates, cv2.bitwise_not(support)), _K11)
    valid = cv2.bitwise_not(cv2.bitwise_or(frontier, excluded))
    valid[observed > 0] = 255
    # The native entry extracts once to select this envelope and extracts
    # again after cropping. Reuse this frame's masks instead; no previous frame
    # evidence enters this observation. Sampling/quadrants use the cropped ROI.
    bx, by, bw, bh = cv2.boundingRect(proposal)
    if bw and bh:
        x0, y0 = max(0, bx-padding), max(0, by-padding)
        x1, y1 = min(proposal.shape[1], bx+bw+padding), min(proposal.shape[0], by+bh+padding)
        observed = observed[y0:y1, x0:x1]
        proposal = proposal[y0:y1, x0:x1]
        valid = valid[y0:y1, x0:x1]
        vx, vy = vx+x0, vy+y0
    nonzero = cv2.findNonZero(proposal)
    if nonzero is None:
        points = np.empty((0, 2), np.int32)
    else:
        nonzero = nonzero.reshape(-1, 2)
        count = min(len(nonzero), MAX_SPARSE_POINTS)
        points = nonzero[(np.arange(count)*len(nonzero)/count).astype(np.int64)]
    return Observation(points, observed, proposal, valid, (vx, vy))


def _cancelled(cancelled):
    return cancelled is not None and bool(cancelled() if callable(cancelled) else cancelled.is_set())


def _lookup(mask, xy):
    h, w = mask.shape
    xx, yy = xy[..., 0], xy[..., 1]
    valid = (xx >= 0) & (xx < w) & (yy >= 0) & (yy < h)
    return (mask[np.clip(yy, 0, h-1), np.clip(xx, 0, w-1)] != 0) & valid


def _coarse_grid(points, prepared, scale, cancelled, origin=(0, 0)):
    """Same integer constellation votes as T-3, using 150 strided C additions."""
    scaled = np.rint(points / scale).astype(np.int32)
    n = len(scaled)
    misses = min(n-1, max(0, n-math.ceil(MIN_SCORE*n)))
    ordered = np.sort(scaled, axis=0)
    lo = -ordered[misses]-2
    hi = np.asarray(prepared.shape[::-1])+1-ordered[n-1-misses]
    if np.any(hi < lo):
        return [], dict(gridCells=0)
    xs = np.arange(lo[0], hi[0]+1, COARSE_STRIDE, dtype=np.int32)
    ys = np.arange(lo[1], hi[1]+1, COARSE_STRIDE, dtype=np.int32)
    scores = np.zeros((len(ys), len(xs)), np.uint16)
    h, w = prepared.shape
    for index, (px, py) in enumerate(scaled):
        if index % 16 == 0 and _cancelled(cancelled):
            return [], dict(cancelled=True, gridCells=scores.size)
        x0 = max(0, int((-int(px)-int(lo[0])+COARSE_STRIDE-1)//COARSE_STRIDE))
        y0 = max(0, int((-int(py)-int(lo[1])+COARSE_STRIDE-1)//COARSE_STRIDE))
        x1 = min(len(xs), int((w-1-int(px)-int(lo[0]))//COARSE_STRIDE+1))
        y1 = min(len(ys), int((h-1-int(py)-int(lo[1]))//COARSE_STRIDE+1))
        if x0 >= x1 or y0 >= y1:
            continue
        rx, ry = int(xs[x0]+px), int(ys[y0]+py)
        scores[y0:y1, x0:x1] += prepared.k3[
            ry:ry+(y1-y0)*COARSE_STRIDE:COARSE_STRIDE,
            rx:rx+(x1-x0)*COARSE_STRIDE:COARSE_STRIDE]
    flat = scores.ravel()
    count = min(32, flat.size)
    threshold = max(5, int(np.partition(flat, flat.size-count)[flat.size-count]))
    ids = np.flatnonzero(flat >= threshold)
    if not len(ids):
        return [], dict(gridCells=scores.size)
    ids = ids[np.lexsort((ids, -flat[ids].astype(np.int32)))[:32]]

    def polish(dx, dy, score):
        # Center wins ties, then the source's y-major/x-major scan order.
        cx = np.arange(max(int(lo[0]), dx-2), min(int(hi[0]), dx+2)+1)
        cy = np.arange(max(int(lo[1]), dy-2), min(int(hi[1]), dy+2)+1)
        xx, yy = np.meshgrid(cx, cy)
        offsets = np.column_stack((xx.ravel(), yy.ravel()))
        votes = _lookup(prepared.k3, scaled[None, :, :]+offsets[:, None, :]).sum(axis=1)
        best = int(np.argmax(votes))
        return (int(offsets[best, 0]), int(offsets[best, 1]), int(votes[best])) if votes[best] > score else (dx, dy, int(score))

    polished = [polish(int(xs[i % len(xs)]), int(ys[i // len(xs)]), int(flat[i])) for i in ids]
    polished.sort(key=lambda row: -row[2])
    peaks = []
    for row in polished:
        if all(abs(row[0]-p[0]) > 2 or abs(row[1]-p[1]) > 2 for p in peaks):
            peaks.append(row)
    best = peaks[0]
    distinct2 = (MIN_DISTINCT/scale)**2
    runners = [p for p in peaks[1:] if (p[0]-best[0])**2+(p[1]-best[1])**2 >= distinct2][:2]
    if not runners:
        distinct = (xs[None, :]-best[0])**2+(ys[:, None]-best[1])**2 >= distinct2
        other_scores = np.where(distinct, scores, 0)
        idx = int(np.argmax(other_scores))
        hits = int(other_scores.ravel()[idx])
        if hits >= 3:
            row = polish(int(xs[idx % len(xs)]), int(ys[idx // len(xs)]), hits)
            if (row[0]-best[0])**2+(row[1]-best[1])**2 >= distinct2:
                runners.append(row)
    seeds = [best]+runners
    retained = [p for p in peaks[1:] if p not in runners]
    return [(origin[0]-x*scale, origin[1]-y*scale, hits) for x, y, hits in seeds+retained], dict(
        gridCells=int(scores.size), coarsePeakScore=best[2], initialCandidates=len(seeds), retainedCandidates=len(peaks))


def _weighted(points, prepared, scale, translations):
    scales = np.asarray(scale, np.float64)
    if scales.ndim:
        scales = scales[:, None, None]
    xy = np.rint((points[None, :, :]-translations[:, None, :])/scales).astype(np.int32)
    k3 = _lookup(prepared.k3, xy)
    k5 = _lookup(prepared.k5, xy)
    return (k5.sum(axis=1)+2*k3.sum(axis=1))/(3.*len(points))


def _refine_many(points, prepared, scale, seeds):
    initial = np.asarray([seed[:2] for seed in seeds], np.float64)
    coarse = np.asarray([(x, y) for x in range(-6, 7, 2) for y in range(-6, 7, 2)
                         if x or y], np.float64)
    translations = initial[:, None, :]+np.vstack((np.zeros((1, 2)), coarse))[None, :, :]
    values = _weighted(points, prepared, scale, translations.reshape(-1, 2)).reshape(len(seeds), -1)
    indices = np.argmax(values, axis=1)
    best = translations[np.arange(len(seeds)), indices]
    fine = np.asarray([(x, y) for x in (-1.5, 0., 1.5) for y in (-1.5, 0., 1.5)
                       if x or y], np.float64)
    translations = best[:, None, :]+np.vstack((np.zeros((1, 2)), fine))[None, :, :]
    values = _weighted(points, prepared, scale, translations.reshape(-1, 2)).reshape(len(seeds), -1)
    indices = np.argmax(values, axis=1)
    return [dict(scale=scale, offset=translations[i, index], weightedScore=float(values[i, index]), probes=56,
                 rawScore=int(seeds[i][2])) for i, index in enumerate(indices)]


def _spatial(observation, prepared, scale, candidate):
    points = observation.points
    points = points[observation.valid_mask[points[:, 1], points[:, 0]] >= 128]
    h, w = observation.valid_mask.shape
    xy = np.rint((points+np.asarray(observation.origin)-candidate['offset'])/scale).astype(np.int32)
    hits = _lookup(prepared.k5, xy)
    parts = (points[:, 0] >= w//2).astype(np.int32)+2*(points[:, 1] >= h//2)
    total = np.bincount(parts, minlength=4)
    counts = np.bincount(parts, weights=hits, minlength=4).astype(np.int32)
    ratios = counts/np.maximum(1, total)
    valid_parts = int(np.count_nonzero(total >= 5))
    passed = int(np.count_nonzero((total >= 5) & (ratios >= .50)))
    hit_count, n = int(hits.sum()), len(points)
    global_score = hit_count/max(1, n)
    largest = int(np.argmax(total))
    dominant = total[largest] >= n*.60 and ratios[largest] >= .50 and global_score >= .50 and hit_count >= 20
    consistent = passed >= 2 or (valid_parts > 0 and passed == valid_parts and global_score >= .50) or dominant or (global_score >= .55 and hit_count >= 25 and passed >= 1)
    candidate.update(globalScore=global_score, totalValidPoints=n, hitPoints=hit_count,
                     validPartitions=valid_parts, passedPartitions=passed,
                     partitionPoints=total.tolist(), partitionHits=counts.tolist(), spatiallyConsistent=bool(consistent))
    return candidate


def _dominant_pitch(signal, minimum=12., maximum=float('inf'), *, interpolate=True):
    signal = np.asarray(signal, np.float64)
    n = len(signal)
    if n < 40:
        return 0., 0.
    centered = signal-signal.mean()
    variance = float(centered@centered)
    if variance < 1e-9:
        return 0., 0.
    lags = np.arange(12, n//2)
    raw = np.correlate(centered, centered, 'full')[n-1+12:n-1+n//2]/variance
    admitted = np.flatnonzero((lags >= minimum) & (lags <= maximum))
    if not len(admitted):
        return 0., 0.
    index = int(admitted[np.argmax(raw[admitted])])
    peak, best_lag = float(raw[index]), int(lags[index])
    if peak <= .05:
        return 0., 0.
    start = max(13, math.ceil(minimum))
    end = min(n//2-1, math.floor(maximum)) if math.isfinite(maximum) else n//2-1
    for lag in range(start, min(best_lag-1, end)):
        i = lag-12
        if not 0 < i < len(raw)-1:
            continue
        ratio = best_lag/lag
        if (raw[i] > raw[i-1] and raw[i] > raw[i+1] and raw[i] >= .80*peak
                and round(ratio) >= 2 and abs(ratio-round(ratio)) < .15):
            best_lag, index, peak = lag, i, float(raw[i])
            break
    median = float(np.sort(np.abs(raw))[len(raw)//2])
    pitch = float(best_lag)
    if interpolate and 0 < index < len(raw)-1:
        a, b, c = raw[index-1:index+2]
        denominator = 2*(2*b-a-c)
        if b > a and b > c and denominator > 1e-9:
            delta = (c-a)/denominator
            if abs(delta) <= .5:
                pitch += float(delta)
    return pitch, peak/max(.01, median)


def _refine_unlocked(points, prepared, seed_scale, seed, observation):
    center = np.asarray(observation.origin)+np.asarray(observation.valid_mask.shape[::-1])/2.
    anchor = (center-np.asarray(seed[:2]))/seed_scale
    best_scale, best_offset = seed_scale, np.asarray(seed[:2], np.float64)
    best_score = float(_weighted(points, prepared, seed_scale, best_offset[None, :])[0])
    probes = 0

    def scan(scales, offsets):
        nonlocal best_scale, best_offset, best_score, probes
        scores = _weighted(points, prepared, np.asarray(scales), np.asarray(offsets))
        probes += len(scales)
        winner = int(np.argmax(scores))
        if scores[winner] > best_score:
            best_scale, best_offset, best_score = float(scales[winner]), np.asarray(offsets[winner]), float(scores[winner])

    candidates = [(seed_scale+ds, center-anchor*(seed_scale+ds)+[dx, dy])
        for ds in (-.020, -.015, 0., .015, .020) if .35 <= seed_scale+ds <= 2.50
        for dx in range(-6, 7, 2) for dy in range(-6, 7, 2)]
    scan(*zip(*candidates))
    anchor = (center-best_offset)/best_scale
    candidates = [(best_scale+ds, center-anchor*(best_scale+ds))
                  for ds in (-.020, -.010, -.005, .005, .010, .020) if .35 <= best_scale+ds <= 2.50]
    scan(*zip(*candidates))
    anchor = (center-best_offset)/best_scale
    candidates = [(best_scale+ds, center-anchor*(best_scale+ds)+[dx, dy])
                  for ds in (-.005, 0., .005) for dx in (-1.5, 0., 1.5) for dy in (-1.5, 0., 1.5)
                  if ds or dx or dy]
    scan(*zip(*candidates))
    return dict(scale=best_scale, offset=best_offset, weightedScore=best_score, probes=probes, rawScore=int(seed[2]))


def _fresh_scale_solve(observation, prepared, cancelled):
    started = time.perf_counter()
    info = dict(referencePitch=prepared.pitch, referencePeakRatio=prepared.pitch_ratio)
    reason = ''
    if prepared.pitch <= 5 or prepared.pitch_ratio < 2:
        reason = 'PreparedFloorReferenceIneligible'
    elif cv2.countNonZero(observation.observed_edges) < 300:
        reason = 'ObservationEdgePixelsBelowThreshold'
    else:
        pitch, ratio = _dominant_pitch(np.count_nonzero(observation.proposal_edges, axis=0),
                                      prepared.pitch*MIN_SCALE, prepared.pitch*MAX_SCALE)
        ratio = min(ratio, prepared.pitch_ratio)
        info.update(livePitch=pitch, peakRatio=ratio)
        if ratio < 2 or pitch <= 5:
            reason = 'ScalePeakRatioBelowThreshold'
        elif not MIN_SCALE <= pitch/prepared.pitch <= MAX_SCALE:
            reason = 'EstimatedScaleOutOfRange'
    info['milliseconds'] = (time.perf_counter()-started)*1000
    if reason:
        return dict(accepted=False, reason=reason, testedScaleHypotheses=1,
                    diagnostics=dict(independentScale=info, timingMs={'total': info['milliseconds']}))
    seed = pitch/prepared.pitch
    result = _bootstrap(observation, prepared, seed, cancelled=cancelled, lock_scale=False)
    info['seed'] = seed
    result['diagnostics']['independentScale'] = info
    best = result['diagnostics'].get('best')
    # The bootstrap must explore the next scale basin when joint refinement
    # reaches its +/-0.045 search edge, including a presently rejected result.
    if best and abs(best['scale']-seed) >= .044 and not _cancelled(cancelled):
        next_scale = best['scale']+math.copysign(.03, best['scale']-seed)
        if MIN_SCALE <= next_scale <= MAX_SCALE:
            expanded = _bootstrap(observation, prepared, next_scale, cancelled=cancelled, lock_scale=True)
            result['testedScaleHypotheses'] += expanded['testedScaleHypotheses']
            result['diagnostics']['expandedScale'] = dict(scale=next_scale, accepted=expanded['accepted'], reason=expanded['reason'])
            if expanded['accepted'] and (not result['accepted'] or expanded['confidence'] > result['confidence']+.015):
                expanded['diagnostics']['independentScale'] = info
                expanded['diagnostics']['expandedFromScale'] = seed
                expanded['testedScaleHypotheses'] = result['testedScaleHypotheses']
                result = expanded
    return result


def _bilinear(field, xy):
    h, w = field.shape
    valid = np.isfinite(xy).all(axis=-1) & (xy[..., 0] >= 0) & (xy[..., 1] >= 0) & (xy[..., 0] <= w-1) & (xy[..., 1] <= h-1)
    xx = np.clip(xy[..., 0], 0, w-1)
    yy = np.clip(xy[..., 1], 0, h-1)
    x0, y0 = xx.astype(np.int32), yy.astype(np.int32)
    x1, y1 = np.minimum(x0+1, w-1), np.minimum(y0+1, h-1)
    fx, fy = xx-x0, yy-y0
    value = (field[y0, x0]*(1-fx)+field[y0, x1]*fx)*(1-fy)+(field[y1, x0]*(1-fx)+field[y1, x1]*fx)*fy
    return np.where(valid, value, np.inf)


def _precision_refine(observation, prepared, seed_scale, seed_offset, lock_scale, cancelled):
    started = time.perf_counter()
    center_local = np.asarray(observation.valid_mask.shape[::-1])/2.
    center = center_local+observation.origin
    reference_center = (center-seed_offset)/seed_scale
    scale, shift = seed_scale, np.zeros(2)
    radius, rounds, probes = 0., 0, 0
    before = after = None

    def expired():
        return (time.perf_counter()-started)*1000 >= 10 or _cancelled(cancelled)

    def finish(reason):
        return dict(seedScale=seed_scale, scale=float(scale), offset=(center-reference_center*scale+shift).tolist(),
                    before=before, after=after, iterations=rounds, probes=probes,
                    milliseconds=(time.perf_counter()-started)*1000, termination=reason,
                    calibrated=reason == 'converged', radiusPixels=radius, scaleLocked=lock_scale)

    if prepared.distance is None:
        return finish('distance-unavailable')
    points = observation.points
    points = points[observation.valid_mask[points[:, 1], points[:, 0]] >= 128]
    parts = (points[:, 0] >= center_local[0]).astype(np.int32)+2*(points[:, 1] >= center_local[1])
    counts = np.bincount(parts, minlength=4)
    radii = np.linalg.norm(points-center_local, axis=1)
    radius = float(radii.max()) if len(radii) else 0.
    span = (counts[0]+counts[2] > 0 and counts[1]+counts[3] > 0
            and counts[0]+counts[1] > 0 and counts[2]+counts[3] > 0)
    if len(points) < 35 or np.count_nonzero(counts >= 15) < 2 or not span or radius < 80:
        return finish('insufficient-support')
    active = np.flatnonzero(counts >= 15)
    weights = np.array([1./(len(active)*counts[p]) if p in active else 0. for p in parts])

    def distances(scales, shifts):
        scales = np.asarray(scales)
        xy = reference_center+(points[None, :, :]-center_local-np.asarray(shifts)[:, None, :])/scales[:, None, None]
        return np.minimum(6., _bilinear(prepared.distance, xy)*scales[:, None])

    def losses(scales, shifts):
        d = distances(scales, shifts)
        huber = np.where(d <= 1.5, .5*d*d, 1.5*(d-.75))
        return huber@weights

    def residual(s, xy):
        d = distances([s], [xy])[0]
        def p95(values):
            return float(np.sort(values)[math.ceil(len(values)*.95)-1]) if len(values) else -1.
        return dict(loss=float(losses([s], [xy])[0]), nearP95=p95(d[radii <= radius*.35]),
                    farP95=p95(d[radii >= radius*.70]), partitionP95=[p95(d[parts == p]) for p in range(4)])

    if expired():
        return finish('budget')
    before = residual(scale, shift)
    if before['loss'] >= 5:
        return finish('out-of-reference')
    best = before['loss']
    ds, dt, converged = seed_scale*.005, 1., False
    for rounds in range(12):
        if expired():
            return finish('budget')
        if dt <= .125 and (lock_scale or ds/seed_scale*radius <= .25):
            converged = True
            break
        candidates = [(scale+si*ds, shift+[xi*dt, yi*dt])
                      for si in ((0,) if lock_scale else (-1, 0, 1))
                      for xi in (-1, 0, 1) for yi in (-1, 0, 1)
                      if (si or xi or yi) and (lock_scale or abs((scale+si*ds)/seed_scale-1) <= .030000001)
                      and abs(shift[0]+xi*dt) <= 4.0000001 and abs(shift[1]+yi*dt) <= 4.0000001]
        values = losses(*zip(*candidates))
        probes += len(candidates)
        index = int(np.argmin(values))
        if values[index] < best-1e-9:
            best, scale, shift = float(values[index]), candidates[index][0], candidates[index][1]
        else:
            ds, dt = ds/2., dt/2.
    else:
        rounds = 12
    if expired():
        return finish('budget')
    if not converged:
        return finish('iterations')
    if (not lock_scale and abs(scale/seed_scale-1) >= .029999) or np.any(np.abs(shift) >= 3.999999):
        return finish('boundary')
    if not lock_scale:
        for sign in (-1, 1):
            candidates = [(scale+sign*scale/radius, shift+[xi*.5, yi*.5]) for xi in range(-2, 3) for yi in range(-2, 3)]
            if expired():
                return finish('budget')
            competitor = float(losses(*zip(*candidates)).min())
            probes += len(candidates)
            if competitor <= best+1e-4:
                return finish('scale-unobservable')
    after = residual(scale, shift)
    return finish('budget' if expired() else 'converged')


def _bootstrap(observation, prepared, scale, *, cancelled=None, lock_scale=True) -> dict:
    """The numerical FastBootstrap stage; the public solve owns the wrapper."""
    started = time.perf_counter()
    diagnostics = dict(algorithm='idvb0059-fast-bootstrap', scaleLocked=lock_scale,
        maxSparsePoints=MAX_SPARSE_POINTS, minimumApertureMargin=MIN_MARGIN,
        minimumDistinctDistance=MIN_DISTINCT, timingMs={})

    def finish(reason, best=None, runner=None):
        diagnostics['timingMs']['total'] = (time.perf_counter()-started)*1000
        if best is not None:
            diagnostics['best'] = {k: v.tolist() if isinstance(v, np.ndarray) else v for k, v in best.items()}
        if runner is not None:
            diagnostics['runnerUp'] = {k: v.tolist() if isinstance(v, np.ndarray) else v for k, v in runner.items()}
        result = dict(accepted=not reason, reason=reason, diagnostics=diagnostics)
        result['testedScaleHypotheses'] = 1
        result['scale'] = best['scale'] if best is not None else scale
        if not reason:
            result['matrixSourceToClient'] = np.array([[best['scale'], 0., best['offset'][0]], [0., best['scale'], best['offset'][1]]], np.float64)
            result['confidence'] = best['weightedScore']
        return result

    if _cancelled(cancelled):
        return finish('Cancelled')
    diagnostics.update(scale=scale, referenceBinding=dict(prepared.binding), referenceRepresentation=prepared.representation)
    diagnostics.update(sparsePointCount=len(observation.points), observedEdgePixels=cv2.countNonZero(observation.observed_edges), proposalEdgePixels=cv2.countNonZero(observation.proposal_edges),
                       observationBounds=[*observation.origin, observation.valid_mask.shape[1], observation.valid_mask.shape[0]])
    if _cancelled(cancelled):
        return finish('Cancelled')
    if not len(observation.points):
        return finish('NoCurrentFrameStructuralPoints')
    mark = time.perf_counter()
    seeds, grid = _coarse_grid(observation.points, prepared, scale, cancelled, observation.origin)
    diagnostics.update(grid)
    diagnostics['timingMs']['translation'] = (time.perf_counter()-mark)*1000
    if _cancelled(cancelled):
        return finish('Cancelled')
    if not seeds:
        return finish('TranslationNoCandidatesFound')
    mark = time.perf_counter()
    initial = int(grid['initialCandidates'])
    client_points = observation.points+np.asarray(observation.origin)
    def refine(rows):
        refined = (_refine_many(client_points, prepared, scale, rows) if lock_scale
                   else [_refine_unlocked(client_points, prepared, scale, row, observation) for row in rows])
        return [_spatial(observation, prepared, candidate['scale'], candidate) for candidate in refined]
    candidates = refine(seeds[:initial])
    best = candidates[0]
    rivals = [c for c in candidates[1:] if np.linalg.norm(c['offset']-best['offset']) >= MIN_DISTINCT]
    if not rivals and best['spatiallyConsistent'] and max(best['weightedScore'], best['globalScore']) >= MIN_SCORE*.85:
        retained = refine(seeds[initial:]) if seeds[initial:] else []
        for candidate in retained:
            candidates.append(candidate)
            if np.linalg.norm(candidate['offset']-best['offset']) >= MIN_DISTINCT:
                rivals.append(candidate)
    runner = max(rivals, key=lambda c: c['globalScore']) if rivals else None
    if lock_scale and runner is not None and runner['spatiallyConsistent'] and runner['globalScore'] > best['globalScore']:
        best, runner = runner, best
    diagnostics['timingMs']['refineAndVerify'] = (time.perf_counter()-mark)*1000
    diagnostics['refinedCandidates'] = len(candidates)
    if _cancelled(cancelled):
        return finish('Cancelled')
    if not MIN_SCALE <= best['scale'] <= MAX_SCALE:
        return finish('RefinedScaleOutOfRange', best, runner)
    if runner is None:
        return finish('NoDistinctRefinedRunnerUp', best)
    margin = best['globalScore']-runner['globalScore']
    required = MIN_SCORE-np.clip((margin-MIN_MARGIN)/.14, 0., 1.)*.09
    diagnostics.update(apertureMargin=margin, requiredVerificationScore=float(required),
                       distinctRunnerUpDistance=float(np.linalg.norm(best['offset']-runner['offset'])))
    if margin < MIN_MARGIN:
        return finish('ApertureMarginBelowThreshold', best, runner)
    if best['weightedScore'] < required:
        return finish('VerificationScoreBelowThreshold', best, runner)
    if not best['spatiallyConsistent']:
        return finish('SpatialPartitionsBelowThreshold', best, runner)
    h, w = observation.valid_mask.shape
    lo = (np.asarray(observation.origin)-best['offset'])/best['scale']
    hi = (np.asarray(observation.origin)+np.array([w, h])-best['offset'])/best['scale']
    overlap = np.minimum(hi, prepared.shape[::-1])-np.maximum(lo, 0.)
    diagnostics['overlapSourcePixels'] = overlap.tolist()
    if np.any(overlap < 50):
        return finish('TransformedBoundsOutOfBounds', best, runner)
    return finish('', best, runner)


def _coverage_context(frame, reference, prepared, observation, coverage_state):
    # AdaptiveScaleKey keys by map/floor/resource revision plus BOTH client and
    # viewport dimensions. The bound resource object's identity covers revision
    # replacement here; retaining each viewport-size entry matches native policy.
    client_h, client_w = np.asarray(frame).shape[:2]
    view_h, view_w = observation.valid_mask.shape
    key = f'{client_w}x{client_h}/{view_w}x{view_h}'
    binding = dict(prepared.binding)
    valid = (isinstance(coverage_state, dict) and coverage_state.get('referenceToken') == id(reference)
             and coverage_state.get('binding') == binding and isinstance(coverage_state.get('entries'), dict))
    entries = {name: dict(value) for name, value in coverage_state['entries'].items()} if valid else {}
    return key, binding, entries, entries.get(key, {}), valid


def _observe_coverage(observation, prepared, reference, matrix, context, *, refreshed):
    key, binding, entries, previous, _ = context
    supported = cv2.dilate(observation.observed_edges, _K5)
    projected = np.rint(prepared.reference_points@matrix[:, :2].T+matrix[:, 2]-observation.origin).astype(np.int32)
    hits = int(_lookup(supported, projected).sum())
    total = len(prepared.reference_points)
    coverage = hits/max(1, total)
    reached = max([0]+[p for p in (10, 15, 20, 25, 30, 40, 50) if coverage*100+1e-9 >= p])
    old_reached = int(previous.get('reachedPercent', 0))
    pending = False if refreshed else bool(previous.get('refreshPending', False))
    if reached > old_reached:
        pending = True
    entries[key] = dict(reachedPercent=max(reached, old_reached), refreshPending=pending)
    state = dict(referenceToken=id(reference), binding=binding, entries=entries)
    measurement = dict(hits=hits, total=total, ratio=coverage,
                       reachedPercent=entries[key]['reachedPercent'], refreshNextAlignment=pending,
                       refreshCompleted=bool(refreshed))
    return state, measurement


def observe_coverage(frame, reference, matrix, *, coverage_state=None) -> dict:
    """Measure an already committed non-0059 pose without solving alignment.

    This is ObserveAlignmentCoverage(refreshed=False): initial/fallback success
    can advance a milestone but can never consume an outstanding refresh. Input
    state is not mutated. The caller stores the returned floor-local state after
    its accepted pose commit and clears it with the same session lifecycle.
    """
    transform = np.asarray(matrix, np.float64)
    if transform.shape == (3, 3) and np.array_equal(transform[2], [0., 0., 1.]):
        transform = transform[:2]
    if transform.shape != (2, 3) or not np.isfinite(transform).all():
        raise ValueError('CommittedCoverageTransformInvalid')
    prepared = prepare(reference)
    observation = extract(frame)
    context = _coverage_context(frame, reference, prepared, observation, coverage_state)
    state, _ = _observe_coverage(observation, prepared, reference, transform, context, refreshed=False)
    return state


def solve(frame, reference, scale, *, cancelled=None, coverage_state=None,
          allow_scale_lock=True, low_structure=False) -> dict:
    """Full 0059 alignment wrapper around current-frame Bootstrap evidence.

    Pass only a trusted scale for this target floor. ``coverage_state`` is the
    last returned ``coverageState`` committed with this floor's successful pose;
    the caller must discard it on session reset. This function never mutates it
    and never commits a pose itself. A rejected result has no pose/state proposal.
    """
    started = time.perf_counter()
    timing = {}
    wrapper = dict(algorithm='idvb0059-steady-wrapper', timingMs=timing,
                   inputScale=scale, lowStructure=bool(low_structure), sameFrameExtractionCount=0)

    def rejected(reason, bootstrap=None):
        timing['total'] = (time.perf_counter()-started)*1000
        if bootstrap is not None:
            wrapper['selectedBootstrap'] = bootstrap['diagnostics']
            for name in ('apertureMargin', 'requiredVerificationScore', 'distinctRunnerUpDistance', 'best', 'runnerUp', 'refinedCandidates'):
                if name in bootstrap['diagnostics']:
                    wrapper[name] = bootstrap['diagnostics'][name]
        return dict(accepted=False, reason=reason, diagnostics=wrapper)

    if _cancelled(cancelled):
        return rejected('Cancelled')
    try:
        scale = float(scale)
    except (TypeError, ValueError):
        return rejected('TrustedFloorScaleMissing')
    if not math.isfinite(scale) or not MIN_SCALE <= scale <= MAX_SCALE:
        return rejected('TrustedFloorScaleOutOfRange')
    mark = time.perf_counter()
    try:
        prepared = prepare(reference)
    except (AttributeError, ValueError, TypeError) as error:
        return rejected(str(error))
    timing['prepare'] = (time.perf_counter()-mark)*1000
    mark = time.perf_counter()
    try:
        observation = extract(frame)
    except (ValueError, TypeError) as error:
        return rejected(str(error))
    timing['extract'] = (time.perf_counter()-mark)*1000
    wrapper.update(sameFrameExtractionCount=1, referenceBinding=dict(prepared.binding),
                   referenceRepresentation=prepared.representation,
                   observationBounds=[*observation.origin, *observation.valid_mask.shape[::-1]],
                   sparsePointCount=len(observation.points), observedEdgePixels=cv2.countNonZero(observation.observed_edges),
                   proposalEdgePixels=cv2.countNonZero(observation.proposal_edges))
    if _cancelled(cancelled):
        return rejected('Cancelled')
    if not len(observation.points):
        return rejected('NoCurrentFrameStructuralPoints')

    coverage_context = _coverage_context(frame, reference, prepared, observation, coverage_state)
    coverage_key, binding, entries, previous, valid_state = coverage_context
    pending = bool(previous.get('refreshPending', False)) and allow_scale_lock and not low_structure
    selected_lock = bool(low_structure or (allow_scale_lock and not pending))
    wrapper['scalePolicy'] = dict(coverageKey=coverage_key, previousReachedPercent=previous.get('reachedPercent', 0),
                                 refreshPending=pending, lockingAllowed=bool(allow_scale_lock),
                                 restoredState=bool(valid_state), initialScaleLocked=selected_lock)
    mark = time.perf_counter()
    if selected_lock:
        result = _bootstrap(observation, prepared, scale, cancelled=cancelled)
    else:
        result = _fresh_scale_solve(observation, prepared, cancelled)
    timing['initialBootstrap'] = (time.perf_counter()-mark)*1000
    tested = result['testedScaleHypotheses']

    def summary(value):
        return dict(accepted=value['accepted'], reason=value['reason'], scale=value.get('scale'),
                    confidence=value.get('confidence'), diagnostics=value['diagnostics'])

    if pending and not _cancelled(cancelled):
        mark = time.perf_counter()
        fresh, best_prior = result, None
        hypotheses = []
        for candidate_scale in (scale, scale*.995, scale*1.005):
            if not MIN_SCALE <= candidate_scale <= MAX_SCALE or _cancelled(cancelled):
                continue
            candidate = _bootstrap(observation, prepared, candidate_scale, cancelled=cancelled)
            tested += candidate['testedScaleHypotheses']
            hypotheses.append(summary(candidate))
            if candidate['accepted'] and (best_prior is None or candidate['confidence'] > best_prior['confidence']+.015):
                best_prior = candidate
        selected_lock = best_prior is not None and (not fresh['accepted'] or fresh['confidence'] <= best_prior['confidence']+.015)
        result = best_prior if selected_lock else fresh
        wrapper['coverageRefresh'] = dict(fresh=summary(fresh), priorHypotheses=hypotheses,
                                         selectedPriorHypothesis=selected_lock)
        timing['scaleRefreshComparison'] = (time.perf_counter()-mark)*1000
    elif not low_structure and selected_lock and result['accepted'] and result['confidence'] < .75 and not _cancelled(cancelled):
        mark = time.perf_counter()
        locked = result
        fresh = _fresh_scale_solve(observation, prepared, cancelled)
        tested += fresh['testedScaleHypotheses']
        replace = (fresh['accepted'] and fresh['confidence'] >= locked['confidence']+.05
                   and abs(fresh['scale']-scale)/scale > .005)
        if replace:
            result, selected_lock = fresh, False
        wrapper['weakSteadyScaleRecheck'] = dict(locked=summary(locked), fresh=summary(fresh), selectedFresh=replace,
                                               minimumScoreImprovement=.05, minimumRelativeScaleChange=.005)
        timing['weakScaleRecheck'] = (time.perf_counter()-mark)*1000
    wrapper.update(testedScaleHypotheses=tested, scaleLocked=selected_lock)
    if _cancelled(cancelled):
        return rejected('Cancelled', result)
    if not result['accepted']:
        return rejected(result['reason'], result)

    matrix = result['matrixSourceToClient'].copy()
    precision = _precision_refine(observation, prepared, float(matrix[0, 0]), matrix[:, 2], selected_lock, cancelled)
    wrapper['precision'] = precision
    timing['precision'] = precision['milliseconds']
    if precision['calibrated']:
        matrix[0, 0] = matrix[1, 1] = precision['scale']
        matrix[:, 2] = precision['offset']
    if _cancelled(cancelled):
        return rejected('Cancelled', result)

    mark = time.perf_counter()
    dense = cv2.findNonZero(observation.observed_edges)
    if dense is None or prepared.distance is None:
        forward = None
        dense_count = 0
    else:
        xy = (dense.reshape(-1, 2)+np.asarray(observation.origin)-matrix[:, 2])/matrix[0, 0]
        h, w = prepared.shape
        inside = (xy[:, 0] >= 0) & (xy[:, 1] >= 0) & (xy[:, 0] < w-1) & (xy[:, 1] < h-1)
        distances = np.where(inside, _bilinear(prepared.distance, xy)*matrix[0, 0], 50.)
        forward, dense_count = float(distances.mean()), len(distances)
    timing['forwardMean'] = (time.perf_counter()-mark)*1000
    wrapper.update(measuredForwardMeanPixels=forward, forwardMeanPointCount=dense_count,
                   chamferQuality=float(np.clip(1-(forward if forward is not None else float('inf'))/3., 0., 1.)),
                   forwardMeanPolicy='reported-by-0059-wrapper; no-added-rejection-threshold')

    mark = time.perf_counter()
    proposal_state, measurement = _observe_coverage(observation, prepared, reference, matrix, coverage_context, refreshed=pending)
    wrapper['coverage'] = dict(measurement, scaleChangedOnRefresh=bool(pending and abs(matrix[0, 0]-scale)/scale > .005))
    timing['coverage'] = (time.perf_counter()-mark)*1000
    wrapper['selectedBootstrap'] = result['diagnostics']
    # Keep the original top-level vote fields convenient for existing telemetry.
    for name in ('apertureMargin', 'requiredVerificationScore', 'distinctRunnerUpDistance', 'best', 'runnerUp', 'refinedCandidates'):
        if name in result['diagnostics']:
            wrapper[name] = result['diagnostics'][name]
    wrapper['finalTransform'] = matrix.tolist()
    timing['total'] = (time.perf_counter()-started)*1000
    if _cancelled(cancelled):
        return rejected('Cancelled', result)
    return dict(accepted=True, reason='', diagnostics=wrapper, matrixSourceToClient=matrix,
                confidence=result['confidence'], coverageState=proposal_state)


__all__ = ['PreparedReference', 'prepare', 'solve', 'observe_coverage']
