"""Original map/floor visual observations, independent of native capture and UI."""
from __future__ import annotations
import math
import cv2
import numpy as np
from client_space_geometry import CLIENT_COMPONENT_POLICIES, centered_safe_viewport, normalized_rect_to_client

MAP_REGIONS = {
    "floorSwitch": (0.77, 0.04, 0.14, 0.17, 0.025),
    "closeButton": (0.90, 0.04, 0.095, 0.24, 0.025),
    "zoomControl": (0.91, 0.27, 0.085, 0.52, 0.018),
    "bottomControls": (0.78, 0.77, 0.21, 0.22, 0.025),
}

BACKDROP = (0.25, 0.14, 0.63, 0.66, 0.45)

MAP_INTERFACE_MIN_WIDTH = 1000

MAP_INTERFACE_MIN_ASPECT = 1.25

MAP_INTERFACE_WIDE_ASPECT = 1.45

MAP_INTERFACE_NARROW_BACKDROP = 0.55

FLOOR_BUTTON_REGIONS = {
    -1: (0.727, 0.018, 0.035, 0.057),
    1: (0.766, 0.018, 0.035, 0.057),
    2: (0.805, 0.018, 0.035, 0.057),
}

def floor_control_vertical_geometry(width: int, height: int) -> dict:
    """Map the wide HUD's floor-control band into a narrow safe viewport.

    When a client is narrower than 16:9, the game keeps the floor controls
    inside a vertically centered 16:9 safe viewport.  Their x positions still
    scale with the full client, but their y positions must include that safe
    viewport's top inset.  This covers 16:10, 4:3, and intermediate ratios
    without hard-coding individual resolutions.
    """
    w, h = max(1, int(width)), max(1, int(height))
    viewport = centered_safe_viewport((w, h))
    # Keep normalized HUD geometry exact; integer rounding belongs only at the
    # final pixel crop in ``normalized_rect_to_client``.
    safe_height = float(
        w * 9.0 / 16.0
        if viewport.mode == "centered-safe-height" else h
    )
    safe_top = float(
        (h - safe_height) / 2.0
        if viewport.mode == "centered-safe-height" else 0
    )
    if safe_top <= 0.5:
        return {
            "layoutFamily": "wide",
            "safeTop": 0.0,
            "safeHeight": 1.0,
            "searchTop": 0.015,
            "searchBottom": 0.090,
            "regionTop": 0.018,
            "regionHeight": 0.057,
        }
    def safe_y(value: float) -> float:
        return float((safe_top + safe_height * value) / h)

    return {
        "layoutFamily": "centered-16x9-safe-area",
        "safeTop": float(safe_top / h),
        "safeHeight": float(safe_height / h),
        "searchTop": safe_y(0.015),
        "searchBottom": safe_y(0.090),
        "regionTop": safe_y(0.018),
        "regionHeight": float(safe_height * 0.057 / h),
    }

def _legacy_floor_control_vertical_geometry() -> dict:
    return {
        "layoutFamily": "cropped-wide-hud",
        "safeTop": 0.0,
        "safeHeight": 1.0,
        "searchTop": 0.015,
        "searchBottom": 0.090,
        "regionTop": 0.018,
        "regionHeight": 0.057,
    }

def _floor_button_layout_in_geometry(
    frame: np.ndarray, vertical: dict,
) -> tuple[list[float], dict] | None:
    """Anchor the triplet to a filled button, not three strong text columns."""
    if frame is None or frame.ndim != 3:
        return None
    h, w = frame.shape[:2]
    if w < 1000 or h < 600:
        return None
    x0, x1 = int(w * 0.70), int(w * 0.995)
    y0 = int(h * vertical["searchTop"])
    y1 = min(h, int(h * (vertical["searchBottom"] + vertical["regionHeight"])))
    gray = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    if gray.size == 0:
        return None
    unit = vertical["safeHeight"] * h
    kw, kh = max(3, round(w * 0.020) | 1), max(3, round(unit * 0.030) | 1)
    # Dense texture survives cursor cuts; sparse text strokes do not fill a
    # button-sized window. A component of this density field anchors one tile.
    density = cv2.boxFilter((gray > 60).astype(np.float32), -1, (kw, kh), normalize=True)
    _, _, stats, _ = cv2.connectedComponentsWithStats((density > 0.70).astype(np.uint8), 8)
    best = None
    filled_floors = set()
    for bx, by, bw, bh, _area in stats[1:]:
        tile_width, tile_height = bw + kw * 0.5, bh + kh * 0.5
        if not (w * 0.020 <= tile_width <= w * 0.060
                and unit * 0.022 <= tile_height <= unit * 0.085
                and 0.8 <= tile_width / tile_height <= 2.2):
            continue
        top, bottom = max(0, int(by - kh * 0.25)), min(gray.shape[0], int(by + bh + kh * 0.25))
        measured = dict(vertical, regionTop=max(0.0, (y0 + top - 0.022 * unit) / h))
        measure_top = int(h * measured["regionTop"])
        measure_bottom = math.ceil(h * (measured["regionTop"] + measured["regionHeight"]))
        # Label peaks must come from the same rows that will be scored. Text
        # below a tile cannot complete an otherwise occluded floor triplet.
        strip = cv2.cvtColor(frame[measure_top:measure_bottom, x0:x1], cv2.COLOR_BGR2GRAY).astype(np.float32)
        baseline = np.percentile(strip, 20, axis=0)
        signal = np.maximum(strip - baseline[None, :], 0.0).mean(axis=0)
        smooth = cv2.GaussianBlur(signal[None, :], (max(5, round(w * 0.003) | 1), 1), 0).ravel()
        local = np.flatnonzero((smooth[1:-1] >= smooth[:-2]) & (smooth[1:-1] > smooth[2:])) + 1
        anchor = int(bx + bw / 2)
        peaks = []
        for index in sorted(local.tolist(), key=lambda value: float(smooth[value]), reverse=True):
            if smooth[index] < 7.0:
                break
            if bx - kw * 0.25 <= index < bx + bw + kw * 0.25:
                continue
            if all(abs(index - existing) >= max(12, round(w * 0.020)) for existing in peaks):
                peaks.append(index)
            if len(peaks) >= 12:
                break
        peaks.append(anchor)
        peaks.sort()
        for ai in range(len(peaks) - 2):
            for bi in range(ai + 1, len(peaks) - 1):
                for ci in range(bi + 1, len(peaks)):
                    first, second, third = peaks[ai], peaks[bi], peaks[ci]
                    if anchor not in (first, second, third):
                        continue
                    left, right = second - first, third - second
                    if not (w * 0.020 <= min(left, right)
                            and max(left, right) <= min(w * 0.080, tile_width * 1.20)):
                        continue
                    uneven = abs(left - right) / max(left, right)
                    if uneven > 0.28:
                        continue
                    filled_floors.add((-1, 1, 2)[(first, second, third).index(anchor)])
                    score = float(smooth[first] + smooth[second] + smooth[third]) - 35.0 * uneven
                    if best is None or score > best[0]:
                        best = (score, [float((x0 + index) / w) for index in (first, second, third)], measured)
    if best is None:
        return None
    return best[1], dict(best[2], filledFloor=(next(iter(filled_floors))
                                             if len(filled_floors) == 1 else None))

def _locate_floor_button_layout(
    frame: np.ndarray,
) -> tuple[list[float] | None, dict]:
    if frame is None or frame.ndim != 3:
        return None, _legacy_floor_control_vertical_geometry()
    h, w = frame.shape[:2]
    primary = floor_control_vertical_geometry(w, h)
    found = _floor_button_layout_in_geometry(frame, primary)
    if found is not None:
        return found
    if primary["layoutFamily"] == "centered-16x9-safe-area":
        # Accepted confirmation-memory frames may be right-edge crops of a
        # wide client. Their pixel aspect looks narrow, but the top safe inset
        # has already been removed. Only allow the proven legacy band when the
        # complete triplet is visibly shifted to that crop's far-right edge.
        cropped = _legacy_floor_control_vertical_geometry()
        cropped_layout = _floor_button_layout_in_geometry(frame, cropped)
        cropped_centers = cropped_layout[0] if cropped_layout is not None else None
        if (
            cropped_centers is not None
            and cropped_centers[0] >= 0.82
            and cropped_centers[-1] >= 0.90
        ):
            return cropped_layout
    return None, primary

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

def neutral_ratio(frame: np.ndarray, region: tuple[float, ...], tinted: bool = False) -> float:
    pixels = region_pixels(
        frame, region, policy=CLIENT_COMPONENT_POLICIES["ui-roi"],
    ).astype(np.int16)
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

def backdrop_ratio(frame: np.ndarray) -> float:
    # OpenCV uses BGR; the original detector's condition is blue >= red-4 and blue >= green-8.
    # The backdrop spans most of the screen; stride 4 still samples roughly
    # 94k pixels at 2560x1417 and avoids spending live latency on redundant
    # neighboring pixels.
    p = region_pixels(frame, BACKDROP, stride=4).astype(np.int16)
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
    # Require the actual map-only button triplet for this tolerant branch.
    floor_controls = None
    if damage:
        floor_controls = (floor_read if floor_read is not None else detect_selected_floor(frame))
    tinted_controls_present = bool(floor_controls and floor_controls.get('layout') == 'localized-triplet')
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
        "clientCoordinatePolicies": dict(CLIENT_COMPONENT_POLICIES),
    }

def detect_selected_floor(frame: np.ndarray) -> dict:
    """Read the textured highlight behind the B1/1/2 floor buttons without OCR."""
    h, w = frame.shape[:2]
    centers, vertical = _locate_floor_button_layout(frame)
    if centers is not None:
        layout = "localized-triplet"
    else:
        # Missing labels cannot turn a fixed crop of text/cursor into a floor.
        return {
            "selectedFloor": None,
            "scores": {"-1": 0.0, "1": 0.0, "2": 0.0},
            "margin": 0.0,
            "confidence": 0.0,
            "reason": ("safe-area-floor-triplet-not-localized"
                       if vertical["layoutFamily"] == "centered-16x9-safe-area"
                       else "floor-triplet-not-localized"),
            "layout": "unlocalized",
            "centers": None,
            "layoutFamily": vertical["layoutFamily"],
            "safeTop": vertical["safeTop"],
            "safeHeight": vertical["safeHeight"],
        }
    scores: dict[int, float] = {}
    regions = FLOOR_BUTTON_REGIONS
    if centers is not None:
        regions = {
            floor: (
                center - 0.0175,
                vertical["regionTop"],
                0.035,
                vertical["regionHeight"],
            )
            for floor, center in zip((-1, 1, 2), centers)
        }
    for floor, (x, y, rw, rh) in regions.items():
        region = frame[int(h * y):math.ceil(h * (y + rh)), int(w * x):math.ceil(w * (x + rw))]
        if not region.size:
            scores[floor] = 0.0
            continue
        gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
        # Clip sparse bright glyphs, signatures and cursor strokes; measure
        # the filled highlight background rather than its brightest outliers.
        scores[floor] = float(np.minimum(gray, np.percentile(gray, 75)).std())
    ordered = sorted(scores, key=scores.get, reverse=True)
    top, second = ordered[0], ordered[1]
    margin = scores[top] - scores[second]
    # Preserve the absolute-contrast path. At lower contrast, require two
    # independent observations to agree: the unique filled tile that anchored
    # the layout, and a texture score at least three times the other buttons.
    # A remembered floor, a lone glyph, or an ambiguous tile cannot qualify.
    filled_floor = vertical.get('filledFloor')
    filled_highlight = bool(filled_floor == top and scores[top] >= 3.0 * scores[second])
    selected = top if margin >= 8.0 and (scores[top] >= 25.0 or filled_highlight) else None
    return {
        "selectedFloor": selected,
        "scores": {str(key): value for key, value in scores.items()},
        "margin": margin,
        "confidence": min(1.0, max(0.0, margin / 25.0)) if selected else 0.0,
        "reason": "highlight-detected" if selected else "floor-controls-unavailable-or-ambiguous",
        "layout": layout,
        "filledFloor": filled_floor,
        "highlightEvidence": ("absolute-contrast" if selected and scores[top] >= 25.0
                              else "filled-tile-and-relative-contrast" if selected else None),
        "centers": centers,
        "layoutFamily": vertical["layoutFamily"],
        "safeTop": vertical["safeTop"],
        "safeHeight": vertical["safeHeight"],
    }
