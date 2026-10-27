from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

FINGERPRINT_SIZE = 64


def _bounds(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    ys, xs = np.where(mask > 0)
    if not len(xs):
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _normalized(mask: np.ndarray, size: int = FINGERPRINT_SIZE) -> np.ndarray:
    binary = (mask > 0).astype(np.uint8)
    bounds = _bounds(binary)
    if bounds is None:
        return np.zeros((size, size), np.uint8)
    x0, y0, x1, y1 = bounds
    crop = binary[y0:y1, x0:x1]
    height, width = crop.shape
    scale = min((size - 8) / max(width, 1), (size - 8) / max(height, 1))
    target = (max(1, round(width * scale)), max(1, round(height * scale)))
    resized = cv2.resize(crop, target, interpolation=cv2.INTER_AREA) >= 0.28
    canvas = np.zeros((size, size), np.uint8)
    left = (size - target[0]) // 2
    top = (size - target[1]) // 2
    canvas[top:top + target[1], left:left + target[0]] = resized
    return canvas


def _skeleton_counts(mask: np.ndarray) -> tuple[int, int]:
    current = (mask > 0).astype(np.uint8) * 255
    skeleton = np.zeros_like(current)
    element = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
    for _ in range(max(mask.shape)):
        eroded = cv2.erode(current, element)
        opened = cv2.dilate(eroded, element)
        skeleton = cv2.bitwise_or(skeleton, cv2.subtract(current, opened))
        current = eroded
        if cv2.countNonZero(current) == 0:
            break
    skeleton = (skeleton > 0).astype(np.uint8)
    neighbours = cv2.filter2D(
        skeleton, cv2.CV_16S, np.ones((3, 3), np.int16),
    ) - skeleton
    endpoints = ((skeleton > 0) & (neighbours == 1)).astype(np.uint8)
    junctions = ((skeleton > 0) & (neighbours >= 3)).astype(np.uint8)
    endpoint_groups = max(0, cv2.connectedComponents(cv2.dilate(
        endpoints, np.ones((5, 5), np.uint8),
    ))[0] - 1)
    junction_groups = max(0, cv2.connectedComponents(cv2.dilate(
        junctions, np.ones((5, 5), np.uint8),
    ))[0] - 1)
    return int(endpoint_groups), int(junction_groups)


def _fast_partial_score(reference: np.ndarray, query: np.ndarray) -> float:
    bounds = _bounds(query)
    if bounds is None or int(query.sum()) < 24:
        return 0.0
    x0, y0, x1, y1 = bounds
    query = query[y0:y1, x0:x1].astype(np.uint8)
    reference = reference.astype(np.uint8)
    best = 0.0
    # Seven geometric levels are sufficient on the 64x64 semantic raster and
    # keep all learned exemplars inside the sub-0.5-second interaction budget.
    for fraction in (0.26, 0.36, 0.48, 0.61, 0.74, 0.87, 1.0):
        scale = fraction * min(
            reference.shape[1] / max(query.shape[1], 1),
            reference.shape[0] / max(query.shape[0], 1),
        )
        width = max(3, round(query.shape[1] * scale))
        height = max(3, round(query.shape[0] * scale))
        if width > reference.shape[1] or height > reference.shape[0]:
            continue
        resized = cv2.resize(query, (width, height), interpolation=cv2.INTER_AREA)
        resized = (resized >= 0.30).astype(np.uint8)
        if int(resized.sum()) < 10:
            continue
        correlation = cv2.matchTemplate(reference, resized, cv2.TM_CCORR_NORMED)
        _minimum, _maximum, _minimum_location, location = cv2.minMaxLoc(correlation)
        x, y = location
        window = reference[y:y + height, x:x + width]
        overlap = int(np.logical_and(window, resized).sum())
        containment = overlap / max(int(resized.sum()), 1)
        query_edge = cv2.morphologyEx(
            resized, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8),
        )
        reference_edge = cv2.morphologyEx(
            window, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8),
        )
        edge_overlap = int(np.logical_and(
            query_edge,
            cv2.dilate(reference_edge, np.ones((3, 3), np.uint8)),
        ).sum())
        edge_score = edge_overlap / max(int(query_edge.sum()), 1)
        best = max(best, 0.68 * containment + 0.32 * edge_score)
    return float(best)


@dataclass(frozen=True)
class SemanticFingerprint:
    """One representation shared by source sheets and live explored maps.

    The normalized occupancy is the partial room/corridor graph rendered as a
    small raster. Endpoint and junction counts are deliberately weak evidence:
    fog exploration can add graph nodes, while a matching local structure may
    already be useful before the whole floor is visible.
    """

    occupied: np.ndarray
    expanded: np.ndarray
    aspect_ratio: float
    occupied_ratio: float
    endpoints: int
    junctions: int


@dataclass(frozen=True)
class SemanticMatch:
    score: float
    partial_score: float
    silhouette_score: float
    topology_score: float


def semantic_fingerprint(mask: np.ndarray) -> SemanticFingerprint | None:
    binary = (mask > 0).astype(np.uint8)
    bounds = _bounds(binary)
    if bounds is None or int(binary.sum()) < 80:
        return None
    x0, y0, x1, y1 = bounds
    crop = binary[y0:y1, x0:x1]
    normalized = _normalized(crop)
    expanded = cv2.dilate(normalized, np.ones((3, 3), np.uint8))
    endpoints, junctions = _skeleton_counts(normalized)
    return SemanticFingerprint(
        occupied=normalized,
        expanded=expanded,
        aspect_ratio=float((x1 - x0) / max(y1 - y0, 1)),
        occupied_ratio=float(crop.sum() / max(crop.size, 1)),
        endpoints=endpoints,
        junctions=junctions,
    )


def semantic_similarity(
    reference: SemanticFingerprint,
    query: SemanticFingerprint,
) -> SemanticMatch:
    # Either observation may be the more explored one, so partial containment
    # is evaluated in both directions. This is the important distinction from
    # the old full-screen SIFT memory comparison.
    partial = max(
        _fast_partial_score(reference.occupied, query.occupied),
        _fast_partial_score(query.occupied, reference.occupied),
    )
    intersection = int(np.logical_and(reference.expanded, query.expanded).sum())
    silhouette = float(
        2.0 * intersection
        / max(int(reference.expanded.sum() + query.expanded.sum()), 1)
    )
    endpoint_delta = abs(reference.endpoints - query.endpoints)
    junction_delta = abs(reference.junctions - query.junctions)
    topology = float(np.exp(-0.18 * endpoint_delta - 0.28 * junction_delta))
    score = 0.62 * partial + 0.33 * silhouette + 0.05 * topology
    return SemanticMatch(
        score=float(np.clip(score, 0.0, 1.0)),
        partial_score=float(partial),
        silhouette_score=silhouette,
        topology_score=topology,
    )
