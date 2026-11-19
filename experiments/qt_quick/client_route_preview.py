"""Read and render one plaza preview frame on the route worker."""

import base64
import io

from PIL import ImageDraw

from account_service import AccountError
from custom_maps import CustomMapLibrary, FLOOR_LABELS, FLOORS, RouteError, draw_route, marker_groups
from route_plaza import prepare_install
from scoped_route_library import ScopedRouteLibrary


def load_preview(account, route, root, saved_maps, cancelled):
    library = (CustomMapLibrary(root) if route.scope.id == "legacy-nightmare"
               else ScopedRouteLibrary(root, route.scope.id))
    if not library.available:
        raise AccountError(library.error or "缺少对应的路线编辑资源，无法绘制地图预览。")
    if saved_maps is None:
        _, saved_maps, _, report = prepare_install(account, library, route, cancelled=cancelled)
    else:
        report = {}
    maps = [dict(id=mid, name=name) for mid, name in library.maps() if mid in saved_maps]
    if not maps:
        raise AccountError("本机路线编辑资源中没有可预览的地图。")
    return library, saved_maps, maps, report


def preview_frame(document, map_id, floor, point_numbers):
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
    image.thumbnail((1050, 1050))
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
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", compress_level=1)
    lost = report.get("missingMaps", 0) + report.get("versionMismatchMaps", 0)
    notice = (f"{lost} 张地图与本机编辑资源不匹配。" if lost else "")
    return dict(maps=maps, map=map_id, floors=[dict(id=f, name=labels[f]) for f in floors], floor=floor,
                image="data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii"),
                numbers=point_numbers,
                notice=error or ("此楼层没有路线。" if not lines and not markers else notice))
