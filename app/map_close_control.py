"""Current-frame X evidence for physical map-close clicks, in client pixels."""
from __future__ import annotations

import math

import cv2
import numpy as np


def locate_map_close_control(frame):
    """Return only a visible diagonal X; the search band itself grants no hit.

    The exit-navigation circle and its label share this band. Requiring bright
    diagonal arms and dark side wedges distinguishes them from the close X.
    Missing/ambiguous evidence leaves map disappearance to the visual observer.
    """
    if frame is None or frame.ndim != 3 or min(frame.shape[:2]) < 100:
        return None
    height, width = frame.shape[:2]
    left, bottom = int(width * .88), int(height * .30)
    gray = cv2.cvtColor(frame[:bottom, left:, :3], cv2.COLOR_BGR2GRAY)
    blur = max(3, round(min(width, height) / 280) | 1)
    mask = (cv2.GaussianBlur(gray, (blur, blur), 0) > 70).astype(np.uint8)
    _, _, components, _ = cv2.connectedComponentsWithStats(mask, 8)
    yy, xx = np.mgrid[:32, :32] / 31.
    diagonals = (np.abs(xx - yy) < .21) | (np.abs(xx + yy - 1) < .21)
    matches = []
    for x, y, w, h, _ in components[1:]:
        if not (.024 * height <= min(w, h) and max(w, h) <= .085 * height
                and .75 <= w / h <= 1.3):
            continue
        shape = cv2.resize(mask[y:y+h, x:x+w], (32, 32), interpolation=cv2.INTER_NEAREST)
        if shape[diagonals].mean() < .55 or shape[~diagonals].mean() > .22:
            continue
        # Bound the clickable X itself, never the ESC label/navigation below.
        pad = round(min(w, h) * .12)
        matches.append([int(left+x-pad), int(y-pad), int(left+x+w+pad), int(y+h+pad)])
    return matches[0] if len(matches) == 1 else None


def map_close_control_hit(frame, cursor, client_bounds):
    if (cursor is None or not client_bounds or len(client_bounds) != 4
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in cursor)):
        return False
    left, top, right, bottom = client_bounds
    if frame is None or frame.shape[:2] != (bottom-top, right-left):
        return False  # No DPI/logical-pixel guess or cropped-frame rescaling.
    bounds = locate_map_close_control(frame)
    if bounds is None:
        return False
    x, y = cursor[0]-left, cursor[1]-top
    return bounds[0] <= x < bounds[2] and bounds[1] <= y < bounds[3]
