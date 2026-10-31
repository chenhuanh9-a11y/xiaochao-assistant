"""Nightmare route plaza: public browsing, verified downloads, data-only handoff.

Android cells belong to legacy template art. Conversion requires the installed
library's explicit template-to-native registration, never the display matrix.
Only the gallery owner's worker may convert and persist a downloaded batch.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import io
import json
import math
import re
import stat
from urllib import error, request
from urllib.parse import urlencode, urlsplit
import zipfile

import numpy as np

from account_service import AccountError, USER_AGENT, call, quoted
from custom_maps import DEFAULT_COLOR, MAX_POINTS, RouteError

CONTENT_KIND = "nightmare-routes"
PACKAGE_SCHEMA = 3
BATCH_SCHEMA = "xingtu-plaza-download-v1"
MANIFEST_NAME = "map-gallery.json"
GRID_STEP = 2
FLOORS = {"minus-1": "B1", "-1": "B1", "1": "1", "2": "2"}
TEMPLATE = re.compile(r"nightmare-layout-(0[1-9]|[12]\d)\Z")
COLOR = re.compile(r"#[0-9A-Fa-f]{6}\Z")
MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
MAX_MANIFEST_BYTES = 6 * 1024 * 1024
MAX_MAPS = 32
TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class PlazaRoute:
    version_id: str
    work_id: str
    revision: int
    title: str
    description: str
    author: str
    map_count: int
    download_count: int
    updated_at: str

    @property
    def updated_date(self):
        return self.updated_at[:10]


def _text(value, limit):
    return str(value).strip()[:limit] if type(value) in (str, int, float) else ""


def _count(value):
    return value if type(value) is int and value >= 0 else 0


def parse_summary(row):
    if not isinstance(row, dict) or row.get("content_kind") != CONTENT_KIND or row.get("status") != "approved":
        return None
    version_id, work_id = _text(row.get("id"), 128), _text(row.get("work_id"), 128)
    modes = row.get("modes", ["nightmare"])
    if not version_id or not work_id or not isinstance(modes, list) or "nightmare" not in modes:
        return None
    return PlazaRoute(version_id, work_id, _count(row.get("revision")),
        _text(row.get("title"), 80) or "未命名路线", _text(row.get("description"), 500),
        _text(row.get("owner_username"), 40) or "匿名", _count(row.get("nightmare_map_count")),
        _count(row.get("download_count")), _text(row.get("updated_at"), 40))


def list_routes(query="", *, api=None):
    params = dict(mode="nightmare", content_kind=CONTENT_KIND, limit=50)
    if str(query or "").strip():
        params["query"] = str(query).strip()[:80]
    rows = (api or call)("GET", "/v1/community/map-galleries?" + urlencode(params))
    if not isinstance(rows, list) or len(rows) > 50:
        raise AccountError("路线广场返回的内容无法识别")
    routes, seen = [], set()
    for row in rows:
        route = parse_summary(row)
        if route is not None and route.version_id not in seen:
            routes.append(route)
            seen.add(route.version_id)
    return routes


def _download_url_allowed(url):
    if not isinstance(url, str) or not url or any(ord(char) < 33 for char in url) or "\\" in url:
        return False
    try:
        parts = urlsplit(url)
        return (parts.scheme == "https" and bool(parts.hostname)
            and parts.hostname.endswith(".aliyuncs.com") and parts.port in (None, 443)
            and parts.username is None and parts.password is None and not parts.fragment)
    except ValueError:
        return False


class _ObjectRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Validate before urllib opens another origin, not after receiving it.
        if not _download_url_allowed(newurl):
            raise AccountError("下载地址被转到了其他站点")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_archive(url, expected_bytes, expected_sha256, *, opener=None):
    """A fresh object-store request carries no API cookies or authorization."""
    if not _download_url_allowed(url):
        raise AccountError("下载地址不受信任")
    if type(expected_bytes) is not int or not 0 < expected_bytes <= MAX_ARCHIVE_BYTES:
        raise AccountError("路线包大小超出上限")
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise AccountError("下载授权缺少校验信息")
    req = request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        transport = opener or request.build_opener(_ObjectRedirect())
        with transport.open(req, timeout=TIMEOUT_SECONDS) as response:
            if not _download_url_allowed(response.geturl()):
                raise AccountError("下载地址被转到了其他站点")
            data = response.read(expected_bytes + 1)
    except error.HTTPError as failure:
        raise AccountError("下载链接已失效，请重试" if failure.code in (403, 404)
                           else f"下载失败（{failure.code}）") from None
    except (error.URLError, TimeoutError, OSError):
        raise AccountError("网络连接失败，请检查网络后重试") from None
    if len(data) != expected_bytes or hashlib.sha256(data).hexdigest() != expected_sha256:
        raise AccountError("路线包校验失败，请重试")
    return data


def _invalid_constant(_value):
    raise ValueError("non-finite JSON number")


def read_manifest(archive, package, version):
    if len(archive) > MAX_ARCHIVE_BYTES:
        raise AccountError("路线包大小超出上限")
    expected_size = package.get("manifest_bytes")
    if type(expected_size) is not int or not 0 < expected_size <= MAX_MANIFEST_BYTES:
        raise AccountError("路线包内容大小不符")
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            members = bundle.infolist()
            if len(members) != 1 or members[0].filename != MANIFEST_NAME:
                raise AccountError("路线包内容不符合格式")
            member = members[0]
            if (member.is_dir() or stat.S_ISLNK(member.external_attr >> 16) or member.flag_bits & 1
                    or member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                raise AccountError("路线包内容不符合格式")
            if member.file_size != expected_size:
                raise AccountError("路线包内容大小不符")
            with bundle.open(member) as handle:
                raw = handle.read(expected_size + 1)
            if len(raw) != expected_size:
                raise AccountError("路线包内容大小不符")
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError):
        raise AccountError("路线包已损坏") from None
    try:
        manifest = json.loads(raw.decode("utf-8"), parse_constant=_invalid_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise AccountError("路线包内容无法识别") from None
    expected = dict(schemaVersion=PACKAGE_SCHEMA, contentKind=CONTENT_KIND, mode="nightmare",
        packageId=package["package_id"], revision=package["revision"], workId=version["work_id"])
    if (not isinstance(manifest, dict) or any(manifest.get(key) != value for key, value in expected.items())
            or type(manifest.get("schemaVersion")) is not int or type(manifest.get("revision")) is not int):
        raise AccountError("路线包与广场记录不一致")
    if not isinstance(manifest.get("maps"), list) or not 0 < len(manifest["maps"]) <= MAX_MAPS:
        raise AccountError("路线包地图数量异常")
    if manifest.get("imageCount", 0) != 0:
        raise AccountError("路线包不得包含图片")
    return manifest


def download(account, route, *, fetcher=None):
    grant = account.authorized("POST",
        f"/v1/community/map-galleries/{quoted(route.version_id)}/packages/nightmare/download", dict(confirmed=True))
    package = grant.get("package") if isinstance(grant, dict) else None
    if (not isinstance(package, dict) or grant.get("content_kind") != CONTENT_KIND
            or grant.get("version_id") != route.version_id or grant.get("work_id") != route.work_id
            or package.get("mode") != "nightmare" or package.get("revision") != route.revision
            or type(package.get("revision")) is not int or not _text(package.get("package_id"), 128)
            or package.get("image_count") != 0 or type(package.get("image_count")) is not int
            or not 0 < _count(package.get("map_count")) <= MAX_MAPS):
        raise AccountError("下载授权与所选路线不一致")
    sha = package.get("sha256")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise AccountError("下载授权缺少校验信息")
    archive = (fetcher or fetch_archive)(grant.get("download_url"), _count(package.get("object_bytes")), sha)
    manifest = read_manifest(archive, package, grant)
    if len(manifest["maps"]) != package["map_count"]:
        raise AccountError("路线包地图数量与授权不一致")
    return manifest, grant


def prepare_download(account, route):
    """Return verified data only; no resource, settings or gallery file writes."""
    manifest, _grant = download(account, route)
    return dict(schema=BATCH_SCHEMA, name=_text(manifest.get("title"), 24) or route.title[:24],
        manifest=manifest, source=dict(workId=route.work_id, versionId=route.version_id,
        revision=manifest["revision"], author=route.author, title=route.title))


def cell_to_image(cell, width, height):
    if type(width) is not int or type(height) is not int or not 0 < width <= 32768 or not 0 < height <= 32768:
        raise RouteError("安卓模板尺寸无效")
    cols = math.ceil(width / GRID_STEP)
    if type(cell) is not int or not 0 <= cell < cols * math.ceil(height / GRID_STEP):
        raise RouteError("路线点超出地图范围")
    return min(width - 1.0, cell % cols * GRID_STEP + 1), min(height - 1.0, cell // cols * GRID_STEP + 1)


def _registration(asset):
    size, matrix = getattr(asset, "template_size", None), getattr(asset, "template_to_native", None)
    if size is None or matrix is None:
        raise RouteError("地图资源缺少安卓模板坐标注册，请更新地图资源后重试")
    matrix = np.asarray(matrix, dtype=np.float64)
    if (len(size) != 2 or matrix.shape != (3, 3) or not np.isfinite(matrix).all()
            or abs(np.linalg.det(matrix)) < 1e-12):
        raise RouteError("安卓模板坐标注册无效")
    cell_to_image(0, *size)
    return size, matrix


def convert_plan(library, map_id, plan):
    graph = library.graph(map_id)
    line_color = plan.get("lineColor")
    if not isinstance(line_color, str) or not COLOR.fullmatch(line_color):
        line_color = DEFAULT_COLOR
    raw_points = plan.get("points") if isinstance(plan.get("points"), list) else []
    points, dropped, assets = [], 0, {}
    pending_start = False
    for raw in raw_points[:MAX_POINTS]:
        # If the first target of a new leg cannot be imported, do not silently
        # connect that leg's next surviving target to the previous route.
        if isinstance(raw, dict) and raw.get("start") is True:
            pending_start = True
        floor = FLOORS.get(str(raw.get("floor"))) if isinstance(raw, dict) else None
        cell = raw.get("cell") if isinstance(raw, dict) else None
        if floor is None or floor not in graph.floors or type(cell) is not int or raw.get("kind") not in ("room", "via"):
            dropped += 1
            continue
        if floor not in assets:
            asset = library.floor(map_id, floor)
            assets[floor] = (asset, *_registration(asset))
        asset, size, matrix = assets[floor]
        try:
            x, y = cell_to_image(cell, *size)
        except RouteError:
            dropped += 1
            continue
        nx, ny, nw = matrix @ np.array([x, y, 1.0])
        if not np.isfinite([nx, ny, nw]).all() or abs(nw) < 1e-12:
            dropped += 1
            continue
        nx, ny = float(nx / nw), float(ny / nw)
        if not (0 <= nx < asset.native_shape[1] and 0 <= ny < asset.native_shape[0]):
            dropped += 1
            continue
        if raw["kind"] == "via":
            node = graph.floors[floor].nearest(nx, ny)
            if node is None:
                dropped += 1
                continue
            px, py = graph.floors[floor].points[node]
            picked = dict(floor=floor, x=float(px), y=float(py), kind="via", room=None)
        else:
            picked = graph.pick(floor, nx, ny)
            if picked is None:
                dropped += 1
                continue
        color = raw.get("color")
        picked["color"] = (color if isinstance(color, str) and COLOR.fullmatch(color) else line_color).upper()
        if pending_start and points:
            picked["start"] = True
        if points and not picked.get("start") and all(points[-1].get(k) == picked.get(k) for k in ("floor", "x", "y", "kind")):
            continue
        points.append(picked)
        pending_start = False
    return points, dropped + max(0, len(raw_points) - MAX_POINTS)


def convert_manifest(library, manifest):
    if not callable(getattr(library, "templates", None)):
        raise RouteError("地图资源缺少安卓模板对应关系，请更新地图资源后重试")
    templates = {}
    for map_id, template in library.templates().items():
        if template in templates:
            raise RouteError("地图资源的安卓模板对应关系重复")
        templates[template] = map_id
    maps, seen = {}, set()
    report = dict(maps=0, missingMaps=0, droppedPoints=0)
    for entry in manifest.get("maps") or ():
        plan = entry.get("routePlan") if isinstance(entry, dict) else None
        template = plan.get("templateId") if isinstance(plan, dict) else None
        map_id = templates.get(template) if isinstance(template, str) and TEMPLATE.fullmatch(template) else None
        if map_id is None:
            report["missingMaps"] += 1
            continue
        if map_id in seen:
            raise RouteError("路线包包含重复地图")
        seen.add(map_id)
        points, dropped = convert_plan(library, map_id, plan)
        report["droppedPoints"] += dropped
        if points:
            maps[map_id] = dict(points=points)
            report["maps"] += 1
    return maps, report


def prepare_gallery(library, batch):
    """Call on the gallery owner's worker, immediately before its guarded write."""
    if not isinstance(batch, dict) or batch.get("schema") != BATCH_SCHEMA:
        raise RouteError("下载批次格式不兼容")
    if not library.available:
        raise RouteError(library.error or "自定义地图资源未安装")
    maps, report = convert_manifest(library, batch["manifest"])
    if not maps:
        raise RouteError("这套路线里没有本软件能显示的地图")
    return dict(name=batch["name"], maps=maps, source=deepcopy(batch["source"]), report=report)
