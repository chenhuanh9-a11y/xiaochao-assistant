"""Read current S0 vertical floor controls from complete signed label evidence.

The search band and player calibration restrict observation, not click positions.
Only the declared mode order 2/1 or 2/1/-1 is accepted; S1 stays unvalidated.
"""
from __future__ import annotations

import math

import cv2
import numpy as np
from floor_contract import floor_contract, S0_MODE_FLOORS, S0_FLOOR_NAMES

# Supported S0 semantic values. The vertical visual order is their reverse.
MODE_FLOORS = dict(S0_MODE_FLOORS)
FLOOR_NAMES = dict(S0_FLOOR_NAMES)

FILL_LEVEL = 62        # selected tile fill; unselected panels and backdrop stay below
GLYPH_LEVEL = 140      # button labels
MIN_REGION_PIXELS = (24, 10)
PITCH_RATIO = .94
BACKDROP_MAX = 75     # median of the unselected buttons and the gaps between them
# New S0 HUD: search the right-side column, excluding the zoom slider.
# These are search bounds, never fixed click coordinates.
VERTICAL_SEARCH_AREA = (.75, .15, .18, .45)
# Automatic fallback uses the S1 position-search domain, not its four-floor
# reader. Keep the default/fixed pass small and retain all S0 signed checks.
AUTO_VERTICAL_SEARCH_AREA = (.55, .04, .435, .89)


def normalized_region(value) -> tuple[float, float, float, float] | None:
    """A finite [x, y, w, h] inside the unit square with a usable size, else None."""
    try:
        x, y, w, h = (float(v) for v in value)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (x, y, w, h)):
        return None
    if w < .01 or h < .005 or x < 0 or y < 0 or x + w > 1.0001 or y + h > 1.0001:
        return None
    return x, y, w, h


def _unread(reason: str, **extra) -> dict:
    return dict(selectedFloor=None, reason=reason, confidence=0., **extra)


def _candidate_tiles(gray: np.ndarray, kernel: int, min_height: float, max_height: float, limit: int = 8):
    """Dense, tile-shaped bright fills, largest first."""
    mask = (gray > FILL_LEVEL).astype(np.uint8)
    # Label strokes and cursor lines are thin; the highlight is a filled block.
    k = max(3, int(kernel) | 1)
    opened = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((k, k), np.uint8))
    count, _labels, stats, _ = cv2.connectedComponentsWithStats(opened, 8)
    tiles = []
    for x, y, w, h, area in stats[1:count]:
        if not min_height <= h <= max_height or not .7 <= w / max(h, 1) <= 2.8:
            continue
        if mask[y:y + h, x:x + w].mean() < .6:
            continue
        tiles.append((int(x), int(y), int(w), int(h), int(area)))
    return sorted(tiles, key=lambda tile: tile[4], reverse=True)[:limit]


def _labelled_button(gray: np.ndarray, center: float, top: int, width: int, height: int,
                     selected: tuple[int, int]):
    """True/False for a labelled unselected button around ``center``; None if clipped."""
    half = width / 2
    # Only the label area must be inside; the soft tile border may be cut.
    if center - half * .6 < 0 or center + half * .6 > gray.shape[1]:
        return None
    # Spacing is close to one tile but not exact; allow a small slide, never
    # onto the highlighted tile itself.
    for shift in (0., -.15, .15, -.3, .3):
        cx = center + shift * width
        x0, x1 = int(round(cx - half * .6)), int(round(cx + half * .6))
        if x0 < 0 or x1 > gray.shape[1] or (x0 < selected[1] and x1 > selected[0]):
            continue
        window = gray[top:top + height, x0:x1]
        if not window.size:
            continue
        glyph = float((window > GLYPH_LEVEL).mean())
        midtone = float(((window > FILL_LEVEL) & (window <= GLYPH_LEVEL)).mean())
        # A label is a few percent of bright strokes on a dark panel; the
        # textured highlight is mostly mid-tones, an empty backdrop has neither.
        if .015 <= glyph <= .45 and midtone < .12:
            return True
    return False


def _read_axis(gray: np.ndarray, floors, tiles, origin, size, reason: str) -> dict:
    """Read one transposed column with exactly the mode's signed label count."""
    if not tiles:
        return _unread("highlight-not-found")
    total = len(floors)
    accepted, first_failure = [], None
    for tx, ty, tw, th, _area in tiles:
        center = tx + tw / 2
        # The filled block's soft edge makes it slightly wider than the spacing.
        pitch = tw * PITCH_RATIO

        def walk(direction):
            found = 0
            for step in range(1, total + 1):
                present = _labelled_button(gray, center + direction * step * pitch, ty, tw, th, (tx, tx + tw))
                if present is None:
                    return found, True
                if not present:
                    return found, False
                found += 1
            return found, False

        (left, clipped_left), (right, clipped_right) = walk(-1), walk(1)
        # Every button must be seen. A button cut by the region edge or covered
        # by something else is unknown, and guessing from the visible side
        # reads the wrong floor, so it yields no floor at all.
        if left + right + 1 != total:
            if first_failure is None:
                first_failure = _unread("too-many-buttons" if left + right + 1 > total else "buttons-incomplete",
                                        left=left, right=right, clippedLeft=clipped_left,
                                        clippedRight=clipped_right)
            continue
        # The unselected buttons sit on the dark map HUD; light app or phone
        # UI never does. The highlighted tile itself is left out.
        row = gray[ty:ty + th]
        span = np.r_[max(0, int(center - (left + .5) * pitch)):tx,
                     tx + tw:min(row.shape[1], int(center + (right + .5) * pitch) + 1)]
        band = row[:, span]
        if band.size and float(np.median(band)) > BACKDROP_MAX:
            if first_failure is None:
                first_failure = _unread("light-background")
            continue
        accepted.append((left, (tx, ty, tw, th), center, pitch))
    if not accepted:
        return first_failure
    if len(accepted) > 1:
        return _unread("columns-ambiguous")
    index, (tx, ty, tw, th), center, pitch = accepted[0]
    floor = floors[index]
    fill = float(np.median(gray[ty:ty + th, tx:tx + tw]))
    (x0, y0), (fw, fh) = origin, size
    centers = [(x0 + center + (i - index) * pitch) / fw for i in range(total)]
    return dict(selectedFloor=floor, floorName=FLOOR_NAMES[floor], reason=reason,
                confidence=min(1., max(0., (fill - FILL_LEVEL) / 30.)), left=index, right=total - 1 - index,
                tileBounds=[(x0 + tx) / fw, (y0 + ty) / fh, tw / fw, th / fh], centers=centers,
                pitch=pitch / fw)


def _vertical_label_evidence(gray, points, bounds, order, selected, origin, size):
    """Check signed glyph structure, so cropped S1 negative labels are not S0.

    This is deliberately a narrow 1/2/-1 shape check, not general OCR. Every
    label must be fully inside the ROI and have the expected sign and width.
    Unsupported fonts/occlusion fail closed rather than relying on count alone.
    """
    fw, fh = size
    ox, oy = origin
    tw, th = bounds[2] * fw, bounds[3] * fh
    evidence = []
    for floor, (cx, cy) in zip(order, points):
        x, y = cx * fw - ox, cy * fh - oy
        x0, x1 = int(x - tw * .35), int(x + tw * .35)
        y0, y1 = int(y - th * .30), int(y + th * .30)
        if x0 < 0 or y0 < 0 or x1 > gray.shape[1] or y1 > gray.shape[0]:
            return None
        patch = gray[y0:y1, x0:x1]
        mask = (patch < FILL_LEVEL if floor == selected else patch > GLYPH_LEVEL).astype(np.uint8)
        _, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        digits = [r for r in stats[1:] if .16 * th <= r[3] <= .40 * th
                  and .035 * tw <= r[2] <= .30 * tw and r[4] >= .002 * tw * th]
        if len(digits) != 1:
            return None
        dx, dy, dw, dh, _ = digits[0]
        if dx == 0 or dy == 0 or dx + dw >= patch.shape[1] or dy + dh >= patch.shape[0]:
            return None
        ratio = dw / dh
        if not ((.10 <= ratio <= .40) if abs(floor) == 1 else (.40 < ratio <= .85)):
            return None
        signs = [r for r in stats[1:] if r[0] + r[2] < dx and .30 * dh <= r[2] <= .85 * dh
                 and r[3] <= .22 * dh and abs(r[1] + r[3] / 2 - (dy + dh / 2)) <= .25 * dh]
        if bool(signs) != (floor < 0):
            return None
        evidence.append(dict(floor=floor, negative=bool(signs), digitAspect=float(ratio)))
    return evidence


def _read_vertical(gray, floors, origin, size, reason):
    """Reuse structural reading with transposed pixels, not rotated labels.

    S0 visual order is the reverse of its semantic floor-value order.
    centerPoints are normalized (x, y) pairs, in observedOrder. Deliberately
    omit scalar centers/pitch: click consumers require full 2D geometry.
    This is floor-reading evidence, not verified click geometry.
    """
    width = size[0]
    transposed = np.ascontiguousarray(gray.T)
    tiles = _candidate_tiles(transposed, round(width * .0036), width * .010, width * .06)
    order = tuple(reversed(floors))
    read = _read_axis(transposed, order, tiles, origin[::-1], size[::-1], reason)
    if read.get("selectedFloor") is None:
        return read
    y, x, h, w = read.pop("tileBounds")
    centers = read.pop("centers")
    before, after = read.pop("left"), read.pop("right")
    points = [[x + w / 2, cy] for cy in centers]
    evidence = _vertical_label_evidence(gray, points, [x, y, w, h], order,
                                        read["selectedFloor"], origin, size)
    if evidence is None:
        return _unread("vertical-labels-unverified")
    pitch = read.pop("pitch")
    return dict(read, controlAxis="vertical", observedOrder=list(order),
                tileBounds=[x, y, w, h], centerPoints=points,
                axisPitch=pitch, before=before, after=after,
                labelEvidence=evidence, controlGeometryVerified=False)


def _column_background(gray, read, origin, size):
    """Measure only this proved column's unselected panels and gaps."""
    ox, oy = origin
    fw, fh = size
    x, y, w, h = read["tileBounds"]
    left, right = max(0, int(x * fw - ox)), min(gray.shape[1], math.ceil((x + w) * fw - ox))
    centers = [point[1] * fh - oy for point in read["centerPoints"]]
    top = max(0, int(min(centers) - h * fh / 2))
    bottom = min(gray.shape[0], math.ceil(max(centers) + h * fh / 2))
    rows = np.arange(top, bottom)
    rows = rows[(rows < y * fh - oy) | (rows >= (y + h) * fh - oy)]
    pixels = gray[rows, left:right]
    return float(np.median(pixels)) if pixels.size else None


def _read_vertical_contrast(gray, floors, origin, size, reason):
    """Compare two complete proofs at fixed exposures, never combine rows.

    A raised map panel can merge with its highlight at the original threshold.
    Always inspect one bounded lower exposure, including on original success:
    using the whole ROI's brightness to skip it hides small bright columns and
    makes enlarging the search region erase observed contradictions.
    """
    original = _read_vertical(gray, floors, origin, size, reason)
    if original.get("reason") == "columns-ambiguous":
        return original
    exposure = 2. / 3.
    normalized = cv2.convertScaleAbs(gray, alpha=exposure)
    read = _read_vertical(normalized, floors, origin, size, reason)
    if read.get("reason") == "columns-ambiguous":
        return _unread("columns-ambiguous", controlExposure=exposure)
    if original.get("selectedFloor") is not None:
        # Never replace an original success or let it hide a second complete
        # column/selected row proved at another exposure.
        conflict = False
        if read.get("selectedFloor") is not None:
            tolerance_x = max(original["tileBounds"][2], read["tileBounds"][2]) * .25
            tolerance_y = max(original["tileBounds"][3], read["tileBounds"][3]) * .25
            conflict = (read["selectedFloor"] != original["selectedFloor"]
                or any(abs(a[0] - b[0]) > tolerance_x or abs(a[1] - b[1]) > tolerance_y
                       for a, b in zip(original["centerPoints"], read["centerPoints"])))
        return (_unread("columns-ambiguous", controlExposure=exposure)
                if conflict else original)
    if read.get("selectedFloor") is not None:
        # Qualify the actual new column, not unrelated pixels elsewhere in the
        # search ROI. Keep the original bounded raised-panel policy; bright
        # app UI and low-contrast labels are not relaxed into a positive.
        background = _column_background(gray, read, origin, size)
        if background is not None and FILL_LEVEL < background <= 90:
            return dict(read, controlExposure=exposure, controlBackground=background)
    return original


def _merge_region_read(fixed, read, source, **extra):
    """A calibrated reading replaces the current vertical observation completely."""
    out = dict(read, **extra, floorSource=source)
    out.update({key: fixed[key] for key in ("season", "mode", "mapOpen") if key in fixed})
    return out


def _crop(frame, box):
    fh, fw = frame.shape[:2]
    x0, y0 = int(fw * box[0]), int(fh * box[1])
    x1, y1 = min(fw, math.ceil(fw * (box[0] + box[2]))), min(fh, math.ceil(fh * (box[1] + box[3])))
    if x1 - x0 < MIN_REGION_PIXELS[0] or y1 - y0 < MIN_REGION_PIXELS[1]:
        return None, (x0, y0)
    return cv2.cvtColor(frame[y0:y1, x0:x1, :3], cv2.COLOR_BGR2GRAY), (x0, y0)


def read_floor_in_region(frame: np.ndarray, region, mode: str, *, season="s0") -> dict:
    """Read the complete current vertical selector inside a normalized region."""
    contract = floor_contract(mode, season=season)
    if contract is None or season != "s0":
        return _unread("vertical-floor-detector-unvalidated" if contract else "floor-contract-unavailable")
    box = normalized_region(region)
    if frame is None or getattr(frame, "ndim", 0) != 3 or box is None:
        return _unread("region-invalid")
    gray, origin = _crop(frame, box)
    if gray is None:
        return _unread("region-too-small")
    read = _read_vertical_contrast(gray, contract.observed_values, origin, frame.shape[1::-1], "region-highlight-read")
    return dict(read, controlSearchRegion=list(box))


def locate_vertical_floor_controls(frame: np.ndarray, mode: str, *, season="s0", expanded=False) -> dict:
    """Read only the new S0 column; never infer a season from button count."""
    contract = floor_contract(mode, season=season)
    if season != "s0" or contract is None:
        return _unread("vertical-floor-detector-unvalidated" if contract else "floor-contract-unavailable")
    if frame is None or getattr(frame, "ndim", 0) != 3:
        return _unread("region-invalid")
    area = AUTO_VERTICAL_SEARCH_AREA if expanded else VERTICAL_SEARCH_AREA
    column, origin = _crop(frame, area)
    result = (_read_vertical_contrast(column, contract.observed_values, origin, frame.shape[1::-1], "auto-located")
              if column is not None else _unread("region-too-small"))
    if not expanded:
        return dict(result, controlSearchRegion=list(area))
    # The broad search domain is metadata, never an automatic screenshot ROI.
    # Once verified, retain only the measured column for failure diagnostics.
    # An unread broad pass supplies no new pixel-saving region.
    height, width = frame.shape[:2]
    result["controlSearchBounds"] = [origin[0], origin[1],
        min(width, math.ceil(width * (area[0] + area[2]))),
        min(height, math.ceil(height * (area[1] + area[3])))]
    if result.get("selectedFloor") is not None:
        x, y, w, h = result["tileBounds"]
        ys = [point[1] for point in result["centerPoints"]]
        left, right = max(0., x - .3 * w), min(1., x + 1.3 * w)
        top, bottom = max(0., min(ys) - .7 * h), min(1., max(ys) + .7 * h)
        result["controlSearchRegion"] = [left, top, right - left, bottom - top]
    return result


def region_for_client(setting, size) -> tuple[float, float, float, float] | None:
    """The saved region when it was framed on a client of the same shape.

    The HUD keeps its place relative to the client only while the aspect ratio
    is unchanged; a region framed for another shape would point at the wrong
    pixels, so it is not used until the player frames it again.
    """
    if not isinstance(setting, dict):
        return None
    rect = normalized_region(setting.get("rect"))
    try:
        saved_w, saved_h = (int(v) for v in setting.get("clientSize"))
        width, height = (int(v) for v in size)
    except (TypeError, ValueError):
        return None
    if rect is None or min(saved_w, saved_h, width, height) <= 0:
        return None
    if abs((width / height) / (saved_w / saved_h) - 1) > .02:
        return None
    return rect


def _auto(mode, frame, floor_read: dict, source: str, **extra) -> dict:
    """Keep a current read or contradiction; expand only genuine no-reads."""
    if (floor_read.get("selectedFloor") is not None
            or floor_read.get("reason") == "columns-ambiguous"):
        return dict(floor_read, floorSource=source, **extra)
    located = locate_vertical_floor_controls(frame, mode, expanded=True)
    if located["selectedFloor"] is None:
        return dict(floor_read, floorSource=source + "-auto-unread", autoRead=located, **extra)
    return _merge_region_read(floor_read, located, "auto-located", autoRead=located, **extra)


def apply_floor_region(settings, mode, frame, floor_read: dict) -> dict:
    """Apply fixed/default-column or player-region preferences to S0 evidence.

    A framed region can replace the observation, never the declared mode or
    season. Click geometry is separately bound to current map-open evidence.
    """
    settings = settings or {}
    # Reject even a stale positive fixed-detector reading in an unregistered
    # season. Legacy callers without gameSeason retain the exact S0 path.
    season = settings.get("gameSeason", "s0")
    if season != "s0":
        contract = floor_contract(mode, season=season)
        return dict(_unread("vertical-floor-detector-unvalidated" if contract is not None else "floor-contract-unavailable"), floorSource="unavailable")
    choice = settings.get("floorControlMode", "auto")
    if choice == "fixed":
        return dict(floor_read, floorSource="fixed")
    if choice != "region":
        return _auto(mode, frame, floor_read, "fixed")
    rect = region_for_client(settings.get("floorControlRegion"), frame.shape[1::-1])
    if rect is None:
        return _auto(mode, frame, floor_read, "fixed-region-unavailable")
    read = read_floor_in_region(frame, rect, mode)
    if read.get("reason") == "columns-ambiguous":
        return _merge_region_read(floor_read, read, "region", regionRead=read)
    if read["selectedFloor"] is None:
        return _auto(mode, frame, floor_read, "fixed-region-unread", regionRead=read)
    return _merge_region_read(floor_read, read, "region", regionRead=read)
