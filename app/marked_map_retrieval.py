from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from side_door_recognition import (
    anchor_appearance,
    anchor_descriptor,
    appearance_ok,
    central_descriptor,
    descriptor_containment,
    descriptor_similarity,
    map_color_mask,
    topology,
    topology_similarity,
)


# The source poster is ordered exactly as the player-facing table: right, left,
# south, then north.  It contains 28 rows and omits the distinct map-09-2 and
# the provisionally named map-29; the local side-door source index covers all
# 30 structural classes.
MARKED_ROWS = (
    ("map-11", "右-左上右下门", "右"),
    ("map-12", "右-双L门", "右"),
    ("map-13", "右-锤子门", "右"),
    ("map-14", "右-骑士门", "右"),
    ("map-15", "右-L门", "右"),
    ("map-10", "右-三L门", "右"),
    ("map-03", "左-Y门", "左"),
    ("map-05", "左-锤子门", "左"),
    ("map-06", "左-倒T门", "左"),
    ("map-04", "左-对角门", "左"),
    ("map-07", "左-音叉门", "左"),
    ("map-02", "左-罐子门", "左"),
    ("map-01", "左-对T门", "左"),
    ("map-08", "左-Y青蛙门", "左"),
    ("map-09", "左-锤灯笼门", "左"),
    ("map-18", "南-三缺一门", "南"),
    ("map-19", "南-十字门", "南"),
    ("map-17", "南-L门", "南"),
    ("map-16", "南-红门", "南"),
    ("map-20", "南-orz门", "南"),
    ("map-23", "北-1门", "北"),
    ("map-24", "北-4门", "北"),
    ("map-25", "北-红门", "北"),
    ("map-27", "北-凹门", "北"),
    ("map-26", "北-T门", "北"),
    ("map-28", "北-红对角门", "北"),
    ("map-21", "北-1沙发门", "北"),
    ("map-22", "北-4安全门", "北"),
)

# White separators measured in the original 5341 x 28217 attachment.  Keeping
# the separators in the checked-in index makes the extraction deterministic;
# coordinates are scaled if the same poster is resized without being cropped.
ROW_SEPARATORS = (
    460, 1297, 2137, 2981, 3831, 4674, 5519, 6363, 7204, 8048,
    8890, 9744, 10589, 11444, 12305, 13144, 13998, 14854, 15689,
    16528, 17382, 18238, 19091, 19945, 20801, 21643, 22499, 23334,
    24173,
)
SOURCE_SIZE = (5341, 28217)
ENTRANCE_X = (1970, 2810)
SPRITE_SIZE = (5880, 3360)
SPRITE_CELL = 840
DENSE_CONFIGS = ((16, 0.70), (32, 0.35), (20, 0.70))


@dataclass
class MarkedReference:
    map_id: str
    map_name: str
    entrance_direction: str
    context: np.ndarray
    full_context: np.ndarray
    context_topology: dict
    dense_contexts: tuple[np.ndarray, ...]
    source_anchor_score: float


def dense_anchor_descriptor(
    mask: np.ndarray,
    anchor: dict,
    radius: int,
    step_ratio: float,
    integral: np.ndarray | None = None,
) -> np.ndarray:
    result = np.zeros((radius * 2 + 1, radius * 2 + 1), np.uint8)
    center_x = anchor["x"] + anchor["width"] / 2
    center_y = anchor["y"] + anchor["height"] / 2
    step = max(1.2, anchor["width"] * step_ratio)
    sample_radius = max(1, round(anchor["width"] * step_ratio * 0.42))
    height, width = mask.shape
    summed = integral if integral is not None else cv2.integral(mask)
    for gy in range(-radius, radius + 1):
        for gx in range(-radius, radius + 1):
            sx = round(center_x + gx * step)
            sy = round(center_y + gy * step)
            x0, x1 = max(0, sx - sample_radius), min(width, sx + sample_radius + 1)
            y0, y1 = max(0, sy - sample_radius), min(height, sy + sample_radius + 1)
            if x1 > x0 and y1 > y0:
                total = summed[y1, x1] - summed[y0, x1] - summed[y1, x0] + summed[y0, x0]
                result[gy + radius, gx + radius] = round(float(total) / ((x1 - x0) * (y1 - y0)))
    return result


def _read_image(path: Path) -> np.ndarray:
    data = np.fromfile(path, np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None
    if image is None:
        raise ValueError(f"无法读取标注入口索引：{path}")
    return image


def _direction_from_topology(value: dict | None) -> str:
    if not value:
        return ""
    return {
        "right": "左",
        "left": "右",
        "up": "南",
        "down": "北",
    }.get(value.get("approach"), "")


class MarkedMapRetriever:
    """Retrieve a map from the poster's 28 entrance thumbnails.

    This is deliberately independent from the directory taxonomy.  The caller
    can therefore require agreement between the two evidence sources instead
    of accidentally counting one label twice.
    """

    def __init__(self, poster_path: Path, exit_icon: np.ndarray) -> None:
        self.poster_path = poster_path
        self.exit_icon = exit_icon
        poster = _read_image(poster_path)
        height, width = poster.shape[:2]
        if (width, height) == SPRITE_SIZE:
            crops = [
                poster[
                    (index // 7) * SPRITE_CELL:(index // 7 + 1) * SPRITE_CELL,
                    (index % 7) * SPRITE_CELL:(index % 7 + 1) * SPRITE_CELL,
                ]
                for index in range(len(MARKED_ROWS))
            ]
        else:
            sx, sy = width / SOURCE_SIZE[0], height / SOURCE_SIZE[1]
            if abs(sx - sy) > 0.025 or width < 1000 or height < 5000:
                raise ValueError(f"标注入口索引尺寸异常：{width}x{height}")
            x0, x1 = round(ENTRANCE_X[0] * sx), round(ENTRANCE_X[1] * sx)
            separators = [round(value * sy) for value in ROW_SEPARATORS]
            margin = max(2, round(12 * sy))
            crops = [
                poster[separators[index] + margin:separators[index + 1] - margin, x0:x1]
                for index in range(len(MARKED_ROWS))
            ]
        self.references: list[MarkedReference] = []
        for index, (map_id, map_name, direction) in enumerate(MARKED_ROWS):
            crop = crops[index]
            anchor = self._find_reference_anchor(crop)
            if anchor is None:
                raise ValueError(f"标注入口模板缺少可靠出口图标：{map_id}")
            mask = map_color_mask(crop, minimum_brightness=50, adaptive=True)
            context = anchor_descriptor(mask, anchor, 9)
            full_context = anchor_descriptor(mask, anchor, 24)
            dense_contexts = tuple(
                dense_anchor_descriptor(mask, anchor, radius, step_ratio)
                for radius, step_ratio in DENSE_CONFIGS
            )
            context_topology = topology(context)
            if not context_topology:
                raise ValueError(f"标注入口模板拓扑不足：{map_id}")
            detected_direction = _direction_from_topology(context_topology)
            if detected_direction != direction:
                # Some poster thumbnails have a diagonal first segment.  The
                # human direction label remains authoritative, but the mismatch
                # is retained in diagnostics through context_topology.
                context_topology = {**context_topology, "markedDirection": direction}
            self.references.append(MarkedReference(
                map_id, map_name, direction, context, full_context,
                context_topology, dense_contexts, float(anchor["score"]),
            ))

    def _find_reference_anchor(self, frame: np.ndarray) -> dict | None:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        candidates: list[dict] = []
        # The poster's thumbnails are larger than the runtime exit-icon source.
        for scale in (0.70, 0.90, 1.0, 1.1, 1.3, 1.5, 1.7, 2.0):
            width = max(8, round(self.exit_icon.shape[1] * scale))
            height = max(7, round(self.exit_icon.shape[0] * scale))
            template = cv2.resize(self.exit_icon, (width, height), interpolation=cv2.INTER_AREA)
            result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
            for _ in range(5):
                _, score, _, point = cv2.minMaxLoc(result)
                x, y = point
                candidate = {
                    "score": float(score), "x": int(x), "y": int(y),
                    "width": width, "height": height,
                }
                candidate["appearance"] = anchor_appearance(frame, candidate)
                if score >= 0.25 and appearance_ok(candidate["appearance"], relaxed=True):
                    candidates.append(candidate)
                result[
                    max(0, y - height):min(result.shape[0], y + height * 2),
                    max(0, x - width):min(result.shape[1], x + width * 2),
                ] = -1
        return max(candidates, key=lambda item: item["score"], default=None)

    def recognize(
        self,
        frame_bgr: np.ndarray,
        query_anchors: list[dict],
        map_mask: np.ndarray | None = None,
        map_integral: np.ndarray | None = None,
    ) -> dict:
        if not query_anchors:
            return {"success": False, "reason": "marked-exit-anchor-not-found", "candidates": []}
        # Use the same permissive brightness floor as the poster build.  The
        # live map is often dimmed by unexplored fog, while the topology and
        # direction gates prevent bright UI fragments from becoming matches.
        mask = map_mask if map_mask is not None else map_color_mask(
            frame_bgr, minimum_brightness=50, adaptive=True
        )
        integral = map_integral if map_integral is not None else cv2.integral(mask)
        by_map: dict[str, dict] = {}
        inferred_directions: set[str] = set()
        for anchor in query_anchors[:8]:
            context = anchor_descriptor(mask, anchor, 9, integral)
            full_context = anchor_descriptor(mask, anchor, 24, integral)
            query_dense_contexts = tuple(
                dense_anchor_descriptor(mask, anchor, radius, step_ratio, integral)
                for radius, step_ratio in DENSE_CONFIGS
            )
            query_topology = topology(context)
            direction = _direction_from_topology(query_topology)
            if not query_topology or not direction:
                continue
            inferred_directions.add(direction)
            for reference in self.references:
                local = descriptor_similarity(
                    central_descriptor(context), central_descriptor(reference.context)
                )
                topology_score = topology_similarity(query_topology, reference.context_topology)
                query_in_reference = descriptor_containment(full_context, reference.full_context)
                reference_in_query = descriptor_containment(reference.full_context, full_context)
                strength_agreement = 1.0 - min(
                    1.0,
                    abs(float(query_topology.get("rayApproachStrength", 0.0)) -
                        float(reference.context_topology.get("rayApproachStrength", 0.0))),
                )
                legacy_score = (
                    local * 0.42 + topology_score * 0.18
                    + query_in_reference * 0.20 + reference_in_query * 0.15
                    + strength_agreement * 0.05
                )
                direction_agrees = reference.entrance_direction == direction
                dense_parts = []
                for query_dense, reference_dense in zip(query_dense_contexts, reference.dense_contexts):
                    containment = descriptor_containment(query_dense, reference_dense, tolerance=2)
                    similarity = descriptor_similarity(query_dense, reference_dense)
                    dense_parts.append(containment * 0.78 + similarity * 0.22)
                dense_score_raw = float(np.mean(dense_parts))
                # The live local topology direction is unstable under fog/panning.
                # Preserve agreement as diagnostics, but do not penalize a candidate
                # before its visual entrance evidence has been compared.
                dense_direction_factor = 1.0
                dense_score = dense_score_raw * dense_direction_factor
                score = legacy_score * 0.48 + dense_score * 0.52
                candidate = {
                    "mapId": reference.map_id,
                    "mapName": reference.map_name,
                    "entranceDirection": reference.entrance_direction,
                    "inferredDirectionAgreement": direction_agrees,
                    "score": float(score),
                    "localScore": float(local),
                    "topologyScore": float(topology_score),
                    "queryInReferenceScore": float(query_in_reference),
                    "referenceInQueryScore": float(reference_in_query),
                    "strengthAgreement": float(strength_agreement),
                    "legacyScore": float(legacy_score),
                    "denseContourScore": float(dense_score),
                    "denseContourRawScore": float(dense_score_raw),
                    "denseDirectionFactor": float(dense_direction_factor),
                    "sourceAnchorScore": reference.source_anchor_score,
                    "queryAnchor": {key: anchor[key] for key in ("x", "y", "width", "height")},
                }
                current = by_map.get(reference.map_id)
                if current is None or candidate["score"] > current["score"]:
                    by_map[reference.map_id] = candidate
        ranked = sorted(by_map.values(), key=lambda item: item["score"], reverse=True)
        if not ranked:
            return {
                "success": False, "reason": "marked-direction-or-context-insufficient",
                "inferredEntranceDirections": sorted(inferred_directions), "candidates": [],
            }
        top = ranked[0]
        margin = top["score"] - (ranked[1]["score"] if len(ranked) > 1 else 0.0)
        success = bool(
            top["score"] >= 0.64 and top["localScore"] >= 0.50
            and top["topologyScore"] >= 0.78 and margin >= 0.075
        )
        confidence = min(0.99, max(0.0,
            top["score"] * 0.72 + min(margin / 0.15, 1.0) * 0.28
        ))
        return {
            "success": success,
            "reason": "accepted-marked-entrance" if success else "marked-entrance-low-margin",
            "mapId": top["mapId"], "mapName": top["mapName"],
            "entranceDirection": top["entranceDirection"],
            "confidence": float(confidence), "margin": float(margin),
            "inferredEntranceDirections": sorted(inferred_directions),
            "candidateCount": len(ranked), "candidates": ranked[:8],
        }
