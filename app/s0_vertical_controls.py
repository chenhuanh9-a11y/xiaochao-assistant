"""Layout-specific S0 vertical click geometry; no capture, input or pose grants.

FloorContract retains semantic IDs and legacy layout compatibility. This module
owns only the separately observed visual order of the new right-side column.
"""
from __future__ import annotations
import math
from floor_contract import floor_contract

S0_VERTICAL_ORDERS = {"difficult": (2, 1), "nightmare": (2, 1, -1)}
LAYOUT = "localized-s0-vertical"


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def validated_control_rects(read):
    """Strictly validate a complete same-frame layout before click admission."""
    if not isinstance(read, dict):
        return None
    if not isinstance(read.get("mode"), str):
        return None
    order = S0_VERTICAL_ORDERS.get(read.get("mode"))
    contract = floor_contract(read.get("mode"), season=read.get("season"))
    if (read.get("season") != "s0" or order is None or contract is None
            or set(order) != set(contract.observed_values)
            or read.get("layout") != LAYOUT or read.get("controlAxis") != "vertical"
            or read.get("mapOpen") is not True or read.get("controlGeometryVerified") is not True
            or type(read.get("selectedFloor")) is not int or read["selectedFloor"] not in order):
        return None
    visual = read.get("observedOrder")
    if not isinstance(visual, (list, tuple)) or any(type(v) is not int for v in visual) or tuple(visual) != order:
        return None
    size = read.get("controlFrameSize")
    if not isinstance(size, (list, tuple)) or len(size) != 2 or any(type(v) is not int or v < 100 for v in size):
        return None
    rows = read.get("controlRects")
    if not isinstance(rows, (list, tuple)) or len(rows) != len(order):
        return None
    rectangles = []
    for floor, row in zip(order, rows):
        if not isinstance(row, dict) or type(row.get("floor")) is not int or row["floor"] != floor:
            return None
        rect = row.get("rect")
        if not isinstance(rect, (list, tuple)) or len(rect) != 4 or not all(_number(x) for x in rect):
            return None
        x0, y0, x1, y1 = rect
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            return None
        rectangles.append(tuple(rect))
    # No overlap even at shared borders. Each row belongs to one visual column.
    for a, b in zip(rectangles, rectangles[1:]):
        if not (a[3] < b[1] and max(a[0], b[0]) < min(a[2], b[2])):
            return None
    return tuple(zip(order, rectangles))


def bind_control_geometry(read, mode, frame_size, *, map_open):
    """Bind detector output to independent map-open evidence and current pixels.

    Click regions are conservative interiors, smaller than both the measured
    highlight and the neighboring-label pitch. Missing/ambiguous data fails
    closed; never retain legacy scalar centers or a prior frame's rectangles.
    """
    out = dict(read)
    for key in ("centers", "pitch", "difficultObservation", "controlVertical", "controlRects", "scores", "margin"):
        out.pop(key, None)
    out.update(season="s0", mode=mode, mapOpen=map_open is True,
               layout="vertical-floor-read-only", controlGeometryVerified=False)
    order = S0_VERTICAL_ORDERS.get(mode)
    try:
        points, evidence = read["centerPoints"], read["labelEvidence"]
        x, y, w, h = read["tileBounds"]
        size = tuple(frame_size)
        if (map_open is not True or order is None or read.get("controlAxis") != "vertical"
                or ("season" in read and read["season"] != "s0")
                or ("mode" in read and read["mode"] != mode)
                or tuple(read.get("observedOrder", ())) != order
                or type(read.get("selectedFloor")) is not int or read["selectedFloor"] not in order
                or len(points) != len(order) or len(evidence) != len(order)
                or not all(_number(v) for v in (x, y, w, h))
                or not (0 <= x < x+w <= 1 and 0 <= y < y+h <= 1)):
            raise ValueError("unverified layout")
        for floor, point, glyph in zip(order, points, evidence):
            if (len(point) != 2 or not all(_number(v) and 0 < v < 1 for v in point)
                    or not isinstance(glyph, dict)
                    or type(glyph.get("floor")) is not int or glyph["floor"] != floor
                    or glyph.get("negative") is not (floor < 0)):
                raise ValueError("unverified label")
        if any(abs(point[0] - (x+w/2)) > w*.1 for point in points):
            raise ValueError("not one column")
        selected_point = points[order.index(read["selectedFloor"])]
        if abs(selected_point[1] - (y+h/2)) > h*.1:
            raise ValueError("selected glyph and highlight disagree")
        gaps = [b[1]-a[1] for a,b in zip(points, points[1:])]
        if min(gaps) <= 0 or max(gaps) > min(gaps)*1.2:
            raise ValueError("invalid spacing")
        half_y = min(h*.4, min(gaps)*.4)
        rows = [dict(floor=floor, rect=[cx-w*.4, cy-half_y, cx+w*.4, cy+half_y])
                for floor,(cx,cy) in zip(order,points)]
        out.update(layout=LAYOUT, controlGeometryVerified=True,
                   controlFrameSize=list(size), controlRects=rows)
        if validated_control_rects(out) is None:
            raise ValueError("invalid rectangles")
        return out
    except (KeyError, TypeError, ValueError, IndexError):
        out.pop("controlRects", None)
        out.update(selectedFloor=None, layout="vertical-floor-read-only",
                   controlGeometryVerified=False, reason="vertical-control-geometry-unverified")
        return out


def control_hit(cursor, client_bounds, read):
    rows = validated_control_rects(read)
    if rows is None:
        return None
    try:
        if (len(cursor) != 2 or len(client_bounds) != 4
                or not all(_number(v) for v in (*cursor, *client_bounds))):
            return None
        left, top, right, bottom = client_bounds
        width, height = right-left, bottom-top
        if (width < 100 or height < 100
                or any(abs(a-b) > 1 for a,b in zip((width,height), read["controlFrameSize"]))):
            return None
        x, y = (cursor[0]-left)/width, (cursor[1]-top)/height
        hits = [floor for floor,(x0,y0,x1,y1) in rows if x0 <= x <= x1 and y0 <= y <= y1]
        return hits[0] if len(hits) == 1 else None
    except (TypeError, ValueError):
        return None
