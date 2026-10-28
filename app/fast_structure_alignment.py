"""Experimental sparse registration; workers propose, the existing V2 owner commits.

Prepared arrays belong to a static reference. Observations belong to one input
frame, and candidate projections are evaluated by lookup, not whole-image warps.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from collections import OrderedDict
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass(frozen=True)
class PreparedStructure:
    distance: np.ndarray
    chains: np.ndarray
    rays: np.ndarray
    pitch: float
    shape: tuple


_prepared = OrderedDict()
_prepared_lock = threading.Lock()


def _pitch(mask):
    signal = np.count_nonzero(mask, axis=0).astype(np.float64)
    signal -= signal.mean()
    if np.dot(signal, signal) < 1e-8:
        return 0.0
    size = 1 << (2 * len(signal) - 1).bit_length()
    spectrum = np.fft.rfft(signal, size)
    corr = np.fft.irfft(spectrum * spectrum.conjugate(), size)[:len(signal)]
    lo, hi = 12, len(signal) // 2
    peaks = [i for i in range(lo, hi - 1)
             if corr[i] > corr[i-1] and corr[i] > corr[i+1] and corr[i] > corr[0] * .12]
    if not peaks:
        return 0.0
    best = max(peaks, key=lambda i: corr[i])
    for i in peaks:
        if i < best and corr[i] >= .8 * corr[best] and abs(best/i - round(best/i)) < .15:
            best = i
            break
    return float(best)


def prepare(reference):
    """Resource-generation operation; never called by the online solver."""
    from single_map_structure import contour_graph
    mask = (np.asarray(reference) > 0).astype(np.uint8)
    edge = cv2.morphologyEx(mask, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
    distance = cv2.distanceTransform(1-edge, cv2.DIST_L2, 3)
    # At the smallest admitted scale (.03), a uint8 field clipped to 31.75
    # source pixels would mark even distant walls as <1 screen pixel away.
    quantized = np.rint(np.minimum(distance, 16383.75) * 4).astype(np.uint16)
    nodes = contour_graph(mask)
    chains, rays = [], []
    for i, node in enumerate(nodes):
        ids = [node['previous'], i, node['next']]
        if all(nodes[j]['valid'] for j in ids):
            chains.append([nodes[j]['point'] for j in ids])
            rays.append(node['rays'])
    return PreparedStructure(quantized, np.asarray(chains, dtype=np.float32).reshape(-1,3,2),
                             np.asarray(rays, dtype=np.float32).reshape(-1,2,2), _pitch(edge), mask.shape)


def prepared_path_for(reference):
    """Use the provider's explicit root; preserve standalone legacy readers."""
    path = Path(reference.path)
    root = getattr(reference, 'resource_root', None)
    directory = Path(root) / 'references' / 'fast-structure' if root is not None else path.parent.parent / 'fast-structure'
    return directory / (path.stem + '-fast.npz')


def load_prepared(reference):
    prepared_path = prepared_path_for(reference)
    if not prepared_path.is_file():
        return None
    stat = prepared_path.stat()
    key = (str(prepared_path.resolve()), stat.st_size, stat.st_mtime_ns, id(reference))
    with _prepared_lock:
        if key in _prepared:
            _prepared.move_to_end(key)
            return _prepared[key][1]
    try:
        with np.load(prepared_path, allow_pickle=False) as data:
            metadata = json.loads(str(data['sourceMetadata'].item()))
            if metadata != reference.metadata:
                return None
            result = PreparedStructure(data['distance'], data['chains'], data['rays'],
                                       float(data['pitch']), tuple(int(x) for x in data['shape']))
        if (result.shape != reference.clean_mask.shape or result.distance.shape != result.shape
                or result.distance.dtype != np.uint16 or result.chains.ndim != 3
                or result.chains.shape[1:] != (3, 2)
                or result.rays.shape != (len(result.chains), 2, 2)
                or not np.isfinite(result.chains).all() or not np.isfinite(result.rays).all()
                or not math.isfinite(result.pitch) or result.pitch < 0):
            return None
        with _prepared_lock:
            # Retaining the reference prevents Python id reuse from admitting
            # another resource through an existing in-memory cache entry.
            _prepared[key] = (reference, result)
            while len(_prepared) > 4:
                _prepared.popitem(last=False)
        return result
    except (OSError, ValueError, KeyError):
        return None


def sample_edges(edge, limit):
    """Deterministic spatial bins keep sparse views from sampling one wall only."""
    y, x = np.nonzero(edge)
    if not len(x):
        return np.empty((0, 2), np.float32)
    points = np.column_stack((x, y))
    if len(points) <= limit:
        return points.astype(np.float32)
    bins = (y * 8 // max(1, edge.shape[0])) * 8 + x * 8 // max(1, edge.shape[1])
    groups = [points[bins == i] for i in np.unique(bins)]
    quota = max(1, math.ceil(limit / len(groups)))
    sampled = np.concatenate([g[np.linspace(0, len(g)-1, min(quota, len(g))).astype(int)] for g in groups])
    if len(sampled) > limit:
        sampled = sampled[np.linspace(0, len(sampled)-1, limit).astype(int)]
    return sampled.astype(np.float32)


def scores(points, prepared, scales, translations):
    """Bounded batches; off-canvas points are misses, never discarded support."""
    scales = np.broadcast_to(np.asarray(scales, np.float32), (len(translations),))
    answer = np.empty(len(translations), np.float32)
    h, w = prepared.shape
    for start in range(0, len(translations), 1024):
        stop = min(start+1024, len(translations))
        ss = scales[start:stop, None]
        xy = np.rint((points[None, :, :] - translations[start:stop, None, :]) / ss[:, :, None]).astype(np.int32)
        xx, yy = xy[:, :, 0], xy[:, :, 1]
        valid = (xx >= 0) & (yy >= 0) & (xx < w) & (yy < h)
        distance = prepared.distance[np.clip(yy, 0, h-1), np.clip(xx, 0, w-1)].astype(np.float32) * ss * .25
        hit3 = valid & (distance <= 3.)
        hit6 = valid & (distance <= 6.)
        answer[start:stop] = (2*hit3.sum(axis=1)+hit6.sum(axis=1))/(3.*len(points))
    return answer


def _distinct(rows, minimum=12., maximum=8):
    selected = []
    for row in sorted(rows, key=lambda r: -r[0]):
        if all(np.linalg.norm(np.asarray(row[2])-old[2]) >= minimum
               or abs(row[1]/old[1]-1) > .025 for old in selected):
            selected.append(row)
            if len(selected) == maximum:
                break
    return selected


def _chain_seeds(frame, context, prepared, cancelled):
    from single_map_structure import observed_corner_chains
    if not len(prepared.chains):
        return []
    source_delta = prepared.chains[:, [0, 2]] - prepared.chains[:, 1, None]
    denominators = np.sum(source_delta*source_delta, axis=(1,2))
    rows = []
    query = observed_corner_chains(frame, context)
    for node in query[:160]:
        if cancelled and cancelled():
            return []
        delta = node['points'][[0,2]] - node['points'][1]
        orientation = np.min(np.sum(node['rays'] * prepared.rays, axis=2), axis=1) >= .95
        scale = np.sum(delta * source_delta, axis=(1,2)) / np.maximum(denominators, 1e-8)
        error = np.linalg.norm(delta - scale[:,None,None]*source_delta, axis=2).max(axis=1)
        for i in np.flatnonzero(orientation & (scale >= .03) & (scale <= 6.) & (error <= 4.)):
            rows.append((float(scale[i]), node['points'][1]-scale[i]*prepared.chains[i,1]))
        if len(rows) > 4096:
            return []  # Exhausted proposal budget is not evidence of uniqueness.
    return rows


def _grid(points, prepared, scale, prior, cancelled):
    if prior is not None:
        offsets = np.arange(-32., 32.1, 4.)
        tx = prior[0,2] + offsets
        ty = prior[1,2] + offsets
    else:
        # Median support bounds admit partially outside viewports too.
        reference_points = points / scale
        lo = -np.quantile(reference_points, .5, axis=0)
        hi = np.asarray(prepared.shape[::-1]) - np.quantile(reference_points, .5, axis=0)
        stride = max(3., 5./scale)
        xs = np.arange(lo[0], hi[0]+stride, stride)
        ys = np.arange(lo[1], hi[1]+stride, stride)
        if len(xs)*len(ys) > 350000:
            return []
        tx, ty = -scale*xs, -scale*ys
    x, y = np.meshgrid(tx, ty)
    translations = np.column_stack((x.ravel(), y.ravel()))
    # A Cartesian search repeats each X coordinate for every row and each Y
    # coordinate for every column. Quantize each axis once, with the same
    # float32 scale and rounding used by scores; retain every original pose.
    scale32 = np.float32(scale)
    xx = np.rint((points[None,:,0]-tx[:,None])/scale32).astype(np.int32)
    yy = np.rint((points[None,:,1]-ty[:,None])/scale32).astype(np.int32)
    h, w = prepared.shape
    valid_x, valid_y = (xx >= 0) & (xx < w), (yy >= 0) & (yy < h)
    xx, yy = np.clip(xx,0,w-1), np.clip(yy,0,h-1)
    if len(translations) < 4096:
        # Small local grids are cheaper as one or a few candidate batches.
        values = np.empty(len(translations), np.float32)
        for start in range(0, len(translations), 1024):
            if cancelled and cancelled():
                return []
            indices = np.arange(start,min(start+1024,len(translations)))
            columns, rows = indices % len(tx), indices // len(tx)
            valid = valid_x[columns] & valid_y[rows]
            distance = prepared.distance[yy[rows],xx[columns]].astype(np.float32) * scale32 * .25
            hit3, hit6 = valid & (distance <= 3.), valid & (distance <= 6.)
            values[start:start+len(indices)] = (2*hit3.sum(axis=1)+hit6.sum(axis=1))/(3.*len(points))
    else:
        # The prepared distance is uint16. Derive inclusive integer cutoffs
        # using the original float32 arithmetic, including its rounding at
        # 3/6 pixels. Visit every pose for each point without materializing
        # candidate-by-point coordinate and floating-distance batches.
        distances = np.arange(65536, dtype=np.float32) * scale32 * .25
        limit3 = int(np.searchsorted(distances, 3., side='right')) - 1
        limit6 = int(np.searchsorted(distances, 6., side='right')) - 1
        totals = np.zeros((len(ty), len(tx)), np.uint32)
        for point in range(len(points)):
            if cancelled and cancelled():
                return []
            columns = np.flatnonzero(valid_x[:,point])
            rows = np.flatnonzero(valid_y[:,point])
            if not len(columns) or not len(rows):
                continue
            # Grid axes are monotonic, so in-bounds indices form one interval.
            # Off-canvas point/pose pairs contribute zero as before; avoid
            # loading their clipped edge pixels just to discard them.
            x0, x1 = int(columns[0]), int(columns[-1]) + 1
            y0, y1 = int(rows[0]), int(rows[-1]) + 1
            distance = prepared.distance[yy[y0:y1,point,None],xx[None,x0:x1,point]]
            hit3, hit6 = distance <= limit3, distance <= limit6
            totals[y0:y1,x0:x1] += 2*hit3.astype(np.uint32) + hit6
        values = (totals.ravel() / (3.*len(points))).astype(np.float32)
    # Suppress complete spatial basins before retaining peaks. Chunk-local
    # top-k can otherwise hide a separate runner-up behind one broad peak.
    rows = []
    for _ in range(8):
        i = int(np.argmax(values))
        if values[i] < 0:
            break
        rows.append((float(values[i]), scale, translations[i]))
        near = np.sum((translations-translations[i])**2, axis=1) < 12.**2
        values[near] = -1
    return rows


def refine(points, prepared, scale, translation, *, lock_scale):
    def loss(scales, translations):
        scales = np.asarray(scales, np.float32)[:, None]
        xy = (points[None]-translations[:,None])/scales[:,:,None]
        x, y = xy[:,:,0], xy[:,:,1]
        h, w = prepared.shape
        valid = (x >= 0) & (y >= 0) & (x < w-1) & (y < h-1)
        x0, y0 = np.floor(x).astype(np.int32), np.floor(y).astype(np.int32)
        fx, fy = x-x0, y-y0
        x0, y0 = np.clip(x0,0,w-2), np.clip(y0,0,h-2)
        field = prepared.distance
        distance = ((1-fx)*(1-fy)*field[y0,x0]+fx*(1-fy)*field[y0,x0+1]
                    +(1-fx)*fy*field[y0+1,x0]+fx*fy*field[y0+1,x0+1]) * scales * .25
        distance = np.where(valid, np.minimum(distance,12.), 12.)
        return np.mean(np.where(distance <= 1.5, .5*distance**2, 1.5*(distance-.75)), axis=1)
    center = np.mean(points, axis=0)
    anchor = (center-translation)/scale
    current_scale, current = float(scale), np.asarray(translation, np.float32).copy()
    best = float(loss([scale],current[None])[0])
    for dt, ds in ((3., .01), (1., .003), (.25, .001), (.125, .0003)):
        scale_values = [current_scale] if lock_scale else [current_scale*(1-ds), current_scale, current_scale*(1+ds)]
        proposals, scales = [], []
        for s in scale_values:
            origin = current if lock_scale else center-anchor*s
            for x in (-dt, 0., dt):
                for y in (-dt, 0., dt):
                    proposals.append(origin+[x,y]); scales.append(s)
        proposals = np.asarray(proposals, np.float32)
        result = loss(scales, proposals)
        winner = int(np.argmin(result))
        if result[winner] <= best:
            best, current_scale, current = float(result[winner]), float(scales[winner]), proposals[winner]
            anchor = (center-current)/current_scale
    confidence = float(scores(points,prepared,current_scale,current[None])[0])
    return confidence, current_scale, current


def propose(frame, reference, prepared, *, prior=None, cancelled=None, context=None):
    from single_map_structure import current_geometry, current_part
    started = time.perf_counter()
    if cancelled and cancelled():
        return [], context, {'reason': 'structure-work-obsolete'}
    context = context if context is not None else current_geometry(frame)
    if context is None:
        return [], context, {'reason': 'fast-observation-unavailable'}
    edge = current_part(frame, context, 'observed')
    points = sample_edges(edge, 300)
    if len(points) < 40:
        return [], context, {'reason': 'fast-insufficient-edges', 'points': len(points)}
    coarse = sample_edges(edge, 96)
    rows = []
    searches = []
    def grid(scale, seed):
        began = time.perf_counter()
        found = _grid(coarse, prepared, scale, seed, cancelled)
        searches.append(dict(scope='local' if seed is not None else 'global',
            elapsedMs=(time.perf_counter()-began)*1000,
            bestScore=found[0][0] if found else None))
        return found
    if prior is not None:
        rows = grid(float(prior[0,0]), prior)
        if not rows or rows[0][0] < .50:
            rows.extend(grid(float(prior[0,0]), None))
    else:
        seeds = _chain_seeds(frame, context, prepared, cancelled)
        if seeds:
            scales = np.asarray([r[0] for r in seeds], np.float32)
            tt = np.asarray([r[1] for r in seeds], np.float32)
            result = scores(coarse, prepared, scales, tt)
            rows.extend((float(v), float(s), t) for v,s,t in zip(result,scales,tt))
        if not rows:
            pitch = _pitch(edge)
            if pitch > 0 and prepared.pitch > 0:
                scale = pitch/prepared.pitch
                if .03 <= scale <= 6.:
                    rows.extend(grid(scale, None))
    retained = _distinct(rows, maximum=8)
    refined = []
    for _, scale, translation in retained:
        if cancelled and cancelled():
            return [], context, {'reason': 'structure-work-obsolete'}
        refined.append(refine(points, prepared, scale, translation, lock_scale=prior is not None))
    refined = _distinct(refined, minimum=12., maximum=3)
    elapsed = (time.perf_counter()-started)*1000
    diagnostics = {'proposalMs': elapsed, 'points': len(points), 'coarsePoints': len(coarse),
                   'scaleLocked': prior is not None, 'candidateCount': len(refined),
                   'scores': [r[0] for r in refined], 'searchStages': searches}
    if not refined or refined[0][0] < .50:
        return [], context, dict(diagnostics, reason='fast-low-score')
    if len(refined) > 1 and refined[0][0]-refined[1][0] < .06:
        return [], context, dict(diagnostics, reason='fast-ambiguous')
    return refined[:1], context, dict(diagnostics, reason='fast-proposal')


def native_point_geometry(frame, prepared, matrix, context, *, lock_scale):
    """Use static native chains, retaining disjoint point fitting and ambiguity.

    Candidate generation cannot authorize a pose. Current measured complete
    chains must establish unique correspondences before the original point
    fitter can evaluate separate support and held-out points.
    """
    from single_map_structure import observed_corner_chains
    from nightmare_candidate import fit_axis_locked_point_geometry
    source = prepared.chains
    scale = float(matrix[0,0])
    centers = source[:,1]*scale+matrix[:2,2]
    delta = (source[:,[0,2]]-source[:,1,None])*scale
    lengths = np.linalg.norm(delta,axis=2)
    pairs = []
    query = observed_corner_chains(frame,context)
    for qi,node in enumerate(query):
        qdelta = node['points'][[0,2]]-node['points'][1]
        qlength = np.linalg.norm(qdelta,axis=1)
        near = np.sum((centers-node['points'][1])**2,axis=1)<=48.**2
        oriented = np.min(np.sum(prepared.rays*node['rays'],axis=2),axis=1)>=.95
        adjacent = np.max(np.abs(lengths-qlength),axis=1)<=3.
        for si in np.flatnonzero(near & oriented & adjacent):
            pairs.append((qi,int(si)))
    qcount = Counter(q for q,s in pairs)
    scount = Counter(s for q,s in pairs)
    unique = [(qi,si) for qi,si in pairs if qcount[qi]==1 and scount[si]==1]
    if len(unique)<6:
        return dict(success=False,reason='native-adjacent-pairs-insufficient',support=len(unique))
    observed = np.asarray([query[q]['points'][1] for q,s in unique])
    native = np.asarray([source[s,1] for q,s in unique])
    policy = {'maximum_relative_scale_correction':0.} if lock_scale else {}
    geometry = fit_axis_locked_point_geometry(native,observed,matrix,local_points=observed,
        query_shape=frame.shape[:2],forward_backward=np.zeros(len(native)),
        flow_error_values=np.zeros(len(native)),initial_count=len(query),component=None,**policy)
    model = 'h0' if (geometry.get('h0') or {}).get('success') else 'h1'
    selected = geometry.get(model) or {}
    geometry.update(structureModel=model if selected.get('success') else None,
                    nativeChainSupport=len(unique))
    return geometry


def try_alignment(assistant, frame, map_id, floor, *, cancelled=None, entry_seed=None, context=None):
    """Full production proposal + existing residual consumers; no state writer."""
    if os.environ.get('IDENTITYV_FAST_ALIGNMENT', '1') == '0' or not assistant._shared_structure_mode_enabled():
        return None
    if cancelled and cancelled():
        return {'success': False, 'reason': 'structure-work-obsolete'}
    started = time.perf_counter()
    reference = assistant._shared_structure_reference('shared-structure', map_id, floor)
    if reference is None:
        return None
    prepared = load_prepared(reference)
    if prepared is None:
        return None
    prior = None
    group = assistant._active_round_layer_group(map_id)
    if group is not None and str(floor) in group.verified_floors:
        view = assistant._round_group_floor_view(map_id, floor, render_overlay=False)
        if view is not None:
            canonical = view.get('canonicalMatrixSourceToClient')
            adapter = assistant.author_coordinate_adapters.matrix_profile_to_canonical('shared-structure', map_id, floor)
            if canonical is not None and adapter is not None:
                matrix = np.asarray(canonical) @ adapter
                if np.isfinite(matrix).all() and matrix[0,0] > 0 and np.allclose(matrix[:2,:2], np.eye(2)*matrix[0,0]):
                    prior = matrix
    rows, context, diagnostics = propose(frame, reference, prepared, prior=prior, cancelled=cancelled, context=context)
    if rows:
        from single_map_structure import graph_point_geometry, partial_wall_geometry, projected_geometry, regional_validation
        score, scale, translation = rows[0]
        matrix = np.array([[scale,0,translation[0]],[0,scale,translation[1]],[0,0,1]], np.float64)
        static_args = {}
        if reference.known_boundary is not None:
            static_args = dict(source_known_boundary=reference.known_boundary,
                               source_unknown=reference.unknown_mask)
        geometry = (graph_point_geometry(frame, reference.clean_mask, matrix,
                     _context=context,allow_scale_recovery=prior is None,**static_args)
                    if static_args else native_point_geometry(frame,prepared,matrix,context,
                                                               lock_scale=prior is not None))
        selected = geometry.get(geometry.get('structureModel')) or {}
        verified = bool(selected.get('success'))
        if selected.get('success'):
            matrix = np.asarray(selected['matrixSourceToClient'], np.float64)
            projected = projected_geometry(frame, reference.clean_mask, matrix, context)['mask']
            verified = bool(regional_validation(frame,context['query'],projected,
                                                _context=context).get('regionalEdgeVerified'))
        wall_args = dict(static_args)
        if static_args:
            wall_args.update(source_wall_mask=reference.known_boundary,
                             _fit_only=True, _allow_scale_recovery=prior is None)
            if reference.native_wall_segments is not None:
                wall_args['_matching_source_lines'] = reference.native_wall_segments
        # The existing initial solver permits disjoint point proof or wall
        # proof. Following and explicit known walls still run wall holdouts.
        partial = (partial_wall_geometry(frame, reference.clean_mask, matrix,
                                         _context=context, **wall_args)
                   if not verified or prior is not None or static_args else
                   dict(success=False,reason='native-point-heldout-verified'))
        if static_args:
            verified = bool(partial.get('success'))
        if partial.get('success'):
            matrix = np.asarray(partial['matrix'], np.float64)
            verified = True
        if prior is not None and not np.allclose(matrix[:2,:2], prior[:2,:2], rtol=0, atol=1e-10):
            verified = False
        diagnostics.update(pointGeometryReason=geometry.get('reason'),
                           wallGeometryReason=partial.get('reason'))
        if verified:
            residual = assistant._rendered_profile_current_frame_residual(
                frame, reference.clean_mask, matrix, profile_id='shared-structure',
                visible_override=context.get('visible'))
            from identityv_daily_assistant import rendered_profile_residual_is_verified
            if rendered_profile_residual_is_verified(residual) and not (cancelled and cancelled()):
                diagnostics.update(totalMs=(time.perf_counter()-started)*1000, accepted=True)
                assistant._log('experimental-fast-alignment', mapId=map_id, floor=floor, **diagnostics)
                return dict(success=True, reason='experimental-fast-structure',
                    matrixSourceToClient=matrix, coordinateSpace='shared-structure-to-client',
                    routeProfileId='shared-structure', selectedRouteCurrentFrameResidual=residual,
                    fastAlignmentDiagnostics=diagnostics, inliers=len(context.get('nodes', [])))
        diagnostics['reason'] = 'fast-existing-validation-rejected'
    diagnostics.update(totalMs=(time.perf_counter()-started)*1000, accepted=False)
    assistant._log('experimental-fast-alignment', mapId=map_id, floor=floor, **diagnostics)
    return None
