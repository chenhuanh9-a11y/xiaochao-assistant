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
    confirmationRequested = Signal(str, str)

    def __init__(self, sandbox, *, library=None, default_path=DEFAULT_GALLERY_FILE):
        super().__init__()
        self.paths = runtime_paths(sandbox)
        self.sandbox = self.paths.installation_root
        self.path = self.paths.custom_galleries_path
        self.exports = self.sandbox / "exports"
        self.library = library or CustomMapLibrary(self.paths.editor_base_root)
        self._injected_library = library is not None
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
        return dict(deepcopy(self._state), busy=self._busy, message=self._message,
                    pendingKind=self._pending["kind"] if self._pending else "",
                    color=self._color, colors=[dict(value=c, name=n) for c, n in COLORS],
                    newStart=self._start, editing=self._editing,
                    canUse=self._use_handler is not None and not self._data_error, inUse=self._in_use)

    snapshot = Property("QVariantMap", _snapshot, notify=changed)
    busy = Property(bool, lambda s: s._busy, notify=changed)
    message = Property(str, lambda s: s._message, notify=changed)

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
        except (OSError, ValueError) as error:
            self._data_error = f"{error}；原文件已保留，当前仅浏览地图。请备份并检查 custom-galleries.json 后刷新。"
        store_path = self.path / "read-only" if self._data_error else self.path
        self._store = GalleryStore(store_path, self.default_path,
            legacy_path=(self.paths.settings_path.with_name("custom-routes.json") if self.paths.production
                         else self.sandbox / "qt-no-implicit-legacy-import.json"))
        self.library = CustomMapLibrary(self._library_root)
        self._cache_map_metadata()
        self._digest = None if self._data_error else _file_digest(self.path)
        self._publish_committed()

    def _cache_map_metadata(self):
        """Verify thumbnails once per library refresh, never per route write."""
        rows = []
        for mid, name in self.library.maps():
            row = dict(id=mid, name=name, thumbnailUrl="", thumbnailError="")
            try:
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

    def base_display_library(self):
        """Worker-safe immutable library snapshot for the original base renderer."""
        with self._committed_lock:
            return self._committed_library

    def display_snapshot(self, gallery_id, map_id=None):
        """Pure worker-safe committed data; never exposes a mutating store/Qt property."""
        if self._data_error:
            return None
        with self._committed_lock:
            row = self._committed.get(str(gallery_id))
            if row is None:
                return None
            result = dict(galleryId=row["id"], galleryName=row["name"],
                points=deepcopy(row.get("maps", {}).get(str(map_id), {}).get("points", [])),
                library=self._committed_library, revision=self._committed_revision,
                diskDigest=self._committed_digest)
        if self.path.is_symlink() or _file_digest(self.path) != result["diskDigest"]:
            return None
        return result

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
            expected = [{k: v for k, v in self._store.export(gid).items() if k != "schema"}
                        for gid, _name, readonly in self._store.galleries() if not readonly]
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
        galleries = [dict(id=gid, name=name, readonly=ro, drawnCount=len(store.drawn_maps(gid)))
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
            imageUrl="", imageWidth=0, imageHeight=0, planError="", available=self.library.available,
            libraryMessage=self.library.error, dataError=self._data_error, canUndo=bool(self._past), canRedo=bool(self._future))
        if not self._map:
            return state
        try:
            graph = self.library.graph(self._map)
        except (OSError, ValueError, KeyError, TypeError) as error:
            state.update(available=False, libraryMessage=f"地图资源无法读取：{error}")
            return state
        floors = [f for f in FLOORS if f in graph.floors]
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
        markers = [dict(**xy((x, y)), label=label) for x, y, label in marker_groups(points, self._floor)]
        state.update(floors=[dict(id=f, name=FLOOR_LABELS[f]) for f in floors], selectedFloor=self._floor,
            imageUrl=QUrl.fromLocalFile(str(asset.image_path)).toString(), imageWidth=int(asset.image_size[0]),
            imageHeight=int(asset.image_size[1]), points=points, lines=lines, markers=markers)
        return state

    def _run(self, function, message="", *, galleries_changed=False):
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
        job.signals.done.connect(done, Qt.QueuedConnection)
        self.pool.start(job)
        return True

    @Slot()
    def refresh(self):
        def reload():
            if not self._injected_library:
                paths = runtime_paths(self.sandbox)
                root = next((candidate for candidate in paths.editor_base_candidates
                             if editor_library_usable(candidate)), paths.editor_base_root)
                self.library = CustomMapLibrary(root)
                self._library_root = self.library.root
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
        if self._busy or self._pending or self._state.get("readonly"):
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
            if len(points) >= MAX_POINTS:
                raise ValueError(f"一张地图最多 {MAX_POINTS} 个目标")
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
            if not path.is_file() or path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
                raise ValueError("请选择不超过 2 MiB 的图集 JSON 文件")
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            self._gallery = self._write(lambda: self._store.import_gallery(payload))
            self._past, self._future = [], []
        self._run(load, "图集已导入", galleries_changed=True)

    def _export_path(self, name):
        name = str(name).strip()
        if not re.fullmatch(r"[^\\/:*?\"<>|\x00-\x1f]{1,80}\.json", name, re.I) or name in (".json", "..json"):
            raise ValueError("请使用有效的 .json 文件名")
        return self._validate_export_path(self.exports / name)

    def _validate_export_path(self, target):
        target = Path(target)
        _no_links(target)
        if not target.is_absolute() or target.suffix.lower() != ".json":
            raise ValueError("请选择本地 .json 文件")
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
                raise ValueError("请选择本地 .json 文件")
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
        path = self._validate_export_path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=path.parent,
                                             prefix=".gallery-export-", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(self._store.export(gid), handle, ensure_ascii=False, indent=1)
                handle.flush()
                os.fsync(handle.fileno())
            self._validate_export_path(path)
            if overwrite:
                if _file_digest(path) != expected:
                    raise ValueError("导出文件已变化，请重新确认")
                os.replace(temporary, path)
            else:
                # Publish a complete file without replacing a destination that
                # appeared after the chooser/inspection.
                os.link(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

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

    def setActiveGallery(self, gid):
        self._in_use = str(gid or "")
        self.changed.emit()

    @Slot()
    def useGallery(self):
        if self._busy or self._pending or self._use_handler is None or self._data_error:
            return
        target = "" if self._in_use == self._gallery else self._gallery
        try:
            receipt = self._use_handler(target)
            self._message = str(receipt.get("message", ""))
            if receipt.get("success") and receipt.get("applied") is True:
                self._in_use = target
        except Exception as error:
            self._message = f"图集未启用：{error}"
        self.changed.emit()

    def shutdown(self):
        self._closed = True
        self.pool.waitForDone()
