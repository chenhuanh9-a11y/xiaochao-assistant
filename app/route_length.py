"""Relative route length estimation per map (unit: 格 = 1/100 of map long edge).

Pure calculation helpers with no persistence or route-schema changes.
All lengths are derived on demand from planned polylines (or floor-tagged
points) and the map's own base dimensions so routes on the same map can be
compared directly. Cross-floor transitions (stairs, transfers, or floor jumps)
contribute zero length.
"""
from __future__ import annotations

import math
from typing import Mapping, Sequence


UNITS_PER_LONG_EDGE = 100.0
UNIT_LABEL = "格"
UNIT_DESCRIPTION = "按本图比例估算（长边 = 100 格），仅用于同一张图内比较；跨层跳转不计长度"
UNIT_SHORT_DESCRIPTION = "按本图比例估算，仅用于同一张图内比较（跨层不计）"
_ZERO_EPS = 1e-6


def _extract_xy(point) -> tuple[float, float] | None:
    if isinstance(point, Mapping):
        raw_x, raw_y = point.get("x"), point.get("y")
    elif not isinstance(point, (str, bytes)):
        try:
            if len(point) < 2:
                return None
            raw_x, raw_y = point[0], point[1]
        except TypeError:
            return None
    else:
        return None
    try:
        x, y = float(raw_x), float(raw_y)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    return x, y


def polyline_pixel_length(points: Sequence) -> float:
    """Euclidean length of one 2D polyline in pixel coordinates."""
    if not points or len(points) < 2:
        return 0.0
    total = 0.0
    previous = _extract_xy(points[0])
    for raw in points[1:]:
        current = _extract_xy(raw)
        if previous is not None and current is not None:
            total += math.hypot(current[0] - previous[0], current[1] - previous[1])
        previous = current
    return total


def pixels_to_units(
    pixels: float,
    reference_long_edge: float,
    *,
    units_per_long_edge: float = UNITS_PER_LONG_EDGE,
) -> float:
    """Convert pixel distance into relative map units (格)."""
    try:
        px = float(pixels)
        span = float(reference_long_edge)
        scale = float(units_per_long_edge)
    except (TypeError, ValueError):
        return 0.0
    if not (math.isfinite(px) and math.isfinite(span) and math.isfinite(scale)):
        return 0.0
    if px <= _ZERO_EPS or span <= 0.0 or scale <= 0.0:
        return 0.0
    return px * scale / span


def format_route_length(units: float, *, unit_label: str = UNIT_LABEL) -> str:
    """Format relative route length for UI display ('0 格' or '42.6 格')."""
    try:
        value = float(units)
    except (TypeError, ValueError):
        return f"0 {unit_label}"
    if not math.isfinite(value) or value <= _ZERO_EPS:
        return f"0 {unit_label}"
    rounded = round(value, 1)
    if rounded <= 0.0:
        rounded = 0.1
    return f"{rounded:.1f} {unit_label}"


def _compact_number(units: float) -> str:
    if not math.isfinite(units) or units <= _ZERO_EPS:
        return "0"
    rounded = round(float(units), 1)
    if rounded <= 0.0:
        rounded = 0.1
    return f"{rounded:.1f}"


def floor_reference_span(asset) -> float:
    """Long edge (in the planner's coordinate space) of a floor asset.

    CustomMapLibrary plans in native canvas pixels (`native_shape`), whereas
    ScopedRouteLibrary and ImportedStructureRouteLibrary plan in image pixels
    (`image_size`, with identity `to_native`).
    """
    native_shape = getattr(asset, "native_shape", None)
    if native_shape is not None and len(native_shape) >= 2:
        try:
            span = float(max(native_shape[0], native_shape[1]))
            if math.isfinite(span) and span > 0.0:
                return span
        except (TypeError, ValueError):
            pass
    image_size = getattr(asset, "image_size", None)
    if image_size is not None and len(image_size) >= 2:
        try:
            span = float(max(image_size[0], image_size[1]))
            if math.isfinite(span) and span > 0.0:
                return span
        except (TypeError, ValueError):
            pass
    return 0.0


def map_floor_ids(library, map_id: str, graph=None) -> list[str]:
    """Ordered floor IDs available for one map across all library backends."""
    if hasattr(library, "map_floors"):
        try:
            floors = [str(f) for f in library.map_floors(map_id)]
            if floors:
                return floors
        except Exception:
            pass
    candidates = [str(f) for f in getattr(library, "floor_ids", ("B1", "1", "2"))]
    if graph is None:
        graph = library.graph(map_id)
    available = {str(f) for f in getattr(graph, "floors", {})}
    ordered = [f for f in candidates if f in available]
    for fid in available:
        if fid not in ordered:
            ordered.append(fid)
    return ordered


def map_reference_span(library, map_id: str, floors: Sequence[str] | None = None) -> float:
    """Single fixed long-edge reference span shared across all floors of a map."""
    floor_list = [str(f) for f in floors] if floors else map_floor_ids(library, map_id)
    best = 0.0
    for fid in floor_list:
        try:
            asset = library.floor(map_id, fid)
        except Exception:
            continue
        span = floor_reference_span(asset)
        if span > best:
            best = span
    return best


def raw_points_floor_pixels(
    points: Sequence[Mapping],
    floors: Sequence[str] | None = None,
) -> dict[str, float]:
    """Compute per-floor straight-line pixel lengths from raw floor-tagged points.

    Cross-floor jumps (`curr.floor != prev.floor`) and explicit `start` flags
    break the chain and contribute zero length.
    """
    totals: dict[str, float] = {str(f): 0.0 for f in floors} if floors else {}
    prev_floor: str | None = None
    prev_xy: tuple[float, float] | None = None
    for pt in points or ():
        if not isinstance(pt, Mapping):
            prev_floor, prev_xy = None, None
            continue
        fid = str(pt.get("floor", ""))
        xy = _extract_xy(pt)
        is_start = bool(pt.get("start", pt.get("newStart", False)))
        if fid and xy is not None:
            if not is_start and prev_floor == fid and prev_xy is not None:
                totals[fid] = totals.get(fid, 0.0) + math.hypot(xy[0] - prev_xy[0], xy[1] - prev_xy[1])
            prev_floor, prev_xy = fid, xy
        else:
            prev_floor, prev_xy = None, None
    return totals


def segment_floor_pixels(graph, segment: Mapping) -> dict[str, float]:
    """On-floor walked pixel length per floor for one planned segment.

    Cross-floor edges (stairs / transfer transitions where consecutive cells
    belong to different floors) are excluded by construction.
    """
    totals: dict[str, float] = {}
    if not isinstance(segment, Mapping):
        return totals
    if "cells" in segment:
        cells = segment.get("cells") or ()
        graph_floors = getattr(graph, "floors", {}) if graph is not None else {}
        prev_floor: str | None = None
        prev_xy: tuple[float, float] | None = None
        for node in cells:
            if not isinstance(node, (tuple, list)) or len(node) < 2:
                prev_floor, prev_xy = None, None
                continue
            fid = str(node[0])
            floor_graph = graph_floors.get(fid) if isinstance(graph_floors, Mapping) else None
            if floor_graph is None or not hasattr(floor_graph, "points"):
                prev_floor, prev_xy = None, None
                continue
            xy = _extract_xy(floor_graph.points[node[1]])
            if xy is None:
                prev_floor, prev_xy = None, None
                continue
            if prev_floor == fid and prev_xy is not None:
                totals[fid] = totals.get(fid, 0.0) + math.hypot(xy[0] - prev_xy[0], xy[1] - prev_xy[1])
            prev_floor, prev_xy = fid, xy
        return totals
    if "points" in segment and "floor" in segment:
        fid = str(segment["floor"])
        dist = polyline_pixel_length(segment.get("points") or ())
        if dist > _ZERO_EPS:
            totals[fid] = dist
        return totals
    return totals


def _segment_is_cross_floor(segment: Mapping | None, prev_point: Mapping | None, curr_point: Mapping | None) -> bool:
    if prev_point is not None and curr_point is not None:
        if str(prev_point.get("floor", "")) != str(curr_point.get("floor", "")):
            return True
    if isinstance(segment, Mapping) and "cells" in segment:
        seen = {str(node[0]) for node in (segment.get("cells") or ()) if isinstance(node, (tuple, list)) and node}
        if len(seen) > 1:
            return True
    return False


def empty_route_length(
    floors: Sequence[str] = (),
    floor_labels: Mapping[str, str] | None = None,
    reference_long_edge: float = 0.0,
    *,
    error: str = "",
    units_per_long_edge: float = UNITS_PER_LONG_EDGE,
) -> dict:
    """Zero/empty route length summary with a stable UI contract."""
    labels = floor_labels or {}
    floor_rows = [
        dict(
            id=str(fid),
            name=labels.get(str(fid), f"{fid}层"),
            pixels=0.0,
            units=0.0,
            text=format_route_length(0.0),
            hasLength=False,
        )
        for fid in floors
    ]
    return dict(
        unit=UNIT_LABEL,
        unitsPerLongEdge=float(units_per_long_edge),
        referenceLongEdge=float(reference_long_edge),
        unitDescription=UNIT_SHORT_DESCRIPTION,
        unitDetailDescription=UNIT_DESCRIPTION,
        totalPixels=0.0,
        totalUnits=0.0,
        totalText="长度待规划" if error else format_route_length(0.0),
        floors=floor_rows,
        activeFloorCount=0,
        multiFloor=len(floor_rows) > 1,
        hasMultiFloorRoute=False,
        compactFloorText="",
        tooltipText="路线规划待调整" if error else "总长 0 格（未连线或单点）",
        segments=[],
        pointSegments=[],
        error=bool(error),
    )


def summarize_route_length(
    graph,
    segments: Sequence[Mapping],
    floors: Sequence[str],
    floor_labels: Mapping[str, str] | None,
    reference_long_edge: float,
    *,
    points: Sequence[Mapping] = (),
    error: str = "",
    units_per_long_edge: float = UNITS_PER_LONG_EDGE,
) -> dict:
    """Aggregate per-floor, total, and per-segment route lengths in relative units."""
    labels = floor_labels or {}
    ordered_floors = [str(f) for f in floors]
    if error:
        summary = empty_route_length(
            ordered_floors,
            labels,
            reference_long_edge,
            error=error,
            units_per_long_edge=units_per_long_edge,
        )
        summary["pointSegments"] = [
            dict(
                hasSegment=False,
                isStart=(idx == 0 or bool(pt.get("start", pt.get("newStart", False)))),
                start=idx - 1 if idx > 0 else -1,
                end=idx,
                fromNumber=idx,
                toNumber=idx + 1,
                units=0.0,
                text="",
                crossFloor=False,
                floors=[],
                error=True,
            )
            for idx, pt in enumerate(points or ())
        ]
        return summary

    floor_px: dict[str, float] = {fid: 0.0 for fid in ordered_floors}
    segment_by_end: dict[int, tuple[Mapping, dict[str, float]]] = {}
    segment_rows: list[dict] = []

    for seg in segments or ():
        per_floor = segment_floor_pixels(graph, seg)
        for fid, dist in per_floor.items():
            if fid not in floor_px:
                ordered_floors.append(fid)
                floor_px[fid] = 0.0
            floor_px[fid] += dist
        end_idx = seg.get("end")
        start_idx = seg.get("start")
        if start_idx is None and isinstance(end_idx, int):
            start_idx = end_idx - 1
        if isinstance(end_idx, int):
            segment_by_end[end_idx] = (seg, per_floor)
        seg_px = sum(per_floor.values())
        seg_units = pixels_to_units(seg_px, reference_long_edge, units_per_long_edge=units_per_long_edge)
        prev_pt = points[start_idx] if isinstance(start_idx, int) and 0 <= start_idx < len(points) else None
        curr_pt = points[end_idx] if isinstance(end_idx, int) and 0 <= end_idx < len(points) else None
        cross_floor = _segment_is_cross_floor(seg, prev_pt, curr_pt)
        seg_floor_rows = []
        for fid in ordered_floors:
            fpx = per_floor.get(fid, 0.0)
            if fpx > _ZERO_EPS:
                fu = pixels_to_units(fpx, reference_long_edge, units_per_long_edge=units_per_long_edge)
                seg_floor_rows.append(dict(
                    id=fid,
                    name=labels.get(fid, f"{fid}层"),
                    units=round(fu, 1) if fu > _ZERO_EPS else 0.0,
                    text=format_route_length(fu),
                ))
        segment_rows.append(dict(
            start=int(start_idx) if isinstance(start_idx, int) else -1,
            end=int(end_idx) if isinstance(end_idx, int) else -1,
            fromNumber=(int(start_idx) + 1) if isinstance(start_idx, int) else 0,
            toNumber=(int(end_idx) + 1) if isinstance(end_idx, int) else 0,
            pixels=round(seg_px, 2),
            units=round(seg_units, 1) if seg_units > _ZERO_EPS else 0.0,
            text=format_route_length(seg_units),
            crossFloor=cross_floor,
            floors=seg_floor_rows,
        ))

    point_segments: list[dict] = []
    for idx, pt in enumerate(points or ()):
        is_start = idx == 0 or bool(pt.get("start", pt.get("newStart", False)))
        if is_start:
            point_segments.append(dict(
                hasSegment=False,
                isStart=True,
                start=-1,
                end=idx,
                fromNumber=0,
                toNumber=idx + 1,
                units=0.0,
                text="",
                crossFloor=False,
                floors=[],
                error=False,
            ))
            continue
        found = segment_by_end.get(idx)
        prev_pt = points[idx - 1]
        if found is not None:
            seg, per_floor = found
            seg_px = sum(per_floor.values())
            seg_units = pixels_to_units(seg_px, reference_long_edge, units_per_long_edge=units_per_long_edge)
            cross_floor = _segment_is_cross_floor(seg, prev_pt, pt)
            seg_floor_rows = []
            for fid in ordered_floors:
                fpx = per_floor.get(fid, 0.0)
                if fpx > _ZERO_EPS:
                    fu = pixels_to_units(fpx, reference_long_edge, units_per_long_edge=units_per_long_edge)
                    seg_floor_rows.append(dict(
                        id=fid,
                        name=labels.get(fid, f"{fid}层"),
                        units=round(fu, 1) if fu > _ZERO_EPS else 0.0,
                        text=format_route_length(fu),
                    ))
            point_segments.append(dict(
                hasSegment=True,
                isStart=False,
                start=idx - 1,
                end=idx,
                fromNumber=idx,
                toNumber=idx + 1,
                units=round(seg_units, 1) if seg_units > _ZERO_EPS else 0.0,
                text=format_route_length(seg_units),
                crossFloor=cross_floor,
                floors=seg_floor_rows,
                error=False,
            ))
        else:
            cross_floor = str(prev_pt.get("floor", "")) != str(pt.get("floor", ""))
            point_segments.append(dict(
                hasSegment=True,
                isStart=False,
                start=idx - 1,
                end=idx,
                fromNumber=idx,
                toNumber=idx + 1,
                units=0.0,
                text=format_route_length(0.0),
                crossFloor=cross_floor,
                floors=[],
                error=False,
            ))

    floor_rows: list[dict] = []
    total_px = 0.0
    active_rows: list[dict] = []
    for fid in ordered_floors:
        px = floor_px.get(fid, 0.0)
        total_px += px
        u = pixels_to_units(px, reference_long_edge, units_per_long_edge=units_per_long_edge)
        has_len = u > _ZERO_EPS
        row = dict(
            id=fid,
            name=labels.get(fid, f"{fid}层"),
            pixels=round(px, 2),
            units=round(u, 1) if has_len else 0.0,
            text=format_route_length(u),
            hasLength=has_len,
        )
        floor_rows.append(row)
        if has_len:
            active_rows.append(row)

    total_units = pixels_to_units(total_px, reference_long_edge, units_per_long_edge=units_per_long_edge)
    total_text = format_route_length(total_units)
    active_count = len(active_rows)
    compact_floor_text = (
        " · ".join(f"{r['name']} {_compact_number(r['units'])}" for r in active_rows)
        if active_count > 1
        else ""
    )
    if active_count > 1:
        detail = " · ".join(f"{r['name']} {r['text']}" for r in active_rows)
        tooltip = f"总长 {total_text}（{detail}）\n{UNIT_SHORT_DESCRIPTION}"
    elif active_count == 1:
        tooltip = f"总长 {total_text}（{active_rows[0]['name']} {active_rows[0]['text']}）\n{UNIT_SHORT_DESCRIPTION}"
    else:
        tooltip = f"总长 0 {UNIT_LABEL}\n{UNIT_SHORT_DESCRIPTION}"

    return dict(
        unit=UNIT_LABEL,
        unitsPerLongEdge=float(units_per_long_edge),
        referenceLongEdge=float(reference_long_edge),
        unitDescription=UNIT_SHORT_DESCRIPTION,
        unitDetailDescription=UNIT_DESCRIPTION,
        totalPixels=round(total_px, 2),
        totalUnits=round(total_units, 1) if total_units > _ZERO_EPS else 0.0,
        totalText=total_text,
        floors=floor_rows,
        activeFloorCount=active_count,
        multiFloor=len(floor_rows) > 1,
        hasMultiFloorRoute=active_count > 1,
        compactFloorText=compact_floor_text,
        tooltipText=tooltip,
        segments=segment_rows,
        pointSegments=point_segments,
        error=False,
    )
