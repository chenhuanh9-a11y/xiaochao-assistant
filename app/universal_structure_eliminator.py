"""Season-agnostic dual-sided structural pool eliminator.

Works with ONLY a season's shared clean-mask structure references (no gate
icon templates and no common-entrance coordinate index required). Used by S0
and future package seasons (S2/S3+) that do not ship a season-specific
entrance coordinate index.

Contradiction requires BOTH a positive witness on this exact frame (at least
one remaining candidate passing dual-sided wall + explored-interior checks)
AND complete exhaustion of candidate poses for every eliminated map.
"""
from collections import OrderedDict
from contextlib import nullcontext
import time

import cv2
import numpy as np


class UniversalStructureEliminator:
    """Candidate-pool eliminator using dual-sided wall & explored-interior checks."""

    MAX_VOID_CACHE = 32
    MAX_PREP_CACHE = 32

    def __init__(self, engine, catalog):
        self.engine = engine
        self.catalog = catalog
        self._void_cache = OrderedDict()
        self._prep_cache = OrderedDict()

    def _prepared(self, map_id, floor, ref):
        """Compact per-eliminator cache avoiding 4-slot global cache thrashing."""
        from fast_structure_alignment import load_prepared, prepare as prepare_fast

        key = (self.catalog.resource_key, str(map_id), str(floor), tuple(ref.clean_mask.shape[:2]))
        cached = self._prep_cache.get(key)
        if cached is not None:
            self._prep_cache.move_to_end(key)
            return cached
        fast_source = getattr(self.engine, "qt_fast_structure_source", None)
        prepared = load_prepared(ref, source=fast_source)
        if prepared is None:
            prepared = prepare_fast(ref.clean_mask)
        if prepared is not None:
            self._prep_cache[key] = prepared
            while len(self._prep_cache) > self.MAX_PREP_CACHE:
                self._prep_cache.popitem(last=False)
        return prepared

    def _void_field(self, map_id, floor, clean_mask):
        """Half-resolution outside distance field (4x faster, <1px error, uint16-compact)."""
        key = (self.catalog.resource_key, str(map_id), str(floor), tuple(clean_mask.shape[:2]))
        cached = self._void_cache.get(key)
        if cached is not None:
            self._void_cache.move_to_end(key)
            return cached
        binary = (np.asarray(clean_mask) > 0).astype(np.uint8)
        h, w = binary.shape[:2]
        sh, sw = max(1, (h + 1) // 2), max(1, (w + 1) // 2)
        if h >= 64 and w >= 64:
            small = cv2.resize(binary, (sw, sh), interpolation=cv2.INTER_AREA)
            small_bin = (small > 0).astype(np.uint8)
            dist = cv2.distanceTransform(1 - small_bin, cv2.DIST_L2, 3) * 2.0
            scale_xy = (sw / float(w), sh / float(h))
        else:
            dist = cv2.distanceTransform(1 - binary, cv2.DIST_L2, 3)
            scale_xy = (1.0, 1.0)
        entry = (dist.astype(np.float32), scale_xy)
        self._void_cache[key] = entry
        while len(self._void_cache) > self.MAX_VOID_CACHE:
            self._void_cache.popitem(last=False)
        return entry

    @staticmethod
    def _evaluate_pose(prepared, void_entry, wall_points, interior_points, scale, translation):
        """Return (compatible, wall_bad_count, wall_span, interior_bad_count, kind)."""
        from s1_common_coordinate_filter import CommonCoordinateFilter
        from structure_observation import has_interior_contradiction

        s = float(scale)
        tx, ty = float(translation[0]), float(translation[1])
        h, w = prepared.shape[:2]

        # 1. Wall check: only spatially coherent unavoidable wall contradictions
        # eliminate a map (no fragile global inlier_ratio cutoff that icons/fog break).
        wxy = np.rint((wall_points - np.array([tx, ty], dtype=np.float64)) / s).astype(int)
        wok = (wxy[:, 0] >= 0) & (wxy[:, 0] < w) & (wxy[:, 1] >= 0) & (wxy[:, 1] < h)
        wdist = np.full(len(wall_points), 10000.0, dtype=np.float64)
        if np.any(wok):
            wdist[wok] = (prepared.distance[wxy[wok, 1], wxy[wok, 0]].astype(np.float64) * 0.25) * s
        wall_bad = wall_points[wdist > 9.0]
        wall_span = np.ptp(wall_bad, axis=0) if len(wall_bad) else np.zeros(2)
        wall_contradicted = CommonCoordinateFilter._has_wall_contradiction(
            wall_bad, wall_span, min_points=14, min_span=26, min_2d_span=12
        )

        # 2. Explored-interior check: high-clearance check so thin interior pillars,
        # doors, or minor rendering thickness differences never falsely kill the true map.
        interior_bad = np.empty((0, 2), dtype=np.float64)
        interior_contradicted = False
        if void_entry is not None and len(interior_points) >= 16:
            void_field, (sx, sy) = void_entry
            vh, vw = void_field.shape[:2]
            ixy_native = (interior_points - np.array([tx, ty], dtype=np.float64)) / s
            ix = np.rint(ixy_native[:, 0] * sx).astype(int)
            iy = np.rint(ixy_native[:, 1] * sy).astype(int)
            iok = (ix >= 0) & (ix < vw) & (iy >= 0) & (iy < vh)
            vdist = np.full(len(interior_points), 10000.0, dtype=np.float64)
            if np.any(iok):
                vdist[iok] = void_field[iy[iok], ix[iok]].astype(np.float64) * s
            interior_bad = interior_points[vdist > 16.0]
            interior_contradicted = has_interior_contradiction(interior_bad, min_points=16, min_span_px=28)

        compatible = (not wall_contradicted) and (not interior_contradicted)
        kind = (
            "none" if compatible
            else "wall+interior" if (wall_contradicted and interior_contradicted)
            else "wall" if wall_contradicted
            else "interior"
        )
        return compatible, int(len(wall_bad)), wall_span, int(len(interior_bad)), kind

    def _candidate_poses(self, frame, context, prepared, coarse, points, *, scale_hint, cancelled):
        """Enumerate top refined candidate poses across the full floor."""
        from fast_structure_alignment import _grid, _chain_seeds, _pitch, _distinct, refine, scores

        rows = []
        edge = context["observed"]
        if scale_hint is not None and 0.03 <= float(scale_hint) <= 6.0:
            rows.extend(_grid(coarse, prepared, float(scale_hint), None, cancelled))
        else:
            seeds = _chain_seeds(frame, context, prepared, cancelled)
            if seeds:
                scales = np.asarray([r[0] for r in seeds], np.float32)
                tt = np.asarray([r[1] for r in seeds], np.float32)
                vals = scores(coarse, prepared, scales, tt)
                rows.extend((float(v), float(s), t) for v, s, t in zip(vals, scales, tt))
            if max((r[0] for r in rows), default=0.0) < 0.45:
                pitch = _pitch(edge)
                if pitch > 0 and prepared.pitch > 0:
                    est = pitch / prepared.pitch
                    if 0.03 <= est <= 6.0:
                        rows.extend(_grid(coarse, prepared, est, None, cancelled))
        if cancelled():
            return []
        retained = _distinct(rows, maximum=3)
        refined = []
        for _, scale, translation in retained:
            if cancelled():
                return []
            refined.append(refine(points, prepared, scale, translation, lock_scale=False))
        return _distinct(refined, minimum=10.0, maximum=3)

    def _evaluate_pool(self, frame, context, ids, floor, wall_points, interior_points,
                       coarse, points, *, scale_hint, cancelled):
        compatible, contradicted, checks = [], [], []
        for mid in ids:
            if cancelled():
                return None
            ref = self.engine.qt_shared_provider("shared-structure", mid, floor)
            if ref is None:
                return None
            prepared = self._prepared(mid, floor, ref)
            if prepared is None:
                return None
            void_entry = self._void_field(mid, floor, ref.clean_mask)
            poses = self._candidate_poses(
                frame, context, prepared, coarse, points,
                scale_hint=scale_hint, cancelled=cancelled,
            )
            if cancelled():
                return None
            best_ok = False
            best_stats = (len(wall_points), np.zeros(2), len(interior_points), "wall")
            for _, s, t in poses:
                ok, wb, wspan, ib, kind = self._evaluate_pose(
                    prepared, void_entry, wall_points, interior_points, s, t
                )
                if ok:
                    best_ok = True
                    best_stats = (wb, wspan, ib, "none")
                    break
                if (wb + ib) < (best_stats[0] + best_stats[2]):
                    best_stats = (wb, wspan, ib, kind)
            if best_ok:
                compatible.append(mid)
            else:
                contradicted.append(mid)
            checks.append(dict(
                mapId=mid,
                unavoidableMissCount=int(best_stats[0]),
                sampleCount=len(wall_points),
                unavoidableSpanPx=best_stats[1].tolist(),
                interiorViolationCount=int(best_stats[2]),
                interiorSampleCount=len(interior_points),
                contradictionKind=best_stats[3],
                explicitContradiction=not best_ok,
            ))
        return compatible, contradicted, checks

    def prepare(self, frame, pool_ids, floor, *, cancelled, stage=None, scale_hint=None):
        """Evaluate every remaining pool map via dual-sided wall + interior checks."""
        from single_map_structure import current_geometry, current_part
        from structure_observation import (
            observed_wall_fragment_edges,
            explored_interior_mask,
            sample_interior_points,
        )
        from fast_structure_alignment import sample_edges
        from season_optional_room import observation_scope

        started = time.perf_counter()
        stage = stage or (lambda *args, **kwargs: None)
        ids = tuple(sorted(set(pool_ids)))

        def fail(reason, **extra):
            return dict(
                schemaVersion=1,
                mode=self.catalog.mode,
                resourceKey=self.catalog.resource_key,
                floor=str(floor),
                evaluatedMapIds=list(ids),
                contradictedMapIds=[],
                unknownMapIds=list(ids),
                coordinateVerified=False,
                identityAuthorized=False,
                gateStatus="not-required",
                gateReason="universal-structure-mode",
                gateMs=0.0,
                totalMs=(time.perf_counter() - started) * 1000,
                reason=reason,
                **extra,
            )

        if not ids or str(floor) not in self.catalog.floors:
            return fail("universal-scope-invalid")
        if cancelled():
            return fail("coordinate-work-obsolete")

        bounds = (0, 0, frame.shape[1], frame.shape[0])
        optional_room = getattr(self.engine, "qt_season", None) == "s1"
        make_scope = getattr(self.engine, "qt_resource_read_scope", None)
        resources = make_scope(cancelled=cancelled) if make_scope is not None else nullcontext()
        grid_runtime = getattr(self.engine, "_qt_grid_runtime", None)
        grid_scope = grid_runtime.fit_scope(cancelled) if grid_runtime is not None else nullcontext()

        with resources, grid_scope, observation_scope(frame, enabled=optional_room, cancelled=cancelled):
            obs_started = time.perf_counter()
            stage("extract", "started", 0.0)
            ctx = current_geometry(frame, observation_bounds=bounds)
            if ctx is None:
                stage("extract", "finished", (time.perf_counter() - obs_started) * 1000)
                return fail("universal-current-geometry-unavailable")
            bx0, by0, bx1, by1 = ctx["observationBounds"]
            if max(bx1 - bx0, by1 - by0) < 50:
                stage("extract", "finished", (time.perf_counter() - obs_started) * 1000)
                return fail("current-structure-insufficient")
            edge = current_part(frame, ctx, "observed")
            wall_edges = observed_wall_fragment_edges(frame, ctx)
            wall_points = sample_edges(wall_edges, 300)
            interior_mask = explored_interior_mask(ctx.get("query"), ctx.get("observationBounds"), margin_px=12)
            interior_points = sample_interior_points(interior_mask, limit=200)
            obs_ms = (time.perf_counter() - obs_started) * 1000
            stage("extract", "finished", obs_ms)
            if len(wall_points) < 24:
                return fail("current-structure-insufficient", observationMs=obs_ms)
            if cancelled():
                return fail("coordinate-work-obsolete", observationMs=obs_ms)

            points = sample_edges(edge, 240)
            coarse = sample_edges(edge, 96)
            if len(coarse) < 20:
                return fail("current-structure-insufficient", observationMs=obs_ms)

            cmp_started = time.perf_counter()
            stage("compare", "started", 0.0)
            use_hint = scale_hint if (isinstance(scale_hint, (int, float)) and 0.03 <= float(scale_hint) <= 6.0) else None
            outcome = self._evaluate_pool(
                frame, ctx, ids, str(floor), wall_points, interior_points,
                coarse, points, scale_hint=use_hint, cancelled=cancelled,
            )
            if outcome is None or cancelled():
                stage("compare", "cancelled", (time.perf_counter() - cmp_started) * 1000)
                return fail("coordinate-work-obsolete", observationMs=obs_ms)

            compatible, contradicted, checks = outcome
            # Zoom-safety fallback: if a hinted scale produced zero compatible maps,
            # retry across the full scale range before deciding anything.
            if not compatible and use_hint is not None:
                outcome = self._evaluate_pool(
                    frame, ctx, ids, str(floor), wall_points, interior_points,
                    coarse, points, scale_hint=None, cancelled=cancelled,
                )
                if outcome is None or cancelled():
                    stage("compare", "cancelled", (time.perf_counter() - cmp_started) * 1000)
                    return fail("coordinate-work-obsolete", observationMs=obs_ms)
                compatible, contradicted, checks = outcome

            cmp_ms = (time.perf_counter() - cmp_started) * 1000
            stage("compare", "finished", cmp_ms)

            if not compatible:
                if len(ids) < len(self.catalog.map_ids):
                    return fail(
                        "coordinate-or-catalogue-conflict",
                        candidateChecks=checks,
                        observationMs=obs_ms,
                        elapsedMs=cmp_ms,
                    )
                return fail(
                    "universal-no-compatible-candidate",
                    candidateChecks=checks,
                    observationMs=obs_ms,
                    elapsedMs=cmp_ms,
                )

            return dict(
                schemaVersion=1,
                mode=self.catalog.mode,
                resourceKey=self.catalog.resource_key,
                floor=str(floor),
                evaluatedMapIds=list(ids),
                contradictedMapIds=contradicted,
                unknownMapIds=compatible,
                coordinateVerified=True,
                poseAvailable=False,
                identityAuthorized=False,
                gateStatus="not-required",
                gateReason="universal-structure-mode",
                gateMs=0.0,
                observationMs=obs_ms,
                elapsedMs=cmp_ms,
                resourceMs=0.0,
                totalMs=(time.perf_counter() - started) * 1000,
                coordinateEvidence=dict(
                    basis="universal-dual-sided-structure",
                    scaleHintUsed=use_hint is not None,
                    perCandidateFreeFit=True,
                ),
                candidateChecks=checks,
                reason="universal-dual-sided-structure-elimination",
            )
