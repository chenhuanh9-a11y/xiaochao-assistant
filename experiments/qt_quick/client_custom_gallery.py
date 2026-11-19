"""Qt adapter for the existing gallery store and road planner; no game owner."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading

from PySide6.QtCore import QObject, Property, QRunnable, QThreadPool, QTimer, Qt, QUrl, Signal, Slot

from client_paths import APP_ROOT, runtime_paths, editor_library_usable, _no_links
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
from route_scopes import SCOPES
from scoped_route_library import ScopedRouteLibrary
from custom_maps import (COLORS, DEFAULT_COLOR, DEFAULT_GALLERY_FILE, FLOORS, FLOOR_LABELS,
                         GALLERY_SCHEMA, MAX_POINTS, CustomMapLibrary, GalleryStore,
                         RouteError, insertion_index, marker_groups)


class _Signals(QObject):
    done = Signal(object, object)


class _Job(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.signals = _Signals()

    def run(self):
        try:
            self.signals.done.emit(self.function(), None)
        except Exception as error:
            self.signals.done.emit(None, str(error))


def _file_digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


class CustomGalleryService(QObject):
    """All writes stay in the explicitly marked Qt sandbox, on one worker."""
    changed = Signal()
    galleriesChanged = Signal()
    scopeChanged = Signal()
    confirmationRequested = Signal(str, str)

    def __init__(self, sandbox, *, library=None, default_path=DEFAULT_GALLERY_FILE):
        super().__init__()
        self._scope_id = "legacy-nightmare"
        self._scope_snapshots = {}
        self.paths = runtime_paths(sandbox)
        self.sandbox = self.paths.installation_root
        self.path = self.paths.custom_galleries_path
        self.exports = self.sandbox / "exports"
        self.library = library or CustomMapLibrary(self.paths.editor_base_root)
        self._injected_library = library is not None
        self._legacy_library_root = self.library.root
        self._library_root = self.library.root
        self._committed_lock = threading.RLock()
        self._committed = {}
        self._committed_library = self.library
        self.default_path = Path(default_path)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self._store = None
        self._data_error = ""
        self._digest = None
        self._job = None
        self._closed = False
        self._busy = False
        self._refresh_pending = False
        self._resource_generation = 0
        self._plaza_pending = None
        self._pending = None
        self._gallery = "default"
        self._map = ""
        self._floor = "1"
        self._color = DEFAULT_COLOR
        self._start = False
        self._past, self._future = [], []
        self._use_handler = None
        self._in_use = ""
        self._editing = False
        self._map_metadata = []
        self._state = dict(galleries=[], maps=[], floors=[], points=[], lines=[], markers=[],
                           selectedGallery="default", selectedMap="", selectedFloor="1", readonly=True,
                           imageUrl="", imageWidth=0, imageHeight=0, planError="", canUndo=False,
                           canRedo=False, available=False, libraryMessage="正在读取自定义图集")
        self._message = ""
        QTimer.singleShot(0, self.refresh)

    def _snapshot(self):
        scope = SCOPES[self._scope_id]
        return dict(deepcopy(self._state), scopeId=scope.id, scenario=scope.scenario, mode=scope.mode,
                    scopeOptions=[dict(id=x.id, name=x.label) for x in SCOPES.values()], busy=self._busy, message=self._message,
                    pendingKind=self._pending["kind"] if self._pending else "",
                    color=self._color, colors=[dict(value=c, name=n) for c, n in COLORS],
                    newStart=self._start, editing=self._editing,
                    canUse=self._use_handler is not None and not self._data_error, inUse=self._in_use)

    snapshot = Property("QVariantMap", _snapshot, notify=changed)
    busy = Property(bool, lambda s: s._busy, notify=changed)
    message = Property(str, lambda s: s._message, notify=changed)
    pendingWrites = Property(bool, lambda s: s._plaza_pending is not None, notify=changed)

    def _export_directory_url(self):
        try:
            _no_links(self.exports)
            if self.exports.is_dir():
                return QUrl.fromLocalFile(str(self.exports))
        except (OSError, ValueError):
            pass
        return QUrl.fromLocalFile(str(self.sandbox))

    exportDirectoryUrl = Property(QUrl, _export_directory_url, notify=changed)

    def _load(self):
        self._data_error = ""
        try:
            if self.path.exists():
                if self.path.is_symlink() or self.path.stat().st_size > 4 * 1024 * 1024:
                    raise ValueError("图库文件不可读取")
                data = json.loads(self.path.read_text(encoding="utf-8-sig"))
                if not isinstance(data, dict) or data.get("schema") != GALLERY_SCHEMA or not isinstance(data.get("galleries"), list):
                    raise ValueError("图库文件格式不兼容")
                if data.get("routeCatalogId") != (self._scope_id if self._scope_id != "legacy-nightmare" else None):
                    raise ValueError("图库文件的场景或难度不一致")
        except (OSError, ValueError) as error:
            self._data_error = f"{error}；原文件已保留，当前仅浏览地图。请备份并检查 custom-galleries.json 后刷新。"
        store_path = self.path / "read-only" if self._data_error else self.path
        self._store = GalleryStore(store_path, self.default_path if self._scope_id == "legacy-nightmare" else self.path.parent / "no-bundled-default.json",
            scope=SCOPES[self._scope_id] if self._scope_id != "legacy-nightmare" else None,
            legacy_path=(self.paths.settings_path.with_name("custom-routes.json") if self.paths.production
                         else self.sandbox / "qt-no-implicit-legacy-import.json"))
        self.library = (CustomMapLibrary(self._library_root) if self._scope_id == "legacy-nightmare"
                        else ScopedRouteLibrary(self._library_root, self._scope_id))
        self._cache_map_metadata()
        self._digest = None if self._data_error else _file_digest(self.path)
        self._publish_committed()
        if self._scope_id == "legacy-nightmare":
            self._restore_scoped_snapshots()

    def _restore_scoped_snapshots(self):
        """Restore saved game selections without requiring an editor-page visit."""
        root = self.paths.resource_root / "assets/android-route-editor"
        if not root.exists():
            from client_paths import RESOURCE_ROOT
            root = RESOURCE_ROOT / "assets/android-route-editor"
        for scope in SCOPES.values():
            if scope.id == "legacy-nightmare":
                continue
            path = self.paths.settings_path.parent / "route-galleries" / scope.id / "galleries.json"
            try:
                _no_links(path)
                if path.exists():
                    if path.stat().st_size > 4 * 1024 * 1024:
                        continue
                    raw = json.loads(path.read_text("utf-8-sig"))
                    if raw.get("schema") != GALLERY_SCHEMA or raw.get("routeCatalogId") != scope.id:
                        continue
                library = ScopedRouteLibrary(root, scope.id)
                store = GalleryStore(path, path.parent / "no-bundled-default.json", scope=scope)
                if store.load_error:
                    continue
                rows = {gid: store.export(gid) for gid, _name, _ro in store.galleries()}
                with self._committed_lock:
                    self._scope_snapshots[scope.id] = dict(library=library, galleries=rows,
                        digest=_file_digest(path), path=path, revision=store.revision, error="")
            except (OSError, ValueError, TypeError, AttributeError):
                continue

    def _cache_map_metadata(self):
        """Verify thumbnails once per library refresh, never per route write."""
        rows = []
        for mid, name in self.library.maps():
            row = dict(id=mid, name=name, thumbnailUrl="", thumbnailError="")
            try:
                if isinstance(self.library, ScopedRouteLibrary):
                    floor = self.library.floor_ids[0]
                else:
                    graph = self.library.graph(mid)
                    floor = next(f for f in FLOORS if f in graph.floors)
                asset = self.library.floor(mid, floor)
                row["thumbnailUrl"] = QUrl.fromLocalFile(str(asset.image_path)).toString()
            except (OSError, ValueError, KeyError, TypeError, StopIteration) as error:
                row["thumbnailError"] = str(error)
            rows.append(row)
        self._map_metadata = rows

    def _publish_committed(self):
        galleries = {gid: self._store.export(gid) for gid, _name, _ro in self._store.galleries()}
        with self._committed_lock:
            self._committed = galleries
            self._committed_library = self.library
            self._committed_digest = self._digest
            self._committed_revision = self._store.revision
            self._scope_snapshots[self._scope_id] = dict(library=self.library, galleries=deepcopy(galleries),
                digest=self._digest, path=self.path, revision=self._store.revision, error=self._data_error)

    def base_display_library(self):
        """Worker-safe immutable library snapshot for the original base renderer."""
        with self._committed_lock:
            return self._scope_snapshots.get("legacy-nightmare", {}).get("library", self._committed_library)

    def display_snapshot(self, gallery_id, map_id=None):
        """Pure worker-safe committed data; never exposes a mutating store/Qt property."""
        scope_id, gid = self.splitGalleryId(gallery_id)
        with self._committed_lock:
            saved = self._scope_snapshots.get(scope_id)
            if saved is None or saved["error"]:
                return None
            row = saved["galleries"].get(gid)
            if row is None:
                return None
            result = dict(galleryId=str(gallery_id), galleryName=row["name"],
                points=deepcopy(row.get("maps", {}).get(str(map_id), {}).get("points", [])),
                library=saved["library"], revision=saved["revision"], diskDigest=saved["digest"],
                scopeId=scope_id)
        if saved["path"].is_symlink() or _file_digest(saved["path"]) != result["diskDigest"]:
            return None
        return result

    @staticmethod
    def splitGalleryId(gallery_id):
        value = str(gallery_id)
        if "~" in value:
            scope_id, gid = value.split("~", 1)
            if scope_id in SCOPES and scope_id != "legacy-nightmare":
                return scope_id, gid
        return "legacy-nightmare", value

    @Slot(str, result=str)
    def profileGalleryId(self, gallery_id):
        return str(gallery_id) if self._scope_id == "legacy-nightmare" else self._scope_id + "~" + str(gallery_id)

    scopeId = Property(str, lambda s: s._scope_id, notify=scopeChanged)

    @Slot(str)
    def selectScope(self, scope_id):
        if scope_id not in SCOPES or scope_id == self._scope_id or self._busy or self._pending:
            return
        def select():
            self._scope_id = scope_id
            self._resource_generation += 1
            self.path = (self.paths.custom_galleries_path if scope_id == "legacy-nightmare" else
                         self.paths.settings_path.parent / "route-galleries" / scope_id / "galleries.json")
            self._library_root = (self._legacy_library_root if scope_id == "legacy-nightmare" else
                                  self.paths.resource_root / "assets/android-route-editor")
            if scope_id != "legacy-nightmare" and not self._library_root.exists():
                from client_paths import RESOURCE_ROOT
                self._library_root = RESOURCE_ROOT / "assets/android-route-editor"
            self._gallery, self._map, self._floor = "default", "", SCOPES[scope_id].floors[0]
            self._editing = False
            self._past, self._future = [], []
            self._load()
        self._run(select, on_done=lambda error: self.scopeChanged.emit() if not error else None)

    def _write(self, operation):
        if self._data_error:
            raise ValueError(self._data_error)
        if self.path.is_symlink() or not self.path.resolve().is_relative_to(self.sandbox):
            raise ValueError("图集路径不可写，原文件已保留")
        temporary = self.path.with_suffix(".tmp")
        if temporary.exists() or temporary.is_symlink():
            raise ValueError("图集暂存文件已存在，请检查后重试；原文件已保留")
        if _file_digest(self.path) != self._digest:
            self._load()
            raise ValueError("图集已被其他操作修改，请刷新后重试")
        try:
            result = operation()
            if result is False:
                raise OSError("图集保存失败")
            data = json.loads(self.path.read_text(encoding="utf-8"))
            expected = [{k: v for k, v in self._store.export(gid, include_source=True).items() if k != "schema"}
                        for gid, _name, _readonly in self._store.galleries() if gid != "default"]
            if data.get("schema") != GALLERY_SCHEMA or data.get("galleries") != expected:
                raise OSError("图集保存未完成，已恢复已保存内容")
            self._digest = _file_digest(self.path)
            self._publish_committed()
            return result
        except Exception:
            if temporary.is_file() and not temporary.is_symlink():
                temporary.unlink()
            self._load()
            raise

    def _state_for_selection(self):
        store = self._store
        galleries = [dict(id=gid, name=name, readonly=ro, source=store.source(gid),
                         originWorkId=store.export(gid).get("originWorkId"), drawnCount=len(store.drawn_maps(gid)))
                     for gid, name, ro in store.galleries()]
        if self._gallery not in [g["id"] for g in galleries]:
            self._gallery = galleries[0]["id"]
        drawn = set(store.drawn_maps(self._gallery))
        maps = [dict(row, drawn=row["id"] in drawn) for row in self._map_metadata]
        if self._map not in [row["id"] for row in maps]:
            self._map = maps[0]["id"] if maps else ""
        state = dict(galleries=galleries, maps=maps, selectedGallery=self._gallery, selectedMap=self._map,
            galleryName=store.name(self._gallery), mapName=self.library.name(self._map),
            drawnCount=len(drawn), mapCount=len(maps),
            selectedFloor=self._floor, floors=[], points=[], lines=[], markers=[], readonly=bool(self._data_error) or store.readonly(self._gallery),
            source=store.source(self._gallery), canDelete=not self._data_error and self._gallery != "default",
            imageUrl="", imageWidth=0, imageHeight=0, planError="", available=self.library.available,
            libraryMessage=self.library.error, dataError=self._data_error, canUndo=bool(self._past), canRedo=bool(self._future))
        if not self._map:
            return state
        try:
            graph = self.library.graph(self._map)
        except (OSError, ValueError, KeyError, TypeError) as error:
            state.update(available=False, libraryMessage=f"地图资源无法读取：{error}")
            return state
        floors = [f for f in getattr(self.library, "floor_ids", FLOORS) if f in graph.floors]
        if self._floor not in floors:
            self._floor = floors[0]
        asset = self.library.floor(self._map, self._floor)
        points = store.route(self._gallery, self._map)
        segments = []
        try:
            segments = graph.plan(points)
        except RouteError as error:
            state["planError"] = str(error)
        inverse = asset.to_image
        def xy(point):
            x, y, z = inverse @ [point[0], point[1], 1]
            return dict(x=float(x / z), y=float(y / z))
        lines = [dict(points=[xy(p) for p in line], color=color)
                 for line, color in graph.floor_lines(segments, self._floor)]
        markers = [dict(**xy((x, y)), label=label) for x, y, label in marker_groups(points, self._floor, per_floor=True)]
        state.update(floors=[dict(id=f, name=getattr(self.library, "floor_labels", FLOOR_LABELS)[f]) for f in floors], selectedFloor=self._floor,
            imageUrl=QUrl.fromLocalFile(str(asset.image_path)).toString(), imageWidth=int(asset.image_size[0]),
            imageHeight=int(asset.image_size[1]), points=points, lines=lines, markers=markers)
        return state

    def _run(self, function, message="", *, galleries_changed=False, on_done=None):
        if self._closed or self._busy or self._pending:
            return False
        self._busy = True
        self.changed.emit()
        def work():
            if self._store is None:
                self._load()
            function()
            return self._state_for_selection()
        job = self._job = _Job(work)
        def done(state, error):
            self._job = None
            self._busy = False
            if self._closed:
                return
            if state is not None:
                self._state = state
            self._message = str(error) if error else (self._data_error or message)
            self.changed.emit()
            if galleries_changed and not error:
                self.galleriesChanged.emit()
            if on_done is not None:
                on_done(error)
            if self._refresh_pending:
                self._refresh_pending = False
                self.refresh()
            else:
                self._drain_plaza()
        job.signals.done.connect(done, Qt.QueuedConnection)
        self.pool.start(job)
        return True

    @Slot()
    def refresh(self):
        # Revoke prepared downloads immediately, even if a writer delays reload.
        self._resource_generation += 1
        if self._busy:
            self._refresh_pending = True
            return
        def reload():
            if not self._injected_library and self._scope_id == "legacy-nightmare":
                paths = runtime_paths(self.sandbox)
                root = next((candidate for candidate in paths.editor_base_candidates
                             if editor_library_usable(candidate)), paths.editor_base_root)
                self.library = CustomMapLibrary(root)
                self._library_root = self.library.root
                self._legacy_library_root = self.library.root
            self._load()
        self._run(reload)

    @Slot(str)
    def selectGallery(self, gid):
        def select():
            if self._store.name(gid) is None:
                raise ValueError("图集已不存在")
            self._gallery = gid
            self._editing = False
            self._past, self._future = [], []
        self._run(select)

    @Slot(str)
    def selectMap(self, mid):
        def select():
            if not self.library.has(mid):
                raise ValueError("地图资源不可用")
            self._map = mid
            self._editing = True
            self._past, self._future = [], []
        self._run(select)

    @Slot()
    def browseGalleries(self):
        if not self._busy and not self._pending:
            self._editing = False
            self.changed.emit()

    @Slot(str)
    def selectFloor(self, floor):
        self._run(lambda: setattr(self, "_floor", str(floor)))

    @Slot(str)
    def createGallery(self, name):
        def create():
            self._gallery = self._write(lambda: self._store.create(name))
            self._past, self._future = [], []
        self._run(create,
                  "图集已创建", galleries_changed=True)

    @Slot(str)
    def copyGallery(self, name):
        def copy():
            self._gallery = self._write(lambda: self._store.create(name, self._gallery))
            self._past, self._future = [], []
        self._run(copy,
                  "副本已创建，可以编辑", galleries_changed=True)

    @Slot(str)
    def renameGallery(self, name):
        self._run(lambda: self._write(lambda: self._store.rename(self._gallery, name)),
                  "图集已重命名", galleries_changed=True)

    @Slot()
    def requestDeleteGallery(self):
        if self._busy or self._pending or not self._state.get("canDelete"):
            return
        self._pending = dict(kind="delete", gallery=self._gallery, digest=self._digest)
        self.changed.emit()
        self.confirmationRequested.emit("删除图集", "删除后，该图集的所有路线都会移除。")

    def _edit(self, points, *, history=True):
        if self._store.readonly(self._gallery) or not self._map:
            raise ValueError("默认图集只读，请先创建副本")
        previous = self._store.route(self._gallery, self._map)
        self._write(lambda: self._store.set_route(self._gallery, self._map, points))
        if history:
            self._past.append(previous)
            del self._past[:-128]
            self._future = []

    @Slot(str)
    def setColor(self, color):
        if not self._busy and not self._pending and color in [row[0] for row in COLORS]:
            self._color = color
            self.changed.emit()

    @Slot()
    def toggleNewStart(self):
        if self._busy or self._pending:
            return
        self._start = not self._start
        self.changed.emit()

    @Slot(float, float)
    def addPoint(self, image_x, image_y):
        def edit():
            points = self._store.route(self._gallery, self._map)
            if len(points) >= getattr(self.library, "max_points", MAX_POINTS):
                raise ValueError(f"一张地图最多 {getattr(self.library, 'max_points', MAX_POINTS)} 个目标")
            asset = self.library.floor(self._map, self._floor)
            u, v, z = asset.to_native @ [image_x, image_y, 1]
            graph = self.library.graph(self._map)
            target = graph.pick(self._floor, float(u / z), float(v / z))
            if target is None:
                raise ValueError("请点击房间内部或可走的道路")
            target["color"] = self._color
            if self._start and points:
                target["start"] = True
            try:
                segments = graph.plan(points)
            except RouteError:
                segments = []
            index = len(points) if self._start else insertion_index(points, segments, graph, target)
            previous = points[index - 1] if index else None
            if not self._start and previous and all(previous.get(k) == target.get(k) for k in ("floor", "room", "x", "y")):
                return
            points.insert(index, target)
            self._edit(points)
            self._start = False
        self._run(edit, "路线已自动保存", galleries_changed=True)

    @Slot(int)
    def removePoint(self, index):
        def edit():
            points = self._store.route(self._gallery, self._map)
            if not 0 <= index < len(points):
                raise ValueError("目标已不存在")
            if points[index].get("start") and index + 1 < len(points):
                points[index + 1]["start"] = True
            del points[index]
            if points:
                points[0].pop("start", None)
            self._edit(points)
        self._run(edit, "目标已删除", galleries_changed=True)

    @Slot(int, int)
    def movePoint(self, index, delta):
        def edit():
            points = self._store.route(self._gallery, self._map)
            destination = index + delta
            if delta not in (-1, 1) or not 0 <= index < len(points) or not 0 <= destination < len(points):
                raise ValueError("无法移动这个目标")
            points[index], points[destination] = points[destination], points[index]
            self._edit(points)
        self._run(edit, "目标顺序已保存", galleries_changed=True)

    @Slot()
    def undo(self):
        def edit():
            if not self._past:
                return
            current = self._store.route(self._gallery, self._map)
            self._edit(self._past[-1], history=False)
            self._past.pop()
            self._future.append(current)
        self._run(edit, "已撤销", galleries_changed=True)

    @Slot()
    def redo(self):
        def edit():
            if not self._future:
                return
            current = self._store.route(self._gallery, self._map)
            self._edit(self._future[-1], history=False)
            self._future.pop()
            self._past.append(current)
        self._run(edit, "已重做", galleries_changed=True)

    @Slot()
    def requestClearRoute(self):
        if self._busy or self._pending or self._state.get("readonly") or not self._state.get("points"):
            return
        self._pending = dict(kind="clear", gallery=self._gallery, map=self._map, digest=self._digest)
        self.changed.emit()
        self.confirmationRequested.emit("清空路线", "清空当前地图的所有楼层目标？此操作可以撤销。")

    @Slot(str)
    def importGallery(self, value):
        path = Path(QUrl(value).toLocalFile() if value.startswith("file:") else value)
        def load():
            _no_links(path)
            if not path.is_file() or path.stat().st_size > 64 * 1024 * 1024:
                raise ValueError("请选择不超过 64 MiB 的图集文件")
            if path.suffix.lower() in (".xingtu-gallery", ".zip"):
                import io, zipfile
                import route_plaza
                archive = path.read_bytes()
                with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
                    member = bundle.getinfo("map-gallery.json")
                    if member.file_size > route_plaza.MAX_MANIFEST_BYTES:
                        raise ValueError("路线清单过大")
                    header = json.loads(bundle.read(member).decode("utf-8-sig"))
                scope = SCOPES[self._scope_id]
                if (header.get("scenario", "lady-of-misfortune"), header.get("mode")) != (scope.scenario, scope.mode):
                    raise ValueError("请先选择路线包对应的场景与难度")
                manifest = route_plaza.read_manifest(archive,
                    dict(mode=scope.mode, package_id=header.get("packageId"), revision=header.get("revision"), manifest_bytes=member.file_size),
                    dict(work_id=header.get("workId"), scenario=scope.scenario))
                maps, report = route_plaza.convert_manifest(self.library, manifest)
                if not maps or report["droppedPoints"] or report["missingMaps"]:
                    raise ValueError("路线包与地图资料不完整兼容，未导入")
                payload = dict(schema=GALLERY_SCHEMA, name=manifest.get("title", "导入路线"), maps=maps,
                               originWorkId=manifest.get("sourceWorkId") or manifest.get("workId"))
                if scope.id != "legacy-nightmare":
                    payload.update(routeCatalogId=scope.id, scenario=scope.scenario, mode=scope.mode)
            else:
                if path.stat().st_size > 4 * 1024 * 1024:
                    raise ValueError("图集 JSON 不能超过 4 MiB")
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
            self._gallery = self._write(lambda: self._store.import_gallery(payload))
            self._past, self._future = [], []
        self._run(load, "图集已导入", galleries_changed=True)

    def _export_path(self, name):
        name = str(name).strip()
        if not re.fullmatch(r"[^\\/:*?\"<>|\x00-\x1f]{1,80}\.(?:json|xingtu-gallery)", name, re.I) or name in (".json", "..json"):
            raise ValueError("请使用有效的 .json 或 .xingtu-gallery 文件名")
        return self._validate_export_path(self.exports / name)

    def _validate_export_path(self, target):
        target = Path(target)
        _no_links(target)
        if not target.is_absolute() or target.suffix.lower() not in (".json", ".xingtu-gallery"):
            raise ValueError("请选择本地 .json 或 .xingtu-gallery 文件")
        if not self.paths.production and not target.resolve().is_relative_to(self.sandbox):
            raise ValueError("导出路径必须位于当前测试数据目录")
        if target.exists() and not target.is_file():
            raise ValueError("导出目标必须是文件")
        return target

    @Slot(str)
    def exportGallery(self, name):
        if self._closed or self._busy or self._pending:
            return
        try:
            path = self._export_path(name)
        except (OSError, ValueError) as error:
            self._message = str(error); self.changed.emit(); return
        self._request_export(path)

    @Slot(str)
    def exportGalleryTo(self, file_url):
        if self._closed or self._busy or self._pending:
            return
        try:
            url = QUrl(file_url)
            if not url.isLocalFile():
                raise ValueError("请选择本地 .json 或 .xingtu-gallery 文件")
            path = self._validate_export_path(Path(url.toLocalFile()))
        except (OSError, ValueError) as error:
            self._message = str(error); self.changed.emit(); return
        self._request_export(path)

    def _request_export(self, path):
        try:
            if path.exists():
                self._pending = dict(kind="export", path=str(path), digest=_file_digest(path), gallery=self._gallery)
                self.changed.emit()
                self.confirmationRequested.emit("覆盖导出文件", f"覆盖 {path.name}？")
            else:
                self._export(path, self._gallery)
        except (OSError, ValueError) as error:
            self._message = f"导出未开始：{error}"
            self.changed.emit()

    def _export(self, path, gid):
        self._run(lambda: self._write_export(path, gid), f"已导出到 {path}")

    def _write_export(self, path, gid, *, overwrite=False, expected=None):
        import map_package_upgrade as files
        from resource_transaction import hardlinks_unsupported
        from star_map_import import _rollback_published

        path = self._validate_export_path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        gallery = self._store.export(gid)
        if path.suffix.lower() == ".xingtu-gallery":
            import uuid
            import route_submission
            # File exchange remains available for personal use, including a
            # downloaded work. This does not authorize publishing a derivative.
            origin = gallery.get("originWorkId")
            personal = dict(gallery, id="local-export", originWorkId=None, source=None, readonly=False)
            maps = route_submission.prepare_maps(self.library, personal)
            draft = dict(id=str(uuid.uuid4()), work_id=str(uuid.uuid4()), revision=1,
                         status="draft", content_kind="nightmare-routes", packages=[],
                         scenario=SCOPES[self._scope_id].scenario)
            data = route_submission.build_package(draft, gid, maps, gallery["name"], "星辰",
                                                   scope_id=self._scope_id, source_work_id=origin).archive
        else:
            data = json.dumps(gallery, ensure_ascii=False, indent=1).encode("utf-8")
        row = dict(path=path.name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        temporary = None
        identity = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent,
                                             prefix=".gallery-export-", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                created = os.fstat(handle.fileno())
                if not created.st_ino:
                    raise ValueError("当前文件系统无法验证导出文件身份")
                identity = (created.st_dev, created.st_ino)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            self._validate_export_path(path)
            if overwrite:
                if _file_digest(path) != expected:
                    raise ValueError("导出文件已变化，请重新确认")
                os.replace(temporary, path)
            else:
                # Publish a complete file without replacing a destination that
                # appeared after the chooser/inspection. Bind the original
                # created object, never recapture a possibly replaced path.
                if not files._matches(temporary, row, list(identity)):
                    raise ValueError("导出临时文件已变化，请重试")
                try:
                    os.link(temporary, path)
                except OSError as error:
                    if not hardlinks_unsupported(error):
                        raise
                    files._move_owned(temporary, path, row, list(identity), linkless=True)
                if not files._matches(path, row, list(identity)):
                    raise ValueError("导出文件已变化，请检查目标文件后重试")
        finally:
            if temporary is not None and identity is not None:
                # A move consumes the name; another writer may already have
                # reused it, even with identical bytes. Never unlink by name.
                _rollback_published(temporary, identity, row["sha256"])

    @Slot(bool)
    def confirmOperation(self, accepted):
        pending, self._pending = self._pending, None
        self.changed.emit()
        if not accepted or pending is None:
            return
        def confirm():
            if pending["kind"] == "export":
                path = self._validate_export_path(Path(pending["path"]))
                if _file_digest(path) != pending["digest"]:
                    raise ValueError("导出文件已变化，请重新确认")
                self._write_export(path, pending["gallery"], overwrite=True, expected=pending["digest"])
            else:
                if _file_digest(self.path) != pending["digest"] or pending["gallery"] != self._gallery:
                    raise ValueError("图集已变化，请重新确认")
                if pending["kind"] == "delete":
                    if pending["gallery"] == self._in_use:
                        raise ValueError("请先停用此图集，再删除")
                    self._write(lambda: self._store.delete(pending["gallery"]))
                    self._gallery = "default"
                    self._past, self._future = [], []
                elif pending["kind"] == "clear" and pending["map"] == self._map:
                    self._edit([])
        self._run(confirm, "操作已完成", galleries_changed=pending["kind"] != "export")

    def setUseHandler(self, callback):
        self._use_handler = callback
        self.changed.emit()

    def plazaSources(self):
        """Read only the last UI snapshot; the store belongs to its single worker."""
        return {row["source"]["workId"]: dict(row["source"])
                for row in self._state["galleries"] if row.get("source")}

    def submissionChoices(self):
        """UI snapshot only; never read the writer's mutable GalleryStore."""
        return [dict(id=row["id"], name=row["name"]) for row in self._state["galleries"]
                if row["id"] != "default" and not row.get("readonly") and not row.get("source") and not row.get("originWorkId")]

    def submissionSnapshot(self, gallery_id):
        if self._closed or self._busy or self._pending or self._refresh_pending or self._data_error:
            raise ValueError("自定义图库正忙或资源已变化，请完成当前操作后重试")
        if not any(row["id"] == gallery_id for row in self.submissionChoices()):
            raise ValueError("请选择自己的可编辑图库")
        with self._committed_lock:
            gallery = deepcopy(self._committed.get(gallery_id))
            library = self._committed_library
        if gallery is None:
            raise ValueError("图库已变化，请重新选择")
        return library, self._resource_generation, gallery

    def submissionContextCurrent(self, context):
        library, generation, gallery = context
        with self._committed_lock:
            current = self._committed.get(gallery["id"])
        return (self.plazaContextCurrent((library, generation)) and current == gallery)

    def submissionDiskCurrent(self, context):
        """Worker-only external-write guard; never hash the file on the UI thread."""
        with self._committed_lock:
            digest = self._committed_digest
        return (self.submissionContextCurrent(context) and not self.path.is_symlink()
                and _file_digest(self.path) == digest)

    def plazaInstallContext(self):
        """UI-thread capture; the read-only library survives selection changes."""
        if self._closed or self._pending:
            return None
        return self.library, self._resource_generation

    def plazaContextCurrent(self, context):
        library, generation = context
        return (not self._closed and not self._refresh_pending
                and generation == self._resource_generation and library is self.library)

    def installPlaza(self, prepared, context, cancelled, done):
        """Queue one prepared save; network work belongs to the plaza pool."""
        if self._closed or self._pending or self._plaza_pending is not None:
            return False
        self._plaza_pending = (prepared, context, cancelled, done)
        self.changed.emit()
        self._drain_plaza()
        return True

    def _drain_plaza(self):
        if self._closed or self._busy or self._pending or self._plaza_pending is None:
            return
        from account_service import AccountError
        prepared, context, cancelled, done = self._plaza_pending
        self._plaza_pending = None
        result = {}
        def install():
            if cancelled() or not self.plazaContextCurrent(context):
                raise AccountError("已取消下载，未导入路线")
            name, maps, source, report = prepared
            gid = self._write(lambda: self._store.install_plaza(name, maps, source))
            result.update(gallery=gid, report=report)
        self._run(install, "路线已下载，可在自定义图库中选用", galleries_changed=True,
                  on_done=lambda error: done(result, error))

    def setActiveGallery(self, gid):
        scope_id, local_id = self.splitGalleryId(gid)
        self._in_use = local_id if gid and scope_id == self._scope_id else ""
        self.changed.emit()

    @Slot()
    def useGallery(self):
        if self._busy or self._pending or self._use_handler is None or self._data_error:
            return
        target = "" if self._in_use == self._gallery else self._gallery
        try:
            receipt = self._use_handler(self.profileGalleryId(target) if target else "")
            self._message = str(receipt.get("message", ""))
            if receipt.get("success") and receipt.get("applied") is True:
                self._in_use = target
        except Exception as error:
            self._message = f"图集未启用：{error}"
        self.changed.emit()

    def shutdown(self):
        self._closed = True
        self._plaza_pending = None
        self.pool.waitForDone()
