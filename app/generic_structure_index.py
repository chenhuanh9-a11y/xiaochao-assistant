"""Season-agnostic local corner-chain geometric fingerprint index.

Builds an automatic scale- and translation-invariant inverted index directly
from standard ``clean_mask`` structures (prepared via ``fast_structure_alignment``)
for any season, mode, and floor.

This index provides *shortlist ordering* only: it ranks likely maps first so the
shared pose solver verifies the right map immediately, without eliminating any
candidate or granting identity/pose authority on its own.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import math
import numpy as np


def _ray_bin(ray):
    return (int(round(float(ray[0]) * 4.0)), int(round(float(ray[1]) * 4.0)))


def _ratio_bin(length_a, length_b):
    a = max(float(length_a), 1.0)
    b = max(float(length_b), 1.0)
    return int(round(math.log2(a / b) * 3.0))


def _chain_descriptors(chains, rays):
    """Extract scale- and translation-invariant single- and pair-corner keys."""
    if chains is None or rays is None or not len(chains):
        return Counter()
    chains = np.asarray(chains, dtype=float)
    rays = np.asarray(rays, dtype=float)
    centers = chains[:, 1]
    leg_a = np.linalg.norm(chains[:, 0] - centers, axis=1)
    leg_b = np.linalg.norm(chains[:, 2] - centers, axis=1)
    keys = Counter()
    singles = []
    for idx in range(len(chains)):
        rb0 = _ray_bin(rays[idx, 0])
        rb1 = _ray_bin(rays[idx, 1])
        rbin = _ratio_bin(leg_a[idx], leg_b[idx])
        single = ("c", rb0, rb1, rbin)
        keys[single] += 1
        singles.append((rb0, rb1, max(leg_a[idx], leg_b[idx], 1.0)))

    # Local 2-corner constellations: for each corner, link to its nearest 3
    # spatial neighbors using scale-invariant relative direction & ratio bins.
    n = len(centers)
    if n >= 2:
        diffs = centers[None, :, :] - centers[:, None, :]
        dists = np.linalg.norm(diffs, axis=2)
        np.fill_diagonal(dists, np.inf)
        k = min(3, n - 1)
        nearest = np.argpartition(dists, k - 1, axis=1)[:, :k]
        for i in range(n):
            rb_i0, rb_i1, scale_i = singles[i]
            for j in nearest[i]:
                d = float(dists[i, j])
                if not math.isfinite(d) or d <= 1.0:
                    continue
                ux = diffs[i, j, 0] / d
                uy = diffs[i, j, 1] / d
                dir_bin = (int(round(ux * 4.0)), int(round(uy * 4.0)))
                dist_bin = _ratio_bin(d, scale_i)
                rb_j0, rb_j1, _ = singles[int(j)]
                keys[("p", rb_i0, rb_i1, rb_j0, rb_j1, dir_bin, dist_bin)] += 1
    return keys


def _query_descriptors(frame, context):
    from single_map_structure import observed_corner_chains
    nodes = observed_corner_chains(frame, context)
    if not nodes:
        return Counter()
    chains = np.asarray([node["points"] for node in nodes], dtype=float)
    rays = np.asarray([node["rays"] for node in nodes], dtype=float)
    return _chain_descriptors(chains, rays)


class GenericStructureIndex:
    """Automatic per-floor inverted index over any season's shared clean masks."""

    def __init__(self, entries_by_floor):
        # entries_by_floor: {floor: {map_id: Counter(descriptor -> count)}}
        self._by_floor = {}
        for floor, maps in entries_by_floor.items():
            postings = defaultdict(dict)
            doc_freq = Counter()
            map_ids = tuple(sorted(maps))
            total = max(1, len(map_ids))
            for mid, desc in maps.items():
                for key, count in desc.items():
                    postings[key][mid] = count
                    doc_freq[key] += 1
            idf = {key: math.log((total + 1.0) / (df + 0.5)) + 1.0 for key, df in doc_freq.items()}
            self._by_floor[str(floor)] = dict(map_ids=map_ids, postings=dict(postings), idf=idf)

    @classmethod
    def from_prepared(cls, prepared_by_floor):
        """Build from {floor: {map_id: PreparedStructure}}."""
        entries = {}
        for floor, items in prepared_by_floor.items():
            floor_map = {}
            for mid, prepared in items.items():
                if prepared is None:
                    continue
                floor_map[str(mid)] = _chain_descriptors(prepared.chains, prepared.rays)
            entries[str(floor)] = floor_map
        return cls(entries)

    @classmethod
    def from_provider(cls, map_ids, floors, provider, *, fast_source=None):
        """Lazily extract prepared structures from fast_source or clean_mask provider."""
        from fast_structure_alignment import prepare
        prepared_by_floor = {}
        for floor in floors:
            fl = str(floor)
            floor_items = {}
            for mid in map_ids:
                prepared = None
                if fast_source is not None:
                    try:
                        prepared = fast_source.load("shared-structure", mid, fl)
                    except Exception:
                        prepared = None
                if prepared is None and provider is not None:
                    try:
                        ref = provider("shared-structure", mid, fl)
                        if ref is not None and getattr(ref, "clean_mask", None) is not None:
                            prepared = prepare(ref.clean_mask)
                    except Exception:
                        prepared = None
                if prepared is not None:
                    floor_items[str(mid)] = prepared
            if floor_items:
                prepared_by_floor[fl] = floor_items
        return cls.from_prepared(prepared_by_floor)

    def has_floor(self, floor):
        return str(floor) in self._by_floor

    def score_candidates(self, frame, floor, candidate_ids, *, context=None):
        """Score candidate maps in milliseconds using invariant corner constellations."""
        bucket = self._by_floor.get(str(floor))
        if bucket is None or not candidate_ids:
            return {}
        if context is None:
            from single_map_structure import current_geometry
            context = current_geometry(frame)
        if context is None:
            return {}
        query = _query_descriptors(frame, context)
        if not query:
            return {}
        postings = bucket["postings"]
        idf = bucket["idf"]
        allowed = set(candidate_ids)
        scores = {mid: 0.0 for mid in allowed}
        for key, qcount in query.items():
            hits = postings.get(key)
            if not hits:
                continue
            weight = idf.get(key, 1.0) * (2.0 if key[0] == "p" else 1.0)
            for mid, ref_count in hits.items():
                if mid in allowed:
                    scores[mid] += weight * min(qcount, ref_count)
        return scores

    def shortlist(self, frame, floor, candidate_ids, *, context=None):
        """Return candidate_ids reordered with strongest geometric matches first."""
        ordered = list(candidate_ids)
        if len(ordered) <= 1:
            return tuple(ordered)
        scores = self.score_candidates(frame, floor, ordered, context=context)
        if not scores or max(scores.values(), default=0.0) <= 0.0:
            return tuple(ordered)
        index_pos = {mid: i for i, mid in enumerate(ordered)}
        ordered.sort(key=lambda mid: (-scores.get(mid, 0.0), index_pos[mid]))
        return tuple(ordered)
