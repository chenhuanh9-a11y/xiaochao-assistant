from __future__ import annotations

"""Fail-closed helpers for the offline voice-navigation feasibility probe.

This module deliberately has no audio playback and no live-assistant hooks.
It answers three separate questions:

1. Is the yellow player marker distinct in the current map frame?
2. Can the current source-to-client fit project it into route-sheet space?
3. Is there a reviewed navigation cue for that route position?

Only a positive answer to all three may produce an authorized cue.  Candidate
coordinates are still returned for diagnostics, but never promoted to speech.
"""

import json
import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from nightmare_candidate import visible_map_component


def _contour_circularity(mask: np.ndarray) -> float:
    contours, _hierarchy = cv2.findContours(
        mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE,
    )
    if not contours:
        return 0.0
    contour = max(contours, key=cv2.contourArea)
    perimeter = float(cv2.arcLength(contour, True))
    if perimeter <= 1e-6:
        return 0.0
    return float(4.0 * math.pi * cv2.contourArea(contour) / (perimeter * perimeter))


def _heading_from_halo(
    hsv: np.ndarray,
    center: tuple[float, float],
    scale: float,
) -> dict:
    """Estimate the translucent direction wedge around the yellow badge.

    The result is advisory.  A room's illumination can bias the pale-pixel
    sectors, so a direction is reliable only when the winning sector is both
    brighter than the median sector and brighter than its opposite sector.
    """

    cx, cy = center
    radius_inner = max(8.0, 12.0 * scale)
    radius_outer = max(radius_inner + 8.0, 42.0 * scale)
    pad = int(math.ceil(radius_outer + 2.0))
    x0 = max(0, int(math.floor(cx)) - pad)
    y0 = max(0, int(math.floor(cy)) - pad)
    x1 = min(hsv.shape[1], int(math.ceil(cx)) + pad + 1)
    y1 = min(hsv.shape[0], int(math.ceil(cy)) + pad + 1)
    if x1 <= x0 or y1 <= y0:
        return {"reliable": False, "degrees": None, "confidence": 0.0}

    patch = hsv[y0:y1, x0:x1].astype(np.float32)
    yy, xx = np.indices(patch.shape[:2], dtype=np.float32)
    dx = xx + x0 - float(cx)
    dy = yy + y0 - float(cy)
    radius = np.sqrt(dx * dx + dy * dy)
    angles = (np.degrees(np.arctan2(dy, dx)) + 360.0) % 360.0
    pale = patch[:, :, 2] * (1.0 - patch[:, :, 1] / 255.0)
    annulus = (radius >= radius_inner) & (radius <= radius_outer)

    sector_means = []
    sector_count = 16
    sector_width = 360.0 / sector_count
    for index in range(sector_count):
        center_angle = index * sector_width
        delta = np.abs((angles - center_angle + 180.0) % 360.0 - 180.0)
        selected = annulus & (delta <= sector_width / 2.0)
        sector_means.append(float(np.mean(pale[selected])) if np.any(selected) else 0.0)

    best_index = int(np.argmax(sector_means))
    best = float(sector_means[best_index])
    median = float(np.median(sector_means))
    opposite = float(sector_means[(best_index + sector_count // 2) % sector_count])
    contrast = min(best - median, best - opposite)
    confidence = float(np.clip((contrast - 5.0) / 20.0, 0.0, 1.0))
    return {
        "reliable": bool(contrast >= 8.0),
        "degrees": round(best_index * sector_width, 1),
        "confidence": round(confidence, 3),
        "contrast": round(contrast, 2),
        "sectorMeans": [round(value, 2) for value in sector_means],
    }


def detect_player_marker(
    frame: np.ndarray,
    map_bounds: tuple[int, int, int, int] | None = None,
) -> dict:
    """Find the yellow numbered player badge inside the visible map island."""

    if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.size == 0:
        return {"success": False, "reason": "invalid-frame", "candidates": []}
    if map_bounds is None:
        visible = visible_map_component(frame)
        if visible is None:
            return {"success": False, "reason": "visible-map-missing", "candidates": []}
        _patch, map_bounds, _component = visible
    x0, y0, x1, y1 = (int(value) for value in map_bounds)
    if x1 <= x0 or y1 <= y0:
        return {"success": False, "reason": "invalid-map-bounds", "candidates": []}

    hsv = cv2.cvtColor(frame[:, :, :3], cv2.COLOR_BGR2HSV)
    yellow = cv2.inRange(
        hsv,
        np.array([16, 100, 145], dtype=np.uint8),
        np.array([42, 255, 255], dtype=np.uint8),
    )
    count, labels, stats, centers = cv2.connectedComponentsWithStats(yellow, 8)
    scale = max(0.55, float(frame.shape[0]) / 1281.0)
    candidates = []
    for index in range(1, count):
        x, y, width, height, area = (int(value) for value in stats[index])
        cx, cy = (float(value) for value in centers[index])
        if not (x0 - 4 * scale <= cx <= x1 + 4 * scale and y0 - 4 * scale <= cy <= y1 + 4 * scale):
            continue
        if not (
            7.0 * scale <= width <= 30.0 * scale
            and 9.0 * scale <= height <= 34.0 * scale
            and 45.0 * scale * scale <= area <= 430.0 * scale * scale
        ):
            continue
        aspect = float(width / max(height, 1))
        if not 0.52 <= aspect <= 1.30:
            continue
        component_mask = labels[y:y + height, x:x + width] == index
        circularity = _contour_circularity(component_mask)
        if circularity < 0.48:
            continue
        target_area = 190.0 * scale * scale
        area_score = math.exp(-abs(math.log(max(area, 1) / target_area)))
        aspect_score = max(0.0, 1.0 - abs(aspect - 0.82) / 0.55)
        score = float(np.clip(
            0.50 * min(circularity / 0.78, 1.0)
            + 0.30 * area_score
            + 0.20 * aspect_score,
            0.0, 1.0,
        ))
        heading = _heading_from_halo(hsv, (cx, cy), scale)
        candidates.append({
            "centerClient": [round(cx, 2), round(cy, 2)],
            "boundsClient": [x, y, x + width, y + height],
            "area": area,
            "aspect": round(aspect, 3),
            "circularity": round(circularity, 3),
            "score": round(score, 3),
            "heading": heading,
        })

    candidates.sort(key=lambda row: float(row["score"]), reverse=True)
    if not candidates:
        return {
            "success": False,
            "reason": "player-marker-not-found",
            "mapBoundsClient": [x0, y0, x1, y1],
            "candidates": [],
        }
    best = candidates[0]
    second_score = float(candidates[1]["score"]) if len(candidates) > 1 else 0.0
    margin = float(best["score"]) - second_score
    success = bool(float(best["score"]) >= 0.72 and margin >= 0.10)
    return {
        "success": success,
        "reason": "player-marker-unique" if success else "player-marker-ambiguous",
        "mapBoundsClient": [x0, y0, x1, y1],
        "centerClient": best["centerClient"],
        "boundsClient": best["boundsClient"],
        "score": best["score"],
        "distinctMargin": round(margin, 3),
        "heading": best["heading"],
        "candidates": candidates[:8],
    }


def client_to_source(
    point: list[float] | tuple[float, float],
    matrix_source_to_client: Any,
) -> list[float] | None:
    matrix = np.asarray(matrix_source_to_client, dtype=np.float64)
    query = np.asarray([float(point[0]), float(point[1]), 1.0], dtype=np.float64)
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all():
        return None
    try:
        inverse = np.linalg.inv(matrix)
    except np.linalg.LinAlgError:
        return None
    projected = inverse @ query
    if not np.isfinite(projected).all() or abs(float(projected[2])) <= 1e-9:
        return None
    projected = projected[:2] / projected[2]
    return [round(float(projected[0]), 2), round(float(projected[1]), 2)]


def nearest_landmark(point: list[float] | None, floor_data: dict | None) -> dict | None:
    if point is None or not isinstance(floor_data, dict):
        return None
    px, py = (float(value) for value in point)
    rows = []
    for landmark in floor_data.get("landmarks", []):
        center = landmark.get("centerSource")
        radius = float(landmark.get("radiusSource", 0.0))
        if not isinstance(center, list) or len(center) != 2 or radius <= 0.0:
            continue
        distance = float(math.hypot(px - float(center[0]), py - float(center[1])))
        rows.append((distance / radius, distance, landmark))
    if not rows:
        return None
    normalized, distance, landmark = min(rows, key=lambda row: row[0])
    if normalized > 1.0:
        return None
    return {
        "id": landmark.get("id"),
        "name": landmark.get("name"),
        "distanceSource": round(distance, 2),
        "normalizedDistance": round(normalized, 3),
        "cueDraft": landmark.get("cueDraft"),
    }


def navigation_decision(
    marker: dict,
    fit: dict,
    floor_data: dict,
) -> dict:
    """Return a diagnostic candidate and, only when reviewed, an authorized cue."""

    source_point = None
    if marker.get("centerClient") is not None and fit.get("matrixSourceToClient") is not None:
        source_point = client_to_source(marker["centerClient"], fit["matrixSourceToClient"])
    landmark = nearest_landmark(source_point, floor_data)
    base = {
        "sourcePointCandidate": source_point,
        "landmarkCandidate": landmark,
        "authorized": False,
        "cue": None,
    }
    if not marker.get("success"):
        return {**base, "reason": marker.get("reason", "player-marker-unverified")}
    if not fit.get("success"):
        return {**base, "reason": f"alignment-unverified:{fit.get('reason', 'unknown')}"}
    if source_point is None:
        return {**base, "reason": "source-projection-invalid"}
    if not floor_data.get("runtimeAllowed"):
        return {**base, "reason": "route-graph-unreviewed"}
    if landmark is None or not landmark.get("cueDraft"):
        return {**base, "reason": "navigation-landmark-unresolved"}
    return {
        **base,
        "authorized": True,
        "reason": "reviewed-navigation-cue",
        "cue": str(landmark["cueDraft"]),
    }


def load_navigation_catalog(path: Path) -> dict:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if raw.get("schema") != "identityv-voice-navigation-prototype-v1":
        raise ValueError("unsupported voice navigation prototype schema")
    return raw

