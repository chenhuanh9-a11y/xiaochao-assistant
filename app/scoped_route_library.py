"""Checked Android route resources, presented through the existing Qt editor.

The Android planner executes in a short-lived QJSEngine on the calling worker.
No JavaScript value or engine crosses threads. Saved coordinates remain in the
declared artwork plane, and packages retain the original Android grid cells.
"""
from copy import deepcopy
from collections import OrderedDict
import hashlib
import json
import math
from pathlib import Path
import stat
import sys
import threading
from types import SimpleNamespace

import numpy as np
from PIL import Image

from custom_maps import DEFAULT_COLOR, RouteError
from route_scopes import SCOPES

MODEL_FILE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "android_route_model.js"
JS_FLOORS = {"B1": "minus-1", "1": "1", "2": "2", "-1": "b1", "-2": "b2", "-3": "b3"}
UI_FLOORS = {v: k for k, v in JS_FLOORS.items()}


class ScopedRouteLibrary:
    def __init__(self, root, scope_id):
        self.root = Path(root)
        self.scope = SCOPES[scope_id]
        self.floor_ids = self.scope.floors
        self.floor_labels = dict(zip(self.scope.floors, self.scope.floor_labels))
        self.max_points = 512 if scope_id == "classic-hard" else 128
        self.version = ""
        self.error = ""
        self._templates, self._files, self._graphs = {}, {}, OrderedDict()
        self._graph_lock = threading.RLock()
        self._display_registrations = {}
        self._manifest_digest = None
        try:
            manifest_bytes = (self.root / "manifest.json").read_bytes()
            self._manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
            manifest = json.loads(manifest_bytes)
            if manifest.get("schema") != "xingtu-android-route-resources-v1":
                raise ValueError("路线资源格式无效")
            self._files = manifest["files"]
            self.version = manifest["sourceCommit"]
            if hashlib.sha256(MODEL_FILE.read_bytes()).hexdigest() != manifest.get("routeModelSha256"):
                raise ValueError("路线规划模型与地图资料版本不一致")
            artwork_name = "pcport/readable-artwork.json"
            if artwork_name in self._files:
                artwork = json.loads(self.checked(artwork_name).read_text("utf-8"))
                for record in artwork.get("classic", []):
                    if record.get("difficulty") == "hard":
                        for floor in record["floors"]:
                            self._display_registrations[(record["mapId"], str(floor["gameFloor"]))] = floor
            catalog = json.loads(self.checked(self.scope.catalog).read_text("utf-8"))
            for row in catalog["templates"]:
                if row.get("difficulty", self.scope.mode) != self.scope.mode:
                    continue
                if row.get("scenario", self.scope.scenario) != self.scope.scenario:
                    raise ValueError("路线资源场景不一致")
                mid = row.get("mapId") or row["id"]
                if mid in self._templates:
                    raise ValueError("路线地图重复")
                self._templates[mid] = row
            if not 1 <= len(self._templates) <= self.scope.max_maps:
                raise ValueError("路线地图数量不符合当前难度")
        except (OSError, ValueError, KeyError, TypeError) as error:
            self._templates = {}
            self.error = "自定义路线资源未就绪：" + str(error)

    @property
    def available(self):
        return bool(self._templates)

    def checked(self, relative):
        if (not isinstance(relative, str) or "\\" in relative or Path(relative).is_absolute()
                or ".." in Path(relative).parts or relative not in self._files):
            raise RouteError("路线资源路径无效")
        if (self._manifest_digest is not None and hashlib.sha256((self.root / "manifest.json").read_bytes()).hexdigest()
                != self._manifest_digest):
            raise RouteError("路线资源已更新，请刷新图库")
        path = self.root / relative
        for parent in (path, *path.parents):
            info = parent.lstat()
            if (stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0)
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
                raise RouteError("路线资源不能使用链接")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != self._files[relative]:
            raise RouteError("路线资源校验失败：" + relative)
        return path

    def maps(self):
        return [(mid, row["name"]) for mid, row in self._templates.items()]

    def has(self, mid):
        return mid in self._templates

    def name(self, mid):
        return self._templates.get(mid, {}).get("name", "")

    def templates(self):
        return {mid: row["id"] for mid, row in self._templates.items()}

    def floor(self, mid, floor):
        row = next((f for f in self._templates[mid]["floors"] if f["id"] == JS_FLOORS.get(floor)), None)
        if row is None:
            raise RouteError("地图楼层不属于当前场景")
        image = self.checked(self.scope.asset_prefix + row["image"])
        return SimpleNamespace(image_path=image, image_size=(row["width"], row["height"]),
                               to_native=np.eye(3), to_image=np.eye(3), metadata=row)

    def image(self, mid, floor):
        with Image.open(self.floor(mid, floor).image_path) as image:
            return image.convert("RGBA")

    def display_registration(self, mid, floor):
        asset = self.floor(mid, floor)
        if self.scope.season == "s1":
            registration = asset.metadata.get("displayRegistration", {})
            matrix = registration.get("imageToNative")
            expected = registration.get("layerMaskSha256")
        else:
            registration = self._display_registrations.get((mid, floor), {})
            if (registration.get("displayAsset") != asset.metadata["image"]
                    or registration.get("imageSize") != list(asset.image_size)):
                raise RouteError("自定义地图缺少当前底图的显示绑定")
            matrix = registration.get("matrixImageToNative")
            expected = registration.get("layerMaskSha256")
        if (not isinstance(matrix, list) or len(matrix) != 6 or not isinstance(expected, str)
                or len(expected) != 64):
            raise RouteError("自定义地图缺少结构显示绑定")
        matrix = np.array([matrix[:3], matrix[3:], [0, 0, 1]], dtype=float)
        if not np.isfinite(matrix).all() or abs(np.linalg.det(matrix)) < 1e-9:
            raise RouteError("自定义地图显示矩阵无效")
        return matrix, expected

    def validate_reference(self, mid, floor, clean_mask):
        from custom_maps import mask_sha256
        self.graph(mid)
        _matrix, expected = self.display_registration(mid, floor)
        if mask_sha256(clean_mask) != expected:
            raise RouteError("自定义地图与当前游戏结构版本不一致")

    def render_layer(self, mid, floor, reference, points, *, point_numbers=False):
        import cv2
        from PIL import ImageDraw
        from custom_maps import draw_route, marker_groups
        self.validate_reference(mid, floor, reference.clean_mask)
        matrix, _expected = self.display_registration(mid, floor)
        canvas = self.image(mid, floor)
        graph = self.graph(mid)
        segments = graph.plan(points)
        markers = marker_groups(points, floor, per_floor=True) if point_numbers else []
        draw_route(ImageDraw.Draw(canvas), lambda p: p, graph.floor_lines(segments, floor), markers,
                   width=6, pill=20, marker_offset=48 if point_numbers else 0, marker_bounds=canvas.size)
        height, width = reference.shape
        rgba = cv2.warpAffine(np.asarray(canvas), matrix[:2], (width, height), flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
        pixels = np.ascontiguousarray(rgba[:, :, [2, 1, 0, 3]])
        pixels[pixels[:, :, 3] == 0] = 0
        return pixels

    def graph(self, mid):
        with self._graph_lock:
            self.checked(self.scope.catalog)
            for row in self._templates[mid]["floors"]:
                self.checked(self.scope.asset_prefix + row["navigation"])
            if mid not in self._graphs:
                self._graphs[mid] = ScopedRouteGraph(self, mid)
            self._graphs.move_to_end(mid)
            graph = self._graphs[mid]
            while len(self._graphs) > 2:
                self._graphs.popitem(last=False)
            return graph

    def package_maps(self, gallery, cancelled=lambda: False):
        maps = []
        for mid, row in gallery.get("maps", {}).items():
            if cancelled():
                raise RouteError("已停止投稿")
            if not row.get("points"):
                continue
            plan = self.graph(mid).document(row["points"])
            maps.append(dict(mapEntryId=mid, displayName=self.name(mid),
                             defaultFloor=plan["points"][0]["floor"], floors=[], routePlan=plan))
        if not 1 <= len(maps) <= self.scope.max_maps:
            raise RouteError("请选择含有已保存路线的图库")
        return maps

    def import_maps(self, manifest):
        if (manifest.get("routeCatalogId"), manifest.get("scenario"), manifest.get("mode")) != (
                self.scope.id, self.scope.scenario, self.scope.mode):
            raise RouteError("路线包不属于当前场景或难度")
        bindings = {row["id"]: mid for mid, row in self._templates.items()}
        maps = {}
        for entry in manifest["maps"]:
            plan = entry.get("routePlan")
            if not isinstance(plan, dict) or plan.get("templateId") not in bindings:
                raise RouteError("路线包包含未知地图")
            mid = bindings[plan["templateId"]]
            if mid in maps:
                raise RouteError("路线包地图重复")
            graph = self.graph(mid)
            if (plan.get("scenario"), plan.get("difficulty"), plan.get("sourceMapId")) != (
                    self.scope.scenario, self.scope.mode, mid):
                raise RouteError("路线方案的场景或地图身份不一致")
            if self.scope.id == "classic-hard" and (plan.get("kind") != "planned-route"
                    or plan.get("version") != 2 or plan.get("artworkSignature") != graph.template.get("artworkSignature")):
                raise RouteError("困难路线绘制版本不一致")
            if plan.get("navigationSignature") != graph.template["navigationSignature"]:
                raise RouteError("路线道路版本不一致，请更新地图资源")
            if (self.scope.season == "s1" and plan.get("floorAssociationSignature")
                    != graph.template.get("floorAssociationSignature")):
                raise RouteError("路线跨层资料版本不一致")
            points = graph.decode_points(plan.get("points"), plan.get("lineColor", DEFAULT_COLOR))
            graph.document(points)  # Replan; never trust downloaded segments.
            if points:
                maps[mid] = dict(points=points)
        return maps, dict(maps=len(maps), missingMaps=0, droppedPoints=0)


class _Coordinates:
    def __init__(self, nav):
        self.nav = nav

    def __getitem__(self, cell):
        nav = self.nav
        return (min(nav["width"] - 1, (cell % nav["cols"]) * nav["step"] + nav["step"] / 2),
                min(nav["height"] - 1, (cell // nav["cols"]) * nav["step"] + nav["step"] / 2))


class ScopedRouteGraph:
    def __init__(self, library, mid):
        self.library, self.mid = library, mid
        self.template = deepcopy(library._templates[mid])
        self.raw, self.floors, self._edges = {}, {}, {}
        for row in self.template["floors"]:
            floor = UI_FLOORS[row["id"]]
            raw = json.loads(library.checked(library.scope.asset_prefix + row["navigation"]).read_text("utf-8"))
            if (raw.get("width"), raw.get("height")) != (row["width"], row["height"]):
                raise RouteError("路线导航与底图尺寸不一致")
            self.raw[row["id"]] = raw
            runs = raw["edges"]
            if len(runs) % 2 or any(type(v) is not int or v < 0 for v in runs):
                raise RouteError("路线道路格式无效")
            if sum(runs[1::2]) != raw["cols"] * raw["rows"] or raw["cols"] * raw["rows"] > 1000000:
                raise RouteError("路线道路尺寸无效")
            self._edges[row["id"]] = np.repeat(np.array(runs[::2], dtype=np.uint8), runs[1::2])
            self.floors[floor] = SimpleNamespace(points=_Coordinates(raw))

    def _execute(self, expression, **values):
        # An engine lives and dies inside this worker call. No Qt object is cached.
        from PySide6.QtQml import QJSEngine
        engine = QJSEngine()
        script = MODEL_FILE.read_text("utf-8")
        result = engine.evaluate(script, str(MODEL_FILE))
        if result.isError():
            raise RouteError(result.toString())
        source = "var model=NightmareRouteModel.createForFloors(" + json.dumps(list(self.raw)) + ");"
        source += "var raw=" + json.dumps(self.raw, separators=(",", ":")) + ";var floors={};"
        source += "Object.keys(raw).forEach(function(f){floors[f]=model.prepare(raw[f]);});"
        source += "var template=" + json.dumps(self.template, separators=(",", ":")) + ";"
        for key, value in values.items():
            source += "var " + key + "=" + json.dumps(value, separators=(",", ":"), allow_nan=False) + ";"
        source += "JSON.stringify(" + expression + ")"
        result = engine.evaluate(source, "qt-route-adapter.js")
        if result.isError():
            raise RouteError(result.toString())
        return json.loads(result.toString())

    def encode_points(self, points):
        if len(points) > self.library.max_points:
            raise RouteError("路线点数量超出上限")
        encoded = []
        for point in points:
            floor = JS_FLOORS.get(point.get("floor"))
            nav = self.raw.get(floor)
            x, y = point.get("x"), point.get("y")
            if (nav is None or type(x) not in (int, float) or type(y) not in (int, float)
                    or not math.isfinite(x) or not math.isfinite(y)
                    or not 0 <= x < nav["width"] or not 0 <= y < nav["height"]):
                raise RouteError("路线点越界或楼层无效")
            cell = int(y // nav["step"]) * nav["cols"] + int(x // nav["step"])
            if not self._edges[floor][cell]:
                raise RouteError("目标不在可走道路内")
            if point["kind"] == "room" and not any(
                    str(r["id"]) == str(point.get("room")) and r["cell"] == cell for r in nav["rooms"]):
                raise RouteError("路线房间与目标不一致")
            value = dict(floor=floor, cell=cell, kind=point["kind"], roomId=int(point.get("room") or 0),
                         color=point.get("color") or DEFAULT_COLOR)
            if point.get("start"):
                value["start"] = True
            encoded.append(value)
        return encoded

    def decode_points(self, points, color=DEFAULT_COLOR):
        if not isinstance(points, list) or len(points) > self.library.max_points:
            raise RouteError("路线目标格式或数量无效")
        decoded = []
        for point in points:
            if not isinstance(point, dict):
                raise RouteError("路线目标无效")
            nav = self.raw.get(point.get("floor"))
            cell = point.get("cell")
            if (nav is None or type(cell) is not int or not 0 <= cell < nav["cols"] * nav["rows"]
                    or point.get("kind") not in ("room", "via")):
                raise RouteError("路线目标越界或类型无效")
            x, y = _Coordinates(nav)[cell]
            value = dict(floor=UI_FLOORS[point["floor"]], x=x, y=y, kind=point["kind"],
                         room=str(point.get("roomId")) if point["kind"] == "room" else None,
                         color=point.get("color") or color)
            if point.get("start") is True:
                value["start"] = True
            decoded.append(value)
        return decoded

    def pick(self, floor, x, y):
        fid = JS_FLOORS[floor]
        row = next(f for f in self.template["floors"] if f["id"] == fid)
        point = self._execute("model.selectionAt(floors[fid],fid,entries,x,y,18)",
                              fid=fid, entries=row.get("entrances", []), x=x, y=y)
        return self.decode_points([point])[0] if point else None

    def document(self, points):
        encoded = self.encode_points(points)
        floor = encoded[0]["floor"] if encoded else next(iter(self.raw))
        document = self._execute("model.document(template,points,model.plan(floors,model.transitionsForTemplate(template),points),floors,style)",
                                 points=encoded, style=dict(version=2, lineColor=DEFAULT_COLOR, editorFloor=floor))
        # These fields belong to Android's editor/native envelope, rather than
        # the shared grid algorithm (nightmare-route-editor.js save()).
        document.update(scenario=self.library.scope.scenario, difficulty=self.library.scope.mode, sourceMapId=self.mid)
        if self.library.scope.id == "classic-hard":
            signature = self.template.get("artworkSignature")
            if not isinstance(signature, str) or len(signature) != 64:
                raise RouteError("困难路线缺少绘制版本")
            document.update(kind="planned-route", artworkSignature=signature)
        if len(json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 160 * 1024:
            raise RouteError("路线方案超过 160 KiB，请减少目标")
        steps = 0
        for segment in document["segments"]:
            if not 1 <= len(segment["parts"]) <= 8:
                raise RouteError("路线跨层次数过多")
            for part in segment["parts"]:
                if len(part["runs"]) > 24000 or any(count > 10000 for count in part["runs"][1::2]):
                    raise RouteError("路线过长，请减少目标")
                steps += sum(part["runs"][1::2])
        if steps > 200000:
            raise RouteError("路线超过二十万步，请减少目标")
        return document

    def plan(self, points):
        if not points:
            return []
        route = self._execute("model.plan(floors,model.transitionsForTemplate(template),points)", points=self.encode_points(points))
        return [dict(start=s["from"], end=s["to"], cost=s["cost"], color=s.get("color") or DEFAULT_COLOR,
                     cells=[(UI_FLOORS[p["floor"]], p["cell"]) for p in s["cells"]]) for s in route["segments"]]

    def floor_lines(self, segments, floor):
        from custom_maps import _merge_collinear
        lines = []
        for segment in segments:
            run = []
            for fid, cell in segment["cells"] + [(None, None)]:
                if fid == floor:
                    run.append(self.floors[floor].points[cell])
                else:
                    if len(run) > 1:
                        lines.append((_merge_collinear(run), segment["color"]))
                    run = []
        return lines
