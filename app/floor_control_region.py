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
GLYPH_LEVEL = 140      # upper reference for local light-label contrast
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


def _reader_spec(mode: str, season: str, controls):
    """Return the one detector contract/order/bank/search pair for this read.

    The legacy path deliberately keeps its reviewed static values. An imported
    profile supplies every detector input together; a missing profile context
    never inherits the static S0 reader.
    """
    if controls is None:
        contract = floor_contract(mode, season=season)
        if contract is None or season != "s0":
            return None
        return (contract, tuple(reversed(contract.observed_values)), None,
                VERTICAL_SEARCH_AREA, AUTO_VERTICAL_SEARCH_AREA)
    try:
        context = controls.for_context(season, mode)
        contract = context.contract
        order = tuple(context.visual_order)
        templates = controls.templates
        fixed = tuple(controls.fixed_search_region)
        expanded = tuple(controls.expanded_search_region)
    except (AttributeError, TypeError, ValueError):
        return None
    values = contract.observed_values
    if (contract.season != season or contract.mode != mode
            or contract.control_axis != "vertical"
            or not values or len(set(values)) != len(values)
            or len(order) != len(values) or set(order) != set(values)
            or templates is None or not isinstance(getattr(templates, "family", None), str)
            or normalized_region(fixed) is None or normalized_region(expanded) is None):
        return None
    return contract, order, templates, fixed, expanded


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


def _bright_label_mask(patch):
    """Separate a locally visible light glyph from its panel, not screen gain.

    A thin numeral need not fill a fixed fraction of a larger button. The
    high percentile estimates its ink while the median estimates the panel;
    retain minimum local contrast so a nearly uniform patch supplies no ink.
    """
    background = float(np.median(patch))
    ink = float(np.percentile(patch, 99))
    if ink - background < 24:
        return np.zeros(patch.shape, np.uint8)
    threshold = min(GLYPH_LEVEL, background + (ink - background) * .5)
    threshold = max(background + 12, threshold)
    return (patch > threshold).astype(np.uint8)


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
        mask = _bright_label_mask(window)
        midtone = float(((window > FILL_LEVEL) & (window <= GLYPH_LEVEL)).mean())
        if midtone >= .12:
            continue
        # Transposed glyph components: long axis is numeral height. Presence
        # depends on its shape, not bright-pixel area divided by the button.
        # Exact digit/sign/order is checked for the complete column below.
        _, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        if any(.16 * width <= w <= .40 * width
               and .035 * height <= h <= .30 * height
               and area >= .002 * width * height
               for _x, _y, w, h, area in stats[1:]):
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


def _vertical_label_evidence(gray, points, bounds, order, selected, origin, size,
                             templates=None):
    """Score the whole signed column and measure its actual numeral centers.

    A weak glyph can be supported by the other rows and the measured highlight;
    an alternative digit or missing minus cannot be relabelled by the mode.
    """
    from s0_floor_templates import classify_label
    fw, fh = size
    ox, oy = origin
    tw, th = bounds[2] * fw, bounds[3] * fh
    evidence, measured_points = [], []
    for floor, (cx, cy) in zip(order, points):
        x, y = cx * fw - ox, cy * fh - oy
        x0, x1 = int(x - tw * .35), int(x + tw * .35)
        y0, y1 = int(y - th * .30), int(y + th * .30)
        if x0 < 0 or y0 < 0 or x1 > gray.shape[1] or y1 > gray.shape[0]:
            return None
        if templates is None:
            match = classify_label(gray[y0:y1,x0:x1], selected=floor==selected)
        else:
            match = classify_label(gray[y0:y1,x0:x1], selected=floor==selected,
                                   templates=templates)
        if match is None or match["label"] != floor:
            return None
        score = match["scores"][floor]
        row = dict(floor=floor, negative=floor<0,
                   digitAspect=match["digitAspect"], score=score)
        if templates is not None:
            row["templateFamily"] = match["family"]
        evidence.append(row)
        # Fill edges change with exposure; the recognized numeral does not.
        # Pitch from the fill only proposes the crop. Keep the actual glyph's
        # center for comparing columns and binding current click geometry.
        measured_points.append([cx, (oy+y0+match["digitCenterY"])/fh])
    scores = [row["score"] for row in evidence]
    if (min(scores) < .45 or sum(score >= .70 for score in scores) < len(scores)-1
            or float(np.mean(scores)) < .70):
        return None
    return evidence, measured_points


def _read_vertical(gray, floors, origin, size, reason, *, visual_order=None, templates=None):
    """Template-qualified whole columns, with observed 2D click geometry.

    Filled tiles propose columns; they do not decide numeral identity. Verify
    every proposal before detecting ambiguity, so an unrelated thin UI glyph
    cannot veto a valid column merely by resembling the old geometric sketch.
    """
    width = size[0]
    transposed = np.ascontiguousarray(gray.T)
    tiles = _candidate_tiles(transposed, round(width * .0036), width * .010, width * .06)
    order = tuple(reversed(floors)) if visual_order is None else tuple(visual_order)
    accepted, first_failure = [], None
    for tile in tiles:
        read = _read_axis(transposed, order, [tile], origin[::-1], size[::-1], reason)
        if read.get("selectedFloor") is None:
            first_failure = first_failure or read
            continue
        y, x, h, w = read.pop("tileBounds")
        centers = read.pop("centers")
        before, after = read.pop("left"), read.pop("right")
        points = [[x + w / 2, cy] for cy in centers]
        labels = _vertical_label_evidence(gray, points, [x,y,w,h], order,
                                          read["selectedFloor"], origin, size,
                                          templates=templates)
        if labels is None:
            first_failure = first_failure or _unread("vertical-labels-unverified")
            continue
        evidence, points = labels
        read.pop("pitch")
        pitch = float(np.mean(np.diff([point[1] for point in points])))
        result = dict(read, controlAxis="vertical", observedOrder=list(order),
                      tileBounds=[x,y,w,h], centerPoints=points, axisPitch=pitch,
                      before=before, after=after, labelEvidence=evidence,
                      controlGeometryVerified=False)
        background = _column_background(gray,result,origin,size)
        tx, ty = round(x*size[0]-origin[0]), round(y*size[1]-origin[1])
        patch = gray[ty:ty+round(h*size[1]),tx:tx+round(w*size[0])]
        if background is None or not patch.size or float(np.median(patch))-background < 12:
            first_failure = first_failure or _unread("highlight-not-found")
            continue
        accepted.append(result)
    if len(accepted) > 1:
        return _unread("columns-ambiguous")
    return accepted[0] if accepted else first_failure or _unread("highlight-not-found")


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


def _read_vertical_contrast(gray, floors, origin, size, reason, *,
                            visual_order=None, templates=None):
    """Compare complete column proofs at bounded, ROI-independent exposures.

    Darkening separates a raised panel from its fill; brightening restores a
    dim highlight's extent. Every exposure proves the full signed column on
    its own. No rows are combined and any complete contradiction is retained.
    """
    original = _read_vertical(gray, floors, origin, size, reason,
                              visual_order=visual_order, templates=templates)
    if original.get("reason") == "columns-ambiguous":
        return original
    accepted = [(original, 1.)] if original.get("selectedFloor") is not None else []
    for exposure in (2. / 3., 1.5):
        read = _read_vertical(cv2.convertScaleAbs(gray, alpha=exposure), floors,
                              origin, size, reason, visual_order=visual_order,
                              templates=templates)
        if read.get("reason") == "columns-ambiguous":
            return _unread("columns-ambiguous", controlExposure=exposure)
        if read.get("selectedFloor") is None:
            continue
        for previous, _ in accepted:
            tolerance_x = max(previous["tileBounds"][2], read["tileBounds"][2]) * .25
            tolerance_y = max(previous["tileBounds"][3], read["tileBounds"][3]) * .25
            if (read["selectedFloor"] != previous["selectedFloor"]
                    or any(abs(a[0]-b[0]) > tolerance_x or abs(a[1]-b[1]) > tolerance_y
                           for a,b in zip(previous["centerPoints"],read["centerPoints"]))):
                return _unread("columns-ambiguous", controlExposure=exposure)
        accepted.append((read, exposure))
    if not accepted:
        return original
    read, exposure = accepted[0]
    if exposure == 1.:
        return read
    return dict(read, controlExposure=exposure,
                controlBackground=_column_background(gray,read,origin,size))


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


def read_floor_in_region(frame: np.ndarray, region, mode: str, *, season="s0",
                         controls=None) -> dict:
    """Read the complete current vertical selector inside a normalized region."""
    spec = _reader_spec(mode, season, controls)
    if spec is None:
        if controls is not None:
            return _unread("controls-profile-context-unavailable", season=season, mode=mode)
        contract = floor_contract(mode, season=season)
        return _unread("vertical-floor-detector-unvalidated" if contract else "floor-contract-unavailable")
    contract, order, templates, _fixed_area, _expanded_area = spec
    box = normalized_region(region)
    if frame is None or getattr(frame, "ndim", 0) != 3 or box is None:
        return _unread("region-invalid")
    gray, origin = _crop(frame, box)
    if gray is None:
        return _unread("region-too-small")
    read = _read_vertical_contrast(gray, contract.observed_values, origin,
                                   frame.shape[1::-1], "region-highlight-read",
                                   visual_order=order, templates=templates)
    if controls is not None:
        read["controlProfileFamily"] = templates.family
    return dict(read, controlSearchRegion=list(box))


def locate_vertical_floor_controls(frame: np.ndarray, mode: str, *, season="s0",
                                   expanded=False, controls=None) -> dict:
    """Read only the new S0 column; never infer a season from button count."""
    spec = _reader_spec(mode, season, controls)
    if spec is None:
        if controls is not None:
            return _unread("controls-profile-context-unavailable", season=season, mode=mode)
        contract = floor_contract(mode, season=season)
        return _unread("vertical-floor-detector-unvalidated" if contract else "floor-contract-unavailable")
    contract, order, templates, fixed_area, expanded_area = spec
    if frame is None or getattr(frame, "ndim", 0) != 3:
        return _unread("region-invalid")
    area = expanded_area if expanded else fixed_area
    column, origin = _crop(frame, area)
    result = (_read_vertical_contrast(column, contract.observed_values, origin,
                                      frame.shape[1::-1], "auto-located",
                                      visual_order=order, templates=templates)
              if column is not None else _unread("region-too-small"))
    if controls is not None:
        result["controlProfileFamily"] = templates.family
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


def _auto(mode, frame, floor_read: dict, source: str, *, season="s0", controls=None,
          **extra) -> dict:
    """Keep a current read or contradiction; expand only genuine no-reads."""
    if (floor_read.get("selectedFloor") is not None
            or floor_read.get("reason") == "columns-ambiguous"):
        return dict(floor_read, floorSource=source, **extra)
    located = locate_vertical_floor_controls(frame, mode, season=season,
                                              expanded=True, controls=controls)
    if located["selectedFloor"] is None:
        return dict(floor_read, floorSource=source + "-auto-unread", autoRead=located, **extra)
    return _merge_region_read(floor_read, located, "auto-located", autoRead=located, **extra)


def apply_floor_region(settings, mode, frame, floor_read: dict, *, controls=None) -> dict:
    """Apply fixed/default-column or player-region preferences to S0 evidence.

    A framed region can replace the observation, never the declared mode or
    season. Click geometry is separately bound to current map-open evidence.
    """
    settings = settings or {}
    # Reject even a stale positive fixed-detector reading in an unregistered
    # season. Legacy callers without gameSeason retain the exact S0 path.
    season = settings.get("gameSeason", "s0")
    spec = _reader_spec(mode, season, controls)
    if controls is not None and spec is None:
        return dict(_unread("controls-profile-context-unavailable", season=season, mode=mode),
                    floorSource="unavailable")
    if season != "s0":
        contract = floor_contract(mode, season=season)
        return dict(_unread("vertical-floor-detector-unvalidated" if contract is not None else "floor-contract-unavailable"), floorSource="unavailable")
    choice = settings.get("floorControlMode", "auto")
    if choice == "fixed":
        return dict(floor_read, floorSource="fixed")
    if choice != "region":
        return _auto(mode, frame, floor_read, "fixed", season=season, controls=controls)
    rect = region_for_client(settings.get("floorControlRegion"), frame.shape[1::-1])
    if rect is None:
        return _auto(mode, frame, floor_read, "fixed-region-unavailable",
                     season=season, controls=controls)
    read = read_floor_in_region(frame, rect, mode, season=season, controls=controls)
    if read.get("reason") == "columns-ambiguous":
        return _merge_region_read(floor_read, read, "region", regionRead=read)
    if read["selectedFloor"] is None:
        return _auto(mode, frame, floor_read, "fixed-region-unread", season=season,
                     controls=controls, regionRead=read)
    return _merge_region_read(floor_read, read, "region", regionRead=read)
