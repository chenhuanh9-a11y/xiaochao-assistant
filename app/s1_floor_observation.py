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

from client_space_geometry import centered_safe_viewport
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


def _label_components(gray):
    return [(polarity, cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)[2][1:])
            for polarity, mask in (("light", gray > 110), ("dark", gray < 65))]


def _labels(gray, width, origin, components=None):
    """Match one distinct same-polarity minus to one digit component."""
    labels = []
    ox, oy = origin
    for polarity, stats in (_label_components(gray) if components is None else components):
        digits, signs = [], []
        for x, y, w, h, area in stats:
            box = tuple(int(v) for v in (x, y, w, h))
            if (width * .008 <= h <= width * .017
                    and width * .0015 <= w <= width * .010
                    and width ** 2 * .000006 <= area <= width ** 2 * .00010):
                digits.append(box)
            if (width * .004 <= w <= width * .009
                    and 1 <= h <= width * .003 and w >= 2.5 * h):
                signs.append(box)
        # Index the exact same sign/digit predicate by local coordinates. Dense
        # map/text components must not pay digit*sign comparisons at every scale
        # followed by repeated scans of the complete match list.
        gap_limit, y_limit = width * .006, width * .004
        cell_width, cell_height = max(1., gap_limit), max(1., y_limit * 2)
        sign_cells = {}
        for si, (sx, sy, sw, sh) in enumerate(signs):
            cell = (int((sx + sw) // cell_width), int((sy + sh / 2) // cell_height))
            sign_cells.setdefault(cell, []).append(si)
        matches = []
        digit_degree, sign_degree = [0] * len(digits), [0] * len(signs)
        for digit_index, (dx, dy, dw, dh) in enumerate(digits):
            center_y = dy + dh / 2
            for cx in range(int((dx - gap_limit) // cell_width), int(dx // cell_width) + 1):
                for cy in range(int((center_y - y_limit) // cell_height),
                                int((center_y + y_limit) // cell_height) + 1):
                    for sign_index in sign_cells.get((cx, cy), ()):
                        sx, sy, sw, sh = signs[sign_index]
                        if (0 <= dx - (sx + sw) <= gap_limit
                                and abs(sy + sh / 2 - center_y) <= y_limit):
                            matches.append((digit_index, sign_index))
                            digit_degree[digit_index] += 1
                            sign_degree[sign_index] += 1
        for di, si in matches:
            # Reusing one sign for multiple glyphs, or choosing between two
            # signs for one glyph, is ambiguous even within the same column.
            if digit_degree[di] != 1 or sign_degree[si] != 1:
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
        # Preserve the cheap closed-frame exit. This is only a precheck; a
        # discovered column is masked and corroborated again below.
        independent = _s1_independent_map_evidence(frame, map_observation)
        if independent is None:
            return dict(out, reason="s1-floor-map-not-open")
    column = _observe_signed_column(frame, width, height)
    localized = column.get("localization") == "signed-column-position-scale"
    if out["mapOpen"] is not True or localized:
        independent = _s1_independent_map_evidence(frame, map_observation,
            floor_bounds=column.get("controlBounds"),
            floor_scale=column.get("controlLocalScale") if localized else None)
        if independent is None:
            return dict(out, reason="s1-floor-localization-ui-unverified" if localized
                        and out["mapOpen"] is True else "s1-floor-map-not-open")
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


def _s1_independent_map_evidence(frame, observation, *, floor_bounds=None, floor_scale=None):
    """Additional map evidence only, excluding the signed column's ROI.

    Large revealed S1 structures can violate the old whole-canvas texture
    test. For that case require every existing non-floor map UI anchor and
    the original backdrop bound, plus the close-X and grouped zoom +/-/rail
    shapes. Neither neutral nor tinted color statistics alone are UI proof.
    A caller must separately require a complete unique signed S1 column and
    single highlight. This helper never reads or assumes any selected floor.
    """
    if (floor_scale is not None and (floor_bounds is None
            or type(floor_scale) not in (int, float) or not np.isfinite(floor_scale) or floor_scale <= 0)):
        return None
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
    viewport = centered_safe_viewport((width, height))
    x0, y0, x1, y1 = (round(viewport.left + viewport.width * SEARCH_BOUNDS[0]),
                      round(viewport.top + viewport.height * SEARCH_BOUNDS[1]),
                      round(viewport.left + viewport.width * SEARCH_BOUNDS[2]),
                      round(viewport.top + viewport.height * SEARCH_BOUNDS[3]))
    anchor_frame = frame.copy()
    anchor_frame[y0:y1, x0:x1] = 0
    geometry_frame = frame
    if floor_bounds is not None:
        if (not isinstance(floor_bounds, (list, tuple)) or len(floor_bounds) != 4
                or any(type(value) is not int for value in floor_bounds)):
            return None
        fx0, fy0, fx1, fy1 = floor_bounds
        if not (0 <= fx0 < fx1 <= width and 0 <= fy0 < fy1 <= height):
            return None
        anchor_frame[fy0:fy1, fx0:fx1] = 0
        geometry_frame = frame.copy()
        geometry_frame[fy0:fy1, fx0:fx1] = 0
    ratios = {name: neutral_ratio(anchor_frame, MAP_REGIONS[name]) for name in independent}
    if all(ratios.get(name, 0.0) >= MAP_REGIONS[name][4] for name in independent):
        geometry = observe_s1_map_ui_geometry(geometry_frame, floor_search_right=SEARCH_BOUNDS[2])
        if geometry.get("verified") is True:
            if floor_scale is not None:
                # New search-domain columns must sit beside the independent
                # control group; a signed list elsewhere on an open map is not
                # the floor HUD. Only disjoint shape pixels supply this proof.
                shapes = geometry["shapes"]
                center = (fx0 + fx1) / 2
                gap = (shapes["plus"]["center"][0] - center) / floor_scale
                if (not .025 <= gap <= .16
                        or shapes["close"]["center"][1] > fy0 + .075 * floor_scale
                        or fy1 > shapes["minus"]["center"][1] + .075 * floor_scale):
                    return None
            return "s1-signed-controls-ui-geometry"
    return None


def _observe_signed_column(frame, width, height):
    """Uninterpreted current pixel proof, without map-open or runtime authority."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    original = _signed_column_from_gray(gray, width, height)
    # Keep every accepted original observation and never resolve two valid
    # columns by making one disappear during exposure normalization.
    if (original.get("selectedFloor") is not None
            or original["reason"] == "s1-floor-column-ambiguous"):
        return original
    x0, y0, x1, y1 = (round(width * SEARCH_BOUNDS[0]), round(height * SEARCH_BOUNDS[1]),
                      round(width * SEARCH_BOUNDS[2]), round(height * SEARCH_BOUNDS[3]))
    roi = gray[y0:y1, x0:x1]
    if not roi.size:
        return original
    # Damage effects dim the whole control, including its selected background.
    # Normalize one bounded exposure from the current control band only; using
    # independent thresholds for glyphs and fill would change their meaning.
    # Bright pixels elsewhere in the map must not determine this exposure.
    # Geometry, distinct signed pairs, complete-column and unique-fill checks
    # below remain mandatory. This does not normalize any map-open evidence.
    reference = float(np.percentile(roi, 99.8))
    if 75 <= reference < 150:
        exposed = cv2.convertScaleAbs(gray, alpha=150 / reference)
        result = _signed_column_from_gray(exposed, width, height)
        if result.get("selectedFloor") is not None or result["reason"] == "s1-floor-column-ambiguous":
            return result
    localized = _joint_signed_column(gray, width, height)
    if localized is not None:
        return localized
    return original


def _signed_column_from_gray(gray, width, height):
    """One exposure must independently prove the entire signed column."""
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
    return _column_evidence(rows, fills, width, height)


def _column_evidence(rows, fills, width, height):
    selected = ORDER[next(i for i, value in enumerate(fills) if value >= .65)]
    points = [[row["centerX"] / width, row["y"] / height] for row in rows]
    evidence = [dict(floor=floor, negative=True, digitAspect=row["glyphAspect"],
                     polarity=row["polarity"], highlightFill=fill,
                     signBounds=row["signBounds"], digitBounds=row["digitBounds"])
                for floor, row, fill in zip(ORDER, rows, fills)]
    bounds = [max(0, round(min(row["centerX"] for row in rows) - .025 * width)),
              max(0, round(rows[0]["y"] - .025 * width)),
              min(width, round(max(row["centerX"] for row in rows) + .025 * width)),
              min(height, round(rows[-1]["y"] + .025 * width))]
    return dict(reason="s1-floor-observed", selectedFloor=selected,
                centerPoints=points, centers=points, labelEvidence=evidence, controlBounds=bounds)


def _joint_signed_column(gray, width, height):
    """Fallback position/scale hypotheses, each proving one whole column.

    The right-side domain is only a search prior. Local row pitch supplies the
    scale used to recheck *all* glyph pairs and fill windows. Exposures/scales
    never contribute different rows to one proof, and conflicting hypotheses
    are rejected rather than ranked. No map-open or click authority is granted.
    """
    viewport = centered_safe_viewport((width, height))
    x0, y0, x1, y1 = round(.55 * width), round(.04 * height), round(.985 * width), round(.93 * height)
    reference_width = viewport.width
    scales = sorted({round(reference_width * factor) for factor in (.5, .65, .8, 1., 1.25, 1.6, 2.)})
    accepted = []
    for exposure in (1., 1.25, 1.6, 2.):
        image = gray if exposure == 1. else cv2.convertScaleAbs(gray, alpha=exposure)
        roi = image[y0:y1, x0:x1]
        components = _label_components(roi)
        if any(len(stats) > 4096 for _, stats in components):
            continue
        labels = {}
        for scale in scales:
            measured = _labels(roi, scale, (x0, y0), components)
            if len(measured) > 16:
                continue
            for row in measured:
                key = (tuple(row["signBounds"]), tuple(row["digitBounds"]), row["polarity"])
                labels[key] = row
        labels = list(labels.values())
        if not 4 <= len(labels) <= 32:
            continue
        hypotheses = 0
        for first in labels:
            if first["glyphAspect"] > .45:
                continue
            for second in labels:
                pitch = second["y"] - first["y"]
                local_width = pitch / .045
                if (second["glyphAspect"] < .35
                        or not .4 * reference_width <= local_width <= 2.2 * reference_width
                        or abs(first["x"] - second["x"]) > .003 * local_width):
                    continue
                rows = [first, second]
                for index in (2, 3):
                    matches = [row for row in labels if row["glyphAspect"] >= .35
                        and abs(row["x"] - first["x"]) <= .003 * local_width
                        and abs(row["y"] - first["y"] - index * pitch) <= .004 * local_width]
                    if len(matches) != 1:
                        break
                    rows.extend(matches)
                if len(rows) != 4:
                    continue
                gaps = np.diff([row["y"] for row in rows])
                heights = [row["digitBounds"][3] for row in rows]
                if max(gaps) - min(gaps) > .004 * local_width or max(heights) > 1.35 * min(heights):
                    continue
                # A fifth adjacent signed row is a list, not the four-row HUD.
                if any(row not in rows and abs(row["x"] - first["x"]) <= .003 * local_width
                       and any(abs(row["y"] - first["y"] - i * pitch) <= .004 * local_width for i in (-1, 4))
                       for row in labels):
                    continue
                hypotheses += 1
                if hypotheses > 32:
                    return dict(reason="s1-floor-localization-budget")
                left = max(0, round(min(row["centerX"] for row in rows) - .025 * local_width))
                right = min(width, round(max(row["centerX"] for row in rows) + .025 * local_width))
                top = max(0, round(first["y"] - .025 * local_width))
                bottom = min(height, round(rows[-1]["y"] + .025 * local_width))
                # Re-extract at the pitch-derived scale. This catches a sign
                # pairing ambiguity hidden by a coarser seed-scale hypothesis.
                verified = _labels(image[top:bottom, left:right], local_width, (left, top))
                identities = {(tuple(row["signBounds"]), tuple(row["digitBounds"]), row["polarity"]) for row in verified}
                if any((tuple(row["signBounds"]), tuple(row["digitBounds"]), row["polarity"]) not in identities for row in rows):
                    continue
                fills = [_fill(image, row, local_width) for row in rows]
                if (any(value is None or .20 < value < .65 for value in fills)
                        or sum(value >= .65 for value in fills if value is not None) != 1):
                    continue
                result = _column_evidence(rows, fills, width, height)
                result.update(localization="signed-column-position-scale", controlLocalScale=float(local_width),
                              controlBounds=[left, top, right, bottom],
                              controlSearchBounds=[x0, y0, x1, y1],
                              controlSearchRegion=[left / width, top / height,
                                                   (right - left) / width, (bottom - top) / height])
                same = [old for old in accepted if all(
                    abs(a[0] - b[0]) * width <= .003 * local_width
                    and abs(a[1] - b[1]) * height <= .004 * local_width
                    for a, b in zip(old["centerPoints"], result["centerPoints"]))]
                if same:
                    if any(old["selectedFloor"] != result["selectedFloor"] for old in same):
                        return dict(reason="s1-floor-column-ambiguous")
                else:
                    accepted.append(result)
                    if len(accepted) > 1:
                        return dict(reason="s1-floor-column-ambiguous")
    return accepted[0] if accepted else None
