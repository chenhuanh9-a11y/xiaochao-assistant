"""Read the selected floor inside a player-drawn floor-control region.

The game slides the whole floor-button row so the *selected* button keeps one
screen position. A fixed rectangle split into equal slots would therefore
always see the highlight in the same slot. The player instead frames the full
range the row can occupy, and the floor is read from structure inside it: the
one filled (highlighted) tile, then how many labelled buttons sit to its left
and right.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

# Floors in on-screen order, left to right, as the detectors report them.
MODE_FLOORS = {"nightmare": (-1, 1, 2), "difficult": (1, 2)}
FLOOR_NAMES = {-1: "B1", 1: "1", 2: "2"}

FILL_LEVEL = 62        # selected tile fill; unselected panels and backdrop stay below
GLYPH_LEVEL = 140      # button labels
MIN_REGION_PIXELS = (24, 10)
PITCH_RATIO = .94


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


def _filled_tile(gray: np.ndarray):
    """Bounding box of the largest dense, tile-shaped bright fill, or None."""
    height = gray.shape[0]
    mask = (gray > FILL_LEVEL).astype(np.uint8)
    # Label strokes and cursor lines are thin; the highlight is a filled block.
    k = max(3, round(height * .12) | 1)
    opened = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((k, k), np.uint8))
    count, _labels, stats, _ = cv2.connectedComponentsWithStats(opened, 8)
    best = None
    for x, y, w, h, area in stats[1:count]:
        if h < height * .25 or not .7 <= w / max(h, 1) <= 2.8:
            continue
        if mask[y:y + h, x:x + w].mean() < .6:
            continue
        if best is None or area > best[4]:
            best = (int(x), int(y), int(w), int(h), int(area))
    return best


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


def read_floor_in_region(frame: np.ndarray, region, mode: str) -> dict:
    """Selected floor (-1/1/2) inside ``region`` (normalized client [x, y, w, h])."""
    floors = MODE_FLOORS.get(mode)
    box = normalized_region(region)
    if floors is None:
        return _unread("unsupported-mode")
    if box is None or frame is None or getattr(frame, "ndim", 0) != 3:
        return _unread("region-invalid")
    fh, fw = frame.shape[:2]
    x0, y0 = int(fw * box[0]), int(fh * box[1])
    x1, y1 = min(fw, math.ceil(fw * (box[0] + box[2]))), min(fh, math.ceil(fh * (box[1] + box[3])))
    if x1 - x0 < MIN_REGION_PIXELS[0] or y1 - y0 < MIN_REGION_PIXELS[1]:
        return _unread("region-too-small")
    gray = cv2.cvtColor(frame[y0:y1, x0:x1, :3], cv2.COLOR_BGR2GRAY)
    tile = _filled_tile(gray)
    if tile is None:
        return _unread("highlight-not-found")
    tx, ty, tw, th, _area = tile
    center = tx + tw / 2
    # The filled block's soft edge makes it slightly wider than the spacing.
    pitch = tw * PITCH_RATIO

    def walk(direction):
        found = 0
        for step in range(1, len(floors)):
            present = _labelled_button(gray, center + direction * step * pitch, ty, tw, th, (tx, tx + tw))
            if present is None:
                return found, True
            if not present:
                return found, False
            found += 1
        return found, False

    (left, clipped_left), (right, clipped_right) = walk(-1), walk(1)
    total = len(floors)
    # Every button must be seen. A button cut by the region edge or covered by
    # something else is unknown, and guessing from the visible side reads the
    # wrong floor, so it yields no floor at all.
    if left + right + 1 != total:
        return _unread("too-many-buttons" if left + right + 1 > total else "buttons-incomplete",
                       left=left, right=right, clippedLeft=clipped_left, clippedRight=clipped_right)
    index = left
    floor = floors[index]
    selected = gray[ty:ty + th, tx:tx + tw]
    fill = float(np.median(selected))
    centers = [(x0 + center + (i - index) * pitch) / fw for i in range(total)]
    return dict(selectedFloor=floor, floorName=FLOOR_NAMES[floor], reason="region-highlight-read",
                confidence=min(1., max(0., (fill - FILL_LEVEL) / 30.)), left=left, right=right,
                tileBounds=[(x0 + tx) / fw, (y0 + ty) / fh, tw / fw, th / fh], centers=centers)


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


def apply_floor_region(settings, mode, frame, floor_read: dict) -> dict:
    """In 框选 mode, replace the fixed reading with the region reading when it reads.

    The fixed detector stays the fallback, and it keeps owning the button
    geometry used for clicks. ``floorSource`` records which reading was used.
    """
    settings = settings or {}
    if settings.get("floorControlMode") != "region":
        return dict(floor_read, floorSource="fixed")
    rect = region_for_client(settings.get("floorControlRegion"), frame.shape[1::-1])
    if rect is None:
        return dict(floor_read, floorSource="fixed-region-unavailable")
    read = read_floor_in_region(frame, rect, mode)
    if read["selectedFloor"] is None:
        return dict(floor_read, floorSource="fixed-region-unread", regionRead=read)
    return dict(floor_read, selectedFloor=read["selectedFloor"], floorSource="region", regionRead=read)
