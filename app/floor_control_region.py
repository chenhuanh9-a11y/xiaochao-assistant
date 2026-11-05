"""Read the selected floor from the structure of the floor-button row.

The game slides the whole floor-button row so the *selected* button keeps one
screen position. A fixed rectangle split into equal slots would therefore
always see the highlight in the same slot. The floor is instead read from
structure: a filled (highlighted) tile, then how many labelled buttons sit to
its left and right. The same reading runs inside a player-drawn region (框选)
or over the client's top-right corner to find the row automatically (自动).
"""
from __future__ import annotations

import math

import cv2
import numpy as np
from floor_contract import floor_contract, S0_MODE_FLOORS, S0_FLOOR_NAMES

# Floors in on-screen order, left to right, as the detectors report them.
MODE_FLOORS = dict(S0_MODE_FLOORS)
FLOOR_NAMES = dict(S0_FLOOR_NAMES)

FILL_LEVEL = 62        # selected tile fill; unselected panels and backdrop stay below
GLYPH_LEVEL = 140      # button labels
MIN_REGION_PIXELS = (24, 10)
PITCH_RATIO = .94
BACKDROP_MAX = 75     # median of the unselected buttons and the gaps between them
# Every supported layout keeps the floor buttons in the client's top-right corner.
SEARCH_AREA = (.5, 0., .5, .35)


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


def _read_row(gray: np.ndarray, floors, tiles, origin, size, reason: str) -> dict:
    """Floor from the first tile with exactly the mode's button count around it."""
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
            for step in range(1, total):
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
    if len({row[0] for row in accepted}) > 1:
        return _unread("rows-disagree")
    index, (tx, ty, tw, th), center, pitch = accepted[0]
    floor = floors[index]
    fill = float(np.median(gray[ty:ty + th, tx:tx + tw]))
    (x0, y0), (fw, fh) = origin, size
    centers = [(x0 + center + (i - index) * pitch) / fw for i in range(total)]
    return dict(selectedFloor=floor, floorName=FLOOR_NAMES[floor], reason=reason,
                confidence=min(1., max(0., (fill - FILL_LEVEL) / 30.)), left=index, right=total - 1 - index,
                tileBounds=[(x0 + tx) / fw, (y0 + ty) / fh, tw / fw, th / fh], centers=centers,
                pitch=pitch / fw)


def _crop(frame, box):
    fh, fw = frame.shape[:2]
    x0, y0 = int(fw * box[0]), int(fh * box[1])
    x1, y1 = min(fw, math.ceil(fw * (box[0] + box[2]))), min(fh, math.ceil(fh * (box[1] + box[3])))
    if x1 - x0 < MIN_REGION_PIXELS[0] or y1 - y0 < MIN_REGION_PIXELS[1]:
        return None, (x0, y0)
    return cv2.cvtColor(frame[y0:y1, x0:x1, :3], cv2.COLOR_BGR2GRAY), (x0, y0)


def read_floor_in_region(frame: np.ndarray, region, mode: str, *, season="s0") -> dict:
    """Selected floor (-1/1/2) inside ``region`` (normalized client [x, y, w, h])."""
    contract = floor_contract(mode, season=season)
    floors = contract.observed_values if contract is not None else None
    box = normalized_region(region)
    if floors is None:
        return _unread("unsupported-mode" if season == "s0" else "floor-contract-unavailable")
    if contract.control_axis != "horizontal":
        return _unread("vertical-floor-detector-unvalidated")
    if box is None or frame is None or getattr(frame, "ndim", 0) != 3:
        return _unread("region-invalid")
    gray, origin = _crop(frame, box)
    if gray is None:
        return _unread("region-too-small")
    # The player framed the row: its tile fills a good part of the height.
    height = gray.shape[0]
    tiles = _candidate_tiles(gray, round(height * .12), height * .25, height)
    return _read_row(gray, floors, tiles, origin, frame.shape[1::-1], "region-highlight-read")


def locate_floor_row(frame: np.ndarray, mode: str, *, season="s0") -> dict:
    """Find the floor-button row anywhere in the top-right corner and read it.

    Nothing about the HUD's position is assumed; tile size is bounded only
    relative to the client width, and the mode's exact button count around the
    highlight is required, so other bright HUD items do not qualify.
    """
    contract = floor_contract(mode, season=season)
    floors = contract.observed_values if contract is not None else None
    if floors is None:
        return _unread("unsupported-mode" if season == "s0" else "floor-contract-unavailable")
    if contract.control_axis != "horizontal":
        return _unread("vertical-floor-detector-unvalidated")
    if frame is None or getattr(frame, "ndim", 0) != 3:
        return _unread("region-invalid")
    gray, origin = _crop(frame, SEARCH_AREA)
    if gray is None:
        return _unread("region-too-small")
    width = frame.shape[1]
    tiles = _candidate_tiles(gray, round(width * .0036), width * .010, width * .06)
    return _read_row(gray, floors, tiles, origin, frame.shape[1::-1], "auto-located")


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
    """Fixed reading first; when it reads nothing, locate the row automatically."""
    if floor_read.get("selectedFloor") is not None:
        return dict(floor_read, floorSource=source, **extra)
    located = locate_floor_row(frame, mode)
    if located["selectedFloor"] is None:
        return dict(floor_read, floorSource=source + "-auto-unread", autoRead=located, **extra)
    return dict(floor_read, selectedFloor=located["selectedFloor"], floorSource="auto-located",
                autoRead=located, **extra)


def apply_floor_region(settings, mode, frame, floor_read: dict) -> dict:
    """Choose the floor reading for the player's 楼层识别 setting.

    自动 (default): the fixed detector, then the automatic row search when it
    reads nothing. 框选: the framed region, then the same automatic path. 固定:
    the fixed detector only, exactly as before. The fixed detector keeps owning
    the button geometry used for clicks; ``floorSource`` records the reading.
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
    if read["selectedFloor"] is None:
        return _auto(mode, frame, floor_read, "fixed-region-unread", regionRead=read)
    return dict(floor_read, selectedFloor=read["selectedFloor"], floorSource="region", regionRead=read)
