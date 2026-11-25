"""Structure-package-backed route library for the Qt custom route editor & Route Plaza.

When a user installs a program-only build and imports a structure package for
any season/mode (S0, S1, or future S2/S3+), this library exposes that package's
maps and floors directly to CustomGalleryService, CustomGalleryDisplay, and
RoutePlazaService so users can draw custom routes, display them in live
gameplay, and upload/download them via Route Plaza.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
from PIL import Image, ImageDraw

from custom_maps import DEFAULT_COLOR, RouteError, draw_route, marker_groups
from map_display_names import format_map_display_name
from route_scopes import RouteScope, SCOPES


def _floor_label(floor_id: str) -> str:
    fid = str(floor_id)
    if fid == "B1":
        return "负一楼"
    if fid.startswith("-") and fid[1:].isdigit():
        return f"B{fid[1:]}"
    if fid.isdigit():
        return {"1": "一楼", "2": "二楼", "3": "三楼"}.get(fid, f"{fid}楼")
    return fid


class _ImportedStructureGraph:
    """Lightweight free-draw polyline planner on a structure package's native canvas."""

    def __init__(self, library: ImportedStructureRouteLibrary, map_id: str):
        self.library = library
        self.map_id = map_id
        self.floors = library.map_floors(map_id)
        self.template_id = map_id
        self.navigation_digest = hashlib.sha256(
            f"imported-structure:{library.version}:{map_id}:{','.join(self.floors)}".encode("utf-8")
        ).hexdigest()

    def pick(self, floor: str, x: float, y: float):
        fid = str(floor)
        if fid not in self.floors:
            raise RouteError("地图楼层不属于当前结构包")
        asset = self.library.floor(self.map_id, fid)
        w, h = asset.image_size
        cx = max(0.0, min(float(w - 1), float(x)))
        cy = max(0.0, min(float(h - 1), float(y)))
        return fid, cx, cy

    def plan(self, points):
        segments = []
        prev = None
        for pt in points or ():
            floor = str(pt.get("floor", ""))
            if floor not in self.floors:
                prev = None
                continue
            pos = (float(pt["x"]), float(pt["y"]))
            color = str(pt.get("color") or DEFAULT_COLOR)
            if prev is not None and not pt.get("newStart") and prev[0] == floor:
                segments.append(dict(floor=floor, color=color, points=[prev[1], pos]))
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
                newStart=bool(pt.get("newStart", False)),
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
        self._maps = {}  # map_id -> {name, floors: {floor_id: {path, size, mask_path}}}
        self._graphs = {}
        self._load_from_resolver()

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

                # Optional bundled rendered display maps (neutralDisplay).
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

                # Typed shared-structure (.npz) entries for clean_mask fallback.
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
                        img_path, size = self._materialize_display(
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
        if out_path.is_file():
            with Image.open(out_path) as img:
                return out_path, img.size
        if display_rel:
            try:
                raw = reader(base_view.path(display_rel))
                arr = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED)
                if arr is not None and arr.ndim == 3 and arr.shape[2] in (3, 4):
                    if arr.shape[2] == 3:
                        rgba = cv2.cvtColor(arr, cv2.COLOR_BGR2RGBA)
                    else:
                        rgba = cv2.cvtColor(arr, cv2.COLOR_BGRA2RGBA)
                    Image.fromarray(rgba, mode="RGBA").save(out_path)
                    return out_path, (int(arr.shape[1]), int(arr.shape[0]))
            except (OSError, ValueError, KeyError):
                pass
        binary = None
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
        if binary is None:
            return None, (0, 0)
        h, w = binary.shape[:2]
        # Render clean readable dark-slate room interior with crisp borders (never harsh pure white).
        edge = cv2.morphologyEx(binary, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0
        canvas = np.zeros((h, w, 4), dtype=np.uint8)
        canvas[binary > 0] = (58, 66, 82, 235)
        canvas[edge] = (176, 194, 218, 255)
        Image.fromarray(canvas, mode="RGBA").save(out_path)
        return out_path, (int(w), int(h))

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

    def import_maps(self, manifest):
        if (manifest.get("routeCatalogId"), manifest.get("scenario"), manifest.get("mode")) != (
                self.scope.id, self.scope.scenario, self.scope.mode):
            raise RouteError("路线包不属于当前场景或难度")
        maps = {}
        for entry in manifest.get("maps") or ():
            plan = entry.get("routePlan")
            if not isinstance(plan, dict):
                raise RouteError("路线包缺少路线规划数据")
            mid = str(plan.get("sourceMapId") or entry.get("mapEntryId") or plan.get("templateId") or "")
            if not self.has(mid):
                raise RouteError("路线包包含当前结构包中不存在的地图")
            if mid in maps:
                raise RouteError("路线包地图重复")
            pts = []
            for pt in plan.get("points") or ():
                pts.append(dict(
                    floor=str(pt["floor"]),
                    x=float(pt["x"]),
                    y=float(pt["y"]),
                    color=str(pt.get("color") or DEFAULT_COLOR),
                    newStart=bool(pt.get("newStart", False)),
                ))
            if pts:
                maps[mid] = dict(points=pts)
        if not maps:
            raise RouteError("路线包中没有有效路线")
        return maps
