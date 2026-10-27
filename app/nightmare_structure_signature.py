from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


SCHEMA = "identityv-nightmare-structure-signatures-v1"
GRID_SIZE = 64
ANALYSIS_SIZE = 256


def stable_structure_channels(image: np.ndarray) -> dict[str, np.ndarray]:
    """Extract stable architecture colors while ignoring vivid route ink.

    These channels deliberately describe geometry, not named rooms.  The
    author sheets contain route arrows that never appear in game, so saturated
    annotations must not become identity evidence.
    """
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    hue, saturation, value = cv2.split(hsv)
    neutral = (
        (saturation <= 72) & (value >= 68) & (value <= 205)
    )
    brown = (
        (hue >= 4) & (hue <= 32) & (saturation >= 22)
        & (saturation <= 175) & (value >= 68) & (value <= 205)
    )
    kernel = np.ones((3, 3), np.uint8)
    neutral_mask = cv2.morphologyEx(neutral.astype(np.uint8), cv2.MORPH_OPEN, kernel)
    brown_mask = cv2.morphologyEx(brown.astype(np.uint8), cv2.MORPH_OPEN, kernel)
    occupied = np.maximum(neutral_mask, brown_mask)
    return {"occupied": occupied, "neutral": neutral_mask, "brown": brown_mask}


def _significant_mask(mask: np.ndarray) -> tuple[np.ndarray, list[dict]]:
    count, labels, stats, centers = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    total = max(int(mask.sum()), 1)
    minimum = max(80, round(total * 0.0025))
    rows = []
    kept = np.zeros_like(mask, dtype=np.uint8)
    for index in range(1, count):
        area = int(stats[index, cv2.CC_STAT_AREA])
        if area < minimum:
            continue
        x, y, width, height = (int(value) for value in stats[index, :4])
        kept[labels == index] = 1
        rows.append({
            "bounds": [x, y, width, height],
            "center": [float(centers[index][0]), float(centers[index][1])],
            "area": area,
        })
    rows.sort(key=lambda row: row["area"], reverse=True)
    return kept, rows[:24]


def _occupied_bounds(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    ys, xs = np.where(mask > 0)
    if not len(xs):
        return None
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    return x0, y0, x1, y1


def _normalized_canvas(mask: np.ndarray, bounds: tuple[int, int, int, int], size: int) -> np.ndarray:
    x0, y0, x1, y1 = bounds
    crop = mask[y0:y1, x0:x1]
    height, width = crop.shape
    scale = min((size - 6) / max(width, 1), (size - 6) / max(height, 1))
    target_width = max(1, round(width * scale))
    target_height = max(1, round(height * scale))
    resized = cv2.resize(crop, (target_width, target_height), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((size, size), np.uint8)
    left = (size - target_width) // 2
    top = (size - target_height) // 2
    canvas[top:top + target_height, left:left + target_width] = resized >= 0.25
    return canvas


def _binary_rows(mask: np.ndarray) -> list[str]:
    return ["".join("1" if value else "0" for value in row) for row in mask.astype(bool)]


def binary_rows_to_mask(rows: list[str]) -> np.ndarray:
    if not rows:
        return np.zeros((0, 0), np.uint8)
    return np.asarray([[character == "1" for character in row] for row in rows], np.uint8)


def _morphological_skeleton(mask: np.ndarray) -> np.ndarray:
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
    return (skeleton > 0).astype(np.uint8)


def _cluster_points(point_mask: np.ndarray, limit: int = 24) -> list[list[float]]:
    expanded = cv2.dilate(point_mask.astype(np.uint8), np.ones((5, 5), np.uint8))
    count, labels, stats, centers = cv2.connectedComponentsWithStats(expanded, 8)
    rows = []
    height, width = point_mask.shape
    for index in range(1, count):
        area = int(stats[index, cv2.CC_STAT_AREA])
        if area < 2:
            continue
        rows.append((area, [float(centers[index][0] / width), float(centers[index][1] / height)]))
    rows.sort(reverse=True, key=lambda row: row[0])
    return [point for _area, point in rows[:limit]]


def _skeleton_summary(mask: np.ndarray) -> tuple[np.ndarray, dict]:
    skeleton = _morphological_skeleton(mask)
    neighbors = cv2.filter2D(skeleton, cv2.CV_16S, np.ones((3, 3), np.int16)) - skeleton
    endpoints = ((skeleton > 0) & (neighbors == 1)).astype(np.uint8)
    junctions = ((skeleton > 0) & (neighbors >= 3)).astype(np.uint8)
    return skeleton, {
        "pixelCount": int(skeleton.sum()),
        "endpoints": _cluster_points(endpoints),
        "junctions": _cluster_points(junctions),
    }


def _wide_area_regions(mask: np.ndarray, brown: np.ndarray) -> list[dict]:
    distance = cv2.distanceTransform((mask > 0).astype(np.uint8), cv2.DIST_L2, 5)
    nonzero = distance[distance > 0]
    if not nonzero.size:
        return []
    threshold = max(3.0, float(np.percentile(nonzero, 68)) * 1.05)
    cores = (distance >= threshold).astype(np.uint8)
    cores = cv2.morphologyEx(cores, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    count, labels, stats, centers = cv2.connectedComponentsWithStats(cores, 8)
    rows = []
    height, width = mask.shape
    for index in range(1, count):
        area = int(stats[index, cv2.CC_STAT_AREA])
        if area < 12:
            continue
        x, y, region_width, region_height = (int(value) for value in stats[index, :4])
        region = labels == index
        brown_ratio = float(brown[region].mean()) if region.any() else 0.0
        rows.append({
            "center": [float(centers[index][0] / width), float(centers[index][1] / height)],
            "bounds": [float(x / width), float(y / height), float(region_width / width), float(region_height / height)],
            "areaRatio": float(area / max(int(mask.sum()), 1)),
            "aspectRatio": float(region_width / max(region_height, 1)),
            "stableColor": "brown" if brown_ratio >= 0.55 else "neutral",
        })
    rows.sort(key=lambda row: row["areaRatio"], reverse=True)
    return rows[:20]


def build_structure_signature(
    image: np.ndarray,
    *,
    map_id: str,
    map_name: str,
    floor: str,
    direction: str,
    door_count: int,
    shape: str,
    exit_anchor: dict | None = None,
) -> tuple[dict, dict[str, np.ndarray]]:
    channels = stable_structure_channels(image)
    occupied, components = _significant_mask(channels["occupied"])
    bounds = _occupied_bounds(occupied)
    if bounds is None:
        raise ValueError(f"{map_id}/{floor}: no stable map structure")
    normalized = {
        key: _normalized_canvas(value, bounds, ANALYSIS_SIZE)
        for key, value in channels.items()
    }
    normalized["occupied"] = _normalized_canvas(occupied, bounds, ANALYSIS_SIZE)
    grid = {
        key: _normalized_canvas(value, bounds, GRID_SIZE)
        for key, value in channels.items()
    }
    grid["occupied"] = _normalized_canvas(occupied, bounds, GRID_SIZE)
    skeleton, skeleton_summary = _skeleton_summary(normalized["occupied"])
    regions = _wide_area_regions(
        normalized["occupied"], normalized["brown"],
    )
    x0, y0, x1, y1 = bounds
    source_height, source_width = image.shape[:2]
    normalized_anchor = None
    if exit_anchor:
        center = exit_anchor.get("center")
        if isinstance(center, list) and len(center) == 2:
            normalized_anchor = {
                **exit_anchor,
                "normalizedInStructure": [
                    float((float(center[0]) - x0) / max(x1 - x0, 1)),
                    float((float(center[1]) - y0) / max(y1 - y0, 1)),
                ],
            }
    signature = {
        "mapId": map_id,
        "mapName": map_name,
        "floor": floor,
        "manualTaxonomy": {
            "direction": direction,
            "doorCount": int(door_count),
            "shape": shape,
        },
        "sourceSize": [source_width, source_height],
        "occupiedBounds": [x0, y0, x1 - x0, y1 - y0],
        "aspectRatio": float((x1 - x0) / max(y1 - y0, 1)),
        "occupiedRatio": float(occupied.sum() / max(source_width * source_height, 1)),
        "stableColorRatios": {
            "neutral": float((channels["neutral"] & occupied).sum() / max(int(occupied.sum()), 1)),
            "brown": float((channels["brown"] & occupied).sum() / max(int(occupied.sum()), 1)),
        },
        "significantComponents": components,
        "wideAreaCandidates": regions,
        "skeleton": skeleton_summary,
        "exitAnchor": normalized_anchor,
        "grids": {
            "size": GRID_SIZE,
            "occupied": _binary_rows(grid["occupied"]),
            "neutral": _binary_rows(grid["neutral"]),
            "brown": _binary_rows(grid["brown"]),
        },
        "semanticLimits": {
            "wideAreaCandidatesAreNamedRooms": False,
            "dynamicGameHighlightUsedAsIdentity": False,
            "routeInkUsedAsIdentity": False,
        },
    }
    artifacts = {
        **channels,
        "significant": occupied,
        "normalizedOccupied": normalized["occupied"],
        "normalizedBrown": normalized["brown"],
        "skeleton": skeleton,
    }
    return signature, artifacts


def _coarse_partial_score(reference: np.ndarray, query: np.ndarray) -> float:
    query_bounds = _occupied_bounds(query)
    if query_bounds is None or int(query.sum()) < 40:
        return 0.0
    qx0, qy0, qx1, qy1 = query_bounds
    query = query[qy0:qy1, qx0:qx1].astype(np.uint8)
    ref = reference.astype(np.uint8)
    best = 0.0
    for fraction in np.linspace(0.22, 1.0, 14):
        scale = fraction * min(
            ref.shape[1] / max(query.shape[1], 1),
            ref.shape[0] / max(query.shape[0], 1),
        )
        width = max(3, round(query.shape[1] * scale))
        height = max(3, round(query.shape[0] * scale))
        if width > ref.shape[1] or height > ref.shape[0]:
            continue
        resized = cv2.resize(query, (width, height), interpolation=cv2.INTER_AREA) >= 0.30
        resized = resized.astype(np.uint8)
        if int(resized.sum()) < 12:
            continue
        correlation = cv2.matchTemplate(ref, resized, cv2.TM_CCORR_NORMED)
        _minimum, maximum, _minimum_location, location = cv2.minMaxLoc(correlation)
        x, y = location
        window = ref[y:y + height, x:x + width]
        overlap = int((window & resized).sum())
        containment = overlap / max(int(resized.sum()), 1)
        query_edge = cv2.morphologyEx(resized, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
        reference_edge = cv2.morphologyEx(window, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
        edge_overlap = int((query_edge & cv2.dilate(reference_edge, np.ones((3, 3), np.uint8))).sum())
        edge_score = edge_overlap / max(int(query_edge.sum()), 1)
        best = max(best, 0.68 * containment + 0.32 * edge_score)
    return float(best)


@dataclass(frozen=True)
class StructureScore:
    map_id: str
    floor: str
    score: float


class NightmareStructureIndex:
    """Small offline index used only as an advisory candidate-ranking hint."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if raw.get("schema") != SCHEMA:
            raise ValueError("unsupported nightmare structure index")
        self.entries = raw.get("entries", [])
        self._grids = {
            (entry["mapId"], str(entry["floor"])): binary_rows_to_mask(entry["grids"]["occupied"])
            for entry in self.entries
        }

    def rank_mask(self, query_mask: np.ndarray, floor: str) -> list[StructureScore]:
        query = _normalized_canvas(
            (query_mask > 0).astype(np.uint8),
            _occupied_bounds(query_mask) or (0, 0, query_mask.shape[1], query_mask.shape[0]),
            GRID_SIZE,
        )
        scores = [
            StructureScore(map_id, candidate_floor, _coarse_partial_score(reference, query))
            for (map_id, candidate_floor), reference in self._grids.items()
            if candidate_floor == floor
        ]
        return sorted(scores, key=lambda row: row.score, reverse=True)

