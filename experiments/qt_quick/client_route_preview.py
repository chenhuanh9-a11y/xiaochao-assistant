"""Read and render one plaza preview frame on the route worker."""

import base64
import io
import json
import threading
from collections import OrderedDict

from PIL import Image, ImageDraw
from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickImageProvider

from account_service import AccountError
from custom_maps import CustomMapLibrary, FLOOR_LABELS, FLOORS, RouteError, draw_route, marker_groups
from route_plaza import fetch_public_cover, prepare_install
from scoped_route_library import ScopedRouteLibrary


def _preview_library(scope_id, root, *, galleries=None):
    if galleries is not None and hasattr(galleries, "_build_library_for_scope"):
        library = galleries._build_library_for_scope(scope_id, root)
    else:
        library = CustomMapLibrary(root) if scope_id == "legacy-nightmare" else ScopedRouteLibrary(root, scope_id)
    if not library.available:
        raise AccountError(library.error or "缺少对应的路线编辑资源，无法绘制地图预览。")
    return library


def load_preview(account, route, root, saved_maps, cancelled, *, galleries=None):
    library = _preview_library(route.scope.id, root, galleries=galleries)
    if saved_maps is None:
        _, saved_maps, _, report = prepare_install(account, library, route, cancelled=cancelled)
    else:
        report = {}
    return _preview_document(library, saved_maps, report)


def _preview_document(library, saved_maps, report):
    maps = [dict(id=mid, name=name) for mid, name in library.maps() if mid in saved_maps]
    if not maps:
        raise AccountError("本机路线编辑资源中没有可预览的地图。")
    return library, saved_maps, maps, report


def _render_frame(document, map_id, floor, point_numbers, max_edge):
    library, routes, maps, report = document
    map_id = map_id or maps[0]["id"]
    points = routes[map_id]["points"]
    graph = library.graph(map_id)
    floors = [value for value in getattr(library, "floor_ids", FLOORS) if value in graph.floors]
    if floor not in floors:
        floor = next((point["floor"] for point in points if point["floor"] in floors), floors[0])
    labels = getattr(library, "floor_labels", FLOOR_LABELS)
    asset = library.floor(map_id, floor)
    image = library.image(map_id, floor).convert("RGBA")
    image.thumbnail((max_edge, max_edge))
    x_scale, y_scale = (new / old for new, old in zip(image.size, asset.image_size))
    inverse = asset.to_image

    def transform(point):
        x, y, w = inverse @ [point[0], point[1], 1]
        return x / w * x_scale, y / w * y_scale

    error = ""
    try:
        lines = graph.floor_lines(graph.plan(points), floor)
    except RouteError as failure:
        lines, error = [], str(failure)
    markers = marker_groups(points, floor, per_floor=True) if point_numbers or not lines else []
    draw_route(ImageDraw.Draw(image), transform, lines, markers, width=4, pill=10,
               marker_offset=15, marker_bounds=image.size)
    lost = report.get("missingMaps", 0) + report.get("versionMismatchMaps", 0)
    notice = (f"{lost} 张地图与本机编辑资源不匹配。" if lost else "")
    frame = dict(maps=maps, map=map_id, floors=[dict(id=f, name=labels[f]) for f in floors], floor=floor,
                 numbers=point_numbers,
                 notice=error or ("此楼层没有路线。" if not lines and not markers else notice))
    return image, frame, bool(lines)


def preview_frame(document, map_id, floor, point_numbers):
    image, frame, _has_lines = _render_frame(document, map_id, floor, point_numbers, 1050)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", compress_level=1)
    frame["image"] = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    return frame


def render_public_cover(library, saved_maps, report):
    document = _preview_document(library, saved_maps, report)
    for entry in document[2]:
        map_id = entry["id"]
        graph = library.graph(map_id)
        for floor in getattr(library, "floor_ids", FLOORS):
            if floor not in graph.floors:
                continue
            image, _frame, has_lines = _render_frame(document, map_id, floor, False, 320)
            if has_lines:
                buffer = io.BytesIO()
                image.save(buffer, format="WEBP", quality=85, method=6)
                return buffer.getvalue(), map_id, floor
    raise AccountError("没有可作为封面的有效路线线段")


class PlazaThumbnailImages(QQuickImageProvider):
    """Read public covers and exact local previews on Qt's image worker."""

    def __init__(self, galleries):
        super().__init__(QQuickImageProvider.Image, QQuickImageProvider.ForceAsynchronousImageLoading)
        self._galleries = galleries
        self._lock = threading.Lock()
        self._published = {}
        self._cache = OrderedDict()

    def publish(self, requests):
        published, urls = {}, {}
        for key, identity in requests.items():
            token = base64.urlsafe_b64encode(json.dumps(identity, separators=(",", ":")).encode()).decode().rstrip("=")
            published[token] = identity
            urls[key] = f"image://plazaroute/{token}"
        with self._lock:
            self._published = published
        return urls

    def requestImage(self, identifier, size, requestedSize):
        image = self._image(identifier)
        if image.isNull():
            image = QImage(1, 1, QImage.Format_RGBA8888)
            image.fill(0)
        if size is not None:
            size.setWidth(image.width())
            size.setHeight(image.height())
        return image

    def _image(self, identifier):
        with self._lock:
            identity = self._published.get(identifier)
            cached = self._cache.get(identifier)
            if cached is not None:
                self._cache.move_to_end(identifier)
        if identity is None:
            return QImage()
        scope_id, work_id, version_id, generation, cover_url = identity
        if not cover_url and not self._galleries.plazaPreviewCurrent(generation):
            return QImage()
        if cached is not None:
            return cached
        try:
            if cover_url:
                data = fetch_public_cover(cover_url)
                if data is None:
                    return QImage()
                with Image.open(io.BytesIO(data), formats=["WEBP"]) as cover:
                    if not all(1 < side <= 320 for side in cover.size) or cover.is_animated:
                        return QImage()
                    pixels = cover.convert("RGBA")
            else:
                root, context, saved = self._galleries.plazaPreviewInput(scope_id, work_id, version_id)
                if saved is None or generation != context:
                    return QImage()
                document = _preview_document(_preview_library(scope_id, root, galleries=self._galleries), saved, {})
                pixels, _frame, _has_lines = _render_frame(document, "", "", False, 240)
            image = QImage(pixels.tobytes(), pixels.width, pixels.height, pixels.width * 4, QImage.Format_RGBA8888).copy()
        except (AccountError, Image.DecompressionBombError, OSError, ValueError, KeyError, IndexError, RuntimeError, TypeError):
            return QImage()
        if not cover_url and not self._galleries.plazaPreviewCurrent(generation):
            return QImage()
        with self._lock:
            if self._published.get(identifier) != identity:
                return QImage()
            self._cache[identifier] = image
            if len(self._cache) > 16:
                self._cache.popitem(last=False)
        return image
