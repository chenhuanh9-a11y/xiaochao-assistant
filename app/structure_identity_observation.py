"""Source-independent measured A/B geometry from one immutable frame.

Only ordinary observed_corner_chains are consumed. Marker recovery proposals,
source maps, fitted residuals, authors and similarity groups never select A.
The fixed screen-cell split precedes A selection; B cannot repair that selection.
This is a geometric observation, not proof that colour/fog contours are the
same feature class as a static renderer-alpha texture footprint.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import time

import cv2
import numpy as np

from entry_evidence_work import FrameSnapshot
from single_map_structure import current_geometry, current_part, observed_corner_chains
from structure_identity_index import ConstellationA, DirectedAnchor, _constraint_kind


OBSERVATION_QUALIFICATION_GAPS = (
    "screen-colour-boundary-versus-static-alpha-footprint-unverified",
    "fog-frontier-classification-unavailable",
)


class _Cancelled(Exception):
    pass


def _check_cancelled(cancelled):
    if cancelled is not None and cancelled():
        raise _Cancelled()


def constellation_issue(anchors, minimum_centres, epsilon, label):
    centres = {anchor.points[1] for anchor in anchors}
    if len(centres) < minimum_centres:
        return f"{label}-physical-centres-insufficient"
    points = np.asarray(sorted(centres), dtype=np.float64)
    try:
        with np.errstate(all="raise"):
            singular = np.linalg.svd(points - points.mean(axis=0), compute_uv=False)
            limit = max(2 * epsilon * math.sqrt(len(points)), float(singular[0]) * 1e-10)
        if not np.isfinite(singular).all() or not math.isfinite(limit):
            return f"{label}-distribution-numeric-unresolved"
    except (FloatingPointError, np.linalg.LinAlgError):
        return f"{label}-distribution-numeric-unresolved"
    if singular[1] <= limit:
        return f"{label}-not-distributed-in-two-dimensions"
    return None


def _arms(anchor):
    return frozenset(tuple(sorted((anchor.points[1], anchor.points[index]))) for index in (0, 2))


def _chain_pixels(anchor, shape):
    pixels = []
    centre = np.asarray(anchor.points[1])
    for endpoint in (anchor.points[0], anchor.points[2]):
        delta = np.asarray(endpoint) - centre
        count = int(math.ceil(math.hypot(*delta))) + 1
        pixels.append(np.rint(centre + np.linspace(0, 1, count)[:, None] * delta).astype(np.int64))
    points = np.concatenate(pixels)
    height, width = shape
    if (np.any(points < 0) or np.any(points[:, 0] >= width) or np.any(points[:, 1] >= height)):
        raise ValueError("measurement-support-outside-frame")
    return points


def independence_violations(a, b, frame_shape, support_halo=5, *, cancelled=None):
    """Check complete vertices/arms and both measurement support halos.

    Two radius-h supports overlap iff one raw support intersects the other's
    radius-2h dilation. Thus this checks both 5px halos, not centre separation.
    Pixel supports use the same rounded line sampling as measured chains.
    """
    if isinstance(support_halo, bool) or not isinstance(support_halo, int) or support_halo < 5:
        raise ValueError("measurement-support-halo-must-be-at-least-five")
    shape = tuple(frame_shape[:2])
    support = np.zeros(shape, np.uint8)
    vertices, arms = set(), set()
    for anchor in a:
        _check_cancelled(cancelled)
        vertices.update(anchor.points)
        arms.update(_arms(anchor))
        points = _chain_pixels(anchor, shape)
        support[points[:, 1], points[:, 0]] = 1
    radius = 2 * support_halo
    expanded = cv2.dilate(support, np.ones((2 * radius + 1, 2 * radius + 1), np.uint8))
    failures = []
    for anchor in b:
        _check_cancelled(cancelled)
        if _arms(anchor) & arms:
            reason = "shared-arm"
        elif set(anchor.points) & vertices:
            reason = "shared-vertex"
        else:
            points = _chain_pixels(anchor, shape)
            reason = "shared-measurement-support-halos" if expanded[points[:, 1], points[:, 0]].any() else None
        if reason is not None:
            failures.append((anchor.feature_id, reason))
    return tuple(failures)


@dataclass(frozen=True)
class StructureIdentityObservation:
    frame_key: str
    frame_shape: tuple[int, int]
    observation_bounds: tuple[int, int, int, int]
    a: ConstellationA
    b: tuple[DirectedAnchor, ...]
    epsilon: float
    support_halo: int
    measured_chain_count: int
    a_pool_count: int
    b_pool_count: int
    rejected_b: tuple
    excluded_measurements: tuple
    issues: tuple[str, ...]
    extraction_ms: float
    qualification_gaps: tuple[str, ...] = OBSERVATION_QUALIFICATION_GAPS

    @property
    def constraint_kind(self):
        # A is the sole observation-side writer of the model selection.
        return getattr(self.a, "constraint_kind", None)

    @property
    def geometry_ready(self):
        return not self.issues


def _choose_a(pool, cancelled):
    # One descriptor per physical centre. Stable tie-breaking never consults B.
    by_centre = {}
    for anchor in sorted(pool, key=lambda row: (row.points[1][1], row.points[1][0], repr(row.feature_id))):
        by_centre.setdefault(anchor.points[1], anchor)
    candidates = tuple(by_centre.values())
    if not candidates:
        return ()
    points = np.asarray([anchor.points[1] for anchor in candidates])
    chosen = [0]
    distance = np.sum((points - points[0]) ** 2, axis=1)
    for _ in range(min(6, len(candidates)) - 1):
        _check_cancelled(cancelled)
        distance[chosen] = -1
        selected = int(np.argmax(distance))
        chosen.append(selected)
        distance = np.minimum(distance, np.sum((points - points[selected]) ** 2, axis=1))
    return tuple(candidates[index] for index in chosen)


def partition_observed_chains(records, *, frame_key, frame_shape, observation_bounds=None,
                              epsilon=3.0, support_halo=5, cancelled=None,
                              constraint_kind="full-chain"):
    """Partition caller-supplied measured records without a source catalogue.

    A candidates use even cells of a fixed 4x4 screen grid; B uses odd cells.
    A is selected using only the A pool. Afterwards conflicting B supports are
    removed, never replaced by selecting a more convenient A constellation.
    """
    began = time.perf_counter()
    _constraint_kind(constraint_kind)
    if not math.isfinite(epsilon) or epsilon < 0:
        raise ValueError("observation-epsilon-invalid")
    if isinstance(support_halo, bool) or not isinstance(support_halo, int) or support_halo < 5:
        raise ValueError("measurement-support-halo-must-be-at-least-five")
    height, width = (int(value) for value in frame_shape[:2])
    bounds = tuple(observation_bounds or (0, 0, width, height))
    x0, y0, x1, y1 = bounds
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise ValueError("observation-bounds-invalid")
    a_pool, b_pool, excluded, rejected = [], [], [], ()
    a, b, issues = (), (), []
    records = tuple(records)
    try:
        centres = {}
        for row in records:
            _check_cancelled(cancelled)
            point = tuple(float(value) for value in row["points"][1])
            cid = centres.setdefault(point, row["index"])
            anchor = DirectedAnchor(row["index"], cid, row["points"], row["rays"])
            points = np.asarray(anchor.points)
            if (np.any(points < (x0 + support_halo, y0 + support_halo))
                    or np.any(points > (x1 - 1 - support_halo, y1 - 1 - support_halo))):
                excluded.append((anchor.feature_id, "measurement-halo-crosses-observation-boundary"))
                continue
            # Grid coordinates are fixed by the client frame, not resized to
            # the visible component/B geometry. Actual crop bounds only decide
            # whether a complete measurement halo is available.
            column = min(3, int(point[0] * 4 / width))
            line = min(3, int(point[1] * 4 / height))
            (a_pool if (column + line) % 2 == 0 else b_pool).append(anchor)
        a = _choose_a(a_pool, cancelled)
        a_issue = constellation_issue(a, 6, epsilon, "a")
        if a_issue is not None:
            issues.append(a_issue)
        rejected = independence_violations(a, b_pool, (height, width), support_halo, cancelled=cancelled)
        rejected_ids = {fid for fid, _ in rejected}
        b = tuple(anchor for anchor in b_pool if anchor.feature_id not in rejected_ids)
        b_issue = constellation_issue(b, 3, epsilon, "b")
        if b_issue is not None:
            issues.append(b_issue)
        _check_cancelled(cancelled)
    except _Cancelled:
        issues.append("observation-cancelled")
        a, b = (), ()
    return StructureIdentityObservation(str(frame_key), (height, width), bounds,
        ConstellationA(tuple(a), True, constraint_kind), tuple(b), float(epsilon), support_halo, len(records),
        len(a_pool), len(b_pool), tuple(rejected), tuple(excluded), tuple(issues),
        (time.perf_counter() - began) * 1000)


def observe_structure_identity(snapshot, *, observation_bounds=None, epsilon=3.0,
                               support_halo=5, cancelled=None, constraint_kind="full-chain"):
    """Run the actual current_geometry/current_part/measured-chain channel."""
    if not isinstance(snapshot, FrameSnapshot):
        raise TypeError("structure-identity-frame-snapshot-required")
    _constraint_kind(constraint_kind)
    frame = snapshot.pixels
    if frame.ndim != 3 or frame.shape[2] < 3:
        raise ValueError("structure-identity-colour-frame-required")
    began = time.perf_counter()
    bounds = tuple(observation_bounds or (0, 0, frame.shape[1], frame.shape[0]))
    issue, records = None, ()
    actual_bounds = bounds
    try:
        _check_cancelled(cancelled)
        context = current_geometry(frame, observation_bounds=bounds)
        _check_cancelled(cancelled)
        if context is None:
            issue = "current-geometry-unavailable"
        else:
            actual_bounds = tuple(context["observationBounds"])
            current_part(frame, context, "nodes")
            _check_cancelled(cancelled)
            current_part(frame, context, "observed")
            _check_cancelled(cancelled)
            records = observed_corner_chains(frame, context)
            _check_cancelled(cancelled)
    except _Cancelled:
        issue = "observation-cancelled"
    result = partition_observed_chains(records, frame_key=snapshot.frame_key,
        frame_shape=frame.shape, observation_bounds=actual_bounds, epsilon=epsilon,
        support_halo=support_halo, cancelled=cancelled, constraint_kind=constraint_kind)
    issues = tuple(dict.fromkeys((*((issue,) if issue else ()), *result.issues)))
    return StructureIdentityObservation(result.frame_key, result.frame_shape, result.observation_bounds,
        result.a, result.b, result.epsilon, result.support_halo, result.measured_chain_count,
        result.a_pool_count, result.b_pool_count, result.rejected_b, result.excluded_measurements,
        issues, (time.perf_counter() - began) * 1000)
