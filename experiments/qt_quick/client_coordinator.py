"""Qt-owned commands and display delivery. No algorithm or automatic native input."""
from __future__ import annotations
from copy import deepcopy
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import threading
from PySide6.QtCore import QObject, Property, QTimer, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtQuick import QQuickImageProvider
from client_paths import APP_ROOT, WORK_ROOT
import sys
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
from reference_position_editor import ReferencePositionEdit


class LiveImages(QQuickImageProvider):
    """Two bounded immutable display images; no disk encoding or mutable QPixmap."""
    def __init__(self):
        super().__init__(QQuickImageProvider.Image)
        self._lock = threading.RLock()
        self._images = {}

    def publish(self, key, pixels):
        import numpy as np
        pixels = np.ascontiguousarray(pixels, dtype=np.uint8)
        if pixels.ndim != 3 or pixels.shape[2] != 4 or pixels.shape[0] * pixels.shape[1] > 20_000_000:
            raise ValueError("Invalid RGBA display payload")
        image = QImage(pixels.data, pixels.shape[1], pixels.shape[0], pixels.strides[0], QImage.Format_RGBA8888).copy()
        with self._lock:
            self._images[str(key)] = image
            while len(self._images) > 2:
                self._images.pop(next(iter(self._images)))
        return "image://live/" + str(key)

    def requestImage(self, identifier, size, requestedSize):
        with self._lock:
            image = self._images.get(identifier, QImage()).copy()
        if size is not None:
            size.setWidth(image.width()); size.setHeight(image.height())
        return image

    def clear(self):
        with self._lock:
            self._images.clear()


def game_window_rows():
    """Read only matching game-window metadata, on an explicit user request."""
    if os.name != "nt":
        return []
    import psutil
    from identityv_live_mvp import identityv_window_identity_is_allowed
    from client_region_capture import client_capture_context
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.IsWindowVisible.restype = wintypes.BOOL
    user.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user.GetWindowTextLengthW.restype = ctypes.c_int
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    rows = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    @callback_type
    def callback(hwnd, extra):
        try:
            if not user.IsWindowVisible(hwnd):
                return True
            length = user.GetWindowTextLengthW(hwnd)
            if not length:
                return True
            buffer = ctypes.create_unicode_buffer(min(length + 1, 1025))
            user.GetWindowTextW(hwnd, buffer, len(buffer))
            pid = wintypes.DWORD()
            user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            process = psutil.Process(pid.value)
            if process.name().casefold() != "dwrg.exe":
                return True
            if not identityv_window_identity_is_allowed(buffer.value, process.exe()):
                return True
            context = client_capture_context(int(hwnd))
            rows.append(dict(hwnd=str(int(hwnd)), title=buffer.value, pid=int(pid.value),
                             width=context.full_client_size[0], height=context.full_client_size[1],
                             dpi=context.dpi, clientBounds=list(context.client_screen_bounds)))
        except (OSError, ValueError, psutil.Error):
            pass
        return True
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.EnumWindows(callback, 0)
    return rows


def another_assistant_running():
    """Only known assistant executable names; no command lines or foreign data."""
    import psutil
    for process in psutil.process_iter(["pid", "name"]):
        try:
            if process.info["pid"] != os.getpid() and (process.info.get("name") or "").casefold() in {
                    "第五人格小抄助手.exe", "xingtuqtclienttest.exe"}:
                return True
        except (psutil.Error, OSError):
            continue
    return False


class ClientCoordinator(QObject):
    changed = Signal()
    pageRequested = Signal(str)
    quitRequested = Signal()
    runtimeEvent = Signal(object)

    def __init__(self, *, backend, settings, resources, session, windows, native_input,
                 capture, runtime, images, window_catalog=game_window_rows):
        super().__init__()
        self.backend, self.settings, self.resources = backend, settings, resources
        self.session, self.windows, self.input = session, windows, native_input
        self.capture, self.runtime, self.images = capture, runtime, images
        self._catalog = window_catalog
        self._window_rows = []
        self._bound = ""
        self._message = "选择游戏窗口后，再按需启用实时功能"
        self._reference = {}
        self._aligned = {}
        self._edit = None
        self._before_edit = None
        self._edit_generation = 0
        self._reference_clickthrough = True
        self._saving_position = False
        self._last_render_token = None
        values = settings.persistedValues
        mode = values.get("gameMode", "nightmare")
        self._applied_context = (mode, values.get("modeRouteProfiles", {}).get(mode) or values.get("routeProfileId", ""))
        self._pending_context = None
        self._binding_signature = (values.get("hotkey"), tuple(values.get("actionHotkeys", ())))
        from client_input import NativeWindowAPI
        self._native_windows = NativeWindowAPI()
        self._closed = False
        self._text_entry = False
        self._requested_action = ""
        self._waiting_foreground = False
        self._map_was_open = False
        self._closing = False
        self._close_timer = QTimer(self)
        self._close_timer.setInterval(40)
        self._close_timer.timeout.connect(self._finish_close)
        settings.changed.connect(self._draft_changed)
        settings.saved.connect(self._settings_saved)
        session.changed.connect(self._session_changed)
        session.actionRequested.connect(self._action_requested)
        resources.resourcesChanged.connect(self._resources_changed)
        native_input.actionTriggered.connect(self._native_action)
        native_input.changed.connect(self.changed)
        backend.replayOverlayRequested.connect(self.showAlignedReplay)
        backend.overlayRequested.connect(self.showReference)
        self._foreground_timer = QTimer(self)
        self._foreground_timer.setInterval(100)
        self._foreground_timer.timeout.connect(self._watch_foreground)
        self._foreground_timer.start()
        self._context_timer = QTimer(self)
        self._context_timer.setInterval(50)
        self._context_timer.timeout.connect(self._try_apply_context)
        self._tracking_timer = QTimer(self)
        self._tracking_timer.setInterval(200)
        self._tracking_timer.timeout.connect(self._track_map)
        self._tracking_timer.start()
        self.runtimeEvent.connect(self._on_runtime_event, Qt.QueuedConnection)
        callback = getattr(self.runtime, "set_event_callback", None)
        if callback:
            callback(self.runtimeEvent.emit)

    message = Property(str, lambda s: s._message, notify=changed)
    gameWindows = Property("QVariantList", lambda s: deepcopy(s._window_rows), notify=changed)
    boundWindow = Property(str, lambda s: s._bound, notify=changed)
    referenceData = Property("QVariantMap", lambda s: dict(s._reference), notify=changed)
    referenceEditing = Property(bool, lambda s: s._edit is not None, notify=changed)
    alignedData = Property("QVariantMap", lambda s: dict(s._aligned), notify=changed)
    inputEnabled = Property(bool, lambda s: s.input.enabled, notify=changed)
    closing = Property(bool, lambda s: s._closing, notify=changed)
    textEntryActive = Property(bool, lambda s: s._text_entry, notify=changed)

    def _status_text(self):
        state = self.session.snapshot
        if self.session.busy and state.get("pendingAction") != "refresh_map":
            return {"inspect_engine_resources": "正在准备识别资源…", "start_round": "正在准备本局…"}.get(self._requested_action, "正在核对地图与位置…")
        receipt = state.get("lastReceipt", {})
        if receipt.get("success") is False:
            reason = str(receipt.get("reason", ""))
            if "capture" in reason:
                return "截图暂不可用，请检查游戏前台与窗口尺寸后重新连接"
            if "map" in reason:
                return "尚未看到稳定的地图画面，打开地图后可重试"
            return "本次核对未完成，可查看诊断信息后重试"
        if state.get("roundActive"):
            return "当前地图位置已核对" if state.get("displayAuthorized") else "本局已开始，切回游戏并打开地图"
        return "游戏窗口已连接，可以开始本局" if self._bound else "先连接游戏窗口，再开始本局"

    runtimeStatus = Property(str, _status_text, notify=changed)

    def _action_requested(self, action, payload):
        self._requested_action = action
        self.changed.emit()

    @Slot(str)
    def navigate(self, name):
        if name in {"home", "gallery", "resources", "settings", "replay", "help"}:
            self.pageRequested.emit(name)

    @Slot()
    def refreshGameWindows(self):
        try:
            self._window_rows = self._catalog()
            self._message = "请选择要连接的游戏窗口" if self._window_rows else "未找到可用游戏窗口，请先打开游戏"
        except Exception as error:
            self._message = f"窗口读取失败：{error}"
        self.changed.emit()

    @Slot(str)
    def bindGameWindow(self, hwnd):
        if self.session.busy or self.resources.busy:
            self._message = "请等待当前操作完成后再切换游戏窗口"
            self.changed.emit(); return
        row = next((row for row in self._window_rows if row["hwnd"] == str(hwnd)), None)
        if not row:
            self._message = "请刷新并选择一个有效的游戏窗口"
            self.changed.emit(); return
        self.input.disable()
        self.capture.disable()
        self._revoke_runtime("binding-changed")
        self._bound = str(hwnd)
        try:
            self.input.bindWindow(int(hwnd))
            self.input.configureSettings(self.settings.persistedValues)
            self.session.dispatchWithPayload("bind_capture", {"hwnd": int(hwnd),
                "region": [0, 0, int(row["width"]), int(row["height"])]})
            self._message = "正在连接游戏窗口…"
        except Exception as error:
            self._bound = ""
            self._message = f"连接失败：{error}"
        self.changed.emit()

    @Slot(bool)
    def setInputEnabled(self, enabled):
        if enabled:
            if not self._bound:
                self._message = "请先选择并连接游戏窗口"
            elif self.session.busy:
                self._message = "请等待窗口连接或当前任务完成"
            elif another_assistant_running():
                self._message = "检测到另一个小抄客户端仍在运行，请先退出后再启用快捷键"
            elif self.input.configureSettings(self.settings.persistedValues) and self.input.enable():
                self._message = "快捷键已启用；结束测试时可在此关闭"
            else:
                self._message = self.input.message
        else:
            self.input.disable()
            self._message = "快捷键已关闭"
        self.changed.emit()

    @Slot()
    def startRound(self):
        if not self._bound or self.session.busy or self.resources.busy:
            self._message = "请先连接游戏窗口，并等待当前操作完成"
            self.changed.emit(); return
        self.setInputEnabled(True)
        if not self.input.enabled:
            return
        self.windows.show("controls")
        self.session.dispatch("start_round")

    @Slot(bool)
    def setTextEntryActive(self, active):
        self._text_entry = bool(active)
        self.input.setTextEntryActive(active)
        self.changed.emit()

    @Slot()
    def toggleControls(self):
        state = self.windows.snapshot.get("controls", {})
        if state.get("visible"):
            self.windows.hide("controls")
        else:
            self.settings.setValue("orbVisible", True)
            self.windows.show("controls")

    @Slot(str)
    def setMode(self, mode):
        if mode not in {"nightmare", "difficult"} or self.settings.busy or self.resources.busy:
            return
        self._revoke_runtime("mode-preference-changed")
        if self.settings.setValue("gameMode", mode):
            self.settings.saveFields(["gameMode"])

    @Slot(str)
    def setRouteProfile(self, profile):
        if self.settings.busy or self.resources.busy:
            return
        self._revoke_runtime("route-preference-changed")
        values = self.settings.values
        choices = dict(values.get("modeRouteProfiles", {}))
        choices[self.session.currentMode] = profile
        if self.settings.setValue("routeProfileId", profile) and self.settings.setValue("modeRouteProfiles", choices):
            self.settings.saveFields(["routeProfileId", "modeRouteProfiles"])

    @Slot()
    def showReference(self):
        if not self.backend.selectedImage:
            self._message = "先在地图资源中选择一个图层"
            self.changed.emit(); return
        self.cancelReferenceEdit()
        self._reference = dict(imageUrl=self.backend.selectedImage, title=self.backend.selectedTitle,
                               floor=self.backend.selectedFloor, key=self.backend.selectedKey)
        self.settings.setValue("referenceSheetVisible", True)
        self.changed.emit()
        self.windows.setClickThrough("reference", True)
        self.windows.show("reference")

    @Slot()
    def hideReference(self):
        self.cancelReferenceEdit()
        self.windows.hide("reference")

    @Slot()
    def beginReferenceEdit(self):
        if not self._reference or self.resources.busy:
            return
        window = self.windows._windows.get("reference")
        if window is None or self._edit:
            return
        self._edit_generation += 1
        self._before_edit = (window.x(), window.y())
        self._reference_clickthrough = bool(self.windows.snapshot["reference"]["clickThrough"])
        self._edit = ReferencePositionEdit(self._edit_generation, (self._reference["key"],),
                                          self._before_edit, (window.width(), window.height()))
        self.windows.setClickThrough("reference", False)
        self.windows.show("reference")
        self._message = "正在编辑定点位置；松开鼠标后仍可继续移动，点击完成保存"
        self.changed.emit()

    @Slot(str, float, float)
    def referencePointer(self, phase, x, y):
        if not self._edit:
            return
        point = self._edit.pointer({"press": "down", "release": "up"}.get(phase, phase), x, y)
        if point:
            self._edit.place(point)
            window = self.windows._windows.get("reference")
            if window is not None:
                window.setX(point[0]); window.setY(point[1])

    def _end_reference_edit(self, restore):
        if not self._edit:
            return
        self._edit.cancel()
        self._edit = None
        window = self.windows._windows.get("reference")
        if restore and window is not None and self._before_edit:
            window.setX(self._before_edit[0]); window.setY(self._before_edit[1])
        self._before_edit = None
        self.windows.setClickThrough("reference", self._reference_clickthrough)
        self.changed.emit()

    @Slot()
    def finishReferenceEdit(self):
        if not self._edit or self.settings.busy:
            return
        window = self.windows._windows.get("reference")
        position = list(self._edit.position)
        if window is not None and window.screen():
            area = window.screen().availableGeometry()
            position = [max(area.left(), min(position[0], area.right()-window.width()+1)),
                        max(area.top(), min(position[1], area.bottom()-window.height()+1))]
            window.setX(position[0]); window.setY(position[1])
        self.settings.setValue("sheetPosition", position)
        self._saving_position = self.settings.saveFields(["sheetPosition"])
        # Applying position is separate from persistence; error remains in settings.
        self._end_reference_edit(False)
        self._message = "位置已应用，正在保存" if self._saving_position else self.settings.message
        self.changed.emit()

    @Slot()
    def cancelReferenceEdit(self):
        self._end_reference_edit(True)

    @Slot()
    def showAlignedReplay(self):
        if self.backend.replayAvailable:
            window = self.windows._windows.get("replay")
            if window is not None:
                window.setProperty("replayMode", True)
            self.windows.show("replay")
        else:
            self.navigate("replay")

    @Slot(str)
    def loadReplay(self, value):
        path = QUrl(value).toLocalFile()
        self.backend.loadReplay(value)
        self.session.dispatchWithPayload("load_saved_result", {"path": path})

    def _draft_changed(self):
        values = self.settings.values
        if not values.get("referenceSheetVisible", True):
            self.hideReference()
        if not values.get("sheetVisibilityEnabled", True):
            self.windows.hide("aligned")
        if not values.get("orbVisible", True):
            self.windows.hide("controls")
        self.changed.emit()

    def _settings_saved(self):
        values = self.settings.persistedValues
        signature = (values.get("hotkey"), tuple(values.get("actionHotkeys", ())))
        if signature != self._binding_signature:
            was_enabled = self.input.enabled
            if self.input.configureSettings(values):
                self._binding_signature = signature
                if was_enabled:
                    self.input.enable()
        configure = getattr(self.runtime, "configure_settings", None)
        if configure:
            configure(values)
        mode = values.get("gameMode", "nightmare")
        profile = values.get("modeRouteProfiles", {}).get(mode) or values.get("routeProfileId", "")
        # Mode and author reconfiguration is serialized by SessionService; don't
        # race two independent commands with different generation tokens.
        if (mode, profile) != self._applied_context:
            self._revoke_runtime("preferences-committed")
            self._pending_context = (mode, profile)
            self._context_timer.start()
            self._try_apply_context()
        if self._saving_position:
            self._saving_position = False
            self._message = "定点位置已保存，已恢复鼠标穿透"
        self.changed.emit()

    def _try_apply_context(self):
        if not self._pending_context or self._closing or self._closed:
            self._context_timer.stop()
            return
        if self.resources.busy or self.resources.pendingKind or self.session.snapshot.get("resourceLocked"):
            return
        mode, profile = self._pending_context
        if self.session.reconfigure(mode, profile):
            self._context_timer.stop()
            if not self.session.busy:
                self._session_changed()
        else:
            self._message = self.session.message

    def _resources_changed(self):
        self.cancelReferenceEdit()
        self._reference = {}
        self._invalidate_display()
        invalidate = getattr(self.runtime, "invalidate", None)
        if invalidate:
            invalidate("resources-changed")
        self.session.refreshRuntime()
        self.backend.refresh()
        self.changed.emit()

    def _invalidate_display(self):
        self._aligned = {}
        self._last_render_token = None
        self.windows.hide("aligned")
        self.images.clear()

    def _revoke_runtime(self, reason):
        invalidate = getattr(self.runtime, "invalidate", None)
        if invalidate:
            invalidate(reason)
        self._invalidate_display()

    def _session_changed(self):
        if self.session.busy:
            if self.session.snapshot.get("pendingAction") != "refresh_map":
                self._invalidate_display()
            self.changed.emit(); return
        state = self.session.snapshot
        actual_context = self.runtime.snapshot()
        if self._pending_context and (actual_context.get("mode"), actual_context.get("profileId")) == self._pending_context:
            self._applied_context = self._pending_context
            self._pending_context = None
            self._context_timer.stop()
        receipt = state.get("lastReceipt", {})
        if receipt.get("action") == "bind_capture":
            if receipt.get("success"):
                self._message = "窗口已连接；点击开始本局后切回游戏"
            else:
                self._bound = ""
                self.input.disable()
                self._message = "连接失败，请刷新游戏窗口后重试"
        if receipt.get("action") == "start_round" and receipt.get("success") is False:
            self.input.disable()
        if state.get("alignedVisible") is False:
            self._invalidate_display()
            self.changed.emit(); return
        render = state.get("render") or {}
        token = render.get("token") or state.get("renderToken")
        if not token or not render.get("liveAuthorization"):
            self._invalidate_display()
            self.changed.emit(); return
        if token == self._last_render_token:
            return
        get_payload = getattr(self.runtime, "render_payload", None)
        payload = get_payload(token) if get_payload else None
        if not payload or not payload.get("liveAuthorization"):
            self._invalidate_display()
            self.changed.emit(); return
        public = {key: value for key, value in payload.items() if key != "pixels"}
        try:
            self._convert_client_geometry(public)
        except (OSError, ValueError) as error:
            self._message = f"暂不显示贴合层：{error}"
            self._invalidate_display()
            self.changed.emit(); return
        path = public.pop("routePath", None)
        if path:
            public["routeUrl"] = QUrl.fromLocalFile(str(path)).toString()
        elif payload.get("pixels") is not None:
            public["routeUrl"] = self.images.publish(str(token), payload["pixels"])
        else:
            self._invalidate_display(); return
        self._aligned, self._last_render_token = public, token
        self.changed.emit()
        if self.settings.values.get("sheetVisibilityEnabled", True):
            self.windows.setClickThrough("aligned", True)
            self.windows.show("aligned")

    def _native_action(self, action):
        if action == "toggle-panel":
            self.toggleControls()
            return
        aliases = {"map": "map", "escape": "close_map", "candidate-prev": "previous_candidate",
                   "candidate-next": "next_candidate", "confirm-round": "confirm_route",
                   "cycle-floor": "cycle_floor", "end-round": "end_round", "start-round": "start_round",
                   "toggle-round": "toggle_round", "toggle-sheet-visibility": "toggle_sheet_visibility"}
        command = aliases.get(action)
        if command:
            # G's visual map settles on the producer's existing prepare loop,
            # not by reading a frozen frame on the UI thread.
            if command == "map":
                self._invalidate_display()
            elif command in {"capture_now", "close_map", "end_round", "toggle_round", "cycle_floor"}:
                self._revoke_runtime("input-" + command)
            if command == "start_round":
                self.startRound()
            else:
                self.session.dispatch(command)

    def _convert_client_geometry(self, payload):
        """Keep H in capture pixels; convert only HWND origin/window DIP size."""
        x, y = int(payload.get("originX", 0)), int(payload.get("originY", 0))
        width, height = int(payload["clientWidth"]), int(payload["clientHeight"])
        if os.name != "nt":
            payload["deviceScale"] = 1.0
            return
        class Rect(ctypes.Structure):
            _fields_ = [(name, ctypes.c_long) for name in ("left", "top", "right", "bottom")]
        class Monitor(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", Rect), ("rcWork", Rect),
                        ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.MonitorFromRect.argtypes = [ctypes.POINTER(Rect), wintypes.DWORD]
        user.MonitorFromRect.restype = wintypes.HANDLE
        user.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Monitor)]
        user.GetMonitorInfoW.restype = wintypes.BOOL
        rectangle = Rect(x, y, x+width, y+height)
        handle = user.MonitorFromRect(ctypes.byref(rectangle), 2)
        info = Monitor(); info.cbSize = ctypes.sizeof(info)
        if not handle or not user.GetMonitorInfoW(handle, ctypes.byref(info)):
            raise OSError("无法读取目标显示器")
        screen = next((screen for screen in QGuiApplication.screens()
                       if screen.name().casefold() == info.szDevice.casefold()), None)
        if screen is None:
            raise ValueError("无法核对 Qt 与游戏显示器坐标")
        dpr = screen.devicePixelRatio()
        if not dpr or dpr <= 0:
            raise ValueError("显示缩放未知")
        payload["deviceScale"] = dpr
        payload["originX"] = screen.geometry().left() + (x-info.rcMonitor.left)/dpr
        payload["originY"] = screen.geometry().top() + (y-info.rcMonitor.top)/dpr
        window = self.windows._windows.get("aligned")
        if window is not None:
            window.setScreen(screen)

    def _watch_foreground(self):
        if self._closed or not self._bound or not self.session.snapshot.get("roundActive"):
            return
        try:
            if self._native_windows.foreground_window() != int(self._bound or 0):
                self._waiting_foreground = True
                self._map_was_open = self._map_was_open or self.session.snapshot.get("mapOpen") or (self.session.busy and self._requested_action in {"map", "capture_now"})
                self.windows.hide("aligned")
            elif self._waiting_foreground:
                if self.session.busy:
                    return
                self._waiting_foreground = False
                if self._map_was_open:
                    self._map_was_open = False
                    self._invalidate_display()
                    self.session.dispatch("capture_now")
            elif self._aligned:
                if self.session.busy:
                    return
                getter = getattr(self.runtime, "render_payload", None)
                if getter is None or getter(self._last_render_token) is None:
                    self._revoke_runtime("capture-context-changed")
                    self._message = "窗口或显示环境已变化，请重新连接游戏窗口"
                    self.session.refreshRuntime()
                elif (self.settings.values.get("sheetVisibilityEnabled", True)
                      and not self.windows.snapshot.get("aligned", {}).get("visible")):
                    self.windows.show("aligned")
        except OSError:
            self.windows.hide("aligned")

    @Slot(object)
    def _on_runtime_event(self, event):
        if self._closed or not isinstance(event, dict):
            return
        if event.get("event") == "render-revoked":
            getter = getattr(self.runtime, "render_payload", None)
            if getter is None or not self._last_render_token or getter(self._last_render_token) is None:
                self._invalidate_display()
                self.changed.emit()
        elif event.get("event") == "aligned-visibility-changed":
            if self.runtime.snapshot().get("alignedVisible") is False:
                self._invalidate_display()
                self.changed.emit()

    def _track_map(self):
        if (self._closed or self._closing or not self._bound or self.session.busy
                or self.resources.busy or self.resources.pendingKind):
            return
        state = self.session.snapshot
        if not state.get("roundActive") or not state.get("mapOpen") or state.get("resourceLocked"):
            return
        if "refresh_map" not in state.get("availableCommands", ()):
            return
        try:
            if self._native_windows.foreground_window() == int(self._bound):
                self.session.dispatch("refresh_map")
        except OSError:
            self._revoke_runtime("foreground-query-failed")

    @Slot()
    def requestClose(self):
        if self._closing:
            return
        self._closing = True
        self.input.disable()
        self.capture.disable()
        self._revoke_runtime("client-close-requested")
        self.session.stop()
        if self.resources.pendingKind:
            self.resources.confirmOperation(False)
        if self.resources.canCancel:
            self.resources.cancel()
        self.changed.emit()
        self._close_timer.start()
        self._finish_close()

    def _finish_close(self):
        if (self.resources.pool.activeThreadCount() or self.settings.pool.activeThreadCount()
                or self.session._pool.activeThreadCount()):
            return
        self._close_timer.stop()
        self.quitRequested.emit()

    def shutdown(self):
        self._closed = True
        self._close_timer.stop()
        self._foreground_timer.stop()
        self._context_timer.stop()
        self._tracking_timer.stop()
        callback = getattr(self.runtime, "set_event_callback", None)
        if callback:
            callback(None)
        self.input.shutdown()
        self.capture.shutdown()
        self.cancelReferenceEdit()
        self._invalidate_display()
