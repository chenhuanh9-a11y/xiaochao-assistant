"""Structure-package-backed route library for the Qt custom route editor & Route Plaza.

When a user installs a program-only build and imports a structure package for
any season/mode (S0, S1, or future S2/S3+), this library exposes that package's
maps and floors directly to CustomGalleryService, CustomGalleryDisplay, and
RoutePlazaService so users can draw walkable, wall-avoiding custom routes,
display them in live gameplay, and upload/download them via Route Plaza.
"""
from __future__ import annotations

import hashlib
import heapq
import math
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
from PIL import Image, ImageDraw

from custom_maps import DEFAULT_COLOR, RouteError, draw_route, marker_groups
from map_display_names import format_map_display_name
from route_scopes import RouteScope


_GRID_STEP = 4
_SNAP_RADIUS_PX = 28.0
_NEIGHBORS_8 = (
    (-1, 0, 1.0), (1, 0, 1.0), (0, -1, 1.0), (0, 1, 1.0),
    (-1, -1, 1.41421356), (1, -1, 1.41421356), (-1, 1, 1.41421356), (1, 1, 1.41421356),
)


def _floor_label(floor_id: str) -> str:
    fid = str(floor_id)
    if fid == "B1":
        return "负一楼"
    if fid.startswith("-") and fid[1:].isdigit():
        return f"B{fid[1:]}"
    if fid.isdigit():
        return {"1": "一楼", "2": "二楼", "3": "三楼"}.get(fid, f"{fid}楼")
    return fid


def _simplify_polyline(points: list[tuple[float, float]], eps: float = 1.5) -> list[tuple[float, float]]:
    """Ramer-Douglas-Peucker simplification to keep corridor paths crisp."""
    if len(points) <= 2:
        return points
    x1, y1 = points[0]
    x2, y2 = points[-1]
    dx, dy = x2 - x1, y2 - y1
    denom = math.hypot(dx, dy)
    max_dist = -1.0
    split = 0
    for idx in range(1, len(points) - 1):
        px, py = points[idx]
        if denom < 1e-6:
            dist = math.hypot(px - x1, py - y1)
        else:
            dist = abs(dy * px - dx * py + x2 * y1 - y2 * x1) / denom
        if dist > max_dist:
            max_dist = dist
            split = idx
    if max_dist > eps:
        left = _simplify_polyline(points[: split + 1], eps)
        right = _simplify_polyline(points[split:], eps)
        return left[:-1] + right
    return [points[0], points[-1]]


class _WalkableFloor:
    """Lazy 8-connected walkable grid built from a floor's binary clean_mask."""

    def __init__(self, image_size: tuple[int, int], mask_path: Path | None):
        self.image_size = image_size
        self._mask_path = mask_path
        self._grid: np.ndarray | None = None
        self._penalty: np.ndarray | None = None
        self._step = _GRID_STEP

    def _ensure_grid(self):
        if self._grid is not None:
            return
        w, h = self.image_size
        gw = max(1, (w + self._step - 1) // self._step)
        gh = max(1, (h + self._step - 1) // self._step)
        mask = None
        if self._mask_path is not None and self._mask_path.is_file():
            raw = cv2.imread(str(self._mask_path), cv2.IMREAD_GRAYSCALE)
            if raw is not None:
                mask = (raw > 16).astype(np.uint8)
        if mask is None:
            self._grid = np.ones((gh, gw), dtype=np.uint8)
            self._penalty = np.zeros((gh, gw), dtype=np.float32)
            return
        small = cv2.resize(mask, (gw, gh), interpolation=cv2.INTER_AREA)
        walkable = (small > 0).astype(np.uint8)
        dist = cv2.distanceTransform(walkable, cv2.DIST_L2, 3)
        # Prefer corridor centers: cells right against walls incur a soft penalty.
        penalty = np.where(walkable > 0, 2.2 / (dist + 0.6), 0.0).astype(np.float32)
        self._grid = walkable
        self._penalty = penalty

    def snap(self, x: float, y: float) -> tuple[float, float] | None:
        self._ensure_grid()
        w, h = self.image_size
        cx = max(0.0, min(float(w - 1), float(x)))
        cy = max(0.0, min(float(h - 1), float(y)))
        grid = self._grid
        gh, gw = grid.shape
        gx = min(gw - 1, max(0, int(round(cx / self._step))))
        gy = min(gh - 1, max(0, int(round(cy / self._step))))
        if grid[gy, gx]:
            return cx, cy
        r = max(1, int(math.ceil(_SNAP_RADIUS_PX / self._step)))
        y0, y1 = max(0, gy - r), min(gh, gy + r + 1)
        x0, x1 = max(0, gx - r), min(gw, gx + r + 1)
        sub = grid[y0:y1, x0:x1]
        ys, xs = np.nonzero(sub)
        if len(ys) == 0:
            return None
        dy = (ys + y0) - gy
        dx = (xs + x0) - gx
        best = int(np.argmin(dx * dx + dy * dy))
        ny, nx = int(ys[best] + y0), int(xs[best] + x0)
        return (
            max(0.0, min(float(w - 1), nx * self._step + self._step * 0.5)),
            max(0.0, min(float(h - 1), ny * self._step + self._step * 0.5)),
        )

    def shortest_path(self, start: tuple[float, float], goal: tuple[float, float]) -> list[tuple[float, float]]:
        self._ensure_grid()
        grid = self._grid
        penalty = self._penalty
        gh, gw = grid.shape
        sx = min(gw - 1, max(0, int(round(start[0] / self._step))))
        sy = min(gh - 1, max(0, int(round(start[1] / self._step))))
        tx = min(gw - 1, max(0, int(round(goal[0] / self._step))))
        ty = min(gh - 1, max(0, int(round(goal[1] / self._step))))
        if not grid[sy, sx] or not grid[ty, tx]:
            return [start, goal]
        if (sx, sy) == (tx, ty):
            return [start, goal]

        start_node = (sx, sy)
        goal_node = (tx, ty)
        open_heap: list[tuple[float, float, int, int]] = [(math.hypot(tx - sx, ty - sy), 0.0, sx, sy)]
        g_score: dict[tuple[int, int], float] = {start_node: 0.0}
        came_from: dict[tuple[int, int], tuple[int, int]] = {}
        closed: set[tuple[int, int]] = set()
        max_expansions = min(60000, gw * gh)
        expansions = 0

        while open_heap and expansions < max_expansions:
            _f, g, cx, cy = heapq.heappop(open_heap)
            node = (cx, cy)
            if node in closed:
                continue
            if node == goal_node:
                raw_pts = [goal]
                cur = node
                while cur in came_from:
                    cur = came_from[cur]
                    if cur != start_node:
                        raw_pts.append((cur[0] * self._step + self._step * 0.5, cur[1] * self._step + self._step * 0.5))
                raw_pts.append(start)
                raw_pts.reverse()
                return _simplify_polyline(raw_pts, eps=1.8)
            closed.add(node)
            expansions += 1
            for dx, dy, step_cost in _NEIGHBORS_8:
                nx, ny = cx + dx, cy + dy
                if nx < 0 or ny < 0 or nx >= gw or ny >= gh:
                    continue
                if not grid[ny, nx]:
                    continue
                # Prevent diagonal corner-cutting through walls.
                if dx != 0 and dy != 0 and (not grid[cy, nx] or not grid[ny, cx]):
                    continue
                nxt = (nx, ny)
                if nxt in closed:
                    continue
                ng = g + step_cost + float(penalty[ny, nx])
                if ng < g_score.get(nxt, math.inf):
                    g_score[nxt] = ng
                    came_from[nxt] = node
                    h = math.hypot(tx - nx, ty - ny)
                    heapq.heappush(open_heap, (ng + h, ng, nx, ny))
        return [start, goal]


class _ImportedStructureGraph:
    """Walkable-mask A* route planner on a structure package's native canvas."""

    def __init__(self, library: ImportedStructureRouteLibrary, map_id: str):
        self.library = library
        self.map_id = map_id
        self.floor_order = library.map_floors(map_id)
        self.floors: dict[str, _WalkableFloor] = {}
        for fid in self.floor_order:
            row = library._maps[map_id]["floors"][fid]
            self.floors[fid] = _WalkableFloor(row["size"], row.get("mask_cache"))
        self.template_id = map_id
        self.navigation_digest = hashlib.sha256(
            f"imported-structure:{library.version}:{map_id}:{','.join(self.floor_order)}".encode("utf-8")
        ).hexdigest()

    def pick(self, floor: str, x: float, y: float) -> dict:
        fid = str(floor)
        wf = self.floors.get(fid)
        if wf is None:
            raise RouteError("地图楼层不属于当前结构包")
        snapped = wf.snap(x, y)
        if snapped is None:
            raise RouteError("请点击地图内可通行区域")
        cx, cy = snapped
        return dict(floor=fid, x=round(cx, 2), y=round(cy, 2), kind="via", room=None)

    def plan(self, points):
        segments = []
        prev = None
        for idx, pt in enumerate(points or ()):
            floor = str(pt.get("floor", ""))
            if floor not in self.floors:
                prev = None
                continue
            pos = (float(pt["x"]), float(pt["y"]))
            color = str(pt.get("color") or DEFAULT_COLOR)
            is_start = bool(pt.get("start", pt.get("newStart", False)))
            if prev is not None and not is_start and prev[0] == floor:
                path_pts = self.floors[floor].shortest_path(prev[1], pos)
                segments.append(dict(floor=floor, color=color, points=path_pts, end=idx))
            prev = (floor, pos)
        return segments

    @staticmethod
    def floor_lines(segments, floor):
        fid = str(floor)
        return [(list(seg["points"]), seg["color"]) for seg in segments if str(seg["floor"]) == fid]

    def document(self, points):
        scope = self.library.scope
        cleaned = []
        for pt in points or ():
            floor = str(pt.get("floor", ""))
            if floor not in self.floors:
                continue
            cleaned.append(dict(
                floor=floor,
                x=round(float(pt["x"]), 2),
                y=round(float(pt["y"]), 2),
                color=str(pt.get("color") or DEFAULT_COLOR),
                start=bool(pt.get("start", pt.get("newStart", False))),
            ))
        if not cleaned:
            raise RouteError("当前地图还没有已保存的路线点")
        return dict(
            schemaVersion=scope.schema_version,
            templateId=self.template_id,
            scenario=scope.scenario,
            difficulty=scope.mode,
            sourceMapId=self.map_id,
            navigationSignature=self.navigation_digest,
            points=cleaned,
        )


class ImportedStructureRouteLibrary:
    """Route editor & display library backed by an active imported structure package."""

    def __init__(self, resolver, scope: RouteScope, cache_root: Path):
        self.resolver = resolver
        self.scope = scope
        self.cache_root = Path(cache_root)
        self.root = self.cache_root
        self.floor_ids = tuple(scope.floors)
        self.floor_labels = dict(zip(scope.floors, scope.floor_labels))
        self.max_points = 512 if scope.id == "classic-hard" else 128
        self.version = ""
        self.error = ""
        self._maps = {}
        self._graphs = {}
        self._load_from_resolver()

    def manifest_digest(self) -> str:
        return hashlib.sha256(f"imported-structure:{self.version}".encode("utf-8")).hexdigest()

    def _load_from_resolver(self):
        if self.resolver is None:
            self.error = f"请先在「地图资源」导入「{self.scope.label}」结构包"
            return
        try:
            snapshot = self.resolver.snapshot()
            resources = snapshot.resolve_context(self.scope.season, self.scope.runtime_mode)
        except Exception as error:
            self.error = f"结构包读取失败：{error}"
            return
        if resources.base is None:
            self.error = f"请先在「地图资源」导入「{self.scope.label}」结构包"
            return
        try:
            from imported_original_base import require_original_base
            from imported_resource_packages import PackageView
            from shared_structure_reference import load_shared_structure

            comp_ref = resources.base.component_ref
            base_view = PackageView(self.resolver.root, comp_ref, assert_current=resources.assert_current)
            with base_view.read_scope():
                reader, context = require_original_base(
                    base_view, season=self.scope.season, mode=self.scope.runtime_mode
                )
                interfaces = context["runtimeInterfaces"]
                pack_doc = reader.json(interfaces["identityPack"])
                self.version = f"{comp_ref.package_id}:{comp_ref.resource_version}:{comp_ref.manifest_sha256[:12]}"

                neutral_layers = {}
                neutral_rel = interfaces.get("neutralDisplay")
                if neutral_rel:
                    try:
                        ndoc = reader.json(neutral_rel)
                        for row in ndoc.get("layers", ()):
                            key = (str(row.get("mapId", "")), str(row.get("floor", "")))
                            rel = row.get("resourcePath") or row.get("displayPath") or row.get("path")
                            if key[0] and key[1] and rel:
                                neutral_layers[key] = rel
                    except (OSError, ValueError, KeyError):
                        neutral_layers = {}

                typed_entries = {}
                typed_rel = interfaces.get("typedIndex")
                if typed_rel:
                    try:
                        tdoc = reader.json(typed_rel)
                        for row in tdoc.get("entries", ()):
                            key = (str(row.get("mapId", "")), str(row.get("floor", "")))
                            if key[0] and key[1] and row.get("resourcePath"):
                                typed_entries[key] = row
                    except (OSError, ValueError, KeyError):
                        typed_entries = {}

                raw_floors = tuple(map(str, pack_doc.get("floors", ()))) or self.floor_ids
                self.floor_ids = raw_floors
                self.floor_labels = {fl: self.floor_labels.get(fl, _floor_label(fl)) for fl in raw_floors}
                version_dir = self.cache_root / f"{self.scope.season}_{self.scope.runtime_mode}_{comp_ref.manifest_sha256[:12]}"
                version_dir.mkdir(parents=True, exist_ok=True)
                self.root = version_dir

                for entry in pack_doc.get("maps", ()):
                    mid = str(entry.get("id", ""))
                    if not mid:
                        continue
                    name = format_map_display_name(
                        mid,
                        str(entry.get("name") or mid),
                        season=self.scope.season,
                        mode=self.scope.runtime_mode,
                    )
                    layers = entry.get("layers") or {}
                    floor_candidates = list(raw_floors)
                    for fl in layers:
                        if str(fl) not in floor_candidates:
                            floor_candidates.append(str(fl))
                    floor_entries = {}
                    for fid in floor_candidates:
                        display_rel = neutral_layers.get((mid, fid))
                        typed_row = typed_entries.get((mid, fid))
                        mask_rel = layers.get(fid)
                        if not display_rel and not typed_row and not mask_rel:
                            continue
                        img_path, size, mask_cache = self._materialize_display(
                            base_view,
                            reader,
                            load_shared_structure,
                            version_dir,
                            mid,
                            fid,
                            display_rel=display_rel,
                            typed_row=typed_row,
                            mask_rel=mask_rel,
                        )
                        if img_path is not None:
                            floor_entries[fid] = dict(
                                path=img_path,
                                size=size,
                                mask_rel=mask_rel,
                                mask_cache=mask_cache,
                                display_rel=display_rel,
                            )
                    if floor_entries:
                        self._maps[mid] = dict(name=name, floors=floor_entries)
            if not self._maps:
                self.error = f"「{self.scope.label}」结构包中没有可绘制的地图图层"
        except Exception as error:
            self._maps = {}
            self.error = f"结构包自定义画线初始化失败：{error}"

    @staticmethod
    def _materialize_display(
        base_view,
        reader,
        load_shared_structure,
        target_dir: Path,
        mid: str,
        fid: str,
        *,
        display_rel,
        typed_row,
        mask_rel,
    ):
        safe_mid = mid.replace("/", "_").replace("\\", "_")
        safe_fid = fid.replace("/", "_").replace("\\", "_")
        out_path = target_dir / f"{safe_mid}__{safe_fid}.png"
        mask_cache = target_dir / f"{safe_mid}__{safe_fid}__mask.png"

        # Always extract binary walkable mask when available so A* pathfinding works
        # even when displaying a bundled rendered PNG!
        binary = None
        if not mask_cache.is_file():
            if typed_row and typed_row.get("resourcePath"):
                try:
                    ref = load_shared_structure(
                        base_view.path(typed_row["resourcePath"]),
                        resource_root=base_view.root,
                        expected_binding=dict(
                            profile=typed_row["profile"],
                            mapId=mid,
                            floor=fid,
                            variant=typed_row.get("variant", "variant-01"),
                        ),
                        read_resource=reader,
                    )
                    if ref is not None and ref.clean_mask is not None:
                        binary = (ref.clean_mask > 0).astype(np.uint8)
                except (OSError, ValueError, KeyError):
                    binary = None
            if binary is None and mask_rel:
                try:
                    raw = reader(base_view.path(mask_rel))
                    mask = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED)
                    if mask is not None:
                        if mask.ndim == 3:
                            alpha = mask[:, :, 3] if mask.shape[2] == 4 else cv2.cvtColor(mask, cv2.COLOR_BGR2GRAY)
                        else:
                            alpha = mask
                        binary = (alpha > 16).astype(np.uint8)
                except (OSError, ValueError, KeyError):
                    binary = None
            if binary is not None:
                cv2.imwrite(str(mask_cache), binary * 255)

        if out_path.is_file():
            with Image.open(out_path) as img:
                return out_path, img.size, (mask_cache if mask_cache.is_file() else None)

        if display_rel:
            try:
                raw = reader(base_view.path(display_rel))
                arr = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED)
                if arr is not None and arr.ndim == 3 and arr.shape[2] in (3, 4):
                    rgba = cv2.cvtColor(arr, cv2.COLOR_BGR2RGBA if arr.shape[2] == 3 else cv2.COLOR_BGRA2RGBA)
                    Image.fromarray(rgba, mode="RGBA").save(out_path)
                    return out_path, (int(arr.shape[1]), int(arr.shape[0])), (mask_cache if mask_cache.is_file() else None)
            except (OSError, ValueError, KeyError):
                pass

        if binary is None and mask_cache.is_file():
            cached = cv2.imread(str(mask_cache), cv2.IMREAD_GRAYSCALE)
            if cached is not None:
                binary = (cached > 16).astype(np.uint8)
        if binary is None:
            return None, (0, 0), None

        h, w = binary.shape[:2]
        edge = cv2.morphologyEx(binary, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0
        canvas = np.zeros((h, w, 4), dtype=np.uint8)
        canvas[binary > 0] = (58, 66, 82, 235)
        canvas[edge] = (176, 194, 218, 255)
        Image.fromarray(canvas, mode="RGBA").save(out_path)
        return out_path, (int(w), int(h)), (mask_cache if mask_cache.is_file() else None)

    @property
    def available(self):
        return bool(self._maps)

    def maps(self):
        return [(mid, row["name"]) for mid, row in self._maps.items()]

    def has(self, mid):
        return str(mid) in self._maps

    def name(self, mid):
        return self._maps.get(str(mid), {}).get("name", "")

    def templates(self):
        return {mid: mid for mid in self._maps}

    def map_floors(self, mid):
        entry = self._maps.get(str(mid))
        if not entry:
            return ()
        return tuple(fl for fl in self.floor_ids if fl in entry["floors"]) or tuple(entry["floors"].keys())

    def floor(self, mid, floor):
        entry = self._maps.get(str(mid))
        fid = str(floor)
        if not entry or fid not in entry["floors"]:
            raise RouteError("地图楼层不属于当前结构包")
        row = entry["floors"][fid]
        return SimpleNamespace(
            image_path=row["path"],
            image_size=row["size"],
            to_native=np.eye(3),
            to_image=np.eye(3),
            metadata=dict(id=fid, width=row["size"][0], height=row["size"][1]),
        )

    def image(self, mid, floor):
        with Image.open(self.floor(mid, floor).image_path) as img:
            return img.convert("RGBA")

    def graph(self, mid):
        key = str(mid)
        if key not in self._maps:
            raise RouteError("当前结构包中找不到该地图")
        if key not in self._graphs:
            self._graphs[key] = _ImportedStructureGraph(self, key)
        return self._graphs[key]

    def validate_reference(self, mid, floor, clean_mask):
        if not self.has(mid) or clean_mask is None:
            raise RouteError("自定义地图与当前结构包不匹配")

    def render_layer(self, mid, floor, reference, points, *, point_numbers=False):
        self.validate_reference(mid, floor, reference.clean_mask)
        canvas = self.image(mid, floor)
        graph = self.graph(mid)
        segments = graph.plan(points)
        markers = marker_groups(points, str(floor), per_floor=True) if point_numbers else []
        draw_route(
            ImageDraw.Draw(canvas), lambda p: p, graph.floor_lines(segments, str(floor)), markers,
            width=6, pill=20, marker_offset=48 if point_numbers else 0, marker_bounds=canvas.size,
        )
        height, width = reference.shape[:2]
        rgba = np.asarray(canvas)
        if rgba.shape[1] != width or rgba.shape[0] != height:
            rgba = cv2.resize(rgba, (width, height), interpolation=cv2.INTER_LINEAR)
        pixels = np.ascontiguousarray(rgba[:, :, [2, 1, 0, 3]])
        pixels[pixels[:, :, 3] == 0] = 0
        return pixels

    def package_maps(self, gallery, cancelled=lambda: False):
        maps = []
        for mid, row in (gallery.get("maps") or {}).items():
            if cancelled():
                raise RouteError("已停止投稿")
            if not row.get("points") or not self.has(mid):
                continue
            plan = self.graph(mid).document(row["points"])
            maps.append(dict(
                mapEntryId=mid,
                displayName=self.name(mid),
                defaultFloor=plan["points"][0]["floor"],
                floors=[],
                routePlan=plan,
            ))
        if not 1 <= len(maps) <= self.scope.max_maps:
            raise RouteError("请选择含有已保存路线的图库")
        return maps

    @staticmethod
    def _normalize_imported_floor(raw_floor: str) -> str:
        val = str(raw_floor or "").strip()
        low = val.lower()
        if low == "minus-1":
            return "B1"
        if low.startswith("b") and low[1:].isdigit():
            return f"-{low[1:]}"
        return val

    def _decode_imported_point(
        self,
        mid: str,
        pt: dict,
        *,
        template_id: str = "",
        default_color: str,
        grid_step: float,
    ) -> dict:
        if not isinstance(pt, dict):
            raise RouteError("路线点格式无效")
        fid = self._normalize_imported_floor(pt.get("floor", ""))
        entry = self._maps.get(mid, {})
        floor_info = (entry.get("floors") or {}).get(fid)
        if floor_info is None:
            raise RouteError(f"路线点楼层 {fid or '未知'} 不属于当前结构包")
        w, h = floor_info["size"]
        if "x" in pt and "y" in pt:
            x, y = float(pt["x"]), float(pt["y"])
        elif "cell" in pt:
            from android_route_grid_index import lookup_android_grid
            cell = pt["cell"]
            grid = lookup_android_grid(template_id, mid, fid)
            if grid is not None:
                cols, step, nw, nh = grid
                scale_x = float(w) / float(max(1, nw))
                scale_y = float(h) / float(max(1, nh))
            else:
                step = max(1.0, float(grid_step or (2.0 if self.scope.id == "legacy-nightmare" else 4.0)))
                nw, nh = w, h
                cols = max(1, int(math.ceil(nw / step)))
                scale_x, scale_y = 1.0, 1.0
            if isinstance(cell, (list, tuple)) and len(cell) == 2:
                ax = min(float(nw - 1), float(cell[0]) * step + step * 0.5)
                ay = min(float(nh - 1), float(cell[1]) * step + step * 0.5)
            elif isinstance(cell, int) and cell >= 0:
                ax = min(float(nw - 1), (cell % cols) * step + step * 0.5)
                ay = min(float(nh - 1), (cell // cols) * step + step * 0.5)
            else:
                raise RouteError("路线点网格坐标格式不支持")
            x = min(float(w - 1), max(0.0, ax * scale_x))
            y = min(float(h - 1), max(0.0, ay * scale_y))
        else:
            raise RouteError("路线点缺少坐标信息")
        return dict(
            floor=fid,
            x=round(x, 2),
            y=round(y, 2),
            color=str(pt.get("color") or default_color or DEFAULT_COLOR),
            start=bool(pt.get("start", pt.get("newStart", False))),
        )

    def import_maps(self, manifest):
        if (manifest.get("routeCatalogId"), manifest.get("scenario"), manifest.get("mode")) != (
                self.scope.id, self.scope.scenario, self.scope.mode):
            raise RouteError("路线包不属于当前场景或难度")
        maps = {}
        missing_maps = 0
        dropped_points = 0
        for entry in manifest.get("maps") or ():
            plan = entry.get("routePlan")
            if not isinstance(plan, dict):
                raise RouteError("路线包缺少路线规划数据")
            template_id = str(plan.get("templateId") or "")
            mid = str(plan.get("sourceMapId") or entry.get("mapEntryId") or template_id or "")
            if not self.has(mid):
                missing_maps += 1
                continue
            if mid in maps:
                raise RouteError("路线包地图重复")
            default_color = str(plan.get("lineColor") or DEFAULT_COLOR)
            default_step = 2.0 if self.scope.id == "legacy-nightmare" else 4.0
            grid_step = float(plan.get("gridStep") or plan.get("step") or default_step)
            pts = []
            for pt in plan.get("points") or ():
                try:
                    pts.append(
                        self._decode_imported_point(
                            mid,
                            pt,
                            template_id=template_id,
                            default_color=default_color,
                            grid_step=grid_step,
                        )
                    )
                except (RouteError, TypeError, ValueError, KeyError):
                    dropped_points += 1
            if pts:
                maps[mid] = dict(points=pts)
        if not maps:
            raise RouteError("路线包中没有有效路线")
        return maps, dict(maps=len(maps), missingMaps=missing_maps, droppedPoints=dropped_points)
