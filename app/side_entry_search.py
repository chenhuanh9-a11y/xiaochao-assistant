"""Prepared doorway neighbourhoods and conservative full-floor rival screening.

Door locations order structure search; no live icon or pose is needed. Rounded
point distance fields only reject impossible four-turn seeds. Every surviving
seed uses the original full reference and final competition is still complete.
"""
import json
import time
from zipfile import BadZipFile
from pathlib import Path

import cv2
import numpy as np


def prepare_arrays(item, points, rays, pairs, door):
    cell = float(item['unit']) / 12
    # Raster-fitted rays have tiny angular differences. A distance field per
    # exact float ray would repeat a full canvas for nearly every source turn.
    # Coarse groups may contain EXTRA orientations, which only loosens a lower
    # bound; exact ray compatibility still owns the final correspondence.
    types, first, groups = np.unique(np.rint(rays).astype(np.int8).reshape(len(rays), 4),
                                    axis=0, return_index=True, return_inverse=True)
    width, height = item['canvas']['width'], item['canvas']['height']
    shape = (int(np.ceil(height/cell))+1, int(np.ceil(width/cell))+1)
    fields = []
    grid = np.rint(points/cell).astype(int)
    for group in range(len(types)):
        ids = np.flatnonzero(groups == group)
        mask = np.ones(shape, np.uint8)
        mask[grid[ids, 1], grid[ids, 0]] = 0
        distance = cv2.distanceTransform(mask, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
        # Each endpoint moves by at most sqrt(2)*cell/2 on rounding. Subtract
        # BOTH errors and round down: this is a lower bound, never a score.
        fields.append(np.clip(np.floor(distance*cell-np.sqrt(2)*cell-1e-4), 0, 255).astype(np.uint8))
    deltas = points[pairs[:, 1]]-points[pairs[:, 0]]
    radius = np.max(np.abs(points[pairs]-door['boundaryCenter']), axis=(1, 2))/item['unit']
    return dict(points=points, rays=rays, pairs=pairs, cell=np.asarray(cell), first=first, groups=groups,
                lower=np.asarray(fields), deltas=deltas, denoms=np.sum(deltas*deltas, axis=1), radius=radius)


def write_index(path, entries, doors):
    arrays, records = {}, []
    for item, points, rays, pairs in entries:
        if str(item['floor']) != '2':
            continue
        door = doors[item['mapId']]
        key = 'm'+str(len(records))
        prepared = prepare_arrays(item, points, rays, pairs, door)
        arrays.update({key+'_'+name: value for name, value in prepared.items()})
        records.append(dict(key=key, mapId=item['mapId'], floor='2',
            structureVersionKey=door['structureVersionKey'], sourceVersion=door['sourceVersion']))
    assert len(records) == len(doors)
    arrays['metadata'] = np.asarray(json.dumps(dict(schemaVersion=1, entries=records)))
    with Path(path).open('xb') as target:
        np.savez_compressed(target, **arrays)


def load_index(root, entries, doors):
    if not doors:
        return {}, 'side-door-source-unavailable'
    try:
        targets = {(item['mapId'], str(item['floor'])): (item, points, rays, pairs)
                   for item, points, rays, pairs in entries if str(item['floor']) == '2'}
        result = {}
        with np.load(Path(root)/'side-entry-search.npz', allow_pickle=False) as archive:
            metadata = json.loads(str(archive['metadata'].item()))
            if metadata['schemaVersion'] != 1:
                raise ValueError('side local index version mismatch')
            for record in metadata['entries']:
                binding = (record['mapId'], record['floor'])
                item, points, rays, pairs = targets[binding]
                door = doors[record['mapId']]
                if any(record[name] != door[name] for name in ('sourceVersion', 'structureVersionKey')):
                    raise ValueError('side local index source mismatch')
                key = record['key']
                prepared = {name: archive[key+'_'+name] for name in
                            ('points','rays','pairs','cell','first','groups','lower','deltas','denoms','radius')}
                # Existing structure versions describe raw geometry, not a
                # particular contour extractor. Compare its actual graph too.
                if not all(np.array_equal(prepared[name], value) for name, value in
                           (('points', points), ('rays', rays), ('pairs', pairs))):
                    raise ValueError('side local index contour mismatch')
                if (prepared['lower'].ndim != 3 or prepared['lower'].dtype != np.uint8
                        or prepared['radius'].shape != (len(pairs),)
                        or prepared['deltas'].shape != (len(pairs), 2)
                        or prepared['denoms'].shape != (len(pairs),)
                        or prepared['groups'].shape != (len(points),)
                        or float(prepared['cell']) != item['unit']/12
                        or len(prepared['first']) != len(prepared['lower'])):
                    raise ValueError('invalid side local index arrays')
                expected_groups = np.unique(np.rint(rays).astype(np.int8).reshape(len(rays),4),
                                            axis=0,return_inverse=True)[1]
                if not np.array_equal(prepared['groups'],expected_groups):
                    raise ValueError('side local index orientation mismatch')
                for value in prepared.values():
                    value.setflags(write=False)
                if binding in result:
                    raise ValueError('duplicate side local index map')
                result[binding] = prepared
        if set(result) != set(targets):
            raise ValueError('side local index inventory incomplete')
        return result, None
    except (OSError, ValueError, KeyError, TypeError, IndexError, BadZipFile) as error:
        return {}, str(error)


def _seeds(q, qpairs, compatible, source, pairs, prepared, frame_shape, unit, deadline):
    scales, translations, radii = [], [], []
    for a, b in qpairs:
        if time.perf_counter() >= deadline:
            return np.empty(0), np.empty((0, 2)), np.empty(0), False
        selected = (compatible[a, pairs[:, 0]] & compatible[b, pairs[:, 1]]
                    & (prepared['denoms'] >= (.6*unit)**2))
        delta = prepared['deltas'][selected]
        scale = ((q[b]-q[a]) @ delta.T)/prepared['denoms'][selected]
        keep = ((scale*unit >= 8) & (scale*unit <= min(frame_shape[:2])/4)
                & (np.linalg.norm(q[b]-q[a]-scale[:, None]*delta, axis=1) <= 3))
        scales.append(scale[keep])
        translations.append((q[a]-scale[:, None]*source[pairs[selected, 0]])[keep])
        radii.append(prepared['radius'][selected][keep])
    scale = np.concatenate(scales)
    translation = np.concatenate(translations)
    radius = np.concatenate(radii)
    keys = np.rint(np.column_stack((scale*unit*4, translation/3))).astype(np.int64)
    _, first = np.unique(keys, axis=0, return_index=True)
    first.sort()  # Same first seed per key as the original query/edge order.
    return scale[first], translation[first], radius[first], True


def possible_turns(q, scales, translations, compatible, prepared):
    """Upper bound on exact three-pixel hits, allowing non-unique matches.

    Out-of-field queries stay possible (zero lower bound), so clipping cannot
    silently remove a seed. The original one-to-one/extent tests run afterwards.
    """
    count = np.zeros(len(scales), np.int32)
    if not len(scales):
        return count
    cell, fields = float(prepared['cell']), prepared['lower']
    combined = {}
    for qi, point in enumerate(q):
        groups = np.unique(prepared['groups'][compatible[qi]])
        if not len(groups):
            continue
        key = tuple(groups)
        if key not in combined:
            combined[key] = fields[groups[0]] if len(groups) == 1 else np.min(fields[groups], axis=0)
        field = combined[key]
        grid = np.rint((point-translations)/scales[:, None]/cell).astype(np.int64)
        inside = ((grid[:, 0] >= 0) & (grid[:, 0] < field.shape[1])
                  & (grid[:, 1] >= 0) & (grid[:, 1] < field.shape[0]))
        distance = np.zeros(len(scales))
        distance[inside] = field[grid[inside, 1], grid[inside, 0]]*scales[inside]
        count += distance <= 3
    return count


def local_hypotheses(q, qpairs, compatible, source, pairs, prepared, frame_shape, unit, deadline):
    scale, translation, radius, complete = _seeds(q, qpairs, compatible, source, pairs, prepared, frame_shape, unit, deadline)
    qids, sids = np.nonzero(compatible)
    query, reference = q[qids], source[sids]
    local = radius <= 3
    # Complete global rivalry is established with cheap prepared bounds, not
    # by pretending unsearched regions are absent. Only plausible far seeds
    # enter the expensive exact correspondence calculation.
    far = np.flatnonzero(~local)
    possible = possible_turns(q, scale[far], translation[far], compatible, prepared)
    expanded = np.zeros(len(scale), bool)
    expanded[far[possible >= 4]] = True
    selected = np.flatnonzero(local | expanded)
    ring = np.searchsorted([3., 5., 8.], radius[selected], side='left')
    order = selected[np.argsort(ring, kind='stable')]
    candidates = []
    evaluated = [0, 0, 0, 0]
    for index in order:
        if time.perf_counter() >= deadline:
            complete = False
            break
        s, t = scale[index], translation[index]
        evaluated[int(np.searchsorted([3., 5., 8.], radius[index]))] += 1
        hits = np.linalg.norm(query-(reference*s+t), axis=1) <= 3
        hq, hs = qids[hits], sids[hits]
        if len(hq) < 4:
            continue
        unique = (np.bincount(hq)[hq] == 1) & (np.bincount(hs)[hs] == 1)
        matched = np.column_stack((hq[unique], hs[unique]))
        if len(matched) < 4:
            continue
        extent = np.ptp(source[matched[:, 1]], axis=0)/unit
        if min(extent) < 1:
            continue
        candidates.append((index, dict(scale=s, translation=t.tolist(), chainCount=len(matched),
                                        extentGrid=extent.tolist(), pairs=matched.tolist())))
    hypotheses = []
    # Restore original greedy pose deduplication/tie order after prioritizing
    # local computation. A different traversal cannot change the winner.
    for _, candidate in sorted(candidates, key=lambda value:value[0]):
        s, t = candidate['scale'], np.asarray(candidate['translation'])
        if any(np.linalg.norm(t-h['translation'])+abs(s-h['scale'])*unit < 3 for h in hypotheses):
            continue
        hypotheses.append(candidate)
    return hypotheses, complete, dict(localSeedTotal=int(local.sum()),
        outsideSeedsScreened=len(far), outsideSeedsRejected=int(np.sum(possible < 4)),
        exactSeedEvaluations=sum(evaluated), ringEvaluations=evaluated)
