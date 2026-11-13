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
from map_interface_observation import detect_map_interface


CAPABILITY = "s1-floor-only-manual-map-use"
ORDER = (-1, -2, -3, -4)
SEARCH_BOUNDS = (.80, .12, .92, .56)
EXPANDED_SEARCH_BOUNDS = (.55, .04, .985, .93)
SOURCE_REPLAY_FLOORS = (-1, -2, -3, -4)


def _source_replay_parameter_gaps(parameters, *, season, mode):
    """Compare the decoder's actual values with this observer's reviewed inputs.

    Qualification belongs to this algorithm, not a package identity or a claim
    in its payload. In particular, compare decoded endpoints without rounding
    or tolerances: legal alternative search parameters remain unreviewed.
    """
    from s1_map_ui_geometry import SEARCH, SIZES, MIN_CORRELATION
    context = parameters.context
    contract = floor_contract(mode, season=season)
    gaps = []
    if (context is None or contract is None or season != 's1'
            or context.key != f'{season}:{mode}' or context.contract != contract
            or context.visual_order != ORDER or contract.observed_values != ORDER):
        gaps.append('s1-controls-floor-contract-not-source-replay-equivalent')
    def bounds(region):
        x, y, width, height = region
        return x, y, x + width, y + height
    for actual, expected, name in (
            (bounds(parameters.fixed_search_region), SEARCH_BOUNDS, 'fixed-search-region'),
            (bounds(parameters.expanded_search_region), EXPANDED_SEARCH_BOUNDS, 'expanded-search-region'),
            (dict(parameters.map_ui.search), SEARCH, 'map-ui-search'),
            (parameters.map_ui.sizes, SIZES, 'map-ui-sizes'),
            (dict(parameters.map_ui.minimum_correlation), MIN_CORRELATION, 'map-ui-correlation')):
        if actual != expected:
            gaps.append(f's1-controls-{name}-not-source-replay-equivalent')
    return tuple(gaps)


def _controls_parameters(controls, *, season, mode):
    from imported_s1_controls_profile import S1ControlsProfile
    if not isinstance(controls, S1ControlsProfile):
        raise ValueError('s1-controls-profile-required')
    parameters = controls.observation_parameters(season, mode)
    if parameters.context is None:
        raise ValueError('s1-controls-context-unsupported')
    return parameters


def controls_source_replay_gaps(controls, *, season, mode):
    """Current-owner qualification shared by observation and native context."""
    parameters = _controls_parameters(controls, season=season, mode=mode)
    gaps = _source_replay_parameter_gaps(parameters, season=season, mode=mode)
    controls.assert_current()
    return gaps


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
                    frame_current=True, controls=None):
    """Observe with trusted defaults or one current, validated S1 profile.

    An external profile supplies the whole parameter set. Unsupported contexts
    and expired views raise instead of falling back to the built-in detector.
    """
    kwargs = dict(season=season, mode=mode, frame_id=frame_id,
                  expected_frame_id=expected_frame_id, frame_current=frame_current)
    if controls is None:
        return _detect_s1_floor(frame, **kwargs)
    parameters = _controls_parameters(controls, season=season, mode=mode)
    context = parameters.context
    gaps = _source_replay_parameter_gaps(parameters, season=season, mode=mode)
    def bounds(region):
        x, y, width, height = region
        return x, y, x + width, y + height
    if not gaps:
        # Equal decoded inputs use the same coordinate/UI/scale policy as the
        # reviewed native observer. Storage location cannot select an algorithm.
        result = _detect_s1_floor(frame, **kwargs)
    else:
        result = _detect_s1_floor(frame, **kwargs, floor_context=context,
            fixed_search_bounds=bounds(parameters.fixed_search_region),
            expanded_search_bounds=bounds(parameters.expanded_search_region), map_ui=parameters.map_ui)
    controls.assert_current()
    if not gaps:
        return result
    # Alternative parameters still supply pixel proposals, not replay authority.
    return dict(result, automaticFloorEligible=False, observedFloorValidated=False,
                validationScope="external-controls-unreviewed", qualificationGaps=list(gaps))


def _detect_s1_floor(frame, *, season, mode, frame_id=None, expected_frame_id=None,
                     frame_current=True, floor_context=None, fixed_search_bounds=None,
                     expanded_search_bounds=None, map_ui=None):
    """Return current pixel evidence, failing closed on any incomplete column.

    Optional IDs reject a known stale frame. Omitting them permits pure image
    replay, and does not attest capture freshness. ``frame_current=True`` is
    only a caller assertion; it does not grant capture or runtime authority.
    """
    out = _result(season, mode, "s1-floor-context-unsupported")
    if not isinstance(season, str) or not isinstance(mode, str):
        return out
    contract = floor_contract(mode, season=season) if floor_context is None else floor_context.contract
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
    order = ORDER if floor_context is None else floor_context.visual_order
    external_parameters = floor_context is not None
    column_parameters = dict(search_bounds=fixed_search_bounds,
                             expanded_search_bounds=expanded_search_bounds, order=order)
    map_parameters = dict(search_bounds=fixed_search_bounds, map_ui=map_ui,
                          full_client_search=external_parameters)
    # Keep the S0 detector unchanged and prevent its implicit default floor read.
    # A raw S1 column is measured separately, before any map-open conclusion.
    map_observation = detect_map_interface(frame, floor_read={})
    out.update(mapObservation=map_observation,
               mapOpen=map_observation.get("detected") is True)
    independent = None
    if out["mapOpen"] is not True:
        # Preserve the cheap closed-frame exit. This is only a precheck; a
        # discovered column is masked and corroborated again below.
        independent = _s1_independent_map_evidence(frame, map_observation, **map_parameters)
        if independent is None:
            return dict(out, reason="s1-floor-map-not-open")
    column = _observe_signed_column(frame, width, height, **column_parameters)
    localized = column.get("localization") == "signed-column-position-scale"
    if out["mapOpen"] is not True or localized or external_parameters:
        independent = _s1_independent_map_evidence(frame, map_observation,
            floor_bounds=column.get("controlBounds"),
            floor_scale=(column.get("controlLocalScale") if localized else
                         width if external_parameters else None), **map_parameters)
        if independent is None:
            return dict(out, reason="s1-floor-localization-ui-unverified" if localized
                        and out["mapOpen"] is True else "s1-floor-independent-ui-unverified"
                        if out["mapOpen"] is True else "s1-floor-map-not-open")
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


def _s1_independent_map_evidence(frame, observation, *, floor_bounds=None, floor_scale=None,
                                 search_bounds=None, map_ui=None, full_client_search=False):
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
    # Broad legacy UI rectangles overlap the floor search at their left edge.
    # Zero that entire area in a temporary measurement frame so a moved or
    # oversized floor highlight cannot supply its own corroborating anchor.
    # Keep the original anchor denominator and thresholds (zero is not neutral).
    height, width = frame.shape[:2]
    viewport = centered_safe_viewport((width, height))
    bounds = SEARCH_BOUNDS if search_bounds is None else search_bounds
    if full_client_search:
        # External signed-column ROIs use full-client pixels. Mask exactly
        # those pixels, including preprocessing, rather than a viewport-shifted
        # approximation. Independent shape searches retain their HUD viewport.
        x0, y0, x1, y1 = (round(width * bounds[0]), round(height * bounds[1]),
                          round(width * bounds[2]), round(height * bounds[3]))
        floor_search_right = (width * bounds[2] - viewport.left) / viewport.width
    else:
        x0, y0, x1, y1 = (round(viewport.left + viewport.width * bounds[0]),
                          round(viewport.top + viewport.height * bounds[1]),
                          round(viewport.left + viewport.width * bounds[2]),
                          round(viewport.top + viewport.height * bounds[3]))
        floor_search_right = bounds[2]
    exclusions = [(x0, y0, x1, y1)]
    if floor_bounds is not None:
        if (not isinstance(floor_bounds, (list, tuple)) or len(floor_bounds) != 4
                or any(type(value) is not int for value in floor_bounds)):
            return None
        fx0, fy0, fx1, fy1 = floor_bounds
        if not (0 <= fx0 < fx1 <= width and 0 <= fy0 < fy1 <= height):
            return None
        exclusions.append((fx0, fy0, fx1, fy1))
    from independent_map_ui import observe_independent_map_ui
    evidence = observe_independent_map_ui(frame, observation, excluded_bounds=tuple(exclusions),
        floor_search_right=floor_search_right, parameters=map_ui)
    if evidence['verified'] is not True:
        return None
    if floor_scale is not None:
        # The signed column must sit beside the independent control group.
        shapes = evidence['geometry']['shapes']
        center = (fx0 + fx1) / 2
        gap = (shapes['plus']['center'][0] - center) / floor_scale
        if (not .025 <= gap <= .16
                or shapes['close']['center'][1] > fy0 + .075 * floor_scale
                or fy1 > shapes['minus']['center'][1] + .075 * floor_scale):
            return None
    return 's1-signed-controls-ui-geometry'


def _observe_signed_column(frame, width, height, *, search_bounds=None,
                             expanded_search_bounds=None, order=None):
    """Uninterpreted current pixel proof, without map-open or runtime authority."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    original = _signed_column_from_gray(gray, width, height, search_bounds=search_bounds, order=order)
    # Keep every accepted original observation and never resolve two valid
    # columns by making one disappear during exposure normalization.
    if (original.get("selectedFloor") is not None
            or original["reason"] == "s1-floor-column-ambiguous"):
        return original
    bounds = SEARCH_BOUNDS if search_bounds is None else search_bounds
    x0, y0, x1, y1 = (round(width * bounds[0]), round(height * bounds[1]),
                      round(width * bounds[2]), round(height * bounds[3]))
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
        result = _signed_column_from_gray(exposed, width, height, search_bounds=search_bounds, order=order)
        if result.get("selectedFloor") is not None or result["reason"] == "s1-floor-column-ambiguous":
            return result
    localized = _joint_signed_column(gray, width, height, search_bounds=expanded_search_bounds, order=order)
    if localized is not None:
        return localized
    return original


def _signed_column_from_gray(gray, width, height, *, search_bounds=None, order=None):
    """One exposure must independently prove the entire signed column."""
    bounds = SEARCH_BOUNDS if search_bounds is None else search_bounds
    x0, y0, x1, y1 = (round(width * bounds[0]), round(height * bounds[1]),
                      round(width * bounds[2]), round(height * bounds[3]))
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
    return _column_evidence(rows, fills, width, height, order=order)


def _column_evidence(rows, fills, width, height, *, order=None):
    order = ORDER if order is None else order
    selected = order[next(i for i, value in enumerate(fills) if value >= .65)]
    points = [[row["centerX"] / width, row["y"] / height] for row in rows]
    evidence = [dict(floor=floor, negative=True, digitAspect=row["glyphAspect"],
                     polarity=row["polarity"], highlightFill=fill,
                     signBounds=row["signBounds"], digitBounds=row["digitBounds"])
                for floor, row, fill in zip(order, rows, fills)]
    bounds = [max(0, round(min(row["centerX"] for row in rows) - .025 * width)),
              max(0, round(rows[0]["y"] - .025 * width)),
              min(width, round(max(row["centerX"] for row in rows) + .025 * width)),
              min(height, round(rows[-1]["y"] + .025 * width))]
    return dict(reason="s1-floor-observed", selectedFloor=selected,
                centerPoints=points, centers=points, labelEvidence=evidence, controlBounds=bounds)


def _joint_signed_column(gray, width, height, *, search_bounds=None, order=None):
    """Fallback position/scale hypotheses, each proving one whole column.

    The right-side domain is only a search prior. Local row pitch supplies the
    scale used to recheck *all* glyph pairs and fill windows. Exposures/scales
    never contribute different rows to one proof, and conflicting hypotheses
    are rejected rather than ranked. No map-open or click authority is granted.
    """
    viewport = centered_safe_viewport((width, height))
    bounds = EXPANDED_SEARCH_BOUNDS if search_bounds is None else search_bounds
    x0, y0, x1, y1 = (round(width * bounds[0]), round(height * bounds[1]),
                      round(width * bounds[2]), round(height * bounds[3]))
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
                result = _column_evidence(rows, fills, width, height, order=order)
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
