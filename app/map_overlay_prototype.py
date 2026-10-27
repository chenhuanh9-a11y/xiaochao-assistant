from __future__ import annotations

import json
import os
import math
from pathlib import Path

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
ASSETS = Path(os.environ.get("IDENTITYV_LEGACY_ASSETS_ROOT", str(ROOT / "assets" / "legacy")))

CALIBRATION_REGISTRY = ROOT / "references" / "overlay-calibrations.json"


def _load_enabled_calibration_profiles() -> list[dict]:
    """Load only truth-verified legacy profiles; fail closed otherwise."""
    if not CALIBRATION_REGISTRY.exists():
        return []
    data = json.loads(CALIBRATION_REGISTRY.read_text(encoding="utf-8"))
    calibrations = []
    for entry in data.get("entries", []):
        if not entry.get("enabled") or not entry.get("referenceAnchor"):
            continue
        relative = entry.get("layerRelative")
        if not relative:
            continue
        calibration = dict(entry)
        calibration["layer"] = ASSETS / Path(relative)
        calibration["profileId"] = (
            f"legacy-{entry['mapId']}-f{int(entry['floor'])}-"
            f"w{int(entry['baseClientWidth'])}"
        )
        calibration["minimumClientWidthRatio"] = float(
            entry.get("minimumClientWidthRatio", 0.65)
        )
        calibration["maximumClientWidthRatio"] = float(
            entry.get("maximumClientWidthRatio", 1.50)
        )
        calibrations.append(calibration)
    return calibrations


CALIBRATION_PROFILES = _load_enabled_calibration_profiles()


def select_runtime_calibration(
    map_id: str,
    floor: int | None,
    client_width: int,
    *,
    profiles: list[dict] | None = None,
) -> tuple[dict | None, dict]:
    """Select the closest verified scene profile without using a candidate."""
    choices = [
        item for item in (profiles if profiles is not None else CALIBRATION_PROFILES)
        if item.get("mapId") == map_id and int(item.get("floor", -1)) == floor
    ]
    if not choices:
        return None, {"success": False, "reason": "overlay-calibration-unavailable"}
    ranked = []
    for item in choices:
        base_width = float(item.get("baseClientWidth") or 0)
        if base_width <= 0 or client_width <= 0:
            continue
        ratio = client_width / base_width
        ranked.append((abs(math.log(ratio)), ratio, item))
    if not ranked:
        return None, {"success": False, "reason": "overlay-calibration-invalid-client-size"}
    ranked.sort(key=lambda row: (row[0], row[2].get("profileId", "")))
    _, ratio, selected = ranked[0]
    if not (
        selected["minimumClientWidthRatio"]
        <= ratio
        <= selected["maximumClientWidthRatio"]
    ):
        return None, {
            "success": False,
            "reason": "overlay-client-size-out-of-profile-range",
            "nearestProfileId": selected.get("profileId"),
            "clientWidthRatio": float(ratio),
            "allowedRatio": [
                selected["minimumClientWidthRatio"],
                selected["maximumClientWidthRatio"],
            ],
        }
    return selected, {
        "success": True,
        "reason": "nearest-verified-scene-profile",
        "profileId": selected.get("profileId"),
        "candidateCount": len(choices),
        "clientWidthRatio": float(ratio),
    }


# Backward-compatible single-profile view for existing tests and callers.
# M2 selection uses CALIBRATION_PROFILES and chooses by the current client size.
CALIBRATIONS = {
    (item["mapId"], int(item["floor"])): item
    for item in CALIBRATION_PROFILES
}


def _validate_live_anchor(
    live_anchor: dict,
    frame_shape: tuple[int, ...],
    calibration: dict,
) -> tuple[bool, dict]:
    height, width = frame_shape[:2]
    try:
        x = float(live_anchor["x"])
        y = float(live_anchor["y"])
        anchor_width = float(live_anchor["width"])
        anchor_height = float(live_anchor["height"])
    except (KeyError, TypeError, ValueError):
        return False, {"reason": "overlay-live-anchor-malformed"}
    if (
        anchor_width <= 0 or anchor_height <= 0
        or x < 0 or y < 0 or x + anchor_width > width or y + anchor_height > height
    ):
        return False, {"reason": "overlay-live-anchor-out-of-frame"}

    if live_anchor.get("trustedManual") is True:
        score = 1.0
        appearance = {}
    else:
        if live_anchor.get("score") is None or not isinstance(live_anchor.get("appearance"), dict):
            return False, {"reason": "overlay-live-anchor-evidence-missing"}
        score = float(live_anchor["score"])
        appearance = live_anchor["appearance"]
        bright = float(appearance.get("brightRatio", 0.0))
        neutral = float(appearance.get("neutralRatio", 0.0))
        dark = float(appearance.get("darkRatio", 1.0))
        if score < 0.50:
            return False, {"reason": "overlay-live-anchor-low-template-score", "score": score}
        if bright < 0.50 or neutral < 0.35 or dark > 0.40:
            return False, {
                "reason": "overlay-live-anchor-appearance-rejected",
                "score": score,
                "brightRatio": bright,
                "neutralRatio": neutral,
                "darkRatio": dark,
            }

    scale = calibration["baseScale"] * width / calibration["baseClientWidth"]
    reference = calibration["referenceAnchor"]
    expected_width = max(1.0, float(reference["width"]) * scale)
    expected_height = max(1.0, float(reference["height"]) * scale)
    width_ratio = anchor_width / expected_width
    height_ratio = anchor_height / expected_height
    if not (0.55 <= width_ratio <= 1.80 and 0.55 <= height_ratio <= 1.80):
        return False, {
            "reason": "overlay-live-anchor-scale-inconsistent",
            "widthRatio": float(width_ratio),
            "heightRatio": float(height_ratio),
        }
    reported_residual = live_anchor.get("residualErrorPx")
    if reported_residual is not None and float(reported_residual) > 6.0:
        return False, {
            "reason": "overlay-live-anchor-residual-too-high",
            "residualErrorPx": float(reported_residual),
            "maximumResidualErrorPx": 6.0,
        }
    bright = float(appearance.get("brightRatio", 1.0))
    neutral = float(appearance.get("neutralRatio", 1.0))
    dark = float(appearance.get("darkRatio", 0.0))
    quality = score + 0.15 * bright + 0.05 * neutral - 0.10 * dark
    return True, {
        "reason": "overlay-live-anchor-accepted",
        "quality": float(quality),
        "score": float(score),
        "widthRatio": float(width_ratio),
        "heightRatio": float(height_ratio),
        "estimatedCenterUncertaintyPx": float(max(0.5, (1.0 - min(score, 1.0)) * 4.0)),
    }


def select_live_anchor(
    candidates: list[dict],
    frame_shape: tuple[int, ...],
    calibration: dict,
) -> tuple[dict | None, dict]:
    accepted = []
    rejected = []
    for candidate in candidates:
        valid, evidence = _validate_live_anchor(candidate, frame_shape, calibration)
        row = {"anchor": candidate, "evidence": evidence}
        (accepted if valid else rejected).append(row)
    if not accepted:
        reason = rejected[0]["evidence"]["reason"] if rejected else "overlay-live-anchor-unavailable"
        return None, {
            "success": False,
            "reason": reason,
            "candidateCount": len(candidates),
            "rejected": [item["evidence"] for item in rejected[:5]],
        }
    accepted.sort(key=lambda item: item["evidence"]["quality"], reverse=True)
    if len(accepted) >= 2:
        first, second = accepted[0], accepted[1]
        margin = first["evidence"]["quality"] - second["evidence"]["quality"]
        a, b = first["anchor"], second["anchor"]
        center_distance = math.hypot(
            (a["x"] + a["width"] / 2) - (b["x"] + b["width"] / 2),
            (a["y"] + a["height"] / 2) - (b["y"] + b["height"] / 2),
        )
        if margin < 0.035 and center_distance > max(a["width"], a["height"]) * 2:
            return None, {
                "success": False,
                "reason": "overlay-live-anchor-ambiguous",
                "qualityMargin": float(margin),
                "centerDistancePx": float(center_distance),
                "acceptedCandidateCount": len(accepted),
            }
    best = accepted[0]
    return best["anchor"], {
        "success": True,
        **best["evidence"],
        "candidateCount": len(candidates),
        "acceptedCandidateCount": len(accepted),
    }


def _read_layer(path: Path) -> np.ndarray | None:
    try:
        data = np.fromfile(path, dtype=np.uint8)
    except OSError:
        return None
    image = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    if image is None or image.ndim != 3 or image.shape[2] != 4:
        return None
    return image


def build_aligned_overlay(
    frame_shape: tuple[int, ...],
    map_id: str,
    floor: int | None,
    live_anchor: dict | None,
    opacity: float = 0.68,
) -> tuple[np.ndarray | None, dict]:
    calibration, profile_selection = select_runtime_calibration(
        map_id, floor, int(frame_shape[1])
    )
    if calibration is None:
        return None, profile_selection
    if not live_anchor:
        return None, {"success": False, "reason": "overlay-live-anchor-unavailable"}
    anchor_valid, anchor_validation = _validate_live_anchor(
        live_anchor, frame_shape, calibration
    )
    if not anchor_valid:
        return None, {"success": False, **anchor_validation}
    layer = _read_layer(calibration["layer"])
    if layer is None:
        return None, {"success": False, "reason": "overlay-layer-unavailable"}

    height, width = frame_shape[:2]
    scale = calibration["baseScale"] * width / calibration["baseClientWidth"]
    reference = calibration["referenceAnchor"]
    ref_cx = reference["x"] + reference["width"] / 2
    ref_cy = reference["y"] + reference["height"] / 2
    live_cx = live_anchor["x"] + live_anchor["width"] / 2
    live_cy = live_anchor["y"] + live_anchor["height"] / 2
    tx, ty = live_cx - scale * ref_cx, live_cy - scale * ref_cy
    matrix = np.array([[scale, 0.0, tx], [0.0, scale, ty]], np.float32)
    mapped_ref_cx = scale * ref_cx + tx
    mapped_ref_cy = scale * ref_cy + ty
    anchor_alignment_error = float(np.hypot(
        mapped_ref_cx - live_cx, mapped_ref_cy - live_cy
    ))
    warped = cv2.warpAffine(
        layer, matrix, (width, height), flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0),
    )
    warped[:, :, 3] = np.clip(warped[:, :, 3].astype(np.float32) * opacity, 0, 255).astype(np.uint8)
    return warped, {
        "success": True, "reason": "truth-calibrated-scale-translation",
        "mapId": map_id, "mapName": calibration["mapName"], "floor": floor,
        "calibrationStatus": calibration.get("status"),
        "calibrationProvenance": calibration.get("provenance"),
        "alignmentModel": "client-width-scale-plus-live-anchor-translation",
        "translationSource": "current-frame-visual-anchor",
        "profileSelection": profile_selection,
        "anchorValidation": anchor_validation,
        "orientation": calibration["orientation"], "scale": float(scale),
        "anchorAlignmentErrorPx": anchor_alignment_error,
        "matrix": matrix.tolist(), "referenceAnchor": reference, "liveAnchor": live_anchor,
    }


def build_aligned_overlay_from_candidates(
    frame_shape: tuple[int, ...],
    map_id: str,
    floor: int | None,
    live_anchor_candidates: list[dict],
    opacity: float = 0.68,
) -> tuple[np.ndarray | None, dict]:
    """Preferred M2 entrypoint: choose a visual anchor, then relocate or hide."""
    calibration, profile_selection = select_runtime_calibration(
        map_id, floor, int(frame_shape[1])
    )
    if calibration is None:
        return None, profile_selection
    live_anchor, anchor_selection = select_live_anchor(
        live_anchor_candidates, frame_shape, calibration
    )
    if live_anchor is None:
        return None, anchor_selection
    overlay, metadata = build_aligned_overlay(
        frame_shape, map_id, floor, live_anchor, opacity
    )
    if overlay is None:
        return None, metadata
    metadata["anchorSelection"] = anchor_selection
    metadata["profileSelection"] = profile_selection
    return overlay, metadata
