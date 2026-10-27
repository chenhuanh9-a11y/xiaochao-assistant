from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
from PIL import Image, ImageDraw

from front_door_layout_recognition import (
    LAYOUT_LABELS,
    NightmareFrontDoorLayoutRecognizer,
)
from front_door_recognition import (
    NightmareFrontDoorRecognizer,
    canonical_front_door_mask,
)
from nightmare_candidate import (
    NightmareCandidateEngine,
    NightmarePack,
    map_color_mask,
    visible_map_component,
)
from front_door_groups import FrontDoorGroupIndex, FrontDoorGroupState


SOURCE_ROOT = Path(__file__).resolve().parents[1]
ROOT = Path(getattr(sys, "_MEIPASS", SOURCE_ROOT))
DEFAULT_CLEAN_PREVIEWS = ROOT / "references" / "nightmare-front-door-clean-previews.json"
DEFAULT_FRONT_DOOR_IDENTITIES = ROOT / "references" / "nightmare-front-door-identities.json"
FRONT_ENTRY_FLOOR = "1"
MINIMUM_LIVE_ANCHOR_SCORE = 0.72
MINIMUM_LIVE_ANCHOR_DISTINCT_MARGIN = 0.08
MINIMUM_LIVE_BOTTOM_POSITION = 0.68
PAIR_04_12 = frozenset({"nightmare-04", "nightmare-12"})
PAIR_04_12_ALLOWED_STAGES = frozenset({
    "first-ring-topology", "second-ring-topology",
})
PAIR_04_12_CANONICAL_SIZE = (264, 246)
PAIR_04_12_WINDOW_UNITS = (-22.0, -41.0, 22.0, 4.0)
PAIR_04_12_ROI = (92, 100, 132, 138)
PAIR_04_MINIMUM_PIXELS = 800
PAIR_12_MAXIMUM_PIXELS = 600
PAIR_MINIMUM_QUERY_PIXELS = 4500
FRONT_GEOMETRY_MINIMUM_SCORE = 85.0
FRONT_GEOMETRY_MINIMUM_MARGIN = 2.0
FRONT_GEOMETRY_MAXIMUM_MEDIAN_PIXELS = 3.0
FRONT_GEOMETRY_MINIMUM_WITHIN_THREE = 0.52
FRONT_GEOMETRY_MINIMUM_EXPLAINED = 0.90
# A one-way residual can make a small, still-ambiguous explored island look
# perfect against several full maps. Keep ranking responsive, but do not turn
# that partial containment into a round lock until enough of the current map
# is visible. The ratio keeps this gate resolution-independent.
FRONT_GEOMETRY_MINIMUM_QUERY_PIXEL_RATIO = 0.01
FRONT_LOCAL_IDENTITY_MINIMUM_SCORE = 85.0
FRONT_LOCAL_IDENTITY_MINIMUM_GROUP_MARGIN = 8.0
FRONT_LOCAL_IDENTITY_MINIMUM_QUERY_PIXELS = 3000
FRONT_LOCAL_IDENTITY_PAIR_MINIMUM_QUERY_PIXELS = 6000
FRONT_SINGLETON_NEAR_DOOR_MINIMUM_SCORE = 80.0
FRONT_SINGLETON_NEAR_DOOR_MINIMUM_MARGIN = 8.0
FRONT_SINGLETON_NEAR_DOOR_STAGES = frozenset({
    "front-left-right-exits", "first-ring-topology", "second-ring-topology",
})
FRONT_TEMPORAL_MAXIMUM_AGE_SECONDS = 45.0
FRONT_TEMPORAL_MAXIMUM_OBSERVATIONS = 6
FRONT_TEMPORAL_ACCEPTED_STAGES = frozenset({
    "first-ring-topology", "second-ring-topology", "entry-room-outline",
    "front-left-right-exits",
})
FRONT_TEMPORAL_MINIMUM_WINS = 4
FRONT_TEMPORAL_MINIMUM_SCORE = 80.0
FRONT_TEMPORAL_MINIMUM_MARGIN = 2.0
FRONT_TEMPORAL_MINIMUM_LAYOUT_MARGIN = 2.0
FRONT_TEMPORAL_MINIMUM_DISTINCT_POSITIONS = 4
FRONT_TEMPORAL_POSITION_SEPARATION_PIXELS = 18.0
# Local cumulative evidence can lock a stable F1 candidate before the legacy
# 4-wins/4-position quorum, without changing the global minimum score gate.
FRONT_TEMPORAL_FAST_MINIMUM_QUALIFIED_OBSERVATIONS = 3
FRONT_TEMPORAL_FAST_MINIMUM_DISTINCT_POSITIONS = 3
FRONT_TEMPORAL_FAST_MINIMUM_QUERY_PIXELS = 7000
FRONT_TEMPORAL_FAST_MINIMUM_QUERY_GROWTH = 300
FRONT_TEMPORAL_FAST_MINIMUM_GROUP_MARGIN = 5.0
FRONT_TEMPORAL_FAST_MINIMUM_AVERAGE_MARGIN = 6.0
FRONT_TEMPORAL_FAST_MINIMUM_ACCUMULATED_EVIDENCE = 2.70
FRONT_POSE_POLICY = "locked-identity-seed+selected-route-current-frame-residual-v1"


def front_entry_scene_evidence(
    frame: np.ndarray,
    engine: NightmareCandidateEngine,
) -> dict:
    """Prove the frame is an entrance scene before using front-door identity.

    F1 is a floor, not an entrance type. A player can reach F1 from F2/B1 or
    reopen it after exploration, so the floor selector alone must never select
    the front-door recognizer (or its pose seed). The explicit scene gate is
    the entrance icon anchored in the lower part of the currently visible map.
    It authorizes only the front-door branch; route pixels still require their
    independent current-frame residual.
    """
    visible = visible_map_component(frame)
    if visible is None:
        return {
            "verified": False,
            "entranceType": "unknown",
            "reason": "front-entry-scene-visible-map-missing",
        }
    _patch, bounds, _component = visible
    x0, y0, x1, y1 = (int(value) for value in bounds)
    map_height = max(1, y1 - y0)
    map_width = max(1, x1 - x0)
    margin = max(24, round(max(map_width, map_height) * 0.08))
    search_bounds = (
        max(0, x0 - margin),
        max(0, round(y0 + map_height * 0.48)),
        min(frame.shape[1], x1 + margin),
        min(frame.shape[0], y1 + margin),
    )
    icon = engine._match_exit_icon(
        frame, search_bounds, np.arange(0.25, 1.81, 0.05),
    )
    if not icon:
        return {
            "verified": False,
            "entranceType": "unknown",
            "reason": "front-entry-scene-anchor-missing",
            "mapBounds": list(bounds),
        }
    anchor_score = float(icon.get("score", 0.0))
    anchor_distinct_margin = float(icon.get("distinctMargin", 0.0))
    bottom_position = (float(icon["center"][1]) - y0) / map_height
    verified = bool(
        anchor_score >= MINIMUM_LIVE_ANCHOR_SCORE
        and anchor_distinct_margin >= MINIMUM_LIVE_ANCHOR_DISTINCT_MARGIN
        and bottom_position >= MINIMUM_LIVE_BOTTOM_POSITION
    )
    return {
        "verified": verified,
        "entranceType": "front-door" if verified else "unknown",
        "reason": (
            "front-entry-scene-explicit-anchor"
            if verified else "front-entry-scene-anchor-not-supported"
        ),
        "mapBounds": list(bounds),
        "searchBounds": list(search_bounds),
        "anchorScore": round(anchor_score, 4),
        "anchorDistinctMargin": round(
            anchor_distinct_margin, 4,
        ),
        "anchorBottomPosition": round(float(bottom_position), 4),
        "anchorCenter": [float(value) for value in icon["center"]],
        "anchorSize": [float(value) for value in icon["size"]],
    }


def corroborate_front_local_identity(
    identity_rows: list[dict],
    layout_recognizer: NightmareFrontDoorLayoutRecognizer,
    *,
    layout_candidate: str,
    stage: str,
    query_pixels: int,
    pair_evidence: dict | None = None,
) -> dict:
    """Apply the front-door identity card inside one door-layout group.

    This mirrors the side-door direction-first flow: the coarse door layout
    selects a small pool, then the entrance-relative local terrain decides
    inside that pool. A reviewed special difference box remains an extra
    discriminator, not a veto over an otherwise unique local identity.
    """
    group_ids = tuple(
        layout_recognizer.layout_maps.get(str(layout_candidate), ())
    )
    by_id = {
        str(row.get("mapId") or ""): row
        for row in identity_rows
        if str(row.get("mapId") or "")
    }
    group_rows = sorted(
        (by_id[map_id] for map_id in group_ids if map_id in by_id),
        key=lambda row: float(row.get("score", 0.0)),
        reverse=True,
    )
    top = group_rows[0] if group_rows else {}
    second = group_rows[1] if len(group_rows) > 1 else {}
    top_map_id = str(top.get("mapId") or "")
    generic_top_map_id = str((identity_rows[0] if identity_rows else {}).get(
        "mapId", "",
    ))
    score = float(top.get("score", 0.0) or 0.0)
    margin = score - float(second.get("score", 0.0) or 0.0)
    pair = pair_evidence if isinstance(pair_evidence, dict) else {}
    pair_ambiguous = bool(
        pair.get("applicable")
        and not pair.get("confident")
        and frozenset(str(value) for value in pair.get("topTwo", []))
        == PAIR_04_12
    )
    pair_maturity_ready = bool(
        not pair_ambiguous
        or (
            str(stage) == "second-ring-topology"
            and int(query_pixels)
            >= FRONT_LOCAL_IDENTITY_PAIR_MINIMUM_QUERY_PIXELS
        )
    )
    confident = bool(
        len(group_rows) >= 2
        and top_map_id
        and top_map_id == generic_top_map_id
        and str(stage) != "door-anchor-only"
        and score >= FRONT_LOCAL_IDENTITY_MINIMUM_SCORE
        and margin >= FRONT_LOCAL_IDENTITY_MINIMUM_GROUP_MARGIN
        and int(query_pixels) >= FRONT_LOCAL_IDENTITY_MINIMUM_QUERY_PIXELS
        and pair_maturity_ready
    )
    return {
        "attempted": bool(group_rows),
        "confident": confident,
        "candidateMapId": top_map_id or None,
        "reason": (
            "front-layout-group-local-identity-supported"
            if confident else "front-layout-group-local-identity-insufficient"
        ),
        "layoutCandidate": str(layout_candidate),
        "groupMapIds": list(group_ids),
        "groupScores": [
            {
                "mapId": str(row.get("mapId") or ""),
                "score": round(float(row.get("score", 0.0)), 3),
            }
            for row in group_rows
        ],
        "score": round(score, 3),
        "groupMargin": round(margin, 3),
        "minimumScore": FRONT_LOCAL_IDENTITY_MINIMUM_SCORE,
        "minimumGroupMargin": FRONT_LOCAL_IDENTITY_MINIMUM_GROUP_MARGIN,
        "queryPixels": int(query_pixels),
        "minimumQueryPixels": FRONT_LOCAL_IDENTITY_MINIMUM_QUERY_PIXELS,
        "pairAmbiguous": pair_ambiguous,
        "pairMaturityReady": pair_maturity_ready,
        "pairMinimumQueryPixels": (
            FRONT_LOCAL_IDENTITY_PAIR_MINIMUM_QUERY_PIXELS
            if pair_ambiguous else None
        ),
        "routeDisplayAllowed": False,
    }


def front_singleton_near_door_fingerprint(
    identity_rows: list[dict],
    layout_recognizer: NightmareFrontDoorLayoutRecognizer,
    group_index: FrontDoorGroupIndex,
    *,
    candidate_map_id: str,
    layout_candidate: str,
    stage: str,
    anchor_supported: bool,
) -> dict:
    """Recognize a reviewed singleton from anchor-normalized near-door topology.

    The identity scores are computed from the canonical door-anchor crop and
    the current progressive stage. For a singleton, uniqueness is measured
    against the other maps in the same four-way class; no whole-map contour or
    query-pixel quota is required. Reviewed pairs intentionally return false.
    """
    group = group_index.group_for(candidate_map_id)
    class_map_ids = tuple(
        layout_recognizer.layout_maps.get(str(layout_candidate), ())
    )
    by_id = {
        str(row.get("mapId") or ""): row
        for row in identity_rows
        if isinstance(row, dict)
    }
    class_rows = sorted(
        (
            {
                "mapId": map_id,
                "score": float((by_id.get(map_id) or {}).get("score", 0.0)),
            }
            for map_id in class_map_ids
        ),
        key=lambda row: (-row["score"], class_map_ids.index(row["mapId"])),
    )
    top = class_rows[0] if class_rows else {}
    second = class_rows[1] if len(class_rows) > 1 else {}
    score = float(top.get("score", 0.0))
    margin = score - float(second.get("score", 0.0))
    supported = bool(
        anchor_supported
        and group is not None
        and len(group.member_ids) == 1
        and str(stage) in FRONT_SINGLETON_NEAR_DOOR_STAGES
        and str(top.get("mapId") or "") == str(candidate_map_id)
        and str(layout_candidate) == str(group.layout or "")
        and score >= FRONT_SINGLETON_NEAR_DOOR_MINIMUM_SCORE
        and margin >= FRONT_SINGLETON_NEAR_DOOR_MINIMUM_MARGIN
    )
    return {
        "supported": supported,
        "candidateMapId": str(candidate_map_id or "") or None,
        "layoutCandidate": str(layout_candidate),
        "stage": str(stage),
        "score": round(score, 3),
        "sameLayoutMargin": round(margin, 3),
        "sameLayoutMapIds": list(class_map_ids),
        "groupId": group.group_id if group is not None else None,
        "singleton": bool(group is not None and len(group.member_ids) == 1),
        "queryPixelsRequired": False,
        "evidenceSource": "door-anchor-normalized-near-door-topology",
        "routeDisplayAllowed": False,
    }


def front_pose_seed_matrix(pose_seed: dict) -> np.ndarray | None:
    """Project a reviewed source floor from one live front-door observation.

    The recognizer compares two canonical crops whose entrance anchors share a
    fixed origin.  Convert the winning canonical perturbation back into the
    route sheet's pixel coordinates.  This is only a seed: route pixels still
    require the independent selected-route residual before display.
    """
    try:
        source = pose_seed["sourceAnchor"]
        live = pose_seed["liveAnchor"]
        source_center = np.asarray(source["center"], dtype=np.float64)
        live_center = np.asarray(live["center"], dtype=np.float64)
        source_size = float(np.mean(source["size"]))
        live_size = float(np.mean(live["size"]))
        reference_scale = float(pose_seed["referenceScale"])
        reference_dx = float(pose_seed["referenceDx"])
        reference_dy = float(pose_seed["referenceDy"])
    except (KeyError, TypeError, ValueError):
        return None
    if (
        source_center.shape != (2,)
        or live_center.shape != (2,)
        or not np.isfinite(source_center).all()
        or not np.isfinite(live_center).all()
        or not np.isfinite([
            source_size, live_size, reference_scale,
            reference_dx, reference_dy,
        ]).all()
        or source_size <= 0.0
        or live_size <= 0.0
        or reference_scale <= 0.0
    ):
        return None
    scale = reference_scale * live_size / source_size
    if not 0.03 <= scale <= 6.0:
        return None
    # canonical_front_door_mask maps the source/live anchor to y=150, while
    # perturb_mask scales around y=159. Preserve that nine-pixel offset when
    # returning to physical sheet coordinates.
    translation = np.asarray([
        live_center[0] + live_size * reference_dx / 10.0,
        live_center[1] + live_size * (
            9.0 * (1.0 - reference_scale) + reference_dy
        ) / 10.0,
    ]) - source_center * scale
    return np.asarray([
        [scale, 0.0, translation[0]],
        [0.0, scale, translation[1]],
        [0.0, 0.0, 1.0],
    ], dtype=np.float64)


def locked_front_pose_seed_matrix(
    diagnostics: dict | None,
    map_id: str,
    floor: str,
) -> np.ndarray | None:
    """Return a map-agnostic F1 seed without granting display permission.

    Identity, entrance pose and route display are separate decisions.  The
    recognizer may contribute a pose only when its locked map and embedded
    seed name the same selected map.  The seed must explicitly require the
    selected route's current-frame residual and must never authorize pixels by
    itself.  No map identifier or map-specific score is used by this policy.
    """
    evidence = diagnostics if isinstance(diagnostics, dict) else {}
    pose_seed = (
        evidence.get("poseSeed")
        if isinstance(evidence.get("poseSeed"), dict) else {}
    )
    selected = str(map_id or "")
    if not (
        str(floor) == FRONT_ENTRY_FLOOR
        and selected
        and evidence.get("automaticMapLockAllowed") is True
        and str(evidence.get("candidateMapId") or "") == selected
        and str(pose_seed.get("mapId") or "") == selected
        and pose_seed.get("candidateOnly") is True
        and pose_seed.get("currentFrameRouteResidualRequired") is True
        and pose_seed.get("authorizesDirectDisplay") is False
    ):
        return None
    return front_pose_seed_matrix(pose_seed)


def corroborate_front_identity_geometry(
    frame: np.ndarray,
    engine: NightmareCandidateEngine,
    recognizer: NightmareFrontDoorRecognizer,
    *,
    map_id: str,
    identity_row: dict,
    icon: dict,
    stage: str,
    identity_margin: float,
    layout_candidate: str,
    expected_layout: str,
) -> dict:
    """Use the selected clean route as independent identity corroboration.

    This does not search for a different identity and never authorizes route
    pixels. It asks whether the current local winner also explains the wider
    explored map under its entrance-relative pose. The normal display path
    must repeat current-frame route validation after the identity is locked.
    """
    result = {
        "attempted": False,
        "confident": False,
        "mapId": str(map_id or "") or None,
        "reason": "front-geometry-preconditions-not-met",
        "routeDisplayAllowed": False,
    }
    score = float(identity_row.get("score", 0.0) or 0.0)
    if not (
        map_id
        and str(stage) != "door-anchor-only"
        and score >= FRONT_GEOMETRY_MINIMUM_SCORE
        and float(identity_margin) >= FRONT_GEOMETRY_MINIMUM_MARGIN
        and str(layout_candidate) != "unknown"
        and str(layout_candidate) == str(expected_layout)
    ):
        return result
    source_identity = recognizer.identity_rows.get(str(map_id)) or {}
    source_anchor = source_identity.get("frontDoorAnchor") or {}
    pose_seed = {
        "sourceAnchor": {
            "center": list(source_anchor.get("center") or []),
            "size": list(source_anchor.get("size") or []),
        },
        "liveAnchor": {
            "center": list(icon.get("center") or []),
            "size": list(icon.get("size") or []),
        },
        "referenceScale": identity_row.get("referenceScale"),
        "referenceDx": identity_row.get("referenceDx"),
        "referenceDy": identity_row.get("referenceDy"),
    }
    matrix = front_pose_seed_matrix(pose_seed)
    route = engine.route_image(str(map_id), FRONT_ENTRY_FLOOR)
    if matrix is None or route is None:
        return {**result, "attempted": True, "reason": "front-geometry-seed-missing"}
    started = time.perf_counter()
    attempts = []
    for maximum_shift, global_recenter in ((96.0, False), (360.0, True)):
        fit = engine.refine_selected_route_translation_fast(
            frame, route, matrix,
            maximum_shift=maximum_shift,
            global_recenter=global_recenter,
            route_profile_id="wang-qingxin",
        )
        attempts.append(fit or {
            "success": False,
            "reason": "front-geometry-translation-unavailable",
        })
        if isinstance(fit, dict) and fit.get("success"):
            break
    best = max(
        attempts,
        key=lambda row: (
            bool((row.get("selectedRouteCurrentFrameResidual") or {}).get("verified")),
            float((row.get("selectedRouteCurrentFrameResidual") or {}).get(
                "explainedPixelFraction", 0.0,
            )),
            float((row.get("selectedRouteCurrentFrameResidual") or {}).get(
                "withinThreePixels", 0.0,
            )),
            -float((row.get("selectedRouteCurrentFrameResidual") or {}).get(
                "medianPixels", 999.0,
            )),
        ),
        default={},
    )
    residual = best.get("selectedRouteCurrentFrameResidual") or {}
    frame_pixels = max(int(frame.shape[0]) * int(frame.shape[1]), 1)
    query_pixel_ratio = float(residual.get("queryPixels", 0.0)) / frame_pixels
    confident = bool(
        best.get("success")
        and residual.get("verified")
        and query_pixel_ratio >= FRONT_GEOMETRY_MINIMUM_QUERY_PIXEL_RATIO
        and float(residual.get("medianPixels", 999.0))
        <= FRONT_GEOMETRY_MAXIMUM_MEDIAN_PIXELS
        and float(residual.get("withinThreePixels", 0.0))
        >= FRONT_GEOMETRY_MINIMUM_WITHIN_THREE
        and float(residual.get("explainedPixelFraction", 0.0))
        >= FRONT_GEOMETRY_MINIMUM_EXPLAINED
    )
    return {
        **result,
        "attempted": True,
        "confident": confident,
        "reason": (
            "front-identity-current-route-geometry-corroborated"
            if confident else "front-identity-current-route-geometry-rejected"
        ),
        "identityScore": score,
        "identityMargin": float(identity_margin),
        "layoutCandidate": str(layout_candidate),
        "expectedLayout": str(expected_layout),
        "seedScale": float(matrix[0, 0]),
        "searchMode": best.get("searchMode"),
        "fitReason": best.get("reason"),
        "translationResidualVerified": bool(residual.get("verified")),
        "queryPixelRatio": query_pixel_ratio,
        "minimumQueryPixelRatio": FRONT_GEOMETRY_MINIMUM_QUERY_PIXEL_RATIO,
        "selectedRouteCurrentFrameResidual": residual,
        "elapsedMs": round((time.perf_counter() - started) * 1000.0, 2),
        "routeDisplayAllowed": False,
        "requiresIndependentDisplayRevalidation": True,
    }


def _canonical_pair_04_12_mask(
    image: np.ndarray,
    center: tuple[float, float],
    anchor_size: float,
) -> np.ndarray:
    """Normalize the first reviewed 04/12 difference beyond the entry crop."""
    height, width = image.shape[:2]
    cx, cy = (float(value) for value in center)
    ux0, uy0, ux1, uy1 = PAIR_04_12_WINDOW_UNITS
    x0, y0 = round(cx + ux0 * anchor_size), round(cy + uy0 * anchor_size)
    x1, y1 = round(cx + ux1 * anchor_size), round(cy + uy1 * anchor_size)
    source = map_color_mask(image, live=True).astype(np.uint8)
    crop = np.zeros((max(1, y1 - y0), max(1, x1 - x0)), np.uint8)
    sx0, sy0 = max(0, x0), max(0, y0)
    sx1, sy1 = min(width, x1), min(height, y1)
    if sx1 > sx0 and sy1 > sy0:
        crop[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0] = source[
            sy0:sy1, sx0:sx1
        ]
    normalized = cv2.resize(
        crop, PAIR_04_12_CANONICAL_SIZE, interpolation=cv2.INTER_AREA,
    )
    return cv2.morphologyEx(
        (normalized >= 0.28).astype(np.uint8),
        cv2.MORPH_CLOSE,
        np.ones((3, 3), np.uint8),
    )


def disambiguate_front_pair_04_12(
    frame: np.ndarray,
    icon: dict,
    identity_rows: list[dict],
    *,
    stage: str,
    detected_layout: str,
) -> dict:
    """Use the first human-reviewed deep difference only for the 04/12 pair.

    The ordinary entry crop ends before this region and repeatedly ranks 12
    above real 04 frames.  This feature is intentionally asymmetric and
    fail-closed: deep visibility and an exact 04/12 top-two shortlist are
    required, while the 600..800 transition band never authorizes a map.
    """
    top_two = [str(row.get("mapId") or "") for row in identity_rows[:2]]
    preconditions = bool(
        frozenset(top_two) == PAIR_04_12
        and str(stage) in PAIR_04_12_ALLOWED_STAGES
        and str(detected_layout) == "top-left"
    )
    if not preconditions:
        return {
            "applicable": False,
            "confident": False,
            "candidateMapId": None,
            "reason": "pair-04-12-not-applicable",
            "topTwo": top_two,
        }
    query = _canonical_pair_04_12_mask(
        frame,
        tuple(float(value) for value in icon["center"]),
        float(np.mean(icon["size"])),
    )
    x0, y0, x1, y1 = PAIR_04_12_ROI
    shifted_counts = []
    for dx in (-3, 0, 3):
        for dy in (-3, 0, 3):
            shifted = cv2.warpAffine(
                query,
                np.asarray([[1.0, 0.0, dx], [0.0, 1.0, dy]], np.float32),
                (query.shape[1], query.shape[0]),
                flags=cv2.INTER_NEAREST,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=0,
            )
            shifted_counts.append(int(np.count_nonzero(shifted[y0:y1, x0:x1])))
    roi_pixels = int(round(float(np.median(shifted_counts))))
    query_pixels = int(np.count_nonzero(query))
    candidate = None
    if query_pixels >= PAIR_MINIMUM_QUERY_PIXELS:
        if roi_pixels >= PAIR_04_MINIMUM_PIXELS:
            candidate = "nightmare-04"
        elif roi_pixels <= PAIR_12_MAXIMUM_PIXELS:
            candidate = "nightmare-12"
    return {
        "applicable": True,
        "confident": candidate is not None,
        "candidateMapId": candidate,
        "reason": (
            "pair-04-12-difference-supported"
            if candidate is not None else "pair-04-12-difference-ambiguous"
        ),
        "topTwo": top_two,
        "stage": str(stage),
        "detectedLayout": str(detected_layout),
        "queryPixels": query_pixels,
        "minimumQueryPixels": PAIR_MINIMUM_QUERY_PIXELS,
        "roiPixels": roi_pixels,
        "roiPixelSamples": shifted_counts,
        "nightmare04MinimumPixels": PAIR_04_MINIMUM_PIXELS,
        "nightmare12MaximumPixels": PAIR_12_MAXIMUM_PIXELS,
        "transitionBandFailClosed": [
            PAIR_12_MAXIMUM_PIXELS + 1,
            PAIR_04_MINIMUM_PIXELS - 1,
        ],
        "evidenceSource": "reviewed-first-difference-box+two-live-ground-truths",
    }


def _empty_front_rows(pack: NightmarePack, floor: str, reason: str) -> list[dict]:
    return [
        {
            "mapId": item.id,
            "name": item.name,
            "direction": item.direction,
            "doorCount": item.door_count,
            "shape": item.shape,
            "floor": floor,
            "score": 0.0,
            "baseScore": 0.0,
            "memoryConfirmations": 0,
            "sideDoorDirection": None,
            "sideDoorDirectionReliable": False,
            "sideDoorDirectionSupported": False,
            "sideDoorDirectionMargin": 0.0,
            "sideDoorDirectionEvidence": "front-entry-floor",
            "sideDoorStageReason": "front-door-live-test",
            "semanticScore": 0.0,
            "semanticMargin": 0.0,
            "identityHardGatePassed": False,
            "identityEvidence": reason,
            "strongSuggestion": False,
            "advisoryOnly": True,
            "displayAllowed": False,
            "frontDoorLiveTest": True,
            "frontDoorLayout": "unknown",
            "frontDoorLayoutLabel": "门型未知",
        }
        for item in pack.maps
    ]


def rank_front_entry_frame(
    frame: np.ndarray,
    engine: NightmareCandidateEngine,
    recognizer: NightmareFrontDoorRecognizer,
    layout_recognizer: NightmareFrontDoorLayoutRecognizer,
    *,
    limit: int = 13,
    entrance_scene_evidence: dict | None = None,
    group_index: FrontDoorGroupIndex | None = None,
) -> tuple[list[dict], dict]:
    """Rank an explicitly proven front-entry frame without authorizing pixels.

    The caller must first pass :func:`front_entry_scene_evidence`; F1 alone is
    never entrance evidence. The entrance icon supplies the coordinate origin.
    A reliable singleton near-door fingerprint may lock immediately; reviewed
    pairs still lock only the pair group and keep member choice provisional.
    """
    visible = visible_map_component(frame)
    if visible is None:
        reason = "front-visible-map-missing"
        return _empty_front_rows(engine.pack, FRONT_ENTRY_FLOOR, reason), {
            "success": False,
            "reason": reason,
            "automaticMapLockAllowed": False,
            "routeDisplayAllowed": False,
        }
    _, bounds, _ = visible
    x0, y0, x1, y1 = (int(value) for value in bounds)
    map_height = max(1, y1 - y0)
    map_width = max(1, x1 - x0)
    margin = max(24, round(max(map_width, map_height) * 0.08))
    search_bounds = (
        max(0, x0 - margin),
        max(0, round(y0 + map_height * 0.48)),
        min(frame.shape[1], x1 + margin),
        min(frame.shape[0], y1 + margin),
    )
    scene = (
        entrance_scene_evidence
        if isinstance(entrance_scene_evidence, dict) else {}
    )
    scene_center = scene.get("anchorCenter")
    scene_size = scene.get("anchorSize")
    icon = (
        {
            "center": tuple(float(value) for value in scene_center),
            "size": tuple(float(value) for value in scene_size),
            "score": float(scene.get("anchorScore", 0.0)),
            "distinctMargin": float(
                scene.get("anchorDistinctMargin", 0.0)
            ),
        }
        if scene.get("verified") is True
        and isinstance(scene_center, (list, tuple))
        and len(scene_center) == 2
        and isinstance(scene_size, (list, tuple))
        and len(scene_size) == 2
        else engine._match_exit_icon(
            frame, search_bounds, np.arange(0.25, 1.81, 0.05),
        )
    )
    if not icon:
        reason = "front-exit-anchor-missing"
        return _empty_front_rows(engine.pack, FRONT_ENTRY_FLOOR, reason), {
            "success": False,
            "reason": reason,
            "mapBounds": list(bounds),
            "automaticMapLockAllowed": False,
            "routeDisplayAllowed": False,
        }
    anchor_score = float(icon.get("score", 0.0))
    anchor_margin = float(icon.get("distinctMargin", 0.0))
    bottom_position = (float(icon["center"][1]) - y0) / map_height
    anchor_supported = bool(
        anchor_score >= MINIMUM_LIVE_ANCHOR_SCORE
        and anchor_margin >= MINIMUM_LIVE_ANCHOR_DISTINCT_MARGIN
        and bottom_position >= MINIMUM_LIVE_BOTTOM_POSITION
    )
    query = canonical_front_door_mask(
        frame,
        tuple(float(value) for value in icon["center"]),
        float(np.mean(icon["size"])),
        live=True,
        include_bounds=tuple(bounds),
    )
    identity = recognizer.rank_normalized_mask(query, limit=13)
    identity_rows = list(identity.get("candidates") or [])
    identity_query_pixels = int(np.count_nonzero(query))
    layout = layout_recognizer.rank_candidates(
        identity_rows,
        stage=str(identity.get("stage") or "door-anchor-only"),
    )
    generic_top_map_id = str(identity.get("candidateMapId") or "")
    layout_candidate = str(layout.get("layoutCandidate") or "unknown")
    detected_layout = str(layout.get("detectedLayout") or "unknown")
    pair_04_12 = disambiguate_front_pair_04_12(
        frame,
        icon,
        identity_rows,
        stage=str(identity.get("stage") or "door-anchor-only"),
        detected_layout=detected_layout,
    )
    pair_top_map_id = str(pair_04_12.get("candidateMapId") or "")
    pair_supported = bool(pair_04_12.get("confident") and pair_top_map_id)
    local_identity = corroborate_front_local_identity(
        identity_rows,
        layout_recognizer,
        layout_candidate=layout_candidate,
        stage=str(identity.get("stage") or "door-anchor-only"),
        query_pixels=identity_query_pixels,
        pair_evidence=pair_04_12,
    )
    local_identity_supported = bool(local_identity.get("confident"))
    top_map_id = pair_top_map_id if pair_supported else generic_top_map_id
    by_id = {str(row.get("mapId")): row for row in identity_rows}
    top_source = by_id.get(top_map_id) or {}
    expected_layout = str(
        layout_recognizer.map_layouts.get(top_map_id) or "unknown"
    )
    layout_supported = bool(
        detected_layout != "unknown"
        and top_map_id in (layout.get("candidateMapIds") or [])
    )
    group_index = group_index or FrontDoorGroupIndex()
    singleton_fingerprint = front_singleton_near_door_fingerprint(
        identity_rows,
        layout_recognizer,
        group_index,
        candidate_map_id=generic_top_map_id,
        layout_candidate=layout_candidate,
        stage=str(identity.get("stage") or "door-anchor-only"),
        anchor_supported=anchor_supported,
    )
    singleton_near_door_supported = bool(
        singleton_fingerprint.get("supported")
    )
    group_competition = group_index.compete(
        identity_rows,
        stage=str(identity.get("stage") or "door-anchor-only"),
        anchor_supported=anchor_supported,
    )
    group_member_map_id = str(
        group_competition.get("provisionalMemberId") or generic_top_map_id
    )
    # A reviewed difference ROI may override the best generic member, but it
    # must remain inside the winning group.  It never changes group identity.
    if pair_supported and pair_top_map_id:
        pair_group = group_index.group_for(pair_top_map_id)
        if pair_group is not None and pair_group.group_id == group_competition.get("groupId"):
            group_member_map_id = pair_top_map_id
    geometry_corroboration = {
        "attempted": False,
        "confident": False,
        "mapId": top_map_id or None,
        "reason": "front-geometry-not-needed",
        "routeDisplayAllowed": False,
    }
    ordinary_identity_lock = bool(
        layout_supported
        and (identity.get("strongSuggestion") or pair_supported)
    )
    identity_lock_ready = bool(
        ordinary_identity_lock
        or local_identity_supported
        or singleton_near_door_supported
    )
    if anchor_supported and not identity_lock_ready:
        geometry_corroboration = corroborate_front_identity_geometry(
            frame, engine, recognizer,
            map_id=top_map_id,
            identity_row=top_source,
            icon=icon,
            stage=str(identity.get("stage") or "door-anchor-only"),
            identity_margin=float(identity.get("margin", 0.0)),
            layout_candidate=layout_candidate,
            expected_layout=expected_layout,
        )
    geometry_supported = bool(geometry_corroboration.get("confident"))
    automatic_lock = bool(
        anchor_supported
        and (
            group_competition.get("locked")
            or identity_lock_ready
            or geometry_supported
        )
    )
    group_locked = bool(anchor_supported and group_competition.get("locked"))
    if group_locked:
        top_map_id = group_member_map_id
        top_source = by_id.get(top_map_id) or {}
    group_layout = str(
        layout_recognizer.map_layouts.get(group_member_map_id) or "unknown"
    ) if group_locked else "unknown"
    # A reviewed group carries a known front-door topology even when the
    # layout recognizer cannot separate its members in this frame.  This is
    # enough to bind the shared front anchor seed; current-frame geometry
    # validation still remains the independent display gate.
    if group_locked and group_layout != "unknown":
        layout_candidate = group_layout
        detected_layout = group_layout
    elif singleton_near_door_supported:
        # The four-way class is still recorded as the first-level category,
        # but a singleton's reviewed near-door topology is independently
        # sufficient. Do not wait for entry-room or outer-contour pixels.
        detected_layout = layout_candidate
    runner_up = (
        next(
            (row for row in identity_rows if str(row.get("mapId")) != top_map_id),
            None,
        )
        if pair_supported else
        next(
            (
                row for row in identity_rows
                if str(row.get("mapId") or "")
                in set(local_identity.get("groupMapIds") or [])
                and str(row.get("mapId") or "") != top_map_id
            ),
            None,
        )
        if local_identity_supported else
        identity_rows[1] if len(identity_rows) > 1 else None
    )
    source_identity = recognizer.identity_rows.get(top_map_id) or {}
    source_anchor = source_identity.get("frontDoorAnchor") or {}
    pose_seed = {
        "mapId": top_map_id or None,
        "sourceAnchor": {
            "center": list(source_anchor.get("center") or []),
            "size": list(source_anchor.get("size") or []),
        },
        "liveAnchor": {
            "center": [float(value) for value in icon["center"]],
            "size": [float(value) for value in icon["size"]],
        },
        "referenceScale": top_source.get("referenceScale"),
        "referenceDx": top_source.get("referenceDx"),
        "referenceDy": top_source.get("referenceDy"),
        "candidateOnly": True,
        "currentFrameRouteResidualRequired": True,
        "authorizesDirectDisplay": False,
    }
    generic_top_score = float(
        (by_id.get(generic_top_map_id) or {}).get("score", 0.0)
    )
    rows = []
    for item in engine.pack.maps:
        source = by_id.get(item.id) or {}
        generic_score = float(source.get("score", 0.0))
        score = (
            max(generic_score, generic_top_score + 0.01)
            if pair_supported and item.id == top_map_id else generic_score
        )
        is_top = item.id == top_map_id
        rows.append({
            "mapId": item.id,
            "name": item.name,
            "direction": item.direction,
            "doorCount": item.door_count,
            "shape": item.shape,
            "floor": FRONT_ENTRY_FLOOR,
            "score": round(score, 3),
            "baseScore": round(generic_score, 3),
            "inliers": 0,
            "goodMatches": 0,
            "inlierRatio": 0.0,
            "medianError": 0.0,
            "memoryConfirmations": 0,
            "memoryBoost": 0.0,
            "sideDoorDirection": None,
            "sideDoorDirectionReliable": False,
            "sideDoorDirectionSupported": False,
            "sideDoorDirectionMargin": 0.0,
            "sideDoorDirectionEvidence": "front-entry-floor",
            "sideDoorStageReason": "front-door-live-test",
            "semanticScore": round(score / 100.0, 4),
            "semanticMargin": round(float(identity.get("margin", 0.0)) / 100.0, 4),
            "identityHardGatePassed": bool(is_top and automatic_lock),
            "identityEvidence": (
                "front-door-pair-04-12+layout-current-frame"
                if is_top and automatic_lock and pair_supported else
                "front-door-layout-group+local-identity-current-frame"
                if is_top and automatic_lock and local_identity_supported else
                "front-door-identity+singleton-near-door-topology"
                if is_top and automatic_lock and singleton_near_door_supported else
                "front-door-identity+selected-route-geometry-current-frame"
                if is_top and automatic_lock and geometry_supported else
                "front-door-identity+layout-current-frame"
                if is_top and automatic_lock else
                "front-door-current-frame-insufficient"
            ),
            "identityRunnerUpMapId": (
                str((runner_up or {}).get("mapId") or "") if is_top else None
            ),
            "identityScoreMargin": (
                round(float(identity.get("margin", 0.0)), 3) if is_top else 0.0
            ),
            "identityBaseMargin": (
                round(float(identity.get("margin", 0.0)), 3) if is_top else 0.0
            ),
            "identitySemanticMargin": (
                round(float(identity.get("margin", 0.0)) / 100.0, 4)
                if is_top else 0.0
            ),
            "identityBudgetExceeded": False,
            "identityElapsedMs": 0.0,
            "strongSuggestion": bool(is_top and automatic_lock),
            "advisoryOnly": True,
            "displayAllowed": False,
            "frontDoorLiveTest": True,
            "frontDoorStage": str(identity.get("stage") or "door-anchor-only"),
            "frontDoorAnchorScore": round(anchor_score, 4),
            "frontDoorAnchorDistinctMargin": round(anchor_margin, 4),
            "frontDoorBottomPosition": round(float(bottom_position), 4),
            "frontDoorAnchorSupported": anchor_supported,
            "frontDoorLayout": (
                group_layout
                if is_top and group_layout != "unknown" else
                layout_candidate
                if is_top and (local_identity_supported or geometry_supported)
                else detected_layout
            ),
            "frontDoorLayoutCandidate": str(layout.get("layoutCandidate") or "unknown"),
            "frontDoorLayoutLabel": (
                str(LAYOUT_LABELS.get(group_layout, group_layout))
                if is_top and group_layout != "unknown" else
                str(LAYOUT_LABELS.get(layout_candidate, layout_candidate))
                if is_top and (local_identity_supported or geometry_supported) else
                str(layout.get("label") or "门型未知")
            ),
            "frontDoorLayoutScore": round(float(layout.get("score", 0.0)), 3),
            "frontDoorLayoutMargin": round(float(layout.get("margin", 0.0)), 3),
            "frontDoorLayoutMapIds": list(layout.get("candidateMapIds") or []),
            "frontDoorGroupLayout": group_layout,
            "frontDoorGenericCandidateMapId": generic_top_map_id,
            "frontDoorPair0412Applicable": bool(pair_04_12.get("applicable")),
            "frontDoorPair0412Confident": pair_supported,
            "frontDoorPair0412CandidateMapId": pair_top_map_id or None,
            "frontDoorPair0412Reason": pair_04_12.get("reason"),
            "frontDoorPair0412QueryPixels": pair_04_12.get("queryPixels"),
            "frontDoorPair0412RoiPixels": pair_04_12.get("roiPixels"),
            "frontDoorPair0412Evidence": pair_04_12,
            "frontDoorLocalIdentity": local_identity,
            "frontDoorGroupId": group_competition.get("groupId"),
            "frontDoorGroupState": "GROUP_LOCKED" if group_locked else "UNLOCKED",
            "frontDoorProvisionalMemberId": (
                group_member_map_id if group_locked else None
            ),
            "frontDoorGroupScore": group_competition.get("score"),
            "frontDoorGroupMargin": group_competition.get("margin"),
            "frontDoorGroupCompetition": group_competition,
            "frontDoorSingletonNearDoorSupported": bool(
                is_top and singleton_near_door_supported
            ),
            "frontDoorSingletonNearDoorFingerprint": (
                singleton_fingerprint if is_top else None
            ),
        })
    rows.sort(key=lambda row: float(row.get("score", 0.0)), reverse=True)
    diagnostics = {
        "success": True,
        "reason": (
            "front-live-singleton-near-door-lock-ready"
            if automatic_lock and singleton_near_door_supported else
            "front-live-auto-lock-ready"
            if automatic_lock else "front-live-manual-fallback"
        ),
        "mapBounds": list(bounds),
        "anchor": {
            "center": [round(float(value), 2) for value in icon["center"]],
            "size": [round(float(value), 2) for value in icon["size"]],
            "score": round(anchor_score, 4),
            "distinctMargin": round(anchor_margin, 4),
            "bottomPosition": round(float(bottom_position), 4),
            "supported": anchor_supported,
        },
        "stage": str(identity.get("stage") or "door-anchor-only"),
        "candidateMapId": top_map_id or None,
        "genericCandidateMapId": generic_top_map_id or None,
        "score": round(float((by_id.get(top_map_id) or {}).get("score", 0.0)), 3),
        "margin": round(float(identity.get("margin", 0.0)), 3),
        "layoutCandidate": str(layout.get("layoutCandidate") or "unknown"),
        "detectedLayout": (
            layout_candidate
            if (local_identity_supported or geometry_supported)
            else detected_layout
        ),
        "layoutLabel": (
            str(LAYOUT_LABELS.get(group_layout, group_layout))
            if group_layout != "unknown" else
            str(LAYOUT_LABELS.get(layout_candidate, layout_candidate))
            if (local_identity_supported or geometry_supported)
            else str(layout.get("label") or "门型未知")
        ),
        "layoutMargin": round(float(layout.get("margin", 0.0)), 3),
        "frontGroupLayout": group_layout,
        "automaticMapLockAllowed": automatic_lock,
        "frontGroupLocked": group_locked,
        "frontGroupId": group_competition.get("groupId") if group_locked else None,
        "frontGroupProvisionalMemberId": group_member_map_id if group_locked else None,
        "frontGroupCompetition": group_competition,
        "singletonNearDoorSupported": singleton_near_door_supported,
        "singletonNearDoorFingerprint": singleton_fingerprint,
        "pair0412": pair_04_12,
        "localIdentity": local_identity,
        "geometryCorroboration": geometry_corroboration,
        "poseSeed": pose_seed,
        "routeDisplayAllowed": False,
        "boundary": (
            "Identity may lock the map; route pixels still require the daily "
            "assistant's independent current-frame alignment validation."
        ),
    }
    return rows[: max(1, int(limit))], diagnostics


def fuse_front_temporal_identity(
    current_rows: list[dict],
    history: list[dict] | None,
    diagnostics: dict,
    *,
    now: float | None = None,
) -> tuple[list[dict], list[dict], dict]:
    """Promote repeated, spatially distinct F1 evidence to an identity lock.

    A single high-confidence frame keeps the existing immediate-lock path.
    The temporal paths are deliberately narrow: deep second-ring frames with
    a supported entrance anchor, a stable layout candidate, and a clear
    current-map lead can vote. Reopening an unchanged frame cannot manufacture
    evidence because spatial separation remains mandatory.
    Route pixels remain controlled by the independent daily alignment gate.
    """
    rows = [dict(row) for row in current_rows if isinstance(row, dict)]
    diag = dict(diagnostics or {})
    timestamp = time.monotonic() if now is None else float(now)
    kept = []
    for observation in history or []:
        try:
            age = timestamp - float(observation.get("at"))
        except (TypeError, ValueError):
            continue
        if 0.0 <= age <= FRONT_TEMPORAL_MAXIMUM_AGE_SECONDS:
            kept.append(dict(observation))

    top = rows[0] if rows else {}
    if bool(diag.get("automaticMapLockAllowed")):
        diag["temporalConsensus"] = {
            "eligible": False,
            "reason": "single-frame-lock-already-ready",
            "historyObservations": len(kept),
            "automaticMapLockAllowed": True,
        }
        return rows, [], diag

    candidate_map_id = str(diag.get("candidateMapId") or "")
    layout_candidate = str(diag.get("layoutCandidate") or "unknown")
    stage = str(diag.get("stage") or "")
    anchor = diag.get("anchor") if isinstance(diag.get("anchor"), dict) else {}
    center = anchor.get("center") if isinstance(anchor.get("center"), list) else []
    layout_map_ids = {
        str(value) for value in (top.get("frontDoorLayoutMapIds") or [])
    }
    score = float(diag.get("score", 0.0) or 0.0)
    identity_margin = float(diag.get("margin", 0.0) or 0.0)
    layout_margin = float(diag.get("layoutMargin", 0.0) or 0.0)
    observation_eligible = bool(
        rows
        and str(top.get("mapId") or "") == candidate_map_id
        and candidate_map_id
        and stage in FRONT_TEMPORAL_ACCEPTED_STAGES
        and bool(anchor.get("supported"))
        and len(center) == 2
        and layout_candidate != "unknown"
        and candidate_map_id in layout_map_ids
        and score >= FRONT_TEMPORAL_MINIMUM_SCORE
        and identity_margin >= FRONT_TEMPORAL_MINIMUM_MARGIN
        and layout_margin >= FRONT_TEMPORAL_MINIMUM_LAYOUT_MARGIN
    )
    if observation_eligible:
        local_identity = top.get("frontDoorLocalIdentity")
        if not isinstance(local_identity, dict):
            local_identity = {}
        group_competition = diag.get("frontGroupCompetition")
        if not isinstance(group_competition, dict):
            group_competition = {}
        group_margin = float(
            local_identity.get(
                "groupMargin",
                diag.get("groupMargin", group_competition.get("margin", 0.0)),
            ) or 0.0
        )
        kept.append({
            "at": timestamp,
            "mapId": candidate_map_id,
            "layoutCandidate": layout_candidate,
            "anchorCenter": [float(center[0]), float(center[1])],
            "score": score,
            "identityMargin": identity_margin,
            "layoutMargin": layout_margin,
            "groupMargin": group_margin,
            "queryPixels": int(
                local_identity.get("queryPixels", diag.get("queryPixels", 0))
                or 0
            ),
            "pairAmbiguous": bool(
                local_identity.get("pairAmbiguous")
                or (
                    top.get("frontDoorPair0412Applicable")
                    and not top.get("frontDoorPair0412Confident")
                )
            ),
        })
    kept = kept[-FRONT_TEMPORAL_MAXIMUM_OBSERVATIONS:]

    votes: dict[str, int] = {}
    for observation in kept:
        map_id = str(observation.get("mapId") or "")
        if map_id:
            votes[map_id] = votes.get(map_id, 0) + 1
    wins = int(votes.get(candidate_map_id, 0))
    competing_wins = max(
        (count for map_id, count in votes.items() if map_id != candidate_map_id),
        default=0,
    )
    recent_window = kept[-FRONT_TEMPORAL_FAST_MINIMUM_QUALIFIED_OBSERVATIONS:]
    recent_competing_wins = sum(
        1 for observation in recent_window
        if str(observation.get("mapId") or "") != candidate_map_id
    )
    matching = [
        observation for observation in kept
        if str(observation.get("mapId") or "") == candidate_map_id
        and str(observation.get("layoutCandidate") or "") == layout_candidate
    ]
    distinct_centers: list[np.ndarray] = []
    for observation in matching:
        candidate_center = np.asarray(
            observation.get("anchorCenter") or [], dtype=np.float64,
        )
        if candidate_center.shape != (2,) or not np.isfinite(candidate_center).all():
            continue
        if all(
            float(np.linalg.norm(candidate_center - previous))
            >= FRONT_TEMPORAL_POSITION_SEPARATION_PIXELS
            for previous in distinct_centers
        ):
            distinct_centers.append(candidate_center)
    qualified = [
        observation for observation in matching
        if (
            not observation.get("pairAmbiguous")
            and int(observation.get("queryPixels", 0))
            >= FRONT_TEMPORAL_FAST_MINIMUM_QUERY_PIXELS
            and float(observation.get("groupMargin", 0.0))
            >= FRONT_TEMPORAL_FAST_MINIMUM_GROUP_MARGIN
        )
    ]
    qualified_centers: list[np.ndarray] = []
    for observation in qualified:
        candidate_center = np.asarray(
            observation.get("anchorCenter") or [], dtype=np.float64,
        )
        if candidate_center.shape != (2,) or not np.isfinite(candidate_center).all():
            continue
        if all(
            float(np.linalg.norm(candidate_center - previous))
            >= FRONT_TEMPORAL_POSITION_SEPARATION_PIXELS
            for previous in qualified_centers
        ):
            qualified_centers.append(candidate_center)
    accumulated_evidence = 0.0
    for observation in qualified:
        query_factor = min(
            1.25,
            float(observation.get("queryPixels", 0))
            / float(FRONT_TEMPORAL_FAST_MINIMUM_QUERY_PIXELS),
        )
        margin_factor = min(
            1.50,
            float(observation.get("groupMargin", 0.0))
            / float(FRONT_TEMPORAL_FAST_MINIMUM_AVERAGE_MARGIN),
        )
        layout_factor = min(
            1.25,
            float(observation.get("layoutMargin", 0.0))
            / float(FRONT_TEMPORAL_MINIMUM_LAYOUT_MARGIN * 2.0),
        )
        accumulated_evidence += (
            0.45 * query_factor + 0.35 * margin_factor + 0.20 * layout_factor
        )
    qualified_margins = [
        float(observation.get("groupMargin", 0.0)) for observation in qualified
    ]
    average_group_margin = (
        sum(qualified_margins) / len(qualified_margins)
        if qualified_margins else 0.0
    )
    query_values = [int(observation.get("queryPixels", 0)) for observation in qualified]
    query_growth = max(query_values) - min(query_values) if query_values else 0
    fast_temporal_lock = bool(
        observation_eligible
        and wins >= FRONT_TEMPORAL_FAST_MINIMUM_QUALIFIED_OBSERVATIONS
        and recent_competing_wins == 0
        and len(qualified) >= FRONT_TEMPORAL_FAST_MINIMUM_QUALIFIED_OBSERVATIONS
        and len(qualified_centers) >= FRONT_TEMPORAL_FAST_MINIMUM_DISTINCT_POSITIONS
        and accumulated_evidence >= FRONT_TEMPORAL_FAST_MINIMUM_ACCUMULATED_EVIDENCE
        and average_group_margin >= FRONT_TEMPORAL_FAST_MINIMUM_AVERAGE_MARGIN
        and query_growth >= FRONT_TEMPORAL_FAST_MINIMUM_QUERY_GROWTH
    )
    legacy_temporal_lock = bool(
        observation_eligible
        and wins >= FRONT_TEMPORAL_MINIMUM_WINS
        and wins >= competing_wins + 2
        and len(distinct_centers) >= FRONT_TEMPORAL_MINIMUM_DISTINCT_POSITIONS
    )
    temporal_lock = bool(fast_temporal_lock or legacy_temporal_lock)
    lock_reason = (
        "front-temporal-accumulated-unique-structure"
        if fast_temporal_lock and not legacy_temporal_lock else
        "front-temporal-consensus" if temporal_lock else None
    )
    first_qualified_at = min(
        (float(observation.get("at", timestamp)) for observation in qualified),
        default=timestamp,
    )
    temporal_meta = {
        "eligible": observation_eligible,
        "reason": (
            "front-temporal-accumulated-unique-structure"
            if fast_temporal_lock else
            "front-temporal-consensus-ready"
            if temporal_lock else
            "front-temporal-current-frame-ineligible"
            if not observation_eligible else
            "front-temporal-consensus-building"
        ),
        "historyObservations": len(kept),
        "votes": votes,
        "candidateMapId": candidate_map_id or None,
        "layoutCandidate": layout_candidate,
        "wins": wins,
        "competingWins": competing_wins,
        "recentCompetingWins": recent_competing_wins,
        "distinctPositions": len(distinct_centers),
        "minimumWins": FRONT_TEMPORAL_MINIMUM_WINS,
        "minimumDistinctPositions": FRONT_TEMPORAL_MINIMUM_DISTINCT_POSITIONS,
        "positionSeparationPixels": FRONT_TEMPORAL_POSITION_SEPARATION_PIXELS,
        "automaticMapLockAllowed": temporal_lock,
        "accumulatedDiscriminativeEvidence": round(accumulated_evidence, 4),
        "qualifiedObservations": len(qualified),
        "qualifiedDistinctPositions": len(qualified_centers),
        "qualifiedAverageGroupMargin": round(average_group_margin, 4),
        "queryPixelsGrowth": int(query_growth),
        "fastPathEligible": bool(
            observation_eligible
            and wins >= FRONT_TEMPORAL_FAST_MINIMUM_QUALIFIED_OBSERVATIONS
            and recent_competing_wins == 0
        ),
        "lockReason": lock_reason,
        "timeToLockMs": (
            round(max(0.0, timestamp - first_qualified_at) * 1000.0, 1)
            if temporal_lock else None
        ),
    }
    if temporal_lock:
        label = str(LAYOUT_LABELS.get(layout_candidate, layout_candidate))
        rows[0]["identityHardGatePassed"] = True
        rows[0]["strongSuggestion"] = True
        rows[0]["identityEvidence"] = (
            "front-door-identity+layout-accumulated-unique-structure"
            if fast_temporal_lock else
            "front-door-identity+layout-temporal-consensus"
        )
        rows[0]["frontDoorTemporalConsensus"] = True
        rows[0]["frontDoorTemporalConsensusFrames"] = wins
        rows[0]["frontDoorTemporalDistinctPositions"] = len(distinct_centers)
        rows[0]["frontDoorAccumulatedDiscriminativeEvidence"] = round(
            accumulated_evidence, 4,
        )
        rows[0]["frontDoorTemporalLockReason"] = lock_reason
        rows[0]["frontDoorTemporalTimeToLockMs"] = temporal_meta["timeToLockMs"]
        rows[0]["frontDoorLayoutCurrentFrame"] = str(
            rows[0].get("frontDoorLayout") or "unknown"
        )
        rows[0]["frontDoorLayout"] = layout_candidate
        rows[0]["frontDoorLayoutLabel"] = label
        diag["reason"] = (
            "front-live-accumulated-unique-structure-lock-ready"
            if fast_temporal_lock else "front-live-temporal-lock-ready"
        )
        diag["automaticMapLockAllowed"] = True
        diag["currentFrameDetectedLayout"] = str(
            diag.get("detectedLayout") or "unknown"
        )
        diag["detectedLayout"] = layout_candidate
        diag["layoutLabel"] = label
    diag["temporalConsensus"] = temporal_meta
    return rows, kept, diag


class FrontDoorLiveTestEngine:
    """Delegate normally; enable front identity only for an entrance scene."""

    def __init__(
        self,
        base: NightmareCandidateEngine,
        log_callback: Callable[..., None] | None = None,
        diagnostic_root: Path | None = None,
    ) -> None:
        self.base = base
        self.pack = base.pack
        self.recognizer = NightmareFrontDoorRecognizer(self.pack)
        self.layout_recognizer = NightmareFrontDoorLayoutRecognizer(
            self.pack, identity_recognizer=self.recognizer,
        )
        self.log_callback = log_callback
        self.diagnostic_root = (
            Path(diagnostic_root) if diagnostic_root is not None else None
        )
        self.front_pose_failure_saved: set[str] = set()
        self.last_front_diagnostics: dict | None = None
        self.front_temporal_history: list[dict] = []
        self.front_group_index = FrontDoorGroupIndex()
        self.front_group_state = FrontDoorGroupState()
        self.front_group_events: list[dict] = []

    def __getattr__(self, name: str):
        return getattr(self.base, name)

    def _save_front_pose_failure(
        self, frame: np.ndarray, map_id: str,
    ) -> str | None:
        if (
            self.diagnostic_root is None
            or map_id in self.front_pose_failure_saved
            or frame is None
        ):
            return None
        try:
            self.diagnostic_root.mkdir(parents=True, exist_ok=True)
            path = self.diagnostic_root / (
                f"{time.strftime('%Y%m%d-%H%M%S')}-{map_id}.jpg"
            )
            encoded, payload = cv2.imencode(
                ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 95],
            )
            if not encoded:
                return None
            path.write_bytes(payload.tobytes())
            self.front_pose_failure_saved.add(map_id)
            return str(path)
        except OSError:
            return None

    def _align_selected_front_pose_fast(
        self, frame: np.ndarray, map_id: str, floor: str,
    ) -> dict | None:
        scene = front_entry_scene_evidence(frame, self.base)
        if str(floor) != FRONT_ENTRY_FLOOR or not scene.get("verified"):
            if self.log_callback is not None and str(floor) == FRONT_ENTRY_FLOOR:
                self.log_callback(
                    "front-pose-scene-gate-rejected",
                    mapId=map_id, floor=floor,
                    entranceSceneEvidence=scene,
                    routeDisplayAllowed=False,
                )
            return None
        diagnostics = (
            self.last_front_diagnostics
            if isinstance(self.last_front_diagnostics, dict) else {}
        )
        matrix = locked_front_pose_seed_matrix(diagnostics, map_id, floor)
        route = self.base.route_image(map_id, floor)
        if matrix is None or route is None:
            return None
        attempts = []
        # Front-door evidence chooses the entrance branch, not a broad search
        # policy. F1 remains bounded to the reviewed pose neighborhood.
        for maximum_shift, global_recenter in ((96.0, False), (180.0, False)):
            refined = self.base.refine_selected_route_translation_fast(
                frame, route, matrix,
                maximum_shift=maximum_shift,
                global_recenter=global_recenter,
                route_profile_id="wang-qingxin",
            )
            attempts.append(refined or {
                "success": False,
                "reason": "front-pose-translation-unavailable",
            })
            if not isinstance(refined, dict) or not refined.get("success"):
                continue
            result_matrix = np.asarray(
                refined["matrixSourceToClient"], dtype=np.float64,
            )
            scale = float(np.sqrt(abs(np.linalg.det(result_matrix[:2, :2]))))
            result = {
                **refined,
                "success": True,
                "reason": "aligned-front-door-pose-current-frame",
                "alignmentMode": "front-door-pose+route-residual",
                "searchMode": (
                    "front-door-pose-global-recenter"
                    if global_recenter else "front-door-pose-local-refine"
                ),
                "mapId": str(map_id),
                "plausibleGeometry": True,
                "scale": scale,
                "anisotropy": 1.0,
                "inliers": 0,
                "inlierRatio": 0.0,
                "medianError": float(
                    (refined.get("selectedRouteCurrentFrameResidual") or {})
                    .get("medianPixels", 0.0)
                ),
                "frontDoorPoseSeedUsed": True,
                "frontDoorPosePolicy": FRONT_POSE_POLICY,
                "currentFrameDisplayValidated": True,
                "requiresSelectedRouteCurrentFrameResidual": True,
            }
            if self.log_callback is not None:
                self.log_callback(
                    "front-pose-alignment-finished",
                    mapId=map_id, floor=floor, success=True,
                    reason=result["reason"], searchMode=result["searchMode"],
                    seedScale=float(matrix[0, 0]), finalScale=scale,
                    frontDoorPosePolicy=FRONT_POSE_POLICY,
                    selectedRouteCurrentFrameResidual=result.get(
                        "selectedRouteCurrentFrameResidual"
                    ),
                    routeDisplayStillRequiresFinalSharedGate=True,
                )
            return result
        failure = attempts[-1] if attempts else {}
        saved_path = self._save_front_pose_failure(frame, str(map_id))
        if self.log_callback is not None:
            self.log_callback(
                "front-pose-alignment-finished",
                mapId=map_id, floor=floor, success=False,
                reason=failure.get("reason"),
                seedScale=float(matrix[0, 0]),
                frontDoorPosePolicy=FRONT_POSE_POLICY,
                selectedRouteCurrentFrameResidual=failure.get(
                    "selectedRouteCurrentFrameResidual"
                ),
                diagnosticFrame=saved_path,
                exhaustiveConfirmedMapFallbackRequired=True,
                routeDisplayAllowed=False,
            )
        return None

    def align_selected_fast(
        self, frame: np.ndarray, map_id: str, floor: str,
    ) -> dict | None:
        front_fit = self._align_selected_front_pose_fast(
            frame, map_id, floor,
        )
        if front_fit is not None:
            return front_fit
        return self.base.align_selected_fast(frame, map_id, floor)

    def front_group_difference_is_applicable(self, map_id: str, floor: str) -> bool:
        """Share static applicability between scheduler and actual sensor."""
        group = self.front_group_index.group_for(map_id)
        return bool(
            str(floor) == FRONT_ENTRY_FLOOR
            and group is not None
            and len(group.member_ids) >= 2
            and len(tuple(getattr(group, "aligned_window", ()) or ())) == 4
            and group.difference_boxes
        )

    def front_group_difference_probe(
        self,
        frame: np.ndarray,
        map_id: str,
        floor: str,
        matrix_source_to_client: np.ndarray,
    ) -> dict:
        """Compare positive, member-specific geometry in the reviewed window.

        This is a bounded, geometry-only sensor.  It never ranks identities,
        and an empty/unexplored box is deliberately non-actionable.
        """
        group = self.front_group_index.group_for(map_id)
        aligned_window = tuple(getattr(group, "aligned_window", ()) or ())
        if not (
            isinstance(frame, np.ndarray)
            and self.front_group_difference_is_applicable(map_id, floor)
        ):
            return {
                "attempted": False,
                "confident": False,
                "reason": "front-group-difference-not-applicable",
            }
        sibling_id = next(
            (member for member in group.member_ids if str(member) != str(map_id)),
            None,
        )
        source_row = group.comparison_source(str(map_id))
        sibling_row = group.comparison_source(str(sibling_id))
        window_to_recognition = group.window_to_recognition(str(map_id))
        if source_row is None or sibling_row is None or window_to_recognition is None:
            return {
                "attempted": True,
                "confident": False,
                "reason": "front-group-difference-coordinate-metadata-unavailable",
            }
        source_anchor = source_row["frontDoorAnchor"]
        sibling_anchor = sibling_row["frontDoorAnchor"]

        render = np.asarray(matrix_source_to_client, dtype=np.float64)
        if not (
            render.shape == (3, 3)
            and np.isfinite(render).all()
        ):
            return {
                "attempted": True,
                "confident": False,
                "reason": "front-group-difference-reference-unavailable",
            }
        # Current-frame poses use legacy recognition pixels. Reviewed boxes
        # use the clean aligned grid; compose once and sample that exact grid.
        render = render @ window_to_recognition
        try:
            inverse = np.linalg.inv(render)
        except np.linalg.LinAlgError:
            return {
                "attempted": True,
                "confident": False,
                "reason": "front-group-difference-pose-singular",
            }
        if not np.isfinite(inverse).all():
            return {
                "attempted": True,
                "confident": False,
                "reason": "front-group-difference-pose-invalid",
            }
        window_x0, window_y0, window_x1, window_y1 = (
            int(value) for value in aligned_window
        )
        window_width = window_x1 - window_x0
        window_height = window_y1 - window_y0
        if window_width <= 0 or window_height <= 0:
            return {
                "attempted": True,
                "confident": False,
                "reason": "front-group-difference-window-invalid",
            }

        def source_box(
            anchor: tuple[float, float], box: tuple[int, int, int, int],
        ) -> tuple[int, int, int, int] | None:
            # This crop is in clean-image pixels, matching the generator's
            # rounded crop origin, not the legacy recognition door anchor.
            try:
                bx0, by0, bx1, by1 = (int(value) for value in box)
            except (TypeError, ValueError):
                return None
            if not (0 <= bx0 < bx1 <= window_width and 0 <= by0 < by1 <= window_height):
                return None
            sx0 = int(round(anchor[0] + window_x0 + bx0))
            sy0 = int(round(anchor[1] + window_y0 + by0))
            sx1 = int(round(anchor[0] + window_x0 + bx1))
            sy1 = int(round(anchor[1] + window_y0 + by1))
            return sx0, sy0, sx1, sy1

        # The highlighted boxes are useful exploration hints, but several
        # are empty in one member. Absence there cannot distinguish fog from
        # that member. Use positive differences in the same reviewed window
        # for both directions; never rank outside this exact two-member group.
        comparison_boxes = ((0, 0, window_width, window_height),)
        source_boxes = [source_box(source_anchor, box) for box in comparison_boxes]
        sibling_boxes = [source_box(sibling_anchor, box) for box in comparison_boxes]
        if not all(source_boxes) or not all(sibling_boxes):
            return {
                "attempted": True,
                "confident": False,
                "reason": "front-group-difference-box-invalid",
                "alignedWindow": list(aligned_window),
                "differenceBoxes": [list(box) for box in group.difference_boxes],
            }

        reference_cache = getattr(self, "_front_group_difference_reference_cache", None)
        if not isinstance(reference_cache, dict):
            reference_cache = {}
            self._front_group_difference_reference_cache = reference_cache

        def reference_box_mask(
            member_id: str, box: tuple[int, int, int, int],
        ) -> np.ndarray | None:
            key = (str(member_id), str(floor), tuple(box))
            cached = reference_cache.get(key)
            if isinstance(cached, np.ndarray):
                return cached
            x0, y0, x1, y1 = box
            row = group.comparison_source(member_id)
            if row is None:
                return None
            try:
                cx0, cy0, cx1, cy1 = row["cleanComponentBox"]
                with Image.open(ROOT / row["cleanMap"]) as opened:
                    crop = np.empty((y1-y0, x1-x0, 3), dtype=np.uint8)
                    crop[:] = (32, 24, 17)
                    ix0, iy0 = max(0, x0, cx0), max(0, y0, cy0)
                    ix1, iy1 = min(opened.width, x1, cx1), min(opened.height, y1, cy1)
                    if ix1 > ix0 and iy1 > iy0:
                        crop[iy0-y0:iy1-y0, ix0-x0:ix1-x0] = np.asarray(
                            opened.convert("RGB").crop((ix0, iy0, ix1, iy1)),
                        )[:, :, ::-1]
            except (OSError, KeyError, TypeError, ValueError):
                return None
            # These are reviewed clean drawings, not annotated route sheets.
            # Preserve their blue corridors, exactly as the comparison
            # generator does; the generic ink filter removes those corridors.
            mask = np.any(crop != (32, 24, 17), axis=2).astype(np.uint8)
            if mask.ndim != 2 or mask.shape != (y1 - y0, x1 - x0):
                return None
            reference_cache[key] = mask
            return mask

        def current_frame_box_mask(
            box: tuple[int, int, int, int],
        ) -> np.ndarray | None:
            sx0, sy0, sx1, sy1 = box
            source_corners = np.asarray([
                [[sx0, sy0]], [[sx1, sy0]],
                [[sx1, sy1]], [[sx0, sy1]],
            ], dtype=np.float64)
            client_corners = cv2.perspectiveTransform(
                source_corners, render,
            ).reshape(-1, 2)
            if not np.isfinite(client_corners).all():
                return None
            pad = 3
            cx0 = max(0, int(np.floor(client_corners[:, 0].min())) - pad)
            cy0 = max(0, int(np.floor(client_corners[:, 1].min())) - pad)
            cx1 = min(frame.shape[1], int(np.ceil(client_corners[:, 0].max())) + pad + 1)
            cy1 = min(frame.shape[0], int(np.ceil(client_corners[:, 1].max())) + pad + 1)
            if cx1 <= cx0 or cy1 <= cy0:
                return None
            live_crop = map_color_mask(frame[cy0:cy1, cx0:cx1], live=True)
            to_source = np.asarray([
                [1.0, 0.0, -sx0],
                [0.0, 1.0, -sy0],
                [0.0, 0.0, 1.0],
            ]) @ inverse @ np.asarray([
                [1.0, 0.0, cx0],
                [0.0, 1.0, cy0],
                [0.0, 0.0, 1.0],
            ])
            return cv2.warpPerspective(
                live_crop, to_source, (sx1 - sx0, sy1 - sy0),
                flags=cv2.INTER_NEAREST,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=0,
            ).astype(np.uint8)

        source_errors = []
        sibling_errors = []
        observed_pixels = 0
        source_support = 0
        sibling_support = 0
        for source_box_value, sibling_box_value, window_box in zip(
            source_boxes, sibling_boxes, comparison_boxes,
        ):
            source_reference = reference_box_mask(str(map_id), source_box_value)
            sibling_reference = reference_box_mask(str(sibling_id), sibling_box_value)
            query_box = current_frame_box_mask(window_box)
            if source_reference is None or sibling_reference is None or query_box is None:
                continue
            if query_box.shape != source_reference.shape or query_box.shape != sibling_reference.shape:
                continue
            # A few current-client pixels of alignment/antialiasing error
            # are not an identity contradiction. Exclude shared boundaries
            # within the same 3px neighborhood used for the live crop.
            radius = max(1, int(np.ceil(3.0 * np.linalg.norm(inverse[:2, :2], ord=2))))
            # At most one scale neighborhood per member, not a growing
            # per-frame cache. Static comparison geometry is reused until
            # the source-to-client scale changes its pixel tolerance.
            static_key = ("positive-differences", str(map_id))
            static = reference_cache.get(static_key)
            if static is None or static[0] != radius:
                # A subpixel-sized whole window has no usable detail. Avoid
                # allocating a kernel larger than the reviewed window.
                if radius >= min(window_width, window_height) // 2:
                    continue
                kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2*radius+1, 2*radius+1))
                source_near = cv2.dilate(source_reference, kernel)
                sibling_near = cv2.dilate(sibling_reference, kernel)
                unique = ((source_reference > 0) & (sibling_near == 0)) | (
                    (sibling_reference > 0) & (source_near == 0)
                )
                static = (radius, kernel, source_near, sibling_near, unique)
                reference_cache[static_key] = static
            _, kernel, source_near, sibling_near, unique = static
            # Thin edge fragments from quantization/pose error must not count
            # as a second member's contradictory structure. Require a solid
            # positive patch surviving the same client-space tolerance.
            observed = cv2.morphologyEx(
                ((query_box > 0) & unique).astype(np.uint8),
                cv2.MORPH_OPEN, kernel,
            ) > 0
            count = int(np.count_nonzero(observed))
            if count < max(80, int(np.count_nonzero(unique) * 0.05)):
                continue
            observed_pixels += count
            source_support += int(np.count_nonzero(observed & (source_reference > 0)))
            sibling_support += int(np.count_nonzero(observed & (sibling_reference > 0)))
            source_errors.append(float(np.mean(np.abs(
                query_box[observed].astype(np.float32) - source_near[observed],
            ))))
            sibling_errors.append(float(np.mean(np.abs(
                query_box[observed].astype(np.float32) - sibling_near[observed],
            ))))
        if not source_errors:
            return {
                "attempted": True,
                "confident": False,
                "candidateMapId": None,
                "reason": "front-group-difference-unexplored-or-empty",
                "observedPixels": 0,
                "differenceBoxes": [list(box) for box in group.difference_boxes],
                "sourceBoxes": [list(box) for box in source_boxes],
                "siblingBoxes": [list(box) for box in sibling_boxes],
            }
        source_error = float(np.mean(source_errors))
        sibling_error = float(np.mean(sibling_errors))
        margin = source_error - sibling_error
        conflicting = source_support >= 160 and sibling_support >= 160
        confident = bool(
            observed_pixels >= 160
            and source_error >= 0.12
            and margin >= 0.08
            and not conflicting
        )
        return {
            "attempted": True,
            "confident": confident,
            "candidateMapId": str(sibling_id) if confident else None,
            "reason": (
                "front-group-difference-supported"
                if confident else "front-group-difference-conflicting-positive-evidence"
                if conflicting else "front-group-difference-ambiguous"
            ),
            "sourceMapId": str(map_id),
            "siblingMapId": str(sibling_id),
            "sourceError": source_error,
            "siblingError": sibling_error,
            "margin": margin,
            "observedPixels": observed_pixels,
            "sourceSupportPixels": source_support,
            "siblingSupportPixels": sibling_support,
            "differenceBoxes": [list(box) for box in group.difference_boxes],
            "sourceBoxes": [list(box) for box in source_boxes],
            "siblingBoxes": [list(box) for box in sibling_boxes],
            "coordinateBasis": "reviewed-clean-window-to-recognition",
            "evidenceSource": "reviewed-window-positive-differences-current-frame",
        }

    def mark_front_member_verified(
        self,
        map_id: str,
        *,
        reason: str = "current-frame-geometry",
        current_frame_residual_verified: bool = False,
    ) -> dict:
        """Promote a provisional member only after the display gate succeeds."""
        group = self.front_group_index.group_for(map_id)
        diagnostics = self.last_front_diagnostics if isinstance(
            self.last_front_diagnostics, dict
        ) else {}
        local = diagnostics.get("localIdentity")
        pair = diagnostics.get("pair0412")
        selected_map_id = str(diagnostics.get("candidateMapId") or "")
        local_map_id = str(
            local.get("candidateMapId") if isinstance(local, dict) else ""
        )
        pair_map_id = str(
            pair.get("candidateMapId") if isinstance(pair, dict) else ""
        )
        member_evidence = bool(
            group is not None
            and (
                current_frame_residual_verified
                or
                len(group.member_ids) == 1
                or (
                    isinstance(pair, dict)
                    and pair.get("confident") is True
                    and pair_map_id == str(map_id)
                    and selected_map_id == str(map_id)
                )
                or (
                    isinstance(local, dict)
                    and local.get("confident") is True
                    and local_map_id == str(map_id)
                    and selected_map_id == str(map_id)
                )
            )
        )
        competition = {
            "locked": bool(group and self.front_group_state.group_id == group.group_id),
            "groupId": group.group_id if group else None,
            "provisionalMemberId": map_id,
            "competitions": [],
        }
        member_verified = bool(
            member_evidence
            and self.front_group_state.state == "GROUP_LOCKED"
            and self.front_group_state.provisional_member_id == str(map_id)
        )
        result = self.front_group_index.transition(
            self.front_group_state,
            competition,
            member_verified=member_verified,
            verified_member_id=str(map_id) if member_verified else None,
        )
        for event in result.get("events") or []:
            event["reason"] = reason
            self.front_group_events.append(event)
            if self.log_callback is not None:
                self.log_callback(event.pop("event"), **event)
        return result

    def switch_front_provisional_member(
        self,
        map_id: str,
        *,
        reason: str = "provisional-member-current-frame-fit-failed",
    ) -> dict:
        """Switch a reviewed group member without granting durable identity."""
        group = self.front_group_index.group_for(map_id)
        state = self.front_group_state
        events = []
        if not (
            group is not None
            and state.state in {"GROUP_LOCKED", "MEMBER_VERIFIED"}
            and state.group_id == group.group_id
            and str(map_id) in group.member_ids
            and str(map_id) != state.provisional_member_id
        ):
            return {
                "state": state.state,
                "groupId": state.group_id,
                "provisionalMemberId": state.provisional_member_id,
                "verifiedMemberId": state.verified_member_id,
                "groupGeneration": state.generation,
                "events": events,
            }
        previous = state.provisional_member_id
        state.provisional_member_id = str(map_id)
        # A structure conflict revokes the previous member's current-round
        # verification.  The replacement remains provisional until its own
        # current-frame residual is accepted by the display transaction.
        state.verified_member_id = None
        state.state = "GROUP_LOCKED"
        state.member_verified_at = None
        state.generation += 1
        state.last_switch_reason = str(reason)
        events.append({
            "event": "front-member-switch",
            "groupId": state.group_id,
            "fromMemberId": previous,
            "toMemberId": state.provisional_member_id,
            "switchReason": state.last_switch_reason,
            "switchMargin": None,
            "groupGeneration": state.generation,
            "currentFrameFitFallback": True,
        })
        for event in events:
            self.front_group_events.append(dict(event))
            if self.log_callback is not None:
                payload = dict(event)
                event_name = payload.pop("event", "front-member-switch")
                self.log_callback(event_name, **payload)
        return {
            "state": state.state,
            "groupId": state.group_id,
            "provisionalMemberId": state.provisional_member_id,
            "verifiedMemberId": state.verified_member_id,
            "groupGeneration": state.generation,
            "events": events,
        }

    def rank(
        self,
        frame: np.ndarray,
        floor: str = "2",
        limit: int = 5,
        *,
        budget_seconds: float | None = None,
    ) -> list[dict]:
        started = time.perf_counter()
        scene = (
            front_entry_scene_evidence(frame, self.base)
            if str(floor) == FRONT_ENTRY_FLOOR else {
                "verified": False,
                "entranceType": "not-applicable",
                "reason": "front-entry-scene-non-f1",
            }
        )
        if str(floor) != FRONT_ENTRY_FLOOR or not scene.get("verified"):
            self.front_temporal_history = []
            self.last_front_diagnostics = None
            self.front_group_state.reset()
            self.front_group_events = []
            if str(floor) != FRONT_ENTRY_FLOOR:
                # Preserve the accepted side-door path byte-for-byte at its
                # public boundary, including the caller's exact time budget.
                return self.base.rank(
                    frame, floor=floor, limit=limit,
                    budget_seconds=budget_seconds,
                )
            if self.log_callback is not None and str(floor) == FRONT_ENTRY_FLOOR:
                self.log_callback(
                    "front-entry-scene-gate-rejected",
                    floor=floor, entranceSceneEvidence=scene,
                    frontIdentityStarted=False,
                )
            remaining_budget = (
                None if budget_seconds is None else
                max(
                    0.01,
                    float(budget_seconds)
                    - (time.perf_counter() - started),
                )
            )
            return self.base.rank(
                frame, floor=floor, limit=limit,
                budget_seconds=remaining_budget,
            )
        rows, diagnostics = rank_front_entry_frame(
            frame, self.base, self.recognizer, self.layout_recognizer,
            limit=limit, entrance_scene_evidence=scene,
            group_index=self.front_group_index,
        )
        rows, temporal_history, diagnostics = fuse_front_temporal_identity(
            rows,
            self.front_temporal_history,
            diagnostics,
        )
        self.front_temporal_history = (
            [] if diagnostics.get("automaticMapLockAllowed")
            else temporal_history
        )
        diagnostics["elapsedMs"] = round(
            (time.perf_counter() - started) * 1000.0, 2,
        )
        diagnostics["entranceType"] = "front-door"
        group_transition = self.front_group_index.transition(
            self.front_group_state,
            diagnostics.get("frontGroupCompetition") or {},
        )
        diagnostics["frontGroupState"] = group_transition
        for event in group_transition.get("events") or []:
            event = dict(event)
            event_name = event.pop("event", "front-group-state")
            self.front_group_events.append({"event": event_name, **event})
            if self.log_callback is not None:
                self.log_callback(event_name, **event)
        for row in rows:
            row["frontDoorGroupId"] = group_transition.get("groupId")
            row["frontDoorGroupState"] = group_transition.get("state")
            row["frontDoorProvisionalMemberId"] = group_transition.get(
                "provisionalMemberId"
            )
            row["frontDoorVerifiedMemberId"] = group_transition.get(
                "verifiedMemberId"
            )
            row["frontDoorGroupGeneration"] = group_transition.get(
                "groupGeneration", 0
            )
            row["frontDoorCandidateOnly"] = True
        provisional_id = group_transition.get("provisionalMemberId")
        if provisional_id:
            rows.sort(
                key=lambda row: (
                    0 if str(row.get("mapId") or "") == str(provisional_id) else 1,
                    -float(row.get("score", 0.0)),
                )
            )
        diagnostics["entranceSceneEvidence"] = scene
        self.last_front_diagnostics = diagnostics
        if self.log_callback is not None:
            self.log_callback("front-live-ranked", **diagnostics)
        return rows


class CleanFrontDoorPreviews:
    def __init__(
        self,
        manifest_path: Path = DEFAULT_CLEAN_PREVIEWS,
        identities_path: Path = DEFAULT_FRONT_DOOR_IDENTITIES,
    ) -> None:
        payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        if payload.get("schema") != "identityv-nightmare-clean-front-previews-v1":
            raise ValueError("clean-front-preview-schema-unsupported")
        self.rows = {
            str(row["mapId"]): row for row in payload.get("maps") or []
        }
        if len(self.rows) != 13:
            raise ValueError("clean-front-preview-coverage-invalid")
        identity_payload = json.loads(
            Path(identities_path).read_text(encoding="utf-8")
        )
        self.identity_rows = {
            str(row["mapId"]): row
            for row in identity_payload.get("maps") or []
        }
        if len(self.identity_rows) != 13:
            raise ValueError("front-door-identity-coverage-invalid")

    def build(
        self,
        map_id: str,
        size: tuple[int, int] = (112, 72),
        *,
        source_path: Path | None = None,
    ) -> Image.Image:
        row = self.rows[str(map_id)]
        identity = self.identity_rows.get(str(map_id))
        use_reviewed_source = bool(
            source_path is not None
            and Path(source_path).is_file()
            and isinstance(identity, dict)
            and isinstance(identity.get("entryContext"), dict)
        )
        path = Path(source_path) if use_reviewed_source else ROOT / Path(row["cleanMap"])
        with Image.open(path) as opened:
            source = opened.convert("RGB")
        crop_authority = (
            identity["entryContext"]["crop"]
            if use_reviewed_source else row["previewCrop"]
        )
        anchor_authority = (
            identity["frontDoorAnchor"]["center"]
            if use_reviewed_source else row["frontDoorAnchor"]
        )
        x0, y0, x1, y1 = (int(value) for value in crop_authority)
        crop = source.crop((x0, y0, x1, y1))
        crop.thumbnail(size, Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", size, "#172231")
        px = (size[0] - crop.width) // 2
        py = (size[1] - crop.height) // 2
        canvas.paste(crop, (px, py))
        ax, ay = (float(value) for value in anchor_authority)
        marker_x = px + (ax - x0) * crop.width / max(x1 - x0, 1)
        marker_y = py + (ay - y0) * crop.height / max(y1 - y0, 1)
        draw = ImageDraw.Draw(canvas)
        radius = 3
        draw.ellipse(
            (marker_x - radius, marker_y - radius, marker_x + radius, marker_y + radius),
            fill="#ffd166", outline="#07101a", width=1,
        )
        return canvas


FRONT_LAYOUT_PLAYER_LABELS = {
    "top-left-right": "三门（上＋左＋右）",
    "top-left": "两门（上＋左）",
    "top-right": "两门（上＋右）",
    "left-right": "两门（左＋右）",
}


def front_candidate_title(item) -> str:
    """Return a deterministic human label that does not need auto scores."""
    layout = FRONT_LAYOUT_PLAYER_LABELS.get(
        str(getattr(item, "front_door_layout", "unknown")),
    )
    shape = str(getattr(item, "shape", "")).strip()
    if layout and shape:
        return f"{layout} · {shape}"
    return str(getattr(item, "name", "正门候选"))


def front_candidate_card_text(item, row: dict | None, *, confirmed: bool) -> str:
    title = front_candidate_title(item)
    if confirmed:
        title = f"已锁定 · {title}"
    score_text = (
        f"自动 {float(row.get('score', 0.0)):.0f}"
        if isinstance(row, dict) else "等待识别"
    )
    return f"  {title}\n  原名：{item.name} · {score_text}"
