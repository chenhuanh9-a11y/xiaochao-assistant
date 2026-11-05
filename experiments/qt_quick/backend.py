"""Qt presentation adapter. No capture, hotkey polling, Tk import or solver copy."""
from __future__ import annotations

from pathlib import Path
import json
import sys
import threading
from PySide6.QtCore import (QAbstractListModel, QObject, Property, QRunnable,
                            QThreadPool, Qt, Signal, Slot, QUrl)

from client_paths import RESOURCE_ROOT as ROOT, WORK_ROOT, runtime_paths, bundled_author_root
sys.path.insert(0, str(ROOT / "app"))
from season_context import SEASONS, season_label
from map_resource_context import load_browse_context
from season_map_resources import browse_modes
from star_map_import import inspect_package, install_package
from resource_transaction import assert_resources_ready, resource_read_lock


class WorkSignals(QObject):
    done = Signal(object, object)


class Work(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.signals = WorkSignals()

    def run(self):
        try:
            self.signals.done.emit(self.function(), None)
        except Exception as error:
            self.signals.done.emit(None, str(error))


class ResourceRows(QAbstractListModel):
    fields = ("key", "title", "mapId", "floor", "variant", "imageUrl", "available")

    def __init__(self):
        super().__init__()
        self.rows = []
        self.mutations = 0

    def roleNames(self):
        return {Qt.UserRole + index + 1: name.encode() for index, name in enumerate(self.fields)}

    def rowCount(self, parent=None):
        return len(self.rows) if parent is None or not parent.isValid() else 0

    def data(self, index, role):
        position = role - Qt.UserRole - 1
        if index.isValid() and 0 <= position < len(self.fields):
            return self.rows[index.row()][self.fields[position]]
        return None

    def reconcile(self, rows):
        """Stable keys; unchanged refresh has zero model signals / delegate churn."""
        wanted = {row["key"] for row in rows}
        for index in range(len(self.rows) - 1, -1, -1):
            if self.rows[index]["key"] not in wanted:
                self.beginRemoveRows(self.index(-1, -1), index, index)
                self.rows.pop(index)
                self.endRemoveRows()
                self.mutations += 1
        for index, row in enumerate(rows):
            current = next((i for i, item in enumerate(self.rows) if item["key"] == row["key"]), -1)
            if current < 0:
                self.beginInsertRows(self.index(-1, -1), index, index)
                self.rows.insert(index, row)
                self.endInsertRows()
                self.mutations += 1
            else:
                if current != index:
                    self.beginMoveRows(self.index(-1, -1), current, current,
                                       self.index(-1, -1), index if current > index else index + 1)
                    self.rows.insert(index, self.rows.pop(current))
                    self.endMoveRows()
                    self.mutations += 1
                changed = [Qt.UserRole + n + 1 for n, name in enumerate(self.fields)
                           if self.rows[index][name] != row[name]]
                if changed:
                    self.rows[index] = row
                    self.dataChanged.emit(self.index(index, 0), self.index(index, 0), changed)
                    self.mutations += 1


class Backend(QObject):
    changed = Signal()
    viewChanging = Signal(str)
    viewChanged = Signal(str)
    confirmReplacement = Signal(str)
    overlayRequested = Signal()
    replayOverlayRequested = Signal()
    clickThroughRequested = Signal(bool)

    def __init__(self, sandbox):
        super().__init__()
        self.paths = runtime_paths(sandbox)
        self.sandbox = self.paths.installation_root
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.model = ResourceRows()
        self.preview_model = ResourceRows()
        self.context = None
        self._season = "s0"
        self._mode, self._profile, self._floor, self._query = "nightmare", "", "", ""
        self._busy, self._importing, self._message = False, False, "正在读取实验资源…"
        self._click_through = False
        self._selected = ""
        self._selection = {}
        self._profiles = []
        self._selections = {}
        self._mode_profiles = {}
        self._context_floors = {}
        self._cancel = threading.Event()
        self._generation = 0
        self._jobs = set()
        self._pending = None
        self._import_notice = ""
        self._replay = {}
        self._replay_message = "选择一份已保存的回放"
        self._replay_generation = 0
        self._shutting_down = False
        self.stale_results = 0
        self.refresh()

    season = Property(str, lambda s: s._season, notify=changed)
    mode = Property(str, lambda s: s._mode, notify=changed)
    profiles = Property("QVariantList", lambda s: s._profiles, notify=changed)
    profileId = Property(str, lambda s: s._profile, notify=changed)
    floor = Property(str, lambda s: s._floor, notify=changed)
    floorOptions = Property("QVariantList", lambda s: s._floor_options(), notify=changed)
    busy = Property(bool, lambda s: s._busy, notify=changed)
    importing = Property(bool, lambda s: s._importing, notify=changed)
    clickThrough = Property(bool, lambda s: s._click_through, notify=changed)
    message = Property(str, lambda s: s._message, notify=changed)
    hasError = Property(bool, lambda s: s._message.startswith(("资源读取失败", "导入失败")), notify=changed)
    replayData = Property("QVariantMap", lambda s: s._replay, notify=changed)
    replayAvailable = Property(bool, lambda s: bool(s._replay), notify=changed)
    replayMessage = Property(str, lambda s: s._replay_message, notify=changed)
    selectedKey = Property(str, lambda s: s._selected, notify=changed)
    selectedRow = Property(int, lambda s: next((i for i, row in enumerate(s.model.rows)
                                               if row["key"] == s._selected), -1), notify=changed)
    selectedTitle = Property(str, lambda s: s._selection.get("title", "先选一张地图"), notify=changed)
    selectedImage = Property(str, lambda s: s._selection.get("imageUrl", ""), notify=changed)
    selectedFloor = Property(str, lambda s: s._selection.get("floor", ""), notify=changed)
    selectedFloorLabel = Property(str, lambda s: next((row["name"] for row in s._floor_options()
        if row["value"] == s._selection.get("floor") and row["value"]), ""), notify=changed)
    count = Property(int, lambda s: len(s.model.rows), notify=changed)
    # A map is one installed map identity with at least one available layer.
    # Floors and route variants stay browseable rows, but do not add maps.
    mapCount = Property(int, lambda s: len({row["mapId"] for row in s.model.rows if row["available"]}), notify=changed)
    previewModel = Property(QObject, lambda s: s.preview_model, constant=True)
    viewKey = Property(str, lambda s: f"{s._season}/{s._mode}/{s._profile}/{s._floor}/{s._query}", notify=changed)

    def _floor_options(self):
        if self._season == "s0":
            return [dict(name=name, value=value) for name, value in
                    (("全部楼层", ""), ("1 楼", "1"), ("2 楼", "2"), ("B1 地下室", "B1"))]
        metadata = getattr(self.context, "floor_metadata", None) or {}
        return [dict(name="全部楼层", value="")] + [
            dict(name=metadata.get(f"source-height-{height}", {}).get("label", f"源高度 {height}"),
                 value=f"source-height-{height}") for height in (4, 3, 2, 1)]

    def _submit(self, function, callback):
        job = Work(function)
        self._jobs.add(job)
        def receive(result, error):
            self._jobs.discard(job)
            if not self._shutting_down:
                callback(result, error)
        job.signals.done.connect(receive, Qt.QueuedConnection)
        self.pool.start(job)

    @Slot()
    def refresh(self):
        self._cancel.set()
        self._cancel = cancel = threading.Event()
        self._generation += 1
        generation, mode, season, initial_view = self._generation, self._mode, self._season, self.viewKey
        self._busy = True
        self.changed.emit()
        def loaded(context, error):
            if generation != self._generation or (mode, season) != (self._mode, self._season):
                self.stale_results += 1
                return
            self._busy = False
            if error or context.error:
                self.context = None
                self._profiles = []
                self.model.reconcile([])
                self.preview_model.reconcile([])
                self._selected, self._selection = "", {}
                self._message = f"资源读取失败：{error or context.error}"
            else:
                self.context = context
                # Keep all declarations in the read context for package repair,
                # but only offer authors with actual local images in the gallery.
                self._profiles = [{"id": p.profile_id, "name": p.name, "packageId": p.package_id or "",
                                   "runtimeSelectable": season == "s0",
                                   "available": p.available_layers,
                                   "availableMaps": p.available_maps, "mapCount": p.map_count,
                                   "previewUrl": self._preview_url(p.first_preview[2], context.path_stamps, self._season)
                                       if p.first_preview else ""} for p in context.profiles
                                  if p.available_layers > 0]
                if self._profile not in {p["id"] for p in self._profiles}:
                    self._profile = next((p["id"] for p in self._profiles if p["available"]),
                                         self._profiles[0]["id"] if self._profiles else "")
                self._apply_rows()
                self._message = (f"S1 图库 · 已读取 {self.mapCount} 份图 · -1至-4层"
                                 if season == "s1" else self._import_notice or f"已读取 {self.mapCount} 份图")
            self.changed.emit()
            if self.viewKey != initial_view:
                self.viewChanged.emit(self.viewKey)
        def read():
            # The UI stays responsive while its worker waits for an author
            # transaction. Never cache the intermediate on-disk removal state.
            with resource_read_lock(self.sandbox, wait=True, cancelled=cancel.is_set):
                assert_resources_ready(self.sandbox)
                return load_browse_context(self.paths.resource_root, mode, season=season, cancelled=cancel.is_set,
                                           display_fallback_root=bundled_author_root())
        self._submit(read, loaded)

    @staticmethod
    def _preview_url(path, stamps, season="s0"):
        stamp = stamps.get(Path(path))
        if not stamp:
            return ""
        url = QUrl.fromLocalFile(str(path))
        url.setQuery(f"v={'-'.join(map(str, stamp))}&season={season}")
        return url.toString()

    def _apply_rows(self):
        rows = []
        profile = next((p for p in self.context.profiles if p.profile_id == self._profile), None) if self.context else None
        if profile:
            for item in profile.maps:
                if self._query and self._query.casefold() not in (item.name + " " + item.id).casefold():
                    continue
                for variant in item.variants:
                    for floor, path in variant.layers.items():
                        if self._floor and str(floor) != self._floor:
                            continue
                        path = Path(path)
                        stamp = self.context.path_stamps.get(path)
                        # The S1 producer hash also binds the preview cache.
                        image_url = self._preview_url(path, self.context.path_stamps, self._season)
                        rows.append(dict(key=f"{self._season}/{self._mode}/{self._profile}/{item.id}/{variant.id}/{floor}",
                                         title=item.name, mapId=item.id, floor=str(floor),
                                         variant=variant.name, imageUrl=image_url,
                                         available=bool(stamp)))
                        if self._season == "s1":
                            rows[-1].update(dict((getattr(self.context, "floor_metadata", None) or {}).get(str(floor), {})))
        self.model.reconcile(rows)
        previews, seen = [], set()
        for row in rows:
            if row["available"] and row["mapId"] not in seen:
                previews.append(row)
                seen.add(row["mapId"])
            if len(previews) == 3:
                break
        self.preview_model.reconcile(previews)
        match = next((r for r in rows if r["key"] == self._selected), None)
        if match:
            self._selection = match
        else:
            self._selection = {}
            self._selected = ""

    def _change(self, field, value):
        if getattr(self, field) == value:
            return
        self.viewChanging.emit(self.viewKey)
        self._selections[self.viewKey] = self._selected
        setattr(self, field, value)
        self._selected = self._selections.get(self.viewKey, "")
        self._apply_rows()
        if self._message.startswith("已读取"):
            self._message = f"已读取 {self.mapCount} 份图"
        self.changed.emit()
        self.viewChanged.emit(self.viewKey)

    @Slot(str)
    def setSeason(self, value):
        self.setContext(value, self._mode)

    @Slot(str, str)
    def setContext(self, season, mode):
        if season not in SEASONS or mode not in browse_modes(season):
            return
        if (season, mode) == (self._season, self._mode):
            return
        # A combined selection is one transition, not a visit to an intermediate
        # season/difficulty. Preserve only the real view being left.
        self.viewChanging.emit(self.viewKey)
        self._selections[self.viewKey] = self._selected
        self._mode_profiles[(self._season, self._mode)] = self._profile
        self._context_floors[(self._season, self._mode)] = self._floor
        self._season, self._mode = season, mode
        self._floor = self._context_floors.get((season, mode), "")
        self.context = None
        self._profiles = []
        self.model.reconcile([])
        self.preview_model.reconcile([])
        self._profile = self._mode_profiles.get((season, mode), "")
        self._selection = {}
        self._selected = self._selections.get(self.viewKey, "")
        self.refresh()
        self.changed.emit()
        self.viewChanged.emit(self.viewKey)

    @Slot(str)
    def setMode(self, value):
        self.setContext(self._season, value)

    @Slot(str)
    def setProfile(self, value):
        if value in {p["id"] for p in self._profiles}:
            self._change("_profile", value)

    @Slot(str)
    def setFloor(self, value):
        if value in {row["value"] for row in self._floor_options()}:
            self._change("_floor", value)

    @Slot()
    def clearGallerySearch(self):
        """The production gallery has no search; discard any old hidden filter.

        Keep the separate laboratory browser's search API and view history.
        """
        self._change("_query", "")

    @Slot(str)
    def search(self, value):
        self._change("_query", value.strip())

    @Slot()
    def selectFirstLayer(self):
        """Match the old author-card preview's initial map selection."""
        if self._selected or not self.model.rows:
            return
        row = next((row for row in self.model.rows if row["available"]), self.model.rows[0])
        self.select(row["key"])

    @Slot(str)
    def select(self, key):
        if key == self._selected:
            return
        row = next((r for r in self.model.rows if r["key"] == key), None)
        if row:
            self._selected, self._selection = key, row
            self.changed.emit()

    @Slot()
    def showOverlay(self):
        self.overlayRequested.emit()

    @Slot()
    def showReplayOverlay(self):
        if self._replay:
            self.replayOverlayRequested.emit()

    @Slot(str)
    def loadReplay(self, value):
        path = Path(QUrl(value).toLocalFile())
        self._replay_generation += 1
        generation = self._replay_generation
        self._replay = {}
        self._replay_message = "正在校验并载入回放…"
        self.changed.emit()
        def read():
            from replay_adapter import load_render_replay
            return load_render_replay(path, WORK_ROOT)
        def loaded(result, error):
            if generation != self._replay_generation:
                return
            self._replay = result if not error else {}
            self._replay_message = f"回放载入失败：{error}" if error else (
                f"{result['label']} · {result['floor']}楼 · 未连接游戏")
            self.changed.emit()
        self._submit(read, loaded)

    @Slot(bool)
    def setClickThrough(self, enabled):
        self._click_through = enabled
        self.changed.emit()
        self.clickThroughRequested.emit(enabled)

    @Slot(str)
    def importArchive(self, value):
        if self._importing or self._pending:
            return
        if self._season != "s0":
            self._message = f"{season_label(self._season)} 资源待补齐，不能向该赛季导入 S0 图包"
            self.changed.emit()
            return
        path = Path(QUrl(value).toLocalFile())
        self._importing = True
        self._import_notice = ""
        self._message = "正在校验图包…"
        self.changed.emit()
        def inspected(result, error):
            if error:
                self._finish_import(None, error)
            elif result.get("replaceFiles"):
                self._pending = (path, result["archiveSha256"])
                self.confirmReplacement.emit(f"将覆盖实验副本中的 {len(result['replaceFiles'])} 个文件。继续？")
            else:
                self._install(path, result["archiveSha256"])
        self._submit(lambda: inspect_package(path, self.sandbox, season="s0"), inspected)

    @Slot(bool)
    def confirmImport(self, accepted):
        if self._pending:
            path, digest = self._pending
            self._pending = None
            if accepted:
                self._install(path, digest)
            else:
                self._importing = False
                self._message = "已取消覆盖"
                self.changed.emit()

    def _install(self, path, digest):
        self._submit(lambda: install_package(path, self.sandbox, season="s0", application_stopped=True,
                                            expected_archive_sha256=digest), self._finish_import)

    def _finish_import(self, result, error):
        self._importing = False
        self._message = f"导入失败：{error}" if error else "图包已导入实验副本"
        self._import_notice = self._message if not error else ""
        self.changed.emit()
        if not error:
            self.refresh()

    def shutdown(self):
        self._shutting_down = True
        self._cancel.set()
        self.pool.clear()
        # Finish any import transaction before process exit; never abandon a writer.
        self.pool.waitForDone()
