"""Player-drawn nightmare routes on the built-in structure canvas.

Routes are stored as waypoints in the same game-native pixel frame as the
shared structure references (one canvas per map and floor). Rendering into
that canvas means the in-game overlay can use an identity bridge: the route
image lines up wherever the structure itself is aligned, with no per-map
calibration. Walkable space is the structure footprint; it does not encode
interior walls, so automatic routing follows open floor and corridors only.
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
                    if not parsed.empty:
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
        with self._lock:
            key = (str(map_id), str(floor))
            if route.empty:
                self._routes.pop(key, None)
            else:
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
            return {floor for (owner, floor) in self._routes if owner == str(map_id)}

    def count(self) -> int:
        with self._lock:
            return len(self._routes)


class Walkable:
    """Coarse routing grid over one structure footprint."""

    def __init__(self, mask: np.ndarray):
        height, width = mask.shape[:2]
        self.shape = (height, width)
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

    @staticmethod
    def _centre(cell) -> tuple[int, int]:
        return cell[0] * GRID + GRID // 2, cell[1] * GRID + GRID // 2

    def snap(self, point, *, reach: int = 12) -> tuple[int, int] | None:
        """Nearest open cell centre within ``reach`` cells, else None."""
        x, y = self._cell(point)
        if self.open[y, x]:
            return self._centre((x, y))
        if not len(self._open_cells):
            return None
        distance = np.abs(self._open_cells - (x, y)).max(axis=1)
        index = int(np.argmin(distance))
        if distance[index] > reach:
            return None
        return self._centre(tuple(int(value) for value in self._open_cells[index]))

    def path(self, start, goal) -> list[tuple[int, int]] | None:
        """A* through open cells, biased towards wide space; native pixels out."""
        source, target = self._cell(start), self._cell(goal)
        if not (self.open[source[1], source[0]] and self.open[target[1], target[0]]):
            return None
        if source == target:
            return [tuple(start), tuple(goal)]
        rows, columns = self.open.shape
        steps = ((1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
                 (1, 1, 1.4142), (1, -1, 1.4142), (-1, 1, 1.4142), (-1, -1, 1.4142))
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
                if dx and dy and not (self.open[y, nx] and self.open[ny, x]):
                    continue  # No corner cutting through void.
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
        contour = np.array([self._centre(cell) for cell in cells], np.int32).reshape(-1, 1, 2)
        simplified = cv2.approxPolyDP(contour, GRID * .9, False).reshape(-1, 2)
        points = [tuple(map(int, point)) for point in simplified]
        points[0], points[-1] = tuple(start), tuple(goal)
        return points


def route_polyline(route: FloorRoute, walkable: Walkable | None) -> list[list[tuple[int, int]]]:
    """Segments between consecutive waypoints, routed when snapping is on."""
    segments = []
    for start, goal in zip(route.points, route.points[1:]):
        routed = walkable.path(start, goal) if route.snap and walkable is not None else None
        segments.append(routed or [start, goal])
    return segments


def _font(size: int):
    for name in ("msyhbd.ttc", "msyh.ttc", "arialbd.ttf", "arial.ttf",
                 "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def render_layer(mask: np.ndarray, route: FloorRoute, walkable: Walkable | None = None) -> np.ndarray:
    """Straight-alpha BGRA on the structure canvas: footprint plus the route.

    Stroke sizes are native pixels; the overlay scales the whole canvas down
    to the game minimap, so they stay proportional to the structure.
    """
    height, width = mask.shape[:2]
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    footprint = Image.fromarray(np.where(mask > 0, 255, 0).astype(np.uint8), "L")
    image.paste((*BASE_FILL, 255), mask=footprint)
    draw = ImageDraw.Draw(image)
    color = _rgb(route.color)
    edge = tuple(round(channel * .55) for channel in color)
    unit = max(8, round(min(width, height) / 160))
    for segment in route_polyline(route, walkable):
        draw.line(segment, fill=(*edge, 255), width=unit * 2 + 6, joint="curve")
    for segment in route_polyline(route, walkable):
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
