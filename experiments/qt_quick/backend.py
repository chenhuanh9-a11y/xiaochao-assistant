"""Qt presentation adapter. No capture, hotkey polling, Tk import or solver copy."""
from __future__ import annotations

from pathlib import Path
import json
import sys
import threading
from PySide6.QtCore import (QAbstractListModel, QObject, Property, QRunnable,
                            QThreadPool, Qt, Signal, Slot, QUrl)

from client_paths import RESOURCE_ROOT as ROOT, WORK_ROOT
sys.path.insert(0, str(ROOT / "app"))
from map_resource_context import load_browse_context
from star_map_import import inspect_package, install_package


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
        self.sandbox = Path(sandbox).resolve()
        if not self.sandbox.is_relative_to(WORK_ROOT) or not (self.sandbox / ".qt-lab-owner").is_file():
            raise ValueError("Only a marked sandbox beneath this clone's work/ is writable")
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.model = ResourceRows()
        self.preview_model = ResourceRows()
        self.context = None
        self._mode, self._profile, self._floor, self._query = "nightmare", "", "", ""
        self._busy, self._importing, self._message = False, False, "正在读取实验资源…"
        self._click_through = False
        self._selected = ""
        self._selection = {}
        self._profiles = []
        self._selections = {}
        self._mode_profiles = {}
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

    mode = Property(str, lambda s: s._mode, notify=changed)
    profiles = Property("QVariantList", lambda s: s._profiles, notify=changed)
    profileId = Property(str, lambda s: s._profile, notify=changed)
    floor = Property(str, lambda s: s._floor, notify=changed)
    busy = Property(bool, lambda s: s._busy, notify=changed)
    importing = Property(bool, lambda s: s._importing, notify=changed)
    clickThrough = Property(bool, lambda s: s._click_through, notify=changed)
    message = Property(str, lambda s: s._message, notify=changed)
    hasError = Property(bool, lambda s: s._message.startswith(("资源读取失败", "导入失败")), notify=changed)
    replayData = Property("QVariantMap", lambda s: s._replay, notify=changed)
    replayAvailable = Property(bool, lambda s: bool(s._replay), notify=changed)
    replayMessage = Property(str, lambda s: s._replay_message, notify=changed)
    selectedKey = Property(str, lambda s: s._selected, notify=changed)
    selectedTitle = Property(str, lambda s: s._selection.get("title", "先选一张地图"), notify=changed)
    selectedImage = Property(str, lambda s: s._selection.get("imageUrl", ""), notify=changed)
    selectedFloor = Property(str, lambda s: s._selection.get("floor", ""), notify=changed)
    count = Property(int, lambda s: len(s.model.rows), notify=changed)
    previewModel = Property(QObject, lambda s: s.preview_model, constant=True)
    viewKey = Property(str, lambda s: f"{s._mode}/{s._profile}/{s._floor}/{s._query}", notify=changed)

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
        generation, mode, initial_view = self._generation, self._mode, self.viewKey
        self._busy = True
        self.changed.emit()
        def loaded(context, error):
            if generation != self._generation:
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
                self._profiles = [{"id": p.profile_id, "name": p.name,
                                   "available": p.available_layers} for p in context.profiles]
                if self._profile not in {p["id"] for p in self._profiles}:
                    self._profile = next((p["id"] for p in self._profiles if p["available"]),
                                         self._profiles[0]["id"] if self._profiles else "")
                self._apply_rows()
                self._message = self._import_notice or f"已读取 {len(self.model.rows)} 张图层 · 实验副本"
            self.changed.emit()
            if self.viewKey != initial_view:
                self.viewChanged.emit(self.viewKey)
        self._submit(lambda: load_browse_context(self.sandbox / "_internal", mode,
                                                cancelled=cancel.is_set), loaded)

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
                        # File version is part of the cache URL: owned upgrades cannot show old art.
                        url = QUrl.fromLocalFile(str(path))
                        if stamp:
                            url.setQuery(f"v={stamp[0]}-{stamp[1]}")
                        rows.append(dict(key=f"{self._mode}/{self._profile}/{item.id}/{variant.id}/{floor}",
                                         title=item.name, mapId=item.id, floor=str(floor),
                                         variant=variant.name, imageUrl=url.toString() if stamp else "",
                                         available=bool(stamp)))
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
        self._mode_profiles[self._mode] = self._profile
        setattr(self, field, value)
        if field == "_mode":
            # Clear immediately: a late nightmare worker cannot populate difficult.
            self.context = None
            self._profiles = []
            self.model.reconcile([])
            self.preview_model.reconcile([])
            self._profile = self._mode_profiles.get(value, "")
            self._selection = {}
            self._selected = self._selections.get(self.viewKey, "")
            self.refresh()
        else:
            self._selected = self._selections.get(self.viewKey, "")
            self._apply_rows()
        self.changed.emit()
        self.viewChanged.emit(self.viewKey)

    @Slot(str)
    def setMode(self, value):
        if value in {"nightmare", "difficult"}:
            self._change("_mode", value)

    @Slot(str)
    def setProfile(self, value):
        if value in {p["id"] for p in self._profiles}:
            self._change("_profile", value)

    @Slot(str)
    def setFloor(self, value):
        if value in {"", "1", "2", "B1"}:
            self._change("_floor", value)

    @Slot(str)
    def search(self, value):
        self._change("_query", value.strip())

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
        self._submit(lambda: inspect_package(path, self.sandbox), inspected)

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
        self._submit(lambda: install_package(path, self.sandbox, application_stopped=True,
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
