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
import io
import math
import threading
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


def _read_chains_rays_fast(fast_source, mid, fl):
    """Read only lightweight chains/rays from precomputed -fast.npz without touching distance cache."""
    entries = getattr(fast_source, "entries", None)
    reader = getattr(fast_source, "reader", None)
    view = getattr(fast_source, "view", None)
    if isinstance(entries, dict) and callable(reader) and view is not None:
        ref = entries.get(("shared-structure", mid, fl))
        if ref is None:
            for key, value in entries.items():
                if isinstance(key, tuple) and len(key) >= 4 and key[1] == mid and str(key[3]) == fl:
                    ref = value
                    break
        if ref is not None:
            raw = reader(view.path(ref.path))
            with np.load(io.BytesIO(raw), allow_pickle=False) as data:
                if "chains" in data and "rays" in data:
                    return np.asarray(data["chains"], dtype=np.float32), np.asarray(data["rays"], dtype=np.float32)
    return None


def _build_floor_bucket(maps):
    postings = defaultdict(dict)
    doc_freq = Counter()
    map_ids = tuple(sorted(maps))
    total = max(1, len(map_ids))
    for mid, desc in maps.items():
        for key, count in desc.items():
            postings[key][mid] = count
            doc_freq[key] += 1
    idf = {key: math.log((total + 1.0) / (df + 0.5)) + 1.0 for key, df in doc_freq.items()}
    return dict(map_ids=map_ids, postings=dict(postings), idf=idf)


class GenericStructureIndex:
    """Automatic per-floor inverted index over any season's shared clean masks."""

    def __init__(self, entries_by_floor, *, floor_loader=None):
        # entries_by_floor: {floor: {map_id: Counter(descriptor -> count)}}
        self._lock = threading.RLock()
        self._by_floor = {str(floor): _build_floor_bucket(maps) for floor, maps in entries_by_floor.items()}
        self._floor_loader = floor_loader
        self._attempted_floors = set(self._by_floor)

    def _ensure_floor(self, floor):
        fl = str(floor)
        bucket = self._by_floor.get(fl)
        if bucket is not None or self._floor_loader is None:
            return bucket
        with self._lock:
            bucket = self._by_floor.get(fl)
            if bucket is not None or fl in self._attempted_floors:
                return bucket
            self._attempted_floors.add(fl)
            maps = self._floor_loader(fl)
            if maps:
                bucket = _build_floor_bucket(maps)
                self._by_floor[fl] = bucket
            return bucket

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

    @staticmethod
    def _load_floor_descriptors(map_ids, fl, provider, fast_source=None):
        from fast_structure_alignment import prepare
        floor_map = {}
        for mid in map_ids:
            chains_rays = None
            if fast_source is not None:
                try:
                    chains_rays = _read_chains_rays_fast(fast_source, mid, fl)
                except Exception:
                    chains_rays = None
            if chains_rays is None and provider is not None:
                try:
                    ref = provider("shared-structure", mid, fl)
                    if ref is not None:
                        prepared = None
                        if fast_source is not None and callable(getattr(fast_source, "read", None)):
                            try:
                                prepared = fast_source.read(ref)
                            except Exception:
                                prepared = None
                        if prepared is None and getattr(ref, "clean_mask", None) is not None:
                            prepared = prepare(ref.clean_mask)
                        if prepared is not None:
                            chains_rays = (prepared.chains, prepared.rays)
                except Exception:
                    chains_rays = None
            if chains_rays is not None:
                floor_map[str(mid)] = _chain_descriptors(chains_rays[0], chains_rays[1])
        return floor_map

    @classmethod
    def from_provider(cls, map_ids, floors, provider, *, fast_source=None, lazy=False):
        """Extract prepared structures from fast_source (.npz chains/rays) or clean_mask provider."""
        ids = tuple(map_ids)
        if lazy:
            allowed = {str(f) for f in floors}
            def load_floor(fl):
                return cls._load_floor_descriptors(ids, fl, provider, fast_source=fast_source) if fl in allowed else {}
            return cls({}, floor_loader=load_floor)
        entries = {}
        for floor in floors:
            fl = str(floor)
            floor_map = cls._load_floor_descriptors(ids, fl, provider, fast_source=fast_source)
            if floor_map:
                entries[fl] = floor_map
        return cls(entries)

    def has_floor(self, floor):
        return self._ensure_floor(floor) is not None

    def score_candidates(self, frame, floor, candidate_ids, *, context=None):
        """Score candidate maps in milliseconds using invariant corner constellations."""
        bucket = self._ensure_floor(floor)
        if bucket is None or not candidate_ids:
            return {}
        if context is None:
            from single_map_structure import current_geometry
            context = current_geometry(frame, observation_bounds=(0, 0, frame.shape[1], frame.shape[0]))
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
