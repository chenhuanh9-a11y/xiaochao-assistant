from __future__ import annotations

import base64
import json
import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


REFERENCE_EXIT_WIDTH = 24.0
REFERENCE_GRID_STEP = REFERENCE_EXIT_WIDTH * 0.72
REFERENCE_SAMPLE_RADIUS = REFERENCE_EXIT_WIDTH * 0.28
BASE_CLIENT_WIDTH = 2560.0
BASE_MAP_GEOMETRY_SCALE = 1.125
RUNTIME_QUERY_SCALE = 0.5


def runtime_descriptor_sampling(frame_width: int) -> tuple[float, float]:
    """Calibrated map geometry sampling, independent of screen-space UI icon size."""
    scale = BASE_MAP_GEOMETRY_SCALE * frame_width / BASE_CLIENT_WIDTH
    return REFERENCE_GRID_STEP * scale, REFERENCE_SAMPLE_RADIUS * scale


def _color_statistics(frame_bgr: np.ndarray) -> tuple[np.ndarray, ...]:
    bgr = frame_bgr.astype(np.int16)
    blue, green, red = bgr[:, :, 0], bgr[:, :, 1], bgr[:, :, 2]
    maximum = np.maximum(np.maximum(blue, green), red)
    minimum = np.minimum(np.minimum(blue, green), red)
    mean = (blue + green + red) / 3.0
    return blue, green, red, mean, maximum - minimum


def _masks_from_statistics(
    statistics: tuple[np.ndarray, ...],
    minimum_brightness: int = 64,
    adaptive: bool = True,
    room_minimum_brightness: int | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    blue, green, red, mean, chroma = statistics
    neutral = (
        (mean >= max(52, minimum_brightness)) & (mean <= (224 if adaptive else 175))
        & (chroma <= (78 if adaptive else 58))
    )
    blue_floor = (
        (blue >= red + (3 if adaptive else 4)) & (blue >= green - (2 if adaptive else 0))
        & (mean >= minimum_brightness) & (mean <= (224 if adaptive else 155))
        & (chroma <= (92 if adaptive else 74))
    )
    brown_floor = (
        (red >= blue + (3 if adaptive else 4)) & (mean >= minimum_brightness)
        & (mean <= (224 if adaptive else 165)) & (chroma <= (92 if adaptive else 72))
    )
    room_floor = room_minimum_brightness if room_minimum_brightness is not None else minimum_brightness
    room = (
        (red >= blue + (3 if adaptive else 4)) & (red >= green - (5 if adaptive else 4))
        & (mean >= room_floor) & (mean <= (224 if adaptive else 165))
        & (chroma <= (92 if adaptive else 72))
    )
    return (
        (neutral | blue_floor | brown_floor).astype(np.uint8) * 255,
        room.astype(np.uint8) * 255,
    )


def map_room_color_masks(
    frame_bgr: np.ndarray,
    minimum_brightness: int = 64,
    adaptive: bool = True,
    room_minimum_brightness: int | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    return _masks_from_statistics(
        _color_statistics(frame_bgr),
        minimum_brightness,
        adaptive,
        room_minimum_brightness,
    )


def runtime_color_masks(frame_bgr: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    statistics = _color_statistics(frame_bgr)
    map_mask, room_mask = _masks_from_statistics(statistics, 64, True, 64)
    marked_map_mask, _ = _masks_from_statistics(statistics, 50, True, 50)
    return map_mask, room_mask, marked_map_mask


def map_color_mask(frame_bgr: np.ndarray, minimum_brightness: int = 64, adaptive: bool = True) -> np.ndarray:
    return map_room_color_masks(frame_bgr, minimum_brightness, adaptive)[0]


def room_color_mask(frame_bgr: np.ndarray, minimum_brightness: int = 64, adaptive: bool = True) -> np.ndarray:
    return map_room_color_masks(frame_bgr, minimum_brightness, adaptive)[1]


def anchor_descriptor(
    mask: np.ndarray,
    anchor: dict,
    radius: int,
    integral: np.ndarray | None = None,
    step_override: float | None = None,
    sample_radius_override: float | None = None,
) -> np.ndarray:
    size = radius * 2 + 1
    center_x = anchor["x"] + anchor["width"] / 2
    center_y = anchor["y"] + anchor["height"] / 2
    step = max(2.0, step_override if step_override is not None else anchor["width"] * 0.72)
    sample_radius = max(1, round(
        sample_radius_override if sample_radius_override is not None else anchor["width"] * 0.28
    ))
    height, width = mask.shape
    summed = integral if integral is not None else cv2.integral(mask)
    grid = np.arange(-radius, radius + 1, dtype=np.float64)
    gx, gy = np.meshgrid(grid, grid)
    sx = np.rint(center_x + gx * step).astype(np.int32)
    sy = np.rint(center_y + gy * step).astype(np.int32)
    x0 = np.clip(sx - sample_radius, 0, width)
    x1 = np.clip(sx + sample_radius + 1, 0, width)
    y0 = np.clip(sy - sample_radius, 0, height)
    y1 = np.clip(sy + sample_radius + 1, 0, height)
    area = (x1 - x0) * (y1 - y0)
    valid = area > 0
    totals = np.zeros((size, size), np.float64)
    totals[valid] = (
        summed[y1[valid], x1[valid]] - summed[y0[valid], x1[valid]]
        - summed[y1[valid], x0[valid]] + summed[y0[valid], x0[valid]]
    )
    result = np.zeros((size, size), np.uint8)
    result[valid] = np.clip(np.rint(totals[valid] / area[valid]), 0, 255).astype(np.uint8)
    return result


def central_descriptor(descriptor: np.ndarray, radius: int = 5) -> np.ndarray:
    size = descriptor.shape[0]
    center = size // 2
    if size < radius * 2 + 1:
        return descriptor.ravel()
    return descriptor[center - radius:center + radius + 1, center - radius:center + radius + 1].ravel()


def descriptor_similarity(left: np.ndarray, right: np.ndarray) -> float:
    if left.size != right.size:
        return 0.0
    a = left.astype(np.float32).ravel() / 255.0
    b = right.astype(np.float32).ravel() / 255.0
    intersection = np.minimum(a, b).sum()
    occupied_f1 = 2 * intersection / max(0.001, float(a.sum() + b.sum()))
    weight = 1 + np.maximum(a, b) * 2
    agreement = 1 - float((np.abs(a - b) * weight).sum()) / max(0.001, float(weight.sum()))
    return float(occupied_f1 * 0.75 + agreement * 0.25)


def descriptor_containment(query: np.ndarray, reference: np.ndarray, tolerance: int = 1) -> float:
    if query.shape != reference.shape:
        return 0.0
    kernel = np.ones((tolerance * 2 + 1, tolerance * 2 + 1), np.uint8)
    expanded = cv2.dilate(reference, kernel)
    q = query.astype(np.float32) / 255.0
    r = expanded.astype(np.float32) / 255.0
    visible = q[q >= 0.08]
    if not visible.size:
        return 0.0
    keep = q >= 0.08
    return float(np.minimum(q[keep], r[keep]).sum() / max(0.001, q[keep].sum()))


def region_mean(descriptor: np.ndarray, min_x: int, max_x: int, min_y: int, max_y: int) -> float:
    center = descriptor.shape[0] // 2
    region = descriptor[center + min_y:center + max_y + 1, center + min_x:center + max_x + 1]
    return float(region.mean() / 255.0) if region.size else 0.0


def ray_mean(descriptor: np.ndarray, dx: int, dy: int, distance: int = 4) -> float:
    center = descriptor.shape[0] // 2
    values = []
    for step in range(1, distance + 1):
        x, y = center + dx * step, center + dy * step
        if 0 <= x < descriptor.shape[1] and 0 <= y < descriptor.shape[0]:
            values.append(descriptor[y, x] / 255.0)
    return float(np.mean(values)) if values else 0.0


def topology(descriptor: np.ndarray, threshold: float = 0.18, approach_hint: str | None = None) -> dict | None:
    arms = {
        "left": region_mean(descriptor, -4, -1, -1, 1), "right": region_mean(descriptor, 1, 4, -1, 1),
        "up": region_mean(descriptor, -1, 1, -4, -1), "down": region_mean(descriptor, -1, 1, 1, 4),
    }
    boundaries = {
        "left": region_mean(descriptor, -5, -1, -3, 3), "right": region_mean(descriptor, 1, 5, -3, 3),
        "up": region_mean(descriptor, -3, 3, -5, -1), "down": region_mean(descriptor, -3, 3, 1, 5),
    }
    rays = {
        "left": ray_mean(descriptor, -1, 0), "right": ray_mean(descriptor, 1, 0),
        "up": ray_mean(descriptor, 0, -1), "down": ray_mean(descriptor, 0, 1),
        "leftUp": ray_mean(descriptor, -1, -1), "rightUp": ray_mean(descriptor, 1, -1),
        "leftDown": ray_mean(descriptor, -1, 1), "rightDown": ray_mean(descriptor, 1, 1),
    }
    quadrants = {
        "leftUp": region_mean(descriptor, -4, -2, -4, -2), "rightUp": region_mean(descriptor, 2, 4, -4, -2),
        "leftDown": region_mean(descriptor, -4, -2, 2, 4), "rightDown": region_mean(descriptor, 2, 4, 2, 4),
    }
    opposite = {"left": "right", "right": "left", "up": "down", "down": "up"}
    strengths = boundaries if approach_hint else arms
    if approach_hint:
        approach = approach_hint
    else:
        approach = max(strengths, key=strengths.get)
    if strengths[approach] < threshold:
        return None
    ray_approach = max(rays, key=rays.get)
    branch_keys = {
        "left": ("leftUp", "leftDown"), "right": ("rightUp", "rightDown"),
        "up": ("leftUp", "rightUp"), "down": ("leftDown", "rightDown"),
    }[approach]
    branch_strengths = [quadrants[k] for k in branch_keys]
    return {
        "approach": approach, "approachStrength": strengths[approach],
        "approachContrast": strengths[approach] - strengths[opposite[approach]],
        "directionStrengths": strengths, "rayApproach": ray_approach,
        "rayApproachStrength": rays[ray_approach], "rayStrengths": rays,
        "branches": [value >= threshold for value in branch_strengths], "branchStrengths": branch_strengths,
    }


def ray_similarity(left: str | None, right: str | None) -> float:
    vectors = {
        "left": (-1, 0), "right": (1, 0), "up": (0, -1), "down": (0, 1),
        "leftUp": (-math.sqrt(0.5), -math.sqrt(0.5)), "rightUp": (math.sqrt(0.5), -math.sqrt(0.5)),
        "leftDown": (-math.sqrt(0.5), math.sqrt(0.5)), "rightDown": (math.sqrt(0.5), math.sqrt(0.5)),
    }
    if left not in vectors or right not in vectors:
        return 0.0
    a, b = vectors[left], vectors[right]
    return max(0.0, a[0] * b[0] + a[1] * b[1])


def _strength_profile_similarity(
    left: dict | None, right: dict | None, keys: tuple[str, ...]
) -> float:
    """Compare a whole directional profile instead of only its strongest bin.

    Exploration can expose one adjacent arm before another, which makes the
    peak direction jump even though the entrance geometry is unchanged.  The
    cosine term preserves the profile shape while the overlap term penalizes
    genuinely missing/excess geometry.
    """
    if not left or not right:
        return 0.0
    left_values = np.asarray([float(left.get(key, 0.0)) for key in keys], dtype=np.float32)
    right_values = np.asarray([float(right.get(key, 0.0)) for key in keys], dtype=np.float32)
    left_norm = float(np.linalg.norm(left_values))
    right_norm = float(np.linalg.norm(right_values))
    if left_norm <= 1e-6 or right_norm <= 1e-6:
        return 0.0
    cosine = float(np.dot(left_values, right_values) / (left_norm * right_norm))
    total = float(left_values.sum() + right_values.sum())
    overlap = float(2.0 * np.minimum(left_values, right_values).sum() / max(total, 1e-6))
    return max(0.0, min(1.0, cosine * 0.58 + overlap * 0.42))


def topology_similarity(left: dict | None, right: dict | None) -> float:
    if not left or not right:
        return 0.0
    if left.get("rayApproach") == right.get("rayApproach"):
        agreement = 1 - abs(
            float(left.get("rayApproachStrength", 0))
            - float(right.get("rayApproachStrength", 0))
        )
        return 0.8 + max(0.0, agreement) * 0.2
    ray_keys = (
        "left", "right", "up", "down",
        "leftUp", "rightUp", "leftDown", "rightDown",
    )
    direction_keys = ("left", "right", "up", "down")
    ray_profile = _strength_profile_similarity(
        left.get("rayStrengths"), right.get("rayStrengths"), ray_keys
    )
    direction_profile = _strength_profile_similarity(
        left.get("directionStrengths"), right.get("directionStrengths"), direction_keys
    )
    branch_left = np.asarray(left.get("branchStrengths", []), dtype=np.float32)
    branch_right = np.asarray(right.get("branchStrengths", []), dtype=np.float32)
    if branch_left.shape == branch_right.shape and branch_left.size:
        branch_profile = max(0.0, 1.0 - float(np.mean(np.abs(branch_left - branch_right))))
    else:
        branch_profile = 0.0
    peak_direction = ray_similarity(left.get("rayApproach"), right.get("rayApproach"))
    return max(0.0, min(1.0,
        ray_profile * 0.56 + direction_profile * 0.22
        + branch_profile * 0.14 + peak_direction * 0.08
    ))


def anchor_appearance(frame_bgr: np.ndarray, anchor: dict) -> dict:
    crop = frame_bgr[anchor["y"]:anchor["y"] + anchor["height"], anchor["x"]:anchor["x"] + anchor["width"]]
    if not crop.size:
        return {}
    rgb = crop[:, :, ::-1].astype(np.float32)
    mean = rgb.mean(axis=2)
    chroma = rgb.max(axis=2) - rgb.min(axis=2)
    bright = mean >= 75
    red, green, blue = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    return {
        "brightPixels": int(bright.sum()), "brightRatio": float(bright.mean()),
        "veryBrightRatio": float((mean >= 120).mean()),
        "neutralRatio": float(((chroma <= 28) & bright).sum() / max(1, bright.sum())),
        "wideNeutralRatio": float(((chroma <= 36) & bright).sum() / max(1, bright.sum())),
        "yellowRatio": float(((red >= blue + 24) & (green >= blue + 18) & (mean >= 70)).mean()),
        "darkRatio": float((mean < 55).mean()), "meanBrightness": float(mean.mean()),
        "brightnessDeviation": float(mean.std()),
    }


def appearance_ok(value: dict, relaxed: bool = True) -> bool:
    return bool(
        value and value["brightPixels"] >= 10 and value["brightRatio"] <= (0.98 if relaxed else 0.9)
        and value["wideNeutralRatio"] >= (0.7 if relaxed else 0.78)
        and (value["veryBrightRatio"] >= (0.01 if relaxed else 0.015) or value["brightnessDeviation"] >= (12 if relaxed else 18))
    )


@dataclass
class SideDoorReference:
    map_id: str
    floor: int
    reference_id: str
    context: np.ndarray | None
    wide_context: np.ndarray | None
    wide_room: np.ndarray | None
    far_context: np.ndarray | None
    far_room: np.ndarray | None
    full_context: np.ndarray | None
    full_room: np.ndarray | None
    context_topology: dict


class SideDoorRecognizer:
    def __init__(
        self,
        vision_index_path: Path,
        taxonomy_path: Path | None = None,
        marked_poster_path: Path | None = None,
    ) -> None:
        data = json.loads(vision_index_path.read_text(encoding="utf-8"))
        icon = data["exitIcon"]
        self.exit_icon = np.frombuffer(base64.b64decode(icon["pixels"]), np.uint8).reshape(icon["height"], icon["width"])
        self.references = []
        for entry in data.get("sideDoors", []):
            if entry.get("dedicatedModeOnly"):
                continue
            if not entry.get("contextAnchorTopology") or not any(
                entry.get(key) for key in (
                    "contextAnchorDescriptor", "fullContextAnchorDescriptor", "fullRoomAnchorDescriptor"
                )
            ):
                continue
            self.references.append(SideDoorReference(
                entry["mapId"], int(entry.get("floor", 1)), entry.get("referenceId", ""),
                self._square(entry["contextAnchorDescriptor"]) if entry.get("contextAnchorDescriptor") else None,
                self._square(entry["wideContextAnchorDescriptor"]) if entry.get("wideContextAnchorDescriptor") else None,
                self._square(entry["wideRoomAnchorDescriptor"]) if entry.get("wideRoomAnchorDescriptor") else None,
                self._square(entry["farContextAnchorDescriptor"]) if entry.get("farContextAnchorDescriptor") else None,
                self._square(entry["farRoomAnchorDescriptor"]) if entry.get("farRoomAnchorDescriptor") else None,
                self._square(entry["fullContextAnchorDescriptor"]) if entry.get("fullContextAnchorDescriptor") else None,
                self._square(entry["fullRoomAnchorDescriptor"]) if entry.get("fullRoomAnchorDescriptor") else None,
                entry["contextAnchorTopology"],
            ))
        self._repair_known_reference_anchors(vision_index_path)
        self.taxonomy_by_map: dict[str, dict] = {}
        if taxonomy_path and taxonomy_path.exists():
            taxonomy = json.loads(taxonomy_path.read_text(encoding="utf-8"))
            grouped: dict[str, list[dict]] = {}
            for record in taxonomy.get("records", []):
                if record.get("map_id"):
                    grouped.setdefault(record["map_id"], []).append(record)
            for map_id, records in grouped.items():
                self.taxonomy_by_map[map_id] = {
                    "shapeCategory": records[0].get("shape_category", ""),
                    "sideDoorPosition": records[0].get("side_door_position", ""),
                    "mapName": records[0].get("map_name", ""),
                    "routeVariants": sorted({record.get("route_variant", "") for record in records if record.get("route_variant")}),
                }
        self.marked_retriever = None
        if marked_poster_path and marked_poster_path.exists():
            # Local import avoids making the reusable descriptor helpers depend
            # on the poster-index module at import time.
            from marked_map_retrieval import MarkedMapRetriever

            self.marked_retriever = MarkedMapRetriever(marked_poster_path, self.exit_icon)

    @staticmethod
    def _square(encoded: str) -> np.ndarray:
        raw = np.frombuffer(base64.b64decode(encoded), np.uint8)
        size = round(math.sqrt(raw.size))
        return raw.reshape(size, size)

    def _repair_known_reference_anchors(self, vision_index_path: Path) -> None:
        """Repair audited floor/anchor defects without mutating source assets.

        map-24 was indexed as floor 1 but its full-map descriptor came from
        y=773, inside the second-floor half of the 900x1500 canonical poster.
        The user-confirmed first-floor side entrance is x=135,y=338. Rebuild
        only that descriptor in memory from the immutable canonical map.
        """
        repairs = {
            "map-24": {"x": 135, "y": 338, "width": 24, "height": 20},
        }
        for map_id, anchor in repairs.items():
            references = [reference for reference in self.references if reference.map_id == map_id]
            if not references:
                continue
            path = vision_index_path.parent / f"{map_id}.webp"
            try:
                image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
            except OSError:
                image = None
            if image is None:
                continue
            map_mask, room_mask = map_room_color_masks(image)
            map_integral = cv2.integral(map_mask)
            room_integral = cv2.integral(room_mask)
            local = anchor_descriptor(map_mask, anchor, 9, map_integral)
            wide = anchor_descriptor(map_mask, anchor, 16, map_integral)
            full = anchor_descriptor(map_mask, anchor, 24, map_integral)
            wide_room = anchor_descriptor(room_mask, anchor, 16, room_integral)
            full_room = anchor_descriptor(room_mask, anchor, 24, room_integral)
            local_topology = topology(local)
            if local_topology is None:
                continue
            for reference in references:
                reference.context = local
                reference.wide_context = wide
                reference.wide_room = wide_room
                reference.full_context = full
                reference.full_room = full_room
                reference.context_topology = local_topology

    def find_anchors(
        self, frame_bgr: np.ndarray, allow_unusual_scales: bool = True,
        *, match_workers: int = 1, search_bounds=None,
    ) -> list[dict]:
        frame_height, frame_width = frame_bgr.shape[:2]
        roi_x0, roi_x1 = round(frame_width * 0.18), round(frame_width * 0.84)
        roi_y0, roi_y1 = round(frame_height * 0.10), round(frame_height * 0.84)
        if search_bounds is not None:
            x0, y0, x1, y1 = map(int, search_bounds)
            roi_x0, roi_y0 = max(roi_x0, x0), max(roi_y0, y0)
            roi_x1, roi_y1 = min(roi_x1, x1), min(roi_y1, y1)
        if roi_x1 <= roi_x0 or roi_y1 <= roi_y0:
            return []
        gray = cv2.cvtColor(frame_bgr[roi_y0:roi_y1, roi_x0:roi_x1], cv2.COLOR_BGR2GRAY)

        def search(scales: tuple[float, ...]) -> list[dict]:
            candidates = []

            def correlate(scale):
                width = max(4, round(self.exit_icon.shape[1] * scale))
                height = max(4, round(self.exit_icon.shape[0] * scale))
                if width > gray.shape[1] or height > gray.shape[0]:
                    return width, height, None
                template = cv2.resize(self.exit_icon, (width, height), interpolation=cv2.INTER_AREA)
                return width, height, cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)

            def consume(width, height, result):
                if result is None:
                    return
                for _ in range(5):
                    _, score, _, point = cv2.minMaxLoc(result)
                    if score < 0.30:
                        break
                    candidate = {
                        "score": float(score),
                        "x": int(point[0] + roi_x0),
                        "y": int(point[1] + roi_y0),
                        "width": width,
                        "height": height,
                    }
                    candidate["appearance"] = anchor_appearance(frame_bgr, candidate)
                    candidates.append(candidate)
                    radius = max(width, height)
                    x0, x1 = max(0, point[0] - radius), min(result.shape[1], point[0] + radius + 1)
                    y0, y1 = max(0, point[1] - radius), min(result.shape[0], point[1] + radius + 1)
                    result[y0:y1, x0:x1] = -1
            if match_workers == 2:
                from collections import deque
                from concurrent.futures import ThreadPoolExecutor
                from itertools import islice
                # Only correlations run concurrently. Consume in scale order,
                # releasing each result before submitting its replacement.
                # At most two futures/results exist; no pool outlives the call.
                with ThreadPoolExecutor(max_workers=2) as executor:
                    remaining = iter(scales)
                    pending = deque(executor.submit(correlate, scale)
                                    for scale in islice(remaining, 2))
                    while pending:
                        future = pending.popleft()
                        consume(*future.result())
                        del future
                        scale = next(remaining, None)
                        if scale is not None:
                            pending.append(executor.submit(correlate, scale))
            else:
                for scale in scales:
                    consume(*correlate(scale))
            return candidates

        def accepted_from(candidates: list[dict]) -> list[dict]:
            candidates.sort(key=lambda x: x["score"], reverse=True)
            distinct = []
            for candidate in candidates:
                cx = candidate["x"] + candidate["width"] / 2
                cy = candidate["y"] + candidate["height"] / 2
                if any(math.hypot(cx - (e["x"] + e["width"] / 2), cy - (e["y"] + e["height"] / 2)) < max(candidate["width"], e["width"]) * 1.25 for e in distinct):
                    continue
                distinct.append(candidate)
                if len(distinct) >= 16:
                    break
            return [
                candidate for candidate in distinct
                if appearance_ok(candidate["appearance"], True)
                and candidate["width"] >= max(10, round(frame_width * 0.0045))
                and frame_width * 0.18 <= candidate["x"] <= frame_width * 0.84
                and frame_height * 0.10 <= candidate["y"] <= frame_height * 0.84
                and not (
                    frame_width * 0.18 <= candidate["x"] <= frame_width * 0.30
                    and candidate["y"] >= frame_height * 0.70
                )
            ]

        # Live readiness uses the native 1.0 UI template first. Exit icons are
        # screen-space UI, not map geometry; all audited live frames exceed
        # 0.50 correlation at this scale even when 0.9/1.1 is the absolute max.
        # This removes two large ROI correlations from the common live path.
        # The live button path stays on the cheap native-scale probe.  M2's
        # offline/window-size recovery path asks for unusual scales explicitly
        # and must include enlarged templates as well; otherwise a dark UI
        # correlation at 0.9 can suppress the real 1.2x exit icon.
        first_scales = (
            (1.0,)
            if not allow_unusual_scales
            else (0.80, 0.90, 1.0, 1.1, 1.2, 1.3)
        )
        accepted = accepted_from(search(first_scales))
        if accepted:
            return accepted
        if not allow_unusual_scales:
            return accepted_from(search((0.90, 1.1)))
        # Fail closed while retaining calibrated coverage for unusual zooms.
        return accepted_from(search((0.18, 0.24, 0.32, 0.42, 0.55, 0.70)))

    def recognize(
        self,
        frame_bgr: np.ndarray,
        selected_floor: int | None = None,
        include_query_features: bool = False,
        early_matcher=None,
        precomputed_anchors: list[dict] | None = None,
        defer_legacy_when_early_score_at_least: float | None = None,
    ) -> dict:
        anchors = precomputed_anchors if precomputed_anchors is not None else self.find_anchors(frame_bgr)
        if not anchors:
            return {"success": False, "reason": "exit-anchor-not-found", "candidates": []}
        # Keep the calibrated base masks byte-for-byte equivalent to the
        # original 64-brightness rule. The independently built marked-poster
        # index intentionally uses 50 to retain dim fog evidence.
        # Descriptors sample at most 24 anchor-spaced cells around the best
        # exit icon. Building three masks for the entire 2560x1417 client used
        # to dominate live latency even though pixels far from that icon could
        # never contribute. Runtime therefore masks only the exact descriptor
        # support window. Offline poster retrieval keeps the full-frame path.
        descriptor_anchor = anchors[0]
        descriptor_step, descriptor_sample_radius = runtime_descriptor_sampling(frame_bgr.shape[1])
        mask_origin_x = 0
        mask_origin_y = 0
        mask_scale = 1.0
        mask_frame = frame_bgr
        if self.marked_retriever is None:
            center_x = descriptor_anchor["x"] + descriptor_anchor["width"] / 2
            center_y = descriptor_anchor["y"] + descriptor_anchor["height"] / 2
            support = math.ceil(24 * descriptor_step + descriptor_sample_radius + 3)
            frame_height, frame_width = frame_bgr.shape[:2]
            mask_origin_x = max(0, math.floor(center_x - support))
            mask_origin_y = max(0, math.floor(center_y - support))
            mask_x1 = min(frame_width, math.ceil(center_x + support + 1))
            mask_y1 = min(frame_height, math.ceil(center_y + support + 1))
            mask_frame = frame_bgr[mask_origin_y:mask_y1, mask_origin_x:mask_x1]
            # Runtime descriptors contain at most 49x49 sampled cells. A half
            # resolution copy of their support still supplies several pixels
            # per cell while reducing three color masks from ~0.8M pixels to
            # ~0.2M. Poster-wide offline retrieval keeps the original scale.
            mask_scale = RUNTIME_QUERY_SCALE
            if mask_scale < 1.0:
                mask_frame = cv2.resize(
                    mask_frame,
                    (
                        max(1, round(mask_frame.shape[1] * mask_scale)),
                        max(1, round(mask_frame.shape[0] * mask_scale)),
                    ),
                    interpolation=cv2.INTER_AREA,
                )
                descriptor_step *= mask_scale
                descriptor_sample_radius *= mask_scale
        map_mask, room_mask, marked_map_mask = runtime_color_masks(mask_frame)
        map_integral = cv2.integral(map_mask)
        room_integral = cv2.integral(room_mask)
        marked_map_integral = cv2.integral(marked_map_mask)
        marked_evidence = (
            self.marked_retriever.recognize(
                frame_bgr, anchors, marked_map_mask, marked_map_integral
            )
            if self.marked_retriever is not None else None
        )
        by_map: dict[str, dict] = {}
        searched_reference_ids: set[str] = set()
        query_features: list[dict] = []
        # The list is template-score ordered. Competing lower-score icons are
        # usually cipher/player/UI markers and were a major source of both late
        # latency and false map candidates. The side-door crop is anchored once.
        for anchor in anchors[:1]:
            local_anchor = dict(anchor)
            local_anchor["x"] = (local_anchor["x"] - mask_origin_x) * mask_scale
            local_anchor["y"] = (local_anchor["y"] - mask_origin_y) * mask_scale
            local_anchor["width"] *= mask_scale
            local_anchor["height"] *= mask_scale
            context = anchor_descriptor(
                map_mask, local_anchor, 9, map_integral,
                descriptor_step, descriptor_sample_radius,
            )
            wide_context = anchor_descriptor(
                map_mask, local_anchor, 16, map_integral,
                descriptor_step, descriptor_sample_radius,
            )
            wide_room = anchor_descriptor(
                room_mask, local_anchor, 16, room_integral,
                descriptor_step, descriptor_sample_radius,
            )
            full_context = anchor_descriptor(
                map_mask, local_anchor, 24, map_integral,
                descriptor_step, descriptor_sample_radius,
            )
            full_room = anchor_descriptor(
                room_mask, local_anchor, 24, room_integral,
                descriptor_step, descriptor_sample_radius,
            )
            query_topology = topology(context)
            if not query_topology:
                continue
            query_features.append({
                "anchor": anchor,
                "local": context,
                "wide": wide_context,
                "full": full_context,
                "topology": query_topology,
            })
            if early_matcher is not None:
                early = early_matcher(query_features, selected_floor)
                if early.get("success"):
                    result = {
                        "success": True,
                        "reason": "accepted-human-labeled-live-exemplar",
                        "mapId": early.get("mapId"), "floor": early.get("floor"),
                        "confidence": early.get("confidence", 0.0),
                        "margin": early.get("margin", 0.0),
                        "selectedFloor": selected_floor,
                        "markedEvidence": None,
                        "anchor": (early.get("candidates") or [{}])[0].get("anchor"),
                        "anchors": anchors, "candidates": early.get("candidates", []),
                        "_earlyLiveEvidence": early,
                    }
                    if include_query_features:
                        result["_queryFeatures"] = query_features
                    return result
                if (
                    defer_legacy_when_early_score_at_least is not None
                    and early.get("candidates")
                    and float(early.get("score", 0.0))
                    >= defer_legacy_when_early_score_at_least
                ):
                    # A strong labeled-live resemblance already prevents a
                    # contradictory synthetic-only acceptance later in the
                    # runtime policy. Defer the expensive legacy ranking and
                    # let the independent fine-outline model either agree or
                    # fail closed. Low live scores still fall through so maps
                    # without labeled exemplars retain legacy coverage.
                    result = {
                        "success": False,
                        "reason": "early-live-evidence-defers-legacy",
                        "mapId": None,
                        "floor": selected_floor,
                        "confidence": 0.0,
                        "margin": 0.0,
                        "selectedFloor": selected_floor,
                        "markedEvidence": None,
                        "anchor": (early.get("candidates") or [{}])[0].get("anchor"),
                        "anchors": anchors,
                        "candidates": [],
                        "legacyReferenceRankingDeferred": True,
                        "_earlyLiveEvidence": early,
                    }
                    if include_query_features:
                        result["_queryFeatures"] = query_features
                    return result
            query_center = central_descriptor(context)
            inferred_position = {
                "right": "侧门在左", "left": "侧门在右",
                "down": "侧门在上", "up": "侧门在下",
            }.get(query_topology.get("approach"), "")
            # A partially explored live map does not expose a stable global side-door
            # direction.  Panning, fog and navigation arrows can make the strongest
            # visible arm change between two views of the same entrance.  Keep the
            # inferred label for diagnostics only; never use it to remove candidates.
            floor_references = [
                reference for reference in self.references
                if selected_floor is None or reference.floor == selected_floor
            ]
            references = floor_references
            for reference in references:
                searched_reference_ids.add(reference.reference_id)
                local = (
                    descriptor_similarity(query_center, central_descriptor(reference.context))
                    if reference.context is not None else None
                )
                topology_score = topology_similarity(query_topology, reference.context_topology)
                wide_containment = (
                    descriptor_containment(wide_context, reference.wide_context)
                    if reference.wide_context is not None else None
                )
                wide_room_containment = (
                    descriptor_containment(wide_room, reference.wide_room)
                    if reference.wide_room is not None else None
                )
                far_reference = reference.full_context if reference.full_context is not None else reference.far_context
                far_room_reference = reference.full_room if reference.full_room is not None else reference.far_room
                context_containment = (
                    descriptor_containment(full_context, far_reference)
                    if far_reference is not None else None
                )
                room_containment = (
                    descriptor_containment(full_room, far_room_reference)
                    if far_room_reference is not None else None
                )
                # Containment deliberately rewards every additional visible road/room
                # while ignoring reference areas that the player has not explored yet.
                weighted_parts = [(topology_score, 0.12)]
                if local is not None:
                    weighted_parts.append((local, 0.22))
                if wide_containment is not None:
                    weighted_parts.append((wide_containment, 0.15))
                if wide_room_containment is not None:
                    weighted_parts.append((wide_room_containment, 0.15))
                if context_containment is not None:
                    weighted_parts.append((context_containment, 0.18))
                if room_containment is not None:
                    weighted_parts.append((room_containment, 0.18))
                score = sum(value * weight for value, weight in weighted_parts) / sum(
                    weight for _, weight in weighted_parts
                )
                taxonomy = self.taxonomy_by_map.get(reference.map_id, {})
                position_match = bool(
                    inferred_position and taxonomy.get("sideDoorPosition") == inferred_position
                )
                # Direction is weak supporting evidence only.  It must not outweigh
                # the actual entrance outline/topology comparison.
                taxonomy_adjustment = 0.01 if position_match else 0.0
                candidate = {
                    "mapId": reference.map_id, "floor": reference.floor, "referenceId": reference.reference_id,
                    "score": score + taxonomy_adjustment, "rawVisualScore": score,
                    "taxonomyAdjustment": taxonomy_adjustment, "taxonomy": taxonomy,
                    "inferredSideDoorPosition": inferred_position, "taxonomyPositionMatch": position_match,
                    "localScore": local, "topologyScore": topology_score,
                    "wideMapContainmentScore": wide_containment,
                    "wideRoomContainmentScore": wide_room_containment,
                    "fullMapContainmentScore": context_containment, "fullRoomContainmentScore": room_containment,
                    "availableVisualEvidence": [
                        name for name, available in (
                            ("local", local is not None), ("topology", True),
                            ("wideMap", wide_containment is not None),
                            ("wideRoom", wide_room_containment is not None),
                            ("fullMap", context_containment is not None),
                            ("fullRoom", room_containment is not None),
                        ) if available
                    ],
                    "iconScore": anchor["score"], "anchor": {k: anchor[k] for k in ("x", "y", "width", "height")},
                    "queryTopology": query_topology, "referenceTopology": reference.context_topology,
                }
                current = by_map.get(reference.map_id)
                if current is None or candidate["score"] > current["score"]:
                    by_map[reference.map_id] = candidate
        ranked = sorted(by_map.values(), key=lambda x: x["score"], reverse=True)[:10]
        if not ranked:
            return {"success": False, "reason": "side-door-context-insufficient", "anchors": anchors, "candidates": []}
        top = ranked[0]
        margin = top["score"] - (ranked[1]["score"] if len(ranked) > 1 else 0.0)
        class_scores: dict[str, float] = {}
        for candidate in by_map.values():
            taxonomy = candidate.get("taxonomy", {})
            class_key = f'{taxonomy.get("shapeCategory", "未分类")}|{taxonomy.get("sideDoorPosition", "未分类")}'
            class_scores[class_key] = max(class_scores.get(class_key, 0.0), candidate["score"])
        ranked_classes = sorted(class_scores.items(), key=lambda item: item[1], reverse=True)
        top_class = ranked_classes[0][0] if ranked_classes else ""
        class_margin = (
            ranked_classes[0][1] - ranked_classes[1][1] if len(ranked_classes) > 1 else ranked_classes[0][1]
        ) if ranked_classes else 0.0
        topology_agrees = top["queryTopology"].get("rayApproach") == top["referenceTopology"].get("rayApproach")
        base_success = bool(
            top["rawVisualScore"] >= 0.68 and top["iconScore"] >= 0.70
            and (top["localScore"] or 0.0) >= 0.58
            and top["topologyScore"] >= 0.94 and topology_agrees
            and top.get("taxonomyPositionMatch", False) and margin >= 0.04 and class_margin >= 0.04
        )
        marked_agrees = bool(
            marked_evidence and marked_evidence.get("success")
            and marked_evidence.get("mapId") == top["mapId"]
        )
        success = base_success and (marked_agrees if marked_evidence is not None else True)
        if success:
            reason = "accepted-cross-validated-entrance" if marked_evidence is not None else "accepted-side-door-topology"
        elif marked_evidence is not None and marked_evidence.get("success") and not marked_agrees:
            reason = "retrieval-evidence-disagreement"
        elif marked_evidence is not None and not marked_evidence.get("success"):
            reason = "marked-entrance-unconfirmed"
        else:
            reason = "side-door-low-margin"
        confidence = min(0.99, max(0.0,
            top["rawVisualScore"] * 0.60
            + min(margin / 0.12, 1.0) * 0.20
            + min(class_margin / 0.12, 1.0) * 0.20
        ))
        if marked_evidence is not None:
            confidence = min(confidence, float(marked_evidence.get("confidence", 0.0)))
        result = {
            "success": success, "reason": reason,
            "mapId": top["mapId"], "floor": top["floor"], "confidence": confidence, "margin": margin,
            "humanClass": top_class, "humanClassMargin": class_margin,
            "humanClassRanking": [{"class": key, "score": score} for key, score in ranked_classes[:5]],
            "searchedReferenceCount": len(searched_reference_ids),
            "totalReferenceCount": len(self.references),
            "selectedFloor": selected_floor,
            "markedEvidence": marked_evidence,
            "crossEvidenceAgreement": marked_agrees if marked_evidence is not None else None,
            "anchor": top["anchor"], "anchors": anchors, "candidates": ranked,
        }
        if include_query_features:
            result["_queryFeatures"] = query_features
        return result
