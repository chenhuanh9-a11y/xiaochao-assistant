"""Original map/floor visual observations, independent of native capture and UI."""
from __future__ import annotations
import math
import cv2
import numpy as np
from client_space_geometry import CLIENT_COMPONENT_POLICIES, centered_safe_viewport, normalized_rect_to_client

MAP_REGIONS = {
    "floorSwitch": (0.75, 0.15, 0.18, 0.45, 0.025),
    "closeButton": (0.90, 0.04, 0.095, 0.24, 0.025),
    "zoomControl": (0.91, 0.27, 0.085, 0.52, 0.018),
    "bottomControls": (0.78, 0.77, 0.21, 0.22, 0.025),
}

BACKDROP = (0.25, 0.14, 0.63, 0.66, 0.45)

MAP_INTERFACE_MIN_WIDTH = 1000

MAP_INTERFACE_MIN_ASPECT = 1.25

MAP_INTERFACE_WIDE_ASPECT = 1.45

MAP_INTERFACE_NARROW_BACKDROP = 0.55

def region_pixels(
    frame: np.ndarray,
    region: tuple[float, ...],
    stride: int = 2,
    *,
    policy: str = "full-client",
) -> np.ndarray:
    h, w = frame.shape[:2]
    x, y, rw, rh = region[:4]
    step = max(1, int(stride))
    x0, y0, x1, y1 = normalized_rect_to_client(
        (w, h), (x, y, rw, rh), policy=policy,
    )
    return frame[y0:y1:step, x0:x1:step]

def _probe_pixels(frame, region, *, stride, policy, excluded_bounds, exposure):
    """Apply pointwise exclusions/gain only to the pixels this probe samples."""
    pixels = region_pixels(frame, region, stride=stride, policy=policy)
    if excluded_bounds:
        pixels = pixels.copy()
        height, width = frame.shape[:2]
        x0, y0, _x1, _y1 = normalized_rect_to_client((width, height), region[:4], policy=policy)
        for left, top, right, bottom in excluded_bounds:
            ix0, iy0 = max(0, (left-x0+stride-1)//stride), max(0, (top-y0+stride-1)//stride)
            ix1, iy1 = min(pixels.shape[1], (right-x0+stride-1)//stride), min(pixels.shape[0], (bottom-y0+stride-1)//stride)
            if ix0 < ix1 and iy0 < iy1:
                pixels[iy0:iy1, ix0:ix1] = 0
    return pixels if exposure == 1. else cv2.convertScaleAbs(pixels, alpha=exposure)


def neutral_ratio(frame: np.ndarray, region: tuple[float, ...], tinted: bool = False,
                  *, excluded_bounds=(), exposure=1.) -> float:
    pixels = _probe_pixels(frame, region, stride=2, policy=CLIENT_COMPONENT_POLICIES["ui-roi"],
                           excluded_bounds=excluded_bounds, exposure=exposure).astype(np.int16)
    if not pixels.size:
        return 0.0
    maximum = pixels.max(axis=2)
    minimum = pixels.min(axis=2)
    mean = pixels.mean(axis=2)
    if tinted:
        mask = (mean >= 52) & (mean <= 230) & ((maximum - minimum) <= 90)
    else:
        mask = (mean >= 90) & (mean <= 230) & ((maximum - minimum) <= 55)
    return float(mask.mean())

def backdrop_ratio(frame: np.ndarray, *, excluded_bounds=(), exposure=1.) -> float:
    # OpenCV uses BGR; the original detector's condition is blue >= red-4 and blue >= green-8.
    # The backdrop spans most of the screen; stride 4 still samples roughly
    # 94k pixels at 2560x1417 and avoids spending live latency on redundant
    # neighboring pixels.
    p = _probe_pixels(frame, BACKDROP, stride=4, policy="full-client",
                      excluded_bounds=excluded_bounds, exposure=exposure).astype(np.int16)
    if not p.size:
        return 0.0
    blue, green, red = p[:, :, 0], p[:, :, 1], p[:, :, 2]
    mean = p.mean(axis=2)
    mask = (mean >= 14) & (mean <= 88) & (blue >= red - 4) & (blue >= green - 8)
    return float(mask.mean())

def backdrop_texture(frame: np.ndarray) -> dict:
    """Separate the flat map canvas from dark/tinted 3-D gameplay frames."""
    pixels = region_pixels(frame, BACKDROP, stride=4)
    if not pixels.size:
        return {"darkRatio": 0.0, "grayStd": 255.0}
    gray = cv2.cvtColor(pixels, cv2.COLOR_BGR2GRAY)
    return {
        "darkRatio": float((gray < 75).mean()),
        "grayStd": float(gray.std()),
    }

def map_interface_shape(width: int, height: int) -> dict:
    """Classify the client layout without treating supported 4:3 as non-map."""
    w, h = int(width), int(height)
    aspect = float(w / max(h, 1))
    if w < MAP_INTERFACE_MIN_WIDTH:
        return {
            "shapeAccepted": False,
            "reason": "capture-shape-width",
            "frameWidth": w,
            "frameHeight": h,
            "aspectRatio": round(aspect, 4),
            "layoutFamily": "unsupported-small",
        }
    if aspect < MAP_INTERFACE_MIN_ASPECT:
        return {
            "shapeAccepted": False,
            "reason": "capture-shape-aspect",
            "frameWidth": w,
            "frameHeight": h,
            "aspectRatio": round(aspect, 4),
            "layoutFamily": "unsupported-narrow",
        }
    return {
        "shapeAccepted": True,
        "reason": "shape-accepted",
        "frameWidth": w,
        "frameHeight": h,
        "aspectRatio": round(aspect, 4),
        "layoutFamily": (
            "wide" if aspect >= MAP_INTERFACE_WIDE_ASPECT else "narrow-4x3"
        ),
    }

def detect_map_interface(frame: np.ndarray, *, floor_read: dict | None = None) -> dict:
    h, w = frame.shape[:2]
    shape = map_interface_shape(w, h)
    if not shape["shapeAccepted"]:
        return {
            **shape,
            "detected": False,
            "matchedAnchors": 0,
            "neutralMatchedAnchors": 0,
            "tintedMatchedAnchors": 0,
            "ratios": {},
            "tintedRatios": {},
            "backdropRatio": 0.0,
            "backdropTexture": {"darkRatio": 0.0, "grayStd": 255.0},
            "flatMapCanvas": False,
            "detectionMode": "shape-rejected",
        }
    ratios = {name: neutral_ratio(frame, region) for name, region in MAP_REGIONS.items()}
    neutral_count = sum(ratios[name] >= MAP_REGIONS[name][4] for name in MAP_REGIONS)
    backdrop = backdrop_ratio(frame)
    texture = backdrop_texture(frame)
    flat_map_canvas = bool(texture["darkRatio"] >= 0.84 and texture["grayStd"] <= 22.5)
    strong = backdrop >= 0.80
    tinted_ratios: dict[str, float] = {}
    tinted_count = 0
    if neutral_count < 3 and strong:
        tinted_ratios = {name: neutral_ratio(frame, region, True) for name, region in MAP_REGIONS.items()}
        tinted_count = sum(tinted_ratios[name] >= MAP_REGIONS[name][4] for name in MAP_REGIONS)
    damage = strong and tinted_count >= 3
    # The broad tinted color bands can also match ordinary dark gameplay.
    # Require complete current signed controls for this tolerant branch.
    floor_controls = None
    if damage:
        floor_controls = (floor_read if floor_read is not None else detect_selected_floor(frame))
    tinted_controls_present = bool(floor_controls
        and floor_controls.get("controlAxis") == "vertical"
        and type(floor_controls.get("selectedFloor")) is int
        and floor_controls.get("selectedFloor") in (-1, 1, 2)
        and floor_controls.get("labelEvidence"))
    matched = max(neutral_count, tinted_count if damage else 0)
    required_backdrop = (
        MAP_INTERFACE_NARROW_BACKDROP
        if shape["layoutFamily"] == "narrow-4x3" else BACKDROP[4]
    )
    detected = bool(
        flat_map_canvas and backdrop >= required_backdrop
        and (neutral_count >= 3 or (damage and tinted_controls_present))
    )
    if detected:
        reason = "map-interface-detected"
    elif not flat_map_canvas:
        reason = "map-canvas-texture-mismatch"
    elif backdrop < required_backdrop:
        reason = "map-backdrop-insufficient"
    elif matched < 3:
        reason = "map-anchors-insufficient"
    elif damage and not tinted_controls_present:
        reason = "tinted-map-controls-unlocalized"
    else:
        reason = "map-evidence-insufficient"
    return {
        **shape,
        "reason": reason,
        "detected": detected,
        "matchedAnchors": int(matched),
        "neutralMatchedAnchors": int(neutral_count),
        "tintedMatchedAnchors": int(tinted_count),
        "ratios": ratios,
        "tintedRatios": tinted_ratios,
        "backdropRatio": backdrop,
        "backdropTexture": texture,
        "flatMapCanvas": flat_map_canvas,
        "detectionMode": "damage-tint" if damage else "neutral",
        "tintedControlsLocalized": tinted_controls_present if damage else None,
        "requiredBackdropRatio": required_backdrop,
        "decisionThresholds": dict(anchorRatios={name: values[4] for name, values in MAP_REGIONS.items()},
            darkRatioMin=.84, grayStdMax=22.5, strongBackdropMin=.80,
            matchedAnchorsMin=3, requiredBackdropRatio=required_backdrop),
        "clientCoordinatePolicies": dict(CLIENT_COMPONENT_POLICIES),
    }

def detect_selected_floor(frame: np.ndarray, *, mode="nightmare", season="s0") -> dict:
    """Observe only the current signed S0 column in an explicitly declared mode."""
    from floor_control_region import locate_vertical_floor_controls
    return locate_vertical_floor_controls(frame, mode, season=season)


def map_preparation_diagnostic(frame, observation, floor_read, *, mode):
    """Describe observed geometry only; never participate in detector decisions.

    The height limit belongs to nightmare floor localization, not map shape.
    Difficult has its own observation contract: do not apply nightmare limits.
    """
    h, w = frame.shape[:2]
    result = dict(frameWidth=int(w), frameHeight=int(h), mode=mode,
        observationReason=observation.get("reason", ""),
        mapDetected=observation.get("mapOpen" if mode == "difficult" else "detected") is True,
        floorReason=floor_read.get("reason", ""), floorLayout=floor_read.get("layout", ""))
    if mode == "nightmare":
        shape = map_interface_shape(w, h)
        result.update(shapeReason=shape["reason"], aspectRatio=shape["aspectRatio"],
            floorControlSizeEligible=w >= 1000 and h >= 600)
    return result
