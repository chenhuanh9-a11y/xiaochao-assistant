"""Pure bounded Tilemap proposals; the existing worker owns pose and display."""
from pathlib import Path
from collections import Counter
import time
import zipfile
import cv2
import numpy as np


class DifficultGridLocator:
    def __init__(self, root, entries):
        self.root = Path(root)
        self.entries = entries
        self.cache = {}
        self.descriptors = {}

    def load(self, map_id, floor):
        key = (map_id, floor)
        if key in self.cache:return self.cache[key]
        row = self.entries.get(key)
        if row is None:return None
        try:
            with np.load(self.root/row['gridResource'], allow_pickle=False) as data:
                chains = np.asarray(data['grid_chains'], dtype=float)
                rays = np.asarray(data['grid_rays'], dtype=float)
            if chains.ndim != 3 or chains.shape[1:] != (3, 2):return None
            if rays.shape != (len(chains), 2, 2):return None
            if not np.isfinite(chains).all() or not np.isfinite(rays).all():return None
            result = (chains, rays)
        except (OSError, ValueError, KeyError, zipfile.BadZipFile):
            return None
        self.cache[key] = result
        delta = chains[:, [0, 2]]-chains[:, 1, None]
        self.descriptors[key] = (delta, np.sum(delta*delta, axis=(1, 2)))
        return result

    def propose(self, frame, map_id, floor, *, context=None, cancelled=None):
        from single_map_structure import current_geometry, observed_corner_chains
        started = time.perf_counter()
        diagnostic = dict(attempted=True, mapId=map_id, floor=floor, candidates=[])
        def finish(reason, candidates=()):
            diagnostic.update(reason=reason, elapsedMs=(time.perf_counter()-started)*1000,
                              candidateCount=len(candidates))
            return list(candidates), diagnostic
        if cancelled and cancelled():return finish('structure-work-obsolete')
        row = self.entries.get((map_id,floor),{})
        if row.get('gridDisabledReason'):return finish(row['gridDisabledReason'])
        index = self.load(map_id, floor)
        if index is None:return finish('grid-resource-unavailable')
        if context is None:context = current_geometry(frame)
        if context is None:return finish('grid-query-unavailable')
        query = observed_corner_chains(frame, context)
        chains, rays = index
        if not len(chains) or not query:return finish('grid-supported-chains-insufficient')
        diagnostic.update(queryChainCount=len(query), sourceChainCount=len(chains))
        # A consensus needs six distinct observed AND source chains. With fewer
        # chains no descriptor search can succeed; do not enumerate repetitions.
        if min(len(query), len(chains)) < 6:
            return finish('grid-consensus-insufficient')
        delta, denominator = self.descriptors[(map_id, floor)]
        seeds = []
        # Local measured lengths provide continuous pixel/grid scale. No
        # screen grid rounding and no truth/last-pose input is used here.
        for qi, q in enumerate(query):
            if cancelled and cancelled():return finish('structure-work-obsolete')
            qdelta = q['points'][[0, 2]]-q['points'][1]
            scale = np.divide(np.sum(qdelta*delta, axis=(1, 2)), denominator,
                              out=np.zeros(len(chains)), where=denominator>0)
            error = np.linalg.norm(qdelta-scale[:, None, None]*delta, axis=2).max(axis=1)
            oriented = np.min(np.sum(q['rays']*rays, axis=2), axis=1) >= .95
            for si in np.flatnonzero(oriented & (scale>0) & (error<=3)):
                si=int(si);seed_scale=float(scale[si]);translation=q['points'][1]-seed_scale*chains[si,1]
                seeds.append((qi,si,seed_scale,translation))
                if len(seeds)>1024:return finish('grid-descriptor-budget-unresolved')
        diagnostic['seedCount'] = len(seeds)
        if not seeds:return finish('grid-no-supported-ratios')
        if min(len({q for q,s,scale,t in seeds}), len({s for q,s,scale,t in seeds})) < 6:
            return finish('grid-consensus-insufficient')
        source = np.array([chains[s] for q,s,scale,t in seeds])
        observed = np.array([query[q]['points'] for q,s,scale,t in seeds])
        correspondences = []
        for qi,si,scale,translation in seeds:
            if cancelled and cancelled():return finish('structure-work-obsolete')
            compatible = np.flatnonzero(np.linalg.norm(source*scale+translation-observed, axis=2).max(axis=1)<=3)
            pairs = {(seeds[j][0], seeds[j][1]) for j in compatible}
            # Match the existing point solver's six independent chain support;
            # three repetitive corners alone cannot establish a location.
            query_counts = Counter(q for q,s in pairs)
            source_counts = Counter(s for q,s in pairs)
            unique = {(q,s) for q,s in pairs if query_counts[q]==1 and source_counts[s]==1}
            if len(unique)<6:continue
            if any(unique<=old for old in correspondences):continue
            correspondences=[old for old in correspondences if not old<unique]
            correspondences.append(unique)
        proposals = []
        diagnostic['correspondences'] = []
        for unique in sorted(correspondences,key=lambda pairs:(-len(pairs),sorted(pairs))):
            pairs = sorted(unique)
            points = np.array([query[q]['points'][1] for q,s in pairs])
            if np.min(np.ptp(points, axis=0))<24:continue
            source_points = np.array([chains[s,1] for q,s in pairs])
            centered = source_points-source_points.mean(axis=0)
            scale = float(np.sum(centered*(points-points.mean(axis=0)))/np.sum(centered*centered))
            translation = points.mean(axis=0)-scale*source_points.mean(axis=0)
            if scale<=0 or np.linalg.norm(source_points*scale+translation-points,axis=1).max()>3:continue
            matrix = np.array([[scale,0,translation[0]],[0,scale,translation[1]],[0,0,1.]])
            h,w = self.entries[(map_id,floor)]['sourceShape']
            extent = np.array([[0,0,1],[w,0,1],[0,h,1],[w,h,1]])
            # Translation participates in deduplication, preserving one-cell
            # alternatives even when rotation and scale are identical.
            radius = min(12., .4*scale*self.entries[(map_id,floor)]['gridUnitPixels'])
            if any(np.linalg.norm((extent@(matrix-p).T)[:,:2], axis=1).max()<=radius for p in proposals):continue
            proposals.append(matrix)
            diagnostic['correspondences'].append(dict(source=source_points.tolist(),observed=points.tolist()))
            if len(proposals)>6:
                diagnostic['overflowCandidates']=[p.tolist() for p in proposals]
                return finish('grid-candidate-budget-unresolved')
        diagnostic['candidates'] = [p.tolist() for p in proposals]
        return finish('grid-proposals' if proposals else 'grid-consensus-insufficient', proposals)
