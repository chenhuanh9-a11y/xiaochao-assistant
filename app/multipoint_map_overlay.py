from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from full_map_retrieval import FullMapRetriever


ROOT = Path(__file__).resolve().parents[1]
ASSETS = Path(os.environ.get("IDENTITYV_LEGACY_ASSETS_ROOT", str(ROOT / "assets" / "legacy")))
PROFILE_REGISTRY = ROOT / "references" / "overlay-calibration-profiles.json"
PRESENTATION_REGISTRY = ROOT / "references" / "overlay-presentation.json"
LIVE_ACCEPTANCE_REGISTRY = ROOT / "references" / "multipoint-live-acceptance.json"


def _read_image(path: Path, flags: int) -> np.ndarray | None:
    try:
        data = np.fromfile(path, dtype=np.uint8)
    except OSError:
        return None
    return cv2.imdecode(data, flags)


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


class MultipointMapOverlay:
    """Known-identity map registration using distributed structural points.

    The geometry fit may produce a review candidate for any registered map.
    Actual live display remains separately gated by human acceptance.
    """

    def __init__(self) -> None:
        profiles = _json(PROFILE_REGISTRY).get("profiles", [])
        self.profiles = {
            (item.get("mapId"), int(item.get("floor", 0))): item
            for item in profiles
            if item.get("mapId") and int(item.get("floor", 0)) in (1, 2)
        }
        self.presentation = _json(PRESENTATION_REGISTRY)
        acceptance = _json(LIVE_ACCEPTANCE_REGISTRY).get("entries", [])
        self.acceptance = {
            (item.get("mapId"), int(item.get("floor", 0))): item
            for item in acceptance
            if item.get("mapId") and int(item.get("floor", 0)) in (1, 2)
        }
        manifest = _json(ASSETS / "maps" / "manifest.json")
        self.map_meta = {item["id"]: item for item in manifest.get("maps", [])}
        self.sift = cv2.SIFT_create(
            nfeatures=5000, contrastThreshold=0.012, edgeThreshold=14
        )
        self.matcher = cv2.BFMatcher(cv2.NORM_L2)

    @lru_cache(maxsize=12)
    def _reference_features(
        self, map_id: str, floor: int
    ) -> tuple[tuple, np.ndarray | None, np.ndarray] | None:
        meta = self.map_meta.get(map_id)
        image = _read_image(ASSETS / "maps" / f"{map_id}.webp", cv2.IMREAD_COLOR)
        if meta is None or image is None:
            return None
        canonical = FullMapRetriever._floor_image(image, meta, floor)
        registration = FullMapRetriever._registration_image(
            canonical, screenshot=False
        )
        keypoints, descriptors = self.sift.detectAndCompute(registration, None)
        return tuple(keypoints), descriptors, registration

    @staticmethod
    def _geometry_agreement(
        reference_registration: np.ndarray,
        live_registration: np.ndarray,
        reference_to_live_crop: np.ndarray,
    ) -> dict:
        """Check that the fitted map agrees beyond a few repeated corridors."""
        warped = cv2.warpAffine(
            reference_registration,
            reference_to_live_crop,
            (live_registration.shape[1], live_registration.shape[0]),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0,
        )
        reference_edges = cv2.Canny(warped, 70, 150) > 0
        live_edges = cv2.Canny(live_registration, 70, 150) > 0
        if not np.any(reference_edges) or not np.any(live_edges):
            return {
                "geometryAccepted": False,
                "liveEdgeCount": int(live_edges.sum()),
                "liveEdgeWithin4": 0.0,
                "liveEdgeWithin8": 0.0,
                "liveEdgeMedianDistancePx": 999.0,
            }
        distance = cv2.distanceTransform(
            (~reference_edges).astype(np.uint8), cv2.DIST_L2, 5
        )
        values = distance[live_edges]
        within4 = float(np.mean(values <= 4.0))
        within8 = float(np.mean(values <= 8.0))
        return {
            "geometryAccepted": False,
            "liveEdgeCount": int(len(values)),
            "liveEdgeWithin4": within4,
            "liveEdgeWithin8": within8,
            "liveEdgeMedianDistancePx": float(np.median(values)),
        }

    @lru_cache(maxsize=2)
    def _masked_source(
        self, map_id: str, floor: int, route_family: str
    ) -> tuple[np.ndarray, np.ndarray, str] | None:
        profile = self.profiles.get((map_id, floor))
        if profile is None:
            return None
        version = (
            profile.get("coordinateEvidence", {})
            .get("versions", {})
            .get(route_family)
        )
        if not version or not version.get("matrixSourceToReference"):
            return None
        pattern = self.presentation.get("displayAssetPattern")
        if not pattern:
            return None
        relative = pattern.format(
            mapId=map_id, floor=floor, routeFamily=route_family
        )
        path = ROOT / Path(relative)
        source = _read_image(path, cv2.IMREAD_UNCHANGED)
        if source is None or source.ndim != 3 or source.shape[2] != 4:
            return None
        alpha = source[:, :, 3]
        ys, xs = np.where(alpha > 0)
        if not len(xs):
            return None
        pad = 2
        x0, x1 = max(0, int(xs.min()) - pad), min(source.shape[1], int(xs.max()) + pad + 1)
        y0, y1 = max(0, int(ys.min()) - pad), min(source.shape[0], int(ys.max()) + pad + 1)
        cropped = np.ascontiguousarray(source[y0:y1, x0:x1])
        source_to_reference = np.asarray(
            version["matrixSourceToReference"], dtype=np.float64
        )
        crop_to_source = np.asarray(
            [[1.0, 0.0, x0], [0.0, 1.0, y0], [0.0, 0.0, 1.0]],
            dtype=np.float64,
        )
        return cropped, source_to_reference @ crop_to_source, str(path.resolve())

    def _fit_reference_to_live(
        self, frame: np.ndarray, map_id: str, floor: int
    ) -> tuple[np.ndarray | None, dict]:
        reference = self._reference_features(map_id, floor)
        if reference is None:
            return None, {"success": False, "reason": "multipoint-reference-unavailable"}
        reference_points, reference_descriptors, reference_registration = reference
        if reference_descriptors is None or len(reference_points) < 8:
            return None, {"success": False, "reason": "multipoint-reference-features-insufficient"}

        height, width = frame.shape[:2]
        crop_x = round(width * 0.28)
        crop_y = round(height * 0.09)
        query = FullMapRetriever._registration_image(frame, screenshot=True)
        query_points, query_descriptors = self.sift.detectAndCompute(query, None)
        if query_descriptors is None or len(query_points) < 8:
            return None, {"success": False, "reason": "multipoint-live-features-insufficient"}
        def estimate(matches: list, *, partial: bool) -> tuple[np.ndarray | None, dict]:
            mode = "mutual-partial" if partial else "one-way-strict"
            if len(matches) < (4 if partial else 6):
                return None, {
                    "success": False,
                    "reason": "multipoint-common-features-insufficient",
                    "matchingMode": mode,
                    "goodMatches": len(matches),
                }
            source = np.float32(
                [reference_points[item.queryIdx].pt for item in matches]
            )
            target = np.float32(
                [query_points[item.trainIdx].pt for item in matches]
            )
            matrix, inlier_mask = cv2.estimateAffinePartial2D(
                source,
                target,
                method=cv2.RANSAC,
                ransacReprojThreshold=5.0 if partial else 4.0,
                maxIters=30000 if partial else 10000,
                confidence=0.999,
                refineIters=50 if partial else 20,
            )
            if matrix is None or inlier_mask is None:
                return None, {
                    "success": False,
                    "reason": "multipoint-ransac-unavailable",
                    "matchingMode": mode,
                    "goodMatches": len(matches),
                }
            keep = inlier_mask.ravel().astype(bool)
            inliers = int(keep.sum())
            ratio = inliers / len(matches)
            source_inliers = source[keep]
            target_inliers = target[keep]
            predicted = cv2.transform(
                source_inliers.reshape(-1, 1, 2), matrix
            ).reshape(-1, 2)
            errors = np.linalg.norm(predicted - target_inliers, axis=1)
            a, b = float(matrix[0, 0]), float(matrix[0, 1])
            scale = float(np.hypot(a, b))
            rotation = float(np.degrees(np.arctan2(-b, a)))
            source_span = (
                float(np.linalg.norm(np.ptp(source_inliers, axis=0)))
                if inliers else 0.0
            )
            target_span = (
                float(np.linalg.norm(np.ptp(target_inliers, axis=0)))
                if inliers else 0.0
            )
            median_error = float(np.median(errors)) if len(errors) else 999.0
            p95_error = float(np.percentile(errors, 95)) if len(errors) else 999.0
            if partial:
                # A partially explored map may expose only a handful of unique
                # rooms. Mutual descriptor agreement prevents the many-to-one
                # corridor matches that previously collapsed scale to zero.
                sane = bool(
                    inliers >= 5
                    and ratio >= 0.04
                    and median_error <= 3.0
                    and p95_error <= 5.5
                    and 0.65 <= scale <= 3.4
                    and abs(rotation) <= 3.0
                    and source_span >= 40.0
                    and target_span >= 60.0
                )
            else:
                sane = bool(
                    inliers >= 10
                    and ratio >= 0.055
                    and median_error <= 2.5
                    and p95_error <= 5.0
                    and 0.75 <= scale <= 2.2
                    and abs(rotation) <= 3.0
                    and source_span >= 70.0
                    and target_span >= 90.0
                )
            evidence = {
                "success": sane,
                "reason": (
                    "partial-multipoint-fit-accepted"
                    if sane and partial
                    else "multipoint-fit-accepted"
                    if sane
                    else "multipoint-fit-below-threshold"
                ),
                "matchingMode": mode,
                "goodMatches": len(matches),
                "inliers": inliers,
                "inlierRatio": float(ratio),
                "medianErrorPx": median_error,
                "p95ErrorPx": p95_error,
                "scale": scale,
                "rotationDeg": rotation,
                "referenceSpanPx": source_span,
                "liveSpanPx": target_span,
            }
            return (matrix.astype(np.float64) if sane else None), evidence

        forward_pairs = self.matcher.knnMatch(
            reference_descriptors, query_descriptors, k=2
        )
        strict_matches = [
            left for left, right in forward_pairs
            if left.distance < 0.74 * right.distance
        ]
        matrix, strict_evidence = estimate(strict_matches, partial=False)
        evidence = strict_evidence
        if matrix is None:
            reverse_pairs = self.matcher.knnMatch(
                query_descriptors, reference_descriptors, k=2
            )
            forward = {
                (left.queryIdx, left.trainIdx): left
                for left, right in forward_pairs
                if left.distance < 0.80 * right.distance
            }
            reverse = {
                (left.trainIdx, left.queryIdx): left
                for left, right in reverse_pairs
                if left.distance < 0.80 * right.distance
            }
            mutual_matches = [
                match for pair, match in forward.items() if pair in reverse
            ]
            matrix, evidence = estimate(mutual_matches, partial=True)
            evidence["strictAttempt"] = strict_evidence
        if matrix is None:
            return None, evidence
        geometry = self._geometry_agreement(
            reference_registration, query, matrix
        )
        if evidence.get("matchingMode") == "mutual-partial":
            geometry_accepted = bool(
                geometry["liveEdgeWithin4"] >= 0.30
                and geometry["liveEdgeWithin8"] >= 0.45
            )
        else:
            geometry_accepted = bool(
                geometry["liveEdgeWithin4"] >= 0.25
                and geometry["liveEdgeWithin8"] >= 0.38
            )
        geometry["geometryAccepted"] = geometry_accepted
        evidence.update(geometry)
        evidence["reviewEligible"] = bool(
            geometry_accepted
            and float(evidence.get("referenceSpanPx") or 0.0) >= 120.0
            and float(evidence.get("liveSpanPx") or 0.0) >= 180.0
        )
        if not geometry_accepted:
            evidence["success"] = False
            evidence["reason"] = "multipoint-geometry-disagreement"
            return None, evidence
        matrix[0, 2] += crop_x
        matrix[1, 2] += crop_y
        return np.vstack([matrix, [0.0, 0.0, 1.0]]), evidence

    def build(
        self,
        frame: np.ndarray,
        map_id: str,
        floor: int | None,
        *,
        route_family: str = "speed",
        opacity: float = 1.0,
    ) -> tuple[np.ndarray | None, dict]:
        if floor not in (1, 2) or (map_id, int(floor)) not in self.profiles:
            return None, {"success": False, "reason": "multipoint-profile-unavailable"}
        floor = int(floor)
        reference_to_live, fit = self._fit_reference_to_live(frame, map_id, floor)
        acceptance = self.acceptance.get((map_id, floor), {})
        display_allowed = bool(
            acceptance.get("enabled")
            and acceptance.get("alignmentStatus") == "verified-live"
            and acceptance.get("materialPolicy") == "mask-only-preserve-all-content"
        )
        if reference_to_live is None:
            return None, {
                **fit,
                "mapId": map_id,
                "floor": floor,
                "displayAllowed": False,
                "runtimePromotionAllowed": False,
            }
        source = self._masked_source(map_id, floor, route_family)
        if source is None:
            return None, {
                **fit,
                "success": False,
                "reason": "multipoint-masked-source-unavailable",
                "mapId": map_id,
                "floor": floor,
                "displayAllowed": False,
                "runtimePromotionAllowed": False,
            }
        layer, source_to_reference, source_path = source
        source_to_live = reference_to_live @ source_to_reference
        height, width = frame.shape[:2]
        warped = cv2.warpPerspective(
            layer,
            source_to_live,
            (width, height),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(0, 0, 0, 0),
        )
        warped[:, :, 3] = np.clip(
            warped[:, :, 3].astype(np.float32) * float(opacity), 0, 255
        ).astype(np.uint8)
        return warped, {
            **fit,
            "mapId": map_id,
            "mapName": self.profiles[(map_id, floor)].get("mapName", map_id),
            "floor": floor,
            "routeFamily": route_family,
            "routeFamiliesShareGeometry": True,
            "model": (
                "known-identity-SIFT-mutual-RANSAC-partial-similarity"
                if fit.get("matchingMode") == "mutual-partial"
                else "known-identity-SIFT-RANSAC-similarity"
            ),
            "sourceAssetPolicy": "mask-only-preserve-all-content",
            "sourceAsset": source_path,
            "referenceToLiveMatrix": reference_to_live.tolist(),
            "sourceToLiveMatrix": source_to_live.tolist(),
            "displayAllowed": display_allowed,
            "displayDecision": (
                "verified-live-profile"
                if display_allowed else "candidate-saved-pending-human-review"
            ),
            "runtimePromotionAllowed": display_allowed,
            "acceptanceProfile": acceptance.get("profileId"),
        }


_ENGINE: MultipointMapOverlay | None = None


def build_multipoint_overlay(
    frame: np.ndarray,
    map_id: str,
    floor: int | None,
    *,
    route_family: str = "speed",
    opacity: float = 1.0,
) -> tuple[np.ndarray | None, dict]:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = MultipointMapOverlay()
    return _ENGINE.build(
        frame, map_id, floor, route_family=route_family, opacity=opacity
    )
