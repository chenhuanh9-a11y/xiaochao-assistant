"""Original frame residual and translation evidence, independent of UI."""
from __future__ import annotations
import math
import cv2
import numpy as np

def canonical_residual_evidence(residual, *, basis, coordinate_pack_id,
                                profile_bridge_verified=True):
    """Shared provenance gate for the existing canonical residual consumer."""
    residual.update({
        "validationBasis": "shared-canonical-geometry-current-frame",
        "validationSensorBasis": basis,
        "coordinatePackId": coordinate_pack_id,
        "poseVerifiedAgainstCanonicalGeometry": bool(
            residual.get("verified") is True and coordinate_pack_id),
        "profileRenderDerivedFromCanonical": bool(coordinate_pack_id and profile_bridge_verified),
        "poseVerifiedAgainstSharedStructure": bool(
            residual.get("verified") and basis == "game-rendered-shared-structure"),
        "poseVerifiedAgainstRenderedProfile": False,
        "routeProfileIndependent": True,
    })
    return residual

def rendered_profile_residual_is_verified(residual: dict | None) -> bool:
    """Authorize a display pose from one of the two supported evidence eras.

    New runtime paths validate the one canonical geometry pose and derive every
    author layer through a fixed reviewed coordinate adapter.  The legacy
    rendered-profile form remains accepted for saved sessions and older tests,
    but new alignment code must not make route paint or labels part of pose
    authorization.
    """
    legacy_rendered = bool(
        isinstance(residual, dict)
        and residual.get("verified") is True
        and residual.get("poseVerifiedAgainstRenderedProfile") is True
        and residual.get("validationBasis")
        == "actual-rendered-profile-structural-mask"
    )
    canonical_geometry = bool(
        isinstance(residual, dict)
        and residual.get("verified") is True
        and residual.get("poseVerifiedAgainstCanonicalGeometry") is True
        and residual.get("profileRenderDerivedFromCanonical") is True
        and residual.get("validationBasis")
        == "shared-canonical-geometry-current-frame"
        and bool(residual.get("coordinatePackId"))
    )
    source_structure=False
    if (isinstance(residual,dict)
        and residual.get("verified") is True
        and residual.get("poseVerifiedAgainstSourceStructure") is True
        and residual.get("validationBasis") == "source-structure-heldout-current-frame"):
        try:
            heldout_inliers=int(residual.get("heldOutInliers",0) or 0)
            heldout_cells=int(residual.get("heldOutCellCount",0) or 0)
            proposed_median=float(residual.get("proposedTransformMedianError",float('inf')))
            proposed_p90=float(residual.get("proposedTransformP90Error",float('inf')))
            source_structure=bool(
                residual.get("sourceCoordinatesVerified") is True
                and residual.get("sourceFloorIsProducerLabelOnly") is True
                and residual.get("sourceCoordinateSpace") == "producer-source-png-pixels"
                and residual.get("sourcePngIsDisplayImage") is True
                and bool(residual.get("sourceGroup"))
                and str(residual.get("gameFloor")) in {"1","2","B1"}
                and heldout_inliers>=5 and heldout_cells>=4
                and np.isfinite([proposed_median,proposed_p90]).all()
                and proposed_median<=4.0 and proposed_p90<=8.0)
        except (TypeError,ValueError):
            source_structure=False
    return legacy_rendered or canonical_geometry or source_structure

def translation_mask_overlap_metrics(
    previous_mask: np.ndarray,
    current_mask: np.ndarray,
    dx_small: float,
    dy_small: float,
) -> dict:
    """Describe old overlap, missing old pixels, and newly explored pixels.

    Metrics are deliberately asymmetric.  New current pixels are reported as
    exploration growth, but they do not reduce containment of durable old map
    walls.  Missing old pixels remain a safety signal.
    """
    old = (np.asarray(previous_mask) > 0.35).astype(np.uint8)
    live = (np.asarray(current_mask) > 0.22).astype(np.uint8)
    if old.shape != live.shape or old.ndim != 2:
        return {
            "stableOverlapRatio": 0.0,
            "missingOldRatio": 1.0,
            "newPixelRatio": 0.0,
            "explorationGrowthRatio": 0.0,
        }
    shifted = cv2.warpAffine(
        old,
        np.asarray(
            [[1.0, 0.0, float(dx_small)], [0.0, 1.0, float(dy_small)]],
            dtype=np.float32,
        ),
        (old.shape[1], old.shape[0]),
        flags=cv2.INTER_NEAREST,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )
    live_wide = cv2.dilate(live, np.ones((5, 5), np.uint8))
    old_wide = cv2.dilate(shifted, np.ones((5, 5), np.uint8))
    shifted_count = int((shifted > 0).sum())
    live_count = int((live > 0).sum())
    stable_count = int(np.logical_and(shifted > 0, live_wide > 0).sum())
    new_count = int(np.logical_and(live > 0, old_wide <= 0).sum())
    stable_ratio = float(stable_count / max(shifted_count, 1))
    return {
        "stableOverlapRatio": stable_ratio,
        "missingOldRatio": float(max(0.0, 1.0 - stable_ratio)),
        "newPixelRatio": float(new_count / max(live_count, 1)),
        "explorationGrowthRatio": float(
            max(0, live_count - shifted_count) / max(shifted_count, 1)
        ),
    }

def phase_relocalize_translation_masks(
    previous: tuple[np.ndarray, float] | None,
    current: tuple[np.ndarray, float] | None,
    *, allow_containment: bool = True,
) -> dict:
    """Recover screen translation while allowing newly explored map pixels.

    The first mask is treated as durable evidence from an already aligned
    frame.  Only containment of those old pixels is required; extra pixels in
    the current mask are expected as the player explores the same layout.
    """
    rejected = {
        "tracked": False, "moved": False, "dx": 0.0, "dy": 0.0,
        "confidence": 0.0, "phaseResponse": 0.0,
        "previousContainment": 0.0, "reason": "phase-mask-unavailable",
    }
    if previous is None or current is None:
        return rejected
    previous_mask, previous_scale = previous
    current_mask, current_scale = current
    if (
        previous_mask.shape != current_mask.shape
        or abs(previous_scale - current_scale) > 1e-6
    ):
        return {**rejected, "reason": "phase-client-size-changed"}
    window = cv2.createHanningWindow(
        (previous_mask.shape[1], previous_mask.shape[0]), cv2.CV_32F,
    )
    # OpenCV may apply the Hanning window in-place. Keep the binary masks
    # untouched for the independent containment check below.
    shift, response = cv2.phaseCorrelate(
        previous_mask.copy(), current_mask.copy(), window,
    )
    dx_small, dy_small = (float(value) for value in shift)
    if not np.isfinite([dx_small, dy_small, response]).all():
        return {**rejected, "reason": "phase-nonfinite"}
    # Phase correlation is unambiguous below half a canvas on each axis.  The
    # old fixed 720-client-pixel radius rejected a legitimate long-idle drag on
    # 1440p even when durable wall containment was excellent. Keep a 10% margin
    # from the wrap boundary and retain the strict response/containment gates.
    maximum_x_small = previous_mask.shape[1] * 0.45
    maximum_y_small = previous_mask.shape[0] * 0.45
    if (
        abs(dx_small) > maximum_x_small
        or abs(dy_small) > maximum_y_small
    ):
        return {**rejected, "phaseResponse": float(response), "reason": "phase-jump-too-large"}
    matrix = np.asarray([[1.0, 0.0, dx_small], [0.0, 1.0, dy_small]], np.float32)
    shifted = cv2.warpAffine(
        previous_mask, matrix,
        (previous_mask.shape[1], previous_mask.shape[0]),
        flags=cv2.INTER_NEAREST,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )
    current_wide = cv2.dilate(
        current_mask.astype(np.uint8), np.ones((5, 5), np.uint8),
    )
    intersection = int(np.logical_and(shifted > 0, current_wide > 0).sum())
    previous_containment = float(intersection / max(int((shifted > 0).sum()), 1))
    overlap_metrics = translation_mask_overlap_metrics(
        previous_mask, current_mask, dx_small, dy_small,
    )
    confidence = float(np.clip(
        0.72 * previous_containment + 0.28 * min(1.0, max(0.0, response) / 0.35),
        0.0, 1.0,
    ))
    if response < 0.08 or previous_containment < 0.78 or confidence < 0.82:
        # Phase correlation is intentionally the first choice because it is
        # fast and sub-pixel precise.  It becomes unstable, however, when a
        # long-unseen map contains far more explored rooms than the anchor
        # frame.  In that case search for the translation that contains the
        # durable old wall pixels.  New pixels are allowed; a second distinct
        # peak is not.  This keeps the safety boundary while avoiding the old
        # "normal exploration == different map" false negative.
        fallback = (containment_relocalize_translation_masks(previous, current)
            if allow_containment else {'tracked': False, 'reason': 'local-proposal-only'})
        if fallback.get("tracked"):
            return {
                **fallback,
                "phaseResponse": float(response),
                "phaseCandidateContainment": previous_containment,
            }
        return {
            **rejected,
            "phaseResponse": float(response),
            "previousContainment": previous_containment,
            "confidence": confidence,
            "containmentSearch": fallback,
            "reason": "phase-geometry-disagrees",
        }
    dx, dy = dx_small / previous_scale, dy_small / previous_scale
    return {
        "tracked": True,
        "moved": float(np.hypot(dx, dy)) >= 0.75,
        "dx": float(dx), "dy": float(dy),
        "confidence": confidence,
        "phaseResponse": float(response),
        "previousContainment": previous_containment,
        **overlap_metrics,
        "anchorCount": 0, "inlierCount": 0,
        "reason": "phase-relocalized",
    }

def containment_relocalize_translation_masks(
    previous: tuple[np.ndarray, float] | None,
    current: tuple[np.ndarray, float] | None,
    *,
    minimum_containment: float = 0.84,
    minimum_distinct_margin: float = 0.018,
) -> dict:
    """Relocate durable old map pixels inside a larger explored map.

    The normalized component silhouette is deliberately not used here: its
    bounding box and shape change whenever new rooms are explored.  The old
    positive pixels form the template, the current mask is widened slightly,
    and a translation is accepted only when most old pixels are explained and
    the winning location is distinct from repeated-corridor alternatives.
    """
    rejected = {
        "tracked": False, "moved": False, "dx": 0.0, "dy": 0.0,
        "confidence": 0.0, "previousContainment": 0.0,
        "distinctMargin": 0.0, "reason": "containment-mask-unavailable",
    }
    if previous is None or current is None:
        return rejected
    previous_mask, previous_scale = previous
    current_mask, current_scale = current
    if (
        previous_mask.shape != current_mask.shape
        or abs(float(previous_scale) - float(current_scale)) > 1e-6
    ):
        return {**rejected, "reason": "containment-client-size-changed"}
    old = (np.asarray(previous_mask) > 0.35).astype(np.float32)
    live = (np.asarray(current_mask) > 0.22).astype(np.uint8)
    ys, xs = np.where(old > 0)
    old_count = int(len(xs))
    if old_count < 300:
        return {**rejected, "reason": "containment-template-too-small"}
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    template = old[y0:y1, x0:x1]
    if min(template.shape) < 8:
        return {**rejected, "reason": "containment-template-degenerate"}

    # Give the old island room to move while keeping the search below the
    # phase-correlation wrap boundary.  Padding with zero represents portions
    # moved outside the current viewport and naturally lowers containment.
    height, width = live.shape
    pad_x = max(8, int(round(width * 0.45)))
    pad_y = max(8, int(round(height * 0.45)))
    widened = cv2.dilate(live, np.ones((5, 5), np.uint8)).astype(np.float32)
    search = cv2.copyMakeBorder(
        widened, pad_y, pad_y, pad_x, pad_x,
        cv2.BORDER_CONSTANT, value=0,
    )
    scores = cv2.matchTemplate(search, template, cv2.TM_CCORR)
    if scores.size == 0 or not np.isfinite(scores).all():
        return {**rejected, "reason": "containment-search-failed"}
    _minimum, best_score, _minimum_at, best_at = cv2.minMaxLoc(scores)
    best_x, best_y = int(best_at[0]), int(best_at[1])
    best_containment = float(best_score / max(old_count, 1))

    # Dilation makes the safety score tolerant to anti-aliasing and one-pixel
    # wall-width changes, but it also creates a small translation plateau.
    # Recover the exact pixel displacement from the undilated mask inside that
    # already-authorized neighbourhood.
    raw_search = cv2.copyMakeBorder(
        live.astype(np.float32), pad_y, pad_y, pad_x, pad_x,
        cv2.BORDER_CONSTANT, value=0,
    )
    raw_scores = cv2.matchTemplate(raw_search, template, cv2.TM_CCORR)
    refine_radius = 4
    rx0, rx1 = max(0, best_x - refine_radius), min(
        raw_scores.shape[1], best_x + refine_radius + 1,
    )
    ry0, ry1 = max(0, best_y - refine_radius), min(
        raw_scores.shape[0], best_y + refine_radius + 1,
    )
    local_raw = raw_scores[ry0:ry1, rx0:rx1]
    if local_raw.size:
        _raw_min, raw_best, _raw_min_at, raw_best_at = cv2.minMaxLoc(
            local_raw,
        )
        if float(raw_best) / max(old_count, 1) >= 0.55:
            best_x = rx0 + int(raw_best_at[0])
            best_y = ry0 + int(raw_best_at[1])

    second_scores = scores.copy()
    exclusion = max(6, int(round(10 * float(previous_scale))))
    sx0, sx1 = max(0, best_x - exclusion), min(
        second_scores.shape[1], best_x + exclusion + 1,
    )
    sy0, sy1 = max(0, best_y - exclusion), min(
        second_scores.shape[0], best_y + exclusion + 1,
    )
    second_scores[sy0:sy1, sx0:sx1] = 0
    second_score = float(cv2.minMaxLoc(second_scores)[1])
    distinct_margin = float(
        (float(best_score) - second_score) / max(old_count, 1)
    )
    dx_small = float(best_x - (pad_x + x0))
    dy_small = float(best_y - (pad_y + y0))
    if (
        abs(dx_small) > width * 0.45
        or abs(dy_small) > height * 0.45
    ):
        return {
            **rejected, "previousContainment": best_containment,
            "distinctMargin": distinct_margin,
            "reason": "containment-jump-too-large",
        }
    if (
        best_containment < float(minimum_containment)
        or distinct_margin < float(minimum_distinct_margin)
    ):
        return {
            **rejected, "previousContainment": best_containment,
            "distinctMargin": distinct_margin,
            "reason": (
                "containment-ambiguous"
                if distinct_margin < float(minimum_distinct_margin)
                else "containment-insufficient"
            ),
        }
    dx, dy = dx_small / float(previous_scale), dy_small / float(previous_scale)
    overlap_metrics = translation_mask_overlap_metrics(
        previous_mask, current_mask, dx_small, dy_small,
    )
    confidence = float(np.clip(
        0.72 * best_containment
        + 0.28 * min(1.0, distinct_margin / 0.08),
        0.0, 1.0,
    ))
    return {
        "tracked": True,
        "moved": float(np.hypot(dx, dy)) >= 0.75,
        "dx": float(dx), "dy": float(dy),
        "confidence": max(0.82, confidence),
        "previousContainment": best_containment,
        "distinctMargin": distinct_margin,
        **overlap_metrics,
        "anchorCount": 0, "inlierCount": 0,
        "reason": "containment-relocalized",
    }
