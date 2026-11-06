"""Observe the S1 signed floor column for a manually selected map.

This pixel observer supplies neither map identity nor input/pose permission.
B1/B2/B3/B4 have static source-image replay evidence for the selected control
row only. This is not map identity, geometry fit, native capture or live proof.
The glyph geometry checks a narrow first digit and three wider digits, not OCR
of each numeral. Capture freshness and context ownership remain the caller's job.
"""
from __future__ import annotations

from itertools import combinations

import cv2
import numpy as np

from floor_contract import floor_contract
from map_interface_observation import MAP_REGIONS, detect_map_interface, neutral_ratio
from s1_map_ui_geometry import observe_s1_map_ui_geometry


CAPABILITY = "s1-floor-only-manual-map-use"
ORDER = (-1, -2, -3, -4)
SEARCH_BOUNDS = (.80, .12, .92, .56)
SOURCE_REPLAY_FLOORS = (-1, -2, -3, -4)


def _result(season, mode, reason, **extra):
    return dict(season=season, mode=mode, reason=reason, selectedFloor=None,
                observedFloorValidated=False, automaticFloorEligible=False, capability=CAPABILITY,
                floorUse="manual-map-only", controlAxis="vertical",
                observedOrder=list(ORDER), mapOpen=None, mapObservation=None,
                centerPoints=[], centers=[], labelEvidence=[], **extra)


def _labels(gray, width, origin):
    """Match one distinct same-polarity minus to one digit component."""
    labels = []
    ox, oy = origin
    for polarity, mask in (("light", gray > 110), ("dark", gray < 65)):
        count, _, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
        digits, signs = [], []
        for x, y, w, h, area in stats[1:count]:
            box = tuple(int(v) for v in (x, y, w, h))
            if (width * .008 <= h <= width * .017
                    and width * .0015 <= w <= width * .010
                    and width ** 2 * .000006 <= area <= width ** 2 * .00010):
                digits.append(box)
            if (width * .004 <= w <= width * .009
                    and 1 <= h <= width * .003 and w >= 2.5 * h):
                signs.append(box)
        matches = []
        for digit_index, (dx, dy, dw, dh) in enumerate(digits):
            for sign_index, (sx, sy, sw, sh) in enumerate(signs):
                if (0 <= dx - (sx + sw) <= width * .006
                        and abs(sy + sh / 2 - (dy + dh / 2)) <= width * .004):
                    matches.append((digit_index, sign_index))
        for di, si in matches:
            # Reusing one sign for multiple glyphs, or choosing between two
            # signs for one glyph, is ambiguous even within the same column.
            if sum(d == di for d, _ in matches) != 1 or sum(s == si for _, s in matches) != 1:
                continue
            dx, dy, dw, dh = digits[di]
            sx, sy, sw, sh = signs[si]
            labels.append(dict(x=ox + sx + sw / 2, y=oy + dy + dh / 2,
                               centerX=ox + (sx + dx + dw) / 2,
                               glyphAspect=dw / dh, polarity=polarity,
                               signBounds=[ox + sx, oy + sy, sw, sh],
                               digitBounds=[ox + dx, oy + dy, dw, dh]))
    return labels


def _fill(gray, label, width):
    cx, cy = label["centerX"], label["y"]
    x0, x1 = round(cx - width * .014), round(cx + width * .014)
    y0, y1 = round(cy - width * .013), round(cy + width * .013)
    if x0 < 0 or y0 < 0 or x1 > gray.shape[1] or y1 > gray.shape[0]:
        return None
    tile = gray[y0:y1, x0:x1]
    return float((tile > 80).mean()) if tile.size else None


def detect_s1_floor(frame, *, season, mode, frame_id=None, expected_frame_id=None,
                    frame_current=True):
    """Return current pixel evidence, failing closed on any incomplete column.

    Optional IDs reject a known stale frame. Omitting them permits pure image
    replay, and does not attest capture freshness. ``frame_current=True`` is
    only a caller assertion; it does not grant capture or runtime authority.
    """
    out = _result(season, mode, "s1-floor-context-unsupported")
    if not isinstance(season, str) or not isinstance(mode, str):
        return out
    contract = floor_contract(mode, season=season)
    if season != "s1" or contract is None or contract.observed_values != ORDER:
        return out
    if frame_current is not True:
        return dict(out, reason="s1-floor-stale-frame")
    if expected_frame_id is not None:
        try:
            same = type(frame_id) is type(expected_frame_id) and bool(frame_id == expected_frame_id)
        except (TypeError, ValueError):
            same = False
        if not same:
            return dict(out, reason="s1-floor-stale-frame")
    if (not isinstance(frame, np.ndarray) or frame.dtype != np.uint8
            or frame.ndim != 3 or frame.shape[2] != 3):
        return dict(out, reason="s1-floor-frame-type")
    height, width = frame.shape[:2]
    if width < 1000 or height < 600:
        return dict(out, reason="s1-floor-frame-size")
    # Keep the S0 detector unchanged and prevent its implicit default floor read.
    # A raw S1 column is measured separately, before any map-open conclusion.
    map_observation = detect_map_interface(frame, floor_read={})
    out.update(mapObservation=map_observation,
               mapOpen=map_observation.get("detected") is True)
    independent = None
    if out["mapOpen"] is not True:
        independent = _s1_independent_map_evidence(frame, map_observation)
        if independent is None:
            return dict(out, reason="s1-floor-map-not-open")
    column = _observe_signed_column(frame, width, height)
    if out["mapOpen"] is not True:
        if column.get("selectedFloor") is None:
            return dict(out, reason="s1-floor-map-not-open")
        # The conjunction is deliberate: neither a floor column alone nor
        # broad color anchors alone is map-open proof. This branch does not
        # consult identity, prior floors, or a floor-dependent map detector.
        map_observation = dict(map_observation, detected=True,
            reason="s1-map-interface-signed-controls",
            detectionMode=independent)
        out.update(mapObservation=map_observation, mapOpen=True)
    if column.get("selectedFloor") is None:
        return dict(out, reason=column["reason"])
    selected = column["selectedFloor"]
    return dict({**out, **column},
                automaticFloorEligible=True,
                observedFloorValidated=selected in SOURCE_REPLAY_FLOORS,
                validationScope="source-image-replay" if selected in SOURCE_REPLAY_FLOORS else "synthetic-only",
                floorSource="s1-signed-pixel-column")


def _s1_independent_map_evidence(frame, observation):
    """Additional map evidence only, excluding the signed column's ROI.

    Large revealed S1 structures can violate the old whole-canvas texture
    test. For that case require every existing non-floor map UI anchor and
    the original backdrop bound, plus the close-X and grouped zoom +/-/rail
    shapes. Neither neutral nor tinted color statistics alone are UI proof.
    A caller must separately require a complete unique signed S1 column and
    single highlight. This helper never reads or assumes any selected floor.
    """
    if observation.get("shapeAccepted") is not True:
        return None
    required = observation.get("requiredBackdropRatio")
    backdrop = observation.get("backdropRatio", 0.0)
    if required is None or backdrop < required:
        return None
    independent = ("closeButton", "zoomControl", "bottomControls")
    # Excluding floor pixels cannot increase a ratio with the same denominator.
    # Skip the temporary frame for ordinary closed gameplay missing any anchor.
    raw_ratios = observation.get("ratios", {})
    if any(raw_ratios.get(name, 0.0) < MAP_REGIONS[name][4] for name in independent):
        return None
    # Broad legacy UI rectangles overlap the floor search at their left edge.
    # Zero that entire area in a temporary measurement frame so a moved or
    # oversized floor highlight cannot supply its own corroborating anchor.
    # Keep the original anchor denominator and thresholds (zero is not neutral).
    height, width = frame.shape[:2]
    x0, y0, x1, y1 = (round(width * SEARCH_BOUNDS[0]), round(height * SEARCH_BOUNDS[1]),
                      round(width * SEARCH_BOUNDS[2]), round(height * SEARCH_BOUNDS[3]))
    anchor_frame = frame.copy()
    anchor_frame[y0:y1, x0:x1] = 0
    ratios = {name: neutral_ratio(anchor_frame, MAP_REGIONS[name]) for name in independent}
    if all(ratios.get(name, 0.0) >= MAP_REGIONS[name][4] for name in independent):
        geometry = observe_s1_map_ui_geometry(frame, floor_search_right=SEARCH_BOUNDS[2])
        if geometry.get("verified") is True:
            return "s1-signed-controls-ui-geometry"
    return None


def _observe_signed_column(frame, width, height):
    """Uninterpreted current pixel proof, without map-open or runtime authority."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    x0, y0, x1, y1 = (round(width * SEARCH_BOUNDS[0]), round(height * SEARCH_BOUNDS[1]),
                      round(width * SEARCH_BOUNDS[2]), round(height * SEARCH_BOUNDS[3]))
    roi = gray[y0:y1, x0:x1]
    if not roi.size:
        return dict(reason="s1-floor-empty-roi")
    labels = _labels(roi, width, (x0, y0))
    if not 4 <= len(labels) <= 16:
        return dict(reason="s1-floor-signed-labels-incomplete")
    columns = []
    for group in combinations(labels, 4):
        rows = sorted(group, key=lambda row: row["y"])
        xs, ys = [row["x"] for row in rows], [row["y"] for row in rows]
        gaps = [b - a for a, b in zip(ys, ys[1:])]
        if (max(xs) - min(xs) > width * .003
                or min(gaps) < width * .035 or max(gaps) > width * .055
                or max(gaps) - min(gaps) > width * .004
                or rows[0]["glyphAspect"] > .45
                or any(row["glyphAspect"] < .35 for row in rows[1:])):
            continue
        fills = [_fill(gray, row, width) for row in rows]
        if (any(value is None or .20 < value < .65 for value in fills)
                or sum(value >= .65 for value in fills if value is not None) != 1):
            continue
        columns.append((rows, fills))
    if len(columns) != 1:
        return dict(reason="s1-floor-column-ambiguous" if columns else "s1-floor-column-unverified")
    rows, fills = columns[0]
    selected = ORDER[next(i for i, value in enumerate(fills) if value >= .65)]
    points = [[row["centerX"] / width, row["y"] / height] for row in rows]
    evidence = [dict(floor=floor, negative=True, digitAspect=row["glyphAspect"],
                     polarity=row["polarity"], highlightFill=fill,
                     signBounds=row["signBounds"], digitBounds=row["digitBounds"])
                for floor, row, fill in zip(ORDER, rows, fills)]
    return dict(reason="s1-floor-observed", selectedFloor=selected,
                centerPoints=points, centers=points, labelEvidence=evidence)
