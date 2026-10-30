"""Custom nightmare maps: the player's own art, game road data, saved routes.

Three things are combined per map floor:
- display: a legacy custom-map image (the maintainer's own art) plus a 3x3
  matrix from its pixels to the native structure canvas, found by
  registration when the package was built;
- planning: the game's road graph for that floor (tile-centre points, room
  ids, typed links through paired doors) in native pixels;
- cross-floor: verified stairs carried over from the legacy editor, also in
  native pixels, because the game data has no verified transfers yet.

Routes are saved as data (ordered targets with colours), never as images.
The in-game layer is the legacy image warped into the native canvas with the
route drawn on top, so the overlay's identity bridge aligns it like the
structure itself.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import heapq
import json
import math
import os
from pathlib import Path
import sys
import threading
import uuid

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

SCHEMA = "xingtu-custom-maps-v1"
GALLERY_SCHEMA = "xingtu-custom-gallery-v1"
FLOORS = ("B1", "1", "2")
FLOOR_LABELS = {"B1": "负一楼", "1": "一楼", "2": "二楼"}
COLORS = (("#63CAFF", "浅蓝"), ("#FFD54F", "黄色"), ("#FF6B81", "粉红"), ("#63E6BE", "绿色"), ("#FFFFFF", "白色"))
DEFAULT_COLOR = "#63CAFF"
MAX_POINTS = 128
PICK_RADIUS = 45  # Native pixels: within one road tile (60 px) of a point.
PROFILE_PREFIX = "custom:"
DEFAULT_GALLERY_ID = "default"
LEGACY_SCHEMA = "xingtu-custom-routes-v1"

USER_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
DEFAULT_LIBRARY_ROOT = Path(os.environ.get("XINGTU_CUSTOM_MAPS_ROOT", "") or USER_ROOT / "assets" / "custom-maps")
DEFAULT_GALLERY_FILE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "custom_default_gallery.json"


class RouteError(ValueError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def mask_sha256(mask) -> str:
    """Hash the binary native canvas, independent of strides and 1/255 values."""
    array = np.asarray(mask)
    if array.ndim != 2:
        raise ValueError("结构 mask 必须是二维数组")
    return hashlib.sha256(np.ascontiguousarray(array > 0, dtype=np.uint8).tobytes()).hexdigest()


def _rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return tuple(int(color[index:index + 2], 16) for index in (0, 2, 4))


def _valid_color(value) -> bool:
    return isinstance(value, str) and len(value) == 7 and value[0] == "#" and all(
        c in "0123456789abcdefABCDEF" for c in value[1:])


# Library ----------------------------------------------------------------------
@dataclass(frozen=True)
class FloorAsset:
    map_id: str
    floor: str
    image_path: Path
    image_size: tuple[int, int]
    to_native: np.ndarray  # 3x3, image pixels -> native pixels
    native_shape: tuple[int, int]
    topology_path: Path
    entrances: tuple[tuple[str, float, float], ...]  # label, image x, image y

    @property
    def to_image(self) -> np.ndarray:
        return np.linalg.inv(self.to_native)


class CustomMapLibrary:
    """Read-only access to an installed custom map package."""

    def __init__(self, root: Path = DEFAULT_LIBRARY_ROOT):
        self.root = Path(root)
        self.error = ""
        self.version = ""
        self._maps: dict[str, dict] = {}
        self._order: list[str] = []
        self._graphs: dict[str, MapGraph] = {}
        self._verified: set[Path] = set()
        self._images: dict[tuple, Image.Image] = {}
        self._lock = threading.RLock()
        try:
            manifest = json.loads((self.root / "manifest.json").read_text("utf-8"))
            if manifest.get("schema") != SCHEMA:
                raise ValueError("自定义地图资源版本不受支持")
            self.version = str(manifest.get("version") or "")
            for row in manifest["maps"]:
                self._maps[row["mapId"]] = row
            self._order = [row["mapId"] for row in sorted(manifest["maps"], key=lambda r: r.get("templateNumber", 0))]
        except FileNotFoundError:
            self.error = "自定义地图资源未安装"
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.error = f"自定义地图资源无法读取：{error}"

    @property
    def available(self) -> bool:
        return not self.error and bool(self._maps)

    def maps(self) -> list[tuple[str, str]]:
        return [(map_id, self._maps[map_id]["name"]) for map_id in self._order]

    def has(self, map_id) -> bool:
        return str(map_id) in self._maps

    def name(self, map_id) -> str:
        return self._maps.get(str(map_id), {}).get("name", str(map_id))

    def _checked(self, relative: str, expected: str) -> Path:
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError("资源路径越界")
        if path not in self._verified:
            if _sha256(path) != expected:
                raise ValueError(f"资源文件校验失败：{relative}")
            self._verified.add(path)
        return path

    def floor(self, map_id, floor) -> FloorAsset:
        row = self._maps[str(map_id)]["floors"][str(floor)]
        return FloorAsset(
            map_id=str(map_id), floor=str(floor),
            image_path=self._checked(row["image"], row["imageSha256"]),
            image_size=tuple(row["imageSize"]), to_native=np.asarray(row["matrixImageToNative"], np.float64),
            native_shape=tuple(row["nativeShape"]),
            topology_path=self._checked(row["topology"], row["topologySha256"]),
            entrances=tuple((e["label"], float(e["x"]), float(e["y"])) for e in row.get("entrances", ())))

    def image(self, map_id, floor) -> Image.Image:
        key = (str(map_id), str(floor))
        with self._lock:
            image = self._images.get(key)
            if image is None:
                with Image.open(self.floor(map_id, floor).image_path) as source:
                    image = source.convert("RGBA")
                self._images[key] = image
                while len(self._images) > 6:
                    self._images.pop(next(iter(self._images)))
            return image

    def validate_reference(self, map_id, floor, clean_mask) -> None:
        """Fail closed when the current runtime geometry differs from registration."""
        row = self._maps[str(map_id)]["floors"][str(floor)]
        if tuple(np.asarray(clean_mask).shape) != tuple(row["nativeShape"]):
            raise ValueError("自定义地图与当前地图结构尺寸不一致")
        expected = row.get("maskSha256")
        if (not isinstance(expected, str) or len(expected) != 64
                or mask_sha256(clean_mask) != expected):
            raise ValueError("自定义地图与当前地图结构 mask 校验不一致，请更新资源包")

    def exit_icon(self) -> Image.Image | None:
        try:
            with Image.open(self.root / "exit-icon.png") as icon:
                return icon.convert("RGBA")
        except OSError:
            return None

    def graph(self, map_id) -> "MapGraph":
        map_id = str(map_id)
        with self._lock:
            graph = self._graphs.get(map_id)
            if graph is None:
                floors = {floor: FloorGraph.load(self.floor(map_id, floor).topology_path)
                          for floor in FLOORS if floor in self._maps[map_id]["floors"]}
                graph = self._graphs[map_id] = MapGraph(floors, self._maps[map_id].get("stairs", ()))
            return graph


# Road graph -------------------------------------------------------------------
class FloorGraph:
    def __init__(self, points, rooms, links):
        self.points = np.asarray(points, np.float64)
        self.rooms = list(rooms)
        self.neighbours: list[list[tuple[int, float]]] = [[] for _ in self.points]
        for a, b, _kind in links:
            cost = float(np.hypot(*(self.points[a] - self.points[b])))
            self.neighbours[a].append((b, cost))
            self.neighbours[b].append((a, cost))
        members: dict[str, list[int]] = {}
        for index, room in enumerate(self.rooms):
            members.setdefault(room, []).append(index)
        self.room_members = members
        self.room_anchor: dict[str, int] = {}
        self.is_room: dict[str, bool] = {}
        for room, indexes in members.items():
            block = self.points[indexes]
            span = (block.max(axis=0) - block.min(axis=0)) / 60 + 1
            # One tile wide is a corridor; a real room spans two tiles both ways.
            self.is_room[room] = bool(span.min() >= 2)
            centre = block.mean(axis=0)
            self.room_anchor[room] = indexes[int(np.argmin(np.hypot(*(block - centre).T)))]

    @classmethod
    def load(cls, path: Path) -> "FloorGraph":
        data = json.loads(Path(path).read_text("utf-8"))
        return cls(data["points"], data["pointRooms"], data["links"])

    def nearest(self, x, y, radius=PICK_RADIUS) -> int | None:
        if not len(self.points):
            return None
        distance = np.hypot(self.points[:, 0] - x, self.points[:, 1] - y)
        index = int(np.argmin(distance))
        return index if distance[index] <= radius else None


class MapGraph:
    """Three floors joined by stairs; nodes are (floor, point index)."""

    def __init__(self, floors: dict[str, FloorGraph], stairs):
        self.floors = floors
        self.links: dict[tuple[str, int], list[tuple[tuple[str, int], float]]] = {}
        for stair in stairs:
            ends = []
            for end in (stair["from"], stair["to"]):
                graph = floors.get(end["floor"])
                node = graph.nearest(end["x"], end["y"], radius=60) if graph is not None else None
                ends.append(None if node is None else (end["floor"], node))
            if None in ends or not stair.get("cost", 0) > 0:
                continue
            self.links.setdefault(ends[0], []).append((ends[1], float(stair["cost"])))
            if stair.get("bidirectional", True):
                self.links.setdefault(ends[1], []).append((ends[0], float(stair["cost"])))

    def pick(self, floor, x, y) -> dict | None:
        """Old editor rule: a click in a room targets the room; elsewhere a via point."""
        graph = self.floors.get(floor)
        node = graph.nearest(x, y) if graph is not None else None
        if node is None:
            return None
        room = graph.rooms[node]
        if graph.is_room.get(room):
            anchor = graph.room_anchor[room]
            px, py = graph.points[anchor]
            return dict(floor=floor, x=float(px), y=float(py), kind="room", room=room)
        px, py = graph.points[node]
        return dict(floor=floor, x=float(px), y=float(py), kind="via", room=None)

    def node(self, point) -> tuple[str, int]:
        graph = self.floors.get(point.get("floor"))
        if graph is None:
            raise RouteError("目标所在楼层不存在")
        if point.get("kind") == "room" and point.get("room") in graph.room_anchor:
            return point["floor"], graph.room_anchor[point["room"]]
        node = graph.nearest(point.get("x", -1e9), point.get("y", -1e9))
        if node is None:
            raise RouteError("目标不在可走区域内")
        return point["floor"], node

    def path(self, start: tuple[str, int], goal: tuple[str, int]) -> tuple[list[tuple[str, int]], float]:
        if start == goal:
            return [start], 0.0
        same_floor = start[0] == goal[0]
        target = self.floors[goal[0]].points[goal[1]]
        def heuristic(node):
            # Stairs may leave this floor and return near the goal for less
            # than its Euclidean distance. Dijkstra is admissible in that case.
            if self.links or not same_floor or node[0] != goal[0]:
                return 0.0
            return float(math.hypot(*(self.floors[node[0]].points[node[1]] - target)))
        best = {start: 0.0}
        parent = {}
        queue = [(heuristic(start), 0.0, start)]
        while queue:
            _, cost, node = heapq.heappop(queue)
            if node == goal:
                cells = [node]
                while cells[-1] != start:
                    cells.append(parent[cells[-1]])
                return cells[::-1], cost
            if cost > best.get(node, math.inf):
                continue
            floor, index = node
            steps = [((floor, nxt), step) for nxt, step in self.floors[floor].neighbours[index]]
            steps.extend(self.links.get(node, ()))
            for neighbour, step in steps:
                candidate = cost + step
                if candidate < best.get(neighbour, math.inf) - 1e-9:
                    best[neighbour] = candidate
                    parent[neighbour] = node
                    heapq.heappush(queue, (candidate + heuristic(neighbour), candidate, neighbour))
        if start[0] != goal[0] and not self.links:
            raise RouteError("这张图的跨层楼梯尚未核实，暂时只能规划同层路线")
        raise RouteError("这两个位置之间没有已确认的通路，请调整目标")

    def plan(self, points) -> list[dict]:
        """Segments between consecutive targets; a point marked start begins a new route."""
        if len(points) > MAX_POINTS:
            raise RouteError(f"一张地图最多添加 {MAX_POINTS} 个目标")
        segments = []
        for index in range(1, len(points)):
            if points[index].get("start"):
                continue
            try:
                cells, cost = self.path(self.node(points[index - 1]), self.node(points[index]))
            except RouteError as error:
                raise RouteError(f"第{index}段：{error}") from None
            segments.append(dict(start=index - 1, end=index, cells=cells, cost=cost,
                                 color=points[index].get("color") or DEFAULT_COLOR))
        return segments

    def floor_lines(self, segments, floor) -> list[tuple[list[tuple[float, float]], str]]:
        """Polylines on one floor (native pixels), collinear points merged."""
        lines = []
        for segment in segments:
            run = []
            for node in segment["cells"] + [(None, None)]:
                if node[0] == floor:
                    run.append(tuple(self.floors[floor].points[node[1]]))
                    continue
                if len(run) >= 2:
                    lines.append((_merge_collinear(run), segment["color"]))
                run = []
        return lines


def _merge_collinear(points):
    out = [points[0]]
    for current, following in zip(points[1:], points[2:]):
        previous = out[-1]
        cross = (current[0] - previous[0]) * (following[1] - current[1]) - (current[1] - previous[1]) * (following[0] - current[0])
        if abs(cross) > 1e-6:
            out.append(current)
    out.append(points[-1])
    return out


def marker_groups(points, floor) -> list[tuple[float, float, str]]:
    """Numbered pills; repeated visits to one target share a pill ("1/3/5+2")."""
    groups: dict[tuple, list[int]] = {}
    places: dict[tuple, tuple[float, float]] = {}
    for number, point in enumerate(points, start=1):
        if point.get("floor") != floor:
            continue
        key = ("room", point["room"]) if point.get("kind") == "room" else ("via", round(point["x"]), round(point["y"]))
        groups.setdefault(key, []).append(number)
        places.setdefault(key, (point["x"], point["y"]))
    result = []
    for key, numbers in groups.items():
        label = "/".join(map(str, numbers[:3])) + (f"+{len(numbers) - 3}" if len(numbers) > 3 else "")
        result.append((*places[key], label))
    return result


def insertion_index(points, segments, graph: MapGraph, target) -> int:
    """Old editor rule: a via point joins the segment it was clicked next to."""
    if target.get("kind") != "via" or len(points) < 2 or not segments:
        return len(points)
    best, insert = math.inf, len(points)
    floor = graph.floors[target["floor"]]
    for segment in segments:
        for node_floor, index in segment["cells"]:
            if node_floor != target["floor"]:
                continue
            px, py = floor.points[index]
            distance = (px - target["x"]) ** 2 + (py - target["y"]) ** 2
            if distance < best:
                best, insert = distance, segment["end"]
    return insert


# Rendering --------------------------------------------------------------------
PILL_FILL, PILL_EDGE = (20, 51, 75, 255), (198, 239, 255, 255)


def _font(size):
    for name in ("msyhbd.ttc", "msyh.ttc", "arialbd.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                 "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


ROUTE_OUTLINE = (15, 27, 40, 255)


def draw_route(draw: ImageDraw.ImageDraw, transform, lines, markers, *, width: float, pill: float):
    """Draw lines and pills; ``transform`` maps native points to target pixels."""
    drawn = [([transform(p) for p in line], color) for line, color in lines]
    # Same as the Android route: a dark outline about a third of the line width on each side, drawn under
    # every line first so crossing routes keep their colours.
    outline = width + max(2.0, width * .7)
    for stroke, fills in ((outline, [ROUTE_OUTLINE] * len(drawn)), (width, [(*_rgb(color), 255) for _, color in drawn])):
        for (pts, _), fill in zip(drawn, fills):
            draw.line(pts, fill=fill, width=max(1, round(stroke)), joint="curve")
            r = stroke / 2
            for x, y in (pts[0], pts[-1]):
                draw.ellipse((x - r, y - r, x + r, y + r), fill=fill)
    font = _font(max(8, round(pill * 1.2)))
    for x, y, label in markers:
        cx, cy = transform((x, y))
        text = draw.textlength(label, font=font)
        half = max(pill, text / 2 + pill * .4)
        draw.rounded_rectangle((cx - half, cy - pill, cx + half, cy + pill), radius=pill, fill=PILL_FILL,
                               outline=PILL_EDGE, width=max(1, round(pill / 10)))
        draw.text((cx, cy), label, fill=(255, 255, 255, 255), font=font, anchor="mm")


def render_game_layer(library: CustomMapLibrary, map_id, floor, points) -> np.ndarray:
    """Straight-alpha BGRA on the native canvas: legacy art, entrances, route."""
    asset = library.floor(map_id, floor)
    art = np.asarray(library.image(map_id, floor))
    height, width = asset.native_shape
    warped = cv2.warpAffine(art, asset.to_native[:2], (width, height), flags=cv2.INTER_LINEAR,
                            borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
    canvas = Image.fromarray(warped, "RGBA")
    scale = float(np.sqrt(abs(np.linalg.det(asset.to_native[:2, :2]))))
    icon = library.exit_icon()
    if icon is not None:
        size = max(20, round(max(asset.image_size) * .025 * scale))
        icon = icon.resize((size, size), Image.Resampling.LANCZOS)
        for _label, x, y in asset.entrances:
            u, v, _ = asset.to_native @ [x, y, 1]
            canvas.alpha_composite(icon, (round(u - size / 2), round(v - size / 2)))
    if points:
        graph = library.graph(map_id)
        try:
            segments = graph.plan(points)
        except RouteError:
            segments = []
        draw = ImageDraw.Draw(canvas)
        draw_route(draw, lambda p: p, graph.floor_lines(segments, floor), marker_groups(points, floor),
                   width=6 * scale, pill=20 * scale)
    rgba = np.asarray(canvas)
    bgra = np.ascontiguousarray(rgba[:, :, [2, 1, 0, 3]])
    bgra[bgra[:, :, 3] == 0] = 0
    return bgra


# Galleries --------------------------------------------------------------------
def _clean_points(raw) -> list[dict]:
    points = []
    for point in raw if isinstance(raw, list) else ():
        if not isinstance(point, dict) or point.get("floor") not in FLOORS:
            continue
        try:
            x, y = float(point["x"]), float(point["y"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (math.isfinite(x) and math.isfinite(y)):
            continue
        clean = dict(floor=point["floor"], x=x, y=y, kind="room" if point.get("kind") == "room" else "via",
                     room=str(point["room"]) if point.get("kind") == "room" and point.get("room") else None,
                     color=point["color"].upper() if _valid_color(point.get("color")) else DEFAULT_COLOR)
        if point.get("start") is True:
            clean["start"] = True
        points.append(clean)
    return points[:MAX_POINTS]


def _clean_gallery(raw) -> dict | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not raw["id"]:
        return None
    maps = {}
    for map_id, route in (raw.get("maps") or {}).items() if isinstance(raw.get("maps"), dict) else ():
        points = _clean_points(route.get("points") if isinstance(route, dict) else None)
        if points:
            maps[str(map_id)] = dict(points=points)
    return dict(id=raw["id"], name=str(raw.get("name") or "未命名图库")[:24], maps=maps)


class GalleryStore:
    """A read-only default gallery plus the player's galleries in one JSON file."""

    def __init__(self, path: Path, default_path: Path = DEFAULT_GALLERY_FILE, *, legacy_path: Path | None = None):
        self.path = Path(path)
        self.revision = 0
        self._lock = threading.RLock()
        self.migration_error = ""
        self._legacy_migrations = []
        try:
            default = _clean_gallery(json.loads(Path(default_path).read_text("utf-8")))
        except (OSError, ValueError):
            default = None
        self.default = default or dict(id=DEFAULT_GALLERY_ID, name="默认路线", maps={})
        self.default["id"] = DEFAULT_GALLERY_ID
        self._galleries: list[dict] = []
        migration_writable = not self.path.exists()
        try:
            raw = json.loads(self.path.read_text("utf-8"))
            if isinstance(raw, dict) and raw.get("schema") == GALLERY_SCHEMA:
                migration_writable = True
                migrations = raw.get("legacyRouteMigrations")
                self._legacy_migrations = [value for value in (migrations if isinstance(migrations, list) else [])
                                           if isinstance(value, str) and len(value) == 64]
                for item in raw.get("galleries") or ():
                    gallery = _clean_gallery(item)
                    if gallery and gallery["id"] != DEFAULT_GALLERY_ID:
                        self._galleries.append(gallery)
        except (OSError, ValueError):
            pass
        self._migrate_legacy(Path(legacy_path) if legacy_path is not None
                             else self.path.with_name("custom-routes.json"), migration_writable)

    def _migrate_legacy(self, source: Path, writable: bool) -> None:
        """Copy legacy native waypoints into per-floor galleries; never move/edit source.

        Separate galleries retain every floor's point budget and do not invent
        cross-floor segments or auto-enable a route under the new planner.
        The source remains the lossless backup (including snap/useInGame).
        """
        try:
            data = source.read_bytes()
        except FileNotFoundError:
            return
        except OSError as error:
            self.migration_error = f"旧路线无法读取：{error}"
            return
        digest = hashlib.sha256(data).hexdigest()
        if digest in self._legacy_migrations:
            return
        if not writable or source.resolve() == self.path.resolve():
            self.migration_error = "图库文件不可迁移；旧路线原文件已保留"
            return
        try:
            raw = json.loads(data.decode("utf-8-sig"))
            if not isinstance(raw, dict) or raw.get("schema") != LEGACY_SCHEMA or not isinstance(raw.get("maps"), dict):
                raise ValueError("旧路线格式无效")
            galleries = {}
            for map_id, floors in raw["maps"].items():
                if not isinstance(floors, dict):
                    raise ValueError("旧路线楼层格式无效")
                for floor, route in floors.items():
                    if floor not in FLOORS or not isinstance(route, dict):
                        raise ValueError("旧路线楼层无效")
                    points = route.get("points", [])
                    if not isinstance(points, list) or len(points) > MAX_POINTS:
                        raise ValueError("旧路线点数或格式无效")
                    converted = []
                    for point in points:
                        if (not isinstance(point, list) or len(point) != 2
                                or any(type(value) not in (int, float) or not math.isfinite(value) for value in point)):
                            raise ValueError("旧路线坐标无效")
                        converted.append(dict(floor=floor, x=point[0], y=point[1], kind="via", room=None,
                                              color=route.get("color") if _valid_color(route.get("color")) else DEFAULT_COLOR))
                    if converted:
                        converted[0]["start"] = True
                        gallery = galleries.setdefault(floor, dict(id=f"legacy-import-{floor}-{digest[:12]}",
                            name=f"旧路线 · {FLOOR_LABELS[floor]}", maps={}))
                        gallery["maps"][str(map_id)] = dict(points=converted)
            additions = list(galleries.values())
            self._galleries.extend(additions)
            self._legacy_migrations.append(digest)
            if not self._save():
                if additions:
                    del self._galleries[-len(additions):]
                self._legacy_migrations.remove(digest)
                raise ValueError("迁移保存失败")
            self.revision += 1
        except (OSError, ValueError, TypeError, OverflowError) as error:
            self.migration_error = f"旧路线迁移失败：{error}；原文件已保留"

    def _save(self) -> bool:
        payload = dict(schema=GALLERY_SCHEMA, galleries=self._galleries,
                       legacyRouteMigrations=self._legacy_migrations)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(temporary, self.path)
            return True
        except OSError:
            return False

    def galleries(self) -> list[tuple[str, str, bool]]:
        with self._lock:
            return [(self.default["id"], self.default["name"], True)] + [(g["id"], g["name"], False) for g in self._galleries]

    def _find(self, gallery_id) -> dict | None:
        if gallery_id == DEFAULT_GALLERY_ID:
            return self.default
        return next((g for g in self._galleries if g["id"] == gallery_id), None)

    def name(self, gallery_id) -> str | None:
        with self._lock:
            gallery = self._find(gallery_id)
            return gallery["name"] if gallery else None

    def readonly(self, gallery_id) -> bool:
        return gallery_id == DEFAULT_GALLERY_ID

    def route(self, gallery_id, map_id) -> list[dict]:
        with self._lock:
            gallery = self._find(gallery_id)
            route = (gallery or {}).get("maps", {}).get(str(map_id))
            return [dict(point) for point in route["points"]] if route else []

    def drawn_maps(self, gallery_id) -> set[str]:
        with self._lock:
            return set((self._find(gallery_id) or {}).get("maps", {}))

    def create(self, name, copy_from=None) -> str:
        with self._lock:
            source = self._find(copy_from) if copy_from else None
            gallery = dict(id=uuid.uuid4().hex[:12], name=(str(name).strip() or "我的路线")[:24],
                           maps=json.loads(json.dumps(source["maps"])) if source else {})
            self._galleries.append(gallery)
            self.revision += 1
            self._save()
            return gallery["id"]

    def rename(self, gallery_id, name) -> bool:
        with self._lock:
            gallery = self._find(gallery_id)
            if gallery is None or self.readonly(gallery_id) or not str(name).strip():
                return False
            gallery["name"] = str(name).strip()[:24]
            self.revision += 1
            return self._save()

    def delete(self, gallery_id) -> bool:
        with self._lock:
            before = len(self._galleries)
            self._galleries = [g for g in self._galleries if g["id"] != gallery_id]
            if len(self._galleries) == before:
                return False
            self.revision += 1
            return self._save()

    def set_route(self, gallery_id, map_id, points) -> bool:
        with self._lock:
            gallery = self._find(gallery_id)
            if gallery is None or self.readonly(gallery_id):
                return False
            points = _clean_points(points)
            if points:
                gallery["maps"][str(map_id)] = dict(points=points)
            else:
                gallery["maps"].pop(str(map_id), None)
            self.revision += 1
            return self._save()

    def export(self, gallery_id) -> dict:
        with self._lock:
            gallery = self._find(gallery_id)
            if gallery is None:
                raise KeyError(gallery_id)
            return dict(schema=GALLERY_SCHEMA, **json.loads(json.dumps(gallery)))

    def import_gallery(self, payload) -> str:
        if not isinstance(payload, dict) or payload.get("schema") != GALLERY_SCHEMA:
            raise ValueError("不是星图图库文件")
        gallery = _clean_gallery(dict(payload, id=uuid.uuid4().hex[:12]))
        if gallery is None:
            raise ValueError("图库内容无效")
        with self._lock:
            self._galleries.append(gallery)
            self.revision += 1
            self._save()
            return gallery["id"]


def profile_id(gallery_id) -> str:
    return PROFILE_PREFIX + str(gallery_id)


def gallery_of(profile) -> str | None:
    profile = str(profile or "")
    return profile[len(PROFILE_PREFIX):] if profile.startswith(PROFILE_PREFIX) and len(profile) > len(PROFILE_PREFIX) else None
