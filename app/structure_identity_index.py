"""Positive, whole-directory retrieval of an already selected constellation A.

This module consumes geometry, never pixels, author displays, B/holdout evidence,
or a per-map pose solver.  Its model is positive uniform scale and translation
with a closed, per-coordinate screen error ``epsilon`` at all three chain
points. Directed rays and positive endpoint/centre connections are also checked;
both prev/next arm orders are indexed because contour traversal may reverse.
It is a fingerprint/proposal producer; even a unique fingerprint cannot lock.

The caller owns the complete competition domain, current resource bindings,
known/unknown eligibility and source-independent selection of A. ``True`` for
eligibility_complete means *all* eligible known-supported anchors were supplied
and unknown/missing geometry cannot hide an eligible competitor. An incomplete
entry can supply positive proposals, but cannot provide negative evidence.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
import math
import time
from typing import Callable, Iterable

import numpy as np


AnchorId = str | int


def _stable_id(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)) or value == "":
        raise ValueError("anchor-id-must-be-a-nonempty-string-or-integer")
    return value


def _coordinates(value, shape, label):
    array = np.asarray(value, dtype=np.float64)
    if array.shape != shape or not np.isfinite(array).all():
        raise ValueError(f"{label}-invalid")
    return array


def _lengths_2d(vectors):
    """Avoid squaring finite 2D components into underflow or overflow."""
    return np.hypot(vectors[..., 0], vectors[..., 1])


def _unit_dot_upper(left, right):
    """Conservative, dimensionless upper bound for direction cosines.

    hypot and fsum avoid naive norm/dot summation error growing with dimension.
    For unit vectors, Cauchy bounds sum(abs(u_i*v_i)) by 1, so product rounding
    has an absolute unit-scale bound. 16 ulp(1) conservatively covers hypot,
    divisions, products and the final sum (including prior normalization).
    Outward rounding applies at every threshold, independently of pixel error.
    """
    upper = []
    for first, second in zip(left, right):
        first_norm, second_norm = math.hypot(*first), math.hypot(*second)
        dot = math.fsum((float(a) / first_norm) * (float(b) / second_norm)
                        for a, b in zip(first, second))
        upper.append(min(1.0, math.nextafter(dot + 16 * math.ulp(1.0), math.inf)))
    return np.asarray(upper)


@dataclass(frozen=True)
class DirectedAnchor:
    """A measured [previous, centre, next] chain with stable physical IDs.

    Tuple snapshots prevent a producer's later numpy/dictionary mutation from
    changing the index. Multiple feature descriptions of one centre must share
    center_id; one-to-one matching is against physical centres, not feature IDs.
    """

    feature_id: AnchorId
    center_id: AnchorId
    points: tuple
    rays: tuple

    def __post_init__(self):
        _stable_id(self.feature_id)
        _stable_id(self.center_id)
        points = _coordinates(self.points, (3, 2), "chain")
        rays = _coordinates(self.rays, (2, 2), "rays")
        delta = points[[0, 2]] - points[1]
        lengths = _lengths_2d(delta)
        ray_lengths = _lengths_2d(rays)
        if (not np.isfinite(lengths).all() or not np.isfinite(ray_lengths).all()
                or np.any(lengths <= 0) or np.any(ray_lengths <= 0)):
            raise ValueError("chain-or-ray-zero-length")
        rays = rays / ray_lengths[:, None]
        # The supplied direction cannot describe a different ordered chain.
        if np.any(_unit_dot_upper(rays, delta / lengths[:, None]) < 1 - 1e-6):
            raise ValueError("rays-do-not-describe-chain")
        object.__setattr__(self, "points", tuple(map(tuple, points.tolist())))
        object.__setattr__(self, "rays", tuple(map(tuple, rays.tolist())))


def anchors_from_arrays(chains, rays, *, feature_ids, center_ids):
    """Adapt prepare.chains/rays; eligibility must be declared separately.

    prepare alone supplies no known/unknown or completeness guarantee.
    """
    chains = np.asarray(chains, dtype=np.float64)
    rays = np.asarray(rays, dtype=np.float64)
    feature_ids, center_ids = tuple(feature_ids), tuple(center_ids)
    if (chains.shape != (len(feature_ids), 3, 2)
            or rays.shape != (len(feature_ids), 2, 2)
            or len(center_ids) != len(feature_ids)):
        raise ValueError("anchor-array-shape-or-id-count")
    return tuple(DirectedAnchor(fid, cid, points, directions)
                 for fid, cid, points, directions in zip(feature_ids, center_ids, chains, rays))


def anchors_from_observed(records):
    """Adapt only the caller's selected observed_corner_chains A records."""
    return tuple(DirectedAnchor(row["index"], row.get("center_id", row["index"]),
                                row["points"], row["rays"]) for row in records)


@dataclass(frozen=True, order=True)
class MapFloorKey:
    mode: str
    map_id: str
    floor: str
    variant: str = ""

    def __post_init__(self):
        if not all(isinstance(value, str) and value for value in
                   (self.mode, self.map_id, self.floor)) or not isinstance(self.variant, str):
            raise ValueError("map-floor-key-invalid")


@dataclass(frozen=True)
class DirectoryEntry:
    key: MapFloorKey
    # Opaque verified resource-instance binding supplied by the resource owner.
    # This component neither resolves paths nor invents a resource identity.
    resource_binding: str | None
    anchors: tuple[DirectedAnchor, ...] | None
    eligible_feature_ids: frozenset[AnchorId] | None
    eligibility_complete: bool | None
    search_allowed: bool | None
    unavailable_reason: str | None = None

    def __post_init__(self):
        if self.anchors is not None:
            object.__setattr__(self, "anchors", tuple(self.anchors))
        if self.eligible_feature_ids is not None:
            object.__setattr__(self, "eligible_feature_ids", frozenset(self.eligible_feature_ids))


@dataclass(frozen=True)
class ConstellationA:
    anchors: tuple[DirectedAnchor, ...]
    source_independent: bool

    def __post_init__(self):
        object.__setattr__(self, "anchors", tuple(self.anchors))


@dataclass(frozen=True)
class DirectoryGap:
    key: MapFloorKey | None
    reason: str
    resource_binding: str | None = None


@dataclass(frozen=True)
class Correspondence:
    query_feature_id: AnchorId
    query_center_id: AnchorId
    source_feature_id: AnchorId
    source_center_id: AnchorId
    query_points: tuple
    source_points: tuple
    source_arm_order: tuple[int, int, int]


class NumericPoseUnresolved(ValueError):
    """The geometric region cannot yet supply a certified float64 matrix."""


@dataclass(frozen=True)
class PoseRegion:
    """Entire continuous feasible region for one correspondence assignment.

    At a positive scale in scale_interval, centered_translation_bounds(scale)
    gives x/y bands for q = scale * (source - source_origin) + translation.
    Global translation_bounds are an outward floating conversion of those
    bands; a usable global float64 matrix still requires seed certification.
    Bounds use all original coordinates, including endpoints.
    """

    scale_interval: tuple[float, float]
    source_points: tuple
    query_points: tuple
    epsilon: float
    roundoff_slack: float = 0.0

    @property
    def source_origin(self):
        return self.source_points[0]

    def centered_translation_bounds(self, scale):
        lo, hi = self.scale_interval
        if not math.isfinite(scale) or scale <= 0 or not lo <= scale <= hi:
            raise ValueError("scale-outside-pose-region")
        source = np.asarray(self.source_points)
        with np.errstate(over="ignore", invalid="ignore"):
            centered = source - self.source_origin
            values = np.asarray(self.query_points) - scale * centered
        tolerance = self.epsilon + self.roundoff_slack
        lower, upper = np.max(values - tolerance, axis=0), np.min(values + tolerance, axis=0)
        if not np.isfinite([lower, upper]).all() or np.any(lower > upper):
            raise NumericPoseUnresolved("centered-translation-bands-not-resolved")
        return tuple(zip(map(float, lower), map(float, upper)))

    def translation_bounds(self, scale):
        centered = np.asarray(self.centered_translation_bounds(scale))
        unit = np.finfo(np.float64).eps / 2
        with np.errstate(over="ignore", invalid="ignore"):
            offset = scale * np.asarray(self.source_origin)
            offset_error = np.abs(offset) * unit / (1 - unit)
            lower = np.nextafter(centered[:, 0] - offset - offset_error, -np.inf)
            upper = np.nextafter(centered[:, 1] - offset + offset_error, np.inf)
        if not np.isfinite([lower, upper]).all() or np.any(lower > upper):
            raise NumericPoseUnresolved("global-translation-bands-not-resolved")
        return tuple(zip(map(float, lower), map(float, upper)))

    def _certified_seed(self):
        lo, hi = self.scale_interval
        scale = lo / 2 + hi / 2 if math.isfinite(hi) else max(1.0, lo)
        if scale <= 0:
            scale = hi
        if not math.isfinite(scale) or scale <= 0:
            raise NumericPoseUnresolved("representative-scale-not-finite-positive")
        bounds = np.asarray(self.centered_translation_bounds(scale))
        centered_translation = bounds[:, 0] / 2 + bounds[:, 1] / 2
        source, query = np.asarray(self.source_points), np.asarray(self.query_points)
        unit = np.finfo(np.float64).eps / 2
        gamma2 = np.nextafter(2 * unit / (1 - 2 * unit), np.inf)
        with np.errstate(over="ignore", invalid="ignore"):
            translation = centered_translation - scale * np.asarray(self.source_origin)
            product = scale * source
            predicted = product + translation
            # Standard two-operation affine-evaluation forward error. Inflate
            # the rounded product magnitude before bounding its exact value.
            product_bound = np.nextafter(np.max(np.abs(product), axis=0) / (1 - unit), np.inf)
            magnitude_bound = np.nextafter(product_bound + np.abs(translation), np.inf)
            arithmetic = np.nextafter(np.nextafter(gamma2 * magnitude_bound, np.inf)
                                     + 2 * np.nextafter(0.0, 1.0), np.inf)
            residual = np.nextafter(np.max(np.abs(predicted - query), axis=0) / (1 - unit)
                                    + np.nextafter(0.0, 1.0), np.inf)
        if not np.isfinite([translation, arithmetic, residual]).all():
            raise NumericPoseUnresolved("global-matrix-arithmetic-not-finite")
        # Auditing one floating evaluation must not hide cancellation: one
        # error bound relates it to the exact matrix, and another covers a
        # subsequent standard float64 evaluation. Matrix-parameter rounding
        # is already present in the audited residual, not added to epsilon.
        if np.any(np.nextafter(residual + 2 * arithmetic, np.inf)
                  > self.epsilon + self.roundoff_slack):
            raise NumericPoseUnresolved("global-matrix-error-exceeds-declared-tolerance")
        return scale, tuple(map(float, translation)), tuple(map(float, arithmetic))

    def seed(self):
        scale, translation, _ = self._certified_seed()
        return scale, translation


@dataclass(frozen=True)
class PoseProposal:
    key: MapFloorKey
    resource_binding: str
    correspondences: tuple[Correspondence, ...]
    pose_region: PoseRegion

    @property
    def matrix_source_to_query(self):
        scale, (tx, ty) = self.pose_region.seed()
        return ((scale, 0.0, tx), (0.0, scale, ty), (0.0, 0.0, 1.0))

    @property
    def global_matrix_arithmetic_error(self):
        """Per-axis forward-error bound over this proposal's source points."""
        return self.pose_region._certified_seed()[2]


@dataclass(frozen=True)
class UnresolvedPoseProposal:
    key: MapFloorKey
    resource_binding: str
    correspondences: tuple[Correspondence, ...]
    pose_region: PoseRegion
    reason: str


@dataclass(frozen=True)
class FingerprintResult:
    proposals: tuple[PoseProposal, ...]
    retrieval_complete: bool
    search_exhausted: bool
    directory_gaps: tuple[DirectoryGap, ...]
    unresolved_reasons: tuple[str, ...]
    fingerprint_unique: bool
    work_done: int
    unresolved_proposals: tuple[UnresolvedPoseProposal, ...] = ()
    # Deliberately unconditional: B, current-frame residual and existing
    # qualifications belong to the shared downstream identity/pose owner.
    authorizesIdentity: bool = field(default=False, init=False)
    lock: bool = field(default=False, init=False)

    @property
    def compatible_map_floors(self):
        return tuple(sorted({proposal.key for proposal in (*self.proposals, *self.unresolved_proposals)}))


@dataclass(frozen=True)
class _Posting:
    entry_index: int
    anchor: DirectedAnchor
    points: np.ndarray
    rays: np.ndarray
    ratio: float
    arm_order: tuple[int, int, int]


class _Unfinished(Exception):
    pass


class _Work:
    def __init__(self, maximum, cancelled, time_budget_ms):
        if maximum is not None and (isinstance(maximum, bool)
                                    or not isinstance(maximum, int) or maximum < 0):
            raise ValueError("max-work-invalid")
        if time_budget_ms is not None and (not math.isfinite(time_budget_ms) or time_budget_ms < 0):
            raise ValueError("time-budget-invalid")
        self.maximum, self.cancelled, self.done = maximum, cancelled, 0
        self.deadline = (time.perf_counter() + time_budget_ms / 1000
                         if time_budget_ms is not None else None)

    def check(self, consume=True):
        if self.cancelled is not None and self.cancelled():
            raise _Unfinished("cancelled")
        if self.deadline is not None and time.perf_counter() >= self.deadline:
            raise _Unfinished("time-budget-exhausted")
        if consume:
            if self.maximum is not None and self.done >= self.maximum:
                raise _Unfinished("work-budget-exhausted")
            self.done += 1


def _validate_centres(anchors):
    feature_ids, by_id, by_point = set(), {}, {}
    for anchor in anchors:
        if not isinstance(anchor, DirectedAnchor) or anchor.feature_id in feature_ids:
            raise ValueError("invalid-or-duplicate-feature-id")
        feature_ids.add(anchor.feature_id)
        point = anchor.points[1]
        if ((anchor.center_id in by_id and by_id[anchor.center_id] != point)
                or (point in by_point and by_point[point] != anchor.center_id)):
            raise ValueError("physical-center-id-coordinate-inconsistent")
        by_id[anchor.center_id], by_point[point] = point, anchor.center_id


def _extend_interval(interval, old_source, old_query, source, query):
    """Eliminate translation exactly, leaving a closed interval in scale.

    max(q-s*x-e) <= min(q-s*x+e) is equivalent to every pairwise
    |(q_i-q_j)-s*(x_i-x_j)| <= 2e, independently on x and y.
    Comparing each new point with old and new points covers every constraint.
    """
    try:
        with np.errstate(all="raise"):
            lo, hi, epsilon = interval
            all_source = np.concatenate((old_source, source))
            all_query = np.concatenate((old_query, query))
            delta_source = (source[:, None] - all_source[None]).reshape(-1)
            delta_query = (query[:, None] - all_query[None]).reshape(-1)
            if not np.isfinite([delta_source, delta_query]).all():
                raise _Unfinished("numeric-point-differences-unresolved")
            zero = delta_source == 0
            if np.any(np.abs(delta_query[zero]) > 2 * epsilon):
                return None
            nonzero = ~zero
            if np.any(nonzero):
                lower = (delta_query[nonzero] - 2 * epsilon) / delta_source[nonzero]
                upper = (delta_query[nonzero] + 2 * epsilon) / delta_source[nonzero]
                if not np.isfinite([lower, upper]).all():
                    raise _Unfinished("numeric-scale-constraints-unresolved")
                # Outward rounding: a floating boundary must not drop a
                # competitor. Intersect ALL inequality intervals, not their union.
                next_lo = float(np.max(np.minimum(lower, upper)))
                next_hi = float(np.min(np.maximum(lower, upper)))
                lo = max(lo, float(np.nextafter(next_lo, -np.inf)))
                hi = min(hi, float(np.nextafter(next_hi, np.inf)))
            if not math.isfinite(lo) or not math.isfinite(hi):
                raise _Unfinished("numeric-scale-interval-unresolved")
            if hi <= 0 or lo > hi:
                return None
            return max(0.0, lo), hi, epsilon
    except FloatingPointError as error:
        # A numerical failure is not negative structural evidence. In
        # particular, an infinite difference must not manufacture scale zero.
        raise _Unfinished("numeric-constraint-arithmetic-unresolved") from error


def _positive_connections(query, source, chosen, query_anchors):
    # Only positive links from A are consumed. Absence of a link is not a
    # negative claim about unseen geometry. Both orientations are checked.
    for other_index, posting in chosen.items():
        other_query = query_anchors[other_index]
        for endpoint in (0, 2):
            if (query.points[endpoint] == other_query.points[1]
                    and tuple(source.points[endpoint]) != posting.anchor.points[1]):
                return False
            if (other_query.points[endpoint] == query.points[1]
                    and tuple(posting.points[endpoint]) != source.anchor.points[1]):
                return False
    return True


class StructureIdentityIndex:
    """All eligible source anchors indexed by ordered arm-length ratio.

    The explicit domain is independent of available entries. Missing/forbidden
    resources remain gaps instead of disappearing from competition. Ratio
    intervals only schedule work; no top-K, Hough winner or local-solver failure
    removes a competitor. Exhaustive correspondence search is budgeted and may
    return unresolved. This first version favours completeness over speed.
    """

    def __init__(self, competition_domain: Iterable[MapFloorKey],
                 entries: Iterable[DirectoryEntry], *, directory_complete: bool | None):
        self.domain, self.entries = tuple(competition_domain), tuple(entries)
        if len(set(self.domain)) != len(self.domain):
            raise ValueError("duplicate-competition-domain-key")
        entry_keys = [entry.key for entry in self.entries]
        if len(set(entry_keys)) != len(entry_keys):
            raise ValueError("duplicate-directory-entry-key")
        gaps, postings = [], []
        if directory_complete is not True:
            gaps.append(DirectoryGap(None, "competition-domain-completeness-unknown"))
        if not self.domain:
            gaps.append(DirectoryGap(None, "competition-domain-empty"))
        for key in self.domain:
            if key not in entry_keys:
                gaps.append(DirectoryGap(key, "directory-entry-missing"))
        for index, entry in enumerate(self.entries):
            def gap(reason):
                gaps.append(DirectoryGap(entry.key, reason, entry.resource_binding))
            if entry.key not in self.domain:
                gap("entry-outside-declared-domain")
                continue
            binding_valid = isinstance(entry.resource_binding, str) and bool(entry.resource_binding)
            if not binding_valid:
                gap("resource-binding-missing")
            if entry.search_allowed is not True:
                gap("search-forbidden" if entry.search_allowed is False else "search-permission-unknown")
            if entry.eligibility_complete is not True:
                gap("known-support-or-eligibility-incomplete")
            if entry.unavailable_reason:
                gap(entry.unavailable_reason)
            if entry.anchors is None:
                gap("source-features-unavailable")
                continue
            _validate_centres(entry.anchors)
            if entry.eligible_feature_ids is None:
                gap("known-valid-feature-set-missing")
                continue
            all_ids = {anchor.feature_id for anchor in entry.anchors}
            if not entry.eligible_feature_ids <= all_ids:
                gap("known-valid-feature-id-unavailable")
            if not binding_valid or entry.search_allowed is not True or entry.unavailable_reason:
                continue
            for anchor in entry.anchors:
                if anchor.feature_id not in entry.eligible_feature_ids:
                    continue
                for arm_order in ((0, 1, 2), (2, 1, 0)):
                    try:
                        with np.errstate(all="raise"):
                            points = np.asarray(anchor.points)[list(arm_order)]
                            delta = points[[0, 2]] - points[1]
                            lengths = _lengths_2d(delta)
                            # Use point-derived directions, not a producer's
                            # accepted raw-ray discrepancy.
                            rays = delta / lengths[:, None]
                            ratio = float(lengths[0] / lengths[1])
                        if (not np.isfinite(rays).all() or not math.isfinite(ratio)
                                or ratio <= 0):
                            raise FloatingPointError("nonfinite-source-descriptor")
                    except FloatingPointError:
                        gap(f"numeric-source-descriptor-unresolved:{anchor.feature_id}")
                        break
                    postings.append(_Posting(index, anchor, points, rays, ratio, arm_order))
        self.directory_gaps = tuple(gaps)
        self._postings = tuple(sorted(postings, key=lambda row: row.ratio))
        self._ratios = tuple(posting.ratio for posting in self._postings)

    def retrieve(self, constellation: ConstellationA, *, epsilon: float,
                 minimum_ray_dot: float = .95, max_work: int | None = 100_000,
                 time_budget_ms: float | None = None,
                 cancelled: Callable[[], bool] | None = None):
        """Retrieve all compatible assignments/pose regions, or report unfinished.

        epsilon is an L-infinity SCREEN-coordinate error per chain point.
        fingerprint_unique means one (mode,map_id), never unique floor/pose.
        A must be chosen before consulting this directory; no B is accepted.
        minimum_ray_dot is a loose pruning threshold on resolved long arms;
        short-arm thresholds include angular uncertainty implied by epsilon.
        """
        if not math.isfinite(epsilon) or epsilon < 0:
            raise ValueError("epsilon-invalid")
        if not math.isfinite(minimum_ray_dot) or not -1 <= minimum_ray_dot <= 1:
            raise ValueError("minimum-ray-dot-invalid")
        work = _Work(max_work, cancelled, time_budget_ms)
        query_anchors = constellation.anchors
        _validate_centres(query_anchors)
        proposals, unresolved, unresolved_proposals = [], [], []

        def result(complete):
            maps = {(proposal.key.mode, proposal.key.map_id) for proposal in proposals}
            retrieval_complete = complete and not self.directory_gaps and not unresolved
            return FingerprintResult(tuple(proposals), retrieval_complete, complete,
                                     self.directory_gaps, tuple(unresolved),
                                     retrieval_complete and len(maps) == 1, work.done,
                                     unresolved_proposals=tuple(unresolved_proposals))

        if constellation.source_independent is not True:
            unresolved.append("constellation-a-source-independence-not-declared")
            return result(False)
        centres = {anchor.center_id: anchor.points[1] for anchor in query_anchors}
        if len(centres) < 6:
            unresolved.append("constellation-a-fewer-than-six-physical-centres")
            return result(False)
        positions = np.asarray(tuple(centres.values()))
        try:
            with np.errstate(all="raise"):
                singular = np.linalg.svd(positions - positions.mean(axis=0), compute_uv=False)
                spread_limit = max(2 * epsilon * math.sqrt(len(positions)), singular[0] * 1e-10)
            if not np.isfinite(singular).all() or not math.isfinite(spread_limit):
                raise FloatingPointError("nonfinite-query-distribution")
        except (FloatingPointError, np.linalg.LinAlgError):
            unresolved.append("numeric-query-distribution-unresolved")
            return result(False)
        # Robust 2D support, not numerical rank from subpixel collinear noise.
        if singular[1] <= spread_limit:
            unresolved.append("constellation-a-not-distributed-in-two-dimensions")
            return result(False)
        # Screen coordinates have already passed through floating producers.
        # Account explicitly for a few arithmetic roundings at a closed error
        # boundary; otherwise equivalent scale/translation can lose a posting
        # by several ULPs. This is exposed separately from caller epsilon.
        query_magnitude = max(1.0, epsilon, float(np.max(np.abs(
            np.asarray([anchor.points for anchor in query_anchors])))))
        roundoff_slack = 16 * np.finfo(np.float64).eps * query_magnitude
        tolerance = epsilon + roundoff_slack
        if not math.isfinite(tolerance):
            unresolved.append("numeric-query-tolerance-unresolved")
            return result(False)
        empty = np.empty((0, 2), dtype=np.float64)
        try:
            work.check(False)
            candidates = [dict() for _ in self.entries]
            for query_index, query in enumerate(query_anchors):
                work.check()
                with np.errstate(all="raise"):
                    qpoints = np.asarray(query.points)
                    qdelta = qpoints[[0, 2]] - qpoints[1]
                    lengths = _lengths_2d(qdelta)
                    qrays = qdelta / lengths[:, None]
                    # An endpoint and its centre can each err by sqrt(2)*epsilon.
                    # Triangle inequality makes this interval a conservative
                    # superset for every positive continuous scale.
                    error = 2 * math.sqrt(2) * tolerance
                if not np.isfinite(qrays).all() or not math.isfinite(error):
                    raise _Unfinished("numeric-query-descriptor-unresolved")
                # With point errors, a short arm's direction is uncertain.
                # A source arm lies in the radius-error ball around the
                # measured query vector. If that ball contains zero, no
                # direction can safely exclude it. Otherwise its largest
                # angle is asin(error / query_length). Keep directed rays,
                # but never let a fixed angular window drop an epsilon-
                # compatible correspondence before original-point checks.
                with np.errstate(all="raise"):
                    ray_floor = np.full(2, -1.0)
                    resolved = lengths > error
                    ray_floor[resolved] = np.sqrt(np.maximum(0.0,
                        1 - (error / lengths[resolved]) ** 2))
                    ray_floor = np.minimum(minimum_ray_dot, ray_floor)
                    lower = max(0.0, float((lengths[0] - error) / (lengths[1] + error)))
                    # This explicit infinity denotes an unbounded interval;
                    # arithmetic overflow/NaN is separately unresolved.
                    upper = (float((lengths[0] + error) / (lengths[1] - error))
                             if lengths[1] > error else math.inf)
                if (not np.isfinite(ray_floor).all() or not math.isfinite(lower)
                        or (lengths[1] > error and not math.isfinite(upper))):
                    raise _Unfinished("numeric-query-ratio-range-unresolved")
                start = bisect_left(self._ratios, np.nextafter(lower, -np.inf))
                end = bisect_right(self._ratios, np.nextafter(upper, np.inf))
                for posting in self._postings[start:end]:
                    work.check()
                    if np.any(_unit_dot_upper(qrays, posting.rays) < ray_floor):
                        continue
                    interval = _extend_interval((0.0, math.inf, tolerance), empty, empty,
                                                posting.points, qpoints)
                    if interval is not None:
                        candidates[posting.entry_index].setdefault(query_index, []).append(posting)
            for entry_index, choices in enumerate(candidates):
                work.check()
                if len(choices) != len(query_anchors):
                    continue
                # Scheduling may depend on posting counts; the selected A and
                # every required node remain unchanged and are all consumed.
                order = sorted(choices, key=lambda qi: (len(choices[qi]), qi))

                def visit(depth, chosen, used_centres, interval, source_points, query_points):
                    work.check()
                    if depth == len(order):
                        entry = self.entries[entry_index]
                        correspondences = tuple(Correspondence(
                            query_anchors[qi].feature_id, query_anchors[qi].center_id,
                            chosen[qi].anchor.feature_id, chosen[qi].anchor.center_id,
                            query_anchors[qi].points, tuple(map(tuple, chosen[qi].points)),
                            chosen[qi].arm_order)
                            for qi in range(len(query_anchors)))
                        region = PoseRegion(interval[:2], tuple(map(tuple, source_points)),
                                            tuple(map(tuple, query_points)), epsilon, roundoff_slack)
                        try:
                            region.seed()
                        except NumericPoseUnresolved as error:
                            reason = f"numeric-pose-unresolved:{entry.key}:{error}"
                            if reason not in unresolved:
                                unresolved.append(reason)
                            unresolved_proposals.append(UnresolvedPoseProposal(entry.key,
                                entry.resource_binding, correspondences, region, str(error)))
                        else:
                            proposals.append(PoseProposal(entry.key, entry.resource_binding,
                                                          correspondences, region))
                        return
                    qi = order[depth]
                    query = query_anchors[qi]
                    for posting in choices[qi]:
                        work.check()
                        prior_query = used_centres.get(posting.anchor.center_id)
                        if prior_query is not None and prior_query != query.center_id:
                            continue
                        if any(query_anchors[old_qi].center_id == query.center_id
                               and old_posting.anchor.center_id != posting.anchor.center_id
                               for old_qi, old_posting in chosen.items()):
                            continue
                        if not _positive_connections(query, posting, chosen, query_anchors):
                            continue
                        next_interval = _extend_interval(interval, source_points, query_points,
                                                         posting.points, np.asarray(query.points))
                        if next_interval is None:
                            continue
                        chosen[qi] = posting
                        visit(depth + 1, chosen, used_centres |
                              {posting.anchor.center_id: query.center_id}, next_interval,
                              np.concatenate((source_points, posting.points)),
                              np.concatenate((query_points, np.asarray(query.points))))
                        del chosen[qi]

                visit(0, {}, {}, (0.0, math.inf, tolerance), empty, empty)
            work.check(False)
        except _Unfinished as error:
            unresolved.append(str(error))
            return result(False)
        except FloatingPointError:
            unresolved.append("numeric-query-arithmetic-unresolved")
            return result(False)
        except RecursionError:
            unresolved.append("correspondence-depth-unresolved")
            return result(False)
        return result(True)
