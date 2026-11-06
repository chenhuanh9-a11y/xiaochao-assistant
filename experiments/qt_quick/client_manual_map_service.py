"""Qt view-model for 手动选图: catalog loading, paging and picker key routing.

Catalog reads and the eventual confirm both happen outside this QObject's
presentation state. Every result is checked against the generation and applied
mode that was current when the picker opened, so a difficulty switch or end
round cannot revive stale options.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import sys

from PySide6.QtCore import (QObject, Property, QPoint, QRunnable, QThreadPool, QTimer, Qt,
                            QUrl, Signal, Slot)
from PySide6.QtGui import QGuiApplication

from client_paths import APP_ROOT
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from client_manual_map import (MODE_TITLES, OPTION_KEYS, PICKER_KEY_NAMES, PickerState,
                               normalize_selection_mode, option_title, picker_groups)


@dataclass(frozen=True)
class MapEntry:
    id: str
    name: str
    direction: str = ""
    thumbnail_path: str = ""
    thumbnail_stamp: str = ""


def _thumbnail_url(entry):
    """Local file URI for QML's asynchronous, size-bounded Image decoding."""
    path = getattr(entry, "thumbnail_path", "")
    if not path:
        return ""
    try:
        # Final guard: a file may disappear between catalog resolution and
        # presentation. The bulk resolver still runs in the catalog worker.
        if not Path(str(path)).is_file():
            return ""
    except OSError:
        return ""
    url = QUrl.fromLocalFile(str(path))
    stamp = getattr(entry, "thumbnail_stamp", "")
    if stamp:
        url.setQuery("v=" + str(stamp))
    return url.toString()


class _CatalogSignals(QObject):
    done = Signal(int, object, object)


class _CatalogJob(QRunnable):
    def __init__(self, generation, provider):
        super().__init__()
        self.generation, self.provider = generation, provider
        self.signals = _CatalogSignals()

    def run(self):
        try:
            result = self.provider()
        except Exception as error:
            self.signals.done.emit(self.generation, None, f"{type(error).__name__}: {error}")
        else:
            self.signals.done.emit(self.generation, result, None)


class ManualMapPickerService(QObject):
    changed = Signal()

    def __init__(self, *, settings, windows, input_backend, session, catalog_provider, commit,
                 geometry_provider=None, parent=None):
        super().__init__(parent)
        self.settings, self.windows = settings, windows
        self.input, self.session = input_backend, session
        self._catalog_provider, self._commit = catalog_provider, commit
        self._geometry_provider = geometry_provider
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._job = None
        self._generation = 0
        self._visible = False
        self._busy = False
        self._closed = False
        self._message = ""
        self._state = None
        self._options = []
        self._catalog_identity = None
        self._catalog_mode = ""
        self._catalog_stale = False
        self._opened_selection_mode = "auto"
        initial = session.snapshot if session is not None else {}
        self._last_round_work = bool(initial.get("roundActive") or initial.get("roundPreparing"))
        self._opened_session_context = None
        self._catalog_session_context = None
        settings.changed.connect(self._settings_changed)
        if session is not None:
            session.changed.connect(self._session_changed)
        signal = getattr(input_backend, "pickerKeyRequested", None)
        if signal is not None:
            signal.connect(self.handleKey)

    visible = Property(bool, lambda s: s._visible, notify=changed)
    busy = Property(bool, lambda s: s._busy, notify=changed)
    message = Property(str, lambda s: s._message, notify=changed)
    options = Property("QVariantList", lambda s: [dict(row) for row in s._options], notify=changed)
    title = Property(str, lambda s: s._title(), notify=changed)
    description = Property(str, lambda s: s._description(), notify=changed)
    pageLabel = Property(str, lambda s: s._page_label(), notify=changed)
    helpText = Property(str, lambda s: s._help_text(), notify=changed)

    def _applied_mode(self):
        mode = getattr(self.session, "currentMode", "") if self.session is not None else ""
        return mode or self.settings.values.get("gameMode", "nightmare")

    def _session_context(self):
        state = self.session.snapshot if self.session is not None else {}
        return (state.get("season", "s0"), self._applied_mode(), state.get("profileId"), state.get("roundId"))

    def _title(self):
        mode = self._catalog_mode or self._applied_mode()
        title = MODE_TITLES.get(mode, "")
        return f"手动选图 · {title}" if title else "手动选图"

    def _description(self):
        if self._busy:
            return self._message or "正在读取地图资源…"
        if not self._state or not self._state.groups:
            return self._message or "当前模式没有可选地图，请先检查地图资源。"
        if self._state.stage == "groups":
            return "先选路线组合，再选地图；不经过地图识别"
        if self._state.single_group:
            return "选择本局地图，选好后按这张图贴合和显示"
        group = self._state.groups[self._state.group][0]
        return f"已选 {group} · 选择本局地图，选好后按这张图贴合和显示"

    def _page_label(self):
        if not self._state:
            return ""
        pages = self._state.page_count()
        return f"{self._state.page + 1} / {pages}" if pages > 1 else ""

    def _help_text(self):
        if not self._state:
            return ""
        pages = self._state.page_count()
        keys = " / ".join(OPTION_KEYS)
        back = "" if self._state.single_group or self._state.stage != "maps" else "，Backspace 返回"
        return f"点击或按 {keys} 选择{'，PgUp/PgDn 翻页' if pages > 1 else ''}{back}，Esc 关闭"

    def _present(self):
        options = []
        state = self._state
        if state is not None and state.groups:
            if state.stage == "groups":
                for index, (label, items) in enumerate(state.options()):
                    options.append(dict(kind="group", key=OPTION_KEYS[index], label=label,
                                        count=len(items)))
            else:
                group = state.groups[state.group][0]
                for index, item in enumerate(state.options()):
                    options.append(dict(kind="map", key=OPTION_KEYS[index],
                                        label=option_title(item, group), mapId=item.id,
                                        thumbnailUrl=_thumbnail_url(item)))
        self._options = options
        self.changed.emit()

    def _place(self):
        """Center over the bound game client, or the primary work area."""
        window = getattr(self.windows, "_windows", {}).get("map-picker") if self.windows else None
        if window is None:
            return
        rect = None
        if callable(self._geometry_provider):
            try:
                candidate = self._geometry_provider()
                values = tuple(float(candidate[key]) for key in ("x", "y", "width", "height"))
                if all(math.isfinite(value) for value in values) and min(values[2:]) > 0:
                    rect = values
            except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError):
                rect = None
        width, height = int(window.width()), int(window.height())
        screen = None
        if rect is not None:
            if rect[2] < width or rect[3] < height:
                rect = None  # Same fallback as Tk when the client is too small.
            else:
                screen = QGuiApplication.screenAt(QPoint(
                    round(rect[0] + rect[2] / 2), round(rect[1] + rect[3] / 2)))
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        left, top, area_width, area_height = (rect if rect is not None else
            (area.x(), area.y(), area.width(), area.height()))
        x = round(left + (area_width - width) / 2)
        y = round(top + (area_height - height) / 2)
        x = max(area.x(), min(x, area.x() + area.width() - width))
        y = max(area.y(), min(y, area.y() + area.height() - height))
        if window.screen() is not screen:
            window.setScreen(screen)
        window.setX(x)
        window.setY(y)

    def _set_visible(self, visible):
        visible = bool(visible)
        if self._visible == visible:
            return
        self._visible = visible
        if visible:
            self._place()
            if self.windows is not None:
                self.windows.show("map-picker")
        elif self.windows is not None:
            self.windows.hide("map-picker")
        setter = getattr(self.input, "setPickerKeys", None)
        if callable(setter):
            setter(list(PICKER_KEY_NAMES) if visible else [])
        self.changed.emit()

    @Slot()
    def toggle(self, source="button"):
        if self._visible:
            self.close()
        else:
            self.open(source)

    @Slot(str)
    def open(self, source="button"):
        state = self.session.snapshot if self.session is not None else {}
        if (self._closed or self._catalog_provider is None
                or state.get("resourceLocked") or state.get("closed")):
            return False
        self._opened_session_context = self._session_context()
        self._catalog_session_context = None
        self._opened_selection_mode = normalize_selection_mode(
            self.settings.values.get("mapSelectionMode"))
        self._generation += 1
        self._busy = True
        self._message = "正在读取地图资源…"
        self._state = None
        self._options = []
        self._catalog_identity = None
        self._catalog_mode = ""
        self._catalog_stale = False
        self._set_visible(True)
        self._present()
        self._start_catalog(self._generation)
        return True

    def _start_catalog(self, generation):
        job = _CatalogJob(generation, self._catalog_provider)
        self._job = job
        job.signals.done.connect(self._catalog_finished, Qt.QueuedConnection)
        self._pool.start(job)

    @Slot(int, object, object)
    def _catalog_finished(self, generation, result, error):
        self._job = None
        if self._closed or generation != self._generation or not self._visible:
            return
        if error:
            self._busy = False
            self._message = f"地图资源读取失败：{error}"
            self._present()
            QTimer.singleShot(0, self._place)
            return
        if isinstance(result, dict) and result.get("available") is False:
            self._busy = False
            self._message = str(result.get("message") or "当前模式的地图资源未就绪，暂时不能选图")
            self._present()
            QTimer.singleShot(0, self._place)
            return
        if not isinstance(result, dict) or not isinstance(result.get("maps"), list):
            self._busy = False
            self._message = "地图资源读取结果无效，请刷新后重试"
            self._present()
            QTimer.singleShot(0, self._place)
            return
        mode = str(result.get("mode") or self._applied_mode())
        if (result.get("season", "s0") != self._session_context()[0]
                or mode != self._applied_mode()
                or self._opened_session_context != self._session_context()):
            # A mode switch landed while this job ran; do not show the old
            # map pack. Retry with a bound delay rather than spinning while
            # the runtime context is still being applied.
            self._busy = False
            self._catalog_stale = True
            self._message = "正在切换模式，稍后重新读取地图资源…"
            self._present()
            QTimer.singleShot(0, self._place)
            QTimer.singleShot(200, self._retry_stale_catalog)
            return
        entries = [MapEntry(str(row.get("id", "")), str(row.get("name", "")),
                            str(row.get("direction", "") or ""),
                            str(row.get("thumbnailPath", "") or ""),
                            str(row.get("thumbnailStamp", "") or ""))
                   for row in result["maps"] if isinstance(row, dict) and row.get("id")]
        groups = picker_groups(entries, mode)
        self._busy = False
        self._catalog_identity = result.get("identity")
        self._catalog_session_context = self._opened_session_context
        self._catalog_mode = mode
        self._message = "" if groups else "当前模式没有可选地图，请先检查地图资源。"
        self._state = PickerState(groups) if groups else None
        self._present()
        QTimer.singleShot(0, self._place)  # QML settles the option grid height first.

    def _retry_stale_catalog(self):
        if self._closed or not self._visible or self._busy or not self._catalog_stale:
            return
        self._catalog_stale = False
        self.open("mode-changed")

    @Slot()
    def close(self):
        self._generation += 1
        self._busy = False
        self._state = None
        self._options = []
        self._catalog_identity = None
        self._catalog_mode = ""
        self._catalog_stale = False
        self._catalog_session_context = None
        self._set_visible(False)
        self.changed.emit()

    @Slot(str)
    def handleKey(self, key):
        if self._closed or not self._visible:
            return False
        key = str(key)
        if key == "Escape":
            self.close()
            return True
        if self._busy or self._state is None:
            return False
        if key == "Backspace":
            if self._state.back():
                self._present()
                return True
            return False
        if key in ("PageUp", "PageDown"):
            if self._state.turn(1 if key == "PageDown" else -1):
                self._present()
                return True
            return False
        if key in OPTION_KEYS:
            return self.choose(OPTION_KEYS.index(key))
        return False

    @Slot(int, result=bool)
    def choose(self, index):
        if self._closed or self._busy or self._state is None:
            return False
        picked = self._state.choose(int(index))
        if picked is None:
            return False
        kind, value = picked
        if kind == "group":
            self._present()
            return True
        identity = self._catalog_identity
        mode = self._catalog_mode
        state = self.session.snapshot if self.session is not None else {}
        if state.get("resourceLocked") or state.get("closed"):
            self.close()
            return False
        if (identity is None or mode != self._applied_mode()
                or self._catalog_session_context != self._session_context()):
            self._message = "地图列表已变化，请重新选择"
            self.open("stale-catalog")
            return False
        map_id = str(value.id)
        accepted = bool(self._commit(map_id)) if callable(self._commit) else False
        if accepted:
            self.close()
        else:
            # Keep the current page so a busy command can be retried without
            # reopening the picker and losing the group choice.
            self._message = "选图请求未提交，请确认本局已开始且当前没有其他操作"
            self.changed.emit()
        return accepted

    def _settings_changed(self):
        if (self._visible and self._opened_selection_mode == "manual"
                and normalize_selection_mode(self.settings.values.get("mapSelectionMode")) == "auto"):
            self.close()

    def _session_changed(self):
        if self._closed:
            return
        state = self.session.snapshot
        round_work = bool(state.get("roundActive") or state.get("roundPreparing"))
        if self._visible and ((self._last_round_work and not round_work)
                              or state.get("resourceLocked") or state.get("closed")):
            # Ending a prepared-but-not-active round revokes its catalog too.
            # An exclusive resource lease always revokes idle picker state.
            self.close()
        self._last_round_work = round_work
        if (self._visible
                and (self._opened_session_context != self._session_context()
                     or (not self._busy and (self._catalog_stale
                         or (self._catalog_mode and self._catalog_mode != self._applied_mode()))))):
            self._catalog_stale = False
            self.open("mode-changed")

    def cancel(self):
        """Revoke UI/input immediately; the coordinator drains our pool first.

        A catalog read cannot be interrupted safely, but its generation is
        obsolete and cannot reopen the picker while application close waits.
        """
        self._closed = True
        self.close()

    def shutdown(self):
        self.cancel()
        self._pool.waitForDone()
