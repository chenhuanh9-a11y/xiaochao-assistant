"""Prepared full-directory local turns; no identity or display authority.

The source model is the existing point/ray/next graph, not complete corner
arms.  A coarse Chebyshev point distance only screens seeds which cannot have
four exact turn hits.  Every survivor uses the ordinary local correspondence
and greedy ordering; absent preparation always leaves the ordinary path open.
"""
from dataclasses import dataclass
import copy
import math
from types import MappingProxyType

import cv2
import numpy as np


def _readonly(value, dtype):
    array = np.asarray(value, dtype=dtype)
    return np.frombuffer(array.tobytes(), dtype=array.dtype).reshape(array.shape)


def _key(item):
    return tuple(str(item[name]) for name in ('mapId', 'floor', 'region', 'sourceProduct'))


@dataclass(frozen=True)
class PreparedLocalTurns:
    key: tuple
    unit: float
    points: np.ndarray
    rays: np.ndarray
    pairs: np.ndarray
    deltas: np.ndarray
    denoms: np.ndarray
    cell: float | None
    groups: np.ndarray | None
    lower: np.ndarray | None

    @property
    def nbytes(self):
        return sum(value.nbytes for value in (self.points, self.rays, self.pairs,
            self.deltas, self.denoms, self.groups, self.lower) if value is not None)


def _point_fields(item, points, rays, unit, allowance, cancelled):
    """Integer L-infinity distances avoid an assumed float32 L2 error bound.

    Nearest-grid source/query rounding costs one cell together.  Subtract TWO
    cells: the extra cell covers guarded floating arithmetic in the forward
    and inverse transforms.  L-infinity <= L2, and flooring/clipping only lowers
    the bound.  Invalid, huge, out-of-field or uncertain coordinates never prune.
    """
    canvas = item.get('canvas') or {}
    width, height = canvas.get('width'), canvas.get('height')
    try:
        finite_canvas = math.isfinite(width) and math.isfinite(height)
    except (TypeError, OverflowError):
        return None, None, None
    if (not len(points) or allowance < 4 or not isinstance(width, (int, float))
            or not isinstance(height, (int, float)) or not finite_canvas or min(width, height) <= 0
            or np.any(points < 0) or np.any(points >= (width, height))):
        return None, None, None
    _, groups = np.unique(np.rint(rays).reshape(len(rays), 4), axis=0, return_inverse=True)
    count = int(groups.max()) + 1
    cell = unit / 12
    if not math.isfinite(cell) or cell <= 0:
        return None, None, None
    for _ in range(16):
        if not math.isfinite(cell):
            return None, None, None
        grid_extent = (height / cell, width / cell)
        if any(not math.isfinite(value) or value > 2 ** 20 for value in grid_extent):
            return None, None, None
        shape = tuple(math.ceil(value) + 1 for value in grid_extent)
        needed = count * shape[0] * shape[1]
        if needed <= allowance:
            break
        cell *= max(1.1, math.sqrt(needed / allowance))
    else:
        return None, None, None
    if max(shape) > 2 ** 20:
        return None, None, None
    grid = np.rint(points / cell).astype(np.int64)
    fields = []
    for group in range(count):
        if cancelled is not None and cancelled():
            raise ValueError('local-structure-preparation-cancelled')
        mask = np.ones(shape, np.uint8)
        ids = np.flatnonzero(groups == group)
        mask[grid[ids, 1], grid[ids, 0]] = 0
        distance = cv2.distanceTransform(mask, cv2.DIST_C, 3)
        # DIST_C has exact integer values on this bounded grid. If the backend
        # does not honour that property, retain all seeds instead of guessing.
        if not np.isfinite(distance).all() or not np.array_equal(distance, np.rint(distance)):
            return None, None, None
        fields.append(np.clip(np.floor(np.maximum(distance.astype(float) - 2, 0) * cell),
                              0, 255).astype(np.uint8))
    return cell, _readonly(groups, np.intp), _readonly(fields, np.uint8)


class LocalStructureIndex:
    """Immutable source snapshots within an explicit, unpruned map/floor domain.

    The lower-field budget changes screening precision, never competition.
    Existing source admission and lifetime checks remain the caller's work.
    Missing entries are retained as directory gaps for the result consumer.
    """
    def __init__(self, entries, *, competition_domain=None,
                 max_lower_bytes=64 * 1024 * 1024, cancelled=None):
        if type(max_lower_bytes) is not int or max_lower_bytes < 0:
            raise ValueError('local-structure-memory-budget-invalid')
        entries = tuple(entries)
        available = tuple(dict.fromkeys((str(row[0]['mapId']), str(row[0]['floor'])) for row in entries))
        domain = available if competition_domain is None else tuple(competition_domain)
        if (any(not isinstance(row, tuple) or len(row) != 2
                or any(not isinstance(value, str) or not value for value in row) for row in domain)
                or len(set(domain)) != len(domain)):
            raise ValueError('local-structure-domain-invalid')
        if not set(available) <= set(domain):
            raise ValueError('local-structure-source-outside-domain')
        allowance = max_lower_bytes // max(1, len(entries))
        members = []
        for item, points, rays, pairs in entries:
            if cancelled is not None and cancelled():
                raise ValueError('local-structure-preparation-cancelled')
            points, rays = _readonly(points, np.float64), _readonly(rays, np.float64)
            raw_pairs = np.asarray(pairs)
            if raw_pairs.dtype.kind not in 'iu':
                raise ValueError('local-structure-pairs-invalid')
            pairs = _readonly(raw_pairs, np.intp)
            unit = float(item['unit'])
            if (points.ndim != 2 or points.shape[1:] != (2,)
                    or rays.shape != (len(points), 2, 2) or pairs.ndim != 2 or pairs.shape[1:] != (2,)
                    or not np.isfinite(points).all() or not np.isfinite(rays).all()
                    or not math.isfinite(unit) or unit <= 0
                    or np.any(pairs < 0) or np.any(pairs >= len(points))):
                raise ValueError('local-structure-graph-invalid')
            deltas = points[pairs[:, 1]] - points[pairs[:, 0]]
            denoms = np.sum(deltas * deltas, axis=1)
            if not np.isfinite(deltas).all() or not np.isfinite(denoms).all():
                raise ValueError('local-structure-pair-geometry-invalid')
            cell, groups, lower = _point_fields(item, points, rays, unit, allowance, cancelled)
            members.append(PreparedLocalTurns(_key(item), unit, points, rays, pairs,
                _readonly(deltas, np.float64), _readonly(denoms, np.float64), cell, groups, lower))
        if len({member.key for member in members}) != len(members):
            raise ValueError('local-structure-duplicate-source')
        if cancelled is not None and cancelled():
            raise ValueError('local-structure-preparation-cancelled')
        self.domain, self.members = domain, tuple(members)
        self.directory_gaps = tuple(row for row in domain if row not in available)
        self._by_key = MappingProxyType({member.key: member for member in members})

    @property
    def nbytes(self):
        return sum(member.nbytes for member in self.members)

    @property
    def lower_bytes(self):
        return sum(member.lower.nbytes for member in self.members if member.lower is not None)

    def for_floor(self, floor):
        result = copy.copy(self)
        result.domain = tuple(row for row in self.domain if row[1] == floor)
        result.directory_gaps = tuple(row for row in self.directory_gaps if row[1] == floor)
        result.members = tuple(member for member in self.members if member.key[1] == floor)
        result._by_key = MappingProxyType({member.key: member for member in result.members})
        return result

    def prepared_for(self, entry):
        """A changed graph uses ordinary search, never a stale rejection field."""
        item, points, rays, pairs = entry
        member = self._by_key.get(_key(item))
        if (member is None or member.unit != item['unit']
                or any(not np.array_equal(value, prepared) for value, prepared in
                       ((points, member.points), (rays, member.rays), (pairs, member.pairs)))):
            return None
        return member


def possible_turns(q, scales, translations, compatible, prepared):
    """Upper bound on distinct query turns passing ordinary L2 <= 3 tests.

    The numerical guard reserves at most a quarter cell for floating error;
    otherwise that seed/query remains possible.  The extra subtracted cell is
    larger than those arithmetic errors plus grid normalization/floor rounding.
    No uncertain or outside-grid observation becomes negative evidence.
    """
    if prepared.lower is None:
        return np.full(len(scales), len(q), dtype=np.int32)
    count = np.zeros(len(scales), np.int32)
    source_extent = max(1., float(np.max(np.abs(prepared.points))))
    combined = {}
    for qi, point in enumerate(q):
        groups = np.unique(prepared.groups[compatible[qi]])
        if not len(groups):
            continue
        key = tuple(groups)
        if key not in combined:
            combined[key] = (prepared.lower[groups[0]] if len(groups) == 1
                             else np.min(prepared.lower[groups], axis=0))
        field = combined[key]
        with np.errstate(all='ignore'):
            grid_float = (point - translations) / scales[:, None] / prepared.cell
            error = (64 * np.finfo(float).eps * (np.max(np.abs(point))
                + np.max(np.abs(translations), axis=1) + np.abs(scales) * source_extent + 1)
                / (np.abs(scales) * prepared.cell))
        safe = (np.isfinite(grid_float).all(axis=1) & (np.max(np.abs(grid_float), axis=1) <= 2 ** 20)
                & np.isfinite(error) & (error <= .25) & (scales > 0))
        grid = np.rint(np.where(safe[:, None], grid_float, 0)).astype(np.int64)
        inside = (safe & (grid[:, 0] >= 0) & (grid[:, 0] < field.shape[1])
                  & (grid[:, 1] >= 0) & (grid[:, 1] < field.shape[0]))
        distance = np.zeros(len(scales))
        distance[inside] = field[grid[inside, 1], grid[inside, 0]] * scales[inside]
        count += distance <= 3
    return count
