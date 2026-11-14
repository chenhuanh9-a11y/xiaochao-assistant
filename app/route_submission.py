"""Nightmare submissions; protocol from Android CommunityBridge/AccountSyncClient.

No gallery writer, credentials, private art or Android paths live here. The
installed editor resource supplies its hashed Android navigation contract.
Native road edges are never advertised as Android navigation edges.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import heapq
import http.client
import io
import json
import math
import re
from urllib.parse import urlsplit
import uuid
import zipfile

import numpy as np

from account_service import AccountError, quoted
from custom_maps import DEFAULT_COLOR
from route_plaza import CONTENT_KIND, MANIFEST_NAME, MAX_MANIFEST_BYTES, _download_url_allowed

from route_scopes import SCOPES

BASE = "/v1/community/map-galleries"
CONTENT_TYPE = "application/x-xingtu-gallery"
MAX_PACKAGE_BYTES = 64 * 1024 * 1024
STATUSES = dict(draft="草稿", pending="审核中", approved="已上架", rejected="已拒绝",
                withdrawn="已撤回", taken_down="已下架")
EDITABLE = {"draft", "rejected", "approved", "withdrawn", "taken_down"}
FLOORS = {"B1": "minus-1", "1": "1", "2": "2"}
DIRECTIONS = ((1, 1, 0), (2, 0, 1), (4, -1, 0), (8, 0, -1),
              (16, 1, -1), (32, 1, 1), (64, -1, 1), (128, -1, -1))


def check_cancelled(cancelled):
    if cancelled():
        raise AccountError("已停止投稿；已创建的草稿或已提交的审核请到「我的投稿」确认。")


def identity(value):
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise AccountError("服务器返回的投稿身份无效，请刷新「我的投稿」。") from None


def validate_version(row):
    if (not isinstance(row, dict) or row.get("content_kind") != CONTENT_KIND
            or row.get("status") not in STATUSES or type(row.get("revision")) is not int
            or row["revision"] < 1 or not isinstance(row.get("packages"), list)):
        raise AccountError("服务器返回的投稿记录无法识别")
    identity(row.get("id")); identity(row.get("work_id"))
    # null is the normal player-work value, not the official source named "null".
    if row.get("official_source_id"):
        raise AccountError("官方作品不能作为个人路线投稿修改")
    return row


def list_mine(account):
    rows = account.authorized("GET", "/v1/community/my/map-galleries")
    if not isinstance(rows, list):
        raise AccountError("服务器返回的投稿列表无法识别")
    return [validate_version(row) for row in rows if isinstance(row, dict)
            and row.get("content_kind") == CONTENT_KIND and not row.get("official_source_id")]


def metadata(title, description):
    title, description = str(title).strip(), str(description).strip()
    # Intersection of native bridge (2..48), server (1..80) and manifest (1..60).
    if not 2 <= len(title.encode("utf-16-le")) // 2 <= 48:
        raise AccountError("请输入 2–48 个字的作品名称")
    if len(description) > 500:
        raise AccountError("作品说明不能超过 500 字")
    return dict(title=title, description=description, content_kind=CONTENT_KIND)


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise AccountError("投稿导航资源格式不正确")
    return value


class Navigation:
    """A checked resource grid, decoded on the plaza worker only."""
    def __init__(self, raw, size):
        self.cols = _integer(raw.get("cols"), 1, 1000)
        self.rows = _integer(raw.get("rows"), 1, 1000)
        self.width = _integer(raw.get("width"), 1, 4096)
        self.height = _integer(raw.get("height"), 1, 4096)
        units = raw.get("unitsPerPixel", 1.)
        if type(units) not in (int, float) or not math.isfinite(units) or units <= 0:
            raise AccountError("投稿导航距离单位无效")
        self.step_cost = 2 * units
        if (raw.get("version") != 1 or raw.get("step") != 2 or list(size) != [self.width, self.height]
                or self.cols != math.ceil(self.width / 2) or self.rows != math.ceil(self.height / 2)):
            raise AccountError("投稿导航与模板坐标不一致，请更新自定义地图资源")
        self.edges = self._decode(raw.get("edges"), 255, np.uint8)
        self.labels = self._decode(raw.get("labels"), 65535, np.uint16)
        self.rooms = {}
        for room in raw.get("rooms", []):
            rid = _integer(room.get("id"), 1, 65535)
            cell = _integer(room.get("cell"), 0, len(self.edges) - 1)
            if rid in self.rooms or not self.edges[cell] or self.labels[cell] != rid:
                raise AccountError("投稿导航房间与道路不一致")
            self.rooms[rid] = cell

    def _decode(self, runs, maximum, dtype):
        if not isinstance(runs, list) or len(runs) % 2 or len(runs) > 2 * self.cols * self.rows:
            raise AccountError("投稿导航数据错误")
        output = np.zeros(self.cols * self.rows, dtype=dtype)
        offset = 0
        for at in range(0, len(runs), 2):
            value = _integer(runs[at], 0, maximum)
            count = _integer(runs[at + 1], 1, len(output))
            if offset + count > len(output):
                raise AccountError("投稿导航数据越界")
            output[offset:offset + count] = value
            offset += count
        if offset != len(output):
            raise AccountError("投稿导航数据不完整")
        return output

    def neighbours(self, cell):
        x, y = cell % self.cols, cell // self.cols
        for bit, dx, dy in DIRECTIONS:
            if self.edges[cell] & bit and 0 <= x + dx < self.cols and 0 <= y + dy < self.rows:
                nxt = cell + dx + dy * self.cols
                if self.edges[nxt]:
                    yield nxt, self.step_cost * (math.sqrt(2) if dx and dy else 1)

    def pick(self, x, y, kind):
        # At most two template pixels of quantisation error. Never snap across
        # a missing road to a distant room or silently drop a user's point.
        col, row = math.floor(x / 2), math.floor(y / 2)
        choices = []
        for cy in range(max(0, row - 1), min(self.rows, row + 2)):
            for cx in range(max(0, col - 1), min(self.cols, col + 2)):
                cell = cy * self.cols + cx
                if self.edges[cell] and (kind != "room" or int(self.labels[cell]) in self.rooms):
                    choices.append(((2 * cx + 1 - x) ** 2 + (2 * cy + 1 - y) ** 2, cell))
        if not choices or min(choices)[0] > 8:
            raise AccountError("路线点无法对应安卓道路，请调整目标或更新地图资源")
        cell = min(choices)[1]
        rid = int(self.labels[cell]) if kind == "room" else 0
        if kind == "room":
            if rid not in self.rooms:
                raise AccountError("路线房间无法对应安卓房间，请调整目标或更新地图资源")
            cell = self.rooms[rid]
        return cell, rid


def _path(navs, stairs, start, end, cancelled):
    if start == end:
        return [start]
    queue, distances, parents = [(0., start)], {start: 0.}, {}
    visits = 0
    while queue:
        cost, node = heapq.heappop(queue)
        if cost != distances[node]:
            continue
        if node == end:
            path = [node]
            while path[-1] != start:
                path.append(parents[path[-1]])
            return path[::-1]
        visits += 1
        if visits % 256 == 0:
            check_cancelled(cancelled)
        floor, cell = node
        neighbours = [((floor, nxt), distance) for nxt, distance in navs[floor].neighbours(cell)]
        neighbours.extend(stairs.get(node, ()))
        for nxt, distance in neighbours:
            candidate = cost + distance
            if candidate < distances.get(nxt, math.inf):
                distances[nxt], parents[nxt] = candidate, node
                heapq.heappush(queue, (candidate, nxt))
    raise AccountError("安卓道路或已核实楼梯无法连通这些目标，请增加新起点或调整路线")


def convert_route(library, map_id, points, *, cancelled=lambda: False):
    template = library.templates().get(map_id)
    if not template:
        raise AccountError(f"地图 {library.name(map_id)} 缺少投稿模板绑定")
    contract = library.submission_navigation(map_id)
    if contract.get("templateId") != template or not re.fullmatch(r"[a-f0-9]{64}", str(contract.get("navigationSignature", ""))):
        raise AccountError("投稿导航签名或地图绑定不正确")
    navs = {}
    inverse = {}
    for floor, raw in contract.get("floors", {}).items():
        native_floor = next((key for key, value in FLOORS.items() if value == floor), None)
        if native_floor is None:
            raise AccountError("投稿导航楼层不受支持")
        asset = library.floor(map_id, native_floor)
        navs[floor] = Navigation(raw, asset.template_size)
        inverse[floor] = np.linalg.inv(asset.template_to_native)
    stairs = {}
    for stair in contract.get("stairs", []):
        if stair.get("kind") != "stairs" or stair.get("verified") is not True:
            continue
        a, b = ((stair[key]["floor"], stair[key]["cell"]) for key in ("from", "to"))
        for floor, cell in (a, b):
            if floor not in navs or not 0 <= cell < len(navs[floor].edges) or not navs[floor].edges[cell]:
                raise AccountError("投稿楼梯与导航道路不一致")
        cost = stair.get("cost")
        if type(cost) not in (float, int) or not math.isfinite(cost) or cost <= 0:
            raise AccountError("投稿楼梯缺少已核实的距离")
        stairs.setdefault(a, []).append((b, float(cost)))
        if stair.get("bidirectional", True):
            stairs.setdefault(b, []).append((a, float(cost)))
    if not 1 <= len(points) <= 128:
        raise AccountError("每张地图需要 1–128 个路线点")
    converted = []
    for point in points:
        check_cancelled(cancelled)
        floor = FLOORS.get(point.get("floor"))
        if floor not in navs or point.get("kind") not in ("via", "room"):
            raise AccountError("路线点楼层或类型不受支持")
        xy = inverse[floor] @ np.array([point["x"], point["y"], 1.])
        if not np.isfinite(xy).all() or abs(xy[2]) < 1e-9:
            raise AccountError("路线点坐标无效")
        cell, room = navs[floor].pick(xy[0] / xy[2], xy[1] / xy[2], point["kind"])
        color = point.get("color", DEFAULT_COLOR)
        if not isinstance(color, str) or not re.fullmatch(r"#[a-fA-F0-9]{6}", color):
            raise AccountError("路线颜色无效")
        converted.append(dict(floor=floor, cell=cell, kind=point["kind"], roomId=room, color=color.upper(),
                              **({"start": True} if converted and point.get("start") is True else {})))
    segments, steps = [], 0
    for target in range(1, len(converted)):
        if converted[target].get("start"):
            continue
        path = _path(navs, stairs, (converted[target - 1]["floor"], converted[target - 1]["cell"]),
                     (converted[target]["floor"], converted[target]["cell"]), cancelled)
        parts = []
        previous = None
        for floor, cell in path:
            if previous is None or floor != previous[0]:
                parts.append(dict(floor=floor, cell=cell, runs=[]))
            else:
                nav = navs[floor]
                dx, dy = cell % nav.cols - previous[1] % nav.cols, cell // nav.cols - previous[1] // nav.cols
                bit = next(bit for bit, x, y in DIRECTIONS if (x, y) == (dx, dy))
                runs = parts[-1]["runs"]
                if runs and runs[-2] == bit and runs[-1] < 10000:
                    runs[-1] += 1
                else:
                    runs.extend((bit, 1))
                steps += 1
            previous = (floor, cell)
        if steps > 200000 or len(parts) > 8 or any(len(p["runs"]) > 24000 for p in parts):
            raise AccountError("路线过长，请减少目标或分开投稿")
        segments.append(dict(**{"from": target - 1, "to": target}, parts=parts))
    plan = dict(version=2, navigationVersion=1, navigationSignature=contract["navigationSignature"],
                templateId=template, points=converted, segments=segments)
    if len(_json(plan)) > 160 * 1024:
        raise AccountError("路线方案超过 160 KiB")
    return plan


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def prepare_maps(library, gallery, *, cancelled=lambda: False):
    if gallery.get("readonly") or gallery.get("source") or gallery.get("originWorkId") or gallery.get("id") == "default":
        raise AccountError("请选择自己的可编辑图库；下载作品及其副本仅供个人使用")
    if hasattr(library, "package_maps"):
        return library.package_maps(gallery, cancelled)
    maps, seen = [], set()
    for map_id, row in gallery.get("maps", {}).items():
        if not row.get("points"):
            continue
        check_cancelled(cancelled)
        plan = convert_route(library, map_id, row["points"], cancelled=cancelled)
        if plan["templateId"] in seen:
            raise AccountError("投稿地图模板重复绑定，请更新资源")
        seen.add(plan["templateId"])
        maps.append(dict(mapEntryId="route-" + uuid.uuid5(uuid.NAMESPACE_URL, plan["templateId"]).hex,
                         displayName=library.name(map_id)[:48], defaultFloor=plan["points"][0]["floor"],
                         floors=[], routePlan=plan))
    if not 1 <= len(maps) <= 27:
        raise AccountError("投稿需要 1–27 张地图的已保存路线")
    return maps


@dataclass(frozen=True)
class Package:
    archive: bytes
    payload: dict


def build_package(draft, gallery_id, maps, title, author, *, upload_identity=None, scope_id="legacy-nightmare", source_work_id=None):
    scope = SCOPES[scope_id]
    validate_version(draft)
    if draft.get("scenario", "lady-of-misfortune") != scope.scenario:
        raise AccountError("投稿场景与所选图库不一致")
    packages = [p for p in draft["packages"] if p.get("mode") == scope.mode]
    if len(packages) > 1 or any(p.get("mode") != scope.mode for p in draft["packages"]):
        raise AccountError("服务器返回的投稿包模式重复或不一致")
    work = identity(draft["work_id"])
    # Java UUID.nameUUIDFromBytes: MD5 without a namespace prefix.
    seed = f"xingtu-map-gallery:{work}:{scope.mode}:{gallery_id.strip().lower()}"
    package_id = identity(packages[0].get("package_id")) if packages else str(uuid.UUID(bytes=hashlib.md5(seed.encode()).digest(), version=3))
    revision = _integer(packages[0].get("revision"), 1, 2**31 - 2) + 1 if packages else 1
    if upload_identity is not None:
        package_id = identity(upload_identity[0])
        revision = _integer(upload_identity[1], 1, 2**31 - 1)
    manifest = dict(schemaVersion=scope.schema_version, contentKind=CONTENT_KIND, mode=scope.mode, workId=work,
                    packageId=package_id, revision=revision, title=title, author=author,
                    minClientVersion="1.11.2", imageCount=0, maps=maps, routeEntryFloor="b1" if scope.season == "s1" else "1")
    if scope.schema_version == 4:
        manifest.update(scenario=scope.scenario, routeCatalogId=scope.id, minClientVersion="1.11.12")
    if source_work_id:
        manifest["sourceWorkId"] = source_work_id
    if not 1 <= len(maps) <= scope.max_maps:
        raise AccountError("路线地图数量不符合当前难度")
    raw = _json(manifest)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise AccountError("路线清单超过 6 MiB")
    output = io.BytesIO()
    # Fixed timestamp and stored bytes keep retries identical and avoid Android's
    # decompression-ratio guard, even for very repetitive routes.
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as bundle:
        bundle.writestr(zipfile.ZipInfo(MANIFEST_NAME, date_time=(2020, 1, 1, 0, 0, 0)), raw)
    archive = output.getvalue()
    if len(archive) > MAX_PACKAGE_BYTES:
        raise AccountError("路线包超过 64 MiB")
    sha = hashlib.sha256(archive).hexdigest()
    payload = dict(schema_version=scope.schema_version, work_id=work, package_id=package_id, package_revision=revision,
                   mode=scope.mode, map_count=len(maps), image_count=0, manifest_bytes=len(raw),
                   object_bytes=len(archive), sha256=sha, content_type=CONTENT_TYPE,
                   idempotency_key=f"{draft['id']}-{package_id}-{revision}-{sha[:16]}")
    if scope.schema_version == 4:
        payload["scenario"] = scope.scenario
    return Package(archive, payload)


def upload_object(upload, archive, *, cancelled=lambda: False, progress=lambda sent, total: None):
    """Fixed length PUT; TLS verified, no redirects, cookies or account headers."""
    if not isinstance(upload, dict) or upload.get("method") != "PUT":
        raise AccountError("服务器返回的上传方式不受支持")
    url = upload.get("url", "")
    if not _download_url_allowed(url):
        raise AccountError("上传地址不受信任")
    parts = urlsplit(url)
    if parts.username or parts.password or parts.fragment or parts.port not in (None, 443) and parts.scheme == "https":
        raise AccountError("上传地址不受信任")
    headers = {"Content-Type": CONTENT_TYPE, "Content-Length": str(len(archive))}
    raw_headers = upload.get("headers") or {}
    if not isinstance(raw_headers, dict):
        raise AccountError("上传授权头格式不正确")
    for name, value in raw_headers.items():
        lower = str(name).lower()
        if lower in ("content-type", "content-md5") or lower.startswith(("x-amz-", "x-oss-", "x-cos-")):
            if not isinstance(value, str) or not 1 <= len(value) <= 1024 or "\r" in value or "\n" in value:
                raise AccountError("上传授权头格式不正确")
            headers[name] = value
    connection = (http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection)(
        parts.hostname, parts.port, timeout=30)
    try:
        check_cancelled(cancelled)
        connection.putrequest("PUT", parts.path + ("?" + parts.query if parts.query else ""))
        for name, value in headers.items():
            connection.putheader(name, value)
        connection.endheaders()
        for at in range(0, len(archive), 64 * 1024):
            check_cancelled(cancelled)
            connection.send(archive[at:at + 64 * 1024])
            progress(min(at + 64 * 1024, len(archive)), len(archive))
        response = connection.getresponse()
        if not 200 <= response.status < 300:
            raise AccountError(f"路线包上传失败（{response.status}），请重试", response.status)
        etag = (response.getheader("ETag") or "").strip().strip('"')
        if not 1 <= len(etag) <= 160:
            raise AccountError("对象存储未返回 ETag，无法确认上传")
        return etag
    except (OSError, http.client.HTTPException):
        raise AccountError("路线包上传失败，请检查网络后重试") from None
    finally:
        connection.close()


class Submission:
    """One resumable attempt, owned by the single plaza worker.

    Retains exact package/idempotency key after failures and cancellation. A
    retry first reads the server state; a lost submit response cannot create a
    duplicate work or re-upload an already bound package.
    """
    def __init__(self, gallery_id, maps, title, description, *, version=None, scope_id="legacy-nightmare"):
        self.scope_id = scope_id
        self.gallery_id, self.maps = gallery_id, maps
        self.metadata = metadata(title, description)
        if scope_id != "legacy-nightmare":
            self.metadata["scenario"] = SCOPES[scope_id].scenario
        self.version = version
        self.draft = None
        self.package = None
        self.bound = False
        self.creation_unknown = False

    def run(self, account, *, cancelled=lambda: False, progress=lambda stage, sent=0, total=0: None):
        def api(method, path, body=None):
            check_cancelled(cancelled)
            return account.authorized(method, path, body)
        progress("正在读取投稿草稿")
        if self.draft is None:
            if self.creation_unknown:
                raise AccountError("草稿创建结果未确认，请先刷新「我的投稿」；关闭窗口后可选择已有草稿继续，避免重复创建")
            check_cancelled(cancelled)
            self.creation_unknown = True
            try:
                draft = api("PUT" if self.version else "POST", BASE + ("/" + quoted(self.version) if self.version else ""), self.metadata)
            except AccountError as failure:
                if 400 <= failure.status < 500:
                    self.creation_unknown = False
                raise
            # Always adopt the *returned* version ID after PUT (approved -> new draft).
            self.draft = validate_version(draft)
            self.creation_unknown = False
            if self.draft["status"] not in ("draft", "rejected"):
                raise AccountError("服务器未返回可上传草稿，请刷新「我的投稿」确认")
        draft = validate_version(api("GET", BASE + "/" + quoted(self.draft["id"])))
        if draft["work_id"] != self.draft["work_id"] or draft["id"] != self.draft["id"]:
            raise AccountError("服务器返回的草稿身份已变化")
        if draft["status"] in ("pending", "approved"):
            return draft
        if draft["status"] not in ("draft", "rejected"):
            raise AccountError("投稿状态已变化，请刷新「我的投稿」后重试", 409)
        if self.package is None:
            self.package = build_package(draft, self.gallery_id, self.maps, self.metadata["title"], account.username, scope_id=self.scope_id)
        package = self.package
        path = BASE + "/" + quoted(draft["id"])
        if not self.bound:
            progress("正在申请上传授权")
            try:
                session = api("POST", path + "/upload-sessions", package.payload)
            except AccountError as failure:
                # A previous device/abandoned upload may have reserved a newer
                # package revision than the last *bound* package. This exact
                # 409 detail is supplied by map_galleries.py, never guessed.
                if failure.status != 409 or failure.upload_identity is None:
                    raise
                self.package = package = build_package(draft, self.gallery_id, self.maps,
                    self.metadata["title"], account.username, upload_identity=failure.upload_identity, scope_id=self.scope_id)
                session = api("POST", path + "/upload-sessions", package.payload)
            if (not isinstance(session, dict) or session.get("status") not in ("created", "completed")
                    or session.get("gallery_version_id") != draft["id"] or session.get("work_id") != draft["work_id"]
                    or any(session.get(key) != package.payload[key] for key in
                           ("package_id", "package_revision", "mode", "map_count", "image_count", "manifest_bytes", "object_bytes", "sha256"))):
                raise AccountError("服务器返回的上传会话与本机路线包不一致")
            sid = identity(session.get("id"))
            if session["status"] != "completed":
                progress("正在上传路线包", 0, len(package.archive))
                etag = upload_object(session.get("upload"), package.archive, cancelled=cancelled,
                                     progress=lambda sent, total: progress("正在上传路线包", sent, total))
                progress("正在校验并绑定路线包")
                completed = api("POST", path + "/upload-sessions/" + sid + "/complete", dict(etag=etag))
                if (not isinstance(completed, dict) or completed.get("id") != sid or completed.get("status") != "completed"
                        or any(completed.get(key) != session.get(key) for key in
                               ("work_id", "gallery_version_id", "package_id", "package_revision", "sha256", "object_bytes"))):
                    raise AccountError("服务器返回的上传完成记录与当前投稿不一致")
            self.bound = True
        progress("正在提交人工审核")
        # Once the server accepts this write it is real even if Cancel arrives
        # while waiting for the response. Never label a confirmed pending as cancelled.
        result = validate_version(api("POST", path + "/submit", dict(confirmed=True)))
        if result["id"] != draft["id"] or result["work_id"] != draft["work_id"] or result["status"] != "pending":
            raise AccountError("审核结果与当前投稿不一致，请刷新「我的投稿」确认")
        return result


def withdraw(account, version):
    row = validate_version(account.authorized("POST", BASE + "/" + quoted(version) + "/withdraw", dict(confirmed=True)))
    if row["id"] != version or row["status"] != "withdrawn":
        raise AccountError("撤回结果与当前投稿不一致，请刷新「我的投稿」确认")
    return row


def submit_review(account, version):
    row = validate_version(account.authorized("POST", BASE + "/" + quoted(version) + "/submit", dict(confirmed=True)))
    if row["id"] != version or row["status"] != "pending":
        raise AccountError("审核结果与当前投稿不一致，请刷新「我的投稿」确认")
    return row
