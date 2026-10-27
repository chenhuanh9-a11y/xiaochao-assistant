from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from side_door_recognition import BASE_CLIENT_WIDTH, BASE_MAP_GEOMETRY_SCALE, map_color_mask


DESCRIPTOR_SIZE = 161
BASE_LIVE_EXTENT = 560.0
MINIMUM_MAP_BRIGHTNESS = 80
SPATIAL_QUERY_SCALE = 0.5


@dataclass
class SpatialOutlineReference:
    map_id: str
    floor: int
    reference_id: str
    descriptor: np.ndarray


def _component_near_anchor(
    mask: np.ndarray,
    anchor: dict,
    close_kernel_size: int = 5,
    minimum_anchor_distance: float = 30.0,
) -> np.ndarray:
    """Keep the map body touching the exit neighborhood, excluding fixed UI."""
    binary = (mask > 0).astype(np.uint8)
    kernel_size = max(1, int(close_kernel_size))
    binary = cv2.morphologyEx(
        binary,
        cv2.MORPH_CLOSE,
        np.ones((kernel_size, kernel_size), np.uint8),
    )
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary)
    if count <= 1:
        return binary * 255
    center_x = float(anchor["x"]) + float(anchor["width"]) / 2
    center_y = float(anchor["y"]) + float(anchor["height"]) / 2
    choices: list[tuple[int, float, int]] = []
    for label in range(1, count):
        x, y, width, height, area = stats[label]
        dx = max(float(x) - center_x, 0.0, center_x - float(x + width))
        dy = max(float(y) - center_y, 0.0, center_y - float(y + height))
        distance = (dx * dx + dy * dy) ** 0.5
        if distance <= max(minimum_anchor_distance, float(anchor["width"]) * 2):
            choices.append((int(area), -distance, label))
    if choices:
        chosen = max(choices)[2]
    else:
        chosen = max(range(1, count), key=lambda label: int(stats[label, cv2.CC_STAT_AREA]))
    return (labels == chosen).astype(np.uint8) * 255


def _anchor_centered_descriptor(
    mask: np.ndarray, anchor: dict, source_extent: float
) -> np.ndarray:
    center_x = float(anchor["x"]) + float(anchor["width"]) / 2
    center_y = float(anchor["y"]) + float(anchor["height"]) / 2
    target_center = (DESCRIPTOR_SIZE - 1) / 2
    scale = (DESCRIPTOR_SIZE - 1) / (2 * source_extent)
    matrix = np.asarray([
        [scale, 0.0, target_center - scale * center_x],
        [0.0, scale, target_center - scale * center_y],
    ], dtype=np.float32)
    return cv2.warpAffine(
        mask, matrix, (DESCRIPTOR_SIZE, DESCRIPTOR_SIZE),
        flags=cv2.INTER_AREA, borderMode=cv2.BORDER_CONSTANT, borderValue=0,
    )


def _outline_similarity(query: np.ndarray, reference: np.ndarray) -> tuple[float, float, float]:
    query_binary = query >= 64
    reference_binary = reference >= 64
    if not query_binary.any() or not reference_binary.any():
        return 0.0, 0.0, 0.0
    tolerance = np.ones((3, 3), np.uint8)
    query_dilated = cv2.dilate(query_binary.astype(np.uint8), tolerance) > 0
    reference_dilated = cv2.dilate(reference_binary.astype(np.uint8), tolerance) > 0
    # Only score reference pixels close to actually visible query geometry.
    # Distant absent pixels may simply be unexplored and cannot be negative evidence.
    observed_support = cv2.dilate(
        query_binary.astype(np.uint8), np.ones((5, 5), np.uint8)
    ) > 0
    observed_reference = reference_binary & observed_support
    precision = float(reference_dilated[query_binary].mean())
    observed_recall = (
        float(query_dilated[observed_reference].mean()) if observed_reference.any() else 0.0
    )
    score = 2 * precision * observed_recall / max(1e-6, precision + observed_recall)
    return score, precision, observed_recall


class SpatialOutlineRetriever:
    """Exit-aligned, fine-grained outline evidence for partially explored maps."""

    def __init__(self, vision_index_path: Path, taxonomy_path: Path | None = None) -> None:
        data = json.loads(vision_index_path.read_text(encoding="utf-8"))
        image_cache: dict[str, np.ndarray | None] = {}
        mask_cache: dict[str, np.ndarray | None] = {}
        self.references: list[SpatialOutlineReference] = []
        seen: set[tuple[str, int, str]] = set()
        for entry in data.get("sideDoors", []):
            if entry.get("dedicatedModeOnly") or not entry.get("fullMapReferenceAnchor"):
                continue
            map_id = entry.get("mapId")
            floor = int(entry.get("floor", 1))
            reference_id = entry.get("referenceId", "")
            key = (map_id, floor, reference_id)
            if not map_id or key in seen:
                continue
            seen.add(key)
            anchor = dict(entry["fullMapReferenceAnchor"])
            if map_id == "map-24" and floor == 1:
                # Audited first-floor side entrance; the immutable index points
                # to the second-floor half for this one record.
                anchor = {"x": 135, "y": 338, "width": 24, "height": 20}
            if map_id not in image_cache:
                path = vision_index_path.parent / f"{map_id}.webp"
                try:
                    image_cache[map_id] = cv2.imdecode(
                        np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR
                    )
                except OSError:
                    image_cache[map_id] = None
            image = image_cache[map_id]
            if image is None:
                continue
            mask_key = f"{map_id}:{MINIMUM_MAP_BRIGHTNESS}"
            if mask_key not in mask_cache:
                mask_cache[mask_key] = map_color_mask(
                    image, MINIMUM_MAP_BRIGHTNESS
                )
            component = _component_near_anchor(mask_cache[mask_key], anchor)
            descriptor = _anchor_centered_descriptor(
                component, anchor, BASE_LIVE_EXTENT / BASE_MAP_GEOMETRY_SCALE
            )
            self.references.append(SpatialOutlineReference(
                map_id=map_id,
                floor=floor,
                reference_id=reference_id,
                descriptor=descriptor,
            ))
        if taxonomy_path and taxonomy_path.exists():
            self._append_missing_taxonomy_references(taxonomy_path)

    @staticmethod
    def _green_annotation_mask(image: np.ndarray) -> np.ndarray:
        blue, green, red = cv2.split(image.astype(np.int16))
        return (
            (green >= 105) & (green >= red + 48) & (green >= blue + 28)
        ).astype(np.uint8) * 255

    @classmethod
    def _side_label_anchors(cls, image: np.ndarray) -> list[dict]:
        """Recover the two floor anchors used by the 30-class source audit."""
        height, width = image.shape[:2]
        scale = width / 4096.0
        mask = cls._green_annotation_mask(image)
        count, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        components = []
        minimum_area = max(28, round(52 * scale * scale))
        for index in range(1, count):
            x, y, component_width, component_height, area = [
                int(value) for value in stats[index]
            ]
            if area < minimum_area:
                continue
            if (
                component_width > 14 * max(component_height, 1)
                or component_height > 14 * max(component_width, 1)
            ):
                continue
            components.append({
                "x": x, "y": y, "width": component_width,
                "height": component_height, "area": area,
            })

        join_distance = 175 * scale
        groups: list[list[dict]] = []
        pending = components[:]
        while pending:
            group = [pending.pop()]
            changed = True
            while changed:
                changed = False
                for item in pending[:]:
                    item_x = item["x"] + item["width"] / 2
                    item_y = item["y"] + item["height"] / 2
                    if any(
                        np.hypot(
                            item_x - (other["x"] + other["width"] / 2),
                            item_y - (other["y"] + other["height"] / 2),
                        ) <= join_distance
                        for other in group
                    ):
                        group.append(item)
                        pending.remove(item)
                        changed = True
            groups.append(group)

        candidates = []
        for group in groups:
            x0 = min(item["x"] for item in group)
            y0 = min(item["y"] for item in group)
            x1 = max(item["x"] + item["width"] for item in group)
            y1 = max(item["y"] + item["height"] for item in group)
            area = sum(item["area"] for item in group)
            if area < 520 * scale * scale:
                continue
            if x1 - x0 > width * 0.16 or y1 - y0 > height * 0.12:
                continue
            center_x, center_y = (x0 + x1) / 2, (y0 + y1) / 2
            vector_x, vector_y = width / 2 - center_x, height / 2 - center_y
            length = max(1.0, float(np.hypot(vector_x, vector_y)))
            shift = 105 * scale
            candidates.append({
                "x": float(center_x + vector_x / length * shift),
                "y": float(center_y + vector_y / length * shift),
                "greenArea": area,
            })
        candidates.sort(key=lambda item: item["greenArea"], reverse=True)
        selected = []
        for candidate in candidates:
            if any(
                np.hypot(candidate["x"] - other["x"], candidate["y"] - other["y"])
                < 420 * scale
                for other in selected
            ):
                continue
            selected.append(candidate)
            if len(selected) == 2:
                break
        selected.sort(key=lambda item: (item["x"], item["y"]))
        for floor, item in enumerate(selected, 1):
            item["floor"] = floor
        return selected

    def _append_missing_taxonomy_references(self, taxonomy_path: Path) -> None:
        taxonomy = json.loads(taxonomy_path.read_text(encoding="utf-8"))
        existing_ids = {reference.map_id for reference in self.references}
        taxonomy_ids = {
            record.get("map_id") for record in taxonomy.get("records", [])
            if record.get("map_id")
        }
        missing_ids = taxonomy_ids - existing_ids
        workspace_root = taxonomy_path.resolve().parents[1]
        for record in taxonomy.get("records", []):
            map_id = record.get("map_id")
            if map_id not in missing_ids or not record.get("source"):
                continue
            source = workspace_root / Path(record["source"])
            try:
                image = cv2.imdecode(np.fromfile(source, dtype=np.uint8), cv2.IMREAD_COLOR)
            except OSError:
                image = None
            if image is None:
                continue
            anchors = self._side_label_anchors(image)
            if len(anchors) != 2:
                continue
            map_mask = map_color_mask(image, MINIMUM_MAP_BRIGHTNESS)
            source_scale = image.shape[1] / 900.0
            for anchor_point in anchors:
                icon_width = 24.0 * source_scale
                icon_height = 20.0 * source_scale
                anchor = {
                    "x": anchor_point["x"] - icon_width / 2,
                    "y": anchor_point["y"] - icon_height / 2,
                    "width": icon_width,
                    "height": icon_height,
                }
                component = _component_near_anchor(map_mask, anchor)
                descriptor = _anchor_centered_descriptor(
                    component, anchor,
                    (BASE_LIVE_EXTENT / BASE_MAP_GEOMETRY_SCALE) * source_scale,
                )
                self.references.append(SpatialOutlineReference(
                    map_id=map_id,
                    floor=int(anchor_point["floor"]),
                    reference_id=f'taxonomy:{record.get("strategy", "unknown")}',
                    descriptor=descriptor,
                ))

    def recognize(
        self, frame: np.ndarray, anchors: list[dict], selected_floor: int | None
    ) -> dict:
        if not anchors or not self.references:
            return {"success": False, "reason": "no-spatial-outline", "candidates": []}
        anchor = anchors[0]
        frame_height, frame_width = frame.shape[:2]
        extent = BASE_LIVE_EXTENT * frame_width / BASE_CLIENT_WIDTH
        center_x = float(anchor["x"]) + float(anchor["width"]) / 2
        center_y = float(anchor["y"]) + float(anchor["height"]) / 2
        padding = 8
        x0 = max(0, int(np.floor(center_x - extent - padding)))
        y0 = max(0, int(np.floor(center_y - extent - padding)))
        x1 = min(frame_width, int(np.ceil(center_x + extent + padding + 1)))
        y1 = min(frame_height, int(np.ceil(center_y + extent + padding + 1)))
        crop = frame[y0:y1, x0:x1]
        local_anchor = dict(anchor)
        local_anchor["x"] -= x0
        local_anchor["y"] -= y0
        # The final descriptor is only 161x161. Running color segmentation,
        # morphology and connected components on the original ~1120px square
        # query wastes most pixels. A 0.5x geometric copy still carries more
        # than three source pixels per descriptor pixel and is normalized by
        # the same exit-centered affine transform.
        query_scale = SPATIAL_QUERY_SCALE
        if query_scale < 1.0:
            crop = cv2.resize(
                crop,
                (
                    max(1, round(crop.shape[1] * query_scale)),
                    max(1, round(crop.shape[0] * query_scale)),
                ),
                interpolation=cv2.INTER_AREA,
            )
            for key in ("x", "y", "width", "height"):
                local_anchor[key] = float(local_anchor[key]) * query_scale
        query_mask = map_color_mask(crop, MINIMUM_MAP_BRIGHTNESS)
        query_component = _component_near_anchor(
            query_mask,
            local_anchor,
            close_kernel_size=3,
            minimum_anchor_distance=30.0 * query_scale,
        )
        query_descriptor = _anchor_centered_descriptor(
            query_component, local_anchor, extent * query_scale
        )

        by_map: dict[str, dict] = {}
        for reference in self.references:
            if selected_floor is not None and reference.floor != selected_floor:
                continue
            score, precision, observed_recall = _outline_similarity(
                query_descriptor, reference.descriptor
            )
            candidate = {
                "mapId": reference.map_id,
                "floor": reference.floor,
                "referenceId": reference.reference_id,
                "score": float(score),
                "precision": float(precision),
                "observedRecall": float(observed_recall),
            }
            current = by_map.get(reference.map_id)
            if current is None or candidate["score"] > current["score"]:
                by_map[reference.map_id] = candidate
        ranked = sorted(by_map.values(), key=lambda value: value["score"], reverse=True)
        if not ranked:
            return {"success": False, "reason": "no-floor-spatial-outline", "candidates": []}
        top = ranked[0]
        margin = top["score"] - (ranked[1]["score"] if len(ranked) > 1 else 0.0)
        success = bool(top["score"] >= 0.70 and margin >= 0.05)
        return {
            "success": success,
            "reason": "spatial-outline-confirmed" if success else "spatial-outline-ambiguous",
            "mapId": top["mapId"],
            "floor": top["floor"],
            "score": top["score"],
            "confidence": top["score"],
            "margin": float(margin),
            "candidateCount": len(ranked),
            "queryScale": query_scale,
            "candidates": ranked[:10],
        }
