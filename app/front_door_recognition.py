from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from nightmare_candidate import NightmarePack, imread_unicode, map_color_mask


SOURCE_ROOT = Path(__file__).resolve().parents[1]
ROOT = Path(getattr(sys, "_MEIPASS", SOURCE_ROOT))
DEFAULT_IDENTITIES = ROOT / "references" / "nightmare-front-door-identities.json"
CANONICAL_SIZE = (200, 160)
STAGE_TOP = {
    "door-anchor-only": 128,
    "entry-room-outline": 96,
    "front-left-right-exits": 64,
    "first-ring-topology": 32,
    "second-ring-topology": 0,
}
STAGE_ORDER = tuple(STAGE_TOP)


@dataclass(frozen=True)
class FrontDoorReference:
    map_id: str
    map_name: str
    identity_id: str
    mask: np.ndarray
    edge: np.ndarray
    anchor_size: float
    variants: tuple[tuple[float, float, float, np.ndarray, np.ndarray], ...]


def _edge(mask: np.ndarray) -> np.ndarray:
    binary = (mask > 0).astype(np.uint8)
    return cv2.morphologyEx(binary, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))


def canonical_front_door_mask(
    image: np.ndarray,
    center: tuple[float, float],
    anchor_size: float,
    *,
    live: bool,
    include_bounds: tuple[int, int, int, int] | None = None,
) -> np.ndarray:
    """Normalize the terrain above a bottom entrance without using map rotation."""
    if image is None or image.size == 0 or anchor_size <= 0:
        return np.zeros((CANONICAL_SIZE[1], CANONICAL_SIZE[0]), np.uint8)
    height, width = image.shape[:2]
    cx, cy = (float(value) for value in center)
    x0 = round(cx - anchor_size * 10.0)
    x1 = round(cx + anchor_size * 10.0)
    y0 = round(cy - anchor_size * 15.0)
    y1 = round(cy + anchor_size * 1.0)
    source_mask = map_color_mask(image, live=live).astype(np.uint8)
    if include_bounds is not None:
        bx0, by0, bx1, by1 = (int(value) for value in include_bounds)
        bounded = np.zeros_like(source_mask)
        bx0, by0 = max(0, bx0), max(0, by0)
        bx1, by1 = min(width, bx1), min(height, by1)
        if bx1 > bx0 and by1 > by0:
            bounded[by0:by1, bx0:bx1] = source_mask[by0:by1, bx0:bx1]
        source_mask = bounded
    # Crop with explicit zero padding so an entrance close to a source edge
    # retains the same coordinate frame as every other map.
    crop = np.zeros((max(1, y1 - y0), max(1, x1 - x0)), np.uint8)
    sx0, sy0 = max(0, x0), max(0, y0)
    sx1, sy1 = min(width, x1), min(height, y1)
    if sx1 > sx0 and sy1 > sy0:
        crop[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0] = source_mask[sy0:sy1, sx0:sx1]
    normalized = cv2.resize(crop, CANONICAL_SIZE, interpolation=cv2.INTER_AREA)
    normalized = (normalized >= 0.28).astype(np.uint8)
    normalized = cv2.morphologyEx(
        normalized, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8),
    )
    return normalized


def visible_front_door_stage(mask: np.ndarray) -> str:
    ys = np.flatnonzero(np.any(mask > 0, axis=1))
    if ys.size == 0:
        return "door-anchor-only"
    top = int(ys.min())
    # Exploration is not always vertical. A long side branch can reveal more
    # usable topology than an entry-room frame while its highest pixel remains
    # below the old row-only threshold. Promote only a dense field that spans
    # most of the canonical width and a substantial part of its height; a bare
    # entrance room remains too small to satisfy all three ratios.
    points_y, points_x = np.where(mask > 0)
    if points_x.size:
        occupied_ratio = float(points_x.size / max(mask.size, 1))
        horizontal_span = float(
            points_x.max() - points_x.min() + 1
        ) / max(mask.shape[1], 1)
        vertical_span = float(
            points_y.max() - points_y.min() + 1
        ) / max(mask.shape[0], 1)
        if (
            top > STAGE_TOP["entry-room-outline"]
            and occupied_ratio >= 0.09
            and horizontal_span >= 0.55
            and vertical_span >= 0.30
        ):
            return "entry-room-outline"
    for stage in reversed(STAGE_ORDER):
        if top <= STAGE_TOP[stage]:
            return stage
    return "door-anchor-only"


def stage_mask(mask: np.ndarray, stage: str) -> np.ndarray:
    clipped = np.zeros_like(mask)
    top = int(STAGE_TOP.get(stage, STAGE_TOP["door-anchor-only"]))
    clipped[top:] = mask[top:]
    return clipped


def perturb_mask(mask: np.ndarray, scale: float, dx: float, dy: float) -> np.ndarray:
    height, width = mask.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2.0, height - 1.0), 0.0, float(scale))
    matrix[0, 2] += float(dx)
    matrix[1, 2] += float(dy)
    return cv2.warpAffine(
        mask, matrix, (width, height), flags=cv2.INTER_NEAREST,
        borderMode=cv2.BORDER_CONSTANT, borderValue=0,
    )


class NightmareFrontDoorRecognizer:
    """Progressive front-entry shortlist; never authorizes route display."""

    def __init__(
        self,
        pack: NightmarePack,
        identity_path: Path = DEFAULT_IDENTITIES,
    ) -> None:
        self.pack = pack
        payload = json.loads(Path(identity_path).read_text(encoding="utf-8"))
        if payload.get("schema") != "identityv-nightmare-front-door-identities-v1":
            raise ValueError("front-door-identity-schema-unsupported")
        assumptions = payload.get("assumptions") or {}
        if (
            assumptions.get("mapOrientationIsIdentityFeature") is not False
            or assumptions.get("allowedExitSides") != ["front", "left", "right"]
            or assumptions.get("diagonalExitAllowed") is not False
        ):
            raise ValueError("front-door-identity-policy-invalid")
        rows = payload.get("maps") or []
        if len(rows) != 13 or {row.get("mapId") for row in rows} != set(pack.by_id):
            raise ValueError("front-door-identity-coverage-invalid")
        self.identity_rows = {str(row["mapId"]): row for row in rows}
        self.references: dict[str, FrontDoorReference] = {}
        for map_id, row in self.identity_rows.items():
            item = pack.by_id[map_id]
            image = imread_unicode(item.layers["1"])
            anchor = row["frontDoorAnchor"]
            size = float(np.mean(anchor.get("size") or [0.0, 0.0]))
            mask = canonical_front_door_mask(
                image, tuple(anchor["center"]), size, live=False,
            )
            # The author sheets are exported at several different scales and
            # the live map UI does not preserve the icon-to-wall ratio exactly.
            # Keep the entrance icon as the fixed origin and precompute a small
            # similarity neighbourhood. This is shadow-only, so it cannot move
            # or reveal an overlay by itself.
            variants = []
            for scale in (0.72, 0.82, 0.92, 1.0, 1.08, 1.18, 1.30):
                for dx in (-8.0, 0.0, 8.0):
                    for dy in (-6.0, 0.0, 6.0):
                        variant = perturb_mask(mask, scale, dx, dy)
                        variants.append((scale, dx, dy, variant, _edge(variant)))
            self.references[map_id] = FrontDoorReference(
                map_id=map_id,
                map_name=item.name,
                identity_id=str(row["frontDoorId"]),
                mask=mask,
                edge=_edge(mask),
                anchor_size=size,
                variants=tuple(variants),
            )

    @staticmethod
    def _score_variant(
        query: np.ndarray,
        query_edge: np.ndarray,
        reference_mask: np.ndarray,
        reference_edge: np.ndarray,
    ) -> dict:
        query = (query > 0).astype(np.uint8)
        query_pixels = int(query.sum())
        edge_pixels = int(query_edge.sum())
        if query_pixels < 35 or edge_pixels < 18:
            return {
                "score": 0.0, "containment": 0.0,
                "edgeWithinTwo": 0.0, "edgeMedian": 999.0,
            }
        reference_wide = cv2.dilate(reference_mask, np.ones((5, 5), np.uint8))
        containment = float(
            np.logical_and(query > 0, reference_wide > 0).sum()
            / max(query_pixels, 1)
        )
        distance = cv2.distanceTransform(
            (1 - reference_edge).astype(np.uint8), cv2.DIST_L2, 3,
        )
        residual = distance[query_edge > 0]
        edge_median = float(np.median(residual)) if residual.size else 999.0
        edge_within_two = float(np.mean(residual <= 2.2)) if residual.size else 0.0
        precision = math.exp(-edge_median / 3.0)
        score = 100.0 * (
            0.48 * edge_within_two + 0.34 * containment + 0.18 * precision
        )
        return {
            "score": float(score),
            "containment": containment,
            "edgeWithinTwo": edge_within_two,
            "edgeMedian": edge_median,
        }

    @classmethod
    def _score(cls, query: np.ndarray, reference: FrontDoorReference) -> dict:
        query = (query > 0).astype(np.uint8)
        query_edge = _edge(query)
        best: dict | None = None
        for scale, dx, dy, mask, edge in reference.variants:
            row = cls._score_variant(query, query_edge, mask, edge)
            if best is None or float(row["score"]) > float(best["score"]):
                best = {
                    **row,
                    "referenceScale": float(scale),
                    "referenceDx": float(dx),
                    "referenceDy": float(dy),
                }
        return best or {
            "score": 0.0, "containment": 0.0,
            "edgeWithinTwo": 0.0, "edgeMedian": 999.0,
            "referenceScale": 1.0, "referenceDx": 0.0, "referenceDy": 0.0,
        }

    def rank_normalized_mask(
        self,
        query: np.ndarray,
        *,
        stage: str | None = None,
        limit: int = 13,
    ) -> dict:
        resolved_stage = stage or visible_front_door_stage(query)
        query_stage = stage_mask(query, resolved_stage)
        rows = []
        for reference in self.references.values():
            score = self._score(query_stage, reference)
            rows.append({
                "mapId": reference.map_id,
                "mapName": reference.map_name,
                "frontDoorId": reference.identity_id,
                **score,
            })
        rows.sort(key=lambda row: row["score"], reverse=True)
        top = rows[0] if rows else None
        second = rows[1] if len(rows) > 1 else None
        margin = float((top or {}).get("score", 0.0) - (second or {}).get("score", 0.0))
        # Door-only evidence is deliberately never high confidence. Until the
        # 13 topology labels are reviewed, even later stages remain shadow-only.
        strong = bool(
            resolved_stage != "door-anchor-only"
            and top is not None
            and float(top["score"]) >= 80.0
            and margin >= 10.0
        )
        return {
            "success": bool(rows),
            "reason": "front-door-shadow-ranked" if rows else "front-door-no-structure",
            "stage": resolved_stage,
            "candidateMapId": (top or {}).get("mapId"),
            "score": float((top or {}).get("score", 0.0)),
            "margin": margin,
            "strongSuggestion": strong,
            "displayAllowed": False,
            "runtimePolicy": "shadow-ranking-only-until-reviewed",
            "candidates": rows[: max(1, int(limit))],
        }

    def rank_from_anchor(
        self,
        frame: np.ndarray,
        center: tuple[float, float],
        anchor_size: float,
        *,
        map_bounds: tuple[int, int, int, int] | None = None,
        limit: int = 13,
    ) -> dict:
        query = canonical_front_door_mask(
            frame, center, anchor_size, live=True, include_bounds=map_bounds,
        )
        result = self.rank_normalized_mask(query, limit=limit)
        return {
            **result,
            "anchor": {
                "center": [float(center[0]), float(center[1])],
                "size": float(anchor_size),
                "screenSide": "bottom",
            },
        }

    def reference_query(
        self,
        map_id: str,
        stage: str,
        *,
        scale: float = 1.0,
        dx: float = 0.0,
        dy: float = 0.0,
    ) -> np.ndarray:
        reference = self.references[map_id]
        return perturb_mask(stage_mask(reference.mask, stage), scale, dx, dy)
