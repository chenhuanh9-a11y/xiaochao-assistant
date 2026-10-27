"""Player-drawn nightmare routes on the built-in structure canvas.

Routes are stored as waypoints in the same game-native pixel frame as the
shared structure references (one canvas per map and floor). Rendering into
that canvas means the in-game overlay can use an identity bridge: the route
image lines up wherever the structure itself is aligned, with no per-map
calibration. An exact released-mask binding can add static source room/door
topology; other resources retain footprint routing. Neither footprint nor this
bounded topology pilot encodes complete interior collision or live door state.
"""
from __future__ import annotations

from dataclasses import dataclass
import heapq
import json
import math
import os
from pathlib import Path
import threading

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

SCHEMA = "xingtu-custom-routes-v1"
FLOORS = ("B1", "1", "2")
FLOOR_LABELS = {"B1": "负一楼", "1": "一楼", "2": "二楼"}
COLORS = ("#2f66eb", "#f08c00", "#e03150", "#12a37f", "#7c4dff")
DEFAULT_COLOR = COLORS[0]
MAX_POINTS = 60
GRID = 10  # Native pixels per routing cell; structure tiles are 60 px.
BASE_FILL = (218, 228, 238)  # Same footprint tone as the plain structure display.


def _rgb(color: str) -> tuple[int, int, int]:
    color = str(color).lstrip("#")
    return tuple(int(color[index:index + 2], 16) for index in (0, 2, 4))


def _valid_color(value) -> str:
    return value if isinstance(value, str) and value in COLORS else DEFAULT_COLOR


@dataclass(frozen=True)
class FloorRoute:
    points: tuple[tuple[int, int], ...] = ()
    color: str = DEFAULT_COLOR
    snap: bool = True

    @property
    def empty(self) -> bool:
        return not self.points

    def to_json(self) -> dict:
        return dict(points=[list(point) for point in self.points], color=self.color, snap=self.snap)

    @classmethod
    def from_json(cls, raw) -> "FloorRoute":
        if not isinstance(raw, dict):
            return cls()
        points = []
        for point in raw.get("points") or ():
            if (isinstance(point, (list, tuple)) and len(point) == 2
                    and all(isinstance(value, (int, float)) and math.isfinite(value) for value in point)):
                points.append((int(round(point[0])), int(round(point[1]))))
        return cls(tuple(points[:MAX_POINTS]), _valid_color(raw.get("color")), raw.get("snap") is not False)


class CustomRouteStore:
    """JSON file next to the user settings; every change is saved atomically."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.revision = 0
        self._lock = threading.RLock()
        self.use_in_game = False
        self._routes: dict[tuple[str, str], FloorRoute] = {}
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
            return
        self.use_in_game = raw.get("useInGame") is True
        for map_id, floors in (raw.get("maps") or {}).items():
            for floor, route in (floors or {}).items() if isinstance(floors, dict) else ():
                if str(floor) in FLOORS:
                    parsed = FloorRoute.from_json(route)
                    self._routes[(str(map_id), str(floor))] = parsed

    def _save(self) -> bool:
        maps: dict[str, dict] = {}
        for (map_id, floor), route in sorted(self._routes.items()):
            maps.setdefault(map_id, {})[floor] = route.to_json()
        payload = dict(schema=SCHEMA, useInGame=self.use_in_game, maps=maps)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(temporary, self.path)
            return True
        except OSError:
            return False

    def route(self, map_id, floor) -> FloorRoute:
        with self._lock:
            return self._routes.get((str(map_id), str(floor)), FloorRoute())

    def set_route(self, map_id, floor, route: FloorRoute) -> bool:
        """Save points and preferences, including preferences before the first point.

        An empty route clears points while retaining its supplied preferences;
        passing ``FloorRoute()`` also resets preferences to their defaults.
        """
        with self._lock:
            key = (str(map_id), str(floor))
            self._routes[key] = route
            self.revision += 1
            return self._save()

    def set_use_in_game(self, enabled: bool) -> bool:
        with self._lock:
            self.use_in_game = bool(enabled)
            self.revision += 1
            return self._save()

    def floors_with_routes(self, map_id) -> set[str]:
        with self._lock:
            return {floor for (owner, floor), route in self._routes.items()
                    if owner == str(map_id) and not route.empty}

    def count(self) -> int:
        with self._lock:
            return sum(not route.empty for route in self._routes.values())


class RoutePathError(ValueError):
    """A snapped route cannot be drawn within the available footprint.

    ``reason`` is one of missing_footprint, invalid_endpoint or no_path.
    Point and segment indexes, when present, are zero based.
    """

    def __init__(self, reason: str, *, point_index=None, segment_index=None):
        self.reason = reason
        self.point_index = point_index
        self.segment_index = segment_index
        if reason == "missing_footprint":
            message = "缺少本层结构范围，无法贴合路线。"
        elif reason == "invalid_endpoint":
            message = f"路点 {point_index + 1} 不在可用结构范围内，请移动路点或关闭贴合。"
        else:
            message = f"路点 {segment_index + 1} 与 {segment_index + 2} 无法在结构范围内连通，请移动路点或关闭贴合。"
        super().__init__(message)


class Walkable:
    """Coarse routing grid over one structure footprint."""

    def __init__(self, mask: np.ndarray):
        from route_topology import for_mask
        self.topology = for_mask(mask)
        height, width = mask.shape[:2]
        self.shape = (height, width)
        self._footprint = mask > 0
        rows, columns = max(1, height // GRID), max(1, width // GRID)
        footprint = (mask[:rows * GRID, :columns * GRID] > 0).astype(np.float32)
        cells = cv2.resize(footprint, (columns, rows), interpolation=cv2.INTER_AREA) >= .5
        self.open = cells
        padded = np.pad(cells.astype(np.uint8), 1)
        # Distance to the nearest void keeps routes on corridor centrelines.
        self.clearance = cv2.distanceTransform(padded, cv2.DIST_L2, 3)[1:-1, 1:-1]
        ys, xs = np.nonzero(cells)
        self._open_cells = np.column_stack((xs, ys)) if len(xs) else np.empty((0, 2), int)

    def _cell(self, point) -> tuple[int, int]:
        rows, columns = self.open.shape
        x = min(columns - 1, max(0, int(point[0]) // GRID))
        y = min(rows - 1, max(0, int(point[1]) // GRID))
        return x, y

    def contains(self, point) -> bool:
        """Whether a native endpoint is in bounds, on footprint and in an open cell."""
        if self.topology is not None:
            return self.topology.contains(point)
        height, width = self.shape
        try:
            x, y = point
            if not (math.isfinite(x) and math.isfinite(y) and 0 <= x < width and 0 <= y < height):
                return False
            cx, cy = self._cell(point)
            return bool(self._footprint[int(y), int(x)] and self.open[cy, cx]
                        and (self.topology is None or self.topology.contains(point)))
        except (TypeError, ValueError, OverflowError):
            return False

    def _line_on_footprint(self, start, goal) -> bool:
        """Check the rasterized centreline, including sub-cell holes in the mask."""
        x0, y0 = map(int, start)
        x1, y1 = map(int, goal)
        left, top = min(x0, x1), min(y0, y1)
        right, bottom = max(x0, x1), max(y0, y1)
        height, width = self.shape
        if left < 0 or top < 0 or right >= width or bottom >= height:
            return False
        line = np.zeros((bottom - top + 1, right - left + 1), np.uint8)
        cv2.line(line, (x0 - left, y0 - top), (x1 - left, y1 - top), 1, 1)
        return not bool(np.any((line > 0) & ~self._footprint[top:bottom + 1, left:right + 1]))

    @staticmethod
    def _centre(cell) -> tuple[int, int]:
        return cell[0] * GRID + GRID // 2, cell[1] * GRID + GRID // 2

    def snap(self, point, *, reach: int = 12) -> tuple[int, int] | None:
        """Nearest open cell centre within ``reach`` cells, else None."""
        if self.topology is not None:
            return self.topology.snap(point)
        height, width = self.shape
        if not (0 <= point[0] < width and 0 <= point[1] < height):
            return None
        x, y = self._cell(point)
        if self.open[y, x]:
            centre = self._centre((x, y))
            if self.contains(centre):
                return centre
        if not len(self._open_cells):
            return None
        distance = np.abs(self._open_cells - (x, y)).max(axis=1)
        for index in np.argsort(distance):
            if distance[index] > reach:
                break
            centre = self._centre(tuple(int(value) for value in self._open_cells[index]))
            if self.contains(centre):
                return centre
        return None

    def path(self, start, goal) -> list[tuple[int, int]] | None:
        """A* through open cells, biased towards wide space; native pixels out."""
        if self.topology is not None:
            return self.topology.path(start, goal)
        if not (self.contains(start) and self.contains(goal)):
            return None
        source, target = self._cell(start), self._cell(goal)
        if not (self.open[source[1], source[0]] and self.open[target[1], target[0]]):
            return None
        def orthogonal(a,b):
            for bend in [(b[0],a[1]),(a[0],b[1])]:
                points=[tuple(a),bend,tuple(b)]
                points=[p for i,p in enumerate(points) if i==0 or p!=points[i-1]]
                if all(self._line_on_footprint(p,q) for p,q in zip(points,points[1:])):
                    return points
            return None
        if source == target:
            return orthogonal(start,goal)
        rows, columns = self.open.shape
        # With no source diagonal geometry, a footprint alone cannot authorize
        # slanted shortcuts through ordinary corridors or right-angle junctions.
        steps = ((1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0))
        clearance = self.clearance
        best = {source: 0.0}
        parent = {}
        queue = [(0.0, source)]
        tx, ty = target
        while queue:
            _, cell = heapq.heappop(queue)
            if cell == target:
                break
            cost = best[cell]
            x, y = cell
            for dx, dy, length in steps:
                nx, ny = x + dx, y + dy
                if not (0 <= nx < columns and 0 <= ny < rows) or not self.open[ny, nx]:
                    continue
                step = cost + length * (1.0 + 2.5 / (1.0 + clearance[ny, nx]))
                neighbour = (nx, ny)
                if step < best.get(neighbour, math.inf):
                    best[neighbour] = step
                    parent[neighbour] = cell
                    heapq.heappush(queue, (step + math.hypot(tx - nx, ty - ny), neighbour))
        if target not in best:
            return None
        cells = [target]
        while cells[-1] != source:
            cells.append(parent[cells[-1]])
        cells.reverse()
        # Remove only collinear grid centres: approximate simplification can cut
        # across void at bends. Retain centres beside arbitrary native endpoints.
        centres = [self._centre(cell) for cell in cells]
        prefix=orthogonal(start,centres[0]);suffix=orthogonal(centres[-1],goal)
        if prefix is None or suffix is None:
            return None
        points = prefix[:]
        for previous, current, following in zip(centres, centres[1:], centres[2:]):
            if (current[0] - previous[0], current[1] - previous[1]) != (
                    following[0] - current[0], following[1] - current[1]):
                points.append(current)
        points.extend(suffix)
        points = [point for index, point in enumerate(points) if index == 0 or point != points[index - 1]]
        if not all(self._line_on_footprint(a, b) for a, b in zip(points, points[1:])):
            return None
        return points


def route_polyline(route: FloorRoute, walkable: Walkable | None) -> list[list[tuple[int, int]]]:
    """Build segments, raising RoutePathError rather than inventing snapped paths."""
    if route.snap and route.points:
        if walkable is None:
            raise RoutePathError("missing_footprint")
        for index, point in enumerate(route.points):
            if not walkable.contains(point):
                raise RoutePathError("invalid_endpoint", point_index=index)
    segments = []
    for index, (start, goal) in enumerate(zip(route.points, route.points[1:])):
        if route.snap:
            routed = walkable.path(start, goal)
            if routed is None:
                raise RoutePathError("no_path", segment_index=index)
            segments.append(routed)
        else:
            segments.append([start, goal])
    return segments


def _font(size: int):
    for name in ("msyhbd.ttc", "msyh.ttc", "arialbd.ttf", "arial.ttf",
                 "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def render_layer(mask: np.ndarray, route: FloorRoute, walkable: Walkable | None = None, *, background=None) -> np.ndarray:
    """Straight-alpha BGRA on the structure canvas: footprint plus the route.

    Stroke sizes are native pixels; the overlay scales the whole canvas down
    to the game minimap, so they stay proportional to the structure.
    """
    height, width = mask.shape[:2]
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    if background is not None and background.image is not None:
        image.alpha_composite(background.image)
    else:
        footprint = Image.fromarray(np.where(mask > 0, 255, 0).astype(np.uint8), "L")
        image.paste((*BASE_FILL, 255), mask=footprint)
    draw = ImageDraw.Draw(image)
    color = _rgb(route.color)
    edge = tuple(round(channel * .55) for channel in color)
    unit = max(8, round(min(width, height) / 160))
    segments = route_polyline(route, walkable)
    for segment in segments:
        draw.line(segment, fill=(*edge, 255), width=unit * 2 + 6, joint="curve")
    for segment in segments:
        draw.line(segment, fill=(*color, 255), width=unit * 2, joint="curve")
    radius = unit * 3
    font = _font(round(radius * 1.15))
    for index, (x, y) in enumerate(route.points, start=1):
        draw.ellipse((x - radius - 3, y - radius - 3, x + radius + 3, y + radius + 3), fill=(*edge, 255))
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(255, 255, 255, 255))
        draw.text((x, y + 1), str(index), fill=(*edge, 255), font=font, anchor="mm")
    pixels = np.asarray(image)
    bgra = np.ascontiguousarray(pixels[:, :, [2, 1, 0, 3]])
    bgra[bgra[:, :, 3] == 0] = 0
    return bgra
