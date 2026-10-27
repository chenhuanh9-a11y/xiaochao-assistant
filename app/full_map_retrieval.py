from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


DESCRIPTOR_SIZE = 128
CHANNEL_WEIGHTS = np.asarray([0.34, 0.34, 0.12, 0.20], np.float32)
SHIFTS = ((-6, -6), (0, -6), (6, -6), (-6, 0), (0, 0), (6, 0), (-6, 6), (0, 6), (6, 6))


def structural_channels(image: np.ndarray) -> np.ndarray:
    value = image.astype(np.int16)
    blue, green, red = cv2.split(value)
    mean = value.mean(axis=2)
    chroma = value.max(axis=2) - value.min(axis=2)
    valid = (mean >= 43) & (mean <= 232) & (chroma <= 112)
    blue_floor = valid & (blue >= red + 5) & (blue >= green - 8)
    brown_floor = valid & (red >= blue + 5) & (red >= green - 12)
    neutral = (mean >= 72) & (mean <= 235) & (chroma <= 34)
    annotation = (
        ((red >= green + 45) & (red >= blue + 45))
        | ((green >= red + 42) & (green >= blue + 20))
        | ((blue >= red + 35) & (green >= red + 35))
        | ((red >= blue + 45) & (green >= blue + 35) & (mean >= 95))
    )
    blue_floor &= ~annotation
    brown_floor &= ~annotation
    neutral &= ~annotation
    base = (blue_floor | brown_floor | neutral).astype(np.uint8) * 255
    base = cv2.morphologyEx(base, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 48, 112)
    edges &= cv2.dilate(base, np.ones((7, 7), np.uint8))
    edges[annotation] = 0
    return np.stack([
        blue_floor.astype(np.uint8),
        brown_floor.astype(np.uint8),
        neutral.astype(np.uint8),
        (edges > 0).astype(np.uint8),
    ])


def _remove_small_components(channels: np.ndarray, minimum_area: int) -> np.ndarray:
    base = (channels[:3].max(axis=0) > 0).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(base, 8)
    keep = np.zeros_like(base)
    for index in range(1, count):
        if int(stats[index, cv2.CC_STAT_AREA]) >= minimum_area:
            keep[labels == index] = 1
    result = channels.copy()
    result[:, keep == 0] = 0
    return result


def normalized_descriptor(image: np.ndarray, screenshot: bool) -> tuple[np.ndarray | None, dict]:
    height, width = image.shape[:2]
    if screenshot:
        # The map canvas occupies the central area. This removes the game view,
        # party list, fixed legend, zoom buttons, taskbar and floor buttons.
        x0, x1 = round(width * 0.28), round(width * 0.90)
        y0, y1 = round(height * 0.09), round(height * 0.91)
        source = image[y0:y1, x0:x1]
        minimum_area = max(40, round(source.size / 3 * 0.000035))
    else:
        x0 = y0 = 0
        source = image
        minimum_area = max(20, round(source.size / 3 * 0.000025))
    channels = _remove_small_components(structural_channels(source), minimum_area)
    occupied = channels[:3].max(axis=0) > 0
    occupied_pixels = int(occupied.sum())
    if occupied_pixels < 250:
        return None, {"occupiedPixels": occupied_pixels, "reason": "map-evidence-insufficient"}
    ys, xs = np.where(occupied)
    bx0, bx1 = int(xs.min()), int(xs.max()) + 1
    by0, by1 = int(ys.min()), int(ys.max()) + 1
    crop = channels[:, by0:by1, bx0:bx1]
    crop_height, crop_width = crop.shape[1:]
    scale = min((DESCRIPTOR_SIZE - 8) / crop_width, (DESCRIPTOR_SIZE - 8) / crop_height)
    target_width = max(1, round(crop_width * scale))
    target_height = max(1, round(crop_height * scale))
    descriptor = np.zeros((4, DESCRIPTOR_SIZE, DESCRIPTOR_SIZE), np.uint8)
    dx = (DESCRIPTOR_SIZE - target_width) // 2
    dy = (DESCRIPTOR_SIZE - target_height) // 2
    for channel in range(4):
        resized = cv2.resize(crop[channel], (target_width, target_height), interpolation=cv2.INTER_AREA)
        descriptor[channel, dy:dy + target_height, dx:dx + target_width] = resized >= 0.25
    return descriptor, {
        "occupiedPixels": occupied_pixels,
        "sourceRegion": [x0, y0, source.shape[1], source.shape[0]],
        "bounds": [x0 + bx0, y0 + by0, crop_width, crop_height],
        "aspectRatio": crop_width / max(1, crop_height),
        "descriptorPixels": [int(descriptor[channel].sum()) for channel in range(4)],
        "reason": "ok",
    }


def shift_channels(channels: np.ndarray, dx: int, dy: int) -> np.ndarray:
    matrix = np.float32([[1, 0, dx], [0, 1, dy]])
    return np.stack([
        cv2.warpAffine(channel, matrix, (DESCRIPTOR_SIZE, DESCRIPTOR_SIZE), flags=cv2.INTER_NEAREST)
        for channel in channels
    ])


@dataclass
class FullMapReference:
    map_id: str
    map_name: str
    floor: int
    descriptor: np.ndarray
    metadata: dict
    registration_image: np.ndarray
    keypoints: tuple
    keypoint_descriptors: np.ndarray | None


class FullMapRetriever:
    def __init__(self, assets: Path, enable_registration: bool = False) -> None:
        self.assets = assets
        self.sift = (
            cv2.SIFT_create(nfeatures=1800, contrastThreshold=0.015, edgeThreshold=12)
            if enable_registration else None
        )
        manifest = json.loads((assets / "maps" / "manifest.json").read_text(encoding="utf-8"))
        self.references: list[FullMapReference] = []
        for meta in manifest.get("maps", []):
            if meta.get("manualOnly"):
                continue
            image = cv2.imread(str(assets / "maps" / f'{meta["id"]}.webp'), cv2.IMREAD_COLOR)
            if image is None:
                continue
            floors = meta.get("floors") or [1]
            for floor in floors:
                part = self._floor_image(image, meta, int(floor))
                descriptor, evidence = normalized_descriptor(part, screenshot=False)
                if descriptor is not None:
                    if self.sift is not None:
                        registration = self._registration_image(part, screenshot=False)
                        keypoints, keypoint_descriptors = self.sift.detectAndCompute(registration, None)
                    else:
                        registration = np.empty((0, 0), np.uint8)
                        keypoints, keypoint_descriptors = (), None
                    self.references.append(FullMapReference(
                        meta["id"], meta.get("name", meta["id"]), int(floor), descriptor, evidence,
                        registration, keypoints, keypoint_descriptors,
                    ))
        self.shifted = np.stack([
            np.stack([shift_channels(reference.descriptor, dx, dy) for reference in self.references])
            for dx, dy in SHIFTS
        ]).astype(bool)

    @staticmethod
    def _floor_image(full: np.ndarray, meta: dict, floor: int) -> np.ndarray:
        height = full.shape[0]
        explicit = meta.get("floorBounds", {}).get(str(floor))
        if explicit:
            left, top = int(explicit["left"]), int(explicit["top"])
            return full[top:top + int(explicit["height"]), left:left + int(explicit["width"])]
        if meta.get("floorLayout") == "single-map":
            return full
        return full[:height // 2] if floor == 1 else full[height // 2:]

    @staticmethod
    def _registration_image(image: np.ndarray, screenshot: bool) -> np.ndarray:
        if screenshot:
            height, width = image.shape[:2]
            image = image[
                round(height * 0.09):round(height * 0.91),
                round(width * 0.28):round(width * 0.90),
            ]
            minimum_area = max(40, round(image.size / 3 * 0.000035))
        else:
            minimum_area = max(20, round(image.size / 3 * 0.000025))
        channels = _remove_small_components(structural_channels(image), minimum_area)
        registration = np.maximum.reduce([
            channels[0] * 90,
            channels[1] * 165,
            channels[2] * 225,
            channels[3] * 255,
        ]).astype(np.uint8)
        return cv2.GaussianBlur(registration, (3, 3), 0.6)

    def recognize_registration(self, frame: np.ndarray, selected_floor: int | None) -> dict:
        if self.sift is None:
            return {"success": False, "reason": "full-map-registration-disabled", "candidates": []}
        query = self._registration_image(frame, screenshot=True)
        query_keypoints, query_descriptors = self.sift.detectAndCompute(query, None)
        if query_descriptors is None or len(query_keypoints) < 8:
            return {"success": False, "reason": "full-map-registration-features-insufficient", "candidates": []}
        candidates = []
        matcher = cv2.BFMatcher(cv2.NORM_L2)
        for reference in self.references:
            if selected_floor is not None and reference.floor != selected_floor:
                continue
            if reference.keypoint_descriptors is None or len(reference.keypoint_descriptors) < 8:
                continue
            pairs = matcher.knnMatch(reference.keypoint_descriptors, query_descriptors, k=2)
            good = [left for left, right in pairs if left.distance < 0.74 * right.distance]
            if len(good) < 6:
                continue
            source = np.float32([reference.keypoints[item.queryIdx].pt for item in good])
            target = np.float32([query_keypoints[item.trainIdx].pt for item in good])
            matrix, inlier_mask = cv2.estimateAffinePartial2D(
                source, target, method=cv2.RANSAC, ransacReprojThreshold=5.0,
                maxIters=2500, confidence=0.995,
            )
            if matrix is None or inlier_mask is None:
                continue
            keep = inlier_mask.ravel().astype(bool)
            inliers = int(keep.sum())
            if inliers < 4:
                continue
            linear = matrix[:, :2]
            singular = np.linalg.svd(linear, compute_uv=False)
            scale = float(singular.mean())
            source_inliers = source[keep]
            target_inliers = target[keep]
            source_span = np.ptp(source_inliers, axis=0)
            target_span = np.ptp(target_inliers, axis=0)
            spatial_span = float(np.linalg.norm(target_span))
            spread = float(np.linalg.norm(source_span))
            projected = cv2.transform(source_inliers.reshape(-1, 1, 2), matrix).reshape(-1, 2)
            errors = np.linalg.norm(projected - target_inliers, axis=1)
            ratio = inliers / len(good)
            sane = bool(
                0.35 <= scale <= 6.0 and singular[0] / max(singular[-1], 1e-6) <= 1.08
                and spread >= 70.0 and spatial_span >= 90.0
            )
            median_error = float(np.median(errors))
            score = (
                inliers + 10.0 * ratio + 0.025 * min(spread, 500.0)
                + 0.02 * min(spatial_span, 800.0) - median_error
            )
            candidates.append({
                "mapId": reference.map_id,
                "mapName": reference.map_name,
                "floor": reference.floor,
                "score": score,
                "inliers": inliers,
                "inlierRatio": ratio,
                "goodMatches": len(good),
                "medianError": median_error,
                "scale": scale,
                "referenceSpan": spread,
                "querySpan": spatial_span,
                "sane": sane,
                "matrixReferenceToQuery": matrix.tolist(),
            })
        candidates.sort(key=lambda item: (item["sane"], item["score"]), reverse=True)
        if not candidates:
            return {"success": False, "reason": "full-map-registration-not-found", "candidates": []}
        top = candidates[0]
        sane_candidates = [item for item in candidates if item["sane"]]
        if sane_candidates:
            top = sane_candidates[0]
            runner_score = sane_candidates[1]["score"] if len(sane_candidates) > 1 else 0.0
        else:
            runner_score = candidates[1]["score"] if len(candidates) > 1 else 0.0
        margin = top["score"] - runner_score
        success = bool(
            selected_floor is not None and top["sane"] and top["inliers"] >= 10
            and top["inlierRatio"] >= 0.22 and top["medianError"] <= 3.5 and margin >= 8.0
        )
        return {
            "success": success,
            "reason": "accepted-full-map-registration" if success else "full-map-registration-unconfirmed",
            "mapId": top["mapId"],
            "mapName": top["mapName"],
            "floor": top["floor"],
            "score": top["score"],
            "margin": margin,
            "queryKeypoints": len(query_keypoints),
            "candidates": candidates[:8],
        }

    def recognize(self, frame: np.ndarray, selected_floor: int | None) -> dict:
        query, evidence = normalized_descriptor(frame, screenshot=True)
        if query is None:
            return {"success": False, "reason": evidence["reason"], "evidence": evidence, "candidates": []}
        candidate_indices = [
            index for index, reference in enumerate(self.references)
            if selected_floor is None or reference.floor == selected_floor
        ]
        if not candidate_indices:
            return {"success": False, "reason": "floor-reference-not-found", "evidence": evidence, "candidates": []}
        references = self.shifted[:, candidate_indices]
        query_bool = query.astype(bool)[None, None, :, :, :]
        query_dilated = np.stack([
            cv2.dilate(channel.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
            for channel in query
        ])[None, None, :, :, :]
        reference_dilated = np.empty_like(references)
        for shift_index in range(references.shape[0]):
            for reference_index in range(references.shape[1]):
                for channel in range(4):
                    reference_dilated[shift_index, reference_index, channel] = cv2.dilate(
                        references[shift_index, reference_index, channel].astype(np.uint8),
                        np.ones((5, 5), np.uint8),
                    ).astype(bool)
        query_energy = query_bool.sum(axis=(-2, -1)).astype(np.float32)
        reference_energy = references.sum(axis=(-2, -1)).astype(np.float32)
        precision = (query_bool & reference_dilated).sum(axis=(-2, -1)) / np.maximum(1.0, query_energy)
        recall = (references & query_dilated).sum(axis=(-2, -1)) / np.maximum(1.0, reference_energy)
        f1 = 2.0 * precision * recall / np.maximum(1e-6, precision + recall)
        weighted = np.einsum("src,c->sr", f1, CHANNEL_WEIGHTS, optimize=True)
        best_shift = weighted.argmax(axis=0)
        scores = weighted.max(axis=0)
        ranked = []
        for local_index, reference_index in enumerate(candidate_indices):
            reference = self.references[reference_index]
            shift_index = int(best_shift[local_index])
            ranked.append({
                "mapId": reference.map_id,
                "mapName": reference.map_name,
                "floor": reference.floor,
                "score": float(scores[local_index]),
                "shift": list(SHIFTS[shift_index]),
                "referenceAspectRatio": reference.metadata.get("aspectRatio"),
            })
        ranked.sort(key=lambda item: item["score"], reverse=True)
        top = ranked[0]
        margin = top["score"] - (ranked[1]["score"] if len(ranked) > 1 else 0.0)
        aspect_difference = abs(
            math.log(max(1e-6, evidence["aspectRatio"]) / max(1e-6, top["referenceAspectRatio"]))
        )
        # Strict until labelled live samples prove calibration. In particular,
        # an unknown floor is never accepted from shape alone.
        success = bool(
            selected_floor is not None and top["score"] >= 0.58 and margin >= 0.08
            and aspect_difference <= 0.30
        )
        return {
            "success": success,
            "reason": "accepted-full-map-shape" if success else "full-map-shape-unconfirmed",
            "mapId": top["mapId"],
            "mapName": top["mapName"],
            "floor": top["floor"],
            "score": top["score"],
            "margin": margin,
            "aspectDifference": aspect_difference,
            "evidence": evidence,
            "candidates": ranked[:8],
        }
