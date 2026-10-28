"""Route plaza: nightmare route galleries shared through the Android app's service.

Listing is public (``GET /v1/community/map-galleries`` with
``content_kind=nightmare-routes``); a download needs a signed-in account and
returns a short-lived link to a ZIP whose only member is ``map-gallery.json``
(package schema 3). Each map's ``routePlan`` stores targets as cells of the
Android editor's navigation grid on the same legacy template art this app's
custom map package uses, so a cell converts to native pixels with the
package's per-floor image matrix:

    image x = min(width - 1, (cell % cols) * 2 + 1),  cols = ceil(width / 2)

Only the ordered targets (room/via, colour, new-start) are kept; legs are
replanned on this app's road data, exactly like targets placed by hand.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import json
import math
import re
from urllib import error, request
from urllib.parse import urlencode, urlsplit
import zipfile

import numpy as np

from account_service import AccountError, AccountService, USER_AGENT, api_root, call, quoted
from custom_maps import DEFAULT_COLOR, MAX_POINTS, CustomMapLibrary, RouteError

CONTENT_KIND = "nightmare-routes"
PACKAGE_SCHEMA = 3
MANIFEST_NAME = "map-gallery.json"
GRID_STEP = 2  # Every Android navigation grid (all 72 floors) uses 2 image pixels per cell.
FLOORS = {"minus-1": "B1", "-1": "B1", "1": "1", "2": "2"}
TEMPLATE = re.compile(r"^nightmare-layout-(0[1-9]|1\d|2[0-7])$")
COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
MAX_MANIFEST_BYTES = 6 * 1024 * 1024
DOWNLOAD_HOST_SUFFIX = ".aliyuncs.com"
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
    def updated_date(self) -> str:
        return self.updated_at[:10]


def _text(value, limit) -> str:
    return str(value).strip()[:limit] if isinstance(value, (str, int, float)) and not isinstance(value, bool) else ""


def _count(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def parse_summary(row) -> PlazaRoute | None:
    if not isinstance(row, dict) or row.get("content_kind") != CONTENT_KIND or row.get("status") != "approved":
        return None
    version_id, work_id = _text(row.get("id"), 64), _text(row.get("work_id"), 64)
    if not version_id or not work_id or "nightmare" not in (row.get("modes") or ["nightmare"]):
        return None
    return PlazaRoute(version_id=version_id, work_id=work_id, revision=_count(row.get("revision")),
                      title=_text(row.get("title"), 80) or "未命名路线", description=_text(row.get("description"), 500),
                      author=_text(row.get("owner_username"), 40) or "匿名",
                      map_count=_count(row.get("nightmare_map_count")), download_count=_count(row.get("download_count")),
                      updated_at=_text(row.get("updated_at"), 40))


def list_routes(query: str = "", *, api=call) -> list[PlazaRoute]:
    params = dict(mode="nightmare", content_kind=CONTENT_KIND, limit=50)
    if str(query or "").strip():
        params["query"] = str(query).strip()[:80]
    rows = api("GET", "/v1/community/map-galleries?" + urlencode(params))
    if not isinstance(rows, list):
        raise AccountError("路线广场返回的内容无法识别")
    return [route for route in map(parse_summary, rows) if route is not None]


# Download -------------------------------------------------------------------
def _download_url_allowed(url: str) -> bool:
    parts = urlsplit(str(url or ""))
    root = urlsplit(api_root())
    if root.scheme == "http":  # Local test server: only its own origin.
        return parts.scheme == "http" and parts.netloc == root.netloc
    return parts.scheme == "https" and bool(parts.hostname) and parts.hostname.endswith(DOWNLOAD_HOST_SUFFIX)


def fetch_archive(url: str, expected_bytes: int, expected_sha256: str) -> bytes:
    """The signed object, checked against the server's package record. No token is sent."""
    if not _download_url_allowed(url):
        raise AccountError("下载地址不受信任")
    if not 0 < expected_bytes <= MAX_ARCHIVE_BYTES:
        raise AccountError("路线包大小超出上限")
    req = request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
            if not _download_url_allowed(response.geturl()):
                raise AccountError("下载地址被转到了其他站点")
            data = response.read(expected_bytes + 1)
    except error.HTTPError as failure:
        raise AccountError("下载链接已失效，请重试" if failure.code in (403, 404) else
                           f"下载失败（{failure.code}）") from None
    except (error.URLError, TimeoutError, OSError):
        raise AccountError("网络连接失败，请检查网络后重试") from None
    if len(data) != expected_bytes or hashlib.sha256(data).hexdigest() != expected_sha256:
        raise AccountError("路线包校验失败，请重试")
    return data


def read_manifest(archive: bytes, package: dict, version: dict) -> dict:
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            members = bundle.infolist()
            if [member.filename for member in members] != [MANIFEST_NAME]:
                raise AccountError("路线包内容不符合格式")
            size = members[0].file_size
            if size > MAX_MANIFEST_BYTES or size != package.get("manifest_bytes", size):
                raise AccountError("路线包内容大小不符")
            raw = bundle.read(MANIFEST_NAME)
    except zipfile.BadZipFile:
        raise AccountError("路线包已损坏") from None
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise AccountError("路线包内容无法识别") from None
    expected = dict(schemaVersion=PACKAGE_SCHEMA, contentKind=CONTENT_KIND, mode="nightmare",
                    packageId=package.get("package_id"), revision=package.get("revision"), workId=version.get("work_id"))
    if not isinstance(manifest, dict) or any(manifest.get(key) != value for key, value in expected.items()):
        raise AccountError("路线包与广场记录不一致")
    if not isinstance(manifest.get("maps"), list) or len(manifest["maps"]) > 27:
        raise AccountError("路线包地图数量异常")
    return manifest


def download(account: AccountService, route: PlazaRoute) -> tuple[dict, dict]:
    """Returns (manifest, server version record)."""
    grant = account.authorized("POST", f"/v1/community/map-galleries/{quoted(route.version_id)}/packages/nightmare/download",
                               dict(confirmed=True))
    package = grant.get("package") if isinstance(grant, dict) else None
    if (not isinstance(package, dict) or grant.get("content_kind") != CONTENT_KIND
            or grant.get("version_id") != route.version_id or grant.get("work_id") != route.work_id):
        raise AccountError("下载授权与所选路线不一致")
    sha = package.get("sha256")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise AccountError("下载授权缺少校验信息")
    archive = fetch_archive(grant.get("download_url"), _count(package.get("object_bytes")), sha)
    return read_manifest(archive, package, grant), grant


# Conversion -----------------------------------------------------------------
def cell_to_image(cell: int, width: int, height: int) -> tuple[float, float]:
    cols = math.ceil(width / GRID_STEP)
    if not 0 <= cell < cols * math.ceil(height / GRID_STEP):
        raise RouteError("路线点超出地图范围")
    col, row = cell % cols, cell // cols
    return (min(width - 1.0, col * GRID_STEP + GRID_STEP / 2), min(height - 1.0, row * GRID_STEP + GRID_STEP / 2))


def convert_plan(library: CustomMapLibrary, map_id: str, plan: dict) -> tuple[list[dict], int]:
    """Android targets -> this app's points (native pixels). Returns (points, dropped)."""
    graph = library.graph(map_id)
    line_color = plan.get("lineColor") if isinstance(plan.get("lineColor"), str) and COLOR.match(plan["lineColor"]) else None
    points, dropped = [], 0
    raw_points = plan.get("points") if isinstance(plan.get("points"), list) else []
    for raw in raw_points[:MAX_POINTS]:
        floor = FLOORS.get(str(raw.get("floor"))) if isinstance(raw, dict) else None
        cell = raw.get("cell") if isinstance(raw, dict) else None
        if floor is None or not isinstance(cell, int) or isinstance(cell, bool):
            dropped += 1
            continue
        try:
            asset = library.floor(map_id, floor)
            x, y = cell_to_image(cell, *asset.image_size)
        except (KeyError, RouteError):
            dropped += 1
            continue
        nx, ny, nw = asset.to_native @ np.array([x, y, 1.0])
        picked = graph.pick(floor, nx / nw, ny / nw)
        if picked is None:
            dropped += 1
            continue
        if raw.get("kind") == "via":  # A via point stays on its road tile, even inside a room.
            node = graph.floors[floor].nearest(nx / nw, ny / nw)
            px, py = graph.floors[floor].points[node]
            picked = dict(floor=floor, x=float(px), y=float(py), kind="via", room=None)
        color = raw.get("color") if isinstance(raw.get("color"), str) and COLOR.match(raw["color"]) else line_color
        picked["color"] = (color or DEFAULT_COLOR).upper()
        if raw.get("start") is True and points:
            picked["start"] = True
        if points and all(points[-1].get(key) == picked.get(key) for key in ("floor", "x", "y", "kind")):
            continue  # Two cells of one room collapse to the same target.
        points.append(picked)
    dropped += max(0, len(raw_points) - MAX_POINTS)
    return points, dropped


def convert_manifest(library: CustomMapLibrary, manifest: dict) -> tuple[dict, dict]:
    """Galleries maps {map id: {points}} plus a report of what did not carry over."""
    templates = {template: map_id for map_id, template in library.templates().items()}
    maps, report = {}, dict(maps=0, missingMaps=0, droppedPoints=0)
    for entry in manifest.get("maps") or ():
        plan = entry.get("routePlan") if isinstance(entry, dict) else None
        template = plan.get("templateId") if isinstance(plan, dict) else None
        map_id = templates.get(template) if isinstance(template, str) and TEMPLATE.match(template) else None
        if map_id is None:
            report["missingMaps"] += 1
            continue
        points, dropped = convert_plan(library, map_id, plan)
        report["droppedPoints"] += dropped
        if points:
            maps[map_id] = dict(points=points)
            report["maps"] += 1
    return maps, report


def install(account: AccountService, library: CustomMapLibrary, store, route: PlazaRoute) -> tuple[str, dict]:
    """Download, convert and save as a read-only gallery. Returns (gallery id, report)."""
    if not library.available:
        raise AccountError(library.error or "自定义地图资源未安装")
    manifest, grant = download(account, route)
    maps, report = convert_manifest(library, manifest)
    if not maps:
        raise AccountError("这套路线里没有本软件能显示的地图")
    source = dict(workId=route.work_id, versionId=route.version_id, revision=_count(manifest.get("revision")),
                  author=route.author, title=route.title)
    name = _text(manifest.get("title"), 24) or route.title[:24]
    return store.install_plaza(name, maps, source), report
